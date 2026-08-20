"""Deterministic source-bound six-arm controls companion generator."""

from __future__ import annotations

import bisect
import ctypes
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks.redred_uzh_mc_wtb_adapter import inspect as inspect_adapter
from benchmarks.redred_uzh_mc_wtb_controls import evaluate_records
from benchmarks.redred_uzh_shapes_pose_join import inspect as inspect_pose_join


PRODUCTION_STATUS = "PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED"
SYNTHETIC_STATUS = "PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE"
IMPLEMENTATION_STATUS = "PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED"
PROMOTION_STATUS = "HOLD_MC_WTB_REAL_DATA_BENEFIT"
PRODUCTION_MODE = "PRODUCTION_SOURCE_BOUND"
SYNTHETIC_MODE = "SYNTHETIC_FIXTURE"
PRODUCTION_RETIRE_PROVENANCE = "OBSERVED_ENDPOINT_RUN"
SYNTHETIC_RETIRE_PROVENANCE = "SYNTHETIC_TEST_FIXTURE"
ARM_NAMES = (
    "RAW", "SENSOR_FIXED", "MC_CORRECT", "MC_WRONG",
    "MC_DELAYED", "RETIRE_WARP",
)
AVAILABLE_FIVE_ARM_NAMES = ARM_NAMES[:-1]
GEOMETRY_STATUSES = (
    "in_fov", "outside_reference_image", "behind_reference", "invalid_distortion",
)
RECORD_SCHEMA = "redred.uzh_mc_wtb_controls.adapter_record/v2"
GENERATOR_SPEC_SCHEMA = "redred.uzh_mc_wtb_controls.generator_spec/v1"
APPROVED_GENERATOR_SPEC_SHA256_ENV = "REDRED_SIXARM_APPROVED_GENERATOR_SPEC_SHA256"
RECEIPT_SCHEMA = "redred.uzh_mc_wtb_controls.generator_receipt/v1"
COMPLETION_SCHEMA = "redred.uzh_mc_wtb_controls.generator_completion/v1"
RETIRE_STREAM_SCHEMA = "redred.uzh_mc_wtb_controls.retire_stream/v1"
RETIRE_RECORD_SCHEMA = "redred.uzh_mc_wtb_controls.retire_record/v1"
OUTPUT_NAME = "controls_six_arm.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETION_NAME = "COMPLETE.json"
FINAL_NAMES = frozenset({OUTPUT_NAME, RECEIPT_NAME, COMPLETION_NAME})

_PRODUCTION_SHA256 = {
    "pose_join_receipt": "85c182e1daa2f380dffa34a559ae2093835b1052c3d9d9a7f5a1f014a9974f87",
    "pose_join_completion": "c7692b20dc7d1f305a723cff695b9b794421fdfd39d6a021a17876c56d155756",
    "pose_join_events": "a49b7d813fde313bfbcc27526e337c7268ab11803a19898feee8f27afc576796",
    "pose_join_poses": "4461d867e8adc8daaeb089fc739613ee7c89ac2f32c825de561ba88ff83ca0c1",
    "pose_join_calibration": "bf718266f210e0bf7d64ff31b1fb4d125f905b0f67d6070976bdaf25ec450cdb",
    "join_spec": "04a81a809164556f744e55b075b94cbc7e2042ccb714e0e03fab8d4aa55a177e",
    "adapter_events": "a8a78cab40e8679cd98b50d78cda5df5c93e55ec100227862c0ad1b611bf599a",
    "adapter_receipt": "f34655799be9b29d82774cf3210f4f870eb396024cdf18f69bb4e48c6bda0197",
    "adapter_completion": "7919657165b5a44696ee34e5d5f1bdab22a21ee2f09f0f97078ae99284ac7b25",
}

_PRODUCTION_FIVE_ORACLE_HASHES = {
    "RAW": "9eff30df05a770cee5930929faa9816a5235cb4e8a6b29c185379e38535b03c2",
    "SENSOR_FIXED": "9009a43c69da4537169e8145935c777259196f04e248c2de85f1d5bb632c8771",
    "MC_CORRECT": "39529955f2565be311b44f45e3d5012a5906bcde7efa3d8de5ce44c07189a189",
    "MC_WRONG": "3ed987fa2fa239b3bd0ec1c520392dd4edff250de19e34ae3e7804d2878bde32",
    "MC_DELAYED": "9389abf2ecaba4d922511f153703fe1e9547f4da912c2c9c6c599a45747c3df3",
    "AVAILABLE_FIVE_COMBINED": "55566cdc189c3519f56ac8d648a74c7b33bb003067e0b1c53c62b404a89cfe2a",
}

_PRODUCTION_FIVE_STATUS_COUNTS = {
    "RAW": {"in_fov": 1100, "outside_reference_image": 0, "behind_reference": 0, "invalid_distortion": 0},
    "SENSOR_FIXED": {"in_fov": 1094, "outside_reference_image": 6, "behind_reference": 0, "invalid_distortion": 0},
    "MC_CORRECT": {"in_fov": 1094, "outside_reference_image": 6, "behind_reference": 0, "invalid_distortion": 0},
    "MC_WRONG": {"in_fov": 1094, "outside_reference_image": 6, "behind_reference": 0, "invalid_distortion": 0},
    "MC_DELAYED": {"in_fov": 1089, "outside_reference_image": 11, "behind_reference": 0, "invalid_distortion": 0},
}

_PRODUCTION_FIVE_OOF_IDS = {
    "RAW": [],
    "SENSOR_FIXED": [13_856_524, 13_856_654, 13_856_794, 13_857_092, 13_857_160, 13_857_171],
    "MC_CORRECT": [13_856_524, 13_856_654, 13_856_794, 13_857_092, 13_857_160, 13_857_171],
    "MC_WRONG": [13_856_285, 13_856_525, 13_856_993, 13_857_224, 13_857_294, 13_857_334],
    "MC_DELAYED": [13_856_285, 13_856_487, 13_856_525, 13_856_576, 13_856_993, 13_856_995, 13_857_174, 13_857_224, 13_857_288, 13_857_294, 13_857_334],
}

_PACKAGE_DIR = Path(__file__).resolve().parent
_PREREG_PATH = _PACKAGE_DIR.parent / "redred_uzh_mc_wtb_controls" / "preregistered.json"


