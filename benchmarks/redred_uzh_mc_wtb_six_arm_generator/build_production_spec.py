#!/usr/bin/env python3
"""Freeze a production generator spec around already validated input bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.redred_uzh_mc_wtb_adapter import inspect as inspect_adapter
from benchmarks.redred_uzh_shapes_pose_join import inspect as inspect_pose_join
from benchmarks.redred_uzh_mc_wtb_six_arm_generator import generator


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def identity(path: Path) -> dict:
    payload = path.read_bytes()
    return {"size_bytes": len(payload), "sha256": sha(payload)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pose-join", type=Path, required=True)
    parser.add_argument("--join-spec", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--retire-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    join_check = inspect_pose_join(args.pose_join, args.join_spec)
    adapter_check = inspect_adapter(args.adapter, args.pose_join, args.join_spec)
    if join_check.get("status") != "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED":
        raise SystemExit("pose-join inspection did not pass")
    if adapter_check.get("status") != "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED":
        raise SystemExit("adapter inspection did not pass")
    if (
        join_check.get("official_uzh_source") is not True
        or join_check.get("promotion_status") != "HOLD_MC_WTB_ADAPTER"
        or adapter_check.get("official_uzh_source_input") is not True
        or adapter_check.get("promotion_status") != "HOLD_MC_WTB_REAL_DATA_BENEFIT"
    ):
        raise SystemExit("upstream official-source flags or promotion boundary differ")
    event_rows = [
        json.loads(line)
        for line in (args.pose_join / "events_pose_join.jsonl").read_text().splitlines()
    ]
    events = event_rows[1:]
    ids = [row["dataset_event_index"] for row in events]
    polarity = Counter(row["polarity_01"] for row in events)
    ties = len(events) - len({row["timestamp_ns"] for row in events})
    decimal_ids = b"".join(f"{value}\n".encode("ascii") for value in ids)
    compact_ids = canonical(ids)
    prereg_raw = generator._PREREG_PATH.read_bytes()

    input_pins = {
        "pose_join": {
            "status": join_check["status"],
            "promotion_status": join_check.get("promotion_status"),
            "receipt": identity(args.pose_join / "receipt.json"),
            "completion": identity(args.pose_join / "COMPLETE.json"),
            "events": identity(args.pose_join / "events_pose_join.jsonl"),
            "poses": identity(args.pose_join / "poses.jsonl"),
            "calibration": identity(args.pose_join / "calibration.json"),
        },
        "join_spec": identity(args.join_spec),
        "adapter": {
            "status": adapter_check["status"],
            "promotion_status": adapter_check["promotion_status"],
            "receipt": identity(args.adapter / "receipt.json"),
            "completion": identity(args.adapter / "COMPLETE.json"),
            "events": identity(args.adapter / "events_mc_wtb_adapter.jsonl"),
        },
        "retire_receipt": identity(args.retire_receipt),
    }
    production_pin_view = {
        "pose_join_receipt": input_pins["pose_join"]["receipt"]["sha256"],
        "pose_join_completion": input_pins["pose_join"]["completion"]["sha256"],
        "pose_join_events": input_pins["pose_join"]["events"]["sha256"],
        "pose_join_poses": input_pins["pose_join"]["poses"]["sha256"],
        "pose_join_calibration": input_pins["pose_join"]["calibration"]["sha256"],
        "join_spec": input_pins["join_spec"]["sha256"],
        "adapter_events": input_pins["adapter"]["events"]["sha256"],
        "adapter_receipt": input_pins["adapter"]["receipt"]["sha256"],
        "adapter_completion": input_pins["adapter"]["completion"]["sha256"],
    }
    if production_pin_view != generator._PRODUCTION_SHA256:
        raise SystemExit("canonical production source/adapter pins differ")
    spec = {
        "schema": generator.GENERATOR_SPEC_SCHEMA,
        "mode": generator.PRODUCTION_MODE,
        "parameter_set_id": "UZH-SHAPES-ROTATION-SIXARM-8X8-1MS-DELAY4998186-V1",
        "input_pins": input_pins,
        "cohort": {
            "record_count": len(events),
            "first_dataset_event_index": ids[0],
            "last_dataset_event_index": ids[-1],
            "decimal_id_lf_sha256": sha(decimal_ids),
            "compact_id_array_lf_sha256": sha(compact_ids),
            "polarity_0": polarity[0],
            "polarity_1": polarity[1],
            "timestamp_tie_extras": ties,
        },
        "geometry_contract": {
            "record_schema": generator.RECORD_SCHEMA,
            "source_pose": "camera_to_world_T_WC",
            "quaternion_order": "xyzw",
            "reference_timestamp_ns": 41_321_000_000,
            "translation_policy": "preserved_not_applied",
            "pixel_rounding": "floor(value_plus_0.5)",
            "bounds": "continuous_before_rounding",
        },
        "delay_contract": {
            "mc_delayed_delta_ns": 4_998_186,
            "lookup": "occurrence_minus_delta_no_clamp",
        },
        "retire_contract": {
            "provenance_class": generator.PRODUCTION_RETIRE_PROVENANCE,
            "source_timebase": {
                "unit": "ns",
                "epoch": "uzh_shapes_rotation_sequence_zero_after_source_minimum_timestamp_subtraction",
            },
            "missing_policy": "fail_no_partial_output",
            "receipt_sha256": identity(args.retire_receipt)["sha256"],
        },
        "controls_preregistration": {
            "schema": "redred.uzh_mc_wtb_controls.preregistration/v2",
            "parameter_set_id": "UZH-S2-CONTROLS-8X8-1MS-V2",
            "raw_sha256": sha(prereg_raw),
        },
        "serialization": {
            "encoding": "ASCII",
            "json": "compact_sorted_keys",
            "line_ending": "LF",
            "header_in_output": False,
        },
        "claim_scope": generator._claim_scope(generator.PRODUCTION_MODE, True),
        "resource_limits": {
            "max_pose_bytes": 64 * 1024 * 1024,
            "max_event_bytes": 32 * 1024 * 1024,
            "max_adapter_bytes": 64 * 1024 * 1024,
            "max_retire_bytes": 32 * 1024 * 1024,
            "max_records": 1100,
        },
    }
    pose_header = json.loads((args.pose_join / "poses.jsonl").read_text().splitlines()[0])
    epoch = pose_header.get("timebase", {}).get("epoch")
    try:
        generator._retire_receipt(
            args.retire_receipt.read_bytes(), events, epoch, spec
        )
    except generator.GeneratorFailure as error:
        raise SystemExit(f"retire receipt semantic preflight failed: {error}") from error
    payload = canonical(spec)
    try:
        with args.output.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise SystemExit("refusing to overwrite an existing generator spec") from error
    print("PRODUCTION_GENERATOR_SPEC_FROZEN", sha(payload))


if __name__ == "__main__":
    main()
