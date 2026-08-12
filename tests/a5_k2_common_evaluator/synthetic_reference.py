#!/usr/bin/env python3
"""Test-only transaction producer used to qualify the evaluator and mutations.

This is not candidate RTL evidence and must never be published as a hardware
PASS.  It exists so every semantic gate has a known-good and known-bad input.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from evaluate_k2 import candidate_identity_sha256, contract_document, digest_bytes
from k2_oracle import (
    EVIDENCE_SCHEMA, RUN_ARTIFACT_SCHEMA, PolicyState, fold_prefix, run_sha,
    advance_actual, object_sha256,
)


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
            "id": candidate_id,
            "claims": {"full_future_trace_equivalence": False},
        },
        "vector_bundle_sha256": bundle["bundle_sha256"],
        "runs": run_documents,
    }


def _file_record(path: Path, digest_kind: str) -> dict[str, str]:
    content = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "digest_kind": digest_kind,
        "digest": digest_bytes(content, digest_kind),
    }


def materialize_owner_fixture(bundle: dict[str, Any], destination: Path, owner_dir: Path,
                              candidate_id: str,
                              policy_class: str = "exact_weighted_scalar_prefix_k2") -> tuple[Path, dict[str, Any]]:
    """Create separate, hash-bound run files around a real three-file owner fixture."""
    destination.mkdir(parents=True, exist_ok=False)
    candidate = {
        "id": candidate_id,
        "source": _file_record(owner_dir / "source.sv", "git_blob_sha1"),
        "binding": _file_record(owner_dir / "binding.sv", "sha256"),
        "runner": _file_record(owner_dir / "runner.py", "sha256"),
        "contract": contract_document(policy_class),
        "claims": {"full_future_trace_equivalence": False},
    }
    identity_sha256 = candidate_identity_sha256(candidate)
    contract_sha256 = object_sha256(candidate["contract"])
    reference = build_reference_evidence(bundle, candidate_id)
    runs = []
    for observed in reference["runs"]:
        artifact_document = {
            "schema": RUN_ARTIFACT_SCHEMA,
            "candidate_identity_sha256": identity_sha256,
            "contract_sha256": contract_sha256,
            "vector_bundle_sha256": bundle["bundle_sha256"],
            "name": observed["name"],
            "run_sha256": observed["run_sha256"],
            "cycles": observed["cycles"],
        }
        artifact_path = destination / f"{observed['name']}.run.json"
        artifact_path.write_text(
            json.dumps(artifact_document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        runs.append({
            "name": observed["name"],
            "run_sha256": observed["run_sha256"],
            "artifact": _file_record(artifact_path, "sha256"),
        })
    document = {
        "schema": EVIDENCE_SCHEMA,
        "candidate": candidate,
        "vector_bundle_sha256": bundle["bundle_sha256"],
        "runs": runs,
    }
    evidence_path = destination / "evidence.json"
    evidence_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence_path, document


def clone(document: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(document)
