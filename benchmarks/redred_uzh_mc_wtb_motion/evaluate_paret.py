#!/usr/bin/env python3
"""Independent preregistered PARET assay for the fixed UZH phase-4 window."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.redred_uzh_mc_wtb_controls.evaluate import evaluate_records
from benchmarks.redred_uzh_mc_wtb_six_arm_generator import inspect as inspect_six_arm


ARMS = ("RAW", "SENSOR_FIXED", "MC_CORRECT", "MC_WRONG", "MC_DELAYED", "RETIRE_WARP")
EXPECTED_PREREGISTRATION_SHA256 = "2d564d92460b86e7aaaadfe4c4118d3d42310520e31c9af4c1f581cd16f1f548"


class EvaluationFailure(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            value.update(chunk)
    return value.hexdigest()


def timestamp_ns(lexeme: str) -> int:
    whole, fractional = lexeme.split(".")
    if len(fractional) != 9 or not whole.isdigit() or not fractional.isdigit():
        raise EvaluationFailure("source timestamp is not exact 9-digit nanoseconds")
    return int(whole) * 1_000_000_000 + int(fractional)


def extract_anchor(events_path: Path, prereg: dict) -> tuple[list[dict], bytes]:
    expected_source_sha = prereg["source_member_sha256"]
    if file_digest(events_path) != expected_source_sha:
        raise EvaluationFailure("raw source member SHA differs")
    lower = prereg["anchor"]["start_timestamp_ns_inclusive"]
    upper = prereg["anchor"]["end_timestamp_ns_exclusive"]
    rows = []
    with events_path.open("r", encoding="ascii") as stream:
        for dataset_index, line in enumerate(stream):
            columns = line.split()
            if len(columns) != 4:
                raise EvaluationFailure(f"malformed raw event line {dataset_index + 1}")
            time_ns = timestamp_ns(columns[0])
            if time_ns >= upper:
                break
            if time_ns < lower:
                continue
            x, y = int(columns[1]), int(columns[2])
            polarity = 1 if int(columns[3]) > 0 else 0
            rows.append({
                "dataset_event_index": dataset_index,
                "timestamp_ns": time_ns,
                "x": x,
                "y": y,
                "polarity_01": polarity,
            })
    polarity = Counter(row["polarity_01"] for row in rows)
    unique = {
        value: len({(row["x"], row["y"]) for row in rows if row["polarity_01"] == value})
        for value in (0, 1)
    }
    contract = prereg["anchor"]
    if (
        len(rows) != contract["expected_records"]
        or polarity[0] != contract["expected_polarity_0"]
        or polarity[1] != contract["expected_polarity_1"]
        or any(polarity[value] < contract["minimum_records_per_polarity"] for value in (0, 1))
        or any(unique[value] < contract["minimum_unique_pixels_per_polarity"] for value in (0, 1))
    ):
        raise EvaluationFailure("independent anchor eligibility differs")
    raw = b"".join(canonical(row) for row in rows)
    return rows, raw


def load_six_arm(path: Path, prereg: dict) -> list[dict]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]
    except Exception as error:
        raise EvaluationFailure(f"invalid six-arm JSONL: {error}") from error
    query = prereg["query"]
    if len(rows) != query["record_count"]:
        raise EvaluationFailure("six-arm denominator differs")
    prior_timestamp = -1
    for ordinal, row in enumerate(rows):
        if (
            row.get("join_sequence_index") != ordinal
            or row.get("dataset_event_index") != query["first_dataset_event_index"] + ordinal
            or set(row.get("arms", {})) != set(ARMS)
            or not isinstance(row.get("polarity_01"), int)
            or row["polarity_01"] not in (0, 1)
            or not isinstance(row.get("timestamp_ns"), int)
            or row["timestamp_ns"] < prior_timestamp
            or not (
                query["start_timestamp_ns_inclusive"]
                <= row["timestamp_ns"]
                < query["end_timestamp_ns_exclusive"]
            )
        ):
            raise EvaluationFailure(f"six-arm identity/order differs at {ordinal}")
        prior_timestamp = row["timestamp_ns"]
    if rows[-1]["dataset_event_index"] != query["last_dataset_event_index"]:
        raise EvaluationFailure("six-arm final identity differs")
    return rows


def costs(rows: list[dict], anchor: list[dict], prereg: dict) -> dict[str, list[float]]:
    width, height = prereg["sensor"]["width"], prereg["sensor"]["height"]
    diagonal = math.hypot(width - 1, height - 1)
    points = {
        polarity: [(row["x"], row["y"]) for row in anchor if row["polarity_01"] == polarity]
        for polarity in (0, 1)
    }
    result = {arm: [] for arm in ARMS}
    for row in rows:
        polarity = row["polarity_01"]
        for arm in ARMS:
            value = row["arms"][arm]
            x, y = value.get("locality_x"), value.get("locality_y")
            projected_arm = arm not in ("RAW", "SENSOR_FIXED")
            invalid_coordinate = (
                not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not math.isfinite(x)
                or not math.isfinite(y)
            )
            if invalid_coordinate or (
                projected_arm and value.get("geometry_status") != "in_fov"
            ):
                cost = 1.0
            else:
                cost = min(
                    min(math.hypot(float(x) - ax, float(y) - ay) for ax, ay in points[polarity]) / diagonal,
                    1.0,
                )
            result[arm].append(cost)
    if result["RAW"] != result["SENSOR_FIXED"]:
        raise EvaluationFailure("RAW/SENSOR_FIXED locality costs differ")
    return result


def bootstrap(rows: list[dict], values: dict[str, list[float]], prereg: dict) -> dict:
    clusters: list[list[int]] = []
    for index, row in enumerate(rows):
        if not clusters or rows[clusters[-1][0]]["timestamp_ns"] != row["timestamp_ns"]:
            clusters.append([])
        clusters[-1].append(index)
    config = prereg["bootstrap"]
    seed = int(hashlib.sha256(config["seed_text"].encode("ascii")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    block = config["block_length_clusters"]
    sample_count = len(clusters)
    blocks = math.ceil(sample_count / block)
    reductions = {arm: [] for arm in ("SENSOR_FIXED", "MC_WRONG", "MC_DELAYED", "RETIRE_WARP")}
    for _ in range(config["resamples"]):
        sampled_clusters = []
        for _block_index in range(blocks):
            start = rng.randrange(sample_count)
            sampled_clusters.extend(clusters[(start + offset) % sample_count] for offset in range(block))
        indices = [index for cluster in sampled_clusters[:sample_count] for index in cluster]
        correct = sum(values["MC_CORRECT"][index] for index in indices) / len(indices)
        for arm in reductions:
            control = sum(values[arm][index] for index in indices) / len(indices)
            reductions[arm].append(float("-inf") if control == 0.0 else 1.0 - correct / control)
    output = {}
    for arm, samples in reductions.items():
        ordered = sorted(samples)
        lower = ordered[math.ceil(0.025 * len(ordered)) - 1]
        familywise_lower = ordered[math.ceil((0.05 / 3.0) * len(ordered)) - 1]
        output[arm] = {
            "lower_97_5_one_sided": lower,
            "lower_98_333_one_sided_bonferroni_three_controls": familywise_lower,
        }
    return output


def validate_retire_authority(
    rows: list[dict], endpoint_inspection_path: Path, retire_receipt_path: Path
) -> dict:
    inspection = json.loads(endpoint_inspection_path.read_text(encoding="ascii"))
    if (
        inspection.get("status")
        != "PASS_MC_WTB_OCCURRENCE_BASELINE_OBSERVED_RETIRE_SCOPED"
        or any(inspection.get(name) != value for name, value in (
            ("generated", 1100), ("accepted", 1100), ("retired", 1100),
            ("missing", 0), ("duplicate", 0), ("source_overrun", 0),
        ))
    ):
        raise EvaluationFailure("endpoint inspection is not a clean observed 1100/1100 authority")
    receipt_rows = [
        json.loads(line)
        for line in retire_receipt_path.read_text(encoding="ascii").splitlines()
    ]
    if len(receipt_rows) != len(rows) + 1 or receipt_rows[0].get("record_count") != len(rows):
        raise EvaluationFailure("retire receipt count differs")
    latencies = []
    for row, receipt in zip(rows, receipt_rows[1:]):
        retire_timestamp = receipt.get("retire_timestamp_ns")
        if (
            receipt.get("dataset_event_index") != row["dataset_event_index"]
            or receipt.get("join_sequence_index") != row["join_sequence_index"]
            or receipt.get("occurrence_timestamp_ns") != row["timestamp_ns"]
            or receipt.get("accepted_count") != 1
            or receipt.get("retired_count") != 1
            or row["arms"]["RETIRE_WARP"].get("pose_lookup_timestamp_ns")
            != retire_timestamp
        ):
            raise EvaluationFailure("RETIRE_WARP does not exactly bind the observed receipt")
        latencies.append(retire_timestamp - row["timestamp_ns"])
    if min(latencies) < 0:
        raise EvaluationFailure("retire authority precedes occurrence")
    ordered = sorted(latencies)
    return {
        "inspection_sha256": digest(endpoint_inspection_path.read_bytes()),
        "receipt_sha256": digest(retire_receipt_path.read_bytes()),
        "minimum_latency_ns": ordered[0],
        "p50_latency_ns": ordered[math.ceil(0.50 * len(ordered)) - 1],
        "p95_latency_ns": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "maximum_latency_ns": ordered[-1],
    }


def decide_status(
    baseline_score: float,
    primary: float | None,
    samples: dict,
    geometry: dict,
    prereg: dict,
) -> tuple[str, str, bool]:
    """Apply the frozen gate order, preserving diagnostics under a HOLD."""
    if baseline_score == 0.0:
        return "HOLD_ZERO_BASELINE_COST", "NOT_EVALUABLE_ZERO_BASELINE", False
    threshold = prereg["primary_effect"]["relative_reduction_strictly_greater_than"]
    primary_pass = (
        primary is not None
        and samples["SENSOR_FIXED"]["lower_97_5_one_sided"] > threshold
    )
    primary_gate = "PASS" if primary_pass else "FAIL_NO_PREREGISTERED_BENEFIT"
    retire_informative = geometry["timing_controls"]["RETIRE_WARP"]["informative"]
    controls_to_check = ["MC_WRONG", "MC_DELAYED"]
    if retire_informative:
        controls_to_check.append("RETIRE_WARP")
    controls_pass = all(
        samples[arm]["lower_98_333_one_sided_bonferroni_three_controls"] > 0.0
        for arm in controls_to_check
    )
    # The frozen preregistration says an uninformative RETIRE angular gate
    # reports HOLD, not PASS or FAIL.  Preserve the primary diagnostic above,
    # but do not let it override that literal final-status rule.
    if not retire_informative:
        return "HOLD_RETIRE_CONTROL_UNINFORMATIVE", primary_gate, controls_pass
    if not primary_pass:
        return "FAIL_NO_PREREGISTERED_BENEFIT", primary_gate, controls_pass
    if (
        not geometry["mc_wrong"]["identified"]
        or not geometry["timing_controls"]["MC_DELAYED"]["identified"]
        or not controls_pass
        or not geometry["timing_controls"]["RETIRE_WARP"]["identified"]
    ):
        return "FAIL_NEGATIVE_CONTROL", primary_gate, controls_pass
    return "PASS_PARET_MOTION_IMPROVEMENT_FIXED_WINDOW_SCOPED", primary_gate, controls_pass


def evaluate(
    events_path: Path,
    six_arm_path: Path,
    prereg_path: Path,
    endpoint_inspection_path: Path,
    retire_receipt_path: Path,
    pose_join_dir: Path,
    join_spec_path: Path,
    adapter_dir: Path,
    generator_spec_path: Path,
) -> dict:
    prereg_raw = prereg_path.read_bytes()
    prereg = json.loads(prereg_raw)
    if (
        digest(prereg_raw) != EXPECTED_PREREGISTRATION_SHA256
        or prereg.get("schema")
        != "redred.uzh_mc_wtb_motion.paret_preregistration/v1"
        or prereg.get("metric_id") != "UZH-P4-PARET-v2"
        or prereg.get("frozen_before_full_six_arm_generation") is not True
        or tuple(prereg.get("arms", ())) != ARMS
    ):
        raise EvaluationFailure("PARET preregistration bytes/identity differ")
    generator_inspection = inspect_six_arm(
        six_arm_path.parent,
        pose_join_dir,
        join_spec_path,
        adapter_dir,
        retire_receipt_path,
        generator_spec_path,
    )
    if generator_inspection.get("status") != "PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED":
        raise EvaluationFailure("source-bound production six-arm inspection did not pass")
    anchor, anchor_raw = extract_anchor(events_path, prereg)
    rows = load_six_arm(six_arm_path, prereg)
    retire_authority = validate_retire_authority(
        rows, endpoint_inspection_path, retire_receipt_path
    )
    values = costs(rows, anchor, prereg)
    scores = {arm: sum(values[arm]) / len(rows) for arm in ARMS}
    primary = (
        None
        if scores["SENSOR_FIXED"] == 0.0
        else 1.0 - scores["MC_CORRECT"] / scores["SENSOR_FIXED"]
    )
    samples = bootstrap(rows, values, prereg)
    geometry = evaluate_records(rows)["geometry_control_gate"]
    status, primary_gate_status, negative_controls_pass = decide_status(
        scores["SENSOR_FIXED"], primary, samples, geometry, prereg
    )
    return {
        "schema": "redred.uzh_mc_wtb_motion.paret_result/v1",
        "status": status,
        "preregistration": {"bytes": len(prereg_raw), "sha256": digest(prereg_raw)},
        "six_arm_source_bound_inspection_sha256": digest(canonical(generator_inspection)),
        "anchor": {
            "records": len(anchor),
            "bytes": len(anchor_raw),
            "sha256": digest(anchor_raw),
            "first_dataset_event_index": anchor[0]["dataset_event_index"],
            "last_dataset_event_index": anchor[-1]["dataset_event_index"],
            "polarity_0": sum(row["polarity_01"] == 0 for row in anchor),
            "polarity_1": sum(row["polarity_01"] == 1 for row in anchor),
        },
        "query_records": len(rows),
        "scores": scores,
        "primary_relative_reduction": primary,
        "primary_gate_status": primary_gate_status,
        "negative_controls_pass": negative_controls_pass,
        "bootstrap": samples,
        "bonferroni_three_control_familywise_alpha": 0.05,
        "retire_authority": retire_authority,
        "geometry_control_gate": geometry,
        "claim_scope": {
            "fixed_window_proxy": prereg["claim_limit"],
            "dataset_or_sequence_generalization": False,
            "codec_or_bandwidth": False,
            "rtl_or_ppa": False,
            "phase5_innovation": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--six-arm", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, default=Path(__file__).with_name("paret_preregistered.json"))
    parser.add_argument("--endpoint-inspection", type=Path, required=True)
    parser.add_argument("--retire-receipt", type=Path, required=True)
    parser.add_argument("--pose-join", type=Path, required=True)
    parser.add_argument("--join-spec", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--generator-spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(
        args.events,
        args.six_arm,
        args.preregistration,
        args.endpoint_inspection,
        args.retire_receipt,
        args.pose_join,
        args.join_spec,
        args.adapter,
        args.generator_spec,
    )
    args.output.write_bytes(canonical(result))
    print(result["status"], json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
