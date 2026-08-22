"""Label-isolated execution, label, and scoring-join authorities for Stage 3.

The execution artifact is the only artifact that may be made visible to a
candidate.  It contains neutral inputs and a score-free current-CAV replay,
but it cannot represent selector rows, labels, ranks, scores, or evaluator
dependencies.  Label authority is built separately.  The two authorities
meet only in a receipt built after a candidate output has a valid aggregate
seal.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3 import current_cav_trace as _trace
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
    logical_replay_authority,
    run_stage3_logical_cycle_model,
)


EXECUTION_INPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.execution_input/v2"
LABEL_AUTHORITY_SCHEMA = "redred.mc_wtb_predictor_stage3.label_authority/v1"
SCORING_JOIN_SCHEMA = "redred.mc_wtb_predictor_stage3.scoring_join_receipt/v1"

PRE_ROLL_NS = 50_000_000
LOGICAL_INGRESS_SCOPE = "MODEL_ONLY_LOGICAL_REPLAY_NO_RTL_OR_PPA_CLAIM"
_PREROLL_RULE = {
    "duration_ns": PRE_ROLL_NS,
    "interval": "[query_start_ns-50000000,query_start_ns)",
    "state_initialization": "reset_then_replay_in_source_order",
    "scoring": "query_interval_only",
    "insufficient_support": "hard_fail_no_extension",
    "outcome_dependent_extension": False,
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_WINDOW_IDENTIFIER = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9_.:-]*|"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*/query_start_ns=(0|[1-9][0-9]*))\Z"
)
_CANDIDATE_IDENTIFIER = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/,+-]{0,510}[A-Za-z0-9])?\Z"
)
_FORBIDDEN_CANDIDATE_FIELD_PIECES = (
    "selector", "label", "score", "evaluator",
)
_LOCKED_CYCLE_PROFILE = STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE
_LOCKED_CYCLE_RUNNER = run_stage3_logical_cycle_model

# These are the complete Python dependencies executed by the score-free trace
# consumer.  The authority producer itself is deliberately absent: this file
# also contains scorer-side label/join code and must never enter a candidate-
# visible dependency closure.
CONSUMER_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
)

_EXECUTION_FIELDS = frozenset((
    "schema", "source_events_authority", "timing_authority",
    "logical_ingress_profile", "logical_ingress_profile_sha256",
    "logical_cycle_replay_authority",
    "logical_cycle_replay_authority_sha256",
    "consumer_dependency_manifest", "consumer_dependency_aggregate_sha256",
    "neutral_registry", "neutral_registry_sha256", "ordered_window_ids_sha256",
    "ordered_event_occurrences_sha256", "ordered_query_event_ids_sha256",
    "windows", "windows_sha256", "neutral_input_sha256",
    "score_free_current_cav_trace", "score_free_current_cav_trace_sha256",
    "window_count", "source_event_occurrence_count", "unique_source_event_count",
    "warmup_event_occurrence_count", "query_event_count", "pose_occurrence_count",
    "aggregate_sha256",
))
_SOURCE_FIELDS = frozenset((
    "source_events_path", "source_events_sha256", "source_events_size_bytes",
    "source_events_line_count",
))
_TIMING_FIELDS = frozenset((
    "pre_roll_ns", "candidate_screen_preroll_rule",
    "candidate_screen_preroll_rule_sha256",
))
_PROFILE_FIELDS = frozenset((
    "schema", "profile_id", "raw_ingress_lanes", "ingress_staging_entries",
    "event_service_lanes", "scope",
))
_LOGICAL_REPLAY_AUTHORITY_FIELDS = frozenset((
    "schema", "frozen_stage4_model_sha256", "frozen_stage4_api_sha256",
    "private_module_namespace", "profile", "profile_sha256", "overrides",
    "retained_event_service_lanes", "exposed_arm", "event_order_rule",
    "event_id_transport", "canonical_module_mutation", "authority_sha256",
))
_DEPENDENCY_FIELDS = frozenset(("path", "sha256"))
_REGISTRY_FIELDS = frozenset((
    "window_id", "warmup_start_ns_inclusive", "query_start_ns_inclusive",
    "query_end_ns_exclusive",
))
_WINDOW_FIELDS = frozenset((
    "window_id", "neutral_bounds_sha256", "events", "events_sha256",
    "ordered_source_event_ids_sha256", "ordered_warmup_event_ids_sha256",
    "ordered_query_event_ids_sha256", "source_event_count", "warmup_event_count",
    "query_event_count", "poses", "poses_sha256", "ordered_pose_ids_sha256",
    "negative_commit_pose_ids_sha256", "pose_input_count", "neutral_inputs_sha256",
    "current_cav_window_trace_sha256",
))
_EVENT_FIELDS = frozenset((
    "event_id", "timestamp_ns", "polarity", "is_query", "sensor_ray",
    "causal_pose_source_index", "event_content_sha256", "transform_guard_valid",
))
_POSE_FIELDS = frozenset((
    "pose_id", "timestamp_ns", "commit_cycle", "quaternion_xyzw", "pose_sha256",
    "value_valid", "arithmetic_valid",
))
_LABEL_AUTHORITY_FIELDS = frozenset((
    "schema", "execution_input_aggregate_sha256", "execution_neutral_input_sha256",
    "ordered_window_ids_sha256", "ordered_query_event_ids_sha256", "window_count",
    "selector_authority", "labels", "selector_labels_sidecar_sha256", "joins",
    "joins_sha256", "aggregate_sha256",
))
_SELECTOR_AUTHORITY_FIELDS = frozenset((
    "stage12_freeze_receipt_sha256", "stage12_source_split_plan_sha256",
    "selector_registry_sha256", "selector_implementation_sha256",
))
_LABEL_FIELDS = frozenset((
    "window_id", "axis", "sign", "motion_bin", "rotation_vector_rad", "purity",
    "motion_proxy", "rank_sha256", "label_sha256",
))
_JOIN_FIELDS = frozenset((
    "schema", "execution_input_aggregate_sha256", "neutral_input_sha256",
    "candidate_output_schema", "candidate_id", "candidate_output_aggregate_sha256",
    "candidate_output_ordered_event_occurrences_sha256",
    "label_authority_aggregate_sha256", "selector_labels_sidecar_sha256",
    "ordered_window_ids_sha256", "ordered_query_event_ids_sha256", "window_count",
    "query_event_count", "joined_windows_sha256", "aggregate_sha256",
))


class Stage3ExecutionAuthorityError(ValueError):
    """An execution, label-authority, or post-output join seal failed."""


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise Stage3ExecutionAuthorityError("%s must be an object" % where)
    return value


def _exact(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    row = _mapping(value, where)
    if frozenset(row) != fields:
        raise Stage3ExecutionAuthorityError("%s field schema differs" % where)
    return row


def _sequence(value: object, where: str, *, nonempty: bool = True) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise Stage3ExecutionAuthorityError("%s must be an ordered array" % where)
    if nonempty and not value:
        raise Stage3ExecutionAuthorityError("%s must be nonempty" % where)
    return value


def _text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        raise Stage3ExecutionAuthorityError("%s must be nonempty text" % where)
    return value


def _identifier(value: object, where: str) -> str:
    result = _text(value, where)
    if _IDENTIFIER.fullmatch(result) is None:
        raise Stage3ExecutionAuthorityError("%s is not a canonical identifier" % where)
    return result


def _window_identifier(value: object, query_start_ns: int, where: str) -> str:
    result = _text(value, where)
    match = _WINDOW_IDENTIFIER.fullmatch(result)
    if match is None or len(result) > 512:
        raise Stage3ExecutionAuthorityError("%s is not a canonical window identifier" % where)
    encoded_query = match.group(1)
    if encoded_query is not None and int(encoded_query) != query_start_ns:
        raise Stage3ExecutionAuthorityError("%s query timestamp differs" % where)
    return result


def _candidate_identifier(value: object, where: str) -> str:
    result = _text(value, where)
    if _CANDIDATE_IDENTIFIER.fullmatch(result) is None:
        raise Stage3ExecutionAuthorityError("%s is not a canonical candidate identifier" % where)
    return result


def _reject_forbidden_candidate_fields(value: object, where: str = "candidate output") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise Stage3ExecutionAuthorityError("%s field name must be text" % where)
            lowered = key.lower()
            if any(piece in lowered for piece in _FORBIDDEN_CANDIDATE_FIELD_PIECES):
                raise Stage3ExecutionAuthorityError("candidate output contains a forbidden field")
            _reject_forbidden_candidate_fields(item, "%s.%s" % (where, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _reject_forbidden_candidate_fields(item, "%s[%d]" % (where, index))


def _sha(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise Stage3ExecutionAuthorityError("%s must be lowercase SHA-256" % where)
    return value


def _integer(value: object, where: str, *, signed: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Stage3ExecutionAuthorityError("%s must be an integer" % where)
    if not signed and value < 0:
        raise Stage3ExecutionAuthorityError("%s must be nonnegative" % where)
    return value


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Stage3ExecutionAuthorityError("%s must be finite" % where)
    result = float(value)
    if not math.isfinite(result):
        raise Stage3ExecutionAuthorityError("%s must be finite" % where)
    return result


def _sealed(body: Mapping[str, object], field: str = "aggregate_sha256") -> Dict[str, object]:
    result = dict(body)
    result[field] = canonical_sha256(body)
    return result


def _verify_seal(row: Mapping[str, object], field: str, where: str) -> str:
    supplied = _sha(row.get(field), "%s %s" % (where, field))
    body = {key: value for key, value in row.items() if key != field}
    if supplied != canonical_sha256(body):
        raise Stage3ExecutionAuthorityError("%s seal differs" % where)
    return supplied


def _canonical_relative_path(value: object, where: str) -> str:
    path = _text(value, where)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or str(parsed) != path or not parsed.parts:
        raise Stage3ExecutionAuthorityError("%s is not a canonical relative path" % where)
    if any(part in ("", ".", "..") for part in parsed.parts):
        raise Stage3ExecutionAuthorityError("%s contains a path alias" % where)
    return path


def _repo_root(repo_root: Optional[Path]) -> Path:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    root = root.resolve()
    if not root.is_dir():
        raise Stage3ExecutionAuthorityError("repository root differs")
    return root


def _module_for_path(path: str) -> str:
    if path.endswith("/__init__.py"):
        return path[:-12].replace("/", ".")
    return path[:-3].replace("/", ".")


def _resolve_import(module: str, node: ast.ImportFrom, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    package = module if is_package else module.rsplit(".", 1)[0]
    parts = package.split(".") if package else []
    if node.level > len(parts):
        return ""
    base = parts[:len(parts) - node.level + 1]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _verify_import_closure(root: Path, paths: Sequence[str]) -> None:
    modules = {_module_for_path(path) for path in paths}
    forbidden = ("selector", "evaluator", "scorer", "scoring", "screen108", "causal_reference")
    for path in paths:
        module = _module_for_path(path)
        is_package = path.endswith("/__init__.py")
        try:
            tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise Stage3ExecutionAuthorityError("dependency source cannot be parsed") from exc
        # Only imports executed while these modules are loaded belong to this
        # execution closure.  Function-local optional imports are unreachable
        # from the fixed current-CAV builder path and are not dependencies of
        # that replay.
        for node in tree.body:
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_import(module, node, is_package)
                if resolved:
                    imported = [resolved]
                    for alias in node.names:
                        if alias.name != "*":
                            candidate = "%s.%s" % (resolved, alias.name)
                            module_path = root / (candidate.replace(".", "/") + ".py")
                            package_path = root / candidate.replace(".", "/") / "__init__.py"
                            if module_path.is_file() or package_path.is_file():
                                imported.append(candidate)
                        if any(piece in alias.name.lower() for piece in forbidden):
                            raise Stage3ExecutionAuthorityError("forbidden candidate dependency import")
            for name in imported:
                lowered = name.lower()
                if any(piece in lowered for piece in forbidden):
                    raise Stage3ExecutionAuthorityError("forbidden candidate dependency import")
                if name.startswith("benchmarks.") and name not in modules:
                    raise Stage3ExecutionAuthorityError("consumer dependency closure is incomplete")


def _build_dependency_manifest(root: Path) -> Sequence[Mapping[str, object]]:
    rows = []
    seen_inodes = set()
    for relative in CONSUMER_DEPENDENCY_PATHS:
        target = root / relative
        try:
            metadata = target.lstat()
            resolved = target.resolve(strict=True)
            content = target.read_bytes()
        except OSError as exc:
            raise Stage3ExecutionAuthorityError("consumer dependency is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise Stage3ExecutionAuthorityError("consumer dependency path aliases storage")
        if root not in resolved.parents:
            raise Stage3ExecutionAuthorityError("consumer dependency escapes repository")
        inode = (metadata.st_dev, metadata.st_ino)
        if inode in seen_inodes:
            raise Stage3ExecutionAuthorityError("consumer dependencies alias one inode")
        seen_inodes.add(inode)
        import hashlib
        rows.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    _verify_import_closure(root, CONSUMER_DEPENDENCY_PATHS)
    return rows


def _verify_dependency_manifest(value: object, root: Path) -> Sequence[Mapping[str, object]]:
    rows = _sequence(value, "consumer dependency manifest")
    if len(rows) != len(CONSUMER_DEPENDENCY_PATHS):
        raise Stage3ExecutionAuthorityError("consumer dependency cardinality differs")
    checked = []
    for index, (raw, expected_path) in enumerate(zip(rows, CONSUMER_DEPENDENCY_PATHS)):
        row = _exact(raw, _DEPENDENCY_FIELDS, "consumer dependency")
        path = _canonical_relative_path(row.get("path"), "consumer dependency path")
        if path != expected_path:
            raise Stage3ExecutionAuthorityError("consumer dependency order or path differs")
        checked.append({"path": path, "sha256": _sha(row.get("sha256"), "consumer dependency digest")})
    expected = _build_dependency_manifest(root)
    if checked != expected:
        raise Stage3ExecutionAuthorityError("consumer dependency content differs")
    return checked


def _validate_source(value: object) -> Mapping[str, object]:
    row = _exact(value, _SOURCE_FIELDS, "source-events authority")
    result = {
        "source_events_path": _canonical_relative_path(row.get("source_events_path"), "source-events path"),
        "source_events_sha256": _sha(row.get("source_events_sha256"), "source-events digest"),
        "source_events_size_bytes": _integer(row.get("source_events_size_bytes"), "source-events size"),
        "source_events_line_count": _integer(row.get("source_events_line_count"), "source-events line count"),
    }
    if result["source_events_size_bytes"] == 0 or result["source_events_line_count"] == 0:
        raise Stage3ExecutionAuthorityError("source-events authority is empty")
    return result


def _validate_profile(value: object) -> Mapping[str, object]:
    row = _exact(value, _PROFILE_FIELDS, "logical ingress profile")
    result = {
        "schema": _text(row.get("schema"), "logical ingress schema"),
        "profile_id": _identifier(row.get("profile_id"), "logical ingress profile ID"),
        "raw_ingress_lanes": _integer(row.get("raw_ingress_lanes"), "raw ingress lanes"),
        "ingress_staging_entries": _integer(row.get("ingress_staging_entries"), "ingress staging entries"),
        "event_service_lanes": _integer(row.get("event_service_lanes"), "event service lanes"),
        "scope": _text(row.get("scope"), "logical ingress scope"),
    }
    if (result["raw_ingress_lanes"] == 0 or result["event_service_lanes"] == 0
            or result["ingress_staging_entries"] < result["raw_ingress_lanes"]):
        raise Stage3ExecutionAuthorityError("logical ingress capacity differs")
    if result != _LOCKED_CYCLE_PROFILE.to_mapping():
        raise Stage3ExecutionAuthorityError("logical ingress profile differs from locked Stage3 profile")
    return result


def _profile_mapping(profile: object) -> Mapping[str, object]:
    try:
        value = profile.to_mapping()  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise Stage3ExecutionAuthorityError("logical ingress profile has no mapping") from exc
    return _validate_profile(value)


def _validate_logical_replay_authority(value: object) -> Mapping[str, object]:
    row = _exact(
        value,
        _LOGICAL_REPLAY_AUTHORITY_FIELDS,
        "logical cycle replay authority",
    )
    supplied = dict(row)
    supplied_sha256 = _sha(
        supplied.pop("authority_sha256"),
        "logical cycle replay authority internal digest",
    )
    if supplied_sha256 != canonical_sha256(supplied):
        raise Stage3ExecutionAuthorityError(
            "logical cycle replay authority internal seal differs"
        )
    try:
        expected = dict(logical_replay_authority())
    except (TypeError, ValueError) as exc:
        raise Stage3ExecutionAuthorityError(
            "locked logical cycle replay authority is unavailable"
        ) from exc
    if dict(row) != expected:
        raise Stage3ExecutionAuthorityError(
            "logical cycle replay authority differs from locked Stage3 authority"
        )
    return expected


def _timing_authority() -> Mapping[str, object]:
    return {
        "pre_roll_ns": PRE_ROLL_NS,
        "candidate_screen_preroll_rule": dict(_PREROLL_RULE),
        "candidate_screen_preroll_rule_sha256": canonical_sha256(_PREROLL_RULE),
    }


def _validate_timing(value: object) -> Mapping[str, object]:
    row = _exact(value, _TIMING_FIELDS, "timing authority")
    if row != _timing_authority():
        raise Stage3ExecutionAuthorityError("50 ms timing authority differs")
    return row


def _validate_registry(value: object) -> Mapping[str, object]:
    row = _exact(value, _REGISTRY_FIELDS, "neutral registry row")
    query_start = _integer(row.get("query_start_ns_inclusive"), "query start")
    result = {
        "window_id": _window_identifier(row.get("window_id"), query_start, "window ID"),
        "warmup_start_ns_inclusive": _integer(row.get("warmup_start_ns_inclusive"), "warmup start"),
        "query_start_ns_inclusive": query_start,
        "query_end_ns_exclusive": _integer(row.get("query_end_ns_exclusive"), "query end"),
    }
    if result["query_start_ns_inclusive"] - result["warmup_start_ns_inclusive"] != PRE_ROLL_NS:
        raise Stage3ExecutionAuthorityError("window does not have exact 50 ms pre-roll")
    if result["query_end_ns_exclusive"] <= result["query_start_ns_inclusive"]:
        raise Stage3ExecutionAuthorityError("query interval is empty")
    return result


def _validate_event(value: object, registry: Mapping[str, object]) -> Mapping[str, object]:
    row = _exact(value, _EVENT_FIELDS, "neutral event")
    event_id = _integer(row.get("event_id"), "event ID")
    timestamp = _integer(row.get("timestamp_ns"), "event timestamp")
    polarity = _integer(row.get("polarity"), "event polarity")
    if polarity not in (0, 1) or type(row.get("is_query")) is not bool or type(row.get("transform_guard_valid")) is not bool:
        raise Stage3ExecutionAuthorityError("neutral event scalar differs")
    ray = _sequence(row.get("sensor_ray"), "sensor ray")
    if len(ray) != 3:
        raise Stage3ExecutionAuthorityError("sensor ray cardinality differs")
    converted = [_finite(component, "sensor ray component") for component in ray]
    norm = math.sqrt(math.fsum(component * component for component in converted))
    if abs(norm - 1.0) > 1.0e-9:
        raise Stage3ExecutionAuthorityError("sensor ray is not unit length")
    causal = _integer(row.get("causal_pose_source_index"), "causal pose source index")
    expected_query = timestamp >= registry["query_start_ns_inclusive"]
    if row.get("is_query") is not expected_query:
        raise Stage3ExecutionAuthorityError("event query membership differs")
    if not registry["warmup_start_ns_inclusive"] <= timestamp < registry["query_end_ns_exclusive"]:
        raise Stage3ExecutionAuthorityError("event lies outside neutral bounds")
    expected_digest = _trace.canonical_event_content_sha256(
        event_id, timestamp, polarity, expected_query, converted, causal,
        row.get("transform_guard_valid"),
    )
    if _sha(row.get("event_content_sha256"), "event content digest") != expected_digest:
        raise Stage3ExecutionAuthorityError("event content digest differs")
    return dict(row)


def _validate_pose(value: object, registry: Mapping[str, object]) -> Mapping[str, object]:
    row = _exact(value, _POSE_FIELDS, "neutral pose")
    pose_id = _integer(row.get("pose_id"), "pose ID")
    timestamp = _integer(row.get("timestamp_ns"), "pose timestamp")
    commit = _integer(row.get("commit_cycle"), "pose commit cycle", signed=True)
    quaternion = _sequence(row.get("quaternion_xyzw"), "pose quaternion")
    if len(quaternion) != 4:
        raise Stage3ExecutionAuthorityError("pose quaternion cardinality differs")
    converted = [_finite(component, "pose quaternion component") for component in quaternion]
    norm = math.sqrt(math.fsum(component * component for component in converted))
    if abs(norm - 1.0) > 1.0e-9:
        raise Stage3ExecutionAuthorityError("pose quaternion is not unit length")
    if type(row.get("value_valid")) is not bool or type(row.get("arithmetic_valid")) is not bool:
        raise Stage3ExecutionAuthorityError("pose validity flags differ")
    expected_commit = _trace.pose_timestamp_to_cycle(timestamp, registry["warmup_start_ns_inclusive"])
    if commit != expected_commit:
        raise Stage3ExecutionAuthorityError("pose commit cycle differs")
    expected_digest = _trace.canonical_pose_value_sha256(pose_id, timestamp, converted)
    if _sha(row.get("pose_sha256"), "pose digest") != expected_digest:
        raise Stage3ExecutionAuthorityError("pose digest differs")
    return dict(row)


def _window_from_trace(window: Mapping[str, object]) -> Mapping[str, object]:
    registry = _mapping(window["registry"], "trace registry")
    events = list(_sequence(window["input_events"], "trace events"))
    poses = list(_sequence(window["input_poses"], "trace poses"))
    query_ids = [row["event_id"] for row in events if row["is_query"]]  # type: ignore[index]
    warmup_ids = [row["event_id"] for row in events if not row["is_query"]]  # type: ignore[index]
    event_ids = [row["event_id"] for row in events]  # type: ignore[index]
    pose_ids = [row["pose_id"] for row in poses]  # type: ignore[index]
    negative = [row["pose_id"] for row in poses if row["commit_cycle"] < 0]  # type: ignore[index]
    body = {
        "window_id": registry["window_id"],
        "neutral_bounds_sha256": canonical_sha256(registry),
        "events": events,
        "events_sha256": canonical_sha256(events),
        "ordered_source_event_ids_sha256": canonical_sha256(event_ids),
        "ordered_warmup_event_ids_sha256": canonical_sha256(warmup_ids),
        "ordered_query_event_ids_sha256": canonical_sha256(query_ids),
        "source_event_count": len(events),
        "warmup_event_count": len(warmup_ids),
        "query_event_count": len(query_ids),
        "poses": poses,
        "poses_sha256": canonical_sha256(poses),
        "ordered_pose_ids_sha256": canonical_sha256(pose_ids),
        "negative_commit_pose_ids_sha256": canonical_sha256(negative),
        "pose_input_count": len(poses),
        "neutral_inputs_sha256": canonical_sha256({"events": events, "poses": poses}),
        "current_cav_window_trace_sha256": window["window_sha256"],
    }
    return body


def build_stage3_execution_input(
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
    *,
    source_events_authority: Mapping[str, object],
    cycle_profile: object = _LOCKED_CYCLE_PROFILE,
    cycle_runner: _trace.CycleRunner = _LOCKED_CYCLE_RUNNER,
    repo_root: Optional[Path] = None,
) -> Mapping[str, object]:
    """Build the only Stage-3 authority that may be exposed to a candidate."""

    if cycle_profile is not _LOCKED_CYCLE_PROFILE:
        raise Stage3ExecutionAuthorityError("Stage3 execution profile is not the locked profile")
    if cycle_runner is not _LOCKED_CYCLE_RUNNER:
        raise Stage3ExecutionAuthorityError("Stage3 execution runner is not the repository runner")
    source = _validate_source(source_events_authority)
    profile = _profile_mapping(cycle_profile)
    replay_authority = _validate_logical_replay_authority(
        logical_replay_authority()
    )
    root = _repo_root(repo_root)
    trace = _trace.build_current_cav_trace(
        registry, event_streams, pose_streams,
        cycle_profile=cycle_profile,
        cycle_runner=cycle_runner,
        cycle_runner_owns_profile=True,
    )
    trace_mapping = trace.to_mapping()
    if json.loads(trace.profile.profile_mapping_json) != profile:
        raise Stage3ExecutionAuthorityError("trace logical-ingress profile differs")
    registry_rows = [dict(window["registry"]) for window in trace_mapping["windows"]]
    windows = [_window_from_trace(window) for window in trace_mapping["windows"]]
    event_occurrences = [
        {"window_id": window["window_id"], "event_ids": [event["event_id"] for event in window["events"]]}
        for window in windows
    ]
    query_ids = [event["event_id"] for window in windows for event in window["events"] if event["is_query"]]
    all_ids = [event["event_id"] for window in windows for event in window["events"]]
    dependencies = list(_build_dependency_manifest(root))
    body = {
        "schema": EXECUTION_INPUT_SCHEMA,
        "source_events_authority": source,
        "timing_authority": _timing_authority(),
        "logical_ingress_profile": profile,
        "logical_ingress_profile_sha256": canonical_sha256(profile),
        "logical_cycle_replay_authority": replay_authority,
        "logical_cycle_replay_authority_sha256": replay_authority[
            "authority_sha256"
        ],
        "consumer_dependency_manifest": dependencies,
        "consumer_dependency_aggregate_sha256": canonical_sha256(dependencies),
        "neutral_registry": registry_rows,
        "neutral_registry_sha256": canonical_sha256(registry_rows),
        "ordered_window_ids_sha256": canonical_sha256([row["window_id"] for row in registry_rows]),
        "ordered_event_occurrences_sha256": canonical_sha256(event_occurrences),
        "ordered_query_event_ids_sha256": canonical_sha256(query_ids),
        "windows": windows,
        "windows_sha256": canonical_sha256(windows),
        "neutral_input_sha256": trace.neutral_input_sha256,
        "score_free_current_cav_trace": trace_mapping,
        "score_free_current_cav_trace_sha256": trace.aggregate_sha256,
        "window_count": len(windows),
        "source_event_occurrence_count": len(all_ids),
        "unique_source_event_count": len(set(all_ids)),
        "warmup_event_occurrence_count": len(all_ids) - len(query_ids),
        "query_event_count": len(query_ids),
        "pose_occurrence_count": sum(window["pose_input_count"] for window in windows),
    }
    result = _sealed(body)
    verify_stage3_execution_input(result, repo_root=root)
    return result


def _validate_trace(
    value: object,
    windows: Sequence[Mapping[str, object]],
    logical_profile: Mapping[str, object],
) -> Mapping[str, object]:
    trace = _mapping(value, "score-free current-CAV trace")
    expected_top = frozenset((
        "schema", "profile", "profile_sha256", "neutral_input_sha256",
        "baseline_schema", "baseline_decisions_sha256", "windows", "aggregate_sha256",
    ))
    _exact(trace, expected_top, "score-free current-CAV trace")
    if trace.get("schema") != _trace.TRACE_SCHEMA or trace.get("baseline_schema") != _trace.BASELINE_SCHEMA:
        raise Stage3ExecutionAuthorityError("score-free current-CAV trace schema differs")
    profile = _exact(
        trace.get("profile"),
        frozenset((
            "schema", "profile_id", "profile_mapping_json",
            "profile_mapping_sha256", "semantic_contract_sha256", "semantics",
        )),
        "current-CAV runner profile",
    )
    try:
        profile_snapshot = _trace.CycleRunnerProfile(
            profile["profile_id"],
            profile["profile_mapping_json"],
            profile["profile_mapping_sha256"],
            profile["semantic_contract_sha256"],
        )
        decoded_profile = json.loads(profile["profile_mapping_json"])
    except (TypeError, ValueError, _trace.CurrentCAVTraceError) as exc:
        raise Stage3ExecutionAuthorityError("current-CAV runner profile differs") from exc
    if profile_snapshot.to_mapping() != profile or decoded_profile != logical_profile:
        raise Stage3ExecutionAuthorityError("trace logical-ingress profile binding differs")
    if _sha(trace.get("profile_sha256"), "trace profile digest") != canonical_sha256(profile):
        raise Stage3ExecutionAuthorityError("trace profile seal differs")
    _sha(trace.get("neutral_input_sha256"), "trace neutral input digest")
    _sha(trace.get("baseline_decisions_sha256"), "trace decision digest")
    trace_windows = _sequence(trace.get("windows"), "trace windows")
    if len(trace_windows) != len(windows):
        raise Stage3ExecutionAuthorityError("trace window cardinality differs")
    decision_windows = []
    for trace_raw, execution in zip(trace_windows, windows):
        tw = _exact(trace_raw, frozenset(("registry", "input_events", "input_poses", "simulation", "window_sha256")), "trace window")
        if _verify_seal(tw, "window_sha256", "trace window") != execution["current_cav_window_trace_sha256"]:
            raise Stage3ExecutionAuthorityError("execution-to-trace window binding differs")
        if tw["registry"]["window_id"] != execution["window_id"] or tw["input_events"] != execution["events"] or tw["input_poses"] != execution["poses"]:  # type: ignore[index]
            raise Stage3ExecutionAuthorityError("execution-to-trace neutral inputs differ")
        simulation = _exact(tw["simulation"], frozenset(("records", "decision_records_sha256", "synthetic_test_mode", "all_event_pose_indices_verified")), "trace simulation")
        if simulation["synthetic_test_mode"] is not False or simulation["all_event_pose_indices_verified"] is not True:
            raise Stage3ExecutionAuthorityError("trace authentication status differs")
        records = _sequence(simulation["records"], "trace decision records")
        if len(records) != execution["source_event_count"] or canonical_sha256(records) != simulation["decision_records_sha256"]:
            raise Stage3ExecutionAuthorityError("trace decision record seal differs")
        checked_records = []
        decision_fields = frozenset((
            "window_id", "event_id", "event_timestamp_ns", "occurrence_cycle",
            "occurrence_pose_ids", "occurrence_pose_timestamps_ns",
            "occurrence_pose_commit_cycles", "occurrence_pose_sha256", "used_pose_ids",
            "used_pose_timestamps_ns", "used_pose_commit_cycles", "used_pose_sha256",
            "disposition", "disposition_reason", "decision_sha256",
        ))
        pose_objects = tuple(_trace.TracePoseInput(
            pose["pose_id"], pose["timestamp_ns"], pose["commit_cycle"],
            tuple(pose["quaternion_xyzw"]), pose["pose_sha256"],
            pose["value_valid"], pose["arithmetic_valid"],
        ) for pose in execution["poses"])
        for record, event in zip(records, execution["events"]):
            decision = _exact(record, decision_fields, "trace decision")
            _verify_seal(decision, "decision_sha256", "trace decision")
            if decision["window_id"] != execution["window_id"] or decision["event_id"] != event["event_id"] or decision["event_timestamp_ns"] != event["timestamp_ns"]:  # type: ignore[index]
                raise Stage3ExecutionAuthorityError("trace decision identity differs")
            event_object = _trace.TraceEventInput(
                event["event_id"], event["timestamp_ns"], event["polarity"],
                event["is_query"], tuple(event["sensor_ray"]),
                event["causal_pose_source_index"], event["event_content_sha256"],
                event["transform_guard_valid"],
            )
            edge = _trace.timestamp_to_cycle(
                event_object.timestamp_ns,
                tw["registry"]["warmup_start_ns_inclusive"],  # type: ignore[index]
            )
            visible = tuple(
                pose for pose in pose_objects
                if pose.commit_cycle < edge
                and pose.timestamp_ns <= event_object.timestamp_ns
            )
            occurrence = visible[-2:]
            if not occurrence or event_object.causal_pose_source_index != occurrence[-1].pose_id:
                raise Stage3ExecutionAuthorityError("trace causal pose source differs")
            selected, disposition, reason = _trace._expected_route(  # type: ignore[attr-defined]
                event_object, occurrence
            )
            def evidence(items):
                return (
                    tuple(item.pose_id for item in items),
                    tuple(item.timestamp_ns for item in items),
                    tuple(item.commit_cycle for item in items),
                    tuple(item.pose_sha256 for item in items),
                )
            occurrence_evidence = evidence(occurrence)
            selected_evidence = evidence(selected)
            expected_decision = _trace.CurrentCAVDecision(
                execution["window_id"], event_object.event_id,
                event_object.timestamp_ns, edge,
                occurrence_evidence[0], occurrence_evidence[1],
                occurrence_evidence[2], occurrence_evidence[3],
                selected_evidence[0], selected_evidence[1],
                selected_evidence[2], selected_evidence[3], disposition, reason,
            ).to_mapping()
            if decision != expected_decision:
                raise Stage3ExecutionAuthorityError("trace causal decision differs")
            checked_records.append(decision)
        decision_windows.append({"window_id": execution["window_id"], "decisions": checked_records})
    expected_baseline = canonical_sha256({"schema": _trace.BASELINE_SCHEMA, "windows": decision_windows})
    if trace["baseline_decisions_sha256"] != expected_baseline:
        raise Stage3ExecutionAuthorityError("trace baseline decision aggregate differs")
    neutral_mapping = {
        "schema": _trace.NEUTRAL_INPUT_SCHEMA,
        "registry": [window["registry"] for window in trace_windows],  # type: ignore[index]
        "windows": [{
            "window_id": window["registry"]["window_id"],  # type: ignore[index]
            "events": window["input_events"],  # type: ignore[index]
            "poses": window["input_poses"],  # type: ignore[index]
        } for window in trace_windows],
    }
    if trace["neutral_input_sha256"] != canonical_sha256(neutral_mapping):
        raise Stage3ExecutionAuthorityError("trace neutral input digest differs")
    _verify_seal(trace, "aggregate_sha256", "score-free current-CAV trace")
    return trace


def verify_stage3_execution_input(
    value: object, *, expected_aggregate_sha256: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> str:
    """Verify all closed execution fields without opening any label artifact."""

    row = _exact(value, _EXECUTION_FIELDS, "Stage3ExecutionInput")
    if row.get("schema") != EXECUTION_INPUT_SCHEMA:
        raise Stage3ExecutionAuthorityError("execution schema differs")
    _validate_source(row.get("source_events_authority"))
    _validate_timing(row.get("timing_authority"))
    profile = _validate_profile(row.get("logical_ingress_profile"))
    if row.get("logical_ingress_profile_sha256") != canonical_sha256(profile):
        raise Stage3ExecutionAuthorityError("logical-ingress profile digest differs")
    replay_authority = _validate_logical_replay_authority(
        row.get("logical_cycle_replay_authority")
    )
    if row.get("logical_cycle_replay_authority_sha256") != replay_authority[
        "authority_sha256"
    ]:
        raise Stage3ExecutionAuthorityError(
            "logical cycle replay authority digest differs"
        )
    dependencies = _verify_dependency_manifest(row.get("consumer_dependency_manifest"), _repo_root(repo_root))
    if row.get("consumer_dependency_aggregate_sha256") != canonical_sha256(dependencies):
        raise Stage3ExecutionAuthorityError("dependency aggregate differs")
    registry_raw = _sequence(row.get("neutral_registry"), "neutral registry")
    registry = [_validate_registry(item) for item in registry_raw]
    ids = [item["window_id"] for item in registry]
    if len(set(ids)) != len(ids):
        raise Stage3ExecutionAuthorityError("neutral window IDs repeat")
    for left, right in zip(registry, registry[1:]):
        if (left["warmup_start_ns_inclusive"] >= right["warmup_start_ns_inclusive"]
                or left["query_start_ns_inclusive"] >= right["query_start_ns_inclusive"]
                or left["query_end_ns_exclusive"] > right["query_start_ns_inclusive"]):
            raise Stage3ExecutionAuthorityError("query windows overlap or move backwards")
    if row.get("neutral_registry_sha256") != canonical_sha256(registry) or row.get("ordered_window_ids_sha256") != canonical_sha256(ids):
        raise Stage3ExecutionAuthorityError("neutral registry seal differs")
    windows_raw = _sequence(row.get("windows"), "execution windows")
    if len(windows_raw) != len(registry):
        raise Stage3ExecutionAuthorityError("execution window cardinality differs")
    windows = []
    all_ids = []
    query_ids = []
    query_seen = set()
    event_occurrences = []
    warmup_count = 0
    pose_count = 0
    for raw, bounds in zip(windows_raw, registry):
        window = _exact(raw, _WINDOW_FIELDS, "execution window")
        if window["window_id"] != bounds["window_id"] or window["neutral_bounds_sha256"] != canonical_sha256(bounds):
            raise Stage3ExecutionAuthorityError("execution window bounds binding differs")
        events = [_validate_event(item, bounds) for item in _sequence(window["events"], "window events")]
        poses = [_validate_pose(item, bounds) for item in _sequence(window["poses"], "window poses")]
        event_ids = [event["event_id"] for event in events]
        event_timestamps = [event["timestamp_ns"] for event in events]
        event_cycles = [
            _trace.timestamp_to_cycle(
                timestamp, bounds["warmup_start_ns_inclusive"]
            )
            for timestamp in event_timestamps
        ]
        if len(set(event_ids)) != len(event_ids):
            raise Stage3ExecutionAuthorityError(
                "window contains duplicate event IDs"
            )
        if any(
            right < left
            for left, right in zip(event_timestamps, event_timestamps[1:])
        ):
            raise Stage3ExecutionAuthorityError(
                "window event timestamps move backwards"
            )
        if any(
            right < left
            for left, right in zip(event_cycles, event_cycles[1:])
        ):
            raise Stage3ExecutionAuthorityError(
                "window event occurrence cycles move backwards"
            )
        pose_ids = [pose["pose_id"] for pose in poses]
        pose_timestamps = [pose["timestamp_ns"] for pose in poses]
        if (any(right <= left for left, right in zip(pose_ids, pose_ids[1:]))
                or any(right <= left for left, right in zip(pose_timestamps, pose_timestamps[1:]))):
            raise Stage3ExecutionAuthorityError("window poses are not source ordered")
        warmup_ids = [event["event_id"] for event in events if not event["is_query"]]
        window_query = [event["event_id"] for event in events if event["is_query"]]
        if not window_query or any(item in query_seen for item in window_query):
            raise Stage3ExecutionAuthorityError("query event IDs are absent or repeated")
        query_seen.update(window_query)
        negative = [pose["pose_id"] for pose in poses if pose["commit_cycle"] < 0]
        expected_values = {
            "events_sha256": canonical_sha256(events),
            "ordered_source_event_ids_sha256": canonical_sha256(event_ids),
            "ordered_warmup_event_ids_sha256": canonical_sha256(warmup_ids),
            "ordered_query_event_ids_sha256": canonical_sha256(window_query),
            "source_event_count": len(events), "warmup_event_count": len(warmup_ids),
            "query_event_count": len(window_query), "poses_sha256": canonical_sha256(poses),
            "ordered_pose_ids_sha256": canonical_sha256(pose_ids),
            "negative_commit_pose_ids_sha256": canonical_sha256(negative),
            "pose_input_count": len(poses),
            "neutral_inputs_sha256": canonical_sha256({"events": events, "poses": poses}),
        }
        if any(window[key] != expected for key, expected in expected_values.items()):
            raise Stage3ExecutionAuthorityError("execution window field or seal differs")
        _sha(window["current_cav_window_trace_sha256"], "current-CAV window trace digest")
        windows.append(dict(window))
        all_ids.extend(event_ids)
        query_ids.extend(window_query)
        event_occurrences.append({"window_id": window["window_id"], "event_ids": event_ids})
        warmup_count += len(warmup_ids)
        pose_count += len(poses)
    if row.get("windows_sha256") != canonical_sha256(windows):
        raise Stage3ExecutionAuthorityError("execution windows aggregate differs")
    if row.get("ordered_event_occurrences_sha256") != canonical_sha256(event_occurrences):
        raise Stage3ExecutionAuthorityError("overlap-safe event occurrence seal differs")
    if row.get("ordered_query_event_ids_sha256") != canonical_sha256(query_ids):
        raise Stage3ExecutionAuthorityError("ordered query-event seal differs")
    trace = _validate_trace(
        row.get("score_free_current_cav_trace"), windows, profile
    )
    if row.get("score_free_current_cav_trace_sha256") != trace["aggregate_sha256"]:
        raise Stage3ExecutionAuthorityError("current-CAV trace binding differs")
    if row.get("neutral_input_sha256") != trace["neutral_input_sha256"]:
        raise Stage3ExecutionAuthorityError("neutral input binding differs")
    expected_counts = {
        "window_count": len(windows), "source_event_occurrence_count": len(all_ids),
        "unique_source_event_count": len(set(all_ids)),
        "warmup_event_occurrence_count": warmup_count,
        "query_event_count": len(query_ids), "pose_occurrence_count": pose_count,
    }
    if any(row.get(key) != count for key, count in expected_counts.items()):
        raise Stage3ExecutionAuthorityError("execution count differs")
    aggregate = _verify_seal(row, "aggregate_sha256", "Stage3ExecutionInput")
    if expected_aggregate_sha256 is not None and aggregate != _sha(expected_aggregate_sha256, "expected execution digest"):
        raise Stage3ExecutionAuthorityError("execution authority pin differs")
    return aggregate


def _selector_authority(value: object) -> Mapping[str, object]:
    row = _exact(value, _SELECTOR_AUTHORITY_FIELDS, "selector authority")
    return {key: _sha(row[key], "selector authority digest") for key in sorted(row)}


def _label(value: object, window_id: str) -> Mapping[str, object]:
    row = _mapping(value, "selector label")
    input_fields = _LABEL_FIELDS - {"label_sha256"}
    if frozenset(row) != input_fields and frozenset(row) != _LABEL_FIELDS:
        raise Stage3ExecutionAuthorityError("selector label field schema differs")
    if row.get("window_id") != window_id:
        raise Stage3ExecutionAuthorityError("selector label window differs")
    if row.get("axis") not in ("X", "Y", "Z") or row.get("sign") not in ("NEGATIVE", "POSITIVE") or row.get("motion_bin") not in ("LOW", "MID", "HIGH"):
        raise Stage3ExecutionAuthorityError("selector label taxonomy differs")
    vector = _sequence(row.get("rotation_vector_rad"), "rotation vector")
    if len(vector) != 3:
        raise Stage3ExecutionAuthorityError("rotation vector cardinality differs")
    body = {
        "window_id": window_id, "axis": row["axis"], "sign": row["sign"],
        "motion_bin": row["motion_bin"],
        "rotation_vector_rad": [_finite(item, "rotation vector component") for item in vector],
        "purity": _finite(row.get("purity"), "purity"),
        "motion_proxy": _finite(row.get("motion_proxy"), "motion proxy"),
        "rank_sha256": _sha(row.get("rank_sha256"), "selector rank digest"),
    }
    if not 0.0 <= body["purity"] <= 1.0:
        raise Stage3ExecutionAuthorityError("purity lies outside [0,1]")
    sealed = _sealed(body, "label_sha256")
    if "label_sha256" in row and row["label_sha256"] != sealed["label_sha256"]:
        raise Stage3ExecutionAuthorityError("selector label seal differs")
    return sealed


def build_stage3_label_authority(
    execution_input: object, labels: Mapping[str, object], *,
    selector_authority: Mapping[str, object], repo_root: Optional[Path] = None,
) -> Mapping[str, object]:
    """Build scorer-only label authority, separately from candidate execution."""

    execution = _mapping(execution_input, "execution input")
    verify_stage3_execution_input(execution, repo_root=repo_root)
    windows = execution["windows"]
    ids = [window["window_id"] for window in windows]  # type: ignore[index]
    if set(labels) != set(ids):
        raise Stage3ExecutionAuthorityError("selector label window set differs")
    checked = [_label(labels[window_id], window_id) for window_id in ids]
    joins = [{
        "window_id": window["window_id"],
        "ordered_query_event_ids_sha256": window["ordered_query_event_ids_sha256"],
        "label_sha256": label["label_sha256"],
    } for window, label in zip(windows, checked)]
    body = {
        "schema": LABEL_AUTHORITY_SCHEMA,
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "execution_neutral_input_sha256": execution["neutral_input_sha256"],
        "ordered_window_ids_sha256": execution["ordered_window_ids_sha256"],
        "ordered_query_event_ids_sha256": execution["ordered_query_event_ids_sha256"],
        "window_count": execution["window_count"],
        "selector_authority": _selector_authority(selector_authority),
        "labels": checked,
        "selector_labels_sidecar_sha256": canonical_sha256(checked),
        "joins": joins, "joins_sha256": canonical_sha256(joins),
    }
    result = _sealed(body)
    verify_stage3_label_authority(result, execution, repo_root=repo_root)
    return result


def verify_stage3_label_authority(
    value: object, execution_input: object, *,
    expected_aggregate_sha256: Optional[str] = None,
    expected_labels_sidecar_sha256: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> str:
    row = _exact(value, _LABEL_AUTHORITY_FIELDS, "Stage3LabelAuthority")
    if row.get("schema") != LABEL_AUTHORITY_SCHEMA:
        raise Stage3ExecutionAuthorityError("label authority schema differs")
    execution = _mapping(execution_input, "execution input")
    verify_stage3_execution_input(execution, repo_root=repo_root)
    bindings = {
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "execution_neutral_input_sha256": execution["neutral_input_sha256"],
        "ordered_window_ids_sha256": execution["ordered_window_ids_sha256"],
        "ordered_query_event_ids_sha256": execution["ordered_query_event_ids_sha256"],
        "window_count": execution["window_count"],
    }
    if any(row.get(key) != expected for key, expected in bindings.items()):
        raise Stage3ExecutionAuthorityError("label-to-execution binding differs")
    _selector_authority(row.get("selector_authority"))
    labels_raw = _sequence(row.get("labels"), "selector labels")
    windows = execution["windows"]
    if len(labels_raw) != len(windows):
        raise Stage3ExecutionAuthorityError("selector label cardinality differs")
    labels = [_label(label, window["window_id"]) for label, window in zip(labels_raw, windows)]
    joins = [{
        "window_id": window["window_id"],
        "ordered_query_event_ids_sha256": window["ordered_query_event_ids_sha256"],
        "label_sha256": label["label_sha256"],
    } for window, label in zip(windows, labels)]
    sidecar = canonical_sha256(labels)
    if row.get("selector_labels_sidecar_sha256") != sidecar or row.get("joins") != joins or row.get("joins_sha256") != canonical_sha256(joins):
        raise Stage3ExecutionAuthorityError("label sidecar or join seal differs")
    if expected_labels_sidecar_sha256 is not None and sidecar != _sha(expected_labels_sidecar_sha256, "expected label sidecar digest"):
        raise Stage3ExecutionAuthorityError("label sidecar pin differs")
    aggregate = _verify_seal(row, "aggregate_sha256", "Stage3LabelAuthority")
    if expected_aggregate_sha256 is not None and aggregate != _sha(expected_aggregate_sha256, "expected label authority digest"):
        raise Stage3ExecutionAuthorityError("label authority pin differs")
    return aggregate


def _candidate_output_bindings(candidate_output: object, execution: Mapping[str, object]) -> Mapping[str, object]:
    _reject_forbidden_candidate_fields(candidate_output)
    output = _mapping(candidate_output, "candidate output")
    required = frozenset(("schema", "candidate_id", "adapter_aggregate_sha256", "neutral_input_sha256", "windows", "aggregate_sha256"))
    if not required.issubset(output):
        raise Stage3ExecutionAuthorityError("candidate output seal envelope differs")
    schema = _text(output.get("schema"), "candidate output schema")
    candidate_id = _candidate_identifier(output.get("candidate_id"), "candidate ID")
    if output.get("adapter_aggregate_sha256") != execution["aggregate_sha256"] or output.get("neutral_input_sha256") != execution["neutral_input_sha256"]:
        raise Stage3ExecutionAuthorityError("candidate output execution binding differs")
    output_aggregate = _verify_seal(output, "aggregate_sha256", "candidate output")
    output_windows = _sequence(output.get("windows"), "candidate output windows")
    execution_windows = execution["windows"]
    if len(output_windows) != len(execution_windows):
        raise Stage3ExecutionAuthorityError("candidate output window cardinality differs")
    occurrences = []
    joined = []
    for output_raw, expected in zip(output_windows, execution_windows):
        window = _mapping(output_raw, "candidate output window")
        if window.get("window_id") != expected["window_id"]:
            raise Stage3ExecutionAuthorityError("candidate output window order differs")
        events = _sequence(window.get("events"), "candidate output events")
        ids = []
        for event in events:
            event_row = _mapping(event, "candidate output event")
            ids.append(_integer(event_row.get("event_id"), "candidate output event ID"))
        expected_ids = [event["event_id"] for event in expected["events"]]
        if ids != expected_ids:
            raise Stage3ExecutionAuthorityError("candidate output event conservation differs")
        if "events_sha256" not in window or window["events_sha256"] != canonical_sha256(events):
            raise Stage3ExecutionAuthorityError("candidate output event seal differs")
        occurrences.append({"window_id": window["window_id"], "event_ids": ids})
        joined.append({
            "window_id": window["window_id"],
            "ordered_query_event_ids_sha256": expected["ordered_query_event_ids_sha256"],
            "candidate_output_events_sha256": window["events_sha256"],
        })
    occurrence_sha = canonical_sha256(occurrences)
    if occurrence_sha != execution["ordered_event_occurrences_sha256"]:
        raise Stage3ExecutionAuthorityError("candidate output occurrence binding differs")
    return {
        "schema": schema, "candidate_id": candidate_id, "aggregate_sha256": output_aggregate,
        "ordered_event_occurrences_sha256": occurrence_sha, "joined": joined,
    }


def build_stage3_scoring_join_receipt(
    execution_input: object, candidate_output: object, label_authority: object, *,
    repo_root: Optional[Path] = None,
) -> Mapping[str, object]:
    """Cross-bind labels only after verifying the candidate output aggregate."""

    execution = _mapping(execution_input, "execution input")
    verify_stage3_execution_input(execution, repo_root=repo_root)
    candidate = _candidate_output_bindings(candidate_output, execution)
    labels = _mapping(label_authority, "label authority")
    verify_stage3_label_authority(labels, execution, repo_root=repo_root)
    label_joins = labels["joins"]
    joined = []
    for candidate_row, label_row in zip(candidate["joined"], label_joins):
        if candidate_row["window_id"] != label_row["window_id"] or candidate_row["ordered_query_event_ids_sha256"] != label_row["ordered_query_event_ids_sha256"]:
            raise Stage3ExecutionAuthorityError("post-output window join differs")
        joined.append(dict(candidate_row, label_sha256=label_row["label_sha256"]))
    body = {
        "schema": SCORING_JOIN_SCHEMA,
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "neutral_input_sha256": execution["neutral_input_sha256"],
        "candidate_output_schema": candidate["schema"],
        "candidate_id": candidate["candidate_id"],
        "candidate_output_aggregate_sha256": candidate["aggregate_sha256"],
        "candidate_output_ordered_event_occurrences_sha256": candidate["ordered_event_occurrences_sha256"],
        "label_authority_aggregate_sha256": labels["aggregate_sha256"],
        "selector_labels_sidecar_sha256": labels["selector_labels_sidecar_sha256"],
        "ordered_window_ids_sha256": execution["ordered_window_ids_sha256"],
        "ordered_query_event_ids_sha256": execution["ordered_query_event_ids_sha256"],
        "window_count": execution["window_count"], "query_event_count": execution["query_event_count"],
        "joined_windows_sha256": canonical_sha256(joined),
    }
    result = _sealed(body)
    verify_stage3_scoring_join_receipt(result, execution, candidate_output, labels, repo_root=repo_root)
    return result


def verify_stage3_scoring_join_receipt(
    value: object, execution_input: object, candidate_output: object,
    label_authority: object, *, expected_aggregate_sha256: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> str:
    row = _exact(value, _JOIN_FIELDS, "Stage3ScoringJoinReceipt")
    if row.get("schema") != SCORING_JOIN_SCHEMA:
        raise Stage3ExecutionAuthorityError("scoring join schema differs")
    execution = _mapping(execution_input, "execution input")
    verify_stage3_execution_input(execution, repo_root=repo_root)
    candidate = _candidate_output_bindings(candidate_output, execution)
    labels = _mapping(label_authority, "label authority")
    verify_stage3_label_authority(labels, execution, repo_root=repo_root)
    joined = []
    for candidate_row, label_row in zip(candidate["joined"], labels["joins"]):
        joined.append(dict(candidate_row, label_sha256=label_row["label_sha256"]))
    expected = {
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "neutral_input_sha256": execution["neutral_input_sha256"],
        "candidate_output_schema": candidate["schema"], "candidate_id": candidate["candidate_id"],
        "candidate_output_aggregate_sha256": candidate["aggregate_sha256"],
        "candidate_output_ordered_event_occurrences_sha256": candidate["ordered_event_occurrences_sha256"],
        "label_authority_aggregate_sha256": labels["aggregate_sha256"],
        "selector_labels_sidecar_sha256": labels["selector_labels_sidecar_sha256"],
        "ordered_window_ids_sha256": execution["ordered_window_ids_sha256"],
        "ordered_query_event_ids_sha256": execution["ordered_query_event_ids_sha256"],
        "window_count": execution["window_count"], "query_event_count": execution["query_event_count"],
        "joined_windows_sha256": canonical_sha256(joined),
    }
    if any(row.get(key) != expected_value for key, expected_value in expected.items()):
        raise Stage3ExecutionAuthorityError("scoring join cross-binding differs")
    aggregate = _verify_seal(row, "aggregate_sha256", "Stage3ScoringJoinReceipt")
    if expected_aggregate_sha256 is not None and aggregate != _sha(expected_aggregate_sha256, "expected scoring join digest"):
        raise Stage3ExecutionAuthorityError("scoring join pin differs")
    return aggregate


__all__ = (
    "CONSUMER_DEPENDENCY_PATHS", "EXECUTION_INPUT_SCHEMA", "LABEL_AUTHORITY_SCHEMA",
    "LOGICAL_INGRESS_SCOPE", "PRE_ROLL_NS", "SCORING_JOIN_SCHEMA",
    "Stage3ExecutionAuthorityError", "build_stage3_execution_input",
    "build_stage3_label_authority", "build_stage3_scoring_join_receipt",
    "verify_stage3_execution_input", "verify_stage3_label_authority",
    "verify_stage3_scoring_join_receipt",
)
