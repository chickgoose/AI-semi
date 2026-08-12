#!/usr/bin/env python3
"""Hash-locked read-only replay of frozen generator-v4 suites."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import runpy
import subprocess
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from model import PairedCorticalColumnK2  # noqa: E402

EXPECTED_GENERATOR_SHA256 = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
EXPECTED_OFFICIAL_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
EXPECTED_GENERATOR_VERSION = "4.0"


class ReplayError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(arguments: list[str], cwd: pathlib.Path) -> str:
    result = subprocess.run(
        arguments, cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False
    )
    if result.returncode:
        raise ReplayError(f"command failed: {' '.join(arguments)}\n{result.stdout}")
    return result.stdout.strip()


def load_events(path: pathlib.Path) -> dict[int, list[tuple[int, int]]]:
    by_cycle: dict[int, list[tuple[int, int]]] = {}
    seen_ids: set[int] = set()
    seen_source_cycle: set[tuple[int, int]] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            event = json.loads(line)
            cycle = event.get("occurrence_cycle")
            source = event.get("logical_source")
            event_id = event.get("tb_only_event_id")
            if not isinstance(cycle, int) or cycle < 0:
                raise ReplayError(f"{path}:{line_number}: invalid cycle")
            if not isinstance(source, int) or not 0 <= source < 16:
                raise ReplayError(f"{path}:{line_number}: invalid source")
            if not isinstance(event_id, int) or event_id in seen_ids:
                raise ReplayError(f"{path}:{line_number}: duplicate event identity")
            if (cycle, source) in seen_source_cycle:
                raise ReplayError(f"{path}:{line_number}: repeated source/cycle")
            seen_ids.add(event_id)
            seen_source_cycle.add((cycle, source))
            by_cycle.setdefault(cycle, []).append((source, event_id))
    return by_cycle


def vector_line(rst_n: bool, valid: int, result: Any) -> str:
    return (
        f"{int(rst_n)} {valid:04x} 1 {result.source_ready:04x} "
        f"{result.grant_count:x} {result.grant_addr0:x} "
        f"{result.grant_addr1:x} {int(result.drain_idle)}"
    )


def model_and_vectors(
    trace: pathlib.Path, metadata: dict[str, Any], output: pathlib.Path
) -> dict[str, int]:
    events = load_events(trace)
    model = PairedCorticalColumnK2()
    pending: list[int | None] = [None] * 16
    offered = accepted = overrun = committed = 0
    lines = []
    for _ in range(2):
        result = model.step(0, True, rst_n=False)
        lines.append(vector_line(False, 0, result))
    stim_cycles = metadata["run"]["stim_cycles"]
    for cycle in range(stim_cycles + 10001):
        for source, event_id in events.get(cycle, ()):
            offered += 1
            if pending[source] is None:
                pending[source] = event_id
                accepted += 1
            else:
                overrun += 1
        valid = sum((item is not None) << source for source, item in enumerate(pending))
        result = model.step(valid, True)
        lines.append(vector_line(True, valid, result))
        if result.source_ready.bit_count() != result.grant_count:
            raise ReplayError(f"non-atomic commit in {trace}")
        for source in range(16):
            if result.source_ready & (1 << source):
                if pending[source] is None:
                    raise ReplayError(f"phantom commit in {trace}")
                pending[source] = None
                committed += 1
        if cycle >= stim_cycles and not any(item is not None for item in pending):
            if result.drain_idle:
                break
    else:
        raise ReplayError(f"drain timeout: {trace}")
    if offered != accepted + overrun or accepted != committed:
        raise ReplayError(f"conservation failure: {trace}")
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {
        "cycles": len(lines), "offered": offered, "accepted": accepted,
        "overrun": overrun, "committed": committed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-root", required=True, type=pathlib.Path)
    parser.add_argument("--generated-root", required=True, type=pathlib.Path)
    parser.add_argument("--vectors-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if any(path.exists() for path in (args.generated_root, args.vectors_root, args.output)):
        raise SystemExit("replay refuses existing output paths")

    common = args.common_root.resolve()
    generator = common / "benchmarks/clean_slate_aer/generate_trace.py"
    official_path = common / "scripts/common_suite_official.py"
    if not generator.is_file() or not official_path.is_file():
        raise SystemExit("required frozen-v4 common sources absent")
    if sha256(generator) != EXPECTED_GENERATOR_SHA256:
        raise SystemExit("generator-v4 source SHA mismatch")
    if sha256(official_path) != EXPECTED_OFFICIAL_SHA256:
        raise SystemExit("official suite policy SHA mismatch")
    if command(["git", "status", "--porcelain", "--untracked-files=no"], common):
        raise SystemExit("common tree has tracked modifications; replay refused")
    sys.dont_write_bytecode = True
    official = runpy.run_path(str(official_path))
    if official.get("GENERATOR_VERSION") != EXPECTED_GENERATOR_VERSION:
        raise SystemExit("generator version mismatch")

    args.generated_root.mkdir(parents=True)
    args.vectors_root.mkdir(parents=True)
    suites: dict[str, Any] = {}
    for suite in ("full50", "capacity22"):
        config = official["SUITES"][suite]
        manifest = common / "benchmarks/clean_slate_aer" / config["manifest_name"]
        if sha256(manifest) != config["manifest_sha256"]:
            raise ReplayError(f"{suite}: manifest SHA mismatch")
        generated = args.generated_root / suite
        command([
            sys.executable, "-B", str(generator), "--manifest", str(manifest),
            "--output-dir", str(generated),
        ], common)
        index = json.loads((generated / "generation-index.json").read_text())
        names = tuple(item["run"]["name"] for item in index["runs"])
        if index.get("generator_version") != EXPECTED_GENERATOR_VERSION:
            raise ReplayError(f"{suite}: generated version mismatch")
        if names != tuple(config["names"]):
            raise ReplayError(f"{suite}: exact run set/order mismatch")
        vector_suite = args.vectors_root / suite
        vector_suite.mkdir()
        records = []
        for metadata in index["runs"]:
            name = metadata["run"]["name"]
            trace = generated / metadata["trace_file"]
            trace_sha = sha256(trace)
            if trace_sha != official["TRACE_SHA256"][name] or trace_sha != metadata["trace_sha256"]:
                raise ReplayError(f"{suite}/{name}: trace SHA mismatch")
            if metadata.get("event_identity_mode") != "address_only":
                raise ReplayError(f"{suite}/{name}: not address-only")
            if metadata["run"].get("sink") != {"mode": "always"}:
                raise ReplayError(f"{suite}/{name}: sink contract mismatch")
            vector = vector_suite / f"{name}.vectors"
            metrics = model_and_vectors(trace, metadata, vector)
            records.append({
                "name": name, "trace_sha256": trace_sha,
                "vector_sha256": sha256(vector), **metrics,
            })
        suites[suite] = {
            "manifest_sha256": config["manifest_sha256"],
            "run_count": len(records), "runs": records,
        }

    document = {
        "schema": "a4_pcck2_frozen_v4_replay_v1",
        "qualification": "LOCAL_MODEL_AND_RTL_VECTORS",
        "common_qualification": "HOLD",
        "provenance": {
            "common_head": command(["git", "rev-parse", "HEAD"], common),
            "common_declared_source_commit": official["SOURCE_COMMIT"],
            "generator_version": EXPECTED_GENERATOR_VERSION,
            "generator_sha256": EXPECTED_GENERATOR_SHA256,
            "official_policy_sha256": EXPECTED_OFFICIAL_SHA256,
        },
        "suites": suites,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("A4_PCCK2_FROZEN_V4_PASS full50=50 capacity22=22")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
