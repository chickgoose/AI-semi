"""Independent absolute-pixel oracle for the MC-WTB causal-core tests.

The oracle consumes plain dictionaries and applies only explicit integer
quarter-turn rules.  It has no dependency on the implementation under test.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


PASS_STATUS = "PASS_SYNTHETIC_CAUSAL_CORE"
FAIL_STATUS = "FAIL_SYNTHETIC_CAUSAL_CORE"


def load_oracle(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "redred.mc_wtb.causal_oracle/v1":
        raise AssertionError("unexpected causal oracle schema")
    if len(value.get("landmarks", [])) != 8 or len(value.get("events", [])) != 32:
        raise AssertionError("causal oracle must contain 8 landmarks and 32 events")
    return value


def oracle_events_by_id(oracle: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {entry["event_id"]: entry for entry in oracle["events"]}


def decoded_coordinates(result: dict[str, Any]) -> dict[int, list[int]]:
    return {
        entry["event_id"]: [entry["reference"]["x"], entry["reference"]["y"]]
        for entry in result["exact_event_ledger"]
    }


def evaluate_coordinates(
    coordinates: dict[int, list[int]], expected_events: list[dict[str, Any]]
) -> dict[str, Any]:
    expected_ids = [entry["event_id"] for entry in expected_events]
    if set(coordinates) != set(expected_ids):
        raise AssertionError("decoded and oracle event-ID sets differ")
    squared_error = 0
    exact = 0
    max_linf = 0
    by_landmark: dict[str, set[tuple[int, int]]] = {}
    for expected in expected_events:
        decoded = coordinates[expected["event_id"]]
        error_x = decoded[0] - expected["reference_xy"][0]
        error_y = decoded[1] - expected["reference_xy"][1]
        squared_error += error_x * error_x + error_y * error_y
        exact += error_x == 0 and error_y == 0
        max_linf = max(max_linf, abs(error_x), abs(error_y))
        by_landmark.setdefault(expected["landmark_id"], set()).add(tuple(decoded))
    return {
        "exact_reference_events": exact,
        "wrong_reference_events": len(expected_events) - exact,
        "pixel_sse": squared_error,
        "pixel_rmse": round(math.sqrt(squared_error / len(expected_events)), 12),
        "max_linf_error": max_linf,
        "decoded_unique_reference_pixels": len(
            {tuple(point) for point in coordinates.values()}
        ),
        "per_landmark_unique_pixel_counts": {
            landmark: len(points) for landmark, points in sorted(by_landmark.items())
        },
        "geometry_accept": exact == len(expected_events) and squared_error == 0,
    }


def evaluate_result(
    result: dict[str, Any], oracle: dict[str, Any], event_ids: list[int]
) -> dict[str, Any]:
    expected_by_id = oracle_events_by_id(oracle)
    expected_events = [expected_by_id[event_id] for event_id in event_ids]
    ledger = result["exact_event_ledger"]
    actual_ids = [entry["event_id"] for entry in ledger]
    if actual_ids != event_ids:
        raise AssertionError("ordered event ledger differs from causal input")
    coordinate_metrics = evaluate_coordinates(decoded_coordinates(result), expected_events)
    metadata_preserved = all(
        entry["sequence_index"] == expected["sequence_index"]
        and entry["timestamp_ns"] == expected["timestamp_ns"]
        and entry["pose_version"] == expected["true_pose"]
        and entry["polarity"] == expected["polarity"]
        and [entry["sensor"]["x"], entry["sensor"]["y"]]
        == expected["sensor_xy"]
        for entry, expected in zip(ledger, expected_events)
    )
    counts = result["exact_input_count_ledger"]
    event_count = len(event_ids)
    ledger_closed = all(
        counts[key] == event_count
        for key in (
            "declared_input_events",
            "parsed_input_events",
            "atomic_timestamp_pose_bindings",
            "sensor_fixed_assignments",
            "pose_compensated_assignments",
        )
    ) and all(
        counts[key] == 0 for key in ("dropped_events", "unaccounted_events")
    )
    sensor = result["representations"]["sensor_fixed"]
    compensated = result["representations"]["pose_compensated_reference"]
    accounting = result["logical_bit_accounting"]
    unique_counts = coordinate_metrics["per_landmark_unique_pixel_counts"]
    uniform_unique_count = (
        next(iter(unique_counts.values()))
        if len(set(unique_counts.values())) == 1
        else None
    )
    metrics = {
        "event_count": event_count,
        "positive_events": counts["positive_events"],
        "negative_events": counts["negative_events"],
        "metadata_preserved": metadata_preserved,
        "ledger_closed": ledger_closed,
        "pose_age_zero": all(entry["pose_age_ns"] == 0 for entry in ledger),
        "maximum_pose_age_ns": result["bottleneck_metrics"]
        ["5_timestamp_fidelity"]["maximum_pose_age_ns"],
        "changed_coordinate_count": sum(
            (entry["sensor"]["x"], entry["sensor"]["y"])
            != (entry["reference"]["x"], entry["reference"]["y"])
            for entry in ledger
        ),
        "sensor_packets": sensor["metrics"]["logical_occupancy_packet_count"],
        "compensated_packets": compensated["metrics"]
        ["logical_occupancy_packet_count"],
        "sensor_projected_bits": accounting["sensor_fixed"]
        ["occupancy_projection_total_bits"],
        "compensated_projected_bits": accounting["pose_compensated_reference"]
        ["occupancy_projection_total_bits"],
        "apparent_packet_delta": sensor["metrics"]
        ["logical_occupancy_packet_count"]
        - compensated["metrics"]["logical_occupancy_packet_count"],
        "per_landmark_unique_pixel_count": uniform_unique_count,
        "sensor_persistent_bins": sensor["metrics"]["occupied_tile_polarity_bins"],
        "compensated_persistent_bins": compensated["metrics"]
        ["occupied_tile_polarity_bins"],
        "compensated_same_tile_extra_events": compensated["metrics"]
        ["same_tile_extra_events"],
        "compensated_max_same_tile_multiplicity": compensated["metrics"]
        ["max_same_tile_multiplicity"],
        **coordinate_metrics,
    }
    return metrics


def assert_expected(metrics: dict[str, Any], expected: dict[str, Any]) -> None:
    mismatches = {
        key: {"expected": value, "actual": metrics.get(key)}
        for key, value in expected.items()
        if metrics.get(key) != value
    }
    if mismatches:
        raise AssertionError(f"causal expectation mismatch: {mismatches}")


def canonical_summary_bytes(
    arm_metrics: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> bytes:
    status = PASS_STATUS
    try:
        for arm, arm_expected in expected.items():
            assert_expected(arm_metrics[arm], arm_expected)
    except (AssertionError, KeyError):
        status = FAIL_STATUS
    summary = {
        "schema": "redred.mc_wtb.synthetic_causal_core_summary/v1",
        "status": status,
        "arms": {
            arm: {
                "exact_reference_events": metrics["exact_reference_events"],
                "pixel_sse": metrics["pixel_sse"],
                "sensor_packets": metrics["sensor_packets"],
                "compensated_packets": metrics["compensated_packets"],
                "geometry_accept": metrics["geometry_accept"],
            }
            for arm, metrics in sorted(arm_metrics.items())
        },
    }
    return (
        json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def _rotate(offset: tuple[int, int], quarter_turn: int) -> tuple[int, int]:
    x, y = offset
    turn = quarter_turn % 4
    if turn == 0:
        return x, y
    if turn == 1:
        return -y, x
    if turn == 2:
        return -x, -y
    return y, -x


def mutant_candidate_coordinates(
    expected_events: list[dict[str, Any]], mutation: str
) -> dict[int, list[int]]:
    """Produce representative faulty inverse outputs for oracle-sensitivity tests."""

    coordinates: dict[int, list[int]] = {}
    for event in expected_events:
        quarter_turn = int(event["true_pose"][1:])
        if mutation == "principal_point_sign":
            sensor_offset = (event["sensor_xy"][0] + 32, event["sensor_xy"][1] + 32)
            decoded_offset = _rotate(sensor_offset, -quarter_turn)
        else:
            sensor_offset = (event["sensor_xy"][0] - 32, event["sensor_xy"][1] - 32)
            if mutation == "matrix_direction_transpose":
                decoded_offset = _rotate(sensor_offset, quarter_turn)
            elif mutation == "pose_permutation":
                decoded_offset = _rotate(sensor_offset, -(quarter_turn + 1))
            else:
                raise ValueError(f"unknown oracle-sensitivity mutation: {mutation}")
        coordinates[event["event_id"]] = [
            32 + decoded_offset[0],
            32 + decoded_offset[1],
        ]
    return coordinates


__all__ = [
    "FAIL_STATUS",
    "PASS_STATUS",
    "assert_expected",
    "canonical_summary_bytes",
    "decoded_coordinates",
    "evaluate_coordinates",
    "evaluate_result",
    "load_oracle",
    "mutant_candidate_coordinates",
    "oracle_events_by_id",
]
