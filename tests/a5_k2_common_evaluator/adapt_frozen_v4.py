#!/usr/bin/env python3
"""Adapt exact generator-v4 traces to the common K2 vector schema, read-only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from k2_oracle import (ContractError, ROW_WHEEL, SOURCE_COUNT, RETIRE_LANES,
                       VECTOR_SCHEMA, file_sha256, object_sha256, run_sha)

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "scripts"))
import common_suite_official as official


OFFICIAL_POLICY_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"


def verify_official_policy() -> None:
    policy = PROJECT / "scripts/common_suite_official.py"
    if file_sha256(policy) != OFFICIAL_POLICY_SHA256:
        raise ContractError("common_suite_official.py differs from the frozen policy identity")


def locate_generator(explicit: Path | None) -> Path:
    candidates = ([explicit] if explicit else []) + [
        PROJECT.parent / "a1/benchmarks/clean_slate_aer/generate_trace.py",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and 'GENERATOR_VERSION = "4.0"' in candidate.read_text(
                encoding="utf-8"):
            return candidate.resolve()
    raise ContractError("exact generator-v4 not found; pass --generator")


def manifest_path(suite: str) -> Path:
    return PROJECT / "tests/common_suite_receipt/fixtures" / official.SUITES[suite]["manifest_name"]


def generate_traces(generator: Path, trace_dir: Path) -> None:
    verify_official_policy()
    if trace_dir.exists():
        raise ContractError(f"refusing to reuse or overwrite generated trace directory {trace_dir}")
    manifest = manifest_path("full50")
    result = subprocess.run(
        [sys.executable, str(generator), "--manifest", str(manifest),
         "--output-dir", str(trace_dir)],
        cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=os.environ.copy(), check=False)
    if result.returncode:
        raise ContractError(f"generator-v4 failed:\n{result.stdout}")
    (trace_dir / "a5-k2-adapter-generation.log").write_text(result.stdout, encoding="utf-8")


def read_trace(path: Path, run_name: str) -> dict[int, list[dict[str, Any]]]:
    cycles: dict[int, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ContractError(f"{path}:{line_number}: {error}") from error
            source_id = item.get("logical_source")
            cycle = item.get("occurrence_cycle")
            trace_id = item.get("tb_only_event_id")
            if not isinstance(source_id, int) or not 0 <= source_id < SOURCE_COUNT:
                raise ContractError(f"{path}:{line_number}: invalid logical source")
            if not isinstance(cycle, int) or cycle < 0 or not isinstance(trace_id, int):
                raise ContractError(f"{path}:{line_number}: invalid cycle/event ID")
            cycles.setdefault(cycle, []).append({
                "event_id": f"v4:{run_name}:{trace_id}",
                "source": source_id,
                "payload": {
                    "address": (int(item["y"]) * 4) + int(item["x"]),
                    "x": int(item["x"]), "y": int(item["y"]),
                    "polarity": int(item["polarity"]), "event_type": item["event_type"],
                },
                "v4_tb_only_event_id": trace_id,
                "deadline": int(item["deadline"]),
            })
    for cycle, items in cycles.items():
        sources = [item["source"] for item in items]
        if len(sources) != len(set(sources)):
            raise ContractError(f"{run_name}: duplicate source occurrence at cycle {cycle}")
    return cycles


def build_bundle(trace_dir: Path, suite: str, drain_cycles: int) -> dict[str, Any]:
    verify_official_policy()
    manifest = manifest_path(suite)
    expected = official.SUITES[suite]
    if file_sha256(manifest) != expected["manifest_sha256"]:
        raise ContractError(f"{suite} manifest differs from frozen identity")
    declarations = json.loads(manifest.read_text(encoding="utf-8"))["runs"]
    if tuple(item["name"] for item in declarations) != expected["names"]:
        raise ContractError(f"{suite} manifest name order differs from frozen identity")
    runs = []
    for declaration in declarations:
        name = declaration["name"]
        trace = trace_dir / f"{name}.events.jsonl"
        if not trace.is_file() or file_sha256(trace) != official.TRACE_SHA256[name]:
            raise ContractError(f"{name}: missing trace or frozen-v4 SHA mismatch")
        by_cycle = read_trace(trace, name)
        stim_cycles = int(declaration["stim_cycles"])
        cycles = [{
            "cycle": cycle, "reset_n": True, "retire_ready": [True, True],
            "occurrences": by_cycle.get(cycle, []),
        } for cycle in range(stim_cycles + drain_cycles)]
        tags = ["required", "frozen_v4", suite]
        if name in official.CAPACITY22:
            tags.append("capacity22")
        run = {
            "name": name, "origin": "exact_generator_v4",
            "purpose": "Read-only adapter of an exact SHA-pinned common occurrence trace.",
            "tags": tags, "reset_policy": "initial_state_only",
            "stim_cycles": stim_cycles, "measurement_window": [0, stim_cycles],
            "trace_sha256": official.TRACE_SHA256[name], "cycles": cycles,
        }
        run["run_sha256"] = run_sha(run)
        runs.append(run)
    bundle: dict[str, Any] = {
        "schema": VECTOR_SCHEMA, "schema_version": 1,
        "source_count": SOURCE_COUNT, "retire_lanes": RETIRE_LANES,
        "event_id_scope": "TB-only; never a synthesized DUT input",
        "cycle_semantics": "v4 occurrence updates one-entry source latch before observed accepts at the indexed edge",
        "oracle_policy": {
            "name": "committed_event_weighted_wheel_rr_v1",
            "row_wheel": list(ROW_WHEEL), "row_for_source": "source_div_4",
            "column_rule": "round_robin",
            "initial_state": {"wheel_pos": 0, "column_rr": [0, 0, 0, 0]},
            "state_transition": "once per committed accepted event; never per physical cycle or attempted slot",
        },
        "frozen_v4": {
            "suite": suite, "source_commit": official.SOURCE_COMMIT,
            "generator_version": official.GENERATOR_VERSION,
            "official_policy_sha256": OFFICIAL_POLICY_SHA256,
            "manifest_sha256": expected["manifest_sha256"],
            "capacity22_is_full50_subset_view": True,
            "drain_cycles": drain_cycles,
        },
        "runs": runs,
    }
    bundle["bundle_sha256"] = object_sha256(bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("full50", "capacity22"), default="full50")
    parser.add_argument("--trace-dir", required=True, type=Path)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--generator", type=Path)
    parser.add_argument("--drain-cycles", type=int, default=64)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        print(f"error: refusing to overwrite {args.output}", file=sys.stderr)
        return 2
    try:
        if args.drain_cycles < 16:
            raise ContractError("drain-cycles must be at least 16 for N16")
        if args.generate:
            generate_traces(locate_generator(args.generator), args.trace_dir)
        bundle = build_bundle(args.trace_dir, args.suite, args.drain_cycles)
    except ContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"A5_K2_FROZEN_V4_ADAPTER_CREATED suite={args.suite} runs={len(bundle['runs'])} sha256={bundle['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
