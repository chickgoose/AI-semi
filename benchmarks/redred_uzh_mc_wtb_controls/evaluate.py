#!/usr/bin/env python3
"""Evaluate geometry controls and tile-locality opportunity, never rate benefit.

The input boundary is deliberately adapter-neutral: one record owns one event
identity and contains all five arm outputs.  An adapter therefore cannot make
an arm look better by omitting a difficult event or by selecting an arm-local
cohort.  This module performs no importing, pose interpolation, warping,
serialization, codec evaluation, RTL work, or PPA estimation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "redred.uzh_mc_wtb_controls.evaluation/v1"
RECORD_SCHEMA = "redred.uzh_mc_wtb_controls.adapter_record/v1"
EVALUATION_STATUS = "CONTROL_EVALUATION_ONLY_NO_BANDWIDTH_OR_BENEFIT_CLAIM"

ARM_NAMES = (
    "SENSOR_FIXED",
    "MC_CORRECT",
    "MC_WRONG",
    "MC_DELAYED",
    "RETIRE_WARP",
)

IN_FOV = "in_fov"
OUTSIDE_REFERENCE_IMAGE = "outside_reference_image"
BEHIND_REFERENCE = "behind_reference"
INVALID_DISTORTION = "invalid_distortion"
GEOMETRY_STATUSES = frozenset(
    {IN_FOV, OUTSIDE_REFERENCE_IMAGE, BEHIND_REFERENCE, INVALID_DISTORTION}
)

TILE_WIDTH_PX = 8
TILE_HEIGHT_PX = 8
TILE_ORIGIN_X_PX = 0
TILE_ORIGIN_Y_PX = 0
TIME_BIN_NS = 1_000_000
TIME_ORIGIN_NS = 41_321_000_000
INVALID_ANGLE_PENALTY_DEGREES = 180.0

CORRECT_P50_MAX_DEGREES = 0.01
CORRECT_P99_MAX_DEGREES = 0.05
CORRECT_MAX_DEGREES = 0.10
WRONG_P95_ABSOLUTE_DELTA_MIN_DEGREES = 0.10
WRONG_P95_RATIO_MIN = 2.0
TIMING_INFORMATIVE_P95_MIN_DEGREES = 0.05
TIMING_P95_ABSOLUTE_DELTA_MIN_DEGREES = 0.02
TIMING_P95_RELATIVE_REDUCTION_MIN = 0.20

_EVENT_KEYS = {
    "schema",
    "event_id",
    "timestamp_ns",
    "polarity_01",
    "oracle_status",
    "oracle_reference_ray",
    "arms",
}
_ARM_KEYS = {
    "geometry_status",
    "reference_ray",
    "locality_x",
    "locality_y",
    "pose_lookup_timestamp_ns",
}


class EvaluationFailure(ValueError):
    """An adapter record violates the pre-registered evaluation contract."""


def _strict_mapping(value: Any, keys: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationFailure(f"{where} must be an object")
    missing = sorted(keys - set(value))
    extra = sorted(set(value) - keys)
    if missing or extra:
        raise EvaluationFailure(
            f"{where} keys mismatch: missing={missing} extra={extra}"
        )
    return value


def _integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvaluationFailure(f"{where} must be an integer >= {minimum}")
    return value


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationFailure(f"{where} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise EvaluationFailure(f"{where} must be a finite number")
    return result


def _vector3(value: Any, where: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise EvaluationFailure(f"{where} must contain exactly three numbers")
    vector = tuple(_finite(item, f"{where}[{index}]") for index, item in enumerate(value))
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0.0 or not math.isfinite(norm):
        raise EvaluationFailure(f"{where} must be a finite nonzero ray")
    return vector  # type: ignore[return-value]


def _optional_vector3(value: Any, where: str) -> tuple[float, float, float] | None:
    return None if value is None else _vector3(value, where)


def _optional_coordinate(value: Any, where: str) -> float | None:
    return None if value is None else _finite(value, where)


def _normalize_ray(ray: Sequence[float]) -> tuple[float, float, float]:
    norm = math.sqrt(sum(component * component for component in ray))
    return tuple(component / norm for component in ray)  # type: ignore[return-value]


def _angle_degrees(left: Sequence[float], right: Sequence[float]) -> float:
    a = _normalize_ray(left)
    b = _normalize_ray(right)
    dot = max(-1.0, min(1.0, sum(a[i] * b[i] for i in range(3))))
    cross = (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
    return math.degrees(math.atan2(math.sqrt(sum(v * v for v in cross)), dot))


def _nearest_rank(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise EvaluationFailure("percentile requires at least one admitted event")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _summarize(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "p99": _nearest_rank(values, 0.99),
        "max": max(values),
        "rms": math.sqrt(sum(value * value for value in values) / len(values)),
    }


def _validate_arm(
    value: Any,
    arm: str,
    event_id: int,
    timestamp_ns: int,
) -> dict[str, Any]:
    row = _strict_mapping(value, _ARM_KEYS, f"event {event_id} arm {arm}")
    status = row["geometry_status"]
    if status not in GEOMETRY_STATUSES:
        raise EvaluationFailure(f"event {event_id} arm {arm} has invalid geometry_status")
    ray = _optional_vector3(row["reference_ray"], f"event {event_id} arm {arm}.reference_ray")
    x = _optional_coordinate(row["locality_x"], f"event {event_id} arm {arm}.locality_x")
    y = _optional_coordinate(row["locality_y"], f"event {event_id} arm {arm}.locality_y")
    lookup = _integer(
        row["pose_lookup_timestamp_ns"],
        f"event {event_id} arm {arm}.pose_lookup_timestamp_ns",
    )

    if status == INVALID_DISTORTION:
        if ray is not None:
            raise EvaluationFailure(f"event {event_id} arm {arm} invalid distortion exposes a ray")
    elif ray is None:
        raise EvaluationFailure(f"event {event_id} arm {arm} valid geometry must expose a ray")

    coordinates_present = x is not None and y is not None
    if (x is None) != (y is None):
        raise EvaluationFailure(f"event {event_id} arm {arm} locality coordinates are partial")
    if arm == "SENSOR_FIXED":
        if not coordinates_present:
            raise EvaluationFailure("SENSOR_FIXED must retain raw sensor locality coordinates")
    elif status in (IN_FOV, OUTSIDE_REFERENCE_IMAGE):
        if not coordinates_present:
            raise EvaluationFailure(f"event {event_id} arm {arm} projected status needs coordinates")
    elif coordinates_present:
        raise EvaluationFailure(f"event {event_id} arm {arm} nonprojectable status exposes coordinates")

    if arm in ("SENSOR_FIXED", "MC_CORRECT", "MC_WRONG") and lookup != timestamp_ns:
        raise EvaluationFailure(f"event {event_id} arm {arm} must use occurrence timestamp")
    if arm == "MC_DELAYED" and lookup >= timestamp_ns:
        raise EvaluationFailure(f"event {event_id} MC_DELAYED must use a strictly earlier timestamp")
    if arm == "RETIRE_WARP" and lookup < timestamp_ns:
        raise EvaluationFailure(f"event {event_id} RETIRE_WARP precedes occurrence")

    return {
        "geometry_status": status,
        "reference_ray": ray,
        "locality_x": x,
        "locality_y": y,
        "pose_lookup_timestamp_ns": lookup,
    }


def _validate_record(value: Any, ordinal: int) -> dict[str, Any]:
    row = _strict_mapping(value, _EVENT_KEYS, f"record {ordinal}")
    if row["schema"] != RECORD_SCHEMA:
        raise EvaluationFailure(f"record {ordinal} schema mismatch")
    event_id = _integer(row["event_id"], f"record {ordinal}.event_id")
    timestamp_ns = _integer(row["timestamp_ns"], f"record {ordinal}.timestamp_ns")
    polarity = _integer(row["polarity_01"], f"record {ordinal}.polarity_01")
    if polarity not in (0, 1):
        raise EvaluationFailure(f"record {ordinal}.polarity_01 must be 0 or 1")
    oracle_status = row["oracle_status"]
    if oracle_status not in GEOMETRY_STATUSES:
        raise EvaluationFailure(f"record {ordinal}.oracle_status is invalid")
    oracle_ray = _optional_vector3(
        row["oracle_reference_ray"], f"record {ordinal}.oracle_reference_ray"
    )
    if oracle_status == INVALID_DISTORTION:
        if oracle_ray is not None:
            raise EvaluationFailure(f"record {ordinal} invalid oracle exposes a ray")
    elif oracle_ray is None:
        raise EvaluationFailure(f"record {ordinal} valid oracle must expose a ray")
    arms = _strict_mapping(row["arms"], set(ARM_NAMES), f"record {ordinal}.arms")
    checked_arms = {
        arm: _validate_arm(arms[arm], arm, event_id, timestamp_ns)
        for arm in ARM_NAMES
    }
    return {
        "event_id": event_id,
        "timestamp_ns": timestamp_ns,
        "polarity_01": polarity,
        "oracle_status": oracle_status,
        "oracle_reference_ray": oracle_ray,
        "arms": checked_arms,
    }


def _coordinate_key(row: Mapping[str, Any], arm: str) -> tuple[Any, ...]:
    output = row["arms"][arm]
    x = output["locality_x"]
    y = output["locality_y"]
    if x is None or y is None:
        # Per-event escape keys keep unprojectable events in N without creating
        # artificial concentration by pooling every failure into one tile.
        return ("escape", output["geometry_status"], row["event_id"])
    return (
        "tile",
        math.floor((x - TILE_ORIGIN_X_PX) / TILE_WIDTH_PX),
        math.floor((y - TILE_ORIGIN_Y_PX) / TILE_HEIGHT_PX),
    )


def _concentration(keys: Sequence[tuple[Any, ...]], admitted: int) -> dict[str, Any]:
    counts = Counter(keys)
    occupancies = list(counts.values())
    sum_squares = sum(count * count for count in occupancies)
    same_pairs = sum(count * (count - 1) for count in occupancies)
    pair_denominator = admitted * (admitted - 1)
    return {
        "denominator_events": admitted,
        "active_keys": len(counts),
        "events_per_key": {
            "p50": _nearest_rank(occupancies, 0.50),
            "p95": _nearest_rank(occupancies, 0.95),
            "max": max(occupancies),
        },
        "hhi_numerator": sum_squares,
        "hhi_denominator": admitted * admitted,
        "hhi": sum_squares / (admitted * admitted),
        "same_key_pair_numerator": same_pairs,
        "same_key_pair_denominator": pair_denominator,
        "same_key_pair_concentration": (
            same_pairs / pair_denominator if pair_denominator else 0.0
        ),
    }


def _locality(rows: Sequence[Mapping[str, Any]], arm: str) -> dict[str, Any]:
    spatial: list[tuple[Any, ...]] = []
    packet: list[tuple[Any, ...]] = []
    coordinate_events = 0
    escape_events = 0
    for row in rows:
        key = _coordinate_key(row, arm)
        if key[0] == "escape":
            escape_events += 1
        else:
            coordinate_events += 1
        spatial_key = (row["polarity_01"], *key)
        time_bin = math.floor((row["timestamp_ns"] - TIME_ORIGIN_NS) / TIME_BIN_NS)
        spatial.append(spatial_key)
        packet.append((time_bin, *spatial_key))
    return {
        "coordinate_events": coordinate_events,
        "escape_events": escape_events,
        "persistent_map": _concentration(spatial, len(rows)),
        "packet_key": _concentration(packet, len(rows)),
    }


def _geometry(rows: Sequence[Mapping[str, Any]], arm: str) -> dict[str, Any]:
    errors: list[float] = []
    status_counts = Counter()
    invalid_or_missing = 0
    for row in rows:
        output = row["arms"][arm]
        status_counts[output["geometry_status"]] += 1
        oracle_ray = row["oracle_reference_ray"]
        arm_ray = output["reference_ray"]
        if oracle_ray is None or arm_ray is None:
            errors.append(INVALID_ANGLE_PENALTY_DEGREES)
            invalid_or_missing += 1
        else:
            errors.append(_angle_degrees(arm_ray, oracle_ray))
    summary = _summarize(errors)
    correct_gate = (
        invalid_or_missing == 0
        and summary["p50"] <= CORRECT_P50_MAX_DEGREES
        and summary["p99"] <= CORRECT_P99_MAX_DEGREES
        and summary["max"] <= CORRECT_MAX_DEGREES
    )
    return {
        "denominator_events": len(rows),
        "penalized_error_events": invalid_or_missing,
        "invalid_penalty_degrees": INVALID_ANGLE_PENALTY_DEGREES,
        "status_counts": {status: status_counts[status] for status in sorted(GEOMETRY_STATUSES)},
        "angular_error_degrees": summary,
        "meets_correct_geometry_gate": correct_gate,
    }


def _cross_angle_summary(
    rows: Sequence[Mapping[str, Any]], left_arm: str, right_arm: str
) -> dict[str, float]:
    values: list[float] = []
    for row in rows:
        left = row["arms"][left_arm]["reference_ray"]
        right = row["arms"][right_arm]["reference_ray"]
        values.append(
            INVALID_ANGLE_PENALTY_DEGREES
            if left is None or right is None
            else _angle_degrees(left, right)
        )
    return _summarize(values)


def _geometry_control_gate(
    rows: Sequence[Mapping[str, Any]], arm_results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    correct_p95 = arm_results["MC_CORRECT"]["angular_error_degrees"]["p95"]
    wrong_p95 = arm_results["MC_WRONG"]["angular_error_degrees"]["p95"]
    wrong_ratio = None if correct_p95 == 0.0 else wrong_p95 / correct_p95
    ratio_gate = (
        wrong_p95 > 0.0 if wrong_ratio is None else wrong_ratio >= WRONG_P95_RATIO_MIN
    )
    wrong_valid = arm_results["MC_WRONG"]["penalized_error_events"] == 0
    wrong_identified = (
        wrong_valid
        and wrong_p95 - correct_p95 >= WRONG_P95_ABSOLUTE_DELTA_MIN_DEGREES
        and ratio_gate
    )

    timing: dict[str, Any] = {}
    all_timing_identified = True
    all_timing_informative = True
    for arm in ("MC_DELAYED", "RETIRE_WARP"):
        separation = _cross_angle_summary(rows, "MC_CORRECT", arm)
        control_p95 = arm_results[arm]["angular_error_degrees"]["p95"]
        control_valid = arm_results[arm]["penalized_error_events"] == 0
        informative = (
            control_valid
            and separation["p95"] >= TIMING_INFORMATIVE_P95_MIN_DEGREES
        )
        identified = (
            informative
            and control_p95 - correct_p95 >= TIMING_P95_ABSOLUTE_DELTA_MIN_DEGREES
            and correct_p95 <= (1.0 - TIMING_P95_RELATIVE_REDUCTION_MIN) * control_p95
        )
        all_timing_informative = all_timing_informative and informative
        all_timing_identified = all_timing_identified and identified
        timing[arm] = {
            "correct_to_control_ray_separation_degrees": separation,
            "all_rays_valid": control_valid,
            "informative": informative,
            "identified": identified,
        }

    primary_correct = (
        arm_results["SENSOR_FIXED"]["meets_correct_geometry_gate"]
        and arm_results["MC_CORRECT"]["meets_correct_geometry_gate"]
    )
    if not primary_correct:
        status = "FAIL_PRIMARY_GEOMETRY"
    elif not wrong_identified:
        status = "HOLD_WRONG_CONTROL_NOT_IDENTIFIED"
    elif not all_timing_informative:
        status = "HOLD_TIMING_CONTROLS_UNINFORMATIVE"
    elif not all_timing_identified:
        status = "HOLD_TIMING_CONTROLS_NOT_IDENTIFIED"
    else:
        status = "PASS_GEOMETRY_CONTROLS_ONLY"
    return {
        "status": status,
        "sensor_fixed_and_mc_correct_meet_absolute_gate": primary_correct,
        "mc_wrong": {
            "p95_absolute_delta_degrees": wrong_p95 - correct_p95,
            "p95_ratio_to_correct": wrong_ratio,
            "p95_ratio_is_unbounded_from_zero_correct_error": (
                wrong_ratio is None and wrong_p95 > 0.0
            ),
            "all_rays_valid": wrong_valid,
            "identified": wrong_identified,
        },
        "timing_controls": timing,
    }


def evaluate_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate one frozen equal-ID cohort under the v1 pre-registration."""

    checked = [_validate_record(value, index) for index, value in enumerate(records)]
    if not checked:
        raise EvaluationFailure("at least one admitted event is required")
    prior_id = -1
    prior_timestamp = -1
    for ordinal, row in enumerate(checked):
        if row["event_id"] <= prior_id:
            raise EvaluationFailure(
                f"record {ordinal} event IDs must be unique and strictly increasing"
            )
        if row["timestamp_ns"] < prior_timestamp:
            raise EvaluationFailure(
                f"record {ordinal} timestamps must be nondecreasing in event order"
            )
        prior_id = row["event_id"]
        prior_timestamp = row["timestamp_ns"]

    arm_results = {
        arm: {
            "geometry": _geometry(checked, arm),
            "tile_locality_opportunity": _locality(checked, arm),
        }
        for arm in ARM_NAMES
    }
    geometry_views = {arm: arm_results[arm]["geometry"] for arm in ARM_NAMES}
    event_id_bytes = b"".join(
        f"{row['event_id']}\n".encode("ascii") for row in checked
    )
    return {
        "schema": SCHEMA,
        "status": EVALUATION_STATUS,
        "cohort": {
            "admitted_event_count": len(checked),
            "first_event_id": checked[0]["event_id"],
            "last_event_id": checked[-1]["event_id"],
            "ordered_event_id_sha256": hashlib.sha256(event_id_bytes).hexdigest(),
            "ordered_event_id_hash_grammar": "ascii_decimal_event_id_plus_lf_per_record",
            "arm_names": list(ARM_NAMES),
            "equal_event_ids_by_construction": True,
        },
        "pre_registered_parameters": {
            "tile_width_px": TILE_WIDTH_PX,
            "tile_height_px": TILE_HEIGHT_PX,
            "tile_origin_x_px": TILE_ORIGIN_X_PX,
            "tile_origin_y_px": TILE_ORIGIN_Y_PX,
            "tile_boundary": "half_open_floor",
            "time_bin_ns": TIME_BIN_NS,
            "time_origin_ns": TIME_ORIGIN_NS,
            "time_assignment": "occurrence_timestamp_for_every_arm",
        },
        "arms": arm_results,
        "geometry_control_gate": _geometry_control_gate(checked, geometry_views),
        "claim_scope": {
            "geometry_control_evaluation": True,
            "tile_locality_opportunity": True,
            "bandwidth_measured": False,
            "compression_measured": False,
            "benefit_claimed": False,
            "codec_evaluated": False,
            "rtl_or_ppa_evaluated": False,
        },
    }


def load_records_jsonl(path: Path) -> list[Mapping[str, Any]]:
    """Load strict JSONL for adapters that do not call the in-memory API."""

    payload = Path(path).read_bytes()
    if not payload or not payload.endswith(b"\n") or b"\r" in payload:
        raise EvaluationFailure("adapter JSONL must be nonempty LF-terminated bytes")
    rows: list[Mapping[str, Any]] = []
    for line_number, raw in enumerate(payload.splitlines(), 1):
        try:
            value = json.loads(
                raw.decode("ascii"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_number,
            )
        except EvaluationFailure:
            raise
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise EvaluationFailure(f"invalid adapter JSONL line {line_number}: {error}") from error
        if not isinstance(value, Mapping):
            raise EvaluationFailure(f"adapter JSONL line {line_number} is not an object")
        rows.append(value)
    return rows


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationFailure(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_number(text: str) -> Any:
    raise EvaluationFailure(f"non-finite JSON number is forbidden: {text}")


__all__ = [
    "ARM_NAMES",
    "EVALUATION_STATUS",
    "EvaluationFailure",
    "RECORD_SCHEMA",
    "evaluate_records",
    "load_records_jsonl",
]
