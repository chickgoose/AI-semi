#!/usr/bin/env python3
"""Read-only replay of the frozen generator-v4 full50/capacity22 suites."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import runpy
import statistics
import subprocess
import sys
from typing import Any

from model import MovingBlockTreeModel, RunMetrics, run_occurrences


EXPECTED_GENERATOR_SHA256 = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
EXPECTED_OFFICIAL_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
EXPECTED_GENERATOR_VERSION = "4.0"
REPRESENTATIVE_RTL_TRACES = (
    "core_simultaneous_identity",
    "shape_b16",
    "global_fanin_identity",
    "mixed_phase_always_ready_identity",
)


class ReplayError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def command_output(arguments: list[str], cwd: pathlib.Path) -> str:
    result = subprocess.run(
        arguments, cwd=cwd, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ReplayError(
            f"command failed ({result.returncode}): {' '.join(arguments)}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout.strip()


def percentile(values: list[int], pct: int) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * pct / 100) - 1)]


def metric_document(metrics: RunMetrics) -> dict[str, Any]:
    return {
        "offered": metrics.offered,
        "accepted": metrics.accepted,
        "overrun": metrics.overrun,
        "retired": metrics.retired,
        "cycles": metrics.cycles,
        "throughput": round(metrics.throughput, 9),
        "output_bubbles": metrics.output_bubbles,
        "mean_e2e_latency": round(statistics.mean(metrics.e2e_latencies), 9),
        "p95_e2e_latency": percentile(metrics.e2e_latencies, 95),
        "p99_e2e_latency": percentile(metrics.e2e_latencies, 99),
        "max_e2e_latency": max(metrics.e2e_latencies),
    }


def load_occurrences(path: pathlib.Path) -> list[tuple[int, int]]:
    occurrences = []
    seen_ids: set[int] = set()
    source_cycles: set[tuple[int, int]] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            event = json.loads(line)
            cycle = event.get("occurrence_cycle")
            source = event.get("logical_source")
            event_id = event.get("tb_only_event_id")
            if not isinstance(cycle, int) or cycle < 0:
                raise ReplayError(f"{path}:{line_number}: invalid occurrence_cycle")
            if not isinstance(source, int) or not 0 <= source < 16:
                raise ReplayError(f"{path}:{line_number}: invalid logical_source")
            if not isinstance(event_id, int) or event_id in seen_ids:
                raise ReplayError(f"{path}:{line_number}: duplicate/invalid event id")
            if (cycle, source) in source_cycles:
                raise ReplayError(f"{path}:{line_number}: repeated source in one cycle")
            seen_ids.add(event_id)
            source_cycles.add((cycle, source))
            occurrences.append((cycle, source))
    return occurrences


def aggregate(metrics: list[RunMetrics]) -> dict[str, Any]:
    latencies = [value for item in metrics for value in item.e2e_latencies]
    offered = sum(item.offered for item in metrics)
    accepted = sum(item.accepted for item in metrics)
    retired = sum(item.retired for item in metrics)
    cycles = sum(item.cycles for item in metrics)
    return {
        "runs": len(metrics),
        "offered": offered,
        "accepted": accepted,
        "overrun": sum(item.overrun for item in metrics),
        "retired": retired,
        "cycles": cycles,
        "throughput": round(retired / cycles, 9),
        "output_bubbles": sum(item.output_bubbles for item in metrics),
        "mean_e2e_latency": round(statistics.mean(latencies), 9),
        "p95_e2e_latency": percentile(latencies, 95),
        "p99_e2e_latency": percentile(latencies, 99),
        "max_e2e_latency": max(latencies),
    }


def vector_line(
    rst_n: bool,
    valid: list[bool],
    payload: list[int],
    result,
) -> str:
    valid_mask = sum(int(bit) << index for index, bit in enumerate(valid))
    ready_mask = sum(
        int(bit) << index for index, bit in enumerate(result.source_ready)
    )
    fields = [str(int(rst_n)), f"{valid_mask:04x}", "1"]
    fields.extend(f"{value:08x}" for value in payload)
    fields.extend(
        [
            f"{ready_mask:04x}", str(int(result.retire_valid)),
            f"{result.retire_source:x}", f"{result.retire_payload:08x}",
        ]
    )
    return " ".join(fields)


def write_rtl_vectors(
    trace: pathlib.Path,
    metadata: dict[str, Any],
    output: pathlib.Path,
) -> int:
    events_by_cycle: dict[int, list[dict[str, Any]]] = {}
    with trace.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            events_by_cycle.setdefault(event["occurrence_cycle"], []).append(event)
    stim_cycles = metadata["run"]["stim_cycles"]
    model = MovingBlockTreeModel(16, 2)
    pending: list[int | None] = [None] * 16
    lines = []
    for _ in range(2):
        result = model.step([False] * 16, [0] * 16, True, rst_n=False)
        lines.append(vector_line(False, [False] * 16, [0] * 16, result))
    for cycle in range(stim_cycles + 10000):
        for event in events_by_cycle.get(cycle, ()):
            source = event["logical_source"]
            if pending[source] is None:
                # The frozen DUT contract exposes only the logical-source
                # address.  tb_only_event_id remains outside the RTL pins.
                pending[source] = source
        valid = [item is not None for item in pending]
        payload = [item or 0 for item in pending]
        result = model.step(valid, payload, True)
        lines.append(vector_line(True, valid, payload, result))
        for source, did_accept in enumerate(result.source_ready):
            if did_accept:
                pending[source] = None
        if (
            cycle >= stim_cycles
            and not any(item is not None for item in pending)
            and model.occupancy() == 0
        ):
            break
    else:
        raise ReplayError(f"RTL vector drain timeout: {trace}")
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return len(lines)


def replay_suite(
    common_root: pathlib.Path,
    suite: str,
    generated_root: pathlib.Path,
    official: dict[str, Any],
    rtl_vectors_dir: pathlib.Path | None,
) -> dict[str, Any]:
    config = official["SUITES"][suite]
    manifest = common_root / "benchmarks/clean_slate_aer" / config["manifest_name"]
    if sha256(manifest) != config["manifest_sha256"]:
        raise ReplayError(f"{suite}: manifest SHA mismatch")
    output_dir = generated_root / suite
    if output_dir.exists():
        raise ReplayError(f"generated output already exists: {output_dir}")
    generator = common_root / "benchmarks/clean_slate_aer/generate_trace.py"
    command_output(
        [
            sys.executable, "-B", str(generator), "--manifest", str(manifest),
            "--output-dir", str(output_dir),
        ],
        common_root,
    )
    index = json.loads((output_dir / "generation-index.json").read_text())
    names = tuple(item["run"]["name"] for item in index["runs"])
    if index.get("generator_version") != EXPECTED_GENERATOR_VERSION:
        raise ReplayError(f"{suite}: generator version mismatch")
    if names != tuple(config["names"]):
        raise ReplayError(f"{suite}: exact run set/order mismatch")

    by_name = {item["run"]["name"]: item for item in index["runs"]}
    fixed_metrics: list[RunMetrics] = []
    moving_metrics: list[RunMetrics] = []
    runs = []
    rtl_vectors = []
    trace_hashes = official["TRACE_SHA256"]
    for name in names:
        metadata = by_name[name]
        trace = output_dir / metadata["trace_file"]
        actual_sha = sha256(trace)
        if actual_sha != trace_hashes[name] or metadata["trace_sha256"] != actual_sha:
            raise ReplayError(f"{suite}/{name}: frozen trace SHA mismatch")
        if (
            metadata.get("event_identity_mode") != "address_only"
            or metadata.get("generator_version") != EXPECTED_GENERATOR_VERSION
            or metadata["run"]["geometry"] != {"width": 4, "height": 4}
            or metadata["run"].get("sink") != {"mode": "always"}
        ):
            raise ReplayError(f"{suite}/{name}: metadata contract mismatch")
        occurrences = load_occurrences(trace)
        fixed = run_occurrences(MovingBlockTreeModel(16, 1), occurrences, [True])
        moving = run_occurrences(MovingBlockTreeModel(16, 2), occurrences, [True])
        if fixed.accepted != fixed.retired or moving.accepted != moving.retired:
            raise ReplayError(f"{suite}/{name}: accepted event did not drain")
        fixed_metrics.append(fixed)
        moving_metrics.append(moving)
        runs.append(
            {
                "name": name,
                "trace_sha256": actual_sha,
                "event_count": metadata["event_count"],
                "fixed": metric_document(fixed),
                "moving": metric_document(moving),
            }
        )
        if rtl_vectors_dir is not None and suite == "full50" and name in REPRESENTATIVE_RTL_TRACES:
            rtl_vectors_dir.mkdir(parents=True, exist_ok=True)
            vector_path = rtl_vectors_dir / f"{name}.vectors.txt"
            vector_cycles = write_rtl_vectors(trace, metadata, vector_path)
            (rtl_vectors_dir / f"{name}.json").write_text(
                json.dumps(
                    {
                        "name": name,
                        "trace_sha256": actual_sha,
                        "vector_cycles": vector_cycles,
                        "max_advance": 2,
                    },
                    indent=2,
                    sort_keys=True,
                ) + "\n"
            )
            rtl_vectors.append(
                {
                    "name": name,
                    "trace_sha256": actual_sha,
                    "vector_cycles": vector_cycles,
                    "vector_sha256": sha256(vector_path),
                    "max_advance": 2,
                }
            )
    return {
        "manifest": config["manifest_name"],
        "manifest_sha256": config["manifest_sha256"],
        "run_count": len(names),
        "fixed": aggregate(fixed_metrics),
        "moving": aggregate(moving_metrics),
        "runs": runs,
        "rtl_vectors": rtl_vectors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-root", required=True, type=pathlib.Path)
    parser.add_argument("--suite", choices=("full50", "capacity22", "all"), default="all")
    parser.add_argument("--generated-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--rtl-vectors-dir", type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists() or args.generated_root.exists():
        raise SystemExit("output/generated path collision")

    common_root = args.common_root.resolve()
    generator = common_root / "benchmarks/clean_slate_aer/generate_trace.py"
    official_path = common_root / "scripts/common_suite_official.py"
    if sha256(generator) != EXPECTED_GENERATOR_SHA256:
        raise SystemExit("generator-v4 source SHA mismatch")
    if sha256(official_path) != EXPECTED_OFFICIAL_SHA256:
        raise SystemExit("official suite policy SHA mismatch")
    sys.dont_write_bytecode = True
    official = runpy.run_path(str(official_path))
    if official.get("GENERATOR_VERSION") != EXPECTED_GENERATOR_VERSION:
        raise SystemExit("official policy generator version mismatch")

    candidate_root = pathlib.Path(__file__).resolve().parents[3]
    suites = ("full50", "capacity22") if args.suite == "all" else (args.suite,)
    common_tracked_status = command_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], common_root
    )
    if common_tracked_status:
        raise SystemExit("common tree has tracked modifications; replay refused")
    document = {
        "schema_version": 1,
        "candidate": "a4-moving-block-max-advance-2",
        "reference": "a4-fixed-one-step-max-advance-1",
        "qualification": "LOCAL_MODEL_REPLAY_ONLY",
        "common_qualification": "HOLD",
        "ppa_qualification": "HOLD",
        "provenance": {
            "common_root": str(common_root),
            "common_head": command_output(["git", "rev-parse", "HEAD"], common_root),
            "common_declared_source_commit": official["SOURCE_COMMIT"],
            "common_tracked_status": common_tracked_status,
            "generator_version": EXPECTED_GENERATOR_VERSION,
            "generator_sha256": EXPECTED_GENERATOR_SHA256,
            "official_policy_sha256": EXPECTED_OFFICIAL_SHA256,
            "candidate_head": command_output(["git", "rev-parse", "HEAD"], candidate_root),
            "candidate_rtl_sha256": sha256(candidate_root / "rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.sv"),
            "candidate_model_sha256": sha256(candidate_root / "rtl/candidates/a4_moving_block_tree/model.py"),
        },
        "suites": {},
    }
    for suite in suites:
        document["suites"][suite] = replay_suite(
            common_root,
            suite,
            args.generated_root,
            official,
            args.rtl_vectors_dir,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        "A4_GENERATOR_V4_REPLAY_PASS "
        + " ".join(
            f"{name}={document['suites'][name]['run_count']}" for name in suites
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
