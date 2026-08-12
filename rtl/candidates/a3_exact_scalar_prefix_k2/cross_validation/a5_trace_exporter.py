#!/usr/bin/env python3
"""Export honest A5 transaction evidence for the candidate's canonical policy.

The scheduler remains atomic.  A small ordered two-entry link is modeled only
after scheduler commit so A5's independent retire-ready observations cannot
partially advance scheduler policy.  This exporter intentionally does not
substitute A5's row-wheel oracle for the candidate's Ganghee Fovea policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE))
from oracle import PolicyState, scalar_prefix_k2  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def output_record(lane: int, item: dict | None) -> dict:
    if item is None:
        return {"lane": lane, "valid": False}
    return {
        "lane": lane,
        "valid": True,
        "source": int(item["source"]),
        "event_id": str(item["event_id"]),
    }


def export_run(vector: dict) -> dict:
    pending: dict[int, dict] = {}
    link: list[dict] = []
    policy = PolicyState()
    held: tuple[int, ...] | None = None
    held_post = PolicyState()
    observations = []

    for stimulus in vector["cycles"]:
        cycle = int(stimulus["cycle"])
        if not stimulus["reset_n"]:
            pending.clear()
            link.clear()
            policy = PolicyState()
            held = None
            held_post = PolicyState()
            observations.append({
                "cycle": cycle,
                "accepts": [],
                "outputs": [output_record(0, None), output_record(1, None)],
                "drain_idle": False,
            })
            continue

        # A5 defines occurrence admission before scheduler acceptance at the
        # same indexed edge.  Occupied-source occurrences are left for the A5
        # evaluator to classify as overrun and never replace the old identity.
        for occurrence in stimulus["occurrences"]:
            source = int(occurrence["source"])
            if source not in pending:
                pending[source] = occurrence

        ready0, ready1 = map(bool, stimulus["retire_ready"])
        lane0 = link[0] if link else None
        # Do not expose the younger physical lane until both its own sink and
        # the ordered head are ready.  Thus an independently stalled record
        # remains an internal link entry and can compact to lane 0 after the
        # older record retires, without ever touching scheduler policy.
        lane1 = link[1] if len(link) > 1 and ready0 and ready1 else None
        outputs = [output_record(0, lane0), output_record(1, lane1)]

        # This is the separate ordered buffered-link boundary.  A blocked head
        # hides the younger record.  Partial link drain never touches policy.
        retired = 0
        if link and ready0:
            retired = 1
            if len(link) > 1 and ready1:
                retired = 2
        if retired:
            del link[:retired]

        # A5 observes transaction-level scheduler acceptance rather than the
        # candidate's registered presentation latency.  Prepare and hold the
        # exact atomic offer, then commit it only when the downstream link is
        # empty.  This is the same policy fold as the RTL/oracle lockstep.
        if held is None:
            pending_mask = sum(1 << source for source in pending)
            held, held_post = scalar_prefix_k2(pending_mask, policy)
            if not held:
                held = None
        fired: tuple[int, ...] = ()
        if not link and held is not None:
            fired = held
            policy = held_post
            held = None
        accepts = []
        if fired:
            if link:
                raise RuntimeError("atomic scheduler committed into a nonempty link")
            for slot, source in enumerate(fired):
                item = pending.pop(source, None)
                if item is None:
                    raise RuntimeError(f"scheduler accepted nonpending source {source}")
                accepts.append({
                    "slot": slot,
                    "source": source,
                    "event_id": item["event_id"],
                })
                link.append(item)

        drain_idle = not pending and not link and not any(
            output["valid"] for output in outputs
        ) and held is None
        observations.append({
            "cycle": cycle,
            "accepts": accepts,
            "outputs": outputs,
            "drain_idle": drain_idle,
        })

    return {
        "name": vector["name"],
        "run_sha256": vector["run_sha256"],
        "cycles": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-id", default="a3-exact-scalar-prefix-k2")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")

    bundle = json.loads(args.vectors.read_text(encoding="utf-8"))
    if bundle.get("schema") != "a5_k2_vector_bundle_v1":
        raise SystemExit("unexpected A5 vector schema")
    rtl = CANDIDATE / "rtl/a3_exact_scalar_prefix_k2.sv"
    runner = CANDIDATE / "run.py"
    evidence = {
        "schema": "a5_k2_candidate_evidence_v1",
        "candidate": {
            "id": args.candidate_id,
            "source_sha256": sha256(rtl),
            "binding_sha256": sha256(Path(__file__)),
            "runner_sha256": sha256(runner),
            "claims": {"full_future_trace_equivalence": False},
        },
        "vector_bundle_sha256": bundle["bundle_sha256"],
        "runs": [export_run(run) for run in bundle["runs"]],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"A3_K2_A5_TRACE_EXPORTED runs={len(evidence['runs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
