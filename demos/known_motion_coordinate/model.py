"""Strict external post-retire, known-motion coordinate transformation."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


INTRINSICS_SCHEMA = "redred.known_motion.intrinsics/v2"
POSE_HEADER_SCHEMA = "redred.known_motion.pose_stream/v2"
POSE_SCHEMA = "redred.known_motion.pose/v2"
EVENT_HEADER_SCHEMA = "redred.aer.retired_event_stream/v3"
EVENT_SCHEMA = "redred.aer.retired_event/v3"
RESULT_HEADER_SCHEMA = "redred.known_motion.transform_stream/v3"
RESULT_SCHEMA = "redred.known_motion.transform_result/v3"
SUMMARY_SCHEMA = "redred.known_motion.summary/v3"

SYNTHETIC_DEMO = "SYNTHETIC_DEMO"
CANONICAL_COMMON_SUITE = "CANONICAL_COMMON_SUITE"
CONVENTION_ID = "redred.camera.xyz-rdf.active-w2s.rrtp.deg.pinhole-row-major/v1"
COORDINATE_CONVENTION: dict[str, Any] = {
    "convention_id": CONVENTION_ID,
    "camera_axes": {
        "x": "+right",
        "y": "+down",
        "z": "+forward",
        "handedness": "right_handed",
    },
    "pixel_axes": {
        "x": "+right",
        "y": "+down",
        "origin": "top_left_pixel_center_0_0",
    },
    "rotation": {
        "type": "active",
        "direction": "world_to_sensor",
        "matrix_storage": "row_major",
        "euler_order": "R_roll@R_tilt@R_pan",
        "pan_axis": "+Y",
        "tilt_axis": "+X",
        "roll_axis": "+Z",
        "angle_unit": "degrees",
        "internal_trig_angle_unit": "radians",
        "degrees_to_radians": "radians=degrees*pi/180",
    },
    "projection": {
        "model": "pinhole",
        "intrinsic_equation": "pixel=K*(ray/ray_z)",
        "extrinsic_equation": "ray_sensor=R_world_to_sensor@ray_world",
        "input_ray": "K_inverse@[x,y,1]",
    },
}


class InterfaceError(ValueError):
    """Raised when an input violates the fail-closed interface contract."""


@dataclass(frozen=True)
class InputBlob:
    path: Path
    data: bytes
    sha256: str


@dataclass(frozen=True)
class Timebase:
    clock_domain: str
    epoch: str
    unit: str


@dataclass(frozen=True)
class Timestamp:
    value: int
    timebase: Timebase

    def as_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "clock_domain": self.timebase.clock_domain,
            "epoch": self.timebase.epoch,
            "unit": self.timebase.unit,
        }


@dataclass(frozen=True)
class Intrinsics:
    intrinsics_id: str
    camera_id: str
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    convention_id: str = CONVENTION_ID


Matrix = tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class Pose:
    pose_id: str
    timestamp: Timestamp
    rotation_world_to_sensor: Matrix
    representation: str


def _read_once(path: str | Path, label: str) -> InputBlob:
    resolved = Path(path)
    if resolved.is_symlink() or not resolved.is_file():
        raise InterfaceError(f"{label} is missing, non-file, or symlinked: {resolved}")
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise InterfaceError(f"cannot read {label}: {exc}") from exc
    return InputBlob(resolved, data, hashlib.sha256(data).hexdigest())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InterfaceError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _loads(data: bytes, location: str) -> Any:
    try:
        text = data.decode("utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except InterfaceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InterfaceError(f"invalid UTF-8 JSON at {location}: {exc}") from exc


def _load_json(blob: InputBlob) -> dict[str, Any]:
    value = _loads(blob.data, str(blob.path))
    if not isinstance(value, dict):
        raise InterfaceError(f"{blob.path}: top-level JSON value must be an object")
    return value


def _load_jsonl(blob: InputBlob) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(blob.data.splitlines(), 1):
        if not line.strip():
            continue
        value = _loads(line, f"{blob.path}:{line_number}")
        if not isinstance(value, dict):
            raise InterfaceError(f"{blob.path}:{line_number}: record must be an object")
        records.append(value)
    if not records:
        raise InterfaceError(f"{blob.path}: JSONL stream is empty")
    return records


def _exact_keys(obj: Any, expected: set[str], where: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise InterfaceError(f"{where} must be an object")
    actual = set(obj)
    if actual != expected:
        raise InterfaceError(
            f"{where} keys differ: missing={sorted(expected - actual)} "
            f"unknown={sorted(actual - expected)}"
        )
    return obj


def _required_string(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj[key]
    if not isinstance(value, str) or not value:
        raise InterfaceError(f"{where}.{key} must be a non-empty string")
    return value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InterfaceError(f"{where} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise InterfaceError(f"{where} must be finite")
    return result


def _nonnegative_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InterfaceError(f"{where} must be a non-negative integer")
    return value


def _digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InterfaceError(f"{where} must be lowercase SHA-256 hex")
    if value == "0" * 64:
        raise InterfaceError(f"{where} must be nonzero")
    return value


def _timebase(obj: Any, where: str) -> Timebase:
    row = _exact_keys(obj, {"clock_domain", "epoch", "unit"}, where)
    unit = _required_string(row, "unit", where)
    if unit != "ns":
        raise InterfaceError(f"{where}.unit must be ns")
    return Timebase(
        _required_string(row, "clock_domain", where),
        _required_string(row, "epoch", where),
        unit,
    )


def _clock_domains(obj: Any, where: str, timebase: Timebase) -> dict[str, str]:
    fields = {
        "pose_timestamp", "occurrence_time", "capture_time", "accept_time",
        "retire_time",
    }
    row = _exact_keys(obj, fields, where)
    for field in sorted(fields):
        label = _required_string(row, field, where)
        if label != timebase.clock_domain:
            raise InterfaceError(
                f"{where}.{field} must equal absolute clock domain "
                f"{timebase.clock_domain}"
            )
    return row


def _timestamp(obj: Any, where: str) -> Timestamp:
    row = _exact_keys(obj, {"value", "clock_domain", "epoch", "unit"}, where)
    timebase = _timebase(
        {key: row[key] for key in ("clock_domain", "epoch", "unit")}, where
    )
    return Timestamp(_nonnegative_integer(row["value"], f"{where}.value"), timebase)


def _provenance(obj: Any, where: str) -> dict[str, Any]:
    row = _exact_keys(obj, {"source_id", "created_by", "content_sha256"}, where)
    _required_string(row, "source_id", where)
    _required_string(row, "created_by", where)
    _digest(row["content_sha256"], f"{where}.content_sha256")
    return row


def _load_intrinsics_blob(blob: InputBlob) -> Intrinsics:
    obj = _exact_keys(
        _load_json(blob),
        {
            "schema", "intrinsics_id", "camera_id", "width", "height",
            "fx", "fy", "cx", "cy", "convention", "provenance",
        },
        str(blob.path),
    )
    if obj["schema"] != INTRINSICS_SCHEMA:
        raise InterfaceError(f"{blob.path}: unsupported intrinsics schema")
    if obj["convention"] != COORDINATE_CONVENTION:
        raise InterfaceError(f"{blob.path}: coordinate convention is not the machine-bound contract")
    _provenance(obj["provenance"], f"{blob.path}.provenance")
    width = _nonnegative_integer(obj["width"], f"{blob.path}.width")
    height = _nonnegative_integer(obj["height"], f"{blob.path}.height")
    if width == 0 or height == 0:
        raise InterfaceError(f"{blob.path}: width and height must be positive")
    fx = _number(obj["fx"], f"{blob.path}.fx")
    fy = _number(obj["fy"], f"{blob.path}.fy")
    if fx <= 0.0 or fy <= 0.0:
        raise InterfaceError(f"{blob.path}: fx and fy must be positive")
    return Intrinsics(
        _required_string(obj, "intrinsics_id", str(blob.path)),
        _required_string(obj, "camera_id", str(blob.path)),
        width,
        height,
        fx,
        fy,
        _number(obj["cx"], f"{blob.path}.cx"),
        _number(obj["cy"], f"{blob.path}.cy"),
    )


def load_intrinsics(path: str | Path) -> Intrinsics:
    """Load and validate one intrinsics file from one exact byte read."""

    return _load_intrinsics_blob(_read_once(path, "intrinsics input"))


def _matmul(left: Matrix, right: Matrix) -> Matrix:
    return tuple(
        tuple(
            sum(left[row][index] * right[index][column] for index in range(3))
            for column in range(3)
        )
        for row in range(3)
    )


def _matvec(matrix: Matrix, vector: tuple[float, float, float]) -> tuple[float, float, float]:
    values = tuple(
        sum(matrix[row][index] * vector[index] for index in range(3))
        for row in range(3)
    )
    return values[0], values[1], values[2]


def _transpose(matrix: Matrix) -> Matrix:
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def _determinant(matrix: Matrix) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _validated_rotation(value: Any, where: str) -> Matrix:
    if not isinstance(value, list) or len(value) != 3:
        raise InterfaceError(f"{where} must be a 3x3 array")
    rows: list[tuple[float, float, float]] = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != 3:
            raise InterfaceError(f"{where}[{row_index}] must contain three numbers")
        values = tuple(
            _number(item, f"{where}[{row_index}][{column_index}]")
            for column_index, item in enumerate(row)
        )
        rows.append((values[0], values[1], values[2]))
    matrix: Matrix = tuple(rows)
    product = _matmul(matrix, _transpose(matrix))
    for row in range(3):
        for column in range(3):
            expected = 1.0 if row == column else 0.0
            if not math.isclose(product[row][column], expected, rel_tol=0.0, abs_tol=1e-9):
                raise InterfaceError(f"{where} must be orthonormal within 1e-9")
    if not math.isclose(_determinant(matrix), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise InterfaceError(f"{where} must have determinant +1")
    return matrix


def euler_world_to_sensor(pan_deg: float, tilt_deg: float, roll_deg: float) -> Matrix:
    """Return active R_roll @ R_tilt @ R_pan; arguments are degrees."""

    pan = math.radians(_number(pan_deg, "pan_deg"))
    tilt = math.radians(_number(tilt_deg, "tilt_deg"))
    roll = math.radians(_number(roll_deg, "roll_deg"))
    cp, sp = math.cos(pan), math.sin(pan)
    ct, st = math.cos(tilt), math.sin(tilt)
    cr, sr = math.cos(roll), math.sin(roll)
    r_pan: Matrix = ((cp, 0.0, sp), (0.0, 1.0, 0.0), (-sp, 0.0, cp))
    r_tilt: Matrix = ((1.0, 0.0, 0.0), (0.0, ct, -st), (0.0, st, ct))
    r_roll: Matrix = ((cr, -sr, 0.0), (sr, cr, 0.0), (0.0, 0.0, 1.0))
    return _matmul(r_roll, _matmul(r_tilt, r_pan))


def _load_pose_blob(blob: InputBlob, intrinsics: Intrinsics) -> tuple[dict[str, Any], list[Pose]]:
    records = _load_jsonl(blob)
    header = _exact_keys(
        records[0],
        {
            "schema", "record_type", "pose_stream_id", "camera_id",
            "intrinsics_id", "convention_id", "timebase", "provenance",
        },
        f"{blob.path}:1",
    )
    if header["schema"] != POSE_HEADER_SCHEMA or header["record_type"] != "header":
        raise InterfaceError(f"{blob.path}: first record is not the v2 pose header")
    if header["camera_id"] != intrinsics.camera_id or header["intrinsics_id"] != intrinsics.intrinsics_id:
        raise InterfaceError(f"{blob.path}: camera/intrinsics binding mismatch")
    if header["convention_id"] != intrinsics.convention_id:
        raise InterfaceError(f"{blob.path}: coordinate convention binding mismatch")
    pose_timebase = _timebase(header["timebase"], f"{blob.path}:1.timebase")
    _provenance(header["provenance"], f"{blob.path}:1.provenance")
    _required_string(header, "pose_stream_id", f"{blob.path}:1")

    poses: list[Pose] = []
    ids: set[str] = set()
    timestamps: set[int] = set()
    common = {"schema", "record_type", "pose_id", "timestamp"}
    for line_number, raw in enumerate(records[1:], 2):
        where = f"{blob.path}:{line_number}"
        if "rotation_matrix" in raw:
            row = _exact_keys(raw, common | {"rotation_matrix", "matrix_direction"}, where)
            if row["matrix_direction"] != "world_to_sensor":
                raise InterfaceError(f"{where}.matrix_direction must be world_to_sensor")
            rotation = _validated_rotation(row["rotation_matrix"], f"{where}.rotation_matrix")
            representation = "rotation_matrix"
        else:
            row = _exact_keys(
                raw,
                common | {"pan", "tilt", "roll", "angle_unit"},
                where,
            )
            if row["angle_unit"] != "degrees":
                raise InterfaceError(f"{where}.angle_unit must be degrees")
            rotation = euler_world_to_sensor(row["pan"], row["tilt"], row["roll"])
            representation = "pan_tilt_roll"
        if row["schema"] != POSE_SCHEMA or row["record_type"] != "pose":
            raise InterfaceError(f"{where}: expected {POSE_SCHEMA} pose record")
        pose_id = _required_string(row, "pose_id", where)
        timestamp = _timestamp(row["timestamp"], f"{where}.timestamp")
        if timestamp.timebase != pose_timebase:
            raise InterfaceError(f"{where}: pose timestamp differs from stream timebase")
        if pose_id in ids or timestamp.value in timestamps:
            raise InterfaceError(f"{where}: duplicate pose ID or pose timestamp")
        ids.add(pose_id)
        timestamps.add(timestamp.value)
        poses.append(Pose(pose_id, timestamp, rotation, representation))
    if not poses:
        raise InterfaceError(f"{blob.path}: pose stream contains no poses")
    poses.sort(key=lambda pose: pose.timestamp.value)
    return header, poses


def load_pose_stream(path: str | Path, intrinsics: Intrinsics) -> tuple[dict[str, Any], list[Pose]]:
    """Load and validate one pose JSONL file from one exact byte read."""

    return _load_pose_blob(_read_once(path, "pose input"), intrinsics)


def warp_pixel(
    x: float,
    y: float,
    intrinsics: Intrinsics,
    rotation_world_to_sensor: Matrix,
    mode: str,
) -> dict[str, Any]:
    """Warp one pixel using the machine-bound active rotation convention."""

    if mode not in ("world-to-sensor", "sensor-to-world"):
        raise InterfaceError(f"unsupported transform mode: {mode}")
    x = _number(x, "pixel.x")
    y = _number(y, "pixel.y")
    if not (0.0 <= x <= intrinsics.width - 1 and 0.0 <= y <= intrinsics.height - 1):
        raise InterfaceError("input pixel lies outside the declared camera image")
    ray = ((x - intrinsics.cx) / intrinsics.fx, (y - intrinsics.cy) / intrinsics.fy, 1.0)
    rotation = (
        rotation_world_to_sensor
        if mode == "world-to-sensor"
        else _transpose(rotation_world_to_sensor)
    )
    rx, ry, rz = _matvec(rotation, ray)
    if rz <= 0.0:
        return {"status": "out_of_fov", "reason": "behind_camera", "ray_z": rz}
    output_x = intrinsics.fx * rx / rz + intrinsics.cx
    output_y = intrinsics.fy * ry / rz + intrinsics.cy
    if not (
        0.0 <= output_x <= intrinsics.width - 1
        and 0.0 <= output_y <= intrinsics.height - 1
    ):
        return {
            "status": "out_of_fov",
            "reason": "outside_image",
            "x_float": output_x,
            "y_float": output_y,
            "ray_z": rz,
        }
    return {
        "status": "in_fov",
        "x": int(math.floor(output_x + 0.5)),
        "y": int(math.floor(output_y + 0.5)),
        "x_float": output_x,
        "y_float": output_y,
        "ray_z": rz,
    }


def _transport_accounting(obj: Any, where: str) -> dict[str, int]:
    row = _exact_keys(obj, {"generated", "source_overrun", "accepted", "retired"}, where)
    result = {key: _nonnegative_integer(row[key], f"{where}.{key}") for key in row}
    if result["generated"] != result["source_overrun"] + result["accepted"]:
        raise InterfaceError(f"{where}: generated must equal source_overrun + accepted")
    return result


def _event_provenance(obj: Any, where: str) -> dict[str, Any]:
    row = _exact_keys(
        obj,
        {
            "run_id", "candidate_id", "workload_id", "manifest_sha256",
            "content_sha256", "transport_receipt",
        },
        where,
    )
    for key in ("run_id", "candidate_id", "workload_id"):
        _required_string(row, key, where)
    _digest(row["manifest_sha256"], f"{where}.manifest_sha256")
    _digest(row["content_sha256"], f"{where}.content_sha256")
    return row


def _load_events_blob(
    blob: InputBlob,
    intrinsics: Intrinsics,
    pose_header: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = _load_jsonl(blob)
    header = _exact_keys(
        records[0],
        {
            "schema", "record_type", "evidence_class", "camera_id",
            "intrinsics_id", "pose_stream_id", "convention_id",
            "coordinate_frame", "pose_lookup_time", "transport_accounting",
            "absolute_timebase", "clock_domains", "provenance",
        },
        f"{blob.path}:1",
    )
    if header["schema"] != EVENT_HEADER_SCHEMA or header["record_type"] != "header":
        raise InterfaceError(f"{blob.path}: first record is not the v3 event header")
    if header["evidence_class"] == CANONICAL_COMMON_SUITE:
        raise InterfaceError(
            "CANONICAL_COMMON_SUITE is HOLD/unsupported: no trusted post-retire "
            "exporter and receipt exists"
        )
    if header["evidence_class"] != SYNTHETIC_DEMO:
        raise InterfaceError(f"{blob.path}: only SYNTHETIC_DEMO is supported")
    if header["camera_id"] != intrinsics.camera_id or header["intrinsics_id"] != intrinsics.intrinsics_id:
        raise InterfaceError(f"{blob.path}: camera/intrinsics binding mismatch")
    if header["pose_stream_id"] != pose_header["pose_stream_id"]:
        raise InterfaceError(f"{blob.path}: pose-stream binding mismatch")
    if header["convention_id"] != intrinsics.convention_id:
        raise InterfaceError(f"{blob.path}: coordinate convention binding mismatch")
    if header["coordinate_frame"] not in ("world_reference_image", "sensor_image"):
        raise InterfaceError(f"{blob.path}: unsupported coordinate_frame")
    if header["pose_lookup_time"] not in ("occurrence_time", "capture_time"):
        raise InterfaceError(f"{blob.path}: pose_lookup_time must select occurrence_time or capture_time")
    absolute_timebase = _timebase(
        header["absolute_timebase"], f"{blob.path}:1.absolute_timebase"
    )
    _clock_domains(
        header["clock_domains"], f"{blob.path}:1.clock_domains", absolute_timebase
    )
    if _timebase(pose_header["timebase"], "pose header timebase") != absolute_timebase:
        raise InterfaceError(f"{blob.path}: pose stream differs from absolute timebase")
    provenance = _event_provenance(header["provenance"], f"{blob.path}:1.provenance")
    if provenance["transport_receipt"] is not None:
        raise InterfaceError(
            "SYNTHETIC_DEMO rejects transport receipts; canonical coordinate join is HOLD"
        )
    header["provenance"] = provenance
    accounting = _transport_accounting(
        header["transport_accounting"], f"{blob.path}:1.transport_accounting"
    )

    event_keys = {
        "schema", "record_type", "tb_only_event_id", "retire_sequence_index",
        "logical_source", "address", "occurrence_time", "capture_time",
        "accept_time", "retire_time", "pose_id", "x", "y", "polarity",
    }
    events: list[dict[str, Any]] = []
    ids: set[int] = set()
    previous_retire_value = -1
    for line_number, raw in enumerate(records[1:], 2):
        where = f"{blob.path}:{line_number}"
        event = _exact_keys(raw, event_keys, where)
        if event["schema"] != EVENT_SCHEMA or event["record_type"] != "event":
            raise InterfaceError(f"{where}: expected {EVENT_SCHEMA} event record")
        event_id = _nonnegative_integer(event["tb_only_event_id"], f"{where}.tb_only_event_id")
        if event_id in ids:
            raise InterfaceError(f"{where}: duplicate tb_only_event_id {event_id}")
        ids.add(event_id)
        retire_sequence_index = _nonnegative_integer(
            event["retire_sequence_index"], f"{where}.retire_sequence_index"
        )
        if retire_sequence_index != len(events):
            raise InterfaceError(
                f"{where}: retire_sequence_index must be contiguous JSONL retirement order"
            )
        event["logical_source"] = _nonnegative_integer(event["logical_source"], f"{where}.logical_source")
        event["address"] = _nonnegative_integer(event["address"], f"{where}.address")
        if event["logical_source"] >= 16 or event["address"] >= 16:
            raise InterfaceError(f"{where}: logical_source/address must be N=16 values")
        if event["address"] != event["logical_source"]:
            raise InterfaceError(
                f"{where}: SYNTHETIC_DEMO address must equal logical_source"
            )
        for key in ("occurrence_time", "capture_time", "accept_time", "retire_time"):
            event[key] = _timestamp(event[key], f"{where}.{key}")
        occurrence = event["occurrence_time"]
        capture = event["capture_time"]
        accept = event["accept_time"]
        retire = event["retire_time"]
        if any(
            timestamp.timebase != absolute_timebase
            for timestamp in (occurrence, capture, accept, retire)
        ):
            raise InterfaceError(
                f"{where}: every stage timestamp must use the exact absolute timebase"
            )
        if not occurrence.value <= capture.value <= accept.value <= retire.value:
            raise InterfaceError(
                f"{where}: require occurrence <= capture <= accept <= retire"
            )
        if retire.value < previous_retire_value:
            raise InterfaceError(
                f"{where}: retire timestamps must follow retire_sequence_index order"
            )
        previous_retire_value = retire.value
        if event["pose_id"] is not None and (
            not isinstance(event["pose_id"], str) or not event["pose_id"]
        ):
            raise InterfaceError(f"{where}.pose_id must be null or a non-empty string")
        event["x"] = _number(event["x"], f"{where}.x")
        event["y"] = _number(event["y"], f"{where}.y")
        polarity = event["polarity"]
        if isinstance(polarity, bool) or not isinstance(polarity, int) or polarity not in (-1, 0, 1):
            raise InterfaceError(f"{where}.polarity must be one of -1, 0, or 1")
        events.append(event)
    if accounting["accepted"] != accounting["retired"] or accounting["retired"] != len(events):
        raise InterfaceError(
            f"{blob.path}: hard precondition requires accepted == retired == event-record count"
        )
    return header, events


def _select_pose(
    event: dict[str, Any],
    lookup_field: str,
    poses: list[Pose],
    max_pose_age_ns: int,
) -> tuple[Pose, str, int, Timestamp]:
    lookup_time: Timestamp = event[lookup_field]
    if event["pose_id"] is not None:
        by_id = {pose.pose_id: pose for pose in poses}
        pose = by_id.get(event["pose_id"])
        if pose is None:
            raise InterfaceError(
                f"event {event['tb_only_event_id']}: unknown pose_id {event['pose_id']!r}"
            )
        selection = "explicit_pose_id"
    else:
        timestamps = [pose.timestamp.value for pose in poses]
        index = bisect.bisect_right(timestamps, lookup_time.value) - 1
        if index < 0:
            raise InterfaceError(
                f"event {event['tb_only_event_id']}: no pose at or before selected lookup time"
            )
        pose = poses[index]
        selection = "timestamp_zero_order_hold"
    if pose.timestamp.timebase != lookup_time.timebase:
        raise InterfaceError(f"event {event['tb_only_event_id']}: pose timebase mismatch")
    age = lookup_time.value - pose.timestamp.value
    if age < 0:
        raise InterfaceError(f"event {event['tb_only_event_id']}: explicit pose is in the future")
    if age > max_pose_age_ns:
        raise InterfaceError(
            f"event {event['tb_only_event_id']}: pose age {age} exceeds inclusive "
            f"maximum {max_pose_age_ns} ns"
        )
    return pose, selection, age, lookup_time


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def transform_files(
    events_path: str | Path,
    intrinsics_path: str | Path,
    poses_path: str | Path,
    output_path: str | Path,
    summary_path: str | Path,
    mode: str,
    max_pose_age_ns: int,
) -> dict[str, Any]:
    """Validate, transform, and atomically write results from exact input bytes."""

    if isinstance(max_pose_age_ns, bool) or not isinstance(max_pose_age_ns, int) or max_pose_age_ns < 0:
        raise InterfaceError("max_pose_age_ns is mandatory and must be a non-negative integer")
    output_path = Path(output_path)
    summary_path = Path(summary_path)
    input_paths = {Path(events_path).resolve(), Path(intrinsics_path).resolve(), Path(poses_path).resolve()}
    if output_path.resolve() in input_paths or summary_path.resolve() in input_paths:
        raise InterfaceError("outputs must not overwrite inputs")
    if output_path.resolve() == summary_path.resolve():
        raise InterfaceError("JSONL output and summary paths must differ")

    event_blob = _read_once(events_path, "event input")
    intrinsics_blob = _read_once(intrinsics_path, "intrinsics input")
    pose_blob = _read_once(poses_path, "pose input")
    intrinsics = _load_intrinsics_blob(intrinsics_blob)
    pose_header, poses = _load_pose_blob(pose_blob, intrinsics)
    event_header, events = _load_events_blob(event_blob, intrinsics, pose_header)
    required_frame = "world_reference_image" if mode == "world-to-sensor" else "sensor_image"
    output_frame = "sensor_image" if mode == "world-to-sensor" else "world_reference_image"
    if mode not in ("world-to-sensor", "sensor-to-world"):
        raise InterfaceError(f"unsupported transform mode: {mode}")
    if event_header["coordinate_frame"] != required_frame:
        raise InterfaceError(f"mode {mode} requires input coordinate_frame {required_frame}")

    provenance = event_header["provenance"]
    result_provenance = {
        "evidence_class": event_header["evidence_class"],
        "run_id": provenance["run_id"],
        "candidate_id": provenance["candidate_id"],
        "workload_id": provenance["workload_id"],
        "manifest_sha256": provenance["manifest_sha256"],
        "source_content_sha256": provenance["content_sha256"],
        "events_input_sha256": event_blob.sha256,
        "intrinsics_input_sha256": intrinsics_blob.sha256,
        "poses_input_sha256": pose_blob.sha256,
    }
    result_header = {
        "schema": RESULT_HEADER_SCHEMA,
        "record_type": "header",
        "mode": mode,
        "input_coordinate_frame": required_frame,
        "output_coordinate_frame": output_frame,
        "camera_id": intrinsics.camera_id,
        "intrinsics_id": intrinsics.intrinsics_id,
        "pose_stream_id": pose_header["pose_stream_id"],
        "convention": COORDINATE_CONVENTION,
        "absolute_timebase": event_header["absolute_timebase"],
        "clock_domains": event_header["clock_domains"],
        "pose_lookup_time": event_header["pose_lookup_time"],
        "maximum_pose_age_ns_inclusive": max_pose_age_ns,
        "provenance": result_provenance,
    }
    output_records = [result_header]
    in_fov = 0
    reasons: dict[str, int] = {}
    selections = {"explicit_pose_id": 0, "timestamp_zero_order_hold": 0}
    maximum_observed_age = 0
    for event in events:
        pose, selection, age, lookup_time = _select_pose(
            event, event_header["pose_lookup_time"], poses, max_pose_age_ns
        )
        selections[selection] += 1
        maximum_observed_age = max(maximum_observed_age, age)
        warped = warp_pixel(
            event["x"], event["y"], intrinsics, pose.rotation_world_to_sensor, mode
        )
        if warped["status"] == "in_fov":
            in_fov += 1
        else:
            reasons[warped["reason"]] = reasons.get(warped["reason"], 0) + 1
        output_records.append(
            {
                "schema": RESULT_SCHEMA,
                "record_type": "event",
                "tb_only_event_id": event["tb_only_event_id"],
                "retire_sequence_index": event["retire_sequence_index"],
                "logical_source": event["logical_source"],
                "address": event["address"],
                "occurrence_time": event["occurrence_time"].as_json(),
                "capture_time": event["capture_time"].as_json(),
                "accept_time": event["accept_time"].as_json(),
                "retire_time": event["retire_time"].as_json(),
                "pose_lookup_time_field": event_header["pose_lookup_time"],
                "pose_lookup_timestamp": lookup_time.as_json(),
                "pose_id": pose.pose_id,
                "pose_timestamp": pose.timestamp.as_json(),
                "pose_age_ns": age,
                "pose_selection": selection,
                "polarity": event["polarity"],
                "input": {"frame": required_frame, "x": event["x"], "y": event["y"]},
                "output": {"frame": output_frame, **warped},
            }
        )

    accounting = event_header["transport_accounting"]
    coordinate_out = len(events) - in_fov
    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": mode,
        "provenance": result_provenance,
        "counts": {
            "input_retired_events": len(events),
            "transformed_in_fov": in_fov,
            "coordinate_out_of_fov": coordinate_out,
            "coordinate_out_of_fov_reasons": dict(sorted(reasons.items())),
        },
        "pose_handling": {
            "lookup_time_field": event_header["pose_lookup_time"],
            "selection_counts": selections,
            "maximum_observed_pose_age_ns": maximum_observed_age,
            "maximum_allowed_pose_age_ns_inclusive": max_pose_age_ns,
            "interpolation": "zero_order_hold_latest_pose_at_or_before_lookup_time",
        },
        "aer_transport_accounting": {
            **accounting,
            "accepted_missing": 0,
        },
        "accounting_invariants": {
            "generated_equals_source_overrun_plus_accepted": True,
            "accepted_equals_retired_equals_event_records": True,
            "retired_equals_in_fov_plus_coordinate_out_of_fov": True,
        },
        "event_identity": {
            "tb_only_event_id": "preserved_unique_noncontiguous_identity",
            "retire_sequence_index": "contiguous_JSONL_retirement_order",
        },
        "scope": {
            "stage": "strictly_external_post_retire_software_demo",
            "model": "rotation-only pinhole rays; no translation, depth, distortion, or pose estimation",
            "ppa": "not candidate RTL/TB and not included in endpoint PPA",
            "loss_separation": "coordinate_out_of_fov is geometry accounting, not AER transport loss",
            "canonical_coordinate_join": "HOLD_UNSUPPORTED",
        },
    }
    jsonl_text = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in output_records
    )
    _atomic_write(output_path, jsonl_text)
    _atomic_write(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary
