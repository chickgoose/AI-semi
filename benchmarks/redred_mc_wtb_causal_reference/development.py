"""Development-only UZH assay for the causal world-reference model.

The registry is a deterministic odd-second time grid selected without arm
scores.  It excludes the consumed metric-v3 interval and never exports events
from that interval.  Results are exploratory: the registry and thresholds were
committed after an informal development inspection, so they are not a new
holdout or confirmatory evidence.
"""

from __future__ import annotations

import argparse
import bisect
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_motion_qualification import rotation_displacement_proxy_q
from .reference import CausalReferenceBank, CausalReferenceConfig, ReferenceObservation


SCHEMA = "redred.mc_wtb_causal_reference.development/v1"
EVENTS_SHA256 = "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda"
GROUNDTRUTH_SHA256 = "bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb"
CALIB_SHA256 = "ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd"
EVENTS_SIZE_BYTES = 509_907_771
EVENTS_LINE_COUNT = 23_126_288

# Anchor and query bytes from the consumed assay must not be selected.  The
# interval is wider than the old 1 ms query so its 0.25 ms anchor is included.
CONSUMED_BLACKLIST = (43_320_750_000, 43_322_000_000)
QUERY_START_SECONDS = (
    5.321, 7.321, 9.321, 11.321, 13.321, 15.321, 17.321, 19.321,
    21.321, 23.321, 25.321, 27.321, 29.321, 31.321, 33.321, 35.321,
    37.321, 39.321, 45.321, 47.321, 49.321, 51.321, 53.321, 55.321,
)
WARMUP_NS = 1_000_000
QUERY_NS = 1_000_000
LOW_MID_PX = 0.35
MID_HIGH_PX = 1.40
_LINE = re.compile(rb"([0-9]+\.[0-9]{9}) ([0-9]+) ([0-9]+) ([01])\n\Z")


class DevelopmentError(ValueError):
    """Development provenance or metric contract failed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_tree_sha256() -> str:
    root = Path(__file__).parents[2]
    digest = hashlib.sha256()
    for package in (
        "benchmarks/redred_mc_wtb_causal_reference",
        "benchmarks/redred_mc_wtb_motion_qualification",
        "benchmarks/redred_uzh_mc_wtb_motion_v3",
    ):
        for path in sorted((root / package).glob("*.py")):
            relative = path.relative_to(root).as_posix().encode("ascii")
            digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _seconds_to_ns(value: bytes) -> int:
    try:
        decimal = Decimal(value.decode("ascii")) * Decimal(1_000_000_000)
    except (UnicodeError, InvalidOperation) as exc:
        raise DevelopmentError("invalid event timestamp") from exc
    integral = decimal.to_integral_value()
    if decimal != integral:
        raise DevelopmentError("event timestamp is not integral nanoseconds")
    return int(integral)


def _normalize(ray: Sequence[float]) -> Tuple[float, float, float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in ray))
    if not math.isfinite(norm) or norm <= 0.0:
        raise DevelopmentError("invalid ray")
    return tuple(float(value) / norm for value in ray)  # type: ignore[return-value]


def _sensor_ray(event, calibration) -> Tuple[float, float, float]:
    # Lazy import keeps the registry/provenance gate runnable on the Python 3.8
    # physical server; the preserved floating-point geometry model uses newer
    # dataclass syntax and is needed only for host-side development scoring.
    from benchmarks.redred_uzh_mc_wtb_motion_v3.geometry_reference import undistort_normalized
    xd = (event.x - calibration.cx) / calibration.fx
    yd = (event.y - calibration.cy) / calibration.fy
    xu, yu = undistort_normalized(xd, yd, calibration)
    return _normalize((xu, yu, 1.0))


def _pose_at_or_before(poses, timestamp_ns: int):
    samples = poses.poses
    times = tuple(sample.timestamp_ns for sample in samples)
    index = bisect.bisect_right(times, timestamp_ns) - 1
    if index < 0:
        raise DevelopmentError("no causal pose exists at or before timestamp")
    return samples[index]


def _two_latest_poses_at_or_before(poses, timestamp_ns: int):
    samples = poses.poses
    times = tuple(sample.timestamp_ns for sample in samples)
    index = bisect.bisect_right(times, timestamp_ns) - 1
    if index < 1:
        raise DevelopmentError("two causal poses are required for motion evidence")
    return samples[index - 1], samples[index]


def _validate_registry(rows: Sequence[Mapping[str, int]]) -> None:
    if not rows or len({str(row["window_id"]) for row in rows}) != len(rows):
        raise DevelopmentError("development registry IDs are empty or duplicated")
    previous_end = None
    for row in sorted(rows, key=lambda item: item["warmup_start_ns_inclusive"]):
        if not (
            row["warmup_start_ns_inclusive"] < row["query_start_ns_inclusive"]
            < row["query_end_ns_exclusive"]
        ):
            raise DevelopmentError("development registry interval is invalid")
        if previous_end is not None and row["warmup_start_ns_inclusive"] < previous_end:
            raise DevelopmentError("development registry intervals overlap")
        previous_end = row["query_end_ns_exclusive"]
        if not (
            row["query_end_ns_exclusive"] <= CONSUMED_BLACKLIST[0]
            or row["warmup_start_ns_inclusive"] >= CONSUMED_BLACKLIST[1]
        ):
            raise DevelopmentError("development registry overlaps consumed interval")


def window_registry() -> Tuple[Mapping[str, int], ...]:
    rows = []
    for seconds in QUERY_START_SECONDS:
        query_start = int(round(seconds * 1_000_000_000))
        row = {
            "window_id": "shapes_rotation_dev_%06d" % int(round(seconds * 1000)),
            "warmup_start_ns_inclusive": query_start - WARMUP_NS,
            "query_start_ns_inclusive": query_start,
            "query_end_ns_exclusive": query_start + QUERY_NS,
        }
        rows.append(row)
    _validate_registry(rows)
    return tuple(rows)


def _validate_sources(dataset_dir: Path) -> Tuple[Path, Path, Path]:
    events = dataset_dir / "events.txt"
    groundtruth = dataset_dir / "groundtruth.txt"
    calib = dataset_dir / "calib.txt"
    for source, expected in (
        (events, EVENTS_SHA256), (groundtruth, GROUNDTRUTH_SHA256), (calib, CALIB_SHA256)
    ):
        if not source.is_file() or _sha256(source) != expected:
            raise DevelopmentError("official source hash mismatch: %s" % source.name)
    if events.stat().st_size != EVENTS_SIZE_BYTES:
        raise DevelopmentError("official events size mismatch")
    return events, groundtruth, calib


def _extract(events_path: Path, registry: Sequence[Mapping[str, int]]) -> Dict[str, list]:
    from benchmarks.redred_uzh_mc_wtb_motion_v3.geometry_reference import EventObservation
    selected = {str(row["window_id"]): [] for row in registry}
    line_count = 0
    cursor = 0
    ordered = sorted(registry, key=lambda row: row["warmup_start_ns_inclusive"])
    with events_path.open("rb") as stream:
        for event_id, raw in enumerate(stream):
            line_count += 1
            match = _LINE.fullmatch(raw)
            if match is None:
                raise DevelopmentError("non-canonical event line %d" % (event_id + 1))
            timestamp_ns = _seconds_to_ns(match.group(1))
            while cursor < len(ordered) and timestamp_ns >= ordered[cursor]["query_end_ns_exclusive"]:
                cursor += 1
            if cursor >= len(ordered):
                continue
            row = ordered[cursor]
            if row["warmup_start_ns_inclusive"] <= timestamp_ns < row["query_end_ns_exclusive"]:
                if CONSUMED_BLACKLIST[0] <= timestamp_ns < CONSUMED_BLACKLIST[1]:
                    raise DevelopmentError("consumed interval event reached development extraction")
                selected[str(row["window_id"])].append(EventObservation(
                    event_id, timestamp_ns, float(match.group(2)), float(match.group(3)), int(match.group(4))
                ))
    if line_count != EVENTS_LINE_COUNT:
        raise DevelopmentError("official events line count mismatch")
    return selected


def _tier(displacement_px: float) -> str:
    if displacement_px < LOW_MID_PX:
        return "LOW"
    if displacement_px < MID_HIGH_PX:
        return "MID"
    return "HIGH"


def _evaluate_window(
    row: Mapping[str, int], events: Sequence[object], poses, calibration
) -> Mapping[str, object]:
    from benchmarks.redred_uzh_mc_wtb_motion_v3.geometry_reference import (
        matvec, quaternion_xyzw_to_rotation_t_wc,
    )
    query_start = row["query_start_ns_inclusive"]
    sensor_observations = []
    correct_observations = []
    query_ids = []
    for event in events:
        sensor_ray = _sensor_ray(event, calibration)
        occurrence_pose = _pose_at_or_before(poses, event.timestamp_ns)
        corrected_ray = _normalize(matvec(
            quaternion_xyzw_to_rotation_t_wc(occurrence_pose.quaternion_xyzw),
            sensor_ray,
        ))
        sensor_observations.append(ReferenceObservation(
            event.event_id, event.timestamp_ns, event.polarity, sensor_ray
        ))
        correct_observations.append(ReferenceObservation(
            event.event_id, event.timestamp_ns, event.polarity, corrected_ray
        ))
        if event.timestamp_ns >= query_start:
            query_ids.append(event.event_id)
    if not query_ids:
        raise DevelopmentError("empty development query")

    config = CausalReferenceConfig(256, 2_000_000)
    sensor_scores = CausalReferenceBank(config).process(sensor_observations)
    correct_scores = CausalReferenceBank(config).process(correct_observations)
    sensor_query = [score for score in sensor_scores if score.timestamp_ns >= query_start]
    correct_query = [score for score in correct_scores if score.timestamp_ns >= query_start]
    if [score.event_id for score in sensor_query] != query_ids:
        raise DevelopmentError("sensor arm changed query identity")
    if [score.event_id for score in correct_query] != query_ids:
        raise DevelopmentError("corrected arm changed query identity")
    if any(not score.reference_available for score in sensor_query + correct_query):
        raise DevelopmentError("causal bank lacks a same-polarity warmup reference")
    sensor_mean = math.fsum(score.angular_cost_rad for score in sensor_query) / len(sensor_query)  # type: ignore[arg-type]
    correct_mean = math.fsum(score.angular_cost_rad for score in correct_query) / len(correct_query)  # type: ignore[arg-type]

    # Use only the two latest pose samples already available at query start.
    # Normalize their observed rotation to a one-millisecond recent-rate proxy.
    prior_pose, latest_pose = _two_latest_poses_at_or_before(poses, query_start)
    observed_q = rotation_displacement_proxy_q(
        prior_pose.quaternion_xyzw, latest_pose.quaternion_xyzw,
        (calibration.fx + calibration.fy) / 2.0, fractional_bits=16,
    )
    pose_delta_ns = latest_pose.timestamp_ns - prior_pose.timestamp_ns
    if pose_delta_ns <= 0:
        raise DevelopmentError("causal pose timestamps are not strictly increasing")
    displacement_px = (observed_q / float(1 << 16)) * QUERY_NS / pose_delta_ns
    raw_reduction = 1.0 - correct_mean / sensor_mean
    tier = _tier(displacement_px)
    policy_reduction = 0.0 if tier == "LOW" else raw_reduction
    identity_digest = hashlib.sha256(
        b"".join(("%d\n" % event_id).encode("ascii") for event_id in query_ids)
    ).hexdigest()
    return {
        "window_id": row["window_id"],
        "query_event_count": len(query_ids),
        "ordered_query_event_ids_sha256": identity_digest,
        "displacement_px_per_query_ms": displacement_px,
        "decision_pose_previous_timestamp_ns": prior_pose.timestamp_ns,
        "decision_pose_latest_timestamp_ns": latest_pose.timestamp_ns,
        "decision_pose_age_at_query_start_ns": query_start - latest_pose.timestamp_ns,
        "development_tier": tier,
        "sensor_fixed_mean_angular_cost_rad": sensor_mean,
        "mc_correct_mean_angular_cost_rad": correct_mean,
        "raw_mc_relative_reduction": raw_reduction,
        "ideal_score_blind_tier_policy_relative_reduction": policy_reduction,
    }


def evaluate_development(dataset_dir: Path) -> Mapping[str, object]:
    from benchmarks.redred_uzh_mc_wtb_motion_v3.evaluate import load_calibration, load_poses
    registry = window_registry()
    events_path, poses_path, calib_path = _validate_sources(dataset_dir)
    # No event bytes are selected before source identity and blacklist checks.
    selected = _extract(events_path, registry)
    poses = load_poses(poses_path)
    calibration = load_calibration(calib_path)
    windows = [
        _evaluate_window(row, selected[str(row["window_id"])], poses, calibration)
        for row in registry
    ]
    tiers = {}
    for tier in ("LOW", "MID", "HIGH"):
        members = [row for row in windows if row["development_tier"] == tier]
        tiers[tier] = {
            "window_count": len(members),
            "equal_window_raw_mc_mean_relative_reduction": math.fsum(
                row["raw_mc_relative_reduction"] for row in members
            ) / len(members),
            "equal_window_ideal_tier_policy_mean_relative_reduction": math.fsum(
                row["ideal_score_blind_tier_policy_relative_reduction"] for row in members
            ) / len(members),
            "minimum_raw_mc_relative_reduction": min(row["raw_mc_relative_reduction"] for row in members),
            "maximum_raw_mc_relative_reduction": max(row["raw_mc_relative_reduction"] for row in members),
        }
    return {
        "schema": SCHEMA,
        "status": "COMPLETE_DEVELOPMENT_ONLY_CAUSAL_DIAGNOSTIC",
        "benefit_claim_status": "HOLD_PRACTICALLY_ZERO_WITH_CAUSAL_POSE_AVAILABILITY",
        "claim_scope": "exploratory development; not holdout, RTL, PPA, P&R, or production-threshold evidence",
        "source": {
            "sequence": "UZH DAVIS shapes_rotation",
            "events_sha256": EVENTS_SHA256,
            "groundtruth_sha256": GROUNDTRUTH_SHA256,
            "calib_sha256": CALIB_SHA256,
        },
        "reproduction": {
            "command": "python3 -m benchmarks.redred_mc_wtb_causal_reference.development --dataset-dir <official_shapes_rotation> --output <result.json>",
            "python_version": sys.version.split()[0],
            "development_py_sha256": _sha256(Path(__file__)),
            "reference_py_sha256": _sha256(Path(__file__).with_name("reference.py")),
            "routing_py_sha256": _sha256(Path(__file__).with_name("routing.py")),
            "geometry_reference_py_sha256": _sha256(
                Path(__file__).parents[1] / "redred_uzh_mc_wtb_motion_v3" / "geometry_reference.py"
            ),
            "evaluate_py_sha256": _sha256(
                Path(__file__).parents[1] / "redred_uzh_mc_wtb_motion_v3" / "evaluate.py"
            ),
            "motion_controller_py_sha256": _sha256(
                Path(__file__).parents[1] / "redred_mc_wtb_motion_qualification" / "controller.py"
            ),
            "runtime_python_tree_sha256": _runtime_tree_sha256(),
            "registry_sha256": hashlib.sha256(
                (json.dumps(list(registry), sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
            ).hexdigest(),
        },
        "consumed_interval_blacklist_ns": list(CONSUMED_BLACKLIST),
        "metric": {
            "past_only": True,
            "score_equal_timestamp_cluster_before_insert": True,
            "polarity_separated": True,
            "capacity_per_polarity": 256,
            "max_age_ns": 2_000_000,
            "warmup_ns": WARMUP_NS,
            "query_ns": QUERY_NS,
            "primary_cost": "nearest prior same-polarity angular world-ray distance",
            "pose_availability": "latest-at-or-before only; zero-order hold for event world ray",
            "route_decision_pose_interval": "two latest supplied pose samples at or before query_start; recent rate normalized to 1ms",
        },
        "provisional_development_tiers": {
            "selection_warning": "exploratory time-grid inspection only; must not authorize a future holdout",
            "low_mid_px": LOW_MID_PX,
            "mid_high_px": MID_HIGH_PX,
            "tie_rule": "LOW if d<0.35; MID if 0.35<=d<1.40; HIGH otherwise",
        },
        "window_registry": list(registry),
        "windows": windows,
        "tier_summary": tiers,
        "equal_window_ideal_tier_policy_mean_relative_reduction": math.fsum(
            row["ideal_score_blind_tier_policy_relative_reduction"] for row in windows
        ) / len(windows),
        "qualifier_hysteresis_or_dwell_executed": False,
        "consumed_interval_raw_bytes_scanned_for_source_hash_and_full_file_pass": True,
        "consumed_interval_selected": False,
        "consumed_interval_arm_generated": False,
        "consumed_interval_scored": False,
        "innovation_stage_started": False,
    }


def main(argv: Iterable[str] = ()) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(tuple(argv) if argv else None)
    result = evaluate_development(args.dataset_dir)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
