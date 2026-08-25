"""Fail-closed two-stream Cluster2-to-CAV bridge contract.

Source sidecars and transport outcomes remain separate until an exact ID join.
No scorer, evaluator, RTL implementation, or guessed Ganghee digest is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .transport_time import (
    MAX_NATIVE_CYCLE,
    MAX_SERIALIZED_TIMESTAMP_NS,
    TRANSPORT_TIME_SEMANTICS,
    DualTimeEvent,
    TransportTimeValidationError,
    build_dual_time_event,
)


SOURCE_EVENT_SCHEMA = "redred.cluster2_cav_bridge.source_event/v1"
TRANSPORT_OUTCOME_SCHEMA = "redred.cluster2_cav_bridge.transport_outcome/v1"
TRANSPORT_OUTCOME_POLARITY_SCHEMA = (
    "redred.cluster2_cav_bridge.transport_outcome/v2"
)
MANIFEST_SCHEMA = "redred.cluster2_cav_bridge.manifest/v2"
PROJECTION_SCHEMA = "redred.cluster2_cav_bridge.four_view_projection/v2"
CANONICAL_JSONL_FORMAT = "canonical-jsonl/v1"
TIMESTAMP_TO_OCCURRENCE_RULE = (
    "ceil_div((timestamp_ns-aer_cycle_zero_timestamp_ns)*1000,aer_clock_period_ps)"
)
VIEW_ORDER = ("RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET")
GANGHEE_AUTHORITY_ROLE = "ganghee_cluster2_top"
OBSERVATIONAL_JOIN_LABEL = "SOURCE_EVENT_OBSERVATIONAL_JOIN_NOT_AER_PAYLOAD"

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_STREAM_RECORDS = 1_000_000
MAX_LINE_BYTES = 64 * 1024

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SOURCE_FIELDS = frozenset((
    "schema", "event_id", "ordinal", "timestamp_ns", "source_index",
    "polarity", "window_id", "is_query", "sensor_ray",
    "causal_pose_source_index", "transform_guard_valid", "event_content_sha256",
))
_OUTCOME_V1_FIELDS = frozenset((
    "schema", "event_id", "source_index", "occurrence_cycle", "outcome",
    "retire_cycle", "retire_native_lane", "retire_row", "retire_col",
))
_OUTCOME_V2_FIELDS = _OUTCOME_V1_FIELDS | frozenset(("retire_polarity",))
_MANIFEST_FIELDS = frozenset((
    "schema", "bridge_id", "source_events", "transport_outcomes",
    "mapping_authority", "source_registry_authority", "pose_stream_authority",
    "native_transport_receipt_authority", "rtl_authorities",
    "aer_clock_period_ps", "aer_cycle_zero_timestamp_ns",
    "timestamp_to_occurrence_cycle_rule", "projection",
))
_STREAM_FIELDS = frozenset(("format", "path", "sha256", "event_count"))
_ARTIFACT_FIELDS = frozenset(("path", "sha256"))
_RTL_FIELDS = frozenset(("role", "path", "sha256"))
_PROJECTION_FIELDS = frozenset(("schema", "views", "aer_projection_semantics"))


class BridgeValidationError(ValueError):
    """An input is ambiguous, unbound, non-canonical, or inconsistent."""


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise BridgeValidationError("%s must be a lowercase full SHA-256" % where)
    return value


def _identifier(value: object, where: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise BridgeValidationError("%s is not a valid identifier" % where)
    return value


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        raise BridgeValidationError("%s must be a non-negative integer" % where)
    return value


def _bounded_nonnegative_int(value: object, where: str, maximum: int) -> int:
    number = _nonnegative_int(value, where)
    if number > maximum:
        raise BridgeValidationError("%s exceeds maximum %d" % (where, maximum))
    return number


def _native_cycle(value: object, where: str) -> int:
    return _bounded_nonnegative_int(value, where, MAX_NATIVE_CYCLE)


def _serialized_timestamp_ns(value: object, where: str) -> int:
    return _bounded_nonnegative_int(value, where, MAX_SERIALIZED_TIMESTAMP_NS)


def _positive_int(value: object, where: str) -> int:
    number = _nonnegative_int(value, where)
    if number == 0:
        raise BridgeValidationError("%s must be positive" % where)
    return number


def _exact_mapping(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise BridgeValidationError("%s field schema differs" % where)
    return value


def _relative_path(value: object, where: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise BridgeValidationError("%s must be a normalized relative POSIX path" % where)
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise BridgeValidationError("%s must be a normalized relative POSIX path" % where)
    return value


def _validate_json_domain(value: object, where: str = "$") -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise BridgeValidationError("%s contains a non-finite number" % where)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_domain(item, "%s[%d]" % (where, index))
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise BridgeValidationError("%s contains a non-string key" % where)
            _validate_json_domain(item, "%s.%s" % (where, key))
        return
    raise BridgeValidationError("%s contains a non-JSON value" % where)


def canonical_json_bytes(value: object) -> bytes:
    """Return compact, sorted ASCII JSON with exactly one terminal LF."""
    _validate_json_domain(value)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise BridgeValidationError("value is not canonical-JSON serializable") from error
    return (encoded + "\n").encode("ascii")


def canonical_jsonl_bytes(rows: Iterable[object]) -> bytes:
    """Return non-empty canonical JSONL under fixed row and byte bounds."""
    payloads = []  # type: List[bytes]
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise BridgeValidationError("JSONL row %d must be an object" % index)
        line = canonical_json_bytes(row)
        if len(line) > MAX_LINE_BYTES:
            raise BridgeValidationError("JSONL line %d exceeds byte limit" % (index + 1))
        payloads.append(line)
        if len(payloads) > MAX_STREAM_RECORDS:
            raise BridgeValidationError("JSONL record count exceeds limit")
    if not payloads:
        raise BridgeValidationError("JSONL must contain at least one record")
    payload = b"".join(payloads)
    if len(payload) > MAX_STREAM_BYTES:
        raise BridgeValidationError("JSONL byte size exceeds limit")
    return payload


def _unique_object(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result = {}  # type: Dict[str, object]
    for key, value in pairs:
        if key in result:
            raise BridgeValidationError("duplicate JSON object key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BridgeValidationError("non-finite JSON number: %s" % value)


def _parse_json(raw: bytes, where: str) -> object:
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise BridgeValidationError("%s must be canonical ASCII JSON" % where) from error
    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except BridgeValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise BridgeValidationError("%s is not valid JSON" % where) from error
    _validate_json_domain(value, where)
    return value


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts:
        if path.is_absolute() and part == path.anchor:
            continue
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise BridgeValidationError("path contains a symlink: %s" % path)
        except FileNotFoundError:
            raise BridgeValidationError("path is unavailable: %s" % path)
        except OSError as error:
            raise BridgeValidationError("cannot inspect path: %s" % path) from error


def _read_regular_bytes(path: Path, limit: int, where: str) -> bytes:
    source = Path(path)
    _reject_symlink_components(source)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(source), flags)
    except OSError as error:
        raise BridgeValidationError("cannot open %s" % where) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BridgeValidationError("%s must be a regular file" % where)
        if before.st_size > limit:
            raise BridgeValidationError("%s exceeds byte limit" % where)
        chunks = []  # type: List[bytes]
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev, before.st_ino, before.st_mode, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev, after.st_ino, after.st_mode, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    if identity_before != identity_after or len(data) != before.st_size:
        raise BridgeValidationError("%s changed while being captured" % where)
    if len(data) > limit:
        raise BridgeValidationError("%s exceeds byte limit" % where)
    return data


def load_canonical_json(path: Path, expected_sha256: str) -> object:
    expected = _sha256(expected_sha256, "expected JSON authority")
    raw = _read_regular_bytes(Path(path), MAX_JSON_BYTES, "JSON input")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise BridgeValidationError("JSON input SHA-256 differs from caller authority")
    value = _parse_json(raw, "JSON input")
    if canonical_json_bytes(value) != raw:
        raise BridgeValidationError("JSON input is not byte-canonical")
    return value


def load_canonical_jsonl(path: Path, expected_sha256: str) -> Tuple[Mapping[str, object], ...]:
    expected = _sha256(expected_sha256, "expected JSONL authority")
    raw = _read_regular_bytes(Path(path), MAX_STREAM_BYTES, "JSONL input")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise BridgeValidationError("JSONL input SHA-256 differs from caller authority")
    if not raw or not raw.endswith(b"\n"):
        raise BridgeValidationError("JSONL input must be non-empty and LF-terminated")
    rows = []  # type: List[Mapping[str, object]]
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        if line in (b"\n", b"\r\n"):
            raise BridgeValidationError("JSONL input contains a blank line")
        if len(line) > MAX_LINE_BYTES:
            raise BridgeValidationError("JSONL line %d exceeds byte limit" % line_number)
        value = _parse_json(line, "JSONL line %d" % line_number)
        if not isinstance(value, Mapping):
            raise BridgeValidationError("JSONL line %d must be an object" % line_number)
        if canonical_json_bytes(value) != line:
            raise BridgeValidationError("JSONL line %d is not byte-canonical" % line_number)
        rows.append(value)
        if len(rows) > MAX_STREAM_RECORDS:
            raise BridgeValidationError("JSONL record count exceeds limit")
    return tuple(rows)


def canonical_event_content_sha256(
    event_id: int,
    timestamp_ns: int,
    polarity: int,
    is_query: bool,
    sensor_ray: Sequence[float],
    causal_pose_source_index: int,
    transform_guard_valid: bool,
) -> str:
    """Match the exact current-CAV neutral event digest preimage."""

    return hashlib.sha256(canonical_json_bytes({
        "event_id": event_id,
        "timestamp_ns": timestamp_ns,
        "polarity": polarity,
        "is_query": is_query,
        "sensor_ray": [float(component) for component in sensor_ray],
        "causal_pose_source_index": causal_pose_source_index,
        "transform_guard_valid": transform_guard_valid,
    })).hexdigest()


def validate_source_event(value: object) -> Mapping[str, object]:
    event = _exact_mapping(value, _SOURCE_FIELDS, "source event")
    if event["schema"] != SOURCE_EVENT_SCHEMA:
        raise BridgeValidationError("source event schema differs")
    _nonnegative_int(event["event_id"], "source event event_id")
    _nonnegative_int(event["ordinal"], "source event ordinal")
    _serialized_timestamp_ns(event["timestamp_ns"], "source event timestamp_ns")
    source_index = _nonnegative_int(event["source_index"], "source event source_index")
    if source_index > 15:
        raise BridgeValidationError("source event source_index must be in [0, 15]")
    if type(event["polarity"]) is not int or event["polarity"] not in (0, 1):
        raise BridgeValidationError("source event polarity must be 0 or 1")
    _identifier(event["window_id"], "source event window_id")
    if type(event["is_query"]) is not bool:
        raise BridgeValidationError("source event is_query must be boolean")
    ray = event["sensor_ray"]
    if not isinstance(ray, list) or len(ray) != 3:
        raise BridgeValidationError("source event sensor_ray must have three numbers")
    converted_ray = []  # type: List[float]
    for index, coordinate in enumerate(ray):
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise BridgeValidationError("source event sensor_ray[%d] is not numeric" % index)
        converted = float(coordinate)
        if not math.isfinite(converted):
            raise BridgeValidationError("source event sensor_ray[%d] is not finite" % index)
        converted_ray.append(converted)
    norm = math.sqrt(math.fsum(component * component for component in converted_ray))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise BridgeValidationError("source event sensor_ray must have unit norm")
    _nonnegative_int(
        event["causal_pose_source_index"], "source event causal_pose_source_index"
    )
    if type(event["transform_guard_valid"]) is not bool:
        raise BridgeValidationError("source event transform_guard_valid must be boolean")
    supplied_digest = _sha256(
        event["event_content_sha256"], "source event content authority"
    )
    expected_digest = canonical_event_content_sha256(
        event["event_id"],  # type: ignore[arg-type]
        event["timestamp_ns"],  # type: ignore[arg-type]
        event["polarity"],  # type: ignore[arg-type]
        event["is_query"],  # type: ignore[arg-type]
        ray,  # type: ignore[arg-type]
        event["causal_pose_source_index"],  # type: ignore[arg-type]
        event["transform_guard_valid"],  # type: ignore[arg-type]
    )
    if supplied_digest != expected_digest:
        raise BridgeValidationError("source event content digest differs")
    return event


def validate_transport_outcome(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BridgeValidationError("transport outcome field schema differs")
    schema = value.get("schema")
    if schema == TRANSPORT_OUTCOME_SCHEMA:
        outcome = _exact_mapping(value, _OUTCOME_V1_FIELDS, "transport outcome")
        carries_polarity = False
    elif schema == TRANSPORT_OUTCOME_POLARITY_SCHEMA:
        outcome = _exact_mapping(value, _OUTCOME_V2_FIELDS, "transport outcome")
        carries_polarity = True
    else:
        raise BridgeValidationError("transport outcome schema differs")
    _nonnegative_int(outcome["event_id"], "transport outcome event_id")
    source_index = _nonnegative_int(outcome["source_index"], "transport outcome source_index")
    if source_index > 15:
        raise BridgeValidationError("transport outcome source_index must be in [0, 15]")
    _native_cycle(outcome["occurrence_cycle"], "transport outcome occurrence_cycle")
    if outcome["outcome"] == "DELIVERED":
        _native_cycle(outcome["retire_cycle"], "transport outcome retire_cycle")
        lane = _nonnegative_int(
            outcome["retire_native_lane"],
            "transport outcome retire_native_lane",
        )
        if lane not in (0, 1):
            raise BridgeValidationError(
                "transport outcome retire_native_lane must be 0 or 1"
            )
        retire_col = _nonnegative_int(
            outcome["retire_col"], "transport outcome retire_col"
        )
        retire_row = _nonnegative_int(
            outcome["retire_row"], "transport outcome retire_row"
        )
        if retire_col > 3 or retire_row > 3:
            raise BridgeValidationError(
                "transport outcome retire row or column is out of range"
            )
        if source_index != retire_row * 4 + retire_col:
            raise BridgeValidationError(
                "transport outcome native row/column differs from source_index"
            )
        if (lane == 0 and retire_row not in (0, 1, 2)) or (
            lane == 1 and retire_row not in (0, 2, 3)
        ):
            raise BridgeValidationError(
                "transport outcome native lane cannot emit the source row"
            )
        if carries_polarity and (
            type(outcome["retire_polarity"]) is not int
            or outcome["retire_polarity"] not in (0, 1)
        ):
            raise BridgeValidationError(
                "transport outcome retire_polarity must be 0 or 1"
            )
    elif outcome["outcome"] == "OVERRUN":
        if (
            outcome["retire_cycle"] is not None
            or outcome["retire_native_lane"] is not None
            or outcome["retire_row"] is not None
            or outcome["retire_col"] is not None
            or (carries_polarity and outcome["retire_polarity"] is not None)
        ):
            raise BridgeValidationError("OVERRUN retire fields must all be null")
    else:
        raise BridgeValidationError("transport outcome must be DELIVERED or OVERRUN")
    return outcome


def _validate_stream(value: object, where: str) -> Mapping[str, object]:
    stream = _exact_mapping(value, _STREAM_FIELDS, where)
    if stream["format"] != CANONICAL_JSONL_FORMAT:
        raise BridgeValidationError("%s format differs" % where)
    _relative_path(stream["path"], where + " path")
    _sha256(stream["sha256"], where + " authority")
    count = _positive_int(stream["event_count"], where + " event_count")
    if count > MAX_STREAM_RECORDS:
        raise BridgeValidationError("%s event_count exceeds limit" % where)
    return stream


def validate_manifest(value: object) -> Mapping[str, object]:
    manifest = _exact_mapping(value, _MANIFEST_FIELDS, "manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise BridgeValidationError("manifest schema differs")
    _identifier(manifest["bridge_id"], "manifest bridge_id")
    source_stream = _validate_stream(manifest["source_events"], "manifest source_events")
    outcome_stream = _validate_stream(
        manifest["transport_outcomes"], "manifest transport_outcomes"
    )
    if source_stream["event_count"] != outcome_stream["event_count"]:
        raise BridgeValidationError("manifest stream event counts differ")
    opaque_authorities = (
        ("mapping_authority", "mapping authority"),
        ("source_registry_authority", "source registry authority"),
        ("pose_stream_authority", "pose stream authority"),
        ("native_transport_receipt_authority", "native transport receipt authority"),
    )
    authority_rows = []  # type: List[Mapping[str, object]]
    for field, label in opaque_authorities:
        authority = _exact_mapping(
            manifest[field], _ARTIFACT_FIELDS, "manifest " + label
        )
        _relative_path(authority["path"], "manifest " + label + " path")
        _sha256(authority["sha256"], "manifest " + label + " digest")
        authority_rows.append(authority)
    rtl_rows = manifest["rtl_authorities"]
    if not isinstance(rtl_rows, list) or not rtl_rows:
        raise BridgeValidationError("manifest rtl_authorities must be non-empty")
    roles = set()
    paths = {source_stream["path"], outcome_stream["path"]}
    paths.update(authority["path"] for authority in authority_rows)
    if len(paths) != 2 + len(authority_rows):
        raise BridgeValidationError("manifest streams and opaque authority paths must be unique")
    for index, raw_rtl in enumerate(rtl_rows):
        rtl = _exact_mapping(raw_rtl, _RTL_FIELDS, "manifest rtl_authorities[%d]" % index)
        role = _identifier(rtl["role"], "manifest RTL role")
        path = _relative_path(rtl["path"], "manifest RTL path")
        _sha256(rtl["sha256"], "manifest RTL digest")
        if role in roles or path in paths:
            raise BridgeValidationError("manifest RTL roles and all artifact paths must be unique")
        roles.add(role)
        paths.add(path)
    if GANGHEE_AUTHORITY_ROLE not in roles:
        raise BridgeValidationError(
            "manifest must bind caller-supplied ganghee_cluster2_top authority"
        )
    aer_clock_period_ps = _positive_int(
        manifest["aer_clock_period_ps"], "manifest aer_clock_period_ps"
    )
    if aer_clock_period_ps % 1000:
        raise BridgeValidationError(
            "manifest aer_clock_period_ps must be whole nanoseconds"
        )
    _serialized_timestamp_ns(
        manifest["aer_cycle_zero_timestamp_ns"],
        "manifest aer_cycle_zero_timestamp_ns",
    )
    if manifest["timestamp_to_occurrence_cycle_rule"] != TIMESTAMP_TO_OCCURRENCE_RULE:
        raise BridgeValidationError("manifest timestamp-to-occurrence rule differs")
    projection = _exact_mapping(
        manifest["projection"], _PROJECTION_FIELDS, "manifest projection"
    )
    if projection["schema"] != PROJECTION_SCHEMA:
        raise BridgeValidationError("manifest projection schema differs")
    if projection["views"] != list(VIEW_ORDER):
        raise BridgeValidationError("manifest projection view order differs")
    if projection["aer_projection_semantics"] != OBSERVATIONAL_JOIN_LABEL:
        raise BridgeValidationError("manifest AER projection semantics label differs")
    return manifest


def _member_path(root: Path, relative: object, where: str) -> Path:
    normalized = _relative_path(relative, where)
    return root / Path(*PurePosixPath(normalized).parts)


def _ceil_occurrence_cycle(timestamp_ns: int, zero_ns: int, period_ps: int) -> int:
    if timestamp_ns < zero_ns:
        raise BridgeValidationError("source timestamp precedes cycle zero")
    delta_ps = (timestamp_ns - zero_ns) * 1000
    cycle = (delta_ps + period_ps - 1) // period_ps
    return _native_cycle(cycle, "derived occurrence cycle")


def _physical_retire_timestamp_ns(
    retire_cycle: int, zero_ns: int, period_ps: int
) -> int:
    retire_delta_ps = retire_cycle * period_ps
    if retire_delta_ps % 1000:
        raise BridgeValidationError("CAV retire timestamp is not an integer nanosecond")
    return _serialized_timestamp_ns(
        zero_ns + retire_delta_ps // 1000,
        "physical_retire_timestamp_ns",
    )


@dataclass(frozen=True)
class JoinedEvent:
    source: Mapping[str, object]
    transport: Mapping[str, object]
    physical_retire_timestamp_ns: Optional[int]
    dual_time: Optional[DualTimeEvent]


def _join_streams(
    source_rows: Sequence[Mapping[str, object]],
    outcome_rows: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> Tuple[JoinedEvent, ...]:
    sources = []  # type: List[Mapping[str, object]]
    source_ids = set()
    previous_timestamp = None
    for index, raw_source in enumerate(source_rows):
        source = validate_source_event(raw_source)
        if source["ordinal"] != index:
            raise BridgeValidationError("source event ordinals must be contiguous")
        if source["event_id"] in source_ids:
            raise BridgeValidationError("source event IDs must be unique")
        source_ids.add(source["event_id"])
        timestamp = source["timestamp_ns"]
        assert isinstance(timestamp, int)
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise BridgeValidationError("source timestamps must be nondecreasing")
        previous_timestamp = timestamp
        sources.append(source)
    outcomes_by_id = {}  # type: Dict[object, Mapping[str, object]]
    outcome_schemas = set()
    for raw_outcome in outcome_rows:
        outcome = validate_transport_outcome(raw_outcome)
        outcome_schemas.add(outcome["schema"])
        if outcome["event_id"] in outcomes_by_id:
            raise BridgeValidationError("transport outcome IDs must be unique")
        outcomes_by_id[outcome["event_id"]] = outcome
    if len(outcome_schemas) != 1:
        raise BridgeValidationError("transport outcome stream mixes schema versions")
    if source_ids != set(outcomes_by_id):
        raise BridgeValidationError("source and transport event IDs do not partition exactly")
    zero_ns = manifest["aer_cycle_zero_timestamp_ns"]
    period_ps = manifest["aer_clock_period_ps"]
    assert isinstance(zero_ns, int) and isinstance(period_ps, int)
    occurrence_slots = set()
    retire_slots = set()
    native_rows_by_cycle_lane = {}  # type: Dict[Tuple[int, int], int]
    last_retire_by_source = {}  # type: Dict[object, int]
    joined = []  # type: List[JoinedEvent]
    delivered_count = 0
    overrun_count = 0
    for source in sources:
        outcome = outcomes_by_id[source["event_id"]]
        if outcome["source_index"] != source["source_index"]:
            raise BridgeValidationError("joined source_index differs")
        expected_occurrence = _ceil_occurrence_cycle(
            source["timestamp_ns"], zero_ns, period_ps  # type: ignore[arg-type]
        )
        if outcome["occurrence_cycle"] != expected_occurrence:
            raise BridgeValidationError("transport occurrence cycle differs from ceil mapping")
        occurrence_slot = (outcome["source_index"], outcome["occurrence_cycle"])
        if occurrence_slot in occurrence_slots:
            raise BridgeValidationError("multiple source events occupy one bitmap occurrence slot")
        occurrence_slots.add(occurrence_slot)
        physical_retire_ns = None  # type: Optional[int]
        dual_time = None  # type: Optional[DualTimeEvent]
        if outcome["outcome"] == "DELIVERED":
            delivered_count += 1
            retire_cycle = outcome["retire_cycle"]
            assert isinstance(retire_cycle, int)
            if outcome["occurrence_cycle"] > retire_cycle:
                raise BridgeValidationError("occurrence_cycle exceeds retire_cycle")
            source_index = outcome["source_index"]
            previous_retire = last_retire_by_source.get(source_index)
            if previous_retire is not None and retire_cycle <= previous_retire:
                raise BridgeValidationError("per-source FIFO retire order differs")
            last_retire_by_source[source_index] = retire_cycle
            native_lane = outcome["retire_native_lane"]
            retire_row = outcome["retire_row"]
            retire_col = outcome["retire_col"]
            assert (
                isinstance(native_lane, int)
                and isinstance(retire_row, int)
                and isinstance(retire_col, int)
            )
            bitmap_key = (retire_cycle, native_lane)
            prior_row = native_rows_by_cycle_lane.get(bitmap_key)
            if prior_row is not None and prior_row != retire_row:
                raise BridgeValidationError(
                    "one native lane-cycle contains more than one row"
                )
            other_lane_row = native_rows_by_cycle_lane.get(
                (retire_cycle, 1 - native_lane)
            )
            if other_lane_row == retire_row:
                raise BridgeValidationError(
                    "two native lanes select the same row in one retire cycle"
                )
            native_rows_by_cycle_lane[bitmap_key] = retire_row
            retire_slot = (retire_cycle, native_lane, retire_col)
            if retire_slot in retire_slots:
                raise BridgeValidationError(
                    "two events occupy one native cycle-lane-column slot"
                )
            retire_slots.add(retire_slot)
            physical_retire_ns = _physical_retire_timestamp_ns(
                retire_cycle, zero_ns, period_ps
            )
            try:
                dual_time = build_dual_time_event(
                    event_timestamp_ns=source["timestamp_ns"],  # type: ignore[arg-type]
                    occurrence_cycle=outcome["occurrence_cycle"],  # type: ignore[arg-type]
                    retire_cycle=retire_cycle,
                    clock_period_ps=period_ps,
                )
            except TransportTimeValidationError as error:
                raise BridgeValidationError(
                    "joined transport dual-time validation failed"
                ) from error
        else:
            overrun_count += 1
        joined.append(JoinedEvent(
            source, outcome, physical_retire_ns, dual_time
        ))
    if delivered_count + overrun_count != len(sources):
        raise BridgeValidationError("DELIVERED and OVERRUN do not partition the source stream")
    return tuple(joined)


def _copy_mapping(value: Mapping[str, object]) -> Dict[str, object]:
    return json.loads(canonical_json_bytes(value).decode("ascii"))


def _project_joined(joined: Sequence[JoinedEvent]) -> Mapping[str, List[Mapping[str, object]]]:
    raw_all = [_copy_mapping(row.source) for row in joined]
    delivered = [row for row in joined if row.transport["outcome"] == "DELIVERED"]
    raw_matched = [_copy_mapping(row.source) for row in delivered]
    aer_occ = [{
        "projection_semantics": OBSERVATIONAL_JOIN_LABEL,
        "event_id": row.transport["event_id"],
        "source_index": row.transport["source_index"],
        "occurrence_cycle": row.transport["occurrence_cycle"],
        "timestamp_ns": row.source["timestamp_ns"],
        "window_id": row.source["window_id"],
        "is_query": row.source["is_query"],
        "polarity": row.source["polarity"],
        "sensor_ray": list(row.source["sensor_ray"]),  # type: ignore[arg-type]
        "causal_pose_source_index": row.source["causal_pose_source_index"],
        "transform_guard_valid": row.source["transform_guard_valid"],
        "event_content_sha256": row.source["event_content_sha256"],
    } for row in delivered]
    raw_keys = [(row["event_id"], row["source_index"]) for row in raw_matched]
    aer_keys = [(row["event_id"], row["source_index"]) for row in aer_occ]
    if raw_keys != aer_keys:
        raise BridgeValidationError(
            "RAW4X4_MATCHED and AER_OCC identity/coordinate projection differs"
        )
    retired = sorted(
        delivered,
        key=lambda row: (
            row.transport["retire_cycle"],
            row.transport["retire_native_lane"],
            row.transport["retire_col"],
            row.transport["event_id"],
        ),
    )
    aer_ret = []  # type: List[Mapping[str, object]]
    for row in retired:
        dual_time = row.dual_time
        physical_retire_ns = row.physical_retire_timestamp_ns
        if dual_time is None or physical_retire_ns is None:
            raise BridgeValidationError("delivered row lacks dual-time validation")
        aer_ret.append({
            "projection_semantics": OBSERVATIONAL_JOIN_LABEL,
            "transport_time_semantics": TRANSPORT_TIME_SEMANTICS,
            "event_id": row.transport["event_id"],
            "source_index": row.transport["source_index"],
            "occurrence_cycle": row.transport["occurrence_cycle"],
            "occurrence_timestamp_ns": row.source["timestamp_ns"],
            "retire_cycle": row.transport["retire_cycle"],
            "retire_native_lane": row.transport["retire_native_lane"],
            "retire_row": row.transport["retire_row"],
            "retire_col": row.transport["retire_col"],
            "physical_retire_timestamp_ns": physical_retire_ns,
            "latency_cycles": dual_time.latency_cycles,
            "latency_ns": dual_time.latency_ns,
            "latency_injected_timestamp_ns": (
                dual_time.latency_injected_timestamp_ns
            ),
            "window_id": row.source["window_id"],
            "is_query": row.source["is_query"],
            "polarity": row.source["polarity"],
            "sensor_ray": list(row.source["sensor_ray"]),  # type: ignore[arg-type]
            "causal_pose_source_index": row.source["causal_pose_source_index"],
            "transform_guard_valid": row.source["transform_guard_valid"],
            "event_content_sha256": row.source["event_content_sha256"],
        })
    return {
        "RAW4X4_ALL": raw_all,
        "RAW4X4_MATCHED": raw_matched,
        "AER_OCC": aer_occ,
        "AER_RET": aer_ret,
    }


@dataclass(frozen=True)
class BridgeBundle:
    manifest: Mapping[str, object]
    manifest_sha256: str
    source_events: Tuple[Mapping[str, object], ...]
    transport_outcomes: Tuple[Mapping[str, object], ...]
    joined_events: Tuple[JoinedEvent, ...]

    def project(self) -> Mapping[str, List[Mapping[str, object]]]:
        if hashlib.sha256(canonical_json_bytes(self.manifest)).hexdigest() != self.manifest_sha256:
            raise BridgeValidationError("captured manifest was mutated after validation")
        source_stream = self.manifest["source_events"]
        outcome_stream = self.manifest["transport_outcomes"]
        if not isinstance(source_stream, Mapping) or not isinstance(outcome_stream, Mapping):
            raise BridgeValidationError("captured stream manifest was mutated")
        if hashlib.sha256(canonical_jsonl_bytes(self.source_events)).hexdigest() != source_stream["sha256"]:
            raise BridgeValidationError("captured source events were mutated after validation")
        if hashlib.sha256(canonical_jsonl_bytes(self.transport_outcomes)).hexdigest() != outcome_stream["sha256"]:
            raise BridgeValidationError("captured transport outcomes were mutated after validation")
        joined = _join_streams(self.source_events, self.transport_outcomes, self.manifest)
        return _project_joined(joined)


def load_bridge_bundle(manifest_path: Path, manifest_authority_sha256: str) -> BridgeBundle:
    manifest_sha = _sha256(
        manifest_authority_sha256, "caller-supplied manifest authority"
    )
    manifest_value = load_canonical_json(Path(manifest_path), manifest_sha)
    manifest = validate_manifest(manifest_value)
    root = Path(manifest_path).parent
    opaque_authorities = (
        ("mapping_authority", "mapping authority"),
        ("source_registry_authority", "source registry authority"),
        ("pose_stream_authority", "pose stream authority"),
        ("native_transport_receipt_authority", "native transport receipt authority"),
    )
    artifacts = []
    for field, label in opaque_authorities:
        authority = manifest[field]
        assert isinstance(authority, Mapping)
        artifacts.append((authority["path"], authority["sha256"], label))
    rtl_rows = manifest["rtl_authorities"]
    assert isinstance(rtl_rows, list)
    artifacts.extend(
        (rtl["path"], rtl["sha256"], "RTL authority")
        for rtl in rtl_rows if isinstance(rtl, Mapping)
    )
    for relative, expected, where in artifacts:
        path = _member_path(root, relative, where + " path")
        data = _read_regular_bytes(path, MAX_STREAM_BYTES, where)
        if hashlib.sha256(data).hexdigest() != expected:
            raise BridgeValidationError("%s SHA-256 differs" % where)
    source_stream = manifest["source_events"]
    outcome_stream = manifest["transport_outcomes"]
    assert isinstance(source_stream, Mapping) and isinstance(outcome_stream, Mapping)
    source_rows = load_canonical_jsonl(
        _member_path(root, source_stream["path"], "source stream path"),
        source_stream["sha256"],  # type: ignore[arg-type]
    )
    outcome_rows = load_canonical_jsonl(
        _member_path(root, outcome_stream["path"], "outcome stream path"),
        outcome_stream["sha256"],  # type: ignore[arg-type]
    )
    if len(source_rows) != source_stream["event_count"]:
        raise BridgeValidationError("source stream event count differs")
    if len(outcome_rows) != outcome_stream["event_count"]:
        raise BridgeValidationError("outcome stream event count differs")
    joined = _join_streams(source_rows, outcome_rows, manifest)
    return BridgeBundle(manifest, manifest_sha, source_rows, outcome_rows, joined)
