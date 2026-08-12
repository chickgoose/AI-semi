#!/usr/bin/env python3
"""Validate and compare exactly three candidate-owned K2 evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePath
import stat
import sys
from typing import Any

from k2_oracle import (
    ContractError, EVIDENCE_SCHEMA, PolicyState, RESULT_SCHEMA, RUN_ARTIFACT_SCHEMA, RETIRE_LANES,
    SOURCE_COUNT, advance_actual, fold_prefix, latency_summary,
    load_json, object_sha256, row_for_source, validate_vector_bundle,
)


IDENTITY_COMPONENTS = ("source", "binding", "runner")
FILE_DIGEST_KINDS = {"sha256", "git_blob_sha1"}
POLICY_DEFINITIONS = {
    "exact_weighted_scalar_prefix_k2": (
        "g0=scalar(P,q); g1=scalar(P-{g0},transition(q,g0)); observed accepts are "
        "a contiguous prefix of [g0,g1]; state advances once per accepted event"
    ),
    "batched_iwrr_k2": (
        "fixed two-token phases (1,2),(0,1),(2,3),(1,2),(1,2),(1,2); "
        "compact live winners; empty entitlements are waived without borrow or debt"
    ),
    "paired_row_calendar_proposal_k2": (
        "fixed row-opportunity phases (0,1),(2,1),(2,1),(2,1),(2,1),(2,3); "
        "aggregate row opportunity only; no paired-column claim"
    ),
}
EDGE_DEFINITION = {
    "clock_edge": "indexed_rising_edge",
    "reset_order": "sample_reset_first_and_abort_pre_reset_pending_and_inflight",
    "occurrence_order": "latch_occurrences_before_acceptance_on_same_indexed_edge",
    "acceptance": "accepts_are_ordered_source_handshakes_on_the_indexed_edge",
    "output_sample": "outputs_are_level_values_immediately_before_the_indexed_edge",
    "retirement": "output_valid_and_vector_retire_ready_commit_on_that_indexed_edge",
}
LATENCY_DEFINITION = {
    "unit": "indexed_rising_edges",
    "occurrence_to_accept": "accept_cycle_minus_occurrence_cycle",
    "accept_to_retire": "retire_cycle_minus_accept_cycle",
    "percentile": "nearest_rank_ceiling",
    "cohort": "event_ids_accepted_by_all_compared_candidates_per_run",
}


def failure(failures: list[dict[str, Any]], code: str, cycle: int | None,
            detail: str) -> None:
    failures.append({"code": code, "cycle": cycle, "detail": detail})


def validate_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value):
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return value


def stable_regular_bytes(path: Path, label: str) -> bytes:
    """Read an immutable single-link regular file without following its leaf."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ContractError(f"{label}: O_NOFOLLOW is required")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise ContractError(f"{label}: cannot open regular file {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ContractError(f"{label}: path is not a single-link regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if stable != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ContractError(f"{label}: file changed while read: {path}")
        content = b"".join(chunks)
        if len(content) != before.st_size:
            raise ContractError(f"{label}: file size/read mismatch: {path}")
        return content
    finally:
        os.close(descriptor)


def digest_bytes(content: bytes, kind: str) -> str:
    if kind == "sha256":
        return hashlib.sha256(content).hexdigest()
    if kind == "git_blob_sha1":
        header = f"blob {len(content)}\0".encode("ascii")
        return hashlib.sha1(header + content).hexdigest()
    raise ContractError(f"unsupported digest kind {kind}")


def resolve_record_path(record_path: Any, evidence_path: Path, label: str) -> Path:
    if not isinstance(record_path, str) or not record_path:
        raise ContractError(f"{label}.path must name a regular file")
    supplied = Path(record_path)
    if not supplied.is_absolute() and any(part in {"", ".", ".."} for part in PurePath(record_path).parts):
        raise ContractError(f"{label}.path must be absolute or normalized evidence-relative")
    return supplied if supplied.is_absolute() else evidence_path.parent / supplied


def validate_file_record_bytes(record: Any, evidence_path: Path, label: str,
                               allowed_kinds: set[str] = FILE_DIGEST_KINDS) -> tuple[dict[str, str], bytes]:
    if not isinstance(record, dict) or set(record) != {"path", "digest_kind", "digest"}:
        raise ContractError(f"{label} must contain path, digest_kind, and digest")
    kind = record["digest_kind"]
    if kind not in allowed_kinds:
        raise ContractError(f"{label}.digest_kind must be one of {sorted(allowed_kinds)}")
    expected_length = 64 if kind == "sha256" else 40
    digest = record["digest"]
    if not isinstance(digest, str) or len(digest) != expected_length or any(
            character not in "0123456789abcdef" for character in digest):
        raise ContractError(f"{label}.digest is not a lowercase {kind} digest")
    path = resolve_record_path(record["path"], evidence_path, label)
    content = stable_regular_bytes(path, label)
    if digest_bytes(content, kind) != digest:
        raise ContractError(f"{label}: digest mismatch for {path}")
    return {"path": record["path"], "digest_kind": kind, "digest": digest}, content


def validate_file_record(record: Any, evidence_path: Path, label: str,
                         allowed_kinds: set[str] = FILE_DIGEST_KINDS) -> dict[str, str]:
    checked, _ = validate_file_record_bytes(record, evidence_path, label, allowed_kinds)
    return checked


def validate_contract(contract: Any, path: Path) -> dict[str, Any]:
    if not isinstance(contract, dict) or set(contract) != {"policy", "edge", "latency"}:
        raise ContractError(f"{path}: contract must contain exact policy, edge, and latency definitions")
    policy = contract["policy"]
    if not isinstance(policy, dict) or set(policy) != {"class", "definition"}:
        raise ContractError(f"{path}: malformed policy definition")
    policy_class = policy["class"]
    if policy_class not in POLICY_DEFINITIONS:
        raise ContractError(f"{path}: unsupported policy class {policy_class}")
    if policy["definition"] != POLICY_DEFINITIONS[policy_class]:
        raise ContractError(f"{path}: policy definition does not exactly match class {policy_class}")
    if contract["edge"] != EDGE_DEFINITION:
        raise ContractError(f"{path}: edge definition mismatch")
    if contract["latency"] != LATENCY_DEFINITION:
        raise ContractError(f"{path}: latency definition mismatch")
    return contract


def contract_document(policy_class: str = "exact_weighted_scalar_prefix_k2") -> dict[str, Any]:
    return {
        "policy": {"class": policy_class, "definition": POLICY_DEFINITIONS[policy_class]},
        "edge": dict(EDGE_DEFINITION),
        "latency": dict(LATENCY_DEFINITION),
    }


def candidate_identity_sha256(candidate: dict[str, Any]) -> str:
    bound = {"id": candidate["id"]}
    bound.update({name: candidate[name] for name in IDENTITY_COMPONENTS})
    return object_sha256(bound)


def load_run_artifact(record: dict[str, Any], evidence_path: Path, candidate: dict[str, Any],
                      contract_sha256: str, vectors: dict[str, Any]) -> dict[str, Any]:
    artifact, content = validate_file_record_bytes(
        record, evidence_path, "run.artifact", {"sha256"})
    artifact_path = resolve_record_path(artifact["path"], evidence_path, "run.artifact")
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"run.artifact: invalid JSON {artifact_path}: {error}") from error
    required = {
        "schema", "candidate_identity_sha256", "contract_sha256",
        "vector_bundle_sha256", "name", "run_sha256", "cycles",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ContractError(f"run.artifact: malformed envelope {artifact_path}")
    if document["schema"] != RUN_ARTIFACT_SCHEMA:
        raise ContractError(f"run.artifact: schema must be {RUN_ARTIFACT_SCHEMA}")
    if document["candidate_identity_sha256"] != candidate_identity_sha256(candidate):
        raise ContractError("run.artifact: candidate identity rebound")
    if document["contract_sha256"] != contract_sha256:
        raise ContractError("run.artifact: contract rebound")
    if document["vector_bundle_sha256"] != vectors["bundle_sha256"]:
        raise ContractError("run.artifact: vector bundle mismatch")
    document["_artifact_record"] = artifact
    return document


def validate_evidence(document: Any, vectors: dict[str, Any], path: Path) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema") != EVIDENCE_SCHEMA:
        raise ContractError(f"{path}: schema must be {EVIDENCE_SCHEMA}")
    if set(document) != {"schema", "candidate", "vector_bundle_sha256", "runs"}:
        raise ContractError(f"{path}: evidence fields do not exactly match schema")
    candidate = document.get("candidate")
    required_candidate = {"id", *IDENTITY_COMPONENTS, "contract", "claims"}
    if not isinstance(candidate, dict) or set(candidate) != required_candidate:
        raise ContractError(f"{path}: incomplete candidate identity")
    if not isinstance(candidate["id"], str) or not candidate["id"]:
        raise ContractError(f"{path}: invalid candidate id")
    for key in IDENTITY_COMPONENTS:
        validate_file_record(candidate[key], path, f"{path}: candidate.{key}")
    contract = validate_contract(candidate["contract"], path)
    contract_sha256 = object_sha256(contract)
    claims = candidate.get("claims")
    if not isinstance(claims, dict) or set(claims) != {"full_future_trace_equivalence"} or not isinstance(
            claims.get("full_future_trace_equivalence"), bool):
        raise ContractError(f"{path}: candidate claims must explicitly classify future equivalence")
    if document.get("vector_bundle_sha256") != vectors["bundle_sha256"]:
        raise ContractError(f"{path}: vector bundle SHA mismatch")
    runs = document.get("runs")
    if not isinstance(runs, list):
        raise ContractError(f"{path}: runs must be an array")
    by_name: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict) or set(run) != {"name", "run_sha256", "artifact"} or not isinstance(run.get("name"), str):
            raise ContractError(f"{path}: malformed run evidence")
        if run["name"] in by_name:
            raise ContractError(f"{path}: duplicate run {run['name']}")
        validate_sha(run["run_sha256"], f"{path}: run {run['name']}.run_sha256")
        artifact = load_run_artifact(run["artifact"], path, candidate, contract_sha256, vectors)
        if artifact["name"] != run["name"] or artifact["run_sha256"] != run["run_sha256"]:
            raise ContractError(f"{path}: run artifact envelope mismatch for {run['name']}")
        by_name[run["name"]] = artifact
    expected = {run["name"] for run in vectors["runs"] if "required" in run.get("tags", [])}
    known = {run["name"] for run in vectors["runs"]}
    unknown = sorted(set(by_name) - known)
    if unknown:
        raise ContractError(f"{path}: unknown runs: {', '.join(unknown)}")
    missing = sorted(expected - set(by_name))
    if missing:
        raise ContractError(f"{path}: missing required runs: {', '.join(missing)}")
    return {"candidate": candidate, "contract_sha256": contract_sha256, "runs": by_name}


def validate_outputs(outputs: Any, run_name: str, cycle: int) -> list[dict[str, Any]]:
    if not isinstance(outputs, list) or len(outputs) != RETIRE_LANES:
        raise ContractError(f"{run_name} cycle {cycle}: outputs must contain two lane records")
    checked: list[dict[str, Any]] = []
    for lane, output in enumerate(outputs):
        if not isinstance(output, dict) or output.get("lane") != lane or not isinstance(
                output.get("valid"), bool):
            raise ContractError(f"{run_name} cycle {cycle}: malformed lane {lane}")
        if output["valid"]:
            if not isinstance(output.get("source"), int) or not 0 <= output["source"] < SOURCE_COUNT:
                raise ContractError(f"{run_name} cycle {cycle}: invalid output source")
            if not isinstance(output.get("event_id"), str) or not output["event_id"]:
                raise ContractError(f"{run_name} cycle {cycle}: valid output lacks event_id")
        elif set(output) != {"lane", "valid"}:
            raise ContractError(f"{run_name} cycle {cycle}: invalid output must omit payload fields")
        checked.append(output)
    return checked


def validate_accepts(accepts: Any, run_name: str, cycle: int) -> list[dict[str, Any]]:
    if not isinstance(accepts, list) or len(accepts) > RETIRE_LANES:
        raise ContractError(f"{run_name} cycle {cycle}: accepts must contain at most two records")
    checked: list[dict[str, Any]] = []
    for slot, accept in enumerate(accepts):
        if not isinstance(accept, dict) or accept.get("slot") != slot:
            raise ContractError(f"{run_name} cycle {cycle}: accepts must be a contiguous ordered prefix")
        if not isinstance(accept.get("source"), int) or not 0 <= accept["source"] < SOURCE_COUNT:
            raise ContractError(f"{run_name} cycle {cycle}: invalid accepted source")
        if not isinstance(accept.get("event_id"), str) or not accept["event_id"]:
            raise ContractError(f"{run_name} cycle {cycle}: accepted event lacks event_id")
        checked.append(accept)
    return checked


def evaluate_run(vector: dict[str, Any], observed: dict[str, Any],
                 thresholds: dict[str, Any]) -> dict[str, Any]:
    name = vector["name"]
    if observed.get("run_sha256") != vector["run_sha256"]:
        raise ContractError(f"{name}: observed run SHA mismatch")
    observations = observed.get("cycles")
    if not isinstance(observations, list) or len(observations) != len(vector["cycles"]):
        raise ContractError(f"{name}: observation cycle count mismatch")

    failures: list[dict[str, Any]] = []
    state = PolicyState()
    pending: dict[int, dict[str, Any]] = {}
    generated: dict[str, dict[str, Any]] = {}
    overrun: set[str] = set()
    accept_cycle: dict[str, int] = {}
    accept_source: dict[str, int] = {}
    accepted_order: list[str] = []
    live_order: list[str] = []
    retired_order: list[str] = []
    retire_cycle: dict[str, int] = {}
    reset_aborted: set[str] = set()
    reset_aborted_pending: set[str] = set()
    reset_aborted_inflight: set[str] = set()
    row_counts = [0, 0, 0, 0]
    persistent_rows: list[int] = []
    prefix_opportunities = 0
    prefix_matches = 0
    primary_matches = 0
    second_attempts = 0
    second_matches = 0
    duplicate_cycles = 0
    first_post_reset_accept: int | None = None
    latest_reset_release = 0
    prior_outputs: list[dict[str, Any]] | None = None
    prior_ready = [True, True]
    empty_since: int | None = None
    drain_lags: list[int] = []

    for cycle_index, (stimulus, observation) in enumerate(zip(vector["cycles"], observations)):
        if not isinstance(observation, dict) or observation.get("cycle") != cycle_index:
            raise ContractError(f"{name}: observation sequence mismatch at {cycle_index}")
        accepts = validate_accepts(observation.get("accepts"), name, cycle_index)
        outputs = validate_outputs(observation.get("outputs"), name, cycle_index)
        if not isinstance(observation.get("drain_idle"), bool):
            raise ContractError(f"{name} cycle {cycle_index}: drain_idle must be boolean")
        reset_n = stimulus["reset_n"]
        ready = stimulus["retire_ready"]

        if not reset_n:
            if accepts:
                failure(failures, "reset_accept", cycle_index, "source accepted while reset asserted")
            if any(output["valid"] for output in outputs):
                failure(failures, "reset_output", cycle_index, "retire output valid during reset")
            if observation["drain_idle"]:
                failure(failures, "reset_drain_idle", cycle_index, "drain_idle asserted during reset")
            pending_abort = {item["event_id"] for item in pending.values()}
            inflight_abort = set(live_order)
            reset_aborted_pending.update(pending_abort)
            reset_aborted_inflight.update(inflight_abort)
            reset_aborted.update(pending_abort | inflight_abort)
            pending.clear()
            live_order.clear()
            state = PolicyState()
            prior_outputs = None
            first_post_reset_accept = None
            latest_reset_release = cycle_index + 1
            empty_since = None
            continue

        for item in stimulus["occurrences"]:
            identifier = item["event_id"]
            source = item["source"]
            generated[identifier] = {"cycle": cycle_index, "source": source}
            if source in pending:
                overrun.add(identifier)
            else:
                pending[source] = item

        expected, _ = fold_prefix(pending, state)
        actual_sources = [item["source"] for item in accepts]
        if expected:
            prefix_opportunities += 1
        if actual_sources == expected[:len(actual_sources)]:
            prefix_matches += 1
        else:
            failure(failures, "prefix_mismatch", cycle_index,
                    f"expected prefix {expected}, observed {actual_sources}")
        if actual_sources:
            if actual_sources[0] == (expected[0] if expected else None):
                primary_matches += 1
            else:
                failure(failures, "primary_mismatch", cycle_index,
                        f"expected primary {expected[:1]}, observed {actual_sources[:1]}")
        if len(actual_sources) == 2:
            second_attempts += 1
            if len(expected) == 2 and actual_sources[1] == expected[1]:
                second_matches += 1
        if len(set(actual_sources)) != len(actual_sources):
            duplicate_cycles += 1
            failure(failures, "same_source_duplicate", cycle_index,
                    f"duplicate accepted sources {actual_sources}")

        if "persistent_weight" in vector.get("tags", []):
            for source in actual_sources:
                if len(persistent_rows) < int(
                        thresholds["hard_gates"]["persistent_commit_window"]):
                    persistent_rows.append(row_for_source(source))

        for accept in accepts:
            source = accept["source"]
            identifier = accept["event_id"]
            if source not in pending:
                failure(failures, "accept_nonpending", cycle_index,
                        f"source {source} was not pending")
                continue
            expected_id = pending[source]["event_id"]
            if identifier != expected_id:
                failure(failures, "stale_or_wrong_event", cycle_index,
                        f"source {source} expected {expected_id}, observed {identifier}")
                continue
            if identifier in accept_cycle:
                failure(failures, "duplicate_accept", cycle_index, identifier)
                continue
            del pending[source]
            accept_cycle[identifier] = cycle_index
            accept_source[identifier] = source
            accepted_order.append(identifier)
            live_order.append(identifier)
            row = row_for_source(source)
            row_counts[row] += 1
            state = advance_actual(state, source)
            if first_post_reset_accept is None:
                first_post_reset_accept = cycle_index

        if prior_outputs is not None:
            for lane in range(RETIRE_LANES):
                if prior_outputs[lane]["valid"] and not prior_ready[lane]:
                    if outputs[lane] != prior_outputs[lane]:
                        failure(failures, "lane_stall_corruption", cycle_index,
                                f"lane {lane} changed while stalled")
        if outputs[1]["valid"] and not outputs[0]["valid"]:
            failure(failures, "output_lane_hole", cycle_index,
                    "lane 1 was valid while ordered lane 0 was invalid")
        fires = [output for lane, output in enumerate(outputs)
                 if output["valid"] and ready[lane]]
        if outputs[0]["valid"] and not ready[0] and len(fires) > 0:
            if outputs[1]["valid"] and ready[1]:
                failure(failures, "younger_lane_bypass", cycle_index,
                        "lane 1 fired while older lane 0 was stalled")
        for output in fires:
            identifier = output["event_id"]
            if not live_order:
                failure(failures, "phantom_retire", cycle_index, identifier)
                continue
            expected_id = live_order[0]
            if identifier != expected_id:
                code = "reset_phantom" if identifier in reset_aborted else "retire_order_or_phantom"
                failure(failures, code, cycle_index,
                        f"expected {expected_id}, observed {identifier}")
                if identifier not in live_order:
                    continue
                live_order.remove(identifier)
            else:
                live_order.pop(0)
            if output["source"] != accept_source.get(identifier):
                failure(failures, "retire_source_mismatch", cycle_index, identifier)
            if identifier in retire_cycle:
                failure(failures, "duplicate_retire", cycle_index, identifier)
            retire_cycle[identifier] = cycle_index
            retired_order.append(identifier)

        if first_post_reset_accept is None and any(output["valid"] for output in outputs):
            failure(failures, "reset_phantom", cycle_index,
                    "output appeared before any post-reset acceptance")

        truly_empty = not pending and not live_order and not any(output["valid"] for output in outputs)
        if observation["drain_idle"] and not truly_empty:
            failure(failures, "false_drain_idle", cycle_index,
                    "drain_idle asserted with pending/inflight/output state")
        if truly_empty:
            if empty_since is None:
                empty_since = cycle_index
            if observation["drain_idle"]:
                drain_lags.append(cycle_index - empty_since)
                empty_since = None
        else:
            empty_since = None

        prior_outputs = outputs
        prior_ready = ready

    expected_live = [identifier for identifier in accepted_order if identifier not in reset_aborted]
    if retired_order != expected_live:
        failure(failures, "accepted_retired_order", None,
                f"accepted live {len(expected_live)} retired {len(retired_order)}")
    if pending:
        failure(failures, "pending_after_drain", None, f"sources {sorted(pending)}")
    if live_order:
        failure(failures, "inflight_after_drain", None, f"events {live_order[:4]}")
    unresolved_occurrences = (set(generated) - overrun - reset_aborted_pending -
                              set(accept_cycle))
    if unresolved_occurrences:
        failure(failures, "occurrence_conservation", None,
                f"unclassified events {sorted(unresolved_occurrences)[:4]}")
    unresolved_accepts = set(accept_cycle) - reset_aborted_inflight - set(retire_cycle)
    if unresolved_accepts:
        failure(failures, "accept_conservation", None,
                f"unclassified accepts {sorted(unresolved_accepts)[:4]}")
    if not observations[-1].get("drain_idle"):
        failure(failures, "drain_idle_missing", len(observations) - 1,
                "final cycle is not drained")
    max_drain_lag = int(thresholds["hard_gates"]["drain_idle_max_lag_cycles"])
    if any(lag > max_drain_lag for lag in drain_lags):
        failure(failures, "drain_idle_late", None, f"lags {drain_lags}")

    weight_window = int(thresholds["hard_gates"]["persistent_commit_window"])
    if "persistent_weight" in vector.get("tags", []):
        if len(persistent_rows) < weight_window:
            failure(failures, "weight_window_short", None,
                    f"need {weight_window}, observed {len(persistent_rows)}")
        else:
            counts = [persistent_rows.count(row) for row in range(4)]
            expected_counts = thresholds["hard_gates"]["persistent_row_counts"]
            if counts != expected_counts:
                failure(failures, "false_1_5_5_1", None,
                        f"expected {expected_counts}, observed {counts}")

    occurrence_to_accept = [accept_cycle[identifier] - generated[identifier]["cycle"]
                            for identifier in accept_cycle]
    accept_to_retire = [retire_cycle[identifier] - accept_cycle[identifier]
                        for identifier in retire_cycle if identifier in accept_cycle]
    if "sparse" in vector.get("tags", []):
        sparse_max = max(occurrence_to_accept, default=0)
        threshold = int(thresholds["hard_gates"]["sparse_max_occurrence_to_accept_cycles"])
        if overrun or len(accept_cycle) != len(generated) or sparse_max > threshold:
            failure(failures, "sparse_not_work_conserving", None,
                    f"generated={len(generated)} accepted={len(accept_cycle)} overrun={len(overrun)} max={sparse_max}")

    if any(item["code"] in {"primary_mismatch", "prefix_mismatch"} for item in failures):
        if not any(item["code"] == "primary_mismatch" for item in failures):
            grade = "PRIMARY_ONLY"
        else:
            grade = "FAIL"
    else:
        grade = "FULL"
    measurement_start, measurement_end = vector["measurement_window"]
    retired_in_window = sum(measurement_start <= cycle < measurement_end
                            for cycle in retire_cycle.values())
    return {
        "name": name,
        "origin": vector["origin"],
        "tags": vector.get("tags", []),
        "status": "PASS" if not failures else "FAIL",
        "hard_failures": failures,
        "accounting": {
            "generated": len(generated), "source_overrun": len(overrun),
            "source_overrun_ratio": len(overrun) / len(generated) if generated else 0.0,
            "accepted": len(accept_cycle), "reset_aborted": len(reset_aborted),
            "reset_aborted_pending": len(reset_aborted_pending),
            "reset_aborted_inflight": len(reset_aborted_inflight),
            "retired": len(retire_cycle), "pending_at_end": len(pending),
            "inflight_at_end": len(live_order),
        },
        "prefix": {
            "grade": grade, "opportunities": prefix_opportunities,
            "matching_prefix_cycles": prefix_matches, "matching_primary": primary_matches,
            "second_attempts": second_attempts, "matching_seconds": second_matches,
            "same_source_duplicate_cycles": duplicate_cycles,
        },
        "committed_row_counts": row_counts,
        "persistent_first_120_row_counts": (
            [persistent_rows.count(row) for row in range(4)] if persistent_rows else None),
        "latency_cycles": {
            "occurrence_to_accept": latency_summary(occurrence_to_accept),
            "accept_to_retire": latency_summary(accept_to_retire),
        },
        "fixed_window": {
            "cycles": measurement_end - measurement_start,
            "retired": retired_in_window,
            "event_per_cycle": retired_in_window / (measurement_end - measurement_start),
        },
        "drain_lags": drain_lags,
        "latest_reset_release": latest_reset_release,
        "_accepted_event_ids": sorted(accept_cycle),
        "_occurrence_to_accept_by_id": {
            identifier: accept_cycle[identifier] - generated[identifier]["cycle"]
            for identifier in accept_cycle
        },
        "_accept_to_retire_by_id": {
            identifier: retire_cycle[identifier] - accept_cycle[identifier]
            for identifier in retire_cycle if identifier in accept_cycle
        },
    }


def aggregate(candidate: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [item for run in runs for item in run["hard_failures"]]
    claims = candidate["claims"]
    if claims["full_future_trace_equivalence"]:
        failures.append({
            "code": "future_trace_overclaim", "cycle": None,
            "detail": "K2 frozen-cohort prefix does not imply equivalence under future arrivals",
        })
    return {
        "candidate": candidate,
        "status": "PASS" if not failures else "HOLD",
        "hard_failure_count": len(failures),
        "hard_failures": failures,
        "runs": runs,
    }


def band_delta(left: float | int | None, right: float | int | None,
               absolute: float, relative: float = 0.0) -> str:
    if left is None or right is None:
        return "UNAVAILABLE"
    tolerance = max(absolute, relative * max(abs(float(left)), abs(float(right))))
    difference = float(left) - float(right)
    if abs(difference) <= tolerance:
        return "TIE"
    return "LEFT_BETTER" if difference > 0 else "RIGHT_BETTER"


def compare(results: list[dict[str, Any]], thresholds: dict[str, Any]) -> dict[str, Any]:
    contract_groups: dict[str, list[str]] = {}
    for result in results:
        contract_groups.setdefault(result["contract_sha256"], []).append(
            result["candidate"]["id"])
    if len(contract_groups) != 1:
        pairwise = []
        for left_index in range(len(results)):
            for right_index in range(left_index + 1, len(results)):
                left, right = results[left_index], results[right_index]
                pairwise.append({
                    "left": left["candidate"]["id"],
                    "right": right["candidate"]["id"],
                    "left_contract_sha256": left["contract_sha256"],
                    "right_contract_sha256": right["contract_sha256"],
                    "hard_gate": "NOT_RANKED",
                    "verdict": "INCOMPARABLE_CONTRACT" if (
                        left["contract_sha256"] != right["contract_sha256"]
                    ) else "NOT_EVALUATED_GLOBAL_CONTRACT_MISMATCH",
                    "per_run": [],
                })
        return {
            "decision": "INCOMPARABLE",
            "reason": "exact policy/edge/latency contract fingerprints differ",
            "rule": "exact same-boundary contract required before any Pareto comparison",
            "aggregate_score": None,
            "pareto_frontier": None,
            "contract_groups": [
                {"contract_sha256": digest, "candidates": sorted(candidate_ids)}
                for digest, candidate_ids in sorted(contract_groups.items())
            ],
            "frontier_scope": None,
            "pairwise": pairwise,
        }
    comparisons: list[dict[str, Any]] = []
    dominated: set[str] = set()
    comparison = thresholds["comparison"]
    result_runs = [{run["name"]: run for run in result["runs"]} for result in results]
    common_names = sorted(set.intersection(*(set(runs) for runs in result_runs)))
    accepted_cohort = {
        name: set.intersection(*(
            set(runs[name]["_accepted_event_ids"]) for runs in result_runs))
        for name in common_names
    }
    retired_cohort = {
        name: accepted_cohort[name] & set.intersection(*(
            set(runs[name]["_accept_to_retire_by_id"]) for runs in result_runs))
        for name in common_names
    }
    for left_index in range(len(results)):
        for right_index in range(left_index + 1, len(results)):
            left = results[left_index]
            right = results[right_index]
            left_runs = {run["name"]: run for run in left["runs"]}
            right_runs = {run["name"]: run for run in right["runs"]}
            per_run = []
            for name in common_names:
                left_run, right_run = left_runs[name], right_runs[name]
                left_ids = set(left_run["_accepted_event_ids"])
                right_ids = set(right_run["_accepted_event_ids"])
                matched = sorted(accepted_cohort[name])
                left_occurrence = [left_run["_occurrence_to_accept_by_id"][item]
                                   for item in matched]
                right_occurrence = [right_run["_occurrence_to_accept_by_id"][item]
                                    for item in matched]
                retire_matched = sorted(retired_cohort[name])
                left_retire = [left_run["_accept_to_retire_by_id"][item]
                               for item in retire_matched]
                right_retire = [right_run["_accept_to_retire_by_id"][item]
                                for item in retire_matched]
                epc_band = comparison["fixed_window_event_per_cycle_equivalence_band"]
                occurrence_band = comparison["p99_occurrence_to_accept_equivalence_band"]
                retire_band = comparison["p99_accept_to_retire_equivalence_band"]
                overrun_band = comparison["overrun_ratio_equivalence_band"]
                left_occ_summary = latency_summary(left_occurrence)
                right_occ_summary = latency_summary(right_occurrence)
                left_ret_summary = latency_summary(left_retire)
                right_ret_summary = latency_summary(right_retire)
                per_run.append({
                    "name": name,
                    "event_per_cycle": band_delta(
                        left_run["fixed_window"]["event_per_cycle"],
                        right_run["fixed_window"]["event_per_cycle"],
                        epc_band["absolute"], epc_band["relative"]),
                    "source_overrun_ratio": band_delta(
                        # Lower overrun is better, hence reverse operands.
                        right_run["accounting"]["source_overrun_ratio"],
                        left_run["accounting"]["source_overrun_ratio"],
                        overrun_band["absolute"]),
                    "accepted_set": {
                        "all_candidate_matched": len(matched),
                        "matched": len(matched), "left_only": len(left_ids - right_ids),
                        "right_only": len(right_ids - left_ids),
                        "symmetric_difference": len(left_ids ^ right_ids),
                    },
                    "matched_occurrence_to_accept": {
                        "left": left_occ_summary, "right": right_occ_summary,
                        "p99_band": band_delta(
                            # Lower latency is better, hence reverse operands.
                            right_occ_summary["p99"], left_occ_summary["p99"],
                            occurrence_band["absolute_cycles"], occurrence_band["relative"]),
                    },
                    "matched_accept_to_retire": {
                        "left": left_ret_summary, "right": right_ret_summary,
                        "p99_band": band_delta(
                            right_ret_summary["p99"], left_ret_summary["p99"],
                            retire_band["absolute_cycles"], retire_band["relative"]),
                    },
                    "latency_comparison_scope": "event-ID cohort common to all three candidates",
                })
            directions = [direction for run in per_run for direction in (
                run["event_per_cycle"], run["source_overrun_ratio"],
                run["matched_occurrence_to_accept"]["p99_band"],
                run["matched_accept_to_retire"]["p99_band"])
                if direction != "UNAVAILABLE"]
            if left["status"] == "PASS" and right["status"] != "PASS":
                eligibility, verdict = "LEFT_ONLY_ELIGIBLE", "LEFT_ADVANCES"
            elif right["status"] == "PASS" and left["status"] != "PASS":
                eligibility, verdict = "RIGHT_ONLY_ELIGIBLE", "RIGHT_ADVANCES"
            elif left["status"] != "PASS" or right["status"] != "PASS":
                eligibility, verdict = "BOTH_INELIGIBLE", "NO_COMPARISON"
            elif "RIGHT_BETTER" not in directions and "LEFT_BETTER" in directions:
                eligibility, verdict = "BOTH_ELIGIBLE", "LEFT_DOMINATES"
                dominated.add(right["candidate"]["id"])
            elif "LEFT_BETTER" not in directions and "RIGHT_BETTER" in directions:
                eligibility, verdict = "BOTH_ELIGIBLE", "RIGHT_DOMINATES"
                dominated.add(left["candidate"]["id"])
            elif directions and all(direction == "TIE" for direction in directions):
                eligibility, verdict = "BOTH_ELIGIBLE", "TIE_WITHIN_BANDS"
            else:
                eligibility, verdict = "BOTH_ELIGIBLE", "PARETO_TRADEOFF"
            comparisons.append({
                "left": left["candidate"]["id"], "right": right["candidate"]["id"],
                "hard_gate": eligibility, "verdict": verdict,
                "per_run": per_run,
            })
    passing = {result["candidate"]["id"] for result in results if result["status"] == "PASS"}
    return {
        "decision": "COMPARABLE",
        "rule": comparison["ranking"],
        "aggregate_score": None,
        "pareto_frontier": sorted(passing - dominated),
        "frontier_scope": "hard-gate-eligible candidates; all-three matched cohorts and frozen bands",
        "contract_groups": [{
            "contract_sha256": next(iter(contract_groups)),
            "candidates": sorted(next(iter(contract_groups.values()))),
        }],
        "pairwise": comparisons,
    }


def evaluate_documents(vectors: dict[str, Any], evidences: list[tuple[Path, dict[str, Any]]],
                       thresholds: dict[str, Any]) -> dict[str, Any]:
    if len(evidences) != 3:
        raise ContractError("the common comparison requires exactly three candidate evidence files")
    vector_by_name = {run["name"]: run for run in vectors["runs"]}
    results = []
    ids: set[str] = set()
    identities: set[str] = set()
    for path, document in evidences:
        try:
            evidence_bytes = stable_regular_bytes(path, "candidate evidence")
            on_disk_document = json.loads(evidence_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContractError(f"candidate evidence is invalid JSON {path}: {error}") from error
        if on_disk_document != document:
            raise ContractError(f"candidate evidence document is not the bytes at {path}")
        checked = validate_evidence(document, vectors, path)
        candidate = checked["candidate"]
        if candidate["id"] in ids:
            raise ContractError(f"duplicate candidate id {candidate['id']}")
        ids.add(candidate["id"])
        component_identity_sha256 = object_sha256({
            name: candidate[name] for name in IDENTITY_COMPONENTS})
        if component_identity_sha256 in identities:
            raise ContractError(f"duplicate bound candidate identity {candidate['id']}")
        identities.add(component_identity_sha256)
        identity_sha256 = candidate_identity_sha256(candidate)
        run_results = []
        for name in vector_by_name:
            if name not in checked["runs"]:
                continue
            run_result = evaluate_run(
                vector_by_name[name], checked["runs"][name], thresholds)
            run_result["artifact"] = checked["runs"][name]["_artifact_record"]
            run_results.append(run_result)
        result = aggregate(candidate, run_results)
        result["candidate_identity_sha256"] = identity_sha256
        result["contract_sha256"] = checked["contract_sha256"]
        result["evidence_path"] = str(path)
        result["evidence_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
        results.append(result)
    comparison_result = compare(results, thresholds)
    for result in results:
        for run in result["runs"]:
            for key in [key for key in run if key.startswith("_")]:
                del run[key]
    all_pass = all(result["status"] == "PASS" for result in results)
    status = "HOLD"
    if all_pass:
        status = comparison_result["decision"]
        if status == "COMPARABLE":
            status = "PASS"
    return {
        "schema": RESULT_SCHEMA,
        "scope": "INDEPENDENT_COMMON_DIGITAL_N16_K2_TRANSACTION_EVALUATION",
        "vector_bundle_sha256": vectors["bundle_sha256"],
        "thresholds_sha256": object_sha256(thresholds),
        "future_trace_equivalence": "EXPLICITLY_NOT_CLAIMED",
        "candidates": results,
        "comparison": comparison_result,
        "status": status,
        "non_qualification": [
            "physical PPA", "official competition release", "future-arrival trace equivalence",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", required=True, type=Path)
    parser.add_argument("--thresholds", type=Path,
                        default=Path(__file__).with_name("thresholds.json"))
    parser.add_argument("--candidate", action="append", type=Path, required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"error: refusing to overwrite {args.output}", file=sys.stderr)
        return 2
    try:
        vectors = validate_vector_bundle(load_json(args.vectors))
        thresholds = load_json(args.thresholds)
        evidences = [(path, load_json(path)) for path in args.candidate]
        result = evaluate_documents(vectors, evidences, thresholds)
    except ContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"A5_K2_EVALUATION_{result['status']} candidates={len(result['candidates'])}")
    if result["status"] == "PASS":
        return 0
    if result["status"] == "INCOMPARABLE":
        return 4
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
