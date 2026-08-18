"""Deterministic rotation-only pinhole coordinate transforms.

The "world" image in this bounded demonstration is a reference pinhole camera
whose axes are fixed in the world.  It is not a metric 3-D reconstruction.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


INTRINSICS_SCHEMA = "redred.known_motion.intrinsics/v1"
POSE_HEADER_SCHEMA = "redred.known_motion.pose_stream/v1"
POSE_SCHEMA = "redred.known_motion.pose/v1"
EVENT_HEADER_SCHEMA = "redred.aer.retired_event_stream/v1"
EVENT_SCHEMA = "redred.aer.retired_event/v1"
RESULT_HEADER_SCHEMA = "redred.known_motion.transform_stream/v1"
RESULT_SCHEMA = "redred.known_motion.transform_result/v1"
SUMMARY_SCHEMA = "redred.known_motion.summary/v1"


class InterfaceError(ValueError):
    """Raised when an input violates the versioned interface contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InterfaceError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _loads(text: str, location: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except InterfaceError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InterfaceError(f"invalid JSON at {location}: {exc}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    value = _loads(path.read_text(encoding="utf-8"), str(path))
    if not isinstance(value, dict):
        raise InterfaceError(f"{path}: top-level JSON value must be an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = _loads(line, f"{path}:{line_number}")
        if not isinstance(value, dict):
            raise InterfaceError(f"{path}:{line_number}: record must be an object")
        records.append(value)
    if not records:
        raise InterfaceError(f"{path}: JSONL stream is empty")
    return records


def _required_string(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
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


def _validate_provenance(
    obj: dict[str, Any], where: str, required: Sequence[str]
) -> dict[str, Any]:
    provenance = obj.get("provenance")
    if not isinstance(provenance, dict):
        raise InterfaceError(f"{where}.provenance must be an object")
    for key in required:
        _required_string(provenance, key, f"{where}.provenance")
    return provenance


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


@dataclass(frozen=True)
class Pose:
    pose_id: str
    timestamp_ns: int
    rotation_world_to_sensor: tuple[tuple[float, float, float], ...]
    representation: str


def load_intrinsics(path: str | Path) -> Intrinsics:
    path = Path(path)
    obj = _read_json(path)
    if obj.get("schema") != INTRINSICS_SCHEMA:
        raise InterfaceError(f"{path}: unsupported intrinsics schema")
    _validate_provenance(obj, str(path), ("source_id", "created_by"))
    width = _nonnegative_integer(obj.get("width"), f"{path}.width")
    height = _nonnegative_integer(obj.get("height"), f"{path}.height")
    if width == 0 or height == 0:
        raise InterfaceError(f"{path}: width and height must be positive")
    fx = _number(obj.get("fx"), f"{path}.fx")
    fy = _number(obj.get("fy"), f"{path}.fy")
    if fx <= 0.0 or fy <= 0.0:
        raise InterfaceError(f"{path}: fx and fy must be positive")
    return Intrinsics(
        intrinsics_id=_required_string(obj, "intrinsics_id", str(path)),
        camera_id=_required_string(obj, "camera_id", str(path)),
        width=width,
        height=height,
        fx=fx,
        fy=fy,
        cx=_number(obj.get("cx"), f"{path}.cx"),
        cy=_number(obj.get("cy"), f"{path}.cy"),
    )


Matrix = tuple[tuple[float, float, float], ...]


def _matmul(left: Matrix, right: Matrix) -> Matrix:
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _matvec(matrix: Matrix, vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))  # type: ignore[return-value]


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
        rows.append(tuple(_number(item, f"{where}[{row_index}]") for item in row))
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
    """Return R_roll @ R_tilt @ R_pan using right-handed active rotations."""

    pan = math.radians(pan_deg)
    tilt = math.radians(tilt_deg)
    roll = math.radians(roll_deg)
    cp, sp = math.cos(pan), math.sin(pan)
    ct, st = math.cos(tilt), math.sin(tilt)
    cr, sr = math.cos(roll), math.sin(roll)
    r_pan: Matrix = ((cp, 0.0, sp), (0.0, 1.0, 0.0), (-sp, 0.0, cp))
    r_tilt: Matrix = ((1.0, 0.0, 0.0), (0.0, ct, -st), (0.0, st, ct))
    r_roll: Matrix = ((cr, -sr, 0.0), (sr, cr, 0.0), (0.0, 0.0, 1.0))
    return _matmul(r_roll, _matmul(r_tilt, r_pan))


def load_pose_stream(path: str | Path, intrinsics: Intrinsics) -> tuple[dict[str, Any], list[Pose]]:
    path = Path(path)
    records = _read_jsonl(path)
    header = records[0]
    if header.get("schema") != POSE_HEADER_SCHEMA or header.get("record_type") != "header":
        raise InterfaceError(f"{path}: first record must be a {POSE_HEADER_SCHEMA} header")
    _validate_provenance(header, f"{path}:1", ("source_id", "created_by"))
    if header.get("camera_id") != intrinsics.camera_id:
        raise InterfaceError(f"{path}: camera_id does not match intrinsics")
    if header.get("intrinsics_id") != intrinsics.intrinsics_id:
        raise InterfaceError(f"{path}: intrinsics_id does not match intrinsics")
    if header.get("rotation_direction") != "world_to_sensor":
        raise InterfaceError(f"{path}: rotation_direction must be world_to_sensor")
    _required_string(header, "pose_stream_id", f"{path}:1")

    poses: list[Pose] = []
    ids: set[str] = set()
    timestamps: set[int] = set()
    for line_number, obj in enumerate(records[1:], 2):
        where = f"{path}:{line_number}"
        if obj.get("schema") != POSE_SCHEMA or obj.get("record_type") != "pose":
            raise InterfaceError(f"{where}: expected {POSE_SCHEMA} pose record")
        pose_id = _required_string(obj, "pose_id", where)
        timestamp_ns = _nonnegative_integer(obj.get("timestamp_ns"), f"{where}.timestamp_ns")
        if pose_id in ids or timestamp_ns in timestamps:
            raise InterfaceError(f"{where}: pose IDs and timestamps must be unique")
        ids.add(pose_id)
        timestamps.add(timestamp_ns)
        has_matrix = "rotation_matrix" in obj
        has_euler = any(key in obj for key in ("pan_deg", "tilt_deg", "roll_deg"))
        if has_matrix == has_euler:
            raise InterfaceError(f"{where}: supply exactly one of rotation_matrix or all Euler angles")
        if has_matrix:
            rotation = _validated_rotation(obj["rotation_matrix"], f"{where}.rotation_matrix")
            representation = "rotation_matrix"
        else:
            missing = [key for key in ("pan_deg", "tilt_deg", "roll_deg") if key not in obj]
            if missing:
                raise InterfaceError(f"{where}: missing Euler fields: {', '.join(missing)}")
            rotation = euler_world_to_sensor(
                _number(obj["pan_deg"], f"{where}.pan_deg"),
                _number(obj["tilt_deg"], f"{where}.tilt_deg"),
                _number(obj["roll_deg"], f"{where}.roll_deg"),
            )
            representation = "pan_tilt_roll"
        poses.append(Pose(pose_id, timestamp_ns, rotation, representation))
    if not poses:
        raise InterfaceError(f"{path}: pose stream contains no poses")
    poses.sort(key=lambda pose: pose.timestamp_ns)
    return header, poses


def warp_pixel(
    x: float,
    y: float,
    intrinsics: Intrinsics,
    rotation_world_to_sensor: Matrix,
    mode: str,
) -> dict[str, Any]:
    """Warp one pixel and return an auditable in-FOV/out-of-FOV result."""

    if mode not in ("world-to-sensor", "sensor-to-world"):
        raise InterfaceError(f"unsupported transform mode: {mode}")
    x = _number(x, "pixel.x")
    y = _number(y, "pixel.y")
    if not (0.0 <= x <= intrinsics.width - 1 and 0.0 <= y <= intrinsics.height - 1):
        raise InterfaceError("input pixel lies outside the declared camera image")
    ray = ((x - intrinsics.cx) / intrinsics.fx, (y - intrinsics.cy) / intrinsics.fy, 1.0)
    rotation = rotation_world_to_sensor if mode == "world-to-sensor" else _transpose(rotation_world_to_sensor)
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


def _load_events(path: Path, intrinsics: Intrinsics, pose_header: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = _read_jsonl(path)
    header = records[0]
    if header.get("schema") != EVENT_HEADER_SCHEMA or header.get("record_type") != "header":
        raise InterfaceError(f"{path}: first record must be a {EVENT_HEADER_SCHEMA} header")
    provenance = _validate_provenance(
        header,
        f"{path}:1",
        ("run_id", "candidate_id", "workload_id", "manifest_sha256"),
    )
    manifest_sha = provenance["manifest_sha256"]
    if len(manifest_sha) != 64 or any(character not in "0123456789abcdef" for character in manifest_sha):
        raise InterfaceError(f"{path}: provenance.manifest_sha256 must be lowercase SHA-256 hex")
    if header.get("camera_id") != intrinsics.camera_id or header.get("intrinsics_id") != intrinsics.intrinsics_id:
        raise InterfaceError(f"{path}: camera/intrinsics binding mismatch")
    if header.get("pose_stream_id") != pose_header.get("pose_stream_id"):
        raise InterfaceError(f"{path}: pose_stream_id binding mismatch")
    if header.get("coordinate_frame") not in ("world_reference_image", "sensor_image"):
        raise InterfaceError(f"{path}: unsupported coordinate_frame")
    accounting = header.get("transport_accounting")
    if not isinstance(accounting, dict):
        raise InterfaceError(f"{path}: transport_accounting must be an object")
    generated = _nonnegative_integer(accounting.get("generated"), f"{path}.generated")
    accepted = _nonnegative_integer(accounting.get("accepted"), f"{path}.accepted")
    retired = _nonnegative_integer(accounting.get("retired"), f"{path}.retired")
    source_overrun = _nonnegative_integer(accounting.get("source_overrun"), f"{path}.source_overrun")
    if generated != accepted + source_overrun:
        raise InterfaceError(f"{path}: generated must equal accepted + source_overrun")
    if retired > accepted:
        raise InterfaceError(f"{path}: retired cannot exceed accepted")

    events: list[dict[str, Any]] = []
    event_ids: set[str | int] = set()
    for line_number, event in enumerate(records[1:], 2):
        where = f"{path}:{line_number}"
        if event.get("schema") != EVENT_SCHEMA or event.get("record_type") != "event":
            raise InterfaceError(f"{where}: expected {EVENT_SCHEMA} event record")
        event_id = event.get("event_id")
        if isinstance(event_id, bool) or not isinstance(event_id, (str, int)) or event_id == "":
            raise InterfaceError(f"{where}.event_id must be a non-empty string or integer")
        if event_id in event_ids:
            raise InterfaceError(f"{where}: duplicate event_id {event_id!r}")
        event_ids.add(event_id)
        _nonnegative_integer(event.get("timestamp_ns"), f"{where}.timestamp_ns")
        _number(event.get("x"), f"{where}.x")
        _number(event.get("y"), f"{where}.y")
        if "polarity" in event:
            polarity = event["polarity"]
            if isinstance(polarity, bool) or not isinstance(polarity, int) or polarity not in (-1, 0, 1):
                raise InterfaceError(f"{where}.polarity must be one of -1, 0, or 1")
        if "pose_id" in event:
            _required_string(event, "pose_id", where)
        events.append(event)
    if retired != len(events):
        raise InterfaceError(f"{path}: retired accounting does not equal the event record count")
    return header, events


def _select_pose(
    event: dict[str, Any], poses: list[Pose], max_pose_age_ns: int | None
) -> tuple[Pose, str, int]:
    timestamp = event["timestamp_ns"]
    if "pose_id" in event:
        by_id = {pose.pose_id: pose for pose in poses}
        pose = by_id.get(event["pose_id"])
        if pose is None:
            raise InterfaceError(f"event {event['event_id']!r}: unknown pose_id {event['pose_id']!r}")
        selection = "explicit_pose_id"
    else:
        timestamps = [pose.timestamp_ns for pose in poses]
        index = bisect.bisect_right(timestamps, timestamp) - 1
        if index < 0:
            raise InterfaceError(f"event {event['event_id']!r}: no pose at or before timestamp")
        pose = poses[index]
        selection = "timestamp_zero_order_hold"
    age = timestamp - pose.timestamp_ns
    if age < 0:
        raise InterfaceError(f"event {event['event_id']!r}: explicit pose is in the future")
    if max_pose_age_ns is not None and age > max_pose_age_ns:
        raise InterfaceError(
            f"event {event['event_id']!r}: pose age {age} exceeds {max_pose_age_ns} ns"
        )
    return pose, selection, age


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    os.replace(temporary, path)


def transform_files(
    events_path: str | Path,
    intrinsics_path: str | Path,
    poses_path: str | Path,
    output_path: str | Path,
    summary_path: str | Path,
    mode: str,
    max_pose_age_ns: int | None = None,
) -> dict[str, Any]:
    """Validate, transform, and atomically write JSONL results and summary."""

    event_path = Path(events_path)
    intrinsics_path = Path(intrinsics_path)
    pose_path = Path(poses_path)
    output_path = Path(output_path)
    summary_path = Path(summary_path)
    if max_pose_age_ns is not None and max_pose_age_ns < 0:
        raise InterfaceError("max_pose_age_ns must be non-negative")
    input_paths = {event_path.resolve(), intrinsics_path.resolve(), pose_path.resolve()}
    if output_path.resolve() in input_paths or summary_path.resolve() in input_paths:
        raise InterfaceError("outputs must not overwrite inputs")
    if output_path.resolve() == summary_path.resolve():
        raise InterfaceError("JSONL output and summary paths must differ")

    intrinsics = load_intrinsics(intrinsics_path)
    pose_header, poses = load_pose_stream(pose_path, intrinsics)
    event_header, events = _load_events(event_path, intrinsics, pose_header)
    required_frame = "world_reference_image" if mode == "world-to-sensor" else "sensor_image"
    output_frame = "sensor_image" if mode == "world-to-sensor" else "world_reference_image"
    if event_header["coordinate_frame"] != required_frame:
        raise InterfaceError(f"mode {mode} requires input coordinate_frame {required_frame}")

    input_hashes = {
        "events_sha256": sha256_file(event_path),
        "intrinsics_sha256": sha256_file(intrinsics_path),
        "poses_sha256": sha256_file(pose_path),
    }
    provenance = event_header["provenance"]
    result_header = {
        "schema": RESULT_HEADER_SCHEMA,
        "record_type": "header",
        "mode": mode,
        "input_coordinate_frame": required_frame,
        "output_coordinate_frame": output_frame,
        "camera_id": intrinsics.camera_id,
        "intrinsics_id": intrinsics.intrinsics_id,
        "pose_stream_id": pose_header["pose_stream_id"],
        "provenance": {
            "run_id": provenance["run_id"],
            "candidate_id": provenance["candidate_id"],
            "workload_id": provenance["workload_id"],
            "manifest_sha256": provenance["manifest_sha256"],
            **input_hashes,
        },
    }
    output_records = [result_header]
    in_fov = 0
    reasons: dict[str, int] = {}
    selections = {"explicit_pose_id": 0, "timestamp_zero_order_hold": 0}
    max_observed_pose_age = 0
    for event in events:
        pose, selection, age = _select_pose(event, poses, max_pose_age_ns)
        selections[selection] += 1
        max_observed_pose_age = max(max_observed_pose_age, age)
        warped = warp_pixel(event["x"], event["y"], intrinsics, pose.rotation_world_to_sensor, mode)
        if warped["status"] == "in_fov":
            in_fov += 1
        else:
            reason = warped["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1
        output_records.append(
            {
                "schema": RESULT_SCHEMA,
                "record_type": "event",
                "event_id": event["event_id"],
                "timestamp_ns": event["timestamp_ns"],
                "polarity": event.get("polarity"),
                "pose_id": pose.pose_id,
                "pose_timestamp_ns": pose.timestamp_ns,
                "pose_age_ns": age,
                "pose_selection": selection,
                "input": {"frame": required_frame, "x": event["x"], "y": event["y"]},
                "output": {"frame": output_frame, **warped},
            }
        )

    accounting = event_header["transport_accounting"]
    coordinate_out = len(events) - in_fov
    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": mode,
        "provenance": result_header["provenance"],
        "counts": {
            "input_retired_events": len(events),
            "transformed_in_fov": in_fov,
            "coordinate_out_of_fov": coordinate_out,
            "coordinate_out_of_fov_reasons": dict(sorted(reasons.items())),
        },
        "pose_handling": {
            "selection_counts": selections,
            "maximum_observed_pose_age_ns": max_observed_pose_age,
            "maximum_allowed_pose_age_ns": max_pose_age_ns,
            "interpolation": "zero_order_hold",
        },
        "aer_transport_accounting": {
            "generated": accounting["generated"],
            "accepted": accounting["accepted"],
            "retired": accounting["retired"],
            "source_overrun": accounting["source_overrun"],
            "accepted_missing": accounting["accepted"] - accounting["retired"],
        },
        "accounting_invariants": {
            "generated_equals_accepted_plus_source_overrun": True,
            "retired_records_match_declared_retired": True,
            "retired_equals_in_fov_plus_coordinate_out_of_fov": len(events) == in_fov + coordinate_out,
        },
        "scope": {
            "model": "rotation-only pinhole rays; no translation, depth, distortion, or pose estimation",
            "ppa": "software demonstration only; not included in endpoint RTL or endpoint PPA",
            "loss_separation": "coordinate_out_of_fov is not AER transport loss",
        },
    }
    jsonl_text = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in output_records)
    _atomic_write(output_path, jsonl_text)
    _atomic_write(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary
