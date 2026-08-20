"""Optional independent oracle for the pinned official UZH five-arm cohort.

This module intentionally imports no repository geometry implementation.  It
recomputes the five available arms from pose-join bytes with standard-library
binary64 math.  RETIRE_WARP and retire receipts are outside this test.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import unittest
from collections import Counter
from pathlib import Path


ROOT_ENV = "REDRED_UZH_SIXARM_ORACLE_POSE_JOIN"
START_NS = 41_321_000_000
DELAY_NS = 4_998_186
SCALE = 10**12
ARMS = ("RAW", "SENSOR_FIXED", "MC_CORRECT", "MC_WRONG", "MC_DELAYED")

SOURCE_HASHES = {
    "events_pose_join.jsonl": "a49b7d813fde313bfbcc27526e337c7268ab11803a19898feee8f27afc576796",
    "poses.jsonl": "4461d867e8adc8daaeb089fc739613ee7c89ac2f32c825de561ba88ff83ca0c1",
    "calibration.json": "bf718266f210e0bf7d64ff31b1fb4d125f905b0f67d6070976bdaf25ec450cdb",
}
ORDERED_ID_LF_SHA256 = "0eb870ed84539b786d8944330d0618509b7e331eab4ca4b4bba21bc51c3e44f0"
COMPACT_ID_ARRAY_LF_SHA256 = "3bfedeb52763572d42d285b5b7483356f5156e535e657ecb67b0f1f7cf2a90ac"

ARM_IDENTITIES = {
    "RAW": (578_088, "9eff30df05a770cee5930929faa9816a5235cb4e8a6b29c185379e38535b03c2"),
    "SENSOR_FIXED": (797_101, "9009a43c69da4537169e8145935c777259196f04e248c2de85f1d5bb632c8771"),
    "MC_CORRECT": (794_910, "39529955f2565be311b44f45e3d5012a5906bcde7efa3d8de5ce44c07189a189"),
    "MC_WRONG": (792_721, "3ed987fa2fa239b3bd0ec1c520392dd4edff250de19e34ae3e7804d2878bde32"),
    "MC_DELAYED": (794_925, "9389abf2ecaba4d922511f153703fe1e9547f4da912c2c9c6c599a45747c3df3"),
}
COMBINED_IDENTITY = (
    2_893_109,
    "55566cdc189c3519f56ac8d648a74c7b33bb003067e0b1c53c62b404a89cfe2a",
)
DELAYED_BRACKET_STREAM_SHA256 = "b4b70878ec598fe450ccffb835df16d559c95a1c6b9143530c412b0e51db6e56"

EXPECTED_OOF = {
    "RAW": [],
    "SENSOR_FIXED": [13_856_524, 13_856_654, 13_856_794, 13_857_092, 13_857_160, 13_857_171],
    "MC_CORRECT": [13_856_524, 13_856_654, 13_856_794, 13_857_092, 13_857_160, 13_857_171],
    "MC_WRONG": [13_856_285, 13_856_525, 13_856_993, 13_857_224, 13_857_294, 13_857_334],
    "MC_DELAYED": [
        13_856_285, 13_856_487, 13_856_525, 13_856_576, 13_856_993,
        13_856_995, 13_857_174, 13_857_224, 13_857_288, 13_857_294,
        13_857_334,
    ],
}
EXPECTED_COUNTS = {
    "RAW": {"in_fov": 1_100},
    "SENSOR_FIXED": {"in_fov": 1_094, "outside_reference_image": 6},
    "MC_CORRECT": {"in_fov": 1_094, "outside_reference_image": 6},
    "MC_WRONG": {"in_fov": 1_094, "outside_reference_image": 6},
    "MC_DELAYED": {"in_fov": 1_089, "outside_reference_image": 11},
}


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def q12(value: float | None) -> int | None:
    if value is None:
        return None
    return (-1 if value < 0.0 else 1) * math.floor(abs(value) * SCALE + 0.5)


def normalize(vector):
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude <= 0.0 or not math.isfinite(magnitude):
        raise AssertionError("invalid independent-oracle vector")
    return tuple(value / magnitude for value in vector)


def slerp(before, after, alpha):
    left, right = normalize(before), normalize(after)
    cosine = sum(a * b for a, b in zip(left, right))
    if cosine < 0.0:
        right = tuple(-value for value in right)
        cosine = -cosine
    cosine = min(1.0, max(-1.0, cosine))
    if cosine > 0.9995:
        return normalize(tuple((1.0 - alpha) * left[i] + alpha * right[i] for i in range(4)))
    theta = math.acos(cosine)
    sine = math.sin(theta)
    return normalize(tuple(
        math.sin((1.0 - alpha) * theta) / sine * left[i]
        + math.sin(alpha * theta) / sine * right[i]
        for i in range(4)
    ))


def quaternion_matrix(quaternion):
    x, y, z, w = normalize(quaternion)
    return (
        (1.0 - 2.0 * (y*y + z*z), 2.0 * (x*y - z*w), 2.0 * (x*z + y*w)),
        (2.0 * (x*y + z*w), 1.0 - 2.0 * (x*x + z*z), 2.0 * (y*z - x*w)),
        (2.0 * (x*z - y*w), 2.0 * (y*z + x*w), 1.0 - 2.0 * (x*x + y*y)),
    )


def transpose(matrix):
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def matmul(left, right):
    return tuple(tuple(
        sum(left[row][k] * right[k][column] for k in range(3))
        for column in range(3)
    ) for row in range(3))


def matvec(matrix, vector):
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))


def distort(x, y, calibration):
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    radius2 = x*x + y*y
    radial = 1.0 + k1*radius2 + k2*radius2*radius2 + k3*radius2*radius2*radius2
    delta_x = 2.0*p1*x*y + p2*(radius2 + 2.0*x*x)
    delta_y = p1*(radius2 + 2.0*y*y) + 2.0*p2*x*y
    return x*radial + delta_x, y*radial + delta_y


def undistort_newton(xd, yd, calibration):
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    x, y = xd, yd
    for _ in range(50):
        projected_x, projected_y = distort(x, y, calibration)
        residual_x, residual_y = projected_x - xd, projected_y - yd
        radius2 = x*x + y*y
        radial = 1.0 + k1*radius2 + k2*radius2*radius2 + k3*radius2*radius2*radius2
        gradient = k1 + 2.0*k2*radius2 + 3.0*k3*radius2*radius2
        dr_dx, dr_dy = 2.0*x*gradient, 2.0*y*gradient
        j00 = radial + x*dr_dx + 2.0*p1*y + 6.0*p2*x
        j01 = x*dr_dy + 2.0*p1*x + 2.0*p2*y
        j10 = y*dr_dx + 2.0*p1*x + 2.0*p2*y
        j11 = radial + y*dr_dy + 6.0*p1*y + 2.0*p2*x
        determinant = j00*j11 - j01*j10
        if not math.isfinite(determinant) or abs(determinant) < 1e-18:
            raise AssertionError("singular independent radtan inverse")
        step_x = (j11*residual_x - j01*residual_y) / determinant
        step_y = (-j10*residual_x + j00*residual_y) / determinant
        x -= step_x
        y -= step_y
        if max(abs(step_x), abs(step_y), abs(residual_x), abs(residual_y)) < 2e-15:
            return x, y
    raise AssertionError("independent radtan inverse did not converge")


def raw_ray(x_raw, y_raw, calibration):
    fx, fy, cx, cy = calibration[:4]
    xu, yu = undistort_newton((x_raw - cx) / fx, (y_raw - cy) / fy, calibration)
    return normalize((xu, yu, 1.0))


def project(ray, calibration):
    fx, fy, cx, cy = calibration[:4]
    if not all(math.isfinite(value) for value in ray):
        return {"status": "invalid_distortion", "x": None, "y": None,
                "x_pixel": None, "y_pixel": None}
    if ray[2] <= 0.0:
        return {"status": "behind_reference", "x": None, "y": None,
                "x_pixel": None, "y_pixel": None}
    xd, yd = distort(ray[0] / ray[2], ray[1] / ray[2], calibration)
    x_float, y_float = fx*xd + cx, fy*yd + cy
    if not math.isfinite(x_float) or not math.isfinite(y_float):
        return {"status": "invalid_distortion", "x": None, "y": None,
                "x_pixel": None, "y_pixel": None}
    if not (0.0 <= x_float <= 239.0 and 0.0 <= y_float <= 179.0):
        return {"status": "outside_reference_image", "x": x_float, "y": y_float,
                "x_pixel": None, "y_pixel": None}
    return {"status": "in_fov", "x": x_float, "y": y_float,
            "x_pixel": math.floor(x_float + 0.5),
            "y_pixel": math.floor(y_float + 0.5)}


def pose_quaternion(row):
    value = row["quaternion_exact_decimal"]
    return tuple(float(value[name]) for name in ("qx", "qy", "qz", "qw"))


def pose_at(poses, times, timestamp_ns):
    right_index = bisect.bisect_right(times, timestamp_ns)
    left_index = right_index - 1
    if left_index < 0 or right_index >= len(poses):
        raise AssertionError(f"timestamp {timestamp_ns} lacks a closed pose bracket")
    left, right = poses[left_index], poses[right_index]
    numerator = timestamp_ns - left["timestamp_ns"]
    denominator = right["timestamp_ns"] - left["timestamp_ns"]
    if not 0 <= numerator < denominator:
        raise AssertionError("invalid independent pose bracket")
    quaternion = slerp(pose_quaternion(left), pose_quaternion(right), numerator / denominator)
    bracket = {
        "left_source_pose_index": left_index,
        "right_source_pose_index": right_index,
        "left_timestamp_ns": left["timestamp_ns"],
        "right_timestamp_ns": right["timestamp_ns"],
        "alpha_numerator_ns": numerator,
        "alpha_denominator_ns": denominator,
    }
    return quaternion, bracket


def canonical_geometry(ray, projection, locality):
    return {
        "geometry_status": projection["status"],
        "reference_ray_q12": [q12(value) for value in ray],
        "projected_x_q12": q12(projection["x"]),
        "projected_y_q12": q12(projection["y"]),
        "projected_x_pixel": projection["x_pixel"],
        "projected_y_pixel": projection["y_pixel"],
        "locality_x_q12": q12(locality[0]),
        "locality_y_q12": q12(locality[1]),
    }


def arm_row(arm, event, lookup, bracket, ray, projection, locality):
    return {
        "schema": "redred.uzh_sixarm_independent_oracle.arm/v1",
        "arm": arm,
        "dataset_event_index": event["dataset_event_index"],
        "join_sequence_index": event["join_sequence_index"],
        "timestamp_ns": event["timestamp_ns"],
        "raw": {"x": event["x"], "y": event["y"],
                "polarity_01": event["polarity_01"]},
        "pose_lookup_timestamp_ns": lookup,
        "pose_bracket": bracket,
        "geometry": canonical_geometry(ray, projection, locality),
    }


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def calculate(root):
    payloads = {name: (root / name).read_bytes() for name in SOURCE_HASHES}
    actual_hashes = {name: sha256(payload) for name, payload in payloads.items()}
    calibration_row = json.loads(payloads["calibration.json"].decode("ascii"))
    exact = calibration_row["parameters_exact_decimal"]
    calibration = tuple(float(exact[name]) for name in
                        ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"))
    poses = [row for row in load_jsonl(root / "poses.jsonl") if row.get("record_type") == "pose"]
    events = [row for row in load_jsonl(root / "events_pose_join.jsonl") if row.get("record_type") == "event"]
    times = [row["timestamp_ns"] for row in poses]
    reference_q, _ = pose_at(poses, times, START_NS)
    reference_inverse = transpose(quaternion_matrix(reference_q))
    rows = {arm: [] for arm in ARMS}
    delayed_brackets = []

    for event in events:
        timestamp = event["timestamp_ns"]
        occurrence_q, occurrence_bracket = pose_at(poses, times, timestamp)
        delayed_timestamp = timestamp - DELAY_NS
        delayed_q, delayed_bracket = pose_at(poses, times, delayed_timestamp)
        sensor_ray = raw_ray(event["x"], event["y"], calibration)
        occurrence_rotation = matmul(reference_inverse, quaternion_matrix(occurrence_q))
        correct_ray = normalize(matvec(occurrence_rotation, sensor_ray))
        wrong_ray = normalize(matvec(transpose(occurrence_rotation), sensor_ray))
        delayed_rotation = matmul(reference_inverse, quaternion_matrix(delayed_q))
        delayed_ray = normalize(matvec(delayed_rotation, sensor_ray))
        raw_projection = {
            "status": "in_fov", "x": float(event["x"]), "y": float(event["y"]),
            "x_pixel": event["x"], "y_pixel": event["y"],
        }
        correct_projection = project(correct_ray, calibration)
        wrong_projection = project(wrong_ray, calibration)
        delayed_projection = project(delayed_ray, calibration)
        definitions = {
            "RAW": (None, None, sensor_ray, raw_projection,
                    (float(event["x"]), float(event["y"]))),
            "SENSOR_FIXED": (timestamp, occurrence_bracket, correct_ray,
                             correct_projection,
                             (float(event["x"]), float(event["y"]))),
            "MC_CORRECT": (timestamp, occurrence_bracket, correct_ray,
                           correct_projection,
                           (correct_projection["x"], correct_projection["y"])),
            "MC_WRONG": (timestamp, occurrence_bracket, wrong_ray,
                         wrong_projection,
                         (wrong_projection["x"], wrong_projection["y"])),
            "MC_DELAYED": (delayed_timestamp, delayed_bracket, delayed_ray,
                           delayed_projection,
                           (delayed_projection["x"], delayed_projection["y"])),
        }
        for arm, (lookup, bracket, ray, projection, locality) in definitions.items():
            rows[arm].append(arm_row(
                arm, event, lookup, bracket, ray, projection, locality,
            ))
        delayed_brackets.append({
            "dataset_event_index": event["dataset_event_index"],
            "timestamp_ns": delayed_timestamp,
            "bracket": delayed_bracket,
        })

    return {
        "source_hashes": actual_hashes,
        "poses": poses,
        "events": events,
        "rows": rows,
        "delayed_brackets": delayed_brackets,
    }


class OfficialFiveArmIndependentOracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get(ROOT_ENV)
        if not value:
            raise unittest.SkipTest(f"set {ROOT_ENV} to the official pose-join directory")
        cls.root = Path(value)
        if not cls.root.is_dir():
            raise AssertionError(f"{ROOT_ENV} is not a directory: {cls.root}")
        cls.result = calculate(cls.root)

    def test_source_identity_and_exact_1100_id_order(self):
        self.assertEqual(self.result["source_hashes"], SOURCE_HASHES)
        self.assertEqual(len(self.result["poses"]), 11_883)
        events = self.result["events"]
        self.assertEqual(len(events), 1_100)
        ids = [row["dataset_event_index"] for row in events]
        self.assertEqual(ids, list(range(13_856_250, 13_857_350)))
        self.assertEqual([row["join_sequence_index"] for row in events], list(range(1_100)))
        id_lines = b"".join(f"{value}\n".encode("ascii") for value in ids)
        self.assertEqual(sha256(id_lines), ORDERED_ID_LF_SHA256)
        self.assertEqual(sha256(canonical(ids)), COMPACT_ID_ARRAY_LF_SHA256)
        for arm in ARMS:
            self.assertEqual(
                [row["dataset_event_index"] for row in self.result["rows"][arm]],
                ids,
            )

    def test_five_canonical_arm_hashes_and_combined_hash(self):
        rows = self.result["rows"]
        self.assertEqual(tuple(rows), ARMS)
        for arm in ARMS:
            payload = b"".join(canonical(row) for row in rows[arm])
            self.assertEqual((len(payload), sha256(payload)), ARM_IDENTITIES[arm], arm)

        combined = []
        for ordinal in range(1_100):
            first = rows["RAW"][ordinal]
            combined.append({
                "schema": "redred.uzh_sixarm_independent_oracle.available_five/v1",
                "dataset_event_index": first["dataset_event_index"],
                "join_sequence_index": first["join_sequence_index"],
                "timestamp_ns": first["timestamp_ns"],
                "raw": first["raw"],
                "arms": {
                    arm: rows[arm][ordinal]["geometry"] | {
                        "pose_lookup_timestamp_ns": rows[arm][ordinal]["pose_lookup_timestamp_ns"],
                        "pose_bracket": rows[arm][ordinal]["pose_bracket"],
                    }
                    for arm in ARMS
                },
            })
        payload = b"".join(canonical(row) for row in combined)
        self.assertEqual((len(payload), sha256(payload)), COMBINED_IDENTITY)

    def test_oof_ids_and_status_counts(self):
        events = self.result["events"]
        for arm in ARMS:
            statuses = [row["geometry"]["geometry_status"] for row in self.result["rows"][arm]]
            actual_oof = [
                event["dataset_event_index"]
                for event, status in zip(events, statuses)
                if status in ("outside_reference_image", "behind_reference")
            ]
            self.assertEqual(actual_oof, EXPECTED_OOF[arm], arm)
            self.assertEqual(dict(sorted(Counter(statuses).items())), EXPECTED_COUNTS[arm], arm)
            self.assertNotIn("behind_reference", statuses, arm)
            self.assertNotIn("invalid_distortion", statuses, arm)

    def test_exact_delayed_delta_and_fresh_bracket_stream(self):
        rows = self.result["rows"]["MC_DELAYED"]
        for row in rows:
            self.assertEqual(row["timestamp_ns"] - row["pose_lookup_timestamp_ns"], DELAY_NS)
            bracket = row["pose_bracket"]
            self.assertEqual(bracket["left_source_pose_index"], 8_240)
            self.assertEqual(bracket["right_source_pose_index"], 8_241)
            self.assertEqual(bracket["alpha_denominator_ns"], 4_999_580)
        self.assertEqual(rows[0]["pose_bracket"]["alpha_numerator_ns"], 3_713_522)
        self.assertEqual(rows[-1]["pose_bracket"]["alpha_numerator_ns"], 4_712_522)
        self.assertEqual(
            sha256(canonical(self.result["delayed_brackets"])),
            DELAYED_BRACKET_STREAM_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
