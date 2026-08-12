#!/usr/bin/env python3
"""Test-only transaction producer used to qualify the evaluator and mutations.

This is not candidate RTL evidence and must never be published as a hardware
PASS.  It exists so every semantic gate has a known-good and known-bad input.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from k2_oracle import EVIDENCE_SCHEMA, PolicyState, fold_prefix, run_sha, advance_actual


TEST_SHA = "0" * 64


def build_reference_evidence(bundle: dict[str, Any], candidate_id: str = "synthetic-reference") -> dict[str, Any]:
    run_documents = []
    for vector in bundle["runs"]:
        pending: dict[int, dict[str, Any]] = {}
        queue: list[dict[str, Any]] = []
        state = PolicyState()
        observations = []
        for stimulus in vector["cycles"]:
            cycle = stimulus["cycle"]
            if not stimulus["reset_n"]:
                pending.clear()
                queue.clear()
                state = PolicyState()
                observations.append({
                    "cycle": cycle, "accepts": [],
                    "outputs": [{"lane": 0, "valid": False}, {"lane": 1, "valid": False}],
                    "drain_idle": False,
                })
                continue
            for item in stimulus["occurrences"]:
                if item["source"] not in pending:
                    pending[item["source"]] = item
            outputs = []
            for lane in range(2):
                if lane < len(queue):
                    item = queue[lane]
                    outputs.append({"lane": lane, "valid": True,
                                    "source": item["source"], "event_id": item["event_id"]})
                else:
                    outputs.append({"lane": lane, "valid": False})
            ready = stimulus["retire_ready"]
            # Ordered K2 retirement does not expose a fireable younger lane
            # while the head lane is stalled.
            if outputs[0]["valid"] and not ready[0]:
                outputs[1] = {"lane": 1, "valid": False}
            retired = 0
            if queue and ready[0]:
                retired = 1
                if len(queue) > 1 and ready[1]:
                    retired = 2
            if retired:
                del queue[:retired]
            free = 2 - len(queue)
            grants, _ = fold_prefix(pending, state, free)
            accepts = []
            for slot, source in enumerate(grants):
                item = pending.pop(source)
                accepts.append({"slot": slot, "source": source, "event_id": item["event_id"]})
                queue.append(item)
                state = advance_actual(state, source)
            drain_idle = not pending and not queue and not any(output["valid"] for output in outputs)
            observations.append({
                "cycle": cycle, "accepts": accepts, "outputs": outputs,
                "drain_idle": drain_idle,
            })
        run_documents.append({
            "name": vector["name"], "run_sha256": run_sha(vector), "cycles": observations,
        })
    return {
        "schema": EVIDENCE_SCHEMA,
        "candidate": {
            "id": candidate_id, "source_sha256": TEST_SHA, "binding_sha256": TEST_SHA,
            "runner_sha256": TEST_SHA,
            "claims": {"full_future_trace_equivalence": False},
            "qualification": "TEST_ONLY_NOT_RTL_EVIDENCE",
        },
        "vector_bundle_sha256": bundle["bundle_sha256"],
        "runs": run_documents,
    }


def clone(document: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(document)
