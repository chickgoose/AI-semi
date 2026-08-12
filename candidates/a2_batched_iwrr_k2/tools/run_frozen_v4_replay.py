#!/usr/bin/env python3
"""Replay pinned generator-v4 N16 suites through the independent K2 model."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from batched_iwrr_k2 import Scheduler  # noqa: E402

PINNED = {
    "benchmarks/clean_slate_aer/generate_trace.py":
        "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
    "benchmarks/clean_slate_aer/manifest.neutrality-n16.json":
        "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "benchmarks/clean_slate_aer/manifest.multilane-n16.json":
        "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inputs(repo: Path) -> None:
    if not repo.is_absolute():
        raise RuntimeError("--a1-repo must be absolute")
    for relative, expected in PINNED.items():
        path = repo / relative
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(f"frozen-v4 SHA mismatch {relative}: {observed}")
    text = (repo / "benchmarks/clean_slate_aer/generate_trace.py").read_text(encoding="utf-8")
    if 'GENERATOR_VERSION = "4.0"' not in text:
        raise RuntimeError("generator is not frozen v4")


def replay_run(trace_path: Path, run: dict) -> dict:
    events_by_cycle: dict[int, list[dict]] = {}
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        events_by_cycle.setdefault(int(event["occurrence_cycle"]), []).append(event)
    model = Scheduler()
    pending: dict[int, tuple[int, int]] = {}
    generated = accepted = overrun = measured = 0
    max_wait = 0
    accepted_ids: set[int] = set()
    stim_cycles = int(run["run"]["stim_cycles"])
    cycle = 0
    while cycle < stim_cycles or pending:
        if cycle < stim_cycles:
            for event in events_by_cycle.get(cycle, []):
                generated += 1
                source = int(event["logical_source"])
                identity = int(event["tb_only_event_id"])
                if source in pending:
                    overrun += 1
                else:
                    pending[source] = (identity, cycle)
        req = sum(1 << source for source in pending)
        result = model.cycle(req, True)
        for lane in range(2):
            if not result.valid[lane]:
                continue
            source = result.address[lane]
            if source not in pending:
                raise RuntimeError(f"phantom grant run={run['run']['name']} source={source}")
            identity, occurrence = pending.pop(source)
            if identity in accepted_ids:
                raise RuntimeError(f"duplicate identity run={run['run']['name']} id={identity}")
            accepted_ids.add(identity)
            accepted += 1
            if cycle < stim_cycles:
                measured += 1
            max_wait = max(max_wait, cycle - occurrence)
        cycle += 1
        if cycle > stim_cycles + 10000:
            raise RuntimeError(f"drain timeout run={run['run']['name']}")
    if generated != accepted + overrun or len(accepted_ids) != accepted:
        raise RuntimeError(f"accounting failure run={run['run']['name']}")
    return {
        "name": run["run"]["name"],
        "generated": generated,
        "accepted": accepted,
        "source_overrun": overrun,
        "measurement_delivered": measured,
        "measurement_cycles": stim_cycles,
        "fixed_window_event_per_cycle": measured / stim_cycles,
        "max_request_wait": max_wait,
        "drain_cycles": cycle - stim_cycles,
    }


def aggregate(rows: list[dict]) -> dict:
    generated = sum(row["generated"] for row in rows)
    accepted = sum(row["accepted"] for row in rows)
    overrun = sum(row["source_overrun"] for row in rows)
    measured = sum(row["measurement_delivered"] for row in rows)
    cycles = sum(row["measurement_cycles"] for row in rows)
    return {
        "runs": len(rows), "generated": generated, "accepted": accepted,
        "delivered": accepted, "source_overrun": overrun,
        "measurement_delivered": measured, "measurement_cycles": cycles,
        "fixed_window_event_per_cycle": measured / cycles,
        "max_request_wait": max(row["max_request_wait"] for row in rows),
        "max_drain_cycles": max(row["drain_cycles"] for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a1-repo", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        repo = args.a1_repo.resolve()
        verify_inputs(repo)
        suites = {
            "full50": "manifest.neutrality-n16.json",
            "capacity22": "manifest.multilane-n16.json",
        }
        result = {
            "schema": "a2_batched_iwrr_k2_frozen_v4_replay_v1",
            "generator_version": "4.0", "pinned_inputs": PINNED,
            "semantics": "single_pending_per_source_atomic_ready_waive_empty_no_borrow",
            "suites": {},
        }
        with tempfile.TemporaryDirectory(prefix="a2-k2-frozen-v4-") as temporary:
            for label, manifest in suites.items():
                out = Path(temporary) / label
                command = [sys.executable, str(repo / "benchmarks/clean_slate_aer/generate_trace.py"),
                           "--manifest", str(repo / "benchmarks/clean_slate_aer" / manifest),
                           "--output-dir", str(out)]
                completed = subprocess.run(command, text=True, capture_output=True)
                if completed.returncode:
                    raise RuntimeError(f"generator failed {label}: {completed.stderr.strip()}")
                index = json.loads((out / "generation-index.json").read_text(encoding="utf-8"))
                if index.get("generator_version") != "4.0":
                    raise RuntimeError(f"generated index version mismatch {label}")
                rows = [replay_run(out / entry["trace_file"], entry) for entry in index["runs"]]
                result["suites"][label] = aggregate(rows)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"A2_K2_FROZEN_V4_REPLAY_PASS output={args.output} "
              f"full50={result['suites']['full50']['runs']} "
              f"capacity22={result['suites']['capacity22']['runs']}")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"A2_K2_FROZEN_V4_REPLAY_FAIL {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