class GeneratorFailure(RuntimeError):
    """An input authority, geometry invariant, or publication gate failed."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ) + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise GeneratorFailure(f"non-canonical JSON value: {error}") from error


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GeneratorFailure(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _json(data: bytes, where: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("ascii"), object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                GeneratorFailure(f"non-finite JSON number in {where}: {token}")
            ),
        )
    except GeneratorFailure:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise GeneratorFailure(f"invalid JSON in {where}: {error}") from error
    if not isinstance(value, dict):
        raise GeneratorFailure(f"{where} must contain an object")
    return value


def _jsonl(data: bytes, where: str) -> list[dict[str, Any]]:
    if not data or not data.endswith(b"\n") or b"\r" in data:
        raise GeneratorFailure(f"{where} must be nonempty LF-only JSONL")
    return [_json(line, f"{where}:{index}") for index, line in enumerate(data.splitlines(), 1)]


def _strict(value: Any, keys: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        actual = sorted(value) if isinstance(value, Mapping) else type(value).__name__
        raise GeneratorFailure(f"{where} keys differ: {actual}")
    return value


def _uint(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GeneratorFailure(f"{where} must be a non-negative integer")
    return value


def _digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise GeneratorFailure(f"{where} must be a lowercase SHA-256")
    return value


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeneratorFailure(f"{where} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise GeneratorFailure(f"{where} must be finite numeric data")
    return result


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.parts[0])
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise GeneratorFailure(f"symlink component is forbidden: {current}")


def _read_stable(path: Path, maximum: int, where: str) -> bytes:
    path = Path(path)
    _reject_symlink_components(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise GeneratorFailure(f"cannot open {where}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > maximum:
            raise GeneratorFailure(f"{where} is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after or len(data) != before.st_size or len(data) > maximum:
            raise GeneratorFailure(f"{where} changed during read or exceeds its limit")
        return data
    finally:
        os.close(descriptor)


def _load_spec(path: Path) -> tuple[dict[str, Any], bytes, bytes]:
    raw = _read_stable(path, 4 * 1024 * 1024, "frozen generator spec")
    spec = _json(raw, "generator_spec.json")
    prereg = _read_stable(_PREREG_PATH, 1024 * 1024, "controls preregistration")
    _strict(spec, {
        "schema", "mode", "parameter_set_id", "input_pins", "cohort",
        "geometry_contract", "delay_contract", "retire_contract",
        "controls_preregistration", "serialization", "claim_scope",
        "resource_limits",
    }, "generator spec")
    if spec.get("schema") != GENERATOR_SPEC_SCHEMA:
        raise GeneratorFailure("generator spec schema differs")
    if spec.get("mode") not in (PRODUCTION_MODE, SYNTHETIC_MODE):
        raise GeneratorFailure("generator spec mode differs")
    if spec["mode"] == PRODUCTION_MODE:
        approved_sha256 = os.environ.get(APPROVED_GENERATOR_SPEC_SHA256_ENV)
        if approved_sha256 is None:
            raise GeneratorFailure(
                f"production requires externally approved generator spec SHA-256 in "
                f"{APPROVED_GENERATOR_SPEC_SHA256_ENV}"
            )
        _digest(approved_sha256, "approved generator spec SHA-256")
        if approved_sha256 != _sha(raw):
            raise GeneratorFailure("generator spec bytes differ from external approval")
    if not isinstance(spec.get("parameter_set_id"), str) or not spec["parameter_set_id"]:
        raise GeneratorFailure("generator parameter_set_id is absent")
    controls = _strict(spec.get("controls_preregistration"), {"schema", "parameter_set_id", "raw_sha256"}, "controls preregistration pin")
    prereg_value = _json(prereg, "controls preregistration")
    if (
        controls["schema"] != "redred.uzh_mc_wtb_controls.preregistration/v2"
        or controls["parameter_set_id"] != "UZH-S2-CONTROLS-8X8-1MS-V2"
        or controls["raw_sha256"] != _sha(prereg)
        or prereg_value.get("parameter_set_id") != controls["parameter_set_id"]
    ):
        raise GeneratorFailure("controls preregistration differs from frozen spec")
    serialization = _strict(spec.get("serialization"), {"encoding", "json", "line_ending", "header_in_output"}, "serialization")
    if serialization != {"encoding": "ASCII", "json": "compact_sorted_keys", "line_ending": "LF", "header_in_output": False}:
        raise GeneratorFailure("generator serialization contract differs")
    geometry = _strict(spec.get("geometry_contract"), {"record_schema", "source_pose", "quaternion_order", "reference_timestamp_ns", "translation_policy", "pixel_rounding", "bounds"}, "geometry contract")
    if (
        geometry["record_schema"] != RECORD_SCHEMA
        or geometry["source_pose"] != "camera_to_world_T_WC"
        or geometry["quaternion_order"] != "xyzw"
        or geometry["translation_policy"] != "preserved_not_applied"
        or geometry["pixel_rounding"] != "floor(value_plus_0.5)"
        or geometry["bounds"] != "continuous_before_rounding"
    ):
        raise GeneratorFailure("generator geometry contract differs")
    delay_contract = _strict(spec.get("delay_contract"), {"mc_delayed_delta_ns", "lookup"}, "delay contract")
    if delay_contract["lookup"] != "occurrence_minus_delta_no_clamp":
        raise GeneratorFailure("delayed lookup contract differs")
    retire_contract = _strict(spec.get("retire_contract"), {"provenance_class", "source_timebase", "missing_policy", "receipt_sha256"}, "retire contract")
    expected_provenance = PRODUCTION_RETIRE_PROVENANCE if spec["mode"] == PRODUCTION_MODE else SYNTHETIC_RETIRE_PROVENANCE
    if retire_contract["provenance_class"] != expected_provenance or retire_contract["missing_policy"] != "fail_no_partial_output":
        raise GeneratorFailure("retire contract mode/provenance differs")
    _digest(retire_contract["receipt_sha256"], "retire receipt spec pin")
    limits = _strict(spec.get("resource_limits"), {"max_pose_bytes", "max_event_bytes", "max_adapter_bytes", "max_retire_bytes", "max_records"}, "resource limits")
    for name, value in limits.items():
        if _uint(value, f"resource_limits.{name}") < 1:
            raise GeneratorFailure("resource limit must be positive")
    _strict(spec.get("input_pins"), {"pose_join", "join_spec", "adapter", "retire_receipt"}, "input pins")
    cohort = _strict(spec.get("cohort"), {"record_count", "first_dataset_event_index", "last_dataset_event_index", "decimal_id_lf_sha256", "compact_id_array_lf_sha256", "polarity_0", "polarity_1", "timestamp_tie_extras"}, "cohort")
    for name in ("record_count", "first_dataset_event_index", "last_dataset_event_index", "polarity_0", "polarity_1", "timestamp_tie_extras"):
        _uint(cohort[name], f"cohort.{name}")
    _digest(cohort["decimal_id_lf_sha256"], "cohort decimal ID SHA")
    _digest(cohort["compact_id_array_lf_sha256"], "cohort compact ID SHA")
    return spec, raw, prereg


# Independent geometry path.  It deliberately imports no adapter geometry helper.
def _normalize(vector: Sequence[float], where: str) -> tuple[float, ...]:
    magnitude = math.sqrt(sum(component * component for component in vector))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise GeneratorFailure(f"{where} is a zero or non-finite vector")
    return tuple(component / magnitude for component in vector)


def _slerp(left_value: Sequence[float], right_value: Sequence[float], alpha: float) -> tuple[float, ...]:
    left = _normalize(left_value, "left quaternion")
    right = _normalize(right_value, "right quaternion")
    cosine = sum(a * b for a, b in zip(left, right))
    if cosine < 0.0:
        right = tuple(-value for value in right)
        cosine = -cosine
    cosine = min(1.0, max(-1.0, cosine))
    if cosine > 0.9995:
        return _normalize(tuple((1.0 - alpha) * left[i] + alpha * right[i] for i in range(4)), "slerp")
    theta = math.acos(cosine)
    sine = math.sin(theta)
    return _normalize(tuple(
        math.sin((1.0 - alpha) * theta) / sine * left[i]
        + math.sin(alpha * theta) / sine * right[i]
        for i in range(4)
    ), "slerp")


def _rotation(quaternion: Sequence[float]) -> tuple[tuple[float, float, float], ...]:
    x, y, z, w = _normalize(quaternion, "quaternion")
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def _transpose(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, float, float], ...]:
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def _matmul(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> tuple[tuple[float, float, float], ...]:
    return tuple(tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3)) for row in range(3))


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))  # type: ignore[return-value]


def _distort(x: float, y: float, calibration: Sequence[float]) -> tuple[float, float]:
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    radius2 = x * x + y * y
    radial = 1.0 + k1 * radius2 + k2 * radius2 * radius2 + k3 * radius2 * radius2 * radius2
    delta_x = 2.0 * p1 * x * y + p2 * (radius2 + 2.0 * x * x)
    delta_y = p1 * (radius2 + 2.0 * y * y) + 2.0 * p2 * x * y
    return x * radial + delta_x, y * radial + delta_y


def _undistort(xd: float, yd: float, calibration: Sequence[float]) -> tuple[float, float] | None:
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    x, y = xd, yd
    for _ in range(50):
        projected_x, projected_y = _distort(x, y, calibration)
        residual_x, residual_y = projected_x - xd, projected_y - yd
        radius2 = x * x + y * y
        radial = 1.0 + k1 * radius2 + k2 * radius2 * radius2 + k3 * radius2 * radius2 * radius2
        gradient = k1 + 2.0 * k2 * radius2 + 3.0 * k3 * radius2 * radius2
        dr_dx, dr_dy = 2.0 * x * gradient, 2.0 * y * gradient
        j00 = radial + x * dr_dx + 2.0 * p1 * y + 6.0 * p2 * x
        j01 = x * dr_dy + 2.0 * p1 * x + 2.0 * p2 * y
        j10 = y * dr_dx + 2.0 * p1 * x + 2.0 * p2 * y
        j11 = radial + y * dr_dy + 6.0 * p1 * y + 2.0 * p2 * x
        determinant = j00 * j11 - j01 * j10
        if not math.isfinite(determinant) or abs(determinant) < 1e-18:
            return None
        step_x = (j11 * residual_x - j01 * residual_y) / determinant
        step_y = (-j10 * residual_x + j00 * residual_y) / determinant
        x -= step_x
        y -= step_y
        if max(abs(step_x), abs(step_y), abs(residual_x), abs(residual_y)) < 2e-15:
            return x, y
    return None


def _raw_ray(x: int, y: int, calibration: Sequence[float]) -> tuple[float, float, float] | None:
    fx, fy, cx, cy = calibration[:4]
    inverse = _undistort((x - cx) / fx, (y - cy) / fy, calibration)
    if inverse is None:
        return None
    return _normalize((inverse[0], inverse[1], 1.0), "raw ray")  # type: ignore[return-value]


def _project(ray: Sequence[float] | None, calibration: Sequence[float], width: int, height: int) -> tuple[str, float | None, float | None, int | None, int | None]:
    if ray is None:
        return "invalid_distortion", None, None, None, None
    if ray[2] <= 0.0:
        return "behind_reference", None, None, None, None
    fx, fy, cx, cy = calibration[:4]
    xd, yd = _distort(ray[0] / ray[2], ray[1] / ray[2], calibration)
    x_float, y_float = fx * xd + cx, fy * yd + cy
    if not math.isfinite(x_float) or not math.isfinite(y_float):
        return "invalid_distortion", None, None, None, None
    if not (0.0 <= x_float <= width - 1 and 0.0 <= y_float <= height - 1):
        return "outside_reference_image", x_float, y_float, None, None
    return "in_fov", x_float, y_float, math.floor(x_float + 0.5), math.floor(y_float + 0.5)


def _raw_observation(x: int, y: int, ray: Sequence[float] | None) -> tuple[str, float | None, float | None, int | None, int | None]:
    """Classify a source pixel without numerically reprojecting its inverse ray.

    The pose-join authority has already established that ``x,y`` are valid sensor
    coordinates.  A successful inverse-distortion therefore makes RAW in-FOV by
    definition; round-trip noise at an image edge must not turn the observation
    into an escape.
    """
    if ray is None:
        return "invalid_distortion", None, None, None, None
    return "in_fov", float(x), float(y), x, y


def _q12(value: float | None) -> int | None:
    if value is None:
        return None
    scale = 10**12
    return (-1 if value < 0.0 else 1) * math.floor(abs(value) * scale + 0.5)


def _oracle_geometry(
    ray: Sequence[float] | None,
    projection: tuple[str, float | None, float | None, int | None, int | None],
    locality: tuple[float | None, float | None],
) -> dict[str, Any]:
    return {
        "geometry_status": projection[0],
        "reference_ray_q12": None if ray is None else [_q12(value) for value in ray],
        "projected_x_q12": _q12(projection[1]),
        "projected_y_q12": _q12(projection[2]),
        "projected_x_pixel": projection[3],
        "projected_y_pixel": projection[4],
        "locality_x_q12": _q12(locality[0]),
        "locality_y_q12": _q12(locality[1]),
    }


def _oracle_arm_row(
    arm: str,
    identity: Mapping[str, int],
    lookup_timestamp: int | None,
    bracket: Mapping[str, int] | None,
    ray: Sequence[float] | None,
    projection: tuple[str, float | None, float | None, int | None, int | None],
    locality: tuple[float | None, float | None],
) -> dict[str, Any]:
    return {
        "schema": "redred.uzh_sixarm_independent_oracle.arm/v1",
        "arm": arm,
        "dataset_event_index": identity["dataset_event_index"],
        "join_sequence_index": identity["join_sequence_index"],
        "timestamp_ns": identity["timestamp_ns"],
        "raw": {
            "x": identity["x_raw"],
            "y": identity["y_raw"],
            "polarity_01": identity["polarity_01"],
        },
        "pose_lookup_timestamp_ns": lookup_timestamp,
        "pose_bracket": None if bracket is None else dict(bracket),
        "geometry": _oracle_geometry(ray, projection, locality),
    }


def _available_five_oracle(rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for arm in AVAILABLE_FIVE_ARM_NAMES:
        payload = b"".join(_canonical(row) for row in rows[arm])
        artifacts[arm] = {"rows": len(rows[arm]), "bytes": len(payload), "sha256": _sha(payload)}
    combined: list[dict[str, Any]] = []
    for ordinal in range(len(rows["RAW"])):
        first = rows["RAW"][ordinal]
        combined.append({
            "schema": "redred.uzh_sixarm_independent_oracle.available_five/v1",
            "dataset_event_index": first["dataset_event_index"],
            "join_sequence_index": first["join_sequence_index"],
            "timestamp_ns": first["timestamp_ns"],
            "raw": first["raw"],
            "arms": {
                arm: {
                    **rows[arm][ordinal]["geometry"],
                    "pose_lookup_timestamp_ns": rows[arm][ordinal]["pose_lookup_timestamp_ns"],
                    "pose_bracket": rows[arm][ordinal]["pose_bracket"],
                }
                for arm in AVAILABLE_FIVE_ARM_NAMES
            },
        })
    payload = b"".join(_canonical(row) for row in combined)
    artifacts["AVAILABLE_FIVE_COMBINED"] = {"rows": len(combined), "bytes": len(payload), "sha256": _sha(payload)}
    oof_ids = {
        arm: [
            row["dataset_event_index"] for row in rows[arm]
            if row["geometry"]["geometry_status"] in ("outside_reference_image", "behind_reference")
        ]
        for arm in AVAILABLE_FIVE_ARM_NAMES
    }
    return {
        "canonical_artifacts": artifacts,
        "oof_dataset_event_indices": oof_ids,
        "oof_dataset_event_indices_sha256": {arm: _sha(_canonical(ids)) for arm, ids in oof_ids.items()},
    }


def _decimal_mapping(value: Any, names: Sequence[str], where: str) -> tuple[float, ...]:
    row = _strict(value, set(names), where)
    output: list[float] = []
    for name in names:
        token = row[name]
        if not isinstance(token, str):
            raise GeneratorFailure(f"{where}.{name} must be an exact decimal string")
        try:
            number = float(token)
        except ValueError as error:
            raise GeneratorFailure(f"{where}.{name} is not decimal") from error
        if not math.isfinite(number):
            raise GeneratorFailure(f"{where}.{name} is not finite")
        output.append(number)
    return tuple(output)


def _poses(payload: bytes) -> tuple[list[int], list[tuple[float, ...]], dict[str, Any]]:
    rows = _jsonl(payload, "poses.jsonl")
    header = rows[0]
    if header.get("record_type") != "header":
        raise GeneratorFailure("poses.jsonl lacks its header")
    times: list[int] = []
    quaternions: list[tuple[float, ...]] = []
    for ordinal, row in enumerate(rows[1:]):
        if row.get("record_type") != "pose" or row.get("source_pose_index") != ordinal:
            raise GeneratorFailure("pose indices are not contiguous")
        times.append(_uint(row.get("timestamp_ns"), "pose timestamp"))
        quaternions.append(_decimal_mapping(row.get("quaternion_exact_decimal"), ("qx", "qy", "qz", "qw"), "pose quaternion"))
    if len(times) < 2 or any(left >= right for left, right in zip(times, times[1:])):
        raise GeneratorFailure("pose timestamps are not strictly increasing")
    return times, quaternions, header


def _pose_at(times: Sequence[int], quaternions: Sequence[Sequence[float]], timestamp_ns: int) -> tuple[tuple[float, ...], dict[str, int]]:
    left = bisect.bisect_right(times, timestamp_ns) - 1
    right = left + 1
    if left < 0 or right >= len(times):
        raise GeneratorFailure(f"timestamp {timestamp_ns} lacks a closed pose bracket")
    numerator = timestamp_ns - times[left]
    denominator = times[right] - times[left]
    if numerator < 0 or numerator >= denominator:
        raise GeneratorFailure("pose bracket violates left <= t < right")
    quaternion = _slerp(quaternions[left], quaternions[right], numerator / denominator)
    return quaternion, {
        "left_source_pose_index": left,
        "right_source_pose_index": right,
        "left_timestamp_ns": times[left],
        "right_timestamp_ns": times[right],
        "alpha_numerator_ns": numerator,
        "alpha_denominator_ns": denominator,
    }


def _calibration(payload: bytes) -> tuple[tuple[float, ...], int, int]:
    row = _json(payload, "calibration.json")
    calibration = _decimal_mapping(
        row.get("parameters_exact_decimal"),
        ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"),
        "calibration parameters",
    )
    sensor = _strict(row.get("sensor"), {"width", "height"}, "calibration sensor")
    width, height = _uint(sensor["width"], "sensor width"), _uint(sensor["height"], "sensor height")
    if width < 1 or height < 1 or calibration[0] <= 0.0 or calibration[1] <= 0.0:
        raise GeneratorFailure("invalid calibration dimensions or focal length")
    return calibration, width, height


def _arm(status: str, ray: Sequence[float] | None, x: float | None, y: float | None, lookup: int | None) -> dict[str, Any]:
    return {
        "geometry_status": status,
        "reference_ray": None if ray is None else list(ray),
        "locality_x": x,
        "locality_y": y,
        "pose_lookup_timestamp_ns": lookup,
    }


def _claim_scope(mode: str, official: bool) -> dict[str, Any]:
    production = mode == PRODUCTION_MODE
    return {
        "official_uzh_source_input": official if production else False,
        "generated_artifact_official_uzh": False,
        "official_redred_traffic": False,
        "canonical_redred_traffic": False,
        "source_bound_pose_join": production,
        "source_bound_correct_adapter": production,
        "actual_retire_receipt_bound": production,
        "retire_timebase_mapping_validated": production,
        "six_controls_generated": True,
        "orientation_only": True,
        "translation_preserved_not_applied": True,
        "depth_or_plane_model_applied": False,
        "offline_future_bracket_slerp": True,
        "future_pose_lookahead_required": True,
        "causal_hardware_claimed": False,
        "clock_alignment_validated": False,
        "codec_evaluated": False,
        "bandwidth_measured": False,
        "compression_measured": False,
        "benefit_claimed": False,
        "rtl_or_ppa_evaluated": False,
    }


def _source_files(pose_join_dir: Path, adapter_dir: Path) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, Any]]:
    join = {
        name: _read_stable(pose_join_dir / name, maximum, f"pose-join {name}")
        for name, maximum in (
            ("calibration.json", 1024 * 1024),
            ("poses.jsonl", 64 * 1024 * 1024),
            ("events_pose_join.jsonl", 32 * 1024 * 1024),
            ("receipt.json", 8 * 1024 * 1024),
            ("COMPLETE.json", 4 * 1024 * 1024),
        )
    }
    adapter_receipt_raw = _read_stable(adapter_dir / "receipt.json", 8 * 1024 * 1024, "adapter receipt")
    adapter_receipt = _json(adapter_receipt_raw, "adapter receipt")
    artifact = adapter_receipt.get("artifact")
    if not isinstance(artifact, Mapping) or artifact.get("name") != "events_mc_wtb_adapter.jsonl":
        raise GeneratorFailure("adapter receipt does not bind the native artifact")
    adapter = {
        "receipt.json": adapter_receipt_raw,
        "COMPLETE.json": _read_stable(adapter_dir / "COMPLETE.json", 4 * 1024 * 1024, "adapter completion"),
        "events_mc_wtb_adapter.jsonl": _read_stable(adapter_dir / "events_mc_wtb_adapter.jsonl", 64 * 1024 * 1024, "adapter events"),
    }
    return join, adapter, adapter_receipt


def _retire_receipt(payload: bytes, events: Sequence[Mapping[str, Any]], epoch: str, spec: Mapping[str, Any]) -> tuple[dict[int, int], dict[str, Any]]:
    rows = _jsonl(payload, "retire receipt")
    header = _strict(
        rows[0],
        {"schema", "record_type", "provenance_class", "producer", "source_timebase", "retire_clock", "mapping_to_source_timebase", "record_count", "ordered_dataset_event_index_sha256"},
        "retire header",
    )
    if header["schema"] != RETIRE_STREAM_SCHEMA or header["record_type"] != "header":
        raise GeneratorFailure("retire stream header differs")
    retire_contract = spec["retire_contract"]
    if header["provenance_class"] != retire_contract["provenance_class"]:
        raise GeneratorFailure("retire provenance class differs from frozen spec")
    producer = _strict(header["producer"], {"implementation_id", "implementation_commit", "config_sha256", "run_id", "raw_run_artifact_sha256"}, "retire producer")
    for name in ("implementation_id", "implementation_commit", "run_id"):
        if not isinstance(producer[name], str) or not producer[name]:
            raise GeneratorFailure(f"retire producer {name} is absent")
    _digest(producer["config_sha256"], "retire config SHA")
    _digest(producer["raw_run_artifact_sha256"], "retire raw run SHA")
    source_timebase = _strict(header["source_timebase"], {"unit", "epoch"}, "retire source timebase")
    if source_timebase != {"unit": "ns", "epoch": epoch} or source_timebase != retire_contract["source_timebase"]:
        raise GeneratorFailure("retire source timebase differs")
    retire_clock = _strict(header["retire_clock"], {"clock_domain", "unit", "epoch"}, "retire clock")
    if any(not isinstance(retire_clock[name], str) or not retire_clock[name] for name in retire_clock):
        raise GeneratorFailure("retire clock identity is incomplete")
    mapping = _strict(header["mapping_to_source_timebase"], {"method", "evidence_sha256", "validated"}, "retire mapping")
    if not isinstance(mapping["method"], str) or not mapping["method"]:
        raise GeneratorFailure("retire mapping method is absent")
    _digest(mapping["evidence_sha256"], "retire mapping evidence SHA")
    expected_validated = spec["mode"] == PRODUCTION_MODE
    if mapping["validated"] is not expected_validated:
        raise GeneratorFailure("retire timebase validation flag differs from mode")
    ids = [event["dataset_event_index"] for event in events]
    decimal_hash = _sha(b"".join(f"{value}\n".encode("ascii") for value in ids))
    if (
        header["record_count"] != len(events)
        or header["ordered_dataset_event_index_sha256"] != decimal_hash
        or len(rows) != len(events) + 1
    ):
        raise GeneratorFailure("retire record count differs from source cohort")
    lookup: dict[int, int] = {}
    previous_retire = -1
    for ordinal, (record_value, event) in enumerate(zip(rows[1:], events)):
        record = _strict(record_value, {"schema", "record_type", "dataset_event_index", "join_sequence_index", "occurrence_timestamp_ns", "accepted_count", "retired_count", "retire_timestamp_ns"}, f"retire record {ordinal}")
        if record["schema"] != RETIRE_RECORD_SCHEMA or record["record_type"] != "retire":
            raise GeneratorFailure("retire record schema/type differs")
        dataset_index = _uint(record["dataset_event_index"], "retire dataset index")
        occurrence = _uint(record["occurrence_timestamp_ns"], "retire occurrence timestamp")
        retired = _uint(record["retire_timestamp_ns"], "retire timestamp")
        if dataset_index != event["dataset_event_index"] or record["join_sequence_index"] != ordinal or occurrence != event["timestamp_ns"]:
            raise GeneratorFailure("retire identity/occurrence differs from source order")
        if record["accepted_count"] != 1 or record["retired_count"] != 1 or retired < occurrence:
            raise GeneratorFailure("retire record is not accepted/retired exactly once after occurrence")
        if dataset_index in lookup or retired < previous_retire:
            raise GeneratorFailure("retire records are duplicate or reordered")
        lookup[dataset_index] = retired
        previous_retire = retired
    return lookup, {"provenance_class": header["provenance_class"], "producer": dict(producer), "source_timebase": dict(source_timebase), "retire_clock": dict(retire_clock), "mapping_to_source_timebase": dict(mapping)}


def _transform(pose_join_dir: Path, join_spec_path: Path, adapter_dir: Path, retire_receipt_path: Path, generator_spec_path: Path) -> tuple[bytes, dict[str, Any]]:
    spec, spec_raw, prereg_raw = _load_spec(generator_spec_path)
    try:
        join_check = inspect_pose_join(pose_join_dir, join_spec_path)
        adapter_check = inspect_adapter(adapter_dir, pose_join_dir, join_spec_path)
    except Exception as error:
        raise GeneratorFailure(f"source-bound upstream inspection failed: {error}") from error
    if join_check.get("status") != "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED":
        raise GeneratorFailure("pose-join scoped status is absent")
    if adapter_check.get("status") != "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED":
        raise GeneratorFailure("adapter scoped status is absent")
    if adapter_check.get("promotion_status") != "HOLD_MC_WTB_REAL_DATA_BENEFIT":
        raise GeneratorFailure("adapter promotion HOLD differs")
    official = join_check.get("official_uzh_source") is True and adapter_check.get("official_uzh_source_input") is True
    if spec["mode"] == PRODUCTION_MODE and not official:
        raise GeneratorFailure("production mode requires the qualified official-source chain")
    if spec["mode"] == SYNTHETIC_MODE and official:
        raise GeneratorFailure("synthetic fixture mode cannot consume or relabel official-source inputs")
    join, adapter, adapter_receipt = _source_files(pose_join_dir, adapter_dir)
    join_spec_raw = _read_stable(join_spec_path, 4 * 1024 * 1024, "bound join spec")
    retire_raw = _read_stable(retire_receipt_path, 32 * 1024 * 1024, "external retire receipt")
    event_rows = _jsonl(join["events_pose_join.jsonl"], "events_pose_join.jsonl")
    event_header, events = event_rows[0], event_rows[1:]
    if event_header.get("record_type") != "header" or not events:
        raise GeneratorFailure("pose-join event stream is absent")
    adapter_rows = _jsonl(adapter["events_mc_wtb_adapter.jsonl"], "adapter events")
    if adapter_rows[0].get("record_type") != "header" or len(adapter_rows) != len(events) + 1:
        raise GeneratorFailure("adapter cohort count differs from pose-join")
    adapter_events = adapter_rows[1:]
    times, quaternions, pose_header = _poses(join["poses.jsonl"])
    calibration, width, height = _calibration(join["calibration.json"])
    epoch_value = pose_header.get("timebase")
    if not isinstance(epoch_value, Mapping) or not isinstance(epoch_value.get("epoch"), str) or epoch_value.get("unit") != "ns":
        raise GeneratorFailure("pose source epoch is not explicit ns")
    join_receipt_sha = _sha(join["receipt.json"])
    actual_input_pins = {
        "pose_join": {
            "status": join_check["status"],
            "promotion_status": join_check.get("promotion_status"),
            "receipt": {"size_bytes": len(join["receipt.json"]), "sha256": join_receipt_sha},
            "completion": {"size_bytes": len(join["COMPLETE.json"]), "sha256": _sha(join["COMPLETE.json"])},
            "events": {"size_bytes": len(join["events_pose_join.jsonl"]), "sha256": _sha(join["events_pose_join.jsonl"])},
            "poses": {"size_bytes": len(join["poses.jsonl"]), "sha256": _sha(join["poses.jsonl"])},
            "calibration": {"size_bytes": len(join["calibration.json"]), "sha256": _sha(join["calibration.json"])},
        },
        "join_spec": {"size_bytes": len(join_spec_raw), "sha256": _sha(join_spec_raw)},
        "adapter": {
            "status": adapter_check["status"],
            "promotion_status": adapter_check["promotion_status"],
            "receipt": {"size_bytes": len(adapter["receipt.json"]), "sha256": _sha(adapter["receipt.json"])},
            "completion": {"size_bytes": len(adapter["COMPLETE.json"]), "sha256": _sha(adapter["COMPLETE.json"])},
            "events": {"size_bytes": len(adapter["events_mc_wtb_adapter.jsonl"]), "sha256": _sha(adapter["events_mc_wtb_adapter.jsonl"])},
        },
        "retire_receipt": {"size_bytes": len(retire_raw), "sha256": _sha(retire_raw)},
    }
    if spec.get("input_pins") != actual_input_pins:
        raise GeneratorFailure("runtime input bytes/status differ from frozen generator spec")
    if spec["mode"] == PRODUCTION_MODE:
        production_actual = {
            "pose_join_receipt": actual_input_pins["pose_join"]["receipt"]["sha256"],
            "pose_join_completion": actual_input_pins["pose_join"]["completion"]["sha256"],
            "pose_join_events": actual_input_pins["pose_join"]["events"]["sha256"],
            "pose_join_poses": actual_input_pins["pose_join"]["poses"]["sha256"],
            "pose_join_calibration": actual_input_pins["pose_join"]["calibration"]["sha256"],
            "join_spec": actual_input_pins["join_spec"]["sha256"],
            "adapter_events": actual_input_pins["adapter"]["events"]["sha256"],
            "adapter_receipt": actual_input_pins["adapter"]["receipt"]["sha256"],
            "adapter_completion": actual_input_pins["adapter"]["completion"]["sha256"],
        }
        if production_actual != _PRODUCTION_SHA256:
            raise GeneratorFailure("production source/adapter bytes differ from frozen canonical pins")
    if spec["retire_contract"]["receipt_sha256"] != _sha(retire_raw):
        raise GeneratorFailure("retire receipt differs from its frozen spec pin")
    retire_times, retire_meta = _retire_receipt(retire_raw, events, epoch_value["epoch"], spec)
    reference_timestamp = _uint(spec["geometry_contract"].get("reference_timestamp_ns"), "reference timestamp")
    selection = event_header.get("selection")
    if not isinstance(selection, Mapping) or selection.get("start_timestamp_ns_inclusive") != reference_timestamp:
        raise GeneratorFailure("generator reference time differs from source selection")
    delay = _uint(spec["delay_contract"].get("mc_delayed_delta_ns"), "MC_DELAYED delta")
    reference_q, reference_bracket = _pose_at(times, quaternions, reference_timestamp)
    reference_rotation = _rotation(reference_q)
    reference_inverse = _transpose(reference_rotation)
    output: list[dict[str, Any]] = []
    available_five_rows: dict[str, list[dict[str, Any]]] = {arm: [] for arm in AVAILABLE_FIVE_ARM_NAMES}
    status_counts = {arm: Counter() for arm in ARM_NAMES}
    correct_oof: list[int] = []
    delayed_brackets: list[dict[str, Any]] = []
    retire_brackets: list[dict[str, Any]] = []
    source_bindings: list[str] = []
    max_adapter_coordinate_error = 0.0
    rounded_mismatches = 0
    for ordinal, (event, native) in enumerate(zip(events, adapter_events)):
        if event.get("record_type") != "event" or native.get("record_type") != "event_disposition":
            raise GeneratorFailure("source/native record type differs")
        identity = {
            "dataset_event_index": event.get("dataset_event_index"),
            "join_sequence_index": event.get("join_sequence_index"),
            "timestamp_ns": event.get("timestamp_ns"),
            "x_raw": event.get("x"),
            "y_raw": event.get("y"),
            "polarity_01": event.get("polarity_01"),
        }
        for key in identity:
            _uint(identity[key], f"event {ordinal}.{key}")
        if identity["join_sequence_index"] != ordinal:
            raise GeneratorFailure("join sequence is not source order")
        native_source = native.get("source_event")
        expected_native = {
            "dataset_event_index": identity["dataset_event_index"],
            "join_sequence_index": ordinal,
            "timestamp_ns": identity["timestamp_ns"],
            "timestamp_seconds_lexeme": event.get("timestamp_seconds_lexeme"),
            "x_sensor": identity["x_raw"],
            "y_sensor": identity["y_raw"],
            "polarity_01": identity["polarity_01"],
        }
        if native_source != expected_native:
            raise GeneratorFailure("adapter occurrence identity differs from pose-join")
        timestamp = identity["timestamp_ns"]
        occurrence_q, occurrence_bracket = _pose_at(times, quaternions, timestamp)
        if event.get("bracket") != occurrence_bracket:
            raise GeneratorFailure("pose-join event bracket differs from independent lookup")
        delayed_timestamp = timestamp - delay
        delayed_q, delayed_bracket = _pose_at(times, quaternions, delayed_timestamp)
        retire_timestamp = retire_times[identity["dataset_event_index"]]
        retire_q, retire_bracket = _pose_at(times, quaternions, retire_timestamp)
        delayed_brackets.append({"dataset_event_index": identity["dataset_event_index"], "timestamp_ns": delayed_timestamp, "bracket": delayed_bracket})
        retire_brackets.append({"dataset_event_index": identity["dataset_event_index"], "timestamp_ns": retire_timestamp, "bracket": retire_bracket})
        raw_ray = _raw_ray(identity["x_raw"], identity["y_raw"], calibration)
        occurrence_matrix = _matmul(reference_inverse, _rotation(occurrence_q))
        correct_ray = None if raw_ray is None else _normalize(_matvec(occurrence_matrix, raw_ray), "correct ray")
        wrong_ray = None if raw_ray is None else _normalize(_matvec(_transpose(occurrence_matrix), raw_ray), "wrong ray")
        delayed_matrix = _matmul(reference_inverse, _rotation(delayed_q))
        delayed_ray = None if raw_ray is None else _normalize(_matvec(delayed_matrix, raw_ray), "delayed ray")
        retire_matrix = _matmul(reference_inverse, _rotation(retire_q))
        retire_ray = None if raw_ray is None else _normalize(_matvec(retire_matrix, raw_ray), "retire ray")
        raw_projection = _raw_observation(identity["x_raw"], identity["y_raw"], raw_ray)
        correct_projection = _project(correct_ray, calibration, width, height)
        wrong_projection = _project(wrong_ray, calibration, width, height)
        delayed_projection = _project(delayed_ray, calibration, width, height)
        retire_projection = _project(retire_ray, calibration, width, height)
        native_geometry = native.get("geometry")
        if not isinstance(native_geometry, Mapping) or native_geometry.get("status") != correct_projection[0]:
            raise GeneratorFailure("adapter status differs from independent correct geometry")
        native_x = native_geometry.get("x_reference_float_decimal")
        native_y = native_geometry.get("y_reference_float_decimal")
        if correct_projection[1] is None:
            if native_x is not None or native_y is not None:
                raise GeneratorFailure("adapter exposes coordinates for nonprojectable geometry")
        else:
            try:
                errors = (abs(float(native_x) - correct_projection[1]), abs(float(native_y) - correct_projection[2]))
            except (TypeError, ValueError) as error:
                raise GeneratorFailure("adapter projectable coordinates are absent") from error
            max_adapter_coordinate_error = max(max_adapter_coordinate_error, *errors)
            if max(errors) > 1e-9:
                raise GeneratorFailure("adapter differs from independent oracle by more than 1e-9 pixel")
        if native_geometry.get("x_reference") != correct_projection[3] or native_geometry.get("y_reference") != correct_projection[4]:
            rounded_mismatches += 1
        expected_disposition = (
            "WORLD_REFERENCE_EVENT" if correct_projection[0] == "in_fov" else
            "RAW_ESCAPE_GEOMETRIC_OOF" if correct_projection[0] in ("outside_reference_image", "behind_reference") else
            "RAW_BYPASS_INVALID_GEOMETRY"
        )
        if native.get("disposition") != expected_disposition:
            raise GeneratorFailure("adapter disposition differs from independent geometry")
        if correct_projection[0] != "in_fov":
            correct_oof.append(identity["dataset_event_index"])
        arms = {
            "RAW": _arm(raw_projection[0], raw_ray, float(identity["x_raw"]), float(identity["y_raw"]), None),
            "SENSOR_FIXED": _arm(correct_projection[0], correct_ray, float(identity["x_raw"]), float(identity["y_raw"]), timestamp),
            "MC_CORRECT": _arm(correct_projection[0], correct_ray, correct_projection[1], correct_projection[2], timestamp),
            "MC_WRONG": _arm(wrong_projection[0], wrong_ray, wrong_projection[1], wrong_projection[2], timestamp),
            "MC_DELAYED": _arm(delayed_projection[0], delayed_ray, delayed_projection[1], delayed_projection[2], delayed_timestamp),
            "RETIRE_WARP": _arm(retire_projection[0], retire_ray, retire_projection[1], retire_projection[2], retire_timestamp),
        }
        for arm, value in arms.items():
            status_counts[arm][value["geometry_status"]] += 1
        available_five_definitions = {
            "RAW": (None, None, raw_ray, raw_projection, (float(identity["x_raw"]), float(identity["y_raw"]))),
            "SENSOR_FIXED": (timestamp, occurrence_bracket, correct_ray, correct_projection, (float(identity["x_raw"]), float(identity["y_raw"]))),
            "MC_CORRECT": (timestamp, occurrence_bracket, correct_ray, correct_projection, (correct_projection[1], correct_projection[2])),
            "MC_WRONG": (timestamp, occurrence_bracket, wrong_ray, wrong_projection, (wrong_projection[1], wrong_projection[2])),
            "MC_DELAYED": (delayed_timestamp, delayed_bracket, delayed_ray, delayed_projection, (delayed_projection[1], delayed_projection[2])),
        }
        for arm, (lookup, bracket, ray, projection, locality) in available_five_definitions.items():
            available_five_rows[arm].append(_oracle_arm_row(arm, identity, lookup, bracket, ray, projection, locality))
        record = {"schema": RECORD_SCHEMA, **identity, "oracle_status": correct_projection[0], "oracle_reference_ray": None if correct_ray is None else list(correct_ray), "arms": arms}
        output.append(record)
        source_bindings.append(_sha(_canonical({"pose_join": event, "adapter": native})))
    if rounded_mismatches:
        raise GeneratorFailure("adapter rounded pixels differ from independent oracle")
    ids = [row["dataset_event_index"] for row in output]
    decimal_id_bytes = b"".join(f"{value}\n".encode("ascii") for value in ids)
    compact_id_bytes = _canonical(ids)
    polarity = Counter(row["polarity_01"] for row in output)
    timestamp_ties = sum(1 for left, right in zip(output, output[1:]) if left["timestamp_ns"] == right["timestamp_ns"])
    expected_cohort = {
        "record_count": len(output),
        "first_dataset_event_index": ids[0],
        "last_dataset_event_index": ids[-1],
        "decimal_id_lf_sha256": _sha(decimal_id_bytes),
        "compact_id_array_lf_sha256": _sha(compact_id_bytes),
        "polarity_0": polarity[0],
        "polarity_1": polarity[1],
        "timestamp_tie_extras": timestamp_ties,
    }
    if spec["cohort"] != expected_cohort:
        raise GeneratorFailure("cohort identity/ledger differs from frozen generator spec")
    available_five_oracle = _available_five_oracle(available_five_rows)
    available_five_status_counts = {
        arm: {status: status_counts[arm][status] for status in GEOMETRY_STATUSES}
        for arm in AVAILABLE_FIVE_ARM_NAMES
    }
    if spec["mode"] == PRODUCTION_MODE:
        if (
            spec["parameter_set_id"] != "UZH-SHAPES-ROTATION-SIXARM-8X8-1MS-DELAY4998186-V1"
            or reference_timestamp != 41_321_000_000
            or delay != 4_998_186
            or expected_cohort["record_count"] != 1100
            or expected_cohort["first_dataset_event_index"] != 13_856_250
            or expected_cohort["last_dataset_event_index"] != 13_857_349
            or expected_cohort["decimal_id_lf_sha256"] != "0eb870ed84539b786d8944330d0618509b7e331eab4ca4b4bba21bc51c3e44f0"
            or expected_cohort["compact_id_array_lf_sha256"] != "3bfedeb52763572d42d285b5b7483356f5156e535e657ecb67b0f1f7cf2a90ac"
            or expected_cohort["polarity_0"] != 674
            or expected_cohort["polarity_1"] != 426
            or expected_cohort["timestamp_tie_extras"] != 458
            or available_five_status_counts != _PRODUCTION_FIVE_STATUS_COUNTS
            or available_five_oracle["oof_dataset_event_indices"] != _PRODUCTION_FIVE_OOF_IDS
            or {name: value["sha256"] for name, value in available_five_oracle["canonical_artifacts"].items()} != _PRODUCTION_FIVE_ORACLE_HASHES
        ):
            raise GeneratorFailure("production cohort/five-arm oracle anchors differ")
    evaluation = evaluate_records(output)
    if evaluation.get("status") != "CONTROL_EVALUATION_ONLY_NO_BANDWIDTH_OR_BENEFIT_CLAIM":
        raise GeneratorFailure("controls evaluator status differs")
    for arm in ARM_NAMES:
        arm_result = evaluation["arms"][arm]
        if arm_result["geometry"]["denominator_events"] != len(output):
            raise GeneratorFailure("geometry denominator differs")
        for locality in ("persistent_map", "packet_key"):
            if arm_result["tile_locality_opportunity"][locality]["denominator_events"] != len(output):
                raise GeneratorFailure("locality denominator differs")
    status = PRODUCTION_STATUS if spec["mode"] == PRODUCTION_MODE else SYNTHETIC_STATUS
    claims = _claim_scope(spec["mode"], official)
    if spec.get("claim_scope") != claims:
        raise GeneratorFailure("claim scope differs from frozen generator spec")
    payload = b"".join(_canonical(row) for row in output)
    binding = {
        "pose_join": {"receipt_sha256": join_receipt_sha, "completion_sha256": _sha(join["COMPLETE.json"]), "events_sha256": _sha(join["events_pose_join.jsonl"]), "poses_sha256": _sha(join["poses.jsonl"]), "calibration_sha256": _sha(join["calibration.json"])},
        "join_spec": {"basename": join_spec_path.name, "raw_sha256": _sha(join_spec_raw)},
        "adapter": {"receipt_sha256": _sha(adapter["receipt.json"]), "completion_sha256": _sha(adapter["COMPLETE.json"]), "events_sha256": _sha(adapter["events_mc_wtb_adapter.jsonl"])},
        "retire_receipt": {"basename": retire_receipt_path.name, "size_bytes": len(retire_raw), "raw_sha256": _sha(retire_raw), **retire_meta},
        "generator_spec": {"basename": generator_spec_path.name, "size_bytes": len(spec_raw), "raw_sha256": _sha(spec_raw), "parameter_set_id": spec.get("parameter_set_id"), "mode": spec["mode"]},
        "controls_preregistration": {"basename": _PREREG_PATH.name, "size_bytes": len(prereg_raw), "raw_sha256": _sha(prereg_raw), "parameter_set_id": "UZH-S2-CONTROLS-8X8-1MS-V2"},
        "generator_implementation": {"module": "benchmarks/redred_uzh_mc_wtb_six_arm_generator/generator.py", "source_sha256": _sha(_read_stable(Path(__file__), 4 * 1024 * 1024, "generator implementation"))},
    }
    core = {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "evidence_class": "SOURCE_BOUND_EQUAL_ID_SIX_ARM_GEOMETRY_CONTROLS",
        "promotion_status": PROMOTION_STATUS,
        "input_binding": binding,
        "parameters": {"reference_timestamp_ns": reference_timestamp, "mc_delayed_delta_ns": delay, "reference_bracket": reference_bracket},
        "cohort": {**expected_cohort, "source_binding_stream_sha256": _sha(_canonical(source_bindings))},
        "arm_ledgers": {
            "status_counts": {arm: {status_name: status_counts[arm][status_name] for status_name in GEOMETRY_STATUSES} for arm in ARM_NAMES},
            "available_five_canonical_oracle": available_five_oracle,
            "geometry_denominator_per_arm": len(output),
            "locality_denominator_per_arm": len(output),
            "correct_geometry_crosscheck": {"max_adapter_coordinate_component_error_px": max_adapter_coordinate_error, "rounded_pixel_mismatches": rounded_mismatches, "translation_applied_events": 0, "correct_oof_dataset_event_indices": correct_oof, "correct_oof_dataset_event_indices_sha256": _sha(_canonical(correct_oof))},
            "delayed_lookup_bracket_stream_sha256": _sha(_canonical(delayed_brackets)),
            "retire_lookup_bracket_stream_sha256": _sha(_canonical(retire_brackets)),
            "conservation": {"source_events": len(output), "controls_records": len(output), "arms_per_record": 6, "arm_entries": len(output) * 6, "dropped": 0, "duplicate": 0, "reordered": 0, "missing_arms": 0},
        },
        "evaluator_result": evaluation,
        "claim_scope": claims,
    }
    return payload, core


def _write_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o444)
    except OSError as error:
        raise GeneratorFailure(f"cannot create staged artifact {path.name}: {error}") from error
    try:
        view = memoryview(payload)
        while view:
            try:
                count = os.write(descriptor, view)
            except OSError as error:
                raise GeneratorFailure(f"cannot write staged artifact {path.name}: {error}") from error
            if count <= 0:
                raise GeneratorFailure(f"short write for staged artifact {path.name}")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_noreplace(parent_fd: int, source_name: str, target_name: str) -> None:
    """Linux renameat2(RENAME_NOREPLACE), fail closed when unavailable."""
    library = ctypes.CDLL(None, use_errno=True)
    operation = getattr(library, "renameat2", None)
    if operation is None:
        raise GeneratorFailure("atomic no-replace renameat2 is unavailable")
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    if operation(parent_fd, os.fsencode(source_name), parent_fd, os.fsencode(target_name), 1) != 0:
        error_number = ctypes.get_errno()
        raise GeneratorFailure(f"atomic no-overwrite publication failed: {os.strerror(error_number)}")


def _publish(result_dir: Path, events_payload: bytes, core: dict[str, Any]) -> dict[str, Any]:
    result_dir = Path(result_dir)
    _reject_symlink_components(result_dir.parent)
    if result_dir.exists() or result_dir.is_symlink():
        raise GeneratorFailure("result path already exists")
    result_dir.parent.mkdir(parents=True, exist_ok=True)
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(result_dir.parent, parent_flags)
    except OSError as error:
        raise GeneratorFailure(f"cannot pin publication parent: {error}") from error
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".{result_dir.name}.sixarm-", dir=result_dir.parent))
    except Exception:
        os.close(parent_fd)
        raise
    try:
        receipt = {**core, "artifact": {"name": OUTPUT_NAME, "size_bytes": len(events_payload), "sha256": _sha(events_payload), "record_count": core["cohort"]["record_count"]}}
        receipt_payload = _canonical(receipt)
        completion = {"schema": COMPLETION_SCHEMA, "status": core["status"], "promotion_status": PROMOTION_STATUS, "artifacts": {OUTPUT_NAME: {"size_bytes": len(events_payload), "sha256": _sha(events_payload)}, RECEIPT_NAME: {"size_bytes": len(receipt_payload), "sha256": _sha(receipt_payload)}}}
        _write_file(staging / OUTPUT_NAME, events_payload)
        _write_file(staging / RECEIPT_NAME, receipt_payload)
        _write_file(staging / COMPLETION_NAME, _canonical(completion))
        os.chmod(staging, 0o555)
        if result_dir.exists() or result_dir.is_symlink():
            raise GeneratorFailure("result path appeared during publication")
        _rename_noreplace(parent_fd, staging.name, result_dir.name)
        os.fsync(parent_fd)
        return receipt
    except Exception:
        if staging.exists():
            os.chmod(staging, 0o700)
            shutil.rmtree(staging)
        raise
    finally:
        os.close(parent_fd)


def generate(pose_join_dir: Path, join_spec_path: Path, adapter_dir: Path, retire_receipt_path: Path, generator_spec_path: Path, result_dir: Path) -> dict[str, Any]:
    """Generate a deterministic six-arm package from five frozen inputs."""
    paths = tuple(Path(value) for value in (pose_join_dir, join_spec_path, adapter_dir, retire_receipt_path, generator_spec_path, result_dir))
    _validate_input_paths(paths[0], paths[1], paths[2], paths[3], paths[4], paths[5])
    events_payload, core = _transform(paths[0], paths[1], paths[2], paths[3], paths[4])
    receipt = _publish(paths[5], events_payload, core)
    checked = inspect(paths[5], paths[0], paths[1], paths[2], paths[3], paths[4])
    if checked["status"] != core["status"]:
        raise GeneratorFailure("post-publication source-bound inspection failed")
    return receipt


def _published(result_dir: Path) -> tuple[bytes, dict[str, Any]]:
    result_dir = Path(result_dir)
    _reject_symlink_components(result_dir)
    if not result_dir.is_dir() or result_dir.is_symlink() or {path.name for path in result_dir.iterdir()} != FINAL_NAMES:
        raise GeneratorFailure("published inventory differs")
    completion_raw = _read_stable(result_dir / COMPLETION_NAME, 4 * 1024 * 1024, "completion")
    completion = _strict(_json(completion_raw, "completion"), {"schema", "status", "promotion_status", "artifacts"}, "completion")
    if completion["schema"] != COMPLETION_SCHEMA or completion["promotion_status"] != PROMOTION_STATUS or completion["status"] not in (PRODUCTION_STATUS, SYNTHETIC_STATUS):
        raise GeneratorFailure("completion status differs")
    artifacts = _strict(completion["artifacts"], {OUTPUT_NAME, RECEIPT_NAME}, "completion artifacts")
    payloads: dict[str, bytes] = {}
    for name, maximum in ((OUTPUT_NAME, 64 * 1024 * 1024), (RECEIPT_NAME, 8 * 1024 * 1024)):
        identity = _strict(artifacts[name], {"size_bytes", "sha256"}, f"completion {name}")
        payload = _read_stable(result_dir / name, maximum, name)
        if len(payload) != identity["size_bytes"] or _sha(payload) != identity["sha256"]:
            raise GeneratorFailure(f"published {name} identity differs")
        payloads[name] = payload
    return payloads[OUTPUT_NAME], _json(payloads[RECEIPT_NAME], RECEIPT_NAME)


def _validate_input_paths(pose_join_dir: Path, join_spec_path: Path, adapter_dir: Path, retire_receipt_path: Path, generator_spec_path: Path, result_dir: Path) -> None:
    inputs = (pose_join_dir, join_spec_path, adapter_dir, retire_receipt_path, generator_spec_path)
    for path in inputs:
        _reject_symlink_components(path)
        if not path.exists():
            raise GeneratorFailure(f"required input is absent: {path}")
    for left_index, left in enumerate(inputs):
        for right in inputs[left_index + 1:]:
            try:
                if os.path.samefile(left, right):
                    raise GeneratorFailure("input authorities must not alias each other")
            except OSError as error:
                raise GeneratorFailure(f"cannot establish input identity: {error}") from error
    result_absolute = result_dir.absolute()
    for directory in (pose_join_dir.absolute(), adapter_dir.absolute()):
        try:
            if os.path.commonpath((str(result_absolute), str(directory))) == str(directory):
                raise GeneratorFailure("result path must not lie inside an input package")
        except ValueError as error:
            raise GeneratorFailure(f"cannot compare result/input paths: {error}") from error


def inspect(result_dir: Path, pose_join_dir: Path, join_spec_path: Path, adapter_dir: Path, retire_receipt_path: Path, generator_spec_path: Path) -> dict[str, Any]:
    """Recompute a package from every original authority; no source-free PASS."""
    _validate_input_paths(Path(pose_join_dir), Path(join_spec_path), Path(adapter_dir), Path(retire_receipt_path), Path(generator_spec_path), Path(result_dir))
    events_payload, receipt = _published(Path(result_dir))
    _strict(receipt, {"schema", "status", "evidence_class", "promotion_status", "input_binding", "parameters", "cohort", "arm_ledgers", "evaluator_result", "claim_scope", "artifact"}, "generator receipt")
    expected_payload, expected_core = _transform(Path(pose_join_dir), Path(join_spec_path), Path(adapter_dir), Path(retire_receipt_path), Path(generator_spec_path))
    if events_payload != expected_payload:
        raise GeneratorFailure("six-arm artifact differs from source-bound recomputation")
    artifact = receipt.get("artifact")
    expected_artifact = {"name": OUTPUT_NAME, "size_bytes": len(events_payload), "sha256": _sha(events_payload), "record_count": expected_core["cohort"]["record_count"]}
    if artifact != expected_artifact:
        raise GeneratorFailure("six-arm receipt artifact identity differs")
    for key, value in expected_core.items():
        if receipt.get(key) != value:
            raise GeneratorFailure(f"six-arm receipt differs from recomputation: {key}")
    return {
        "status": expected_core["status"],
        "promotion_status": PROMOTION_STATUS,
        "record_count": expected_core["cohort"]["record_count"],
        "official_uzh_source_input": expected_core["claim_scope"]["official_uzh_source_input"],
        "actual_retire_receipt_bound": expected_core["claim_scope"]["actual_retire_receipt_bound"],
        "artifact_sha256": _sha(events_payload),
        "receipt_sha256": _sha(_canonical(receipt)),
    }


__all__ = [
    "APPROVED_GENERATOR_SPEC_SHA256_ENV", "ARM_NAMES", "GeneratorFailure", "IMPLEMENTATION_STATUS", "PRODUCTION_STATUS",
    "PROMOTION_STATUS", "SYNTHETIC_STATUS", "generate", "inspect",
]
