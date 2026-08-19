"""Independent integer fixture generator for the MC-WTB four-arm causal core.

This test-only generator deliberately uses only standard-library JSON, hashing,
and integer quarter-turn arithmetic.  It does not use the implementation under
test to generate either observations or expected reference coordinates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "mcwtb-independent-quarter-turn/v1"
STATUS = "PASS_SYNTHETIC_CAUSAL_CORE"
TIMEBASE = {
    "clock_domain": "mcwtb_causal_absolute_clock",
    "epoch": "synthetic_causal_epoch_0",
    "unit": "ns",
}
CAMERA_ID = "mcwtb-causal-camera-65"
INTRINSICS_ID = "mcwtb-causal-pinhole-65-v1"
POSE_STREAM_ID = "mcwtb-causal-quarter-turns-v1"
CONVENTION_ID = "redred.camera.xyz-rdf.active-w2s.rrtp.deg.pinhole-row-major/v1"

ROTATIONS = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
    ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
)

EXPECTED_RESULTS = {
    "C0_IDENTITY": {
        "event_count": 8,
        "positive_events": 7,
        "negative_events": 1,
        "exact_reference_events": 8,
        "pixel_sse": 0,
        "changed_coordinate_count": 0,
        "sensor_packets": 7,
        "compensated_packets": 7,
        "sensor_projected_bits": 903,
        "compensated_projected_bits": 903,
        "geometry_accept": True,
    },
    "C1_CORRECT": {
        "event_count": 32,
        "positive_events": 28,
        "negative_events": 4,
        "exact_reference_events": 32,
        "pixel_sse": 0,
        "decoded_unique_reference_pixels": 8,
        "per_landmark_unique_pixel_count": 1,
        "sensor_packets": 28,
        "compensated_packets": 28,
        "sensor_projected_bits": 3612,
        "compensated_projected_bits": 3612,
        "geometry_accept": True,
    },
    "C2_WRONG_VALID": {
        "event_count": 32,
        "positive_events": 28,
        "negative_events": 4,
        "exact_reference_events": 8,
        "pixel_sse": 9664,
        "per_landmark_unique_pixel_count": 4,
        "sensor_packets": 28,
        "compensated_packets": 28,
        "sensor_projected_bits": 3612,
        "compensated_projected_bits": 3612,
        "geometry_accept": False,
    },
    "C3_POSE_PERMUTED": {
        "event_count": 32,
        "positive_events": 28,
        "negative_events": 4,
        "exact_reference_events": 0,
        "pixel_sse": 9664,
        "per_landmark_unique_pixel_count": 1,
        "sensor_packets": 28,
        "compensated_packets": 24,
        "sensor_projected_bits": 3612,
        "compensated_projected_bits": 3096,
        "apparent_packet_delta": 4,
        "geometry_accept": False,
    },
}


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
        for record in records
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def landmarks() -> list[dict[str, Any]]:
    result = []
    for index in range(7):
        dx = ((11 * index + 3) % 29) - 14
        dy = ((17 * index + 5) % 27) - 13
        result.append(
            {
                "landmark_id": f"L{index}",
                "polarity": 1,
                "reference_xy": [32 + dx, 32 + dy],
            }
        )
    result.append(
        {"landmark_id": "L7", "polarity": -1, "reference_xy": [40, 20]}
    )
    return result


def sensor_xy(reference_xy: list[int], quarter_turn: int) -> list[int]:
    dx = reference_xy[0] - 32
    dy = reference_xy[1] - 32
    if quarter_turn == 0:
        return [32 + dx, 32 + dy]
    if quarter_turn == 1:
        return [32 - dy, 32 + dx]
    if quarter_turn == 2:
        return [32 - dx, 32 - dy]
    if quarter_turn == 3:
        return [32 + dy, 32 - dx]
    raise ValueError("quarter_turn must be in 0..3")


def _intrinsics() -> dict[str, Any]:
    return {
        "schema": "redred.known_motion.intrinsics/v2",
        "intrinsics_id": INTRINSICS_ID,
        "camera_id": CAMERA_ID,
        "width": 65,
        "height": 65,
        "fx": 32,
        "fy": 32,
        "cx": 32,
        "cy": 32,
        "convention": {
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
        },
        "provenance": {
            "source_id": "mcwtb-independent-causal-generator-v1",
            "created_by": "test-only independent integer generator",
            "content_sha256": "1" * 64,
        },
    }


def _pose_header() -> dict[str, Any]:
    return {
        "schema": "redred.known_motion.pose_stream/v2",
        "record_type": "header",
        "pose_stream_id": POSE_STREAM_ID,
        "camera_id": CAMERA_ID,
        "intrinsics_id": INTRINSICS_ID,
        "convention_id": CONVENTION_ID,
        "timebase": TIMEBASE,
        "provenance": {
            "source_id": "mcwtb-independent-causal-generator-v1",
            "created_by": "test-only independent integer generator",
            "content_sha256": "2" * 64,
        },
    }


def _pose_record(index: int, matrix_index: int) -> dict[str, Any]:
    return {
        "schema": "redred.known_motion.pose/v2",
        "record_type": "pose",
        "pose_id": f"P{index}",
        "timestamp": {"value": 1000 * (index + 1), **TIMEBASE},
        "rotation_matrix": [list(row) for row in ROTATIONS[matrix_index]],
        "matrix_direction": "world_to_sensor",
    }


def _poses(matrix_indices: tuple[int, ...]) -> bytes:
    records = [_pose_header()]
    records.extend(
        _pose_record(index, matrix_index)
        for index, matrix_index in enumerate(matrix_indices)
    )
    return _jsonl_bytes(records)


def _event_header(count: int) -> dict[str, Any]:
    return {
        "schema": "redred.mc_wtb.event_stream/v1",
        "record_type": "header",
        "evidence_class": "SYNTHETIC_DEMO",
        "camera_id": CAMERA_ID,
        "intrinsics_id": INTRINSICS_ID,
        "pose_stream_id": POSE_STREAM_ID,
        "coordinate_frame": "sensor_image",
        "timebase": TIMEBASE,
        "declared_event_count": count,
    }


def _event_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    oracle_events: list[dict[str, Any]] = []
    for quarter_turn in range(4):
        for landmark_index, landmark in enumerate(landmarks()):
            sequence = 8 * quarter_turn + landmark_index
            event_id = 5000 + sequence
            observed = sensor_xy(landmark["reference_xy"], quarter_turn)
            events.append(
                {
                    "schema": "redred.mc_wtb.event/v1",
                    "record_type": "event",
                    "event_id": event_id,
                    "sequence_index": sequence,
                    "timestamp_ns": 1000 * (quarter_turn + 1),
                    "pose_version": f"P{quarter_turn}",
                    "x": observed[0],
                    "y": observed[1],
                    "polarity": landmark["polarity"],
                }
            )
            oracle_events.append(
                {
                    "event_id": event_id,
                    "sequence_index": sequence,
                    "timestamp_ns": 1000 * (quarter_turn + 1),
                    "true_pose": f"P{quarter_turn}",
                    "landmark_id": landmark["landmark_id"],
                    "polarity": landmark["polarity"],
                    "sensor_xy": observed,
                    "reference_xy": landmark["reference_xy"],
                }
            )
    return events, oracle_events


def _bundle_sha256(artifacts: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(artifacts):
        digest.update(len(name.encode("utf-8")).to_bytes(4, "big"))
        digest.update(name.encode("utf-8"))
        digest.update(len(artifacts[name]).to_bytes(8, "big"))
        digest.update(artifacts[name])
    return digest.hexdigest()


def _expected_summary_bytes() -> bytes:
    summary = {
        "schema": "redred.mc_wtb.synthetic_causal_core_summary/v1",
        "status": STATUS,
        "arms": {
            arm: {
                "exact_reference_events": expected["exact_reference_events"],
                "pixel_sse": expected["pixel_sse"],
                "sensor_packets": expected["sensor_packets"],
                "compensated_packets": expected["compensated_packets"],
                "geometry_accept": expected["geometry_accept"],
            }
            for arm, expected in sorted(EXPECTED_RESULTS.items())
        },
    }
    return (
        json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def build_artifacts() -> dict[str, bytes]:
    events, oracle_events = _event_records()
    oracle = {
        "schema": "redred.mc_wtb.causal_oracle/v1",
        "coordinate_frame": "fixed_reference_camera_image",
        "landmarks": landmarks(),
        "events": oracle_events,
    }
    artifacts = {
        "events_identity.jsonl": _jsonl_bytes([_event_header(8), *events[:8]]),
        "events_causal.jsonl": _jsonl_bytes([_event_header(32), *events]),
        "intrinsics.json": _json_bytes(_intrinsics()),
        "poses_identity.jsonl": _poses((0,)),
        "poses_correct.jsonl": _poses((0, 1, 2, 3)),
        "poses_wrong_valid.jsonl": _poses((0, 0, 0, 0)),
        "poses_permuted.jsonl": _poses((1, 2, 3, 0)),
        "oracle.json": _json_bytes(oracle),
        "expected_summary.json": _expected_summary_bytes(),
    }
    generator_source = Path(__file__).read_bytes()
    manifest = {
        "schema": "redred.mc_wtb.causal_manifest/v1",
        "status": STATUS,
        "claim_scope": "test-only quarter-turn synthetic causal core",
        "generator_version": GENERATOR_VERSION,
        "generator_seed": None,
        "generator_source_sha256": _sha256(generator_source),
        "artifact_sha256": {
            name: _sha256(data) for name, data in sorted(artifacts.items())
        },
        "artifact_bundle_sha256": _bundle_sha256(artifacts),
        "landmark_table_sha256": _sha256(_json_bytes(landmarks())),
        "common_c1_c2_c3_event_sha256": _sha256(artifacts["events_causal.jsonl"]),
        "expected_results": EXPECTED_RESULTS,
        "forbidden_claims": [
            "actual wire bandwidth reduction",
            "codec or reversible transport",
            "real-data generalization",
            "RTL or PPA readiness",
            "PASS_SYNTHETIC_CAUSAL_DISCRIMINATION",
        ],
    }
    return {**artifacts, "manifest.json": _json_bytes(manifest)}


def write_artifacts(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    for name, data in build_artifacts().items():
        (output_directory / name).write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    write_artifacts(args.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
