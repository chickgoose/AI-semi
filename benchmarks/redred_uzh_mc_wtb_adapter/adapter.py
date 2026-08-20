"""Deterministic source-bound UZH pose-join disposition adapter.

This module deliberately stops before packetization.  It maps each verified
source occurrence to exactly one orientation-only geometry disposition while
preserving the complete raw event identity.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from benchmarks.redred_uzh_mc_wtb import geometry
from benchmarks.redred_uzh_shapes_pose_join import inspect as inspect_pose_join


STATUS = "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED"
EVIDENCE_CLASS = "SOURCE_BOUND_ORIENTATION_ONLY_EVENT_DISPOSITIONS"
PROMOTION_STATUS = "HOLD_MC_WTB_REAL_DATA_BENEFIT"

STREAM_SCHEMA = "redred.uzh_mc_wtb_adapter.event_stream/v1"
EVENT_SCHEMA = "redred.uzh_mc_wtb_adapter.event_disposition/v1"
RECEIPT_SCHEMA = "redred.uzh_mc_wtb_adapter.receipt/v1"
COMPLETION_SCHEMA = "redred.uzh_mc_wtb_adapter.completion/v1"

WORLD_REFERENCE_EVENT = "WORLD_REFERENCE_EVENT"
RAW_ESCAPE_GEOMETRIC_OOF = "RAW_ESCAPE_GEOMETRIC_OOF"
RAW_BYPASS_INVALID_GEOMETRY = "RAW_BYPASS_INVALID_GEOMETRY"
DISPOSITIONS = frozenset({
    WORLD_REFERENCE_EVENT,
    RAW_ESCAPE_GEOMETRIC_OOF,
    RAW_BYPASS_INVALID_GEOMETRY,
})

EVENTS_NAME = "events_mc_wtb_adapter.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETION_NAME = "COMPLETE.json"
FINAL_NAMES = frozenset({EVENTS_NAME, RECEIPT_NAME, COMPLETION_NAME})

SOURCE_CALIBRATION = "calibration.json"
SOURCE_POSES = "poses.jsonl"
SOURCE_EVENTS = "events_pose_join.jsonl"
SOURCE_RECEIPT = "receipt.json"
SOURCE_COMPLETION = "COMPLETE.json"
SOURCE_NAMES = (SOURCE_CALIBRATION, SOURCE_POSES, SOURCE_EVENTS, SOURCE_RECEIPT)


class AdapterFailure(RuntimeError):
    """Raised when source binding, geometry, or publication cannot be proven."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ) + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise AdapterFailure(f"value is not canonical JSON: {error}") from error


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterFailure(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _json(data: bytes, where: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("ascii"), object_pairs_hook=_no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterFailure(f"invalid JSON in {where}: {error}") from error
    if not isinstance(value, dict):
        raise AdapterFailure(f"{where} must contain a JSON object")
    return value


def _jsonl(data: bytes, where: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(data.splitlines(keepends=True), 1):
        if not raw.endswith(b"\n"):
            raise AdapterFailure(f"{where}:{line_number} lacks LF termination")
        rows.append(_json(raw[:-1], f"{where}:{line_number}"))
    if not rows:
        raise AdapterFailure(f"{where} is empty")
    return rows


def _strict(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise AdapterFailure(f"{where} keys differ: {actual}")
    return value


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterFailure(f"{where} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise AdapterFailure(f"{where} must be finite numeric data")
    return result


def _float_text(value: float, where: str) -> str:
    finite = _finite(value, where)
    return format(finite, ".17g")


def _uint(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdapterFailure(f"{where} must be a non-negative integer")
    return value


def _digest(value: object, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise AdapterFailure(f"{where} must be a lowercase SHA-256")
    return value


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    for component in parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise AdapterFailure(f"symlink path component is forbidden: {current}")


def _read_stable(path: Path, maximum: int, where: str) -> bytes:
    path = Path(path)
    _reject_symlink_components(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AdapterFailure(f"cannot open {where}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AdapterFailure(f"{where} must be a regular file")
        if before.st_size < 1 or before.st_size > maximum:
            raise AdapterFailure(f"{where} exceeds its byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise AdapterFailure(f"{where} exceeds its byte limit")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if identity_before != identity_after or total != before.st_size:
            raise AdapterFailure(f"{where} changed during read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o444)
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
    except OSError as error:
        raise AdapterFailure(f"cannot exclusively publish {path.name}: {error}") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _capture_pose_join(result_dir: Path, spec_path: Path) -> tuple[dict[str, Any], dict[str, bytes], bytes]:
    try:
        qualified = inspect_pose_join(result_dir, spec_path)
    except Exception as error:
        raise AdapterFailure(f"pose-join package/spec binding failed: {error}") from error
    if qualified.get("status") != "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED":
        raise AdapterFailure("pose-join package does not have the scoped completed status")
    if qualified.get("promotion_status") != "HOLD_MC_WTB_ADAPTER":
        raise AdapterFailure("pose-join package promotion status is not held")

    completion_raw = _read_stable(result_dir / SOURCE_COMPLETION, 4 * 1024 * 1024, "pose-join completion")
    completion = _strict(
        _json(completion_raw, SOURCE_COMPLETION),
        {"schema", "status", "promotion_status", "artifacts"},
        "pose-join completion",
    )
    if completion["status"] != "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED" or completion["promotion_status"] != "HOLD_MC_WTB_ADAPTER":
        raise AdapterFailure("captured pose-join completion status differs")
    artifacts = completion["artifacts"]
    if not isinstance(artifacts, dict):
        raise AdapterFailure("pose-join completion artifacts must be an object")
    maximums = {
        SOURCE_CALIBRATION: 1024 * 1024,
        SOURCE_POSES: 64 * 1024 * 1024,
        SOURCE_EVENTS: 32 * 1024 * 1024,
        SOURCE_RECEIPT: 8 * 1024 * 1024,
    }
    payloads: dict[str, bytes] = {}
    for name, maximum in maximums.items():
        row = artifacts.get(name)
        if not isinstance(row, dict) or set(row) != {"size_bytes", "sha256"}:
            raise AdapterFailure(f"pose-join completion lacks exact identity for {name}")
        size = _uint(row["size_bytes"], f"pose-join {name} size")
        digest = _digest(row["sha256"], f"pose-join {name} SHA")
        payload = _read_stable(result_dir / name, maximum, f"pose-join {name}")
        if len(payload) != size or _sha(payload) != digest:
            raise AdapterFailure(f"captured pose-join artifact differs: {name}")
        payloads[name] = payload
    spec_raw = _read_stable(spec_path, 4 * 1024 * 1024, "bound pose-join specification")
    receipt = _json(payloads[SOURCE_RECEIPT], SOURCE_RECEIPT)
    specification = receipt.get("specification_identity")
    if not isinstance(specification, dict) or specification.get("raw_sha256") != _sha(spec_raw):
        raise AdapterFailure("captured specification bytes differ from pose-join binding")
    binding = {
        "pose_join_status": qualified["status"],
        "pose_join_promotion_status": qualified["promotion_status"],
        "official_uzh_source": qualified.get("official_uzh_source") is True,
        "generated_pose_join_artifact_official_uzh": False,
        "pose_join_receipt_sha256": _sha(payloads[SOURCE_RECEIPT]),
        "pose_join_completion_sha256": _sha(completion_raw),
        "bound_spec_basename": spec_path.name,
        "bound_spec_raw_sha256": _sha(spec_raw),
        "source_artifacts": {
            name: {"size_bytes": len(payloads[name]), "sha256": _sha(payloads[name])}
            for name in (SOURCE_CALIBRATION, SOURCE_POSES, SOURCE_EVENTS)
        },
    }
    return binding, payloads, spec_raw


def _decimal_values(value: Any, names: tuple[str, ...], where: str) -> tuple[float, ...]:
    row = _strict(value, set(names), where)
    results: list[float] = []
    for name in names:
        token = row[name]
        if not isinstance(token, str):
            raise AdapterFailure(f"{where}.{name} must preserve an exact decimal string")
        try:
            number = float(token)
        except ValueError as error:
            raise AdapterFailure(f"{where}.{name} is not a decimal") from error
        if not math.isfinite(number):
            raise AdapterFailure(f"{where}.{name} must be finite")
        results.append(number)
    return tuple(results)


def _timed_pose(row: dict[str, Any], ordinal: int) -> geometry.TimedWorldCameraPose:
    if row.get("record_type") != "pose" or row.get("source_pose_index") != ordinal:
        raise AdapterFailure("pose records must preserve contiguous source indices")
    timestamp = _uint(row.get("timestamp_ns"), f"pose[{ordinal}].timestamp_ns")
    translation = _decimal_values(
        row.get("position_m_exact_decimal"), ("px", "py", "pz"),
        f"pose[{ordinal}].position",
    )
    quaternion = _decimal_values(
        row.get("quaternion_exact_decimal"), ("qx", "qy", "qz", "qw"),
        f"pose[{ordinal}].quaternion",
    )
    try:
        return geometry.TimedWorldCameraPose(
            timestamp,
            geometry.WorldCameraPose(translation, quaternion),
        )
    except geometry.GeometryError as error:
        raise AdapterFailure(f"invalid pose[{ordinal}]: {error}") from error


def _interpolate(
    poses: list[geometry.TimedWorldCameraPose], times: list[int], timestamp_ns: int,
) -> tuple[geometry.TimedWorldCameraPose, dict[str, int]]:
    left = bisect.bisect_right(times, timestamp_ns) - 1
    right = left + 1
    if left < 0 or right >= len(poses):
        raise AdapterFailure(f"timestamp {timestamp_ns} lacks a closed pose bracket")
    before, after = poses[left], poses[right]
    numerator = timestamp_ns - before.timestamp_ns
    denominator = after.timestamp_ns - before.timestamp_ns
    if numerator < 0 or denominator <= 0 or numerator >= denominator:
        raise AdapterFailure("pose bracket violates left <= t < right")
    try:
        interpolated = geometry.interpolate_world_camera_pose(before, after, timestamp_ns)
    except geometry.GeometryError as error:
        raise AdapterFailure(f"pose interpolation failed: {error}") from error
    return interpolated, {
        "left_source_pose_index": left,
        "right_source_pose_index": right,
        "left_timestamp_ns": before.timestamp_ns,
        "right_timestamp_ns": after.timestamp_ns,
        "alpha_numerator_ns": numerator,
        "alpha_denominator_ns": denominator,
    }


def _pose_value(value: geometry.TimedWorldCameraPose, bracket: dict[str, int]) -> dict[str, Any]:
    return {
        "timestamp_ns": value.timestamp_ns,
        "source_bracket": bracket,
        "translation_world_m_decimal": [
            _float_text(component, "pose translation")
            for component in value.pose.translation_world
        ],
        "quaternion_xyzw_decimal": [
            _float_text(component, "pose quaternion")
            for component in value.pose.quaternion_xyzw
        ],
        "interpolation": "linear_translation_shortest_arc_normalized_xyzw_slerp",
    }


def _calibration(payload: bytes) -> geometry.RadtanCalibration:
    row = _json(payload, SOURCE_CALIBRATION)
    parameters = _decimal_values(
        row.get("parameters_exact_decimal"),
        ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"),
        "calibration.parameters_exact_decimal",
    )
    sensor = row.get("sensor")
    if sensor != {"width": 240, "height": 180}:
        raise AdapterFailure("pose-join calibration sensor must be DAVIS240C 240x180")
    try:
        return geometry.RadtanCalibration(240, 180, *parameters)
    except geometry.GeometryError as error:
        raise AdapterFailure(f"invalid calibration: {error}") from error


def _claim_scope(official_input: bool) -> dict[str, Any]:
    return {
        "official_uzh_source_input": official_input,
        "generated_artifact_official_uzh": False,
        "official_redred_traffic": False,
        "canonical_redred_traffic": False,
        "orientation_only": True,
        "translation_preserved_not_applied": True,
        "depth_or_plane_model_applied": False,
        "offline_future_bracket_slerp": True,
        "future_pose_lookahead_required": True,
        "causal_hardware_claimed": False,
        "clock_alignment_validated": False,
        "raw_escape_is_disposition_only": True,
        "raw_packet_fifo_or_decoder_implemented": False,
        "controls_implemented": False,
        "codec_or_wire_benefit_claimed": False,
        "rtl_timing_power_or_ppa_claimed": False,
    }


def _transform(pose_join_dir: Path, spec_path: Path) -> tuple[bytes, dict[str, Any]]:
    binding, payloads, _ = _capture_pose_join(pose_join_dir, spec_path)
    calibration = _calibration(payloads[SOURCE_CALIBRATION])

    pose_rows = _jsonl(payloads[SOURCE_POSES], SOURCE_POSES)
    if pose_rows[0].get("record_type") != "header":
        raise AdapterFailure("poses.jsonl must begin with its verified header")
    poses = [_timed_pose(row, index) for index, row in enumerate(pose_rows[1:])]
    times = [pose.timestamp_ns for pose in poses]
    if len(poses) < 2 or any(left >= right for left, right in zip(times, times[1:])):
        raise AdapterFailure("pose timestamps must be strictly increasing")

    event_rows = _jsonl(payloads[SOURCE_EVENTS], SOURCE_EVENTS)
    source_header = event_rows[0]
    if source_header.get("record_type") != "header":
        raise AdapterFailure("events_pose_join.jsonl must begin with its verified header")
    source_events = event_rows[1:]
    selection = source_header.get("selection")
    if not isinstance(selection, dict):
        raise AdapterFailure("pose-join event header lacks selection")
    reference_timestamp = _uint(
        selection.get("start_timestamp_ns_inclusive"),
        "selection.start_timestamp_ns_inclusive",
    )
    reference_pose, reference_bracket = _interpolate(poses, times, reference_timestamp)
    reference_value = _pose_value(reference_pose, reference_bracket)

    claims = _claim_scope(binding["official_uzh_source"])
    header = {
        "schema": STREAM_SCHEMA,
        "record_type": "header",
        "status": STATUS,
        "evidence_class": EVIDENCE_CLASS,
        "promotion_status": PROMOTION_STATUS,
        "record_count": len(source_events),
        "source_binding": binding,
        "reference_pose": reference_value,
        "geometry_contract": {
            "source_pose": "UZH_xyzw_camera_to_world_T_WC",
            "reference": "selection_start_timestamp",
            "warp": "orientation_only_raw_radtan_current_sensor_to_reference",
            "continuous_bounds_before_rounding": True,
            "pixel_rounding": "floor(value_plus_0.5)",
            "invalid_geometry_is_not_geometric_oof": True,
        },
        "claim_scope": claims,
    }

    output: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    geometry_counts: Counter[str] = Counter()
    seen_ids: set[int] = set()
    maximum_translation_norm = 0.0
    for ordinal, source in enumerate(source_events):
        if source.get("record_type") != "event":
            raise AdapterFailure(f"source event row {ordinal} has the wrong record type")
        dataset_index = _uint(source.get("dataset_event_index"), f"event[{ordinal}].dataset_event_index")
        join_index = _uint(source.get("join_sequence_index"), f"event[{ordinal}].join_sequence_index")
        timestamp_ns = _uint(source.get("timestamp_ns"), f"event[{ordinal}].timestamp_ns")
        x = _uint(source.get("x"), f"event[{ordinal}].x")
        y = _uint(source.get("y"), f"event[{ordinal}].y")
        polarity = source.get("polarity_01")
        if join_index != ordinal or dataset_index in seen_ids or polarity not in (0, 1):
            raise AdapterFailure("source event identity/order/polarity is not exact-once")
        seen_ids.add(dataset_index)
        if x >= calibration.width or y >= calibration.height:
            raise AdapterFailure("source event lies outside DAVIS240C")

        current_pose, event_bracket = _interpolate(poses, times, timestamp_ns)
        if source.get("bracket") != event_bracket:
            raise AdapterFailure(f"source event {dataset_index} bracket differs from pose stream")
        causal = source.get("causal_pose")
        expected_causal = {
            "source_pose_index": event_bracket["left_source_pose_index"],
            "pose_timestamp_ns": event_bracket["left_timestamp_ns"],
            "age_ns": event_bracket["alpha_numerator_ns"],
        }
        if causal != expected_causal:
            raise AdapterFailure(f"source event {dataset_index} causal pose differs")
        try:
            relative = geometry.relative_geometry(reference_pose.pose, current_pose.pose)
            warped = geometry.warp_raw_sensor_to_reference(x, y, calibration, relative)
        except geometry.GeometryError as error:
            raise AdapterFailure(f"event {dataset_index} geometry contract failed: {error}") from error

        if warped.status == geometry.IN_FOV:
            disposition = WORLD_REFERENCE_EVENT
        elif warped.status in (geometry.OUTSIDE_REFERENCE_IMAGE, geometry.BEHIND_REFERENCE):
            disposition = RAW_ESCAPE_GEOMETRIC_OOF
        elif warped.status == geometry.INVALID_DISTORTION:
            disposition = RAW_BYPASS_INVALID_GEOMETRY
        else:
            raise AdapterFailure(f"event {dataset_index} returned unknown geometry status")
        disposition_counts[disposition] += 1
        geometry_counts[warped.status] += 1
        translation_norm = math.sqrt(sum(
            component * component
            for component in relative.translation_current_in_reference
        ))
        maximum_translation_norm = max(maximum_translation_norm, translation_norm)

        source_identity = {
            "dataset_event_index": dataset_index,
            "join_sequence_index": join_index,
            "timestamp_ns": timestamp_ns,
            "timestamp_seconds_lexeme": source.get("timestamp_seconds_lexeme"),
            "x_sensor": x,
            "y_sensor": y,
            "polarity_01": polarity,
        }
        output.append({
            "schema": EVENT_SCHEMA,
            "record_type": "event_disposition",
            "source_event": source_identity,
            "source_identity_sha256": _sha(_canonical(source_identity)),
            "source_pose_join": {
                "causal_pose": causal,
                "bracket": event_bracket,
            },
            "event_pose": _pose_value(current_pose, event_bracket),
            "disposition": disposition,
            "geometry": {
                "status": warped.status,
                "x_reference": warped.x_reference,
                "y_reference": warped.y_reference,
                "x_reference_float_decimal": None if warped.x_reference_float is None else _float_text(warped.x_reference_float, "x_reference_float"),
                "y_reference_float_decimal": None if warped.y_reference_float is None else _float_text(warped.y_reference_float, "y_reference_float"),
                "ray_z_decimal": None if warped.ray_z is None else _float_text(warped.ray_z, "ray_z"),
                "distortion_iterations": warped.distortion_iterations,
                "translation_current_in_reference_m_decimal": [
                    _float_text(component, "relative translation")
                    for component in relative.translation_current_in_reference
                ],
                "translation_applied_to_pixel_warp": False,
            },
        })

    if len(output) != len(source_events) or len(seen_ids) != len(source_events):
        raise AdapterFailure("one-disposition-per-source-event conservation failed")
    events_payload = b"".join(_canonical(row) for row in [header, *output])
    conservation = {
        "input_joined_events": len(source_events),
        "output_dispositions": len(output),
        "world_reference_events": disposition_counts[WORLD_REFERENCE_EVENT],
        "raw_escape_geometric_oof": disposition_counts[RAW_ESCAPE_GEOMETRIC_OOF],
        "raw_bypass_invalid_geometry": disposition_counts[RAW_BYPASS_INVALID_GEOMETRY],
        "dropped_events": 0,
        "duplicate_events": 0,
        "reordered_events": 0,
        "equation": "input_joined_events == world_reference_events + raw_escape_geometric_oof + raw_bypass_invalid_geometry",
    }
    if len(source_events) != sum(disposition_counts.values()):
        raise AdapterFailure("disposition conservation equation failed")
    core = {
        "source_binding": binding,
        "reference_pose": reference_value,
        "geometry_contract": header["geometry_contract"],
        "conservation": conservation,
        "geometry_status_counts": {
            geometry.IN_FOV: geometry_counts[geometry.IN_FOV],
            geometry.OUTSIDE_REFERENCE_IMAGE: geometry_counts[geometry.OUTSIDE_REFERENCE_IMAGE],
            geometry.BEHIND_REFERENCE: geometry_counts[geometry.BEHIND_REFERENCE],
            geometry.INVALID_DISTORTION: geometry_counts[geometry.INVALID_DISTORTION],
        },
        "maximum_retained_translation_norm_m_decimal": _float_text(
            maximum_translation_norm, "maximum translation norm"
        ),
        "claim_scope": claims,
    }
    return events_payload, core


def adapt(pose_join_dir: Path, spec_path: Path, result_dir: Path) -> dict[str, Any]:
    """Validate a bound pose-join package, transform it, and publish once."""

    pose_join_dir, spec_path, result_dir = map(Path, (pose_join_dir, spec_path, result_dir))
    events_payload, core = _transform(pose_join_dir, spec_path)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": STATUS,
        "evidence_class": EVIDENCE_CLASS,
        "promotion_status": PROMOTION_STATUS,
        **core,
        "artifact": {
            "name": EVENTS_NAME,
            "size_bytes": len(events_payload),
            "sha256": _sha(events_payload),
            "record_count": core["conservation"]["output_dispositions"],
        },
    }
    receipt_payload = _canonical(receipt)
    completion = {
        "schema": COMPLETION_SCHEMA,
        "status": STATUS,
        "promotion_status": PROMOTION_STATUS,
        "artifacts": {
            EVENTS_NAME: {"size_bytes": len(events_payload), "sha256": _sha(events_payload)},
            RECEIPT_NAME: {"size_bytes": len(receipt_payload), "sha256": _sha(receipt_payload)},
        },
    }
    completion_payload = _canonical(completion)

    _reject_symlink_components(result_dir.parent)
    try:
        result_dir.mkdir(mode=0o755, parents=False, exist_ok=False)
    except OSError as error:
        raise AdapterFailure(f"cannot create exclusive result directory: {error}") from error
    _write_exclusive(result_dir / EVENTS_NAME, events_payload)
    _write_exclusive(result_dir / RECEIPT_NAME, receipt_payload)
    _write_exclusive(result_dir / COMPLETION_NAME, completion_payload)
    inspected = inspect(result_dir, pose_join_dir, spec_path)
    if inspected["status"] != STATUS:
        raise AdapterFailure("post-publication inspection did not retain scoped status")
    return receipt


def _inspect_payloads(result_dir: Path) -> tuple[bytes, dict[str, Any]]:
    _reject_symlink_components(result_dir)
    try:
        info = result_dir.stat(follow_symlinks=False)
    except OSError as error:
        raise AdapterFailure(f"result directory is absent: {error}") from error
    if not stat.S_ISDIR(info.st_mode):
        raise AdapterFailure("result path must be a directory")
    names = {path.name for path in result_dir.iterdir()}
    if names != FINAL_NAMES:
        raise AdapterFailure(f"result artifact inventory differs: {sorted(names)}")
    completion = _strict(
        _json(_read_stable(result_dir / COMPLETION_NAME, 4 * 1024 * 1024, COMPLETION_NAME), COMPLETION_NAME),
        {"schema", "status", "promotion_status", "artifacts"}, "completion",
    )
    if completion["schema"] != COMPLETION_SCHEMA or completion["status"] != STATUS or completion["promotion_status"] != PROMOTION_STATUS:
        raise AdapterFailure("completion was changed or promoted")
    artifacts = _strict(completion["artifacts"], {EVENTS_NAME, RECEIPT_NAME}, "completion.artifacts")
    payloads: dict[str, bytes] = {}
    for name, maximum in ((EVENTS_NAME, 64 * 1024 * 1024), (RECEIPT_NAME, 4 * 1024 * 1024)):
        identity = _strict(artifacts[name], {"size_bytes", "sha256"}, f"completion.artifacts.{name}")
        size = _uint(identity["size_bytes"], f"completion.artifacts.{name}.size_bytes")
        digest = _digest(identity["sha256"], f"completion.artifacts.{name}.sha256")
        payload = _read_stable(result_dir / name, maximum, name)
        if len(payload) != size or _sha(payload) != digest:
            raise AdapterFailure(f"artifact differs from completion: {name}")
        payloads[name] = payload
    return payloads[EVENTS_NAME], _json(payloads[RECEIPT_NAME], RECEIPT_NAME)


def inspect(
    result_dir: Path,
    pose_join_dir: Path,
    spec_path: Path,
) -> dict[str, Any]:
    """Inspect and recompute an adapter package from its bound source and spec."""

    events_payload, receipt = _inspect_payloads(Path(result_dir))
    expected_receipt_keys = {
        "schema", "status", "evidence_class", "promotion_status",
        "source_binding", "reference_pose", "geometry_contract", "conservation",
        "geometry_status_counts", "maximum_retained_translation_norm_m_decimal",
        "claim_scope", "artifact",
    }
    _strict(receipt, expected_receipt_keys, "receipt")
    if (receipt["schema"], receipt["status"], receipt["evidence_class"], receipt["promotion_status"]) != (
        RECEIPT_SCHEMA, STATUS, EVIDENCE_CLASS, PROMOTION_STATUS,
    ):
        raise AdapterFailure("receipt was changed or promoted")
    official = receipt["source_binding"].get("official_uzh_source") is True
    if receipt["claim_scope"] != _claim_scope(official):
        raise AdapterFailure("claim scope was broadened")
    artifact = _strict(receipt["artifact"], {"name", "size_bytes", "sha256", "record_count"}, "receipt.artifact")
    if artifact != {
        "name": EVENTS_NAME,
        "size_bytes": len(events_payload),
        "sha256": _sha(events_payload),
        "record_count": receipt["conservation"].get("output_dispositions"),
    }:
        raise AdapterFailure("receipt artifact identity differs")

    rows = _jsonl(events_payload, EVENTS_NAME)
    header, records = rows[0], rows[1:]
    if header.get("schema") != STREAM_SCHEMA or header.get("record_type") != "header":
        raise AdapterFailure("event artifact header differs")
    if header.get("status") != STATUS or header.get("promotion_status") != PROMOTION_STATUS:
        raise AdapterFailure("event artifact header was promoted")
    if header.get("record_count") != len(records):
        raise AdapterFailure("event artifact header count differs")
    if header.get("source_binding") != receipt["source_binding"] or header.get("reference_pose") != receipt["reference_pose"] or header.get("geometry_contract") != receipt["geometry_contract"] or header.get("claim_scope") != receipt["claim_scope"]:
        raise AdapterFailure("event artifact header differs from receipt")

    counts: Counter[str] = Counter()
    seen: set[int] = set()
    previous_dataset_index = -1
    for ordinal, row in enumerate(records):
        if row.get("schema") != EVENT_SCHEMA or row.get("record_type") != "event_disposition":
            raise AdapterFailure(f"event disposition {ordinal} schema differs")
        disposition = row.get("disposition")
        if disposition not in DISPOSITIONS:
            raise AdapterFailure(f"event disposition {ordinal} is unknown")
        source = row.get("source_event")
        if not isinstance(source, dict) or source.get("join_sequence_index") != ordinal:
            raise AdapterFailure("adapter event sequence identity differs")
        dataset_index = _uint(source.get("dataset_event_index"), "adapter dataset_event_index")
        if dataset_index in seen or dataset_index <= previous_dataset_index:
            raise AdapterFailure("adapter dataset event identity is duplicate or reordered")
        seen.add(dataset_index)
        previous_dataset_index = dataset_index
        if row.get("source_identity_sha256") != _sha(_canonical(source)):
            raise AdapterFailure("adapter source identity digest differs")
        geometry_row = row.get("geometry")
        if not isinstance(geometry_row, dict):
            raise AdapterFailure("adapter geometry record is absent")
        status_value = geometry_row.get("status")
        expected = (
            WORLD_REFERENCE_EVENT if status_value == geometry.IN_FOV else
            RAW_ESCAPE_GEOMETRIC_OOF if status_value in (geometry.OUTSIDE_REFERENCE_IMAGE, geometry.BEHIND_REFERENCE) else
            RAW_BYPASS_INVALID_GEOMETRY if status_value == geometry.INVALID_DISTORTION else None
        )
        if disposition != expected:
            raise AdapterFailure("geometry status and disposition differ")
        counts[disposition] += 1

    conservation = receipt["conservation"]
    expected_counts = {
        WORLD_REFERENCE_EVENT: conservation.get("world_reference_events"),
        RAW_ESCAPE_GEOMETRIC_OOF: conservation.get("raw_escape_geometric_oof"),
        RAW_BYPASS_INVALID_GEOMETRY: conservation.get("raw_bypass_invalid_geometry"),
    }
    if len(records) != conservation.get("input_joined_events") or len(records) != conservation.get("output_dispositions"):
        raise AdapterFailure("adapter conservation total differs")
    if any(counts[name] != expected_counts[name] for name in DISPOSITIONS):
        raise AdapterFailure("adapter disposition counters differ")
    if any(conservation.get(name) != 0 for name in ("dropped_events", "duplicate_events", "reordered_events")):
        raise AdapterFailure("adapter loss/order counters were promoted")

    expected_payload, core = _transform(Path(pose_join_dir), Path(spec_path))
    if events_payload != expected_payload:
        raise AdapterFailure("adapter artifact differs from source-bound recomputation")
    for key, expected_value in core.items():
        if receipt.get(key) != expected_value:
            raise AdapterFailure(f"adapter receipt differs from recomputation: {key}")
    return {
        "status": STATUS,
        "promotion_status": PROMOTION_STATUS,
        "official_uzh_source_input": official,
        "record_count": len(records),
        "world_reference_events": counts[WORLD_REFERENCE_EVENT],
        "raw_escape_geometric_oof": counts[RAW_ESCAPE_GEOMETRIC_OOF],
        "raw_bypass_invalid_geometry": counts[RAW_BYPASS_INVALID_GEOMETRY],
        "receipt_sha256": _sha(_canonical(receipt)),
    }


__all__ = [
    "AdapterFailure",
    "COMPLETION_NAME",
    "DISPOSITIONS",
    "EVIDENCE_CLASS",
    "EVENTS_NAME",
    "PROMOTION_STATUS",
    "RAW_BYPASS_INVALID_GEOMETRY",
    "RAW_ESCAPE_GEOMETRIC_OOF",
    "RECEIPT_NAME",
    "STATUS",
    "WORLD_REFERENCE_EVENT",
    "adapt",
    "inspect",
]
