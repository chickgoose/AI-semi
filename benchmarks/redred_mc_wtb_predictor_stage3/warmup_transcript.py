"""Streaming, score-free warmup transcripts for query-only Stage-3 outputs.

Only chain endpoints, scalar causal state, the current cluster/cycle counters,
and at most 256 reference observations per polarity are retained.  Rich warmup
decisions are consumed exactly once and are never stored in a receipt.

Event identity and row provenance come from the dependency-bound deterministic
native replay, not from these unkeyed transcript chains.  Its execution
authority rejects duplicate source event IDs and supplies the canonical ordered
warmup-ID digest; this builder recomputes that digest incrementally so arbitrary
(including numerically decreasing) IDs remain in exact source order without an
unbounded in-memory ``seen`` set.
"""

from __future__ import annotations

from collections import deque
import hashlib
import math
import re
from typing import Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


WARMUP_TRANSCRIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.warmup_transcript/v2"
REFERENCE_PRIME_POLICY_SCHEMA = "redred.mc_wtb_predictor_stage3.reference_prime_policy/v1"
TRANSPORT_POLICY_SCHEMA = "redred.mc_wtb_predictor_stage3.transport_diagnostic_policy/v1"
RESET_SCHEMA = "redred.mc_wtb_predictor_stage3.warmup_reset/v1"
QUERY_START_STATE_SCHEMA = "redred.mc_wtb_predictor_stage3.query_start_state/v1"
REFERENCE_SNAPSHOT_SCHEMA = "redred.mc_wtb_predictor_stage3.query_start_reference_snapshot/v1"
PENDING_TRANSITION_SCHEMA = "redred.mc_wtb_predictor_stage3.pending_query_transition/v1"
REPLAY_AUTHORITY_SCHEMA = "redred.mc_wtb_predictor_stage3.native_replay_authority/v1"
BOUNDARY_CLOSE_SCHEMA = "redred.mc_wtb_predictor_stage3.warmup_boundary_close/v1"

_REPLAY_AUTHORITY_SEMANTICS = {
    "schema": REPLAY_AUTHORITY_SCHEMA,
    "stream_origin": "dependency_bound_deterministic_native_replay",
    "occurrence_order_rule": "execution_authority_window_source_order",
    "event_identity_rule": "execution_authority_unique_source_event_id",
    "transition_order_rule": "native_pose_commit_order_events_before_same_edge_pose",
    "integrity_scope": "transcript_chains_are_not_independent_authentication",
}
NATIVE_REPLAY_AUTHORITY_SHA256 = canonical_sha256(_REPLAY_AUTHORITY_SEMANTICS)

_CHAIN_SEED_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_chain_seed/v1"
_CHAIN_LINK_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_chain_link/v1"
_LEAF_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_leaf/v1"
_OCCURRENCE_DOMAIN = "stage3/warmup/occurrence/v1"
_CLUSTER_DOMAIN = "stage3/warmup/same_edge_cluster/v1"
_CLUSTER_MEMBER_DOMAIN = "stage3/warmup/same_edge_cluster_member/v1"
_CLUSTER_POSE_DOMAIN = "stage3/warmup/same_edge_pose/v1"
_TRANSITION_DOMAIN = "stage3/warmup/state_transition/v1"
_DEPENDENCY_DOMAIN = "stage3/warmup/state_dependency_pose/v1"
_TRANSPORT_DOMAIN = "stage3/warmup/transport_cycle_violation/v1"
_TRANSPORT_MEMBER_DOMAIN = "stage3/warmup/transport_cycle_member/v1"
_SNAPSHOT_DOMAINS = (
    "stage3/warmup/reference_snapshot/polarity_0/v1",
    "stage3/warmup/reference_snapshot/polarity_1/v1",
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.:/,+-]{0,510}[A-Za-z0-9])?\Z")
_WINDOW_IDENTIFIER = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/,+=-]{0,510}[A-Za-z0-9])?\Z"
)

_BINDING_FIELDS = frozenset((
    "candidate_id", "candidate_config_sha256",
    "candidate_dependency_aggregate_sha256", "logical_ingress_profile_sha256",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "window_input_sha256", "reference_prime_implementation_sha256",
    "native_replay_authority_sha256", "ordered_warmup_event_ids_sha256",
))
_BOUNDS_FIELDS = frozenset((
    "window_id", "warmup_start_ns_inclusive", "query_start_ns_inclusive",
    "query_end_ns_exclusive", "query_start_decision_cycle",
))
_REFERENCE_POLICY_FIELDS = frozenset((
    "schema", "capacity_per_polarity", "max_age_ns",
    "expiration_rule", "selection_rule", "equal_timestamp_rule",
    "snapshot_boundary_rule", "warmup_mode",
))
_TRANSPORT_POLICY_FIELDS = frozenset((
    "schema", "capacity_per_cycle", "violation_rule", "service_rule",
))
_STATE_BODY_FIELDS = frozenset((
    "schema", "predictor_state_version", "predictor_state_sha256",
    "dependency_pose_count", "dependency_pose_chain_sha256",
    "last_dependency_pose_id",
))
_RESET_FIELDS = _STATE_BODY_FIELDS | frozenset((
    "reset_generation", "reset_cycle", "reset_sha256",
))
_QUERY_STATE_FIELDS = _STATE_BODY_FIELDS | frozenset((
    "query_start_state_receipt_sha256",
))
_OCCURRENCE_FIELDS = frozenset((
    "occurrence_ordinal", "event_id", "event_content_sha256", "timestamp_ns",
    "polarity", "occurrence_cycle", "decision_cycle", "service_cycle",
    "predictor_state_version", "predictor_state_sha256",
    "state_dependency_pose_count", "state_dependency_pose_chain_sha256",
    "state_last_dependency_pose_id", "candidate_attempted", "candidate_used",
    "route", "decision_sha256", "world_ray",
))
_TRANSITION_FIELDS = frozenset((
    "transition_ordinal", "pose_id", "pose_content_sha256",
    "measurement_timestamp_ns", "commit_cycle", "publication_cycle",
    "effective_cycle", "state_changed", "prior_state_version",
    "prior_state_sha256", "prior_dependency_pose_count",
    "prior_dependency_pose_chain_sha256", "prior_last_dependency_pose_id",
    "next_state_version", "next_state_sha256", "next_dependency_pose_count",
    "next_dependency_pose_chain_sha256", "next_last_dependency_pose_id",
    "native_transition_sha256",
))
_SNAPSHOT_OBSERVATION_FIELDS = frozenset((
    "occurrence_ordinal", "event_id", "timestamp_ns", "polarity", "world_ray",
))
_SNAPSHOT_FIELDS = frozenset((
    "schema", "binding_context_sha256", "reference_prime_policy_sha256",
    "reference_prime_implementation_sha256", "query_start_ns_inclusive",
    "last_warmup_timestamp_ns", "merge_order", "polarity_0",
    "polarity_0_sha256", "polarity_1", "polarity_1_sha256",
    "observation_count", "occupancy", "snapshot_sha256",
))
_ENDPOINT_FIELDS = frozenset(("occurrence_ordinal", "event_id", "timestamp_ns"))
_BOUNDARY_CLOSE_FIELDS = frozenset((
    "schema", "window_id", "query_start_ns_inclusive",
    "last_warmup_occurrence_ordinal", "last_warmup_event_id",
    "last_warmup_timestamp_ns", "warmup_transition_count",
    "last_warmup_transition_measurement_timestamp_ns",
    "last_warmup_cluster_timestamp_ns", "first_query_source_ordinal",
    "first_query_event_id", "first_query_timestamp_ns",
    "execution_input_aggregate_sha256", "ordered_warmup_event_ids_sha256",
    "native_replay_authority_sha256", "boundary_close_sha256",
))
_PENDING_TRANSITION_FIELDS = frozenset((
    "schema", "transition_ordinal", "pose_id", "pose_content_sha256",
    "commit_cycle", "publication_cycle", "effective_cycle",
    "next_state_version", "next_state_sha256", "next_dependency_pose_count",
    "next_dependency_pose_chain_sha256", "next_last_dependency_pose_id",
    "native_transition_sha256", "pending_transition_sha256",
))
_REPLAY_AUTHORITY_FIELDS = frozenset((
    "schema", "authority_sha256", "stream_origin", "occurrence_order_rule",
    "event_identity_rule", "transition_order_rule", "integrity_scope",
    "candidate_dependency_aggregate_sha256", "execution_input_aggregate_sha256",
    "ordered_warmup_event_ids_sha256", "authority_receipt_sha256",
))
_RECEIPT_FIELDS = frozenset((
    "schema", "bindings", "bindings_sha256", "bounds", "bounds_sha256",
    "reference_prime_policy", "reference_prime_policy_sha256",
    "transport_policy", "transport_policy_sha256", "reset",
    "query_start_state", "binding_context_sha256", "warmup_occurrence_count",
    "same_edge_cluster_count", "state_transition_count",
    "transport_cycle_violation_count", "warmup_occurrence_chain_sha256",
    "same_edge_cluster_chain_sha256", "state_transition_chain_sha256",
    "transport_cycle_violation_chain_sha256", "first_warmup_occurrence",
    "last_warmup_occurrence", "boundary_close_authority",
    "pending_query_transition",
    "native_replay_authority", "query_start_reference_snapshot",
    "receipt_sha256",
))


