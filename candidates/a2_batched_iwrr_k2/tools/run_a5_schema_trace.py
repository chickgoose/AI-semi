#!/usr/bin/env python3
"""Emit A5-41c425b evidence through an explicitly separate output adapter.

The scheduler accepts every offered bundle atomically.  A FIFO-like behavioral
link adapter then presents the accepted events on two ordered retire lanes.  It
never feeds per-lane retirement back into scheduler policy.  This tool formats
evidence; it does not claim that A2's interleaved calendar matches A5's scalar
wheel oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from batched_iwrr_k2 import Scheduler  # noqa: E402


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invalid_outputs() -> list[dict]:
    return [{"lane": 0, "valid": False}, {"lane": 1, "valid": False}]


def adapter_outputs(queue: list[dict], ready: list[bool]) -> tuple[list[dict], int]:
    """Present ordered lanes without allowing a younger lane to bypass lane 0."""
    outputs = invalid_outputs()
    if queue:
        outputs[0] = {"lane": 0, "valid": True, **queue[0]}
    # Lane 1 is exposed only when both entries retire on this edge.  This is a
    # conservative adapter choice that avoids partial presentation state.
    if len(queue) >= 2 and ready[0] and ready[1]:
        outputs[1] = {"lane": 1, "valid": True, **queue[1]}
    retired = int(outputs[0]["valid"] and ready[0])
    retired += int(outputs[1]["valid"] and ready[1])
    return outputs, retired


def run_trace(vector_run: dict) -> dict:
    scheduler = Scheduler()
    pending: dict[int, dict] = {}
    link_queue: list[dict] = []
    cycles = []
    for stimulus in vector_run["cycles"]:
        cycle = int(stimulus["cycle"])
        if not stimulus["reset_n"]:
            scheduler.reset()
            pending.clear()
            link_queue.clear()
            cycles.append({
                "cycle": cycle, "accepts": [], "outputs": invalid_outputs(),
                "drain_idle": False,
            })
            continue

        for occurrence in stimulus["occurrences"]:
            source = int(occurrence["source"])
            if source not in pending:
                pending[source] = occurrence

        outputs, retired = adapter_outputs(link_queue, stimulus["retire_ready"])
        req = sum(1 << source for source in pending)
        offer = scheduler.cycle(req, True)
        accepts = []
        for slot, source in enumerate(offer.address[:offer.count]):
            occurrence = pending.pop(source)
            record = {"source": source, "event_id": occurrence["event_id"]}
            accepts.append({"slot": slot, **record})
            link_queue.append(record)
        if retired:
            del link_queue[:retired]
        cycles.append({
            "cycle": cycle,
            "accepts": accepts,
            "outputs": outputs,
            "drain_idle": not pending and not link_queue and not any(
                output["valid"] for output in outputs),
        })
    return {
        "name": vector_run["name"],
        "run_sha256": vector_run["run_sha256"],
        "cycles": cycles,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    vectors = json.loads(args.vectors.read_text(encoding="utf-8"))
    if vectors.get("schema") != "a5_k2_vector_bundle_v1":
        raise SystemExit("A2_K2_A5_TRACE_FAIL wrong vector schema")
    document = {
        "schema": "a5_k2_candidate_evidence_v1",
        "candidate": {
            "id": "a2-batched-iwrr-k2",
            "source_sha256": file_sha256(ROOT / "rtl/a2_batched_iwrr_k2.sv"),
            "binding_sha256": file_sha256(Path(__file__).resolve()),
            "runner_sha256": file_sha256(ROOT / "model/batched_iwrr_k2.py"),
            "claims": {"full_future_trace_equivalence": False},
        },
        "vector_bundle_sha256": vectors["bundle_sha256"],
        "runs": [run_trace(run) for run in vectors["runs"]],
    }
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(f"A2_K2_A5_SCHEMA_TRACE_PASS runs={len(document['runs'])} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
