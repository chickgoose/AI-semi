"""Fail-closed evaluator for the MC-WTB motion metric-v3 assay.

The primary is a same-reference, same-polarity angular nearest-anchor cost.
The analytic Gaussian focus score is a mandatory complementary check.  It is
never substituted for the primary and never drops finite out-of-FOV events.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from .cohort import Event, extract_cohorts, load_spec, require_equal_event_ids
from .focus import FocusSample, PaddedCanvas, compute_focus_by_arm
from .geometry_reference import (
    BEHIND_REFERENCE,
    CONTINUOUS_EXTENT,
    IN_FOV,
    INVALID_GEOMETRY,
    OUTSIDE_FOV,
    CommonReferenceGeometry,
    EventObservation,
    PoseSeries,
    RadtanCalibration,
    ReferenceWarp,
    TimedPoseTWC,
    classify_fov,
    distort_normalized,
    matmul,
    matvec,
    quaternion_xyzw_to_rotation_t_wc,
    transpose,
    undistort_normalized,
)
from .statistics import moving_block_cluster_draws, paired_effect_sizes


ARMS = ("RAW", "SENSOR_FIXED", "MC_CORRECT", "MC_WRONG", "MC_DELAYED")
DEFAULT_DELAY_NS = 4_998_186
DEFAULT_SIGMA_PX = 1.0
DEFAULT_CANVAS = PaddedCanvas(240, 180, 16.0)


class EvaluationError(ValueError):
    """The frozen metric contract could not be evaluated exactly."""


def _seconds_to_ns(text: str, where: str) -> int:
    try:
        value = Decimal(text) * Decimal(1_000_000_000)
    except InvalidOperation as exc:
        raise EvaluationError(f"{where} timestamp is invalid") from exc
    integral = value.to_integral_value()
    if value != integral:
        raise EvaluationError(f"{where} timestamp is not an integer nanosecond")
    return int(integral)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_calibration(path: str | Path) -> RadtanCalibration:
    source = Path(path)
    try:
        tokens = source.read_text(encoding="ascii").split()
        values = tuple(float(token) for token in tokens)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvaluationError("cannot parse calib.txt") from exc
    if len(values) != 9:
        raise EvaluationError("calib.txt must contain exactly nine values")
    return RadtanCalibration(240, 180, *values)


def load_poses(path: str | Path) -> PoseSeries:
    source = Path(path)
    poses: list[TimedPoseTWC] = []
    try:
        with source.open("r", encoding="ascii", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                fields = line.rstrip("\n").split(" ")
                if len(fields) != 8 or any(field == "" for field in fields):
                    raise EvaluationError(f"groundtruth line {line_number} is not canonical")
                timestamp_ns = _seconds_to_ns(fields[0], f"groundtruth line {line_number}")
                numeric = tuple(float(field) for field in fields[1:])
                if not all(math.isfinite(value) for value in numeric):
                    raise EvaluationError(f"groundtruth line {line_number} is non-finite")
                poses.append(
                    TimedPoseTWC(
                        timestamp_ns,
                        (numeric[3], numeric[4], numeric[5], numeric[6]),
                        (numeric[0], numeric[1], numeric[2]),
                    )
                )
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, EvaluationError):
            raise
        raise EvaluationError("cannot parse groundtruth.txt") from exc
    return PoseSeries(poses)


def _normalize(ray: Sequence[float]) -> tuple[float, float, float]:
    if len(ray) != 3 or not all(math.isfinite(float(value)) for value in ray):
        raise EvaluationError("ray must contain three finite values")
    norm = math.sqrt(sum(float(value) ** 2 for value in ray))
    if norm <= 0.0:
        raise EvaluationError("ray norm must be positive")
    return tuple(float(value) / norm for value in ray)  # type: ignore[return-value]


def _sensor_ray(event: EventObservation, calibration: RadtanCalibration) -> tuple[float, float, float]:
    xd = (event.x - calibration.cx) / calibration.fx
    yd = (event.y - calibration.cy) / calibration.fy
    xu, yu = undistort_normalized(xd, yd, calibration)
    return _normalize((xu, yu, 1.0))


def _project(
    event: EventObservation,
    ray: Sequence[float],
    calibration: RadtanCalibration,
    reference_timestamp_ns: int,
    lookup_timestamp_ns: int,
) -> ReferenceWarp:
    checked = _normalize(ray)
    if checked[2] <= 0.0:
        return ReferenceWarp(
            event.event_id, event.timestamp_ns, reference_timestamp_ns,
            event.x, event.y, event.polarity, BEHIND_REFERENCE, checked,
            None, None, None, lookup_timestamp_ns, lookup_timestamp_ns, 0, 0,
        )
    xd, yd = distort_normalized(checked[0] / checked[2], checked[1] / checked[2], calibration)
    x = calibration.fx * xd + calibration.cx
    y = calibration.fy * yd + calibration.cy
    fov = classify_fov(x, y, calibration, CONTINUOUS_EXTENT)
    return ReferenceWarp(
        event.event_id, event.timestamp_ns, reference_timestamp_ns,
        event.x, event.y, event.polarity, IN_FOV if fov.in_fov else OUTSIDE_FOV,
        checked, x, y, fov, lookup_timestamp_ns, lookup_timestamp_ns, 0, 0,
    )


def _pose_warp(
    event: EventObservation,
    poses: PoseSeries,
    calibration: RadtanCalibration,
    reference_timestamp_ns: int,
    lookup_timestamp_ns: int,
    *,
    inverse: bool = False,
) -> ReferenceWarp:
    reference = poses.at(reference_timestamp_ns)
    occurrence = poses.at(lookup_timestamp_ns)
    r_reference = quaternion_xyzw_to_rotation_t_wc(reference.quaternion_xyzw)
    r_occurrence = quaternion_xyzw_to_rotation_t_wc(occurrence.quaternion_xyzw)
    relative = matmul(transpose(r_reference), r_occurrence)
    if inverse:
        relative = transpose(relative)
    ray = matvec(relative, _sensor_ray(event, calibration))
    return _project(event, ray, calibration, reference_timestamp_ns, lookup_timestamp_ns)


def _observation(event: Event) -> EventObservation:
    return EventObservation(
        event.dataset_event_index,
        event.timestamp_ns,
        float(event.x),
        float(event.y),
        event.polarity_01,
    )


def _angular_distance(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    return math.acos(min(1.0, max(-1.0, dot)))


def _nearest_costs(
    query: Sequence[ReferenceWarp], anchor: Sequence[ReferenceWarp]
) -> tuple[float, ...]:
    anchors: dict[int, list[tuple[float, float, float]]] = {0: [], 1: []}
    for item in anchor:
        if item.reference_ray is None:
            raise EvaluationError("anchor contains invalid reference ray")
        anchors[item.polarity].append(item.reference_ray)
    if not anchors[0] or not anchors[1]:
        raise EvaluationError("anchor lacks one polarity")
    costs: list[float] = []
    for item in query:
        if item.reference_ray is None:
            raise EvaluationError("query contains invalid reference ray")
        costs.append(min(_angular_distance(item.reference_ray, ray) for ray in anchors[item.polarity]))
    return tuple(costs)


def _bootstrap_effect(
    timestamps_ns: Sequence[int],
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    seed_text: str,
    resamples: int,
    block_length_clusters: int,
    lower_quantile: float,
) -> dict[str, object]:
    point = paired_effect_sizes(baseline, candidate)
    draws = moving_block_cluster_draws(
        timestamps_ns,
        block_length_clusters=block_length_clusters,
        resamples=resamples,
        seed_text=seed_text,
        stream_id="angular-primary",
    )
    reductions = sorted(
        paired_effect_sizes(
            tuple(baseline[index] for index in draw),
            tuple(candidate[index] for index in draw),
        )["relative_mean_reduction"]
        for draw in draws
    )
    rank = max(0, min(len(reductions) - 1, math.ceil(lower_quantile * len(reductions)) - 1))
    return {
        "point": point,
        "bootstrap_resamples": resamples,
        "one_sided_lower_quantile": lower_quantile,
        "relative_reduction_lower_bound": reductions[rank],
    }


def _status_counts(values: Sequence[ReferenceWarp]) -> dict[str, int]:
    return {
        status: sum(item.status == status for item in values)
        for status in (IN_FOV, OUTSIDE_FOV, BEHIND_REFERENCE, INVALID_GEOMETRY)
    }


def evaluate_window(
    *,
    cohort_id: str,
    anchor_events: Sequence[Event],
    query_events: Sequence[Event],
    poses: PoseSeries,
    calibration: RadtanCalibration,
    reference_timestamp_ns: int,
    delay_ns: int = DEFAULT_DELAY_NS,
    sigma_px: float = DEFAULT_SIGMA_PX,
    canvas: PaddedCanvas = DEFAULT_CANVAS,
    resamples: int = 10_000,
    block_length_clusters: int = 32,
    minimum_relative_reduction: float = 0.05,
) -> dict[str, object]:
    """Evaluate one already-frozen window without changing any source record."""

    if not anchor_events or not query_events:
        raise EvaluationError("anchor and query must be non-empty")
    anchor_observations = tuple(_observation(event) for event in anchor_events)
    query_observations = tuple(_observation(event) for event in query_events)
    common = CommonReferenceGeometry(poses, calibration, reference_timestamp_ns, CONTINUOUS_EXTENT)
    anchor = common.warp_many(anchor_observations)

    arms: dict[str, tuple[ReferenceWarp, ...]] = {}
    sensor_fixed = tuple(
        _project(event, _sensor_ray(event, calibration), calibration, reference_timestamp_ns, event.timestamp_ns)
        for event in query_observations
    )
    arms["RAW"] = sensor_fixed
    arms["SENSOR_FIXED"] = sensor_fixed
    arms["MC_CORRECT"] = common.warp_many(query_observations)
    arms["MC_WRONG"] = tuple(
        _pose_warp(event, poses, calibration, reference_timestamp_ns, event.timestamp_ns, inverse=True)
        for event in query_observations
    )
    arms["MC_DELAYED"] = tuple(
        _pose_warp(event, poses, calibration, reference_timestamp_ns, event.timestamp_ns - delay_ns)
        for event in query_observations
    )

    expected_ids = tuple(event.dataset_event_index for event in query_events)
    require_equal_event_ids(expected_ids, {
        name: (item.event_id for item in values) for name, values in arms.items()
    })
    if any(item.status == INVALID_GEOMETRY for values in arms.values() for item in values):
        raise EvaluationError("an arm contains invalid geometry")

    costs = {name: _nearest_costs(values, anchor) for name, values in arms.items()}
    timestamps = tuple(event.timestamp_ns for event in query_events)
    seed = f"UZH-MCWTB-METRIC-V3|{cohort_id}|{reference_timestamp_ns}|{len(query_events)}"
    primary = _bootstrap_effect(
        timestamps, costs["SENSOR_FIXED"], costs["MC_CORRECT"],
        seed_text=seed, resamples=resamples,
        block_length_clusters=block_length_clusters, lower_quantile=0.025,
    )
    controls = {
        name: _bootstrap_effect(
            timestamps, costs[name], costs["MC_CORRECT"],
            seed_text=f"{seed}|CONTROL|{name}", resamples=resamples,
            block_length_clusters=block_length_clusters,
            lower_quantile=1.0 / 60.0,
        )
        for name in ("MC_WRONG", "MC_DELAYED")
    }

    focus_inputs = {
        name: tuple(
            FocusSample(item.event_id, item.reference_x, item.reference_y, item.polarity)
            for item in values
            if item.reference_x is not None and item.reference_y is not None
        )
        for name, values in arms.items()
    }
    if any(len(values) != len(query_events) for values in focus_inputs.values()):
        raise EvaluationError("focus projection unavailable for at least one event")
    focus = compute_focus_by_arm(focus_inputs, sigma_px=sigma_px, canvas=canvas)
    focus_scores = {name: value.score for name, value in focus.items()}
    focus_gate = {
        "mc_correct_strictly_above_sensor_fixed": focus_scores["MC_CORRECT"] > focus_scores["SENSOR_FIXED"],
        "mc_correct_strictly_above_wrong": focus_scores["MC_CORRECT"] > focus_scores["MC_WRONG"],
    }
    focus_diagnostics = {
        # A constant pose-time offset under nearly constant angular velocity
        # can translate the whole corrected cloud without changing pairwise
        # concentration.  Absolute delayed-pose separation belongs to the
        # angular primary, not to this translation-invariant focus metric.
        "mc_correct_minus_delayed": focus_scores["MC_CORRECT"] - focus_scores["MC_DELAYED"],
        "delayed_control_is_not_a_focus_gate": True,
    }
    point = primary["point"]
    assert isinstance(point, Mapping)
    candidate_gate = (
        float(point["relative_mean_reduction"]) > minimum_relative_reduction
        and float(primary["relative_reduction_lower_bound"]) > minimum_relative_reduction
        and all(float(value["relative_reduction_lower_bound"]) > 0.0 for value in controls.values())
        and all(focus_gate.values())
    )
    return {
        "schema": "redred.uzh_mc_wtb_motion_v3.window_result/v1",
        "cohort_id": cohort_id,
        "reference_timestamp_ns": reference_timestamp_ns,
        "event_identity": {
            "anchor_count": len(anchor_events),
            "query_count": len(query_events),
            "first_query_id": expected_ids[0],
            "last_query_id": expected_ids[-1],
        },
        "primary_angular_nn": primary,
        "negative_controls": controls,
        "focus": {
            "metric_id": next(iter(focus.values())).metric_id,
            "sigma_px": sigma_px,
            "scores": focus_scores,
            "gate": focus_gate,
            "diagnostics": focus_diagnostics,
        },
        "coverage": {name: _status_counts(values) for name, values in arms.items()},
        "candidate_gate_all_components": candidate_gate,
        "claim": "DEVELOPMENT_DIAGNOSTIC_ONLY_UNLESS_BOUND_TO_A_SEALED_HOLDOUT_RECEIPT",
    }


def evaluate_dataset_cohort(
    dataset_root: str | Path,
    cohort_id: str,
    *,
    allow_holdout: bool = False,
    resamples: int = 10_000,
) -> dict[str, object]:
    root = Path(dataset_root)
    spec = load_spec()
    split = next(
        (str(cohort["split"]) for cohort in spec["cohorts"] if cohort["id"] == cohort_id),
        None,
    )
    if split is None:
        raise EvaluationError(f"unknown cohort: {cohort_id}")
    if split == "holdout" and not allow_holdout:
        raise EvaluationError("holdout remains sealed; pass explicit allow_holdout only after contract freeze")
    extraction = extract_cohorts(root / "events.txt")
    prereg = json.loads((Path(__file__).with_name("development_preregistered.json")).read_text())
    pins = prereg["input_authority"]
    if _sha256(root / "groundtruth.txt") != pins["poses_member_sha256"]:
        raise EvaluationError("groundtruth.txt hash differs from preregistration")
    if _sha256(root / "calib.txt") != pins["calibration_member_sha256"]:
        raise EvaluationError("calib.txt hash differs from preregistration")
    cohort = next(item for item in spec["cohorts"] if item["id"] == cohort_id)
    reference_timestamp_ns = int(cohort["query"]["start_timestamp_ns_inclusive"])
    return evaluate_window(
        cohort_id=cohort_id,
        anchor_events=extraction.window(cohort_id, "anchor").records,
        query_events=extraction.window(cohort_id, "query").records,
        poses=load_poses(root / "groundtruth.txt"),
        calibration=load_calibration(root / "calib.txt"),
        reference_timestamp_ns=reference_timestamp_ns,
        resamples=resamples,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--resamples", type=int, default=10_000)
    args = parser.parse_args(argv)
    result = evaluate_dataset_cohort(
        args.dataset_root, args.cohort,
        allow_holdout=args.allow_holdout, resamples=args.resamples,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