class WarmupTranscriptError(ValueError):
    """A warmup boundary, stream, state, or canonical seal is invalid."""


def _mapping(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise WarmupTranscriptError("%s field schema differs" % where)
    return value


def _integer(value: object, where: str, signed: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarmupTranscriptError("%s must be an integer" % where)
    if not signed and value < 0:
        raise WarmupTranscriptError("%s must be nonnegative" % where)
    return value


def _boolean(value: object, where: str) -> bool:
    if type(value) is not bool:
        raise WarmupTranscriptError("%s must be an exact bool" % where)
    return value


def _identifier(value: object, where: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise WarmupTranscriptError("%s must be a canonical identifier" % where)
    return value


def _window_identifier(value: object, where: str) -> str:
    if type(value) is not str or _WINDOW_IDENTIFIER.fullmatch(value) is None:
        raise WarmupTranscriptError("%s must be a canonical window identifier" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise WarmupTranscriptError("%s must be lowercase SHA-256" % where)
    return value


def _optional_id(value: object, count: int, where: str) -> Optional[int]:
    if count == 0:
        if value is not None:
            raise WarmupTranscriptError("%s must be null for an empty dependency chain" % where)
        return None
    return _integer(value, where)


def _unit_ray(value: object, where: str) -> List[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise WarmupTranscriptError("%s must contain three components" % where)
    result = []  # type: List[float]
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise WarmupTranscriptError("%s must contain finite numbers" % where)
        item = float(raw)
        if not math.isfinite(item):
            raise WarmupTranscriptError("%s must contain finite numbers" % where)
        result.append(item)
    norm = math.sqrt(math.fsum(item * item for item in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise WarmupTranscriptError("%s must be a unit ray" % where)
    return result


def _sealed_mapping(value: object, fields: frozenset, seal_field: str, where: str) -> Dict[str, object]:
    row = _mapping(value, fields, where)
    supplied = _sha256(row.get(seal_field), where + " seal")
    body = {key: row[key] for key in row if key != seal_field}
    if supplied != canonical_sha256(body):
        raise WarmupTranscriptError("%s seal differs" % where)
    return dict(row)


def _leaf_sha256(domain: str, leaf: Mapping[str, object]) -> str:
    return canonical_sha256({"domain": domain, "leaf": dict(leaf), "schema": _LEAF_SCHEMA})


def _chain_seed(domain: str, context_sha256: str) -> str:
    return canonical_sha256({
        "binding_context_sha256": context_sha256,
        "domain": domain,
        "schema": _CHAIN_SEED_SCHEMA,
    })


def _chain_link(domain: str, prior: str, ordinal: int, leaf_sha256: str) -> str:
    return canonical_sha256({
        "domain": domain, "leaf_sha256": leaf_sha256, "ordinal": ordinal,
        "prior_sha256": prior, "schema": _CHAIN_LINK_SCHEMA,
    })


def _dependency_link(prior: str, count: int, pose_id: int, pose_sha256: str) -> str:
    leaf = {"pose_content_sha256": pose_sha256, "pose_id": pose_id}
    return _chain_link(_DEPENDENCY_DOMAIN, prior, count, _leaf_sha256(_DEPENDENCY_DOMAIN, leaf))


def _normalize_bindings(value: object) -> Dict[str, object]:
    row = _mapping(value, _BINDING_FIELDS, "transcript bindings")
    result = {"candidate_id": _identifier(row["candidate_id"], "candidate ID")}
    for field in _BINDING_FIELDS - frozenset(("candidate_id",)):
        result[field] = _sha256(row[field], field)
    if result["native_replay_authority_sha256"] != NATIVE_REPLAY_AUTHORITY_SHA256:
        raise WarmupTranscriptError("native replay authority differs")
    return result


def _normalize_bounds(value: object) -> Dict[str, object]:
    row = _mapping(value, _BOUNDS_FIELDS, "warmup bounds")
    result = {
        "window_id": _window_identifier(row["window_id"], "window ID"),
        "warmup_start_ns_inclusive": _integer(row["warmup_start_ns_inclusive"], "warmup start"),
        "query_start_ns_inclusive": _integer(row["query_start_ns_inclusive"], "query start"),
        "query_end_ns_exclusive": _integer(row["query_end_ns_exclusive"], "query end"),
        "query_start_decision_cycle": _integer(row["query_start_decision_cycle"], "query-start cycle"),
    }
    if not result["warmup_start_ns_inclusive"] < result["query_start_ns_inclusive"] < result["query_end_ns_exclusive"]:
        raise WarmupTranscriptError("warmup/query bounds are not strictly increasing")
    return result


def _normalize_reference_policy(value: object) -> Dict[str, object]:
    row = _mapping(value, _REFERENCE_POLICY_FIELDS, "reference-prime policy")
    capacity = _integer(row["capacity_per_polarity"], "reference capacity")
    max_age = _integer(row["max_age_ns"], "reference max age")
    if row["schema"] != REFERENCE_PRIME_POLICY_SCHEMA:
        raise WarmupTranscriptError("reference-prime policy schema differs")
    if capacity != 256 or max_age != 2_000_000:
        raise WarmupTranscriptError("reference-prime fixed bounds differ")
    if (
        row["expiration_rule"] != "drop_timestamp_lt_cluster_timestamp_minus_max_age_ns"
        or row["selection_rule"] != "minimum_angular_distance_then_timestamp_then_event_id"
        or row["equal_timestamp_rule"] != "complete_cluster_before_insert"
        or row["snapshot_boundary_rule"] != "after_last_warmup_cluster_without_query_start_expiry"
        or row["warmup_mode"] != "prime_without_metrics"
    ):
        raise WarmupTranscriptError("reference-prime semantics differ")
    return {
        "schema": REFERENCE_PRIME_POLICY_SCHEMA,
        "capacity_per_polarity": 256,
        "max_age_ns": 2_000_000,
        "expiration_rule": "drop_timestamp_lt_cluster_timestamp_minus_max_age_ns",
        "selection_rule": "minimum_angular_distance_then_timestamp_then_event_id",
        "equal_timestamp_rule": "complete_cluster_before_insert",
        "snapshot_boundary_rule": "after_last_warmup_cluster_without_query_start_expiry",
        "warmup_mode": "prime_without_metrics",
    }


def _normalize_transport_policy(value: object) -> Dict[str, object]:
    row = _mapping(value, _TRANSPORT_POLICY_FIELDS, "transport policy")
    capacity = _integer(row["capacity_per_cycle"], "transport capacity")
    if row["schema"] != TRANSPORT_POLICY_SCHEMA or capacity < 1:
        raise WarmupTranscriptError("transport policy differs")
    if row["violation_rule"] != "observed_occurrences_gt_capacity" or row["service_rule"] != "record_without_deferral":
        raise WarmupTranscriptError("transport policy semantics differ")
    return {
        "schema": TRANSPORT_POLICY_SCHEMA,
        "capacity_per_cycle": capacity,
        "violation_rule": "observed_occurrences_gt_capacity",
        "service_rule": "record_without_deferral",
    }


def _normalize_state_body(row: Mapping[str, object], where: str) -> Dict[str, object]:
    count = _integer(row["dependency_pose_count"], where + " dependency count")
    return {
        "schema": row["schema"],
        "predictor_state_version": _integer(row["predictor_state_version"], where + " version"),
        "predictor_state_sha256": _sha256(row["predictor_state_sha256"], where + " digest"),
        "dependency_pose_count": count,
        "dependency_pose_chain_sha256": _sha256(row["dependency_pose_chain_sha256"], where + " dependency chain"),
        "last_dependency_pose_id": _optional_id(row["last_dependency_pose_id"], count, where + " last dependency"),
    }


def _normalize_reset(value: object) -> Dict[str, object]:
    row = _sealed_mapping(value, _RESET_FIELDS, "reset_sha256", "warmup reset")
    if row["schema"] != RESET_SCHEMA:
        raise WarmupTranscriptError("warmup reset schema differs")
    result = _normalize_state_body(row, "reset state")
    result.update({
        "reset_generation": _integer(row["reset_generation"], "reset generation"),
        "reset_cycle": _integer(row["reset_cycle"], "reset cycle"),
        "reset_sha256": row["reset_sha256"],
    })
    if result["reset_cycle"] != 0:
        raise WarmupTranscriptError("warmup reset cycle must be exactly zero")
    return result


def _normalize_query_state(value: object) -> Dict[str, object]:
    row = _sealed_mapping(value, _QUERY_STATE_FIELDS, "query_start_state_receipt_sha256", "query-start state")
    if row["schema"] != QUERY_START_STATE_SCHEMA:
        raise WarmupTranscriptError("query-start state schema differs")
    result = _normalize_state_body(row, "query-start state")
    result["query_start_state_receipt_sha256"] = row["query_start_state_receipt_sha256"]
    return result


def _normalize_occurrence(value: object, ordinal: int) -> Dict[str, object]:
    row = _mapping(value, _OCCURRENCE_FIELDS, "warmup occurrence")
    dependency_count = _integer(row["state_dependency_pose_count"], "occurrence dependency count")
    result = {
        "occurrence_ordinal": _integer(row["occurrence_ordinal"], "occurrence ordinal"),
        "event_id": _integer(row["event_id"], "event ID"),
        "event_content_sha256": _sha256(row["event_content_sha256"], "event content"),
        "timestamp_ns": _integer(row["timestamp_ns"], "event timestamp"),
        "polarity": _integer(row["polarity"], "event polarity"),
        "occurrence_cycle": _integer(row["occurrence_cycle"], "occurrence cycle", signed=True),
        "decision_cycle": _integer(row["decision_cycle"], "decision cycle"),
        "service_cycle": _integer(row["service_cycle"], "service cycle"),
        "predictor_state_version": _integer(row["predictor_state_version"], "occurrence state version"),
        "predictor_state_sha256": _sha256(row["predictor_state_sha256"], "occurrence state"),
        "state_dependency_pose_count": dependency_count,
        "state_dependency_pose_chain_sha256": _sha256(row["state_dependency_pose_chain_sha256"], "occurrence dependency chain"),
        "state_last_dependency_pose_id": _optional_id(row["state_last_dependency_pose_id"], dependency_count, "occurrence last dependency"),
        "candidate_attempted": _boolean(row["candidate_attempted"], "candidate-attempted flag"),
        "candidate_used": _boolean(row["candidate_used"], "candidate-used flag"),
        "route": _identifier(row["route"], "warmup route"),
        "decision_sha256": _sha256(row["decision_sha256"], "warmup decision"),
        "world_ray": _unit_ray(row["world_ray"], "warmup world ray"),
    }
    if result["occurrence_ordinal"] != ordinal:
        raise WarmupTranscriptError("warmup occurrence ordinal is not exact")
    if result["polarity"] not in (0, 1):
        raise WarmupTranscriptError("event polarity must be integer zero or one")
    if result["occurrence_cycle"] != result["decision_cycle"] - 1:
        raise WarmupTranscriptError("occurrence edge must equal decision edge minus one")
    if result["service_cycle"] != result["decision_cycle"]:
        raise WarmupTranscriptError("warmup decision was deferred")
    if result["candidate_used"] and not result["candidate_attempted"]:
        raise WarmupTranscriptError("candidate use lacks an attempt")
    return result


def _normalize_transition(value: object, ordinal: int) -> Dict[str, object]:
    row = _mapping(value, _TRANSITION_FIELDS, "state transition")
    prior_count = _integer(row["prior_dependency_pose_count"], "prior dependency count")
    next_count = _integer(row["next_dependency_pose_count"], "next dependency count")
    result = {
        "transition_ordinal": _integer(row["transition_ordinal"], "transition ordinal"),
        "pose_id": _integer(row["pose_id"], "transition pose ID"),
        "pose_content_sha256": _sha256(row["pose_content_sha256"], "transition pose content"),
        "measurement_timestamp_ns": _integer(row["measurement_timestamp_ns"], "pose measurement timestamp"),
        "commit_cycle": _integer(row["commit_cycle"], "pose commit cycle"),
        "publication_cycle": _integer(row["publication_cycle"], "state publication cycle"),
        "effective_cycle": _integer(row["effective_cycle"], "state effective cycle"),
        "state_changed": _boolean(row["state_changed"], "state-changed flag"),
        "prior_state_version": _integer(row["prior_state_version"], "prior state version"),
        "prior_state_sha256": _sha256(row["prior_state_sha256"], "prior state"),
        "prior_dependency_pose_count": prior_count,
        "prior_dependency_pose_chain_sha256": _sha256(row["prior_dependency_pose_chain_sha256"], "prior dependency chain"),
        "prior_last_dependency_pose_id": _optional_id(row["prior_last_dependency_pose_id"], prior_count, "prior last dependency"),
        "next_state_version": _integer(row["next_state_version"], "next state version"),
        "next_state_sha256": _sha256(row["next_state_sha256"], "next state"),
        "next_dependency_pose_count": next_count,
        "next_dependency_pose_chain_sha256": _sha256(row["next_dependency_pose_chain_sha256"], "next dependency chain"),
        "next_last_dependency_pose_id": _optional_id(row["next_last_dependency_pose_id"], next_count, "next last dependency"),
        "native_transition_sha256": _sha256(row["native_transition_sha256"], "native transition"),
    }
    if result["transition_ordinal"] != ordinal:
        raise WarmupTranscriptError("state transition ordinal is not exact")
    return result


def _pending_transition_receipt(transition: Mapping[str, object]) -> Dict[str, object]:
    body = {
        "schema": PENDING_TRANSITION_SCHEMA,
        "transition_ordinal": transition["transition_ordinal"],
        "pose_id": transition["pose_id"],
        "pose_content_sha256": transition["pose_content_sha256"],
        "commit_cycle": transition["commit_cycle"],
        "publication_cycle": transition["publication_cycle"],
        "effective_cycle": transition["effective_cycle"],
        "next_state_version": transition["next_state_version"],
        "next_state_sha256": transition["next_state_sha256"],
        "next_dependency_pose_count": transition["next_dependency_pose_count"],
        "next_dependency_pose_chain_sha256": transition["next_dependency_pose_chain_sha256"],
        "next_last_dependency_pose_id": transition["next_last_dependency_pose_id"],
        "native_transition_sha256": transition["native_transition_sha256"],
    }
    return dict(body, pending_transition_sha256=canonical_sha256(body))


def _normalize_pending_transition(value: object) -> Optional[Dict[str, object]]:
    if value is None:
        return None
    row = _sealed_mapping(
        value, _PENDING_TRANSITION_FIELDS, "pending_transition_sha256",
        "pending query transition",
    )
    if row["schema"] != PENDING_TRANSITION_SCHEMA:
        raise WarmupTranscriptError("pending query transition schema differs")
    next_count = _integer(
        row["next_dependency_pose_count"], "pending transition dependency count"
    )
    result = {
        "schema": PENDING_TRANSITION_SCHEMA,
        "transition_ordinal": _integer(row["transition_ordinal"], "pending transition ordinal"),
        "pose_id": _integer(row["pose_id"], "pending transition pose ID"),
        "pose_content_sha256": _sha256(row["pose_content_sha256"], "pending transition pose content"),
        "commit_cycle": _integer(row["commit_cycle"], "pending transition commit cycle"),
        "publication_cycle": _integer(row["publication_cycle"], "pending transition publication cycle"),
        "effective_cycle": _integer(row["effective_cycle"], "pending transition effective cycle"),
        "next_state_version": _integer(row["next_state_version"], "pending transition state version"),
        "next_state_sha256": _sha256(row["next_state_sha256"], "pending transition state"),
        "next_dependency_pose_count": next_count,
        "next_dependency_pose_chain_sha256": _sha256(
            row["next_dependency_pose_chain_sha256"], "pending transition dependency chain"
        ),
        "next_last_dependency_pose_id": _optional_id(
            row["next_last_dependency_pose_id"], next_count,
            "pending transition last dependency",
        ),
        "native_transition_sha256": _sha256(
            row["native_transition_sha256"], "pending native transition"
        ),
        "pending_transition_sha256": row["pending_transition_sha256"],
    }
    if result["publication_cycle"] > result["effective_cycle"]:
        raise WarmupTranscriptError("pending transition publication/effective order differs")
    return result


def _native_replay_authority(bindings: Mapping[str, object]) -> Dict[str, object]:
    body = dict(_REPLAY_AUTHORITY_SEMANTICS)
    body.update({
        "authority_sha256": NATIVE_REPLAY_AUTHORITY_SHA256,
        "candidate_dependency_aggregate_sha256": bindings[
            "candidate_dependency_aggregate_sha256"
        ],
        "execution_input_aggregate_sha256": bindings[
            "execution_input_aggregate_sha256"
        ],
        "ordered_warmup_event_ids_sha256": bindings[
            "ordered_warmup_event_ids_sha256"
        ],
    })
    return dict(body, authority_receipt_sha256=canonical_sha256(body))


def _normalize_native_replay_authority(
    value: object, bindings: Mapping[str, object]
) -> Dict[str, object]:
    row = _sealed_mapping(
        value, _REPLAY_AUTHORITY_FIELDS, "authority_receipt_sha256",
        "native replay authority",
    )
    expected = _native_replay_authority(bindings)
    if row != expected:
        raise WarmupTranscriptError("native replay authority binding differs")
    return dict(row)


def _normalize_boundary_close(
    value: object, bindings: Mapping[str, object], bounds: Mapping[str, object],
    last_endpoint: Mapping[str, object], occurrence_count: int,
    transition_count: int, last_transition_timestamp: Optional[int],
) -> Dict[str, object]:
    row = _sealed_mapping(
        value, _BOUNDARY_CLOSE_FIELDS, "boundary_close_sha256",
        "warmup boundary-close authority",
    )
    if row["schema"] != BOUNDARY_CLOSE_SCHEMA:
        raise WarmupTranscriptError("warmup boundary-close schema differs")
    result = {
        "schema": BOUNDARY_CLOSE_SCHEMA,
        "window_id": _window_identifier(row["window_id"], "boundary window ID"),
        "query_start_ns_inclusive": _integer(
            row["query_start_ns_inclusive"], "boundary query start"
        ),
        "last_warmup_occurrence_ordinal": _integer(
            row["last_warmup_occurrence_ordinal"], "boundary last warmup ordinal"
        ),
        "last_warmup_event_id": _integer(
            row["last_warmup_event_id"], "boundary last warmup event ID"
        ),
        "last_warmup_timestamp_ns": _integer(
            row["last_warmup_timestamp_ns"], "boundary last warmup timestamp"
        ),
        "warmup_transition_count": _integer(
            row["warmup_transition_count"], "boundary warmup transition count"
        ),
        "last_warmup_transition_measurement_timestamp_ns": (
            None if row["last_warmup_transition_measurement_timestamp_ns"] is None
            else _integer(
                row["last_warmup_transition_measurement_timestamp_ns"],
                "boundary last transition measurement timestamp",
            )
        ),
        "last_warmup_cluster_timestamp_ns": _integer(
            row["last_warmup_cluster_timestamp_ns"], "boundary last cluster timestamp"
        ),
        "first_query_source_ordinal": _integer(
            row["first_query_source_ordinal"], "boundary first query source ordinal"
        ),
        "first_query_event_id": _integer(
            row["first_query_event_id"], "boundary first query event ID"
        ),
        "first_query_timestamp_ns": _integer(
            row["first_query_timestamp_ns"], "boundary first query timestamp"
        ),
        "execution_input_aggregate_sha256": _sha256(
            row["execution_input_aggregate_sha256"], "boundary execution input"
        ),
        "ordered_warmup_event_ids_sha256": _sha256(
            row["ordered_warmup_event_ids_sha256"], "boundary warmup event IDs"
        ),
        "native_replay_authority_sha256": _sha256(
            row["native_replay_authority_sha256"], "boundary replay authority"
        ),
        "boundary_close_sha256": row["boundary_close_sha256"],
    }
    if (
        result["window_id"] != bounds["window_id"]
        or result["query_start_ns_inclusive"] != bounds["query_start_ns_inclusive"]
        or result["execution_input_aggregate_sha256"]
        != bindings["execution_input_aggregate_sha256"]
        or result["ordered_warmup_event_ids_sha256"]
        != bindings["ordered_warmup_event_ids_sha256"]
        or result["native_replay_authority_sha256"]
        != bindings["native_replay_authority_sha256"]
    ):
        raise WarmupTranscriptError("warmup boundary-close authority binding differs")
    observed_last = (
        result["last_warmup_occurrence_ordinal"],
        result["last_warmup_event_id"],
        result["last_warmup_timestamp_ns"],
    )
    expected_last = (
        last_endpoint["occurrence_ordinal"], last_endpoint["event_id"],
        last_endpoint["timestamp_ns"],
    )
    if observed_last != expected_last:
        raise WarmupTranscriptError("warmup boundary-close last occurrence differs")
    if (
        result["warmup_transition_count"] != transition_count
        or result["last_warmup_transition_measurement_timestamp_ns"]
        != last_transition_timestamp
    ):
        raise WarmupTranscriptError("warmup boundary-close transition endpoint differs")
    expected_cluster_timestamp = max(
        last_endpoint["timestamp_ns"],
        last_transition_timestamp
        if last_transition_timestamp is not None else last_endpoint["timestamp_ns"],
    )
    if result["last_warmup_cluster_timestamp_ns"] != expected_cluster_timestamp:
        raise WarmupTranscriptError("warmup boundary-close cluster endpoint differs")
    if result["first_query_source_ordinal"] != occurrence_count:
        raise WarmupTranscriptError("first query does not immediately follow warmup source order")
    if result["last_warmup_cluster_timestamp_ns"] == result["first_query_timestamp_ns"]:
        raise WarmupTranscriptError("equal-timestamp warmup/query cluster was split")
    if result["last_warmup_cluster_timestamp_ns"] > result["first_query_timestamp_ns"]:
        raise WarmupTranscriptError("warmup/query timestamp order differs")
    if result["first_query_timestamp_ns"] < bounds["query_start_ns_inclusive"]:
        raise WarmupTranscriptError("first query precedes the query interval")
    return result


def _snapshot_hash(polarity: int, values: Sequence[Mapping[str, object]]) -> str:
    return canonical_sha256({"domain": _SNAPSHOT_DOMAINS[polarity], "observations": list(values)})


class WarmupTranscriptBuilder:
    """Incremental per-window builder with bounded retained state."""

    def __init__(
        self,
        *,
        bindings: Mapping[str, object],
        bounds: Mapping[str, object],
        reference_prime_policy: Mapping[str, object],
        transport_policy: Mapping[str, object],
        reset: Mapping[str, object],
    ) -> None:
        self._bindings = _normalize_bindings(bindings)
        self._bounds = _normalize_bounds(bounds)
        self._reference_policy = _normalize_reference_policy(reference_prime_policy)
        self._transport_policy = _normalize_transport_policy(transport_policy)
        self._reset = _normalize_reset(reset)
        if self._reset["reset_cycle"] >= self._bounds["query_start_decision_cycle"]:
            raise WarmupTranscriptError("reset is not before the query-start edge")
        self._bindings_sha = canonical_sha256(self._bindings)
        self._bounds_sha = canonical_sha256(self._bounds)
        self._reference_policy_sha = canonical_sha256(self._reference_policy)
        self._transport_policy_sha = canonical_sha256(self._transport_policy)
        self._context_sha = canonical_sha256({
            "bindings_sha256": self._bindings_sha,
            "bounds_sha256": self._bounds_sha,
            "reference_prime_policy_sha256": self._reference_policy_sha,
            "reset_sha256": self._reset["reset_sha256"],
            "transport_policy_sha256": self._transport_policy_sha,
        })
        self._occurrence_chain = _chain_seed(_OCCURRENCE_DOMAIN, self._context_sha)
        self._cluster_chain = _chain_seed(_CLUSTER_DOMAIN, self._context_sha)
        self._transition_chain = _chain_seed(_TRANSITION_DOMAIN, self._context_sha)
        self._transport_chain = _chain_seed(_TRANSPORT_DOMAIN, self._context_sha)
        self._occurrence_count = 0
        self._ordered_event_ids_hasher = hashlib.sha256()
        self._ordered_event_ids_hasher.update(b"[")
        self._cluster_count = 0
        self._transition_count = 0
        self._violation_count = 0
        self._current_state = (
            self._reset["predictor_state_version"],
            self._reset["predictor_state_sha256"],
            self._reset["dependency_pose_count"],
            self._reset["dependency_pose_chain_sha256"],
            self._reset["last_dependency_pose_id"],
        )
        self._pending_state = None  # type: Optional[Tuple[int, int, str, int, str, Optional[int]]]
        self._pending_transition_receipt = None  # type: Optional[Dict[str, object]]
        self._last_action_cycle = self._reset["reset_cycle"]
        self._last_action_was_transition = False
        self._last_timestamp = None  # type: Optional[int]
        self._last_transition_commit = None  # type: Optional[int]
        self._last_transition_pose_id = None  # type: Optional[int]
        self._last_transition_measurement_timestamp = None  # type: Optional[int]
        self._first_endpoint = None  # type: Optional[Dict[str, object]]
        self._last_endpoint = None  # type: Optional[Dict[str, object]]
        self._cluster_key = None  # type: Optional[Tuple[int, int, int, int, str, int, str, Optional[int]]]
        self._cluster_first_ordinal = 0
        self._cluster_member_count = 0
        self._cluster_member_chain = ""
        self._cluster_pose_count = 0
        self._cluster_pose_chain = ""
        self._cycle = None  # type: Optional[int]
        self._cycle_first_ordinal = 0
        self._cycle_member_count = 0
        self._cycle_member_chain = ""
        self._reference_banks = (deque(), deque())  # type: Tuple[Deque[Mapping[str, object]], Deque[Mapping[str, object]]]
        self._finalized = False

    def retained_state_counts(self) -> Mapping[str, int]:
        """Return auditable bounds; rich warmup rows are never retained."""

        return {
            "rich_occurrence_rows": 0,
            "rich_transition_rows": 0,
            "reference_polarity_0": len(self._reference_banks[0]),
            "reference_polarity_1": len(self._reference_banks[1]),
        }

    def _ensure_open(self) -> None:
        if self._finalized:
            raise WarmupTranscriptError("warmup transcript builder is finalized")

    def _publish_pending(self, cycle: int) -> None:
        if self._pending_state is not None and self._pending_state[0] <= cycle:
            effective, version, digest, count, chain, last_pose = self._pending_state
            del effective
            self._current_state = (version, digest, count, chain, last_pose)
            self._pending_state = None
            self._pending_transition_receipt = None

    def _finish_cluster(self) -> None:
        if self._cluster_key is None:
            return
        timestamp, occurrence_cycle, decision_cycle, version, digest, dependency_count, dependency_chain, last_pose = self._cluster_key
        leaf = {
            "cluster_ordinal": self._cluster_count,
            "timestamp_ns": timestamp,
            "occurrence_cycle": occurrence_cycle,
            "decision_cycle": decision_cycle,
            "service_cycle": decision_cycle,
            "first_occurrence_ordinal": self._cluster_first_ordinal,
            "occurrence_count": self._cluster_member_count,
            "occurrence_member_chain_sha256": self._cluster_member_chain,
            "predictor_state_version": version,
            "predictor_state_sha256": digest,
            "state_dependency_pose_count": dependency_count,
            "state_dependency_pose_chain_sha256": dependency_chain,
            "state_last_dependency_pose_id": last_pose,
            "same_edge_pose_count": self._cluster_pose_count,
            "same_edge_pose_chain_sha256": self._cluster_pose_chain,
        }
        self._cluster_chain = _chain_link(
            _CLUSTER_DOMAIN, self._cluster_chain, self._cluster_count,
            _leaf_sha256(_CLUSTER_DOMAIN, leaf),
        )
        self._cluster_count += 1
        self._cluster_key = None

    def _finish_cycle(self) -> None:
        if self._cycle is None:
            return
        capacity = self._transport_policy["capacity_per_cycle"]
        if self._cycle_member_count > capacity:
            leaf = {
                "violation_ordinal": self._violation_count,
                "decision_cycle": self._cycle,
                "first_occurrence_ordinal": self._cycle_first_ordinal,
                "last_occurrence_ordinal": self._cycle_first_ordinal + self._cycle_member_count - 1,
                "event_count": self._cycle_member_count,
                "capacity_per_cycle": capacity,
                "overflow_count": self._cycle_member_count - capacity,
                "occurrence_member_chain_sha256": self._cycle_member_chain,
            }
            self._transport_chain = _chain_link(
                _TRANSPORT_DOMAIN, self._transport_chain, self._violation_count,
                _leaf_sha256(_TRANSPORT_DOMAIN, leaf),
            )
            self._violation_count += 1
        self._cycle = None

    def update_occurrence(self, value: Mapping[str, object]) -> None:
        """Consume one warmup occurrence in source/execution order."""

        self._ensure_open()
        occurrence = _normalize_occurrence(value, self._occurrence_count)
        timestamp = occurrence["timestamp_ns"]
        cycle = occurrence["decision_cycle"]
        if not self._bounds["warmup_start_ns_inclusive"] <= timestamp < self._bounds["query_start_ns_inclusive"]:
            raise WarmupTranscriptError("event crosses the warmup/query boundary")
        if cycle >= self._bounds["query_start_decision_cycle"]:
            raise WarmupTranscriptError("warmup decision crosses the query-start edge")
        if cycle < self._last_action_cycle or (cycle == self._last_action_cycle and self._last_action_was_transition):
            raise WarmupTranscriptError("event was reordered after a same-edge pose transition")
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise WarmupTranscriptError("warmup timestamps moved backwards")
        self._publish_pending(cycle)
        expected_state = self._current_state
        observed_state = (
            occurrence["predictor_state_version"], occurrence["predictor_state_sha256"],
            occurrence["state_dependency_pose_count"],
            occurrence["state_dependency_pose_chain_sha256"],
            occurrence["state_last_dependency_pose_id"],
        )
        if observed_state != expected_state:
            raise WarmupTranscriptError("warmup occurrence used unavailable state or pose")

        leaf_sha = _leaf_sha256(_OCCURRENCE_DOMAIN, occurrence)
        if self._occurrence_count:
            self._ordered_event_ids_hasher.update(b",")
        self._ordered_event_ids_hasher.update(str(occurrence["event_id"]).encode("ascii"))
        self._occurrence_chain = _chain_link(
            _OCCURRENCE_DOMAIN, self._occurrence_chain, self._occurrence_count, leaf_sha
        )
        cluster_key = (
            timestamp, occurrence["occurrence_cycle"], cycle,
            occurrence["predictor_state_version"], occurrence["predictor_state_sha256"],
            occurrence["state_dependency_pose_count"],
            occurrence["state_dependency_pose_chain_sha256"],
            occurrence["state_last_dependency_pose_id"],
        )
        if self._cluster_key is None or cluster_key != self._cluster_key:
            if self._cluster_key is not None and timestamp == self._cluster_key[0]:
                raise WarmupTranscriptError("equal-timestamp cluster changed edge or state")
            self._finish_cluster()
            self._cluster_key = cluster_key
            self._cluster_first_ordinal = self._occurrence_count
            self._cluster_member_count = 0
            member_context = canonical_sha256({
                "binding_context_sha256": self._context_sha,
                "cluster_ordinal": self._cluster_count,
            })
            self._cluster_member_chain = _chain_seed(_CLUSTER_MEMBER_DOMAIN, member_context)
            self._cluster_pose_count = 0
            self._cluster_pose_chain = _chain_seed(_CLUSTER_POSE_DOMAIN, member_context)
        self._cluster_member_chain = _chain_link(
            _CLUSTER_MEMBER_DOMAIN, self._cluster_member_chain,
            self._cluster_member_count, leaf_sha,
        )
        self._cluster_member_count += 1

        if self._cycle is None or cycle != self._cycle:
            self._finish_cycle()
            self._cycle = cycle
            self._cycle_first_ordinal = self._occurrence_count
            self._cycle_member_count = 0
            cycle_context = canonical_sha256({
                "binding_context_sha256": self._context_sha, "decision_cycle": cycle,
            })
            self._cycle_member_chain = _chain_seed(_TRANSPORT_MEMBER_DOMAIN, cycle_context)
        self._cycle_member_chain = _chain_link(
            _TRANSPORT_MEMBER_DOMAIN, self._cycle_member_chain,
            self._cycle_member_count, leaf_sha,
        )
        self._cycle_member_count += 1

        max_age = self._reference_policy["max_age_ns"]
        cutoff = timestamp - max_age
        for bank in self._reference_banks:
            while bank and bank[0]["timestamp_ns"] < cutoff:
                bank.popleft()
        observation = {
            "occurrence_ordinal": self._occurrence_count,
            "event_id": occurrence["event_id"],
            "timestamp_ns": timestamp,
            "polarity": occurrence["polarity"],
            "world_ray": list(occurrence["world_ray"]),
        }
        bank = self._reference_banks[occurrence["polarity"]]
        bank.append(observation)
        capacity = self._reference_policy["capacity_per_polarity"]
        while len(bank) > capacity:
            bank.popleft()

        endpoint = {
            "occurrence_ordinal": self._occurrence_count,
            "event_id": occurrence["event_id"],
            "timestamp_ns": timestamp,
        }
        if self._first_endpoint is None:
            self._first_endpoint = endpoint
        self._last_endpoint = endpoint
        self._last_timestamp = timestamp
        self._last_action_cycle = cycle
        self._last_action_was_transition = False
        self._occurrence_count += 1

    def update_state_transition(self, value: Mapping[str, object]) -> None:
        """Consume one pose/state transition in execution order."""

        self._ensure_open()
        transition = _normalize_transition(value, self._transition_count)
        cycle = transition["commit_cycle"]
        pose_id = transition["pose_id"]
        if not self._bounds["warmup_start_ns_inclusive"] <= transition["measurement_timestamp_ns"] < self._bounds["query_start_ns_inclusive"]:
            raise WarmupTranscriptError("state transition measurement crosses the warmup boundary")
        if not self._reset["reset_cycle"] <= cycle < self._bounds["query_start_decision_cycle"]:
            raise WarmupTranscriptError("state transition crosses the reset/query boundary")
        if cycle < self._last_action_cycle:
            raise WarmupTranscriptError("state transition moved backwards")
        if self._last_transition_commit is not None and cycle <= self._last_transition_commit:
            raise WarmupTranscriptError("state transition commit cycles are not strictly increasing")
        if self._last_transition_pose_id is not None and pose_id <= self._last_transition_pose_id:
            raise WarmupTranscriptError("state transition pose IDs are not strictly increasing")
        if transition["publication_cycle"] <= cycle or transition["effective_cycle"] < transition["publication_cycle"]:
            raise WarmupTranscriptError("state transition is not future-only")
        if self._cluster_key is not None and self._cluster_key[2] < cycle:
            self._finish_cluster()
        if self._cycle is not None and self._cycle < cycle:
            self._finish_cycle()
        self._publish_pending(cycle)
        if self._pending_state is not None:
            raise WarmupTranscriptError("state transitions overlap before publication")
        current_version, current_sha, current_count, current_chain, current_last = self._current_state
        prior = (
            transition["prior_state_version"], transition["prior_state_sha256"],
            transition["prior_dependency_pose_count"],
            transition["prior_dependency_pose_chain_sha256"],
            transition["prior_last_dependency_pose_id"],
        )
        if prior != self._current_state:
            raise WarmupTranscriptError("state transition parent differs")
        if transition["state_changed"]:
            next_count = current_count + 1
            next_chain = _dependency_link(current_chain, current_count, pose_id, transition["pose_content_sha256"])
            expected_next = (
                current_version + 1, transition["next_state_sha256"],
                next_count, next_chain, pose_id,
            )
            if transition["next_state_sha256"] == current_sha:
                raise WarmupTranscriptError("changed transition retained the prior state digest")
        else:
            expected_next = self._current_state
        supplied_next = (
            transition["next_state_version"], transition["next_state_sha256"],
            transition["next_dependency_pose_count"],
            transition["next_dependency_pose_chain_sha256"],
            transition["next_last_dependency_pose_id"],
        )
        if supplied_next != expected_next:
            raise WarmupTranscriptError("state transition next state differs")

        leaf_sha = _leaf_sha256(_TRANSITION_DOMAIN, transition)
        self._transition_chain = _chain_link(
            _TRANSITION_DOMAIN, self._transition_chain, self._transition_count, leaf_sha
        )
        if self._cluster_key is not None and self._cluster_key[2] == cycle:
            pose_leaf = {"pose_content_sha256": transition["pose_content_sha256"], "pose_id": pose_id}
            self._cluster_pose_chain = _chain_link(
                _CLUSTER_POSE_DOMAIN, self._cluster_pose_chain,
                self._cluster_pose_count, _leaf_sha256(_CLUSTER_POSE_DOMAIN, pose_leaf),
            )
            self._cluster_pose_count += 1
            self._finish_cluster()
        if self._cycle == cycle:
            self._finish_cycle()
        if transition["state_changed"]:
            self._pending_state = (
                transition["effective_cycle"], supplied_next[0], supplied_next[1],
                supplied_next[2], supplied_next[3], supplied_next[4],
            )
            self._pending_transition_receipt = _pending_transition_receipt(transition)
        self._last_action_cycle = cycle
        self._last_action_was_transition = True
        self._last_transition_commit = cycle
        self._last_transition_pose_id = pose_id
        measurement_timestamp = transition["measurement_timestamp_ns"]
        if (
            self._last_transition_measurement_timestamp is None
            or measurement_timestamp > self._last_transition_measurement_timestamp
        ):
            self._last_transition_measurement_timestamp = measurement_timestamp
        self._transition_count += 1

    def finalize(
        self, query_start_state: Mapping[str, object],
        boundary_close_authority: object = None,
    ) -> Mapping[str, object]:
        """Seal the query-boundary state and bounded reference snapshot."""

        self._ensure_open()
        self._finish_cluster()
        self._finish_cycle()
        if self._occurrence_count == 0 or self._first_endpoint is None or self._last_endpoint is None:
            raise WarmupTranscriptError("warmup occurrence stream must not be empty")
        ordered_ids_hasher = self._ordered_event_ids_hasher.copy()
        ordered_ids_hasher.update(b"]\n")
        if ordered_ids_hasher.hexdigest() != self._bindings["ordered_warmup_event_ids_sha256"]:
            raise WarmupTranscriptError(
                "warmup event IDs differ from authoritative source order"
            )
        checked_boundary = _normalize_boundary_close(
            boundary_close_authority, self._bindings, self._bounds,
            self._last_endpoint, self._occurrence_count,
            self._transition_count, self._last_transition_measurement_timestamp,
        )
        self._publish_pending(self._bounds["query_start_decision_cycle"])
        checked_query_state = _normalize_query_state(query_start_state)
        observed_query_state = (
            checked_query_state["predictor_state_version"],
            checked_query_state["predictor_state_sha256"],
            checked_query_state["dependency_pose_count"],
            checked_query_state["dependency_pose_chain_sha256"],
            checked_query_state["last_dependency_pose_id"],
        )
        if observed_query_state != self._current_state:
            raise WarmupTranscriptError("query-start state does not match causal transitions")

        zero = [dict(row) for row in self._reference_banks[0]]
        one = [dict(row) for row in self._reference_banks[1]]
        snapshot_body = {
            "schema": REFERENCE_SNAPSHOT_SCHEMA,
            "binding_context_sha256": self._context_sha,
            "reference_prime_policy_sha256": self._reference_policy_sha,
            "reference_prime_implementation_sha256": self._bindings["reference_prime_implementation_sha256"],
            "query_start_ns_inclusive": self._bounds["query_start_ns_inclusive"],
            "last_warmup_timestamp_ns": self._last_endpoint["timestamp_ns"],
            "merge_order": "timestamp_ns_then_occurrence_ordinal",
            "polarity_0": zero,
            "polarity_0_sha256": _snapshot_hash(0, zero),
            "polarity_1": one,
            "polarity_1_sha256": _snapshot_hash(1, one),
            "observation_count": len(zero) + len(one),
            "occupancy": [len(zero), len(one)],
        }
        snapshot = dict(snapshot_body, snapshot_sha256=canonical_sha256(snapshot_body))
        body = {
            "schema": WARMUP_TRANSCRIPT_SCHEMA,
            "bindings": self._bindings,
            "bindings_sha256": self._bindings_sha,
            "bounds": self._bounds,
            "bounds_sha256": self._bounds_sha,
            "reference_prime_policy": self._reference_policy,
            "reference_prime_policy_sha256": self._reference_policy_sha,
            "transport_policy": self._transport_policy,
            "transport_policy_sha256": self._transport_policy_sha,
            "reset": self._reset,
            "query_start_state": checked_query_state,
            "binding_context_sha256": self._context_sha,
            "warmup_occurrence_count": self._occurrence_count,
            "same_edge_cluster_count": self._cluster_count,
            "state_transition_count": self._transition_count,
            "transport_cycle_violation_count": self._violation_count,
            "warmup_occurrence_chain_sha256": self._occurrence_chain,
            "same_edge_cluster_chain_sha256": self._cluster_chain,
            "state_transition_chain_sha256": self._transition_chain,
            "transport_cycle_violation_chain_sha256": self._transport_chain,
            "first_warmup_occurrence": self._first_endpoint,
            "last_warmup_occurrence": self._last_endpoint,
            "boundary_close_authority": checked_boundary,
            "pending_query_transition": self._pending_transition_receipt,
            "native_replay_authority": _native_replay_authority(self._bindings),
            "query_start_reference_snapshot": snapshot,
        }
        self._finalized = True
        return dict(body, receipt_sha256=canonical_sha256(body))


def begin_warmup_transcript(
    *, bindings: Mapping[str, object], bounds: Mapping[str, object],
    reference_prime_policy: Mapping[str, object],
    transport_policy: Mapping[str, object], reset: Mapping[str, object],
) -> WarmupTranscriptBuilder:
    """Begin an incremental transcript without allocating a warmup row array."""

    return WarmupTranscriptBuilder(
        bindings=bindings, bounds=bounds,
        reference_prime_policy=reference_prime_policy,
        transport_policy=transport_policy, reset=reset,
    )


class _Peekable:
    def __init__(self, values: Iterable[Mapping[str, object]]) -> None:
        self._iterator = iter(values)  # type: Iterator[Mapping[str, object]]
        self._value = None  # type: Optional[Mapping[str, object]]
        self._loaded = False

    def peek(self) -> Optional[Mapping[str, object]]:
        if not self._loaded:
            try:
                self._value = next(self._iterator)
            except StopIteration:
                self._value = None
            self._loaded = True
        return self._value

    def pop(self) -> Mapping[str, object]:
        value = self.peek()
        if value is None:
            raise StopIteration
        self._loaded = False
        self._value = None
        return value


def build_warmup_transcript(
    *, bindings: Mapping[str, object], bounds: Mapping[str, object],
    reference_prime_policy: Mapping[str, object],
    transport_policy: Mapping[str, object], reset: Mapping[str, object],
    query_start_state: Mapping[str, object],
    boundary_close_authority: Mapping[str, object],
    warmup_occurrences: Iterable[Mapping[str, object]],
    state_transitions: Iterable[Mapping[str, object]],
) -> Mapping[str, object]:
    """Merge authoritative replay iterables with events before same-edge poses.

    Both streams must be reconstructed by the native replay identified in the
    bindings.  The fixed authority receipt records that provenance boundary;
    row seals and transcript chains alone do not authenticate derived rays or
    decisions.
    """

    builder = begin_warmup_transcript(
        bindings=bindings, bounds=bounds,
        reference_prime_policy=reference_prime_policy,
        transport_policy=transport_policy, reset=reset,
    )
    occurrences = _Peekable(warmup_occurrences)
    transitions = _Peekable(state_transitions)
    while occurrences.peek() is not None or transitions.peek() is not None:
        occurrence = occurrences.peek()
        transition = transitions.peek()
        if occurrence is None:
            builder.update_state_transition(transitions.pop())
        elif transition is None:
            builder.update_occurrence(occurrences.pop())
        else:
            occurrence_row = _mapping(occurrence, _OCCURRENCE_FIELDS, "warmup occurrence")
            transition_row = _mapping(transition, _TRANSITION_FIELDS, "state transition")
            occurrence_cycle = _integer(occurrence_row["decision_cycle"], "decision cycle")
            transition_cycle = _integer(transition_row["commit_cycle"], "pose commit cycle")
            if occurrence_cycle <= transition_cycle:
                builder.update_occurrence(occurrences.pop())
            else:
                builder.update_state_transition(transitions.pop())
    return builder.finalize(query_start_state, boundary_close_authority)


def _validate_snapshot(value: object, policy: Mapping[str, object], bounds: Mapping[str, object]) -> Dict[str, object]:
    row = _sealed_mapping(value, _SNAPSHOT_FIELDS, "snapshot_sha256", "reference snapshot")
    if row["schema"] != REFERENCE_SNAPSHOT_SCHEMA or row["merge_order"] != "timestamp_ns_then_occurrence_ordinal":
        raise WarmupTranscriptError("reference snapshot contract differs")
    if row["query_start_ns_inclusive"] != bounds["query_start_ns_inclusive"]:
        raise WarmupTranscriptError("reference snapshot query boundary differs")
    last_warmup_timestamp = _integer(
        row["last_warmup_timestamp_ns"], "reference last warmup timestamp"
    )
    if not (
        bounds["warmup_start_ns_inclusive"]
        <= last_warmup_timestamp
        < bounds["query_start_ns_inclusive"]
    ):
        raise WarmupTranscriptError("reference snapshot warmup boundary differs")
    checked = []  # type: List[List[Mapping[str, object]]]
    for polarity in (0, 1):
        field = "polarity_%d" % polarity
        values = row[field]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise WarmupTranscriptError("reference snapshot bank must be an ordered array")
        if len(values) > policy["capacity_per_polarity"]:
            raise WarmupTranscriptError("reference snapshot exceeds polarity capacity")
        normalized = []  # type: List[Mapping[str, object]]
        previous = None  # type: Optional[Tuple[int, int]]
        for raw in values:
            item = _mapping(raw, _SNAPSHOT_OBSERVATION_FIELDS, "reference observation")
            current = {
                "occurrence_ordinal": _integer(item["occurrence_ordinal"], "reference ordinal"),
                "event_id": _integer(item["event_id"], "reference event ID"),
                "timestamp_ns": _integer(item["timestamp_ns"], "reference timestamp"),
                "polarity": _integer(item["polarity"], "reference polarity"),
                "world_ray": _unit_ray(item["world_ray"], "reference world ray"),
            }
            if current["polarity"] != polarity:
                raise WarmupTranscriptError("reference observation is in the wrong polarity bank")
            key = (current["timestamp_ns"], current["occurrence_ordinal"])
            if previous is not None and key <= previous:
                raise WarmupTranscriptError("reference snapshot is not oldest-to-newest")
            cutoff = last_warmup_timestamp - policy["max_age_ns"]
            if not cutoff <= current["timestamp_ns"] <= last_warmup_timestamp:
                raise WarmupTranscriptError(
                    "reference observation differs from the last warmup bank state"
                )
            previous = key
            normalized.append(current)
        if row[field + "_sha256"] != _snapshot_hash(polarity, normalized):
            raise WarmupTranscriptError("reference polarity snapshot seal differs")
        checked.append(normalized)
    if row["occupancy"] != [len(checked[0]), len(checked[1])] or row["observation_count"] != len(checked[0]) + len(checked[1]):
        raise WarmupTranscriptError("reference snapshot counts differ")
    result = dict(row)
    result["polarity_0"] = checked[0]
    result["polarity_1"] = checked[1]
    return result


def _validate_receipt(value: object) -> Dict[str, object]:
    row = _sealed_mapping(value, _RECEIPT_FIELDS, "receipt_sha256", "warmup transcript")
    if row["schema"] != WARMUP_TRANSCRIPT_SCHEMA:
        raise WarmupTranscriptError("warmup transcript schema differs")
    bindings = _normalize_bindings(row["bindings"])
    bounds = _normalize_bounds(row["bounds"])
    reference_policy = _normalize_reference_policy(row["reference_prime_policy"])
    transport_policy = _normalize_transport_policy(row["transport_policy"])
    reset = _normalize_reset(row["reset"])
    query_state = _normalize_query_state(row["query_start_state"])
    pending_transition = _normalize_pending_transition(row["pending_query_transition"])
    replay_authority = _normalize_native_replay_authority(
        row["native_replay_authority"], bindings
    )
    if row["bindings_sha256"] != canonical_sha256(bindings) or row["bounds_sha256"] != canonical_sha256(bounds):
        raise WarmupTranscriptError("transcript input binding seal differs")
    if row["reference_prime_policy_sha256"] != canonical_sha256(reference_policy) or row["transport_policy_sha256"] != canonical_sha256(transport_policy):
        raise WarmupTranscriptError("transcript policy seal differs")
    expected_context_sha = canonical_sha256({
        "bindings_sha256": row["bindings_sha256"],
        "bounds_sha256": row["bounds_sha256"],
        "reference_prime_policy_sha256": row["reference_prime_policy_sha256"],
        "reset_sha256": reset["reset_sha256"],
        "transport_policy_sha256": row["transport_policy_sha256"],
    })
    if row["binding_context_sha256"] != expected_context_sha:
        raise WarmupTranscriptError("transcript binding context seal differs")
    for field in (
        "binding_context_sha256", "warmup_occurrence_chain_sha256",
        "same_edge_cluster_chain_sha256", "state_transition_chain_sha256",
        "transport_cycle_violation_chain_sha256",
    ):
        _sha256(row[field], field)
    for field in (
        "warmup_occurrence_count", "same_edge_cluster_count",
        "state_transition_count", "transport_cycle_violation_count",
    ):
        _integer(row[field], field)
    for field in ("first_warmup_occurrence", "last_warmup_occurrence"):
        endpoint = _mapping(row[field], _ENDPOINT_FIELDS, field)
        _integer(endpoint["occurrence_ordinal"], field + " ordinal")
        _integer(endpoint["event_id"], field + " event ID")
        timestamp = _integer(endpoint["timestamp_ns"], field + " timestamp")
        if not bounds["warmup_start_ns_inclusive"] <= timestamp < bounds["query_start_ns_inclusive"]:
            raise WarmupTranscriptError("transcript endpoint crosses warmup bounds")
    boundary_raw = _mapping(
        row["boundary_close_authority"], _BOUNDARY_CLOSE_FIELDS,
        "warmup boundary-close authority",
    )
    boundary_last_transition = boundary_raw[
        "last_warmup_transition_measurement_timestamp_ns"
    ]
    if boundary_last_transition is not None:
        boundary_last_transition = _integer(
            boundary_last_transition,
            "boundary last transition measurement timestamp",
        )
    boundary_close = _normalize_boundary_close(
        boundary_raw, bindings, bounds, row["last_warmup_occurrence"],
        row["warmup_occurrence_count"], row["state_transition_count"],
        boundary_last_transition,
    )
    snapshot = _validate_snapshot(row["query_start_reference_snapshot"], reference_policy, bounds)
    if snapshot["last_warmup_timestamp_ns"] != row["last_warmup_occurrence"]["timestamp_ns"]:
        raise WarmupTranscriptError("reference snapshot last-warmup binding differs")
    if snapshot["binding_context_sha256"] != row["binding_context_sha256"] or snapshot["reference_prime_policy_sha256"] != row["reference_prime_policy_sha256"]:
        raise WarmupTranscriptError("reference snapshot authority binding differs")
    if snapshot["reference_prime_implementation_sha256"] != bindings["reference_prime_implementation_sha256"]:
        raise WarmupTranscriptError("reference snapshot implementation binding differs")
    if pending_transition is not None:
        if pending_transition["effective_cycle"] <= bounds["query_start_decision_cycle"]:
            raise WarmupTranscriptError("pending transition is already effective at query start")
        expected_next = (
            pending_transition["next_state_version"],
            pending_transition["next_state_sha256"],
            pending_transition["next_dependency_pose_count"],
            pending_transition["next_dependency_pose_chain_sha256"],
            pending_transition["next_last_dependency_pose_id"],
        )
        observed_query = (
            query_state["predictor_state_version"],
            query_state["predictor_state_sha256"],
            query_state["dependency_pose_count"],
            query_state["dependency_pose_chain_sha256"],
            query_state["last_dependency_pose_id"],
        )
        if expected_next == observed_query:
            raise WarmupTranscriptError("pending transition was prematurely applied")
    result = dict(row)
    result.update({
        "bindings": bindings, "bounds": bounds,
        "reference_prime_policy": reference_policy,
        "transport_policy": transport_policy, "reset": reset,
        "query_start_state": query_state,
        "boundary_close_authority": boundary_close,
        "pending_query_transition": pending_transition,
        "native_replay_authority": replay_authority,
        "query_start_reference_snapshot": snapshot,
    })
    return result


def verify_warmup_transcript(
    receipt: object, *, bindings: Mapping[str, object], bounds: Mapping[str, object],
    reference_prime_policy: Mapping[str, object],
    transport_policy: Mapping[str, object], reset: Mapping[str, object],
    query_start_state: Mapping[str, object],
    boundary_close_authority: Mapping[str, object],
    warmup_occurrences: Iterable[Mapping[str, object]],
    state_transitions: Iterable[Mapping[str, object]],
) -> str:
    """Recompute from dependency-bound deterministic-replay iterables.

    The stream chains provide transcript integrity, not independent source
    authentication.  Callers must reconstruct both iterables using the fixed
    native replay authority and the dependency/input digests in ``bindings``.
    """

    checked = _validate_receipt(receipt)
    expected = build_warmup_transcript(
        bindings=bindings, bounds=bounds,
        reference_prime_policy=reference_prime_policy,
        transport_policy=transport_policy, reset=reset,
        query_start_state=query_start_state,
        boundary_close_authority=boundary_close_authority,
        warmup_occurrences=warmup_occurrences,
        state_transitions=state_transitions,
    )
    if checked != expected:
        raise WarmupTranscriptError("warmup transcript differs from replayed iterables")
    return _sha256(expected["receipt_sha256"], "warmup transcript receipt")


__all__ = (
    "BOUNDARY_CLOSE_SCHEMA", "NATIVE_REPLAY_AUTHORITY_SHA256",
    "PENDING_TRANSITION_SCHEMA",
    "QUERY_START_STATE_SCHEMA", "REFERENCE_PRIME_POLICY_SCHEMA",
    "REFERENCE_SNAPSHOT_SCHEMA", "REPLAY_AUTHORITY_SCHEMA", "RESET_SCHEMA",
    "TRANSPORT_POLICY_SCHEMA", "WARMUP_TRANSCRIPT_SCHEMA", "WarmupTranscriptBuilder",
    "WarmupTranscriptError", "begin_warmup_transcript",
    "build_warmup_transcript", "verify_warmup_transcript",
)
