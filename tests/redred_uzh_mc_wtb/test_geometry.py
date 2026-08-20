"""Independent analytical tests for the UZH raw-reference geometry API."""

from __future__ import annotations

import bisect
import hashlib
import math
import os
import unittest
from pathlib import Path

from benchmarks.redred_uzh_mc_wtb import geometry


SQRT_HALF = math.sqrt(0.5)
WIDTH = 240
HEIGHT = 180
REAL_CALIBRATION = geometry.RadtanCalibration(
    width=WIDTH,
    height=HEIGHT,
    fx=199.092366542,
    fy=198.828820470,
    cx=132.192071378,
    cy=110.712660011,
    k1=-0.368436311798,
    k2=0.150947243557,
    p1=-0.000296130534385,
    p2=-0.000759431726241,
    k3=0.0,
)
ZERO_CALIBRATION = geometry.RadtanCalibration(
    width=WIDTH,
    height=HEIGHT,
    fx=200.0,
    fy=201.0,
    cx=119.5,
    cy=89.5,
    k1=0.0,
    k2=0.0,
    p1=0.0,
    p2=0.0,
    k3=0.0,
)


def _transpose(matrix: tuple[tuple[float, float, float], ...]):
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def _matmul(left, right):
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _matvec(matrix, vector):
    return tuple(sum(matrix[row][column] * vector[column] for column in range(3)) for row in range(3))


def _matrix_from_xyzw_oracle(quaternion):
    """Direct closed-form oracle, independent of the production module."""

    x, y, z, w = quaternion
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = (component / norm for component in (x, y, z, w))
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def _distort_oracle(x_u: float, y_u: float, calibration):
    r2 = x_u * x_u + y_u * y_u
    radial = 1.0 + calibration.k1 * r2 + calibration.k2 * r2 * r2 + calibration.k3 * r2 * r2 * r2
    return (
        x_u * radial + 2.0 * calibration.p1 * x_u * y_u + calibration.p2 * (r2 + 2.0 * x_u * x_u),
        y_u * radial + calibration.p1 * (r2 + 2.0 * y_u * y_u) + 2.0 * calibration.p2 * x_u * y_u,
    )


def _field(result, name):
    return result[name] if isinstance(result, dict) else getattr(result, name)


def _relative_geometry(reference_to_current, sensor_to_reference):
    return geometry.RelativeGeometry(
        rotation_reference_to_current=reference_to_current,
        rotation_sensor_to_reference=sensor_to_reference,
        translation_current_in_reference=(0.0, 0.0, 0.0),
        translation_reference_in_current=(0.0, 0.0, 0.0),
    )


def _identity_relative_geometry():
    identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return _relative_geometry(identity, identity)


def _undistorted_pair(result):
    if result.status != geometry.IN_FOV:
        raise AssertionError(f"expected valid undistortion, got {result.status!r}")
    return result.x_undistorted, result.y_undistorted


class GeometryTestCase(unittest.TestCase):
    def assertMatrixAlmostEqual(self, actual, expected, places=12):
        self.assertEqual(len(actual), 3)
        for actual_row, expected_row in zip(actual, expected):
            self.assertEqual(len(actual_row), 3)
            for actual_value, expected_value in zip(actual_row, expected_row):
                self.assertAlmostEqual(actual_value, expected_value, places=places)

    def assertVectorAlmostEqual(self, actual, expected, places=12):
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=places)


class QuaternionAnalyticalTests(GeometryTestCase):
    def test_identity_and_all_positive_axis_quarter_turns(self):
        cases = (
            ((0.0, 0.0, 0.0, 1.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
            ((SQRT_HALF, 0.0, 0.0, SQRT_HALF), ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))),
            ((0.0, SQRT_HALF, 0.0, SQRT_HALF), ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))),
            ((0.0, 0.0, SQRT_HALF, SQRT_HALF), ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
        )
        for quaternion, expected in cases:
            with self.subTest(quaternion=quaternion):
                self.assertMatrixAlmostEqual(
                    geometry.quaternion_xyzw_to_world_camera_matrix(quaternion), expected
                )

    def test_q_and_minus_q_are_the_same_rotation(self):
        quaternion = (0.182574185835, -0.365148371670, 0.547722557505, 0.730296743340)
        negative = tuple(-component for component in quaternion)
        self.assertMatrixAlmostEqual(
            geometry.quaternion_xyzw_to_world_camera_matrix(quaternion),
            geometry.quaternion_xyzw_to_world_camera_matrix(negative),
        )

    def test_xyzw_order_mutant_is_killed(self):
        xyzw = (SQRT_HALF, 0.0, 0.0, SQRT_HALF)
        expected_x_quarter_turn = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
        actual = geometry.quaternion_xyzw_to_world_camera_matrix(xyzw)
        self.assertMatrixAlmostEqual(actual, expected_x_quarter_turn)

        # The exact same four fields misread as wxyz produce this very different
        # rotation when passed to an xyzw implementation as (w,x,y,z).
        reordered_mutant = _matrix_from_xyzw_oracle((xyzw[3], xyzw[0], xyzw[1], xyzw[2]))
        maximum_difference = max(
            abs(actual[row][column] - reordered_mutant[row][column])
            for row in range(3)
            for column in range(3)
        )
        self.assertGreater(maximum_difference, 0.9)

    def test_rejects_zero_or_nonfinite_quaternion(self):
        for quaternion in ((0.0, 0.0, 0.0, 0.0), (math.nan, 0.0, 0.0, 1.0), (0.0, math.inf, 0.0, 1.0)):
            with self.subTest(quaternion=quaternion):
                with self.assertRaises(geometry.GeometryError):
                    geometry.quaternion_xyzw_to_world_camera_matrix(quaternion)


class RelativeRotationTests(GeometryTestCase):
    RX_90 = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
    RY_90 = ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))
    RZ_90 = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    def test_world_to_sensor_relative_direction(self):
        # Absolute source poses are camera-to-world.  For reference I and a
        # current +90 degree camera-to-world Z rotation, the model-facing
        # reference-world-to-current-sensor rotation is R_current^T.
        expected = _transpose(self.RZ_90)
        reference = geometry.WorldCameraPose((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
        current = geometry.WorldCameraPose(
            (0.0, 0.0, 0.0), (0.0, 0.0, SQRT_HALF, SQRT_HALF)
        )
        relative = geometry.relative_geometry(reference, current)
        actual = relative.rotation_world_to_sensor
        self.assertMatrixAlmostEqual(actual, expected)
        self.assertMatrixAlmostEqual(relative.rotation_sensor_to_reference, self.RZ_90)
        self.assertVectorAlmostEqual(_matvec(actual, (0.0, 1.0, 0.0)), (1.0, 0.0, 0.0))

    def test_noncommuting_multiplication_order(self):
        reference_camera_to_world = self.RX_90
        current_camera_to_world = _matmul(self.RZ_90, self.RY_90)
        expected = _matmul(_transpose(current_camera_to_world), reference_camera_to_world)
        reversed_mutant = _matmul(reference_camera_to_world, _transpose(current_camera_to_world))
        self.assertGreater(
            max(
                abs(expected[row][column] - reversed_mutant[row][column])
                for row in range(3)
                for column in range(3)
            ),
            0.9,
        )
        reference = geometry.WorldCameraPose(
            (0.0, 0.0, 0.0), (SQRT_HALF, 0.0, 0.0, SQRT_HALF)
        )
        current = geometry.WorldCameraPose(
            (0.0, 0.0, 0.0), (-0.5, 0.5, 0.5, 0.5)
        )
        self.assertMatrixAlmostEqual(geometry.relative_geometry(reference, current).rotation_world_to_sensor, expected)


class SlerpTests(GeometryTestCase):
    def test_endpoints_and_axis_midpoint(self):
        identity = (0.0, 0.0, 0.0, 1.0)
        z_quarter_turn = (0.0, 0.0, SQRT_HALF, SQRT_HALF)
        expected_midpoint = (0.0, 0.0, math.sin(math.pi / 8.0), math.cos(math.pi / 8.0))

        self.assertMatrixAlmostEqual(
            geometry.quaternion_xyzw_to_world_camera_matrix(geometry.slerp_xyzw(identity, z_quarter_turn, 0.0)),
            geometry.quaternion_xyzw_to_world_camera_matrix(identity),
        )
        self.assertMatrixAlmostEqual(
            geometry.quaternion_xyzw_to_world_camera_matrix(geometry.slerp_xyzw(identity, z_quarter_turn, 1.0)),
            geometry.quaternion_xyzw_to_world_camera_matrix(z_quarter_turn),
        )
        self.assertMatrixAlmostEqual(
            geometry.quaternion_xyzw_to_world_camera_matrix(geometry.slerp_xyzw(identity, z_quarter_turn, 0.5)),
            _matrix_from_xyzw_oracle(expected_midpoint),
        )

    def test_antipodal_endpoint_uses_same_shortest_arc(self):
        start = (0.0, 0.0, 0.0, 1.0)
        end = (0.0, 0.0, SQRT_HALF, SQRT_HALF)
        end_negative = tuple(-component for component in end)
        normal = geometry.slerp_xyzw(start, end, 0.375)
        antipodal = geometry.slerp_xyzw(start, end_negative, 0.375)
        self.assertMatrixAlmostEqual(
            geometry.quaternion_xyzw_to_world_camera_matrix(normal),
            geometry.quaternion_xyzw_to_world_camera_matrix(antipodal),
        )

    def test_slerp_rejects_extrapolation(self):
        for alpha in (-1e-12, 1.0 + 1e-12, math.nan):
            with self.subTest(alpha=alpha):
                with self.assertRaises(geometry.GeometryError):
                    geometry.slerp_xyzw((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.0), alpha)


class RadtanTests(GeometryTestCase):
    def test_zero_distortion_is_identity(self):
        points = ((0.0, 0.0), (-0.6, -0.4), (0.4, -0.3), (0.55, 0.42))
        for point in points:
            with self.subTest(point=point):
                self.assertVectorAlmostEqual(
                    geometry.distort_normalized(point[0], point[1], ZERO_CALIBRATION), point
                )
                self.assertVectorAlmostEqual(
                    _undistorted_pair(
                        geometry.undistort_normalized(point[0], point[1], ZERO_CALIBRATION)
                    ),
                    point,
                )

    def test_real_calibration_forward_values_and_roundtrip(self):
        # Raw pixels are first normalized into distorted coordinates.  The
        # returned undistorted point must independently re-distort to the raw
        # normalized coordinate, including at all four image corners.
        raw_pixels = ((0.0, 0.0), (239.0, 0.0), (0.0, 179.0), (239.0, 179.0), (132.0, 111.0))
        for u_raw, v_raw in raw_pixels:
            with self.subTest(raw_pixel=(u_raw, v_raw)):
                x_distorted = (u_raw - REAL_CALIBRATION.cx) / REAL_CALIBRATION.fx
                y_distorted = (v_raw - REAL_CALIBRATION.cy) / REAL_CALIBRATION.fy
                x_undistorted, y_undistorted = _undistorted_pair(
                    geometry.undistort_normalized(
                        x_distorted, y_distorted, REAL_CALIBRATION
                    )
                )
                oracle_redistorted = _distort_oracle(
                    x_undistorted, y_undistorted, REAL_CALIBRATION
                )
                self.assertAlmostEqual(oracle_redistorted[0], x_distorted, delta=2e-11)
                self.assertAlmostEqual(oracle_redistorted[1], y_distorted, delta=2e-11)
                self.assertVectorAlmostEqual(
                    geometry.distort_normalized(
                        x_undistorted, y_undistorted, REAL_CALIBRATION
                    ),
                    oracle_redistorted,
                )

    def test_real_calibration_corner_is_not_treated_as_rectified(self):
        x_distorted = -REAL_CALIBRATION.cx / REAL_CALIBRATION.fx
        y_distorted = -REAL_CALIBRATION.cy / REAL_CALIBRATION.fy
        x_undistorted, y_undistorted = _undistorted_pair(
            geometry.undistort_normalized(
                x_distorted, y_distorted, REAL_CALIBRATION
            )
        )
        u_undistorted = REAL_CALIBRATION.fx * x_undistorted + REAL_CALIBRATION.cx
        v_undistorted = REAL_CALIBRATION.fy * y_undistorted + REAL_CALIBRATION.cy
        self.assertAlmostEqual(u_undistorted, -37.706, delta=0.02)
        self.assertAlmostEqual(v_undistorted, -31.687, delta=0.02)

    def test_invalid_calibration_and_nonfinite_distortion_fail_closed(self):
        invalid_calibrations = (
            dict(width=240, height=180, fx=0.0, fy=200.0, cx=0.0, cy=0.0),
            dict(width=240, height=180, fx=200.0, fy=math.inf, cx=0.0, cy=0.0),
            dict(width=0, height=180, fx=200.0, fy=200.0, cx=0.0, cy=0.0),
        )
        for base in invalid_calibrations:
            with self.subTest(base=base):
                with self.assertRaises(geometry.GeometryError):
                    geometry.RadtanCalibration(
                        **base, k1=0.0, k2=0.0, p1=0.0, p2=0.0, k3=0.0
                    )

        with self.assertRaises(geometry.GeometryError):
            geometry.RadtanCalibration(
                width=240,
                height=180,
                fx=200.0,
                fy=200.0,
                cx=0.0,
                cy=0.0,
                k1=math.nan,
                k2=0.0,
                p1=0.0,
                p2=0.0,
                k3=0.0,
            )

        for point in ((math.nan, 0.0), (0.0, math.inf)):
            with self.subTest(point=point):
                result = geometry.undistort_normalized(
                    point[0], point[1], REAL_CALIBRATION
                )
                self.assertEqual(result.status, geometry.INVALID_DISTORTION)
                self.assertIsNone(result.x_undistorted)
                self.assertIsNone(result.y_undistorted)
                self.assertIsNone(
                    geometry.distort_normalized(point[0], point[1], REAL_CALIBRATION)
                )


class BoundaryAndWarpTests(GeometryTestCase):
    def test_continuous_raw_reference_boundaries_precede_rounding(self):
        calibration = geometry.RadtanCalibration(
            width=9,
            height=9,
            fx=4.0,
            fy=4.0,
            cx=4.0,
            cy=4.0,
            k1=0.0,
            k2=0.0,
            p1=0.0,
            p2=0.0,
            k3=0.0,
        )
        identity = _identity_relative_geometry()
        for coordinate in ((0.0, 0.0), (8.0, 8.0), (1e-12, 7.999999999999)):
            with self.subTest(in_fov=coordinate):
                result = geometry.warp_raw_sensor_to_reference(
                    coordinate[0], coordinate[1], calibration, identity
                )
                self.assertEqual(result.status, geometry.IN_FOV)

        # A valid Y-axis rotation places the center ray just beyond each
        # continuous x boundary.  It must be OOF before any pixel rounding.
        for target_x in (-1e-9, 8.0 + 1e-9):
            with self.subTest(outside_target_x=target_x):
                angle = math.atan((target_x - calibration.cx) / calibration.fx)
                cosine = math.cos(angle)
                sine = math.sin(angle)
                sensor_to_reference = (
                    (cosine, 0.0, sine),
                    (0.0, 1.0, 0.0),
                    (-sine, 0.0, cosine),
                )
                relative = _relative_geometry(
                    _transpose(sensor_to_reference), sensor_to_reference
                )
                result = geometry.warp_raw_sensor_to_reference(
                    calibration.cx, calibration.cy, calibration, relative
                )
                self.assertEqual(result.status, geometry.OUTSIDE_REFERENCE_IMAGE)
                self.assertAlmostEqual(result.x_reference_float, target_x, delta=1e-12)

    def test_rounding_is_floor_plus_one_half_not_bankers_rounding(self):
        cases = (
            (0.0, 0),
            (0.499999, 0),
            (0.5, 1),
            (0.500001, 1),
            (2.5, 3),
            (238.499999, 238),
            (238.5, 239),
            (239.0, 239),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(geometry.deterministic_pixel_round(value), expected)

    def test_identity_raw_reference_warp(self):
        for u_raw, v_raw in ((0, 0), (132, 111), (239, 179)):
            with self.subTest(raw_pixel=(u_raw, v_raw)):
                result = geometry.warp_raw_sensor_to_reference(
                    u_raw, v_raw, REAL_CALIBRATION, _identity_relative_geometry()
                )
                self.assertEqual(_field(result, "status"), "in_fov")
                self.assertAlmostEqual(_field(result, "x_reference_float"), u_raw, delta=2e-8)
                self.assertAlmostEqual(_field(result, "y_reference_float"), v_raw, delta=2e-8)
                self.assertEqual(_field(result, "x_reference"), u_raw)
                self.assertEqual(_field(result, "y_reference"), v_raw)

    def test_axis_rotation_and_relative_direction_are_observable_in_warp(self):
        calibration = geometry.RadtanCalibration(
            width=9,
            height=9,
            fx=2.0,
            fy=2.0,
            cx=4.0,
            cy=4.0,
            k1=0.0,
            k2=0.0,
            p1=0.0,
            p2=0.0,
            k3=0.0,
        )
        # The API consumes R_Ct_C0 (world/reference to current sensor) and
        # transposes it to warp a current sensor ray into the reference frame.
        world_to_sensor = ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        relative = _relative_geometry(world_to_sensor, _transpose(world_to_sensor))
        result = geometry.warp_raw_sensor_to_reference(6.0, 4.0, calibration, relative)
        self.assertEqual(_field(result, "status"), "in_fov")
        self.assertAlmostEqual(_field(result, "x_reference_float"), 4.0, places=12)
        self.assertAlmostEqual(_field(result, "y_reference_float"), 6.0, places=12)

    def test_behind_reference_is_a_valid_oof_not_invalid_geometry(self):
        flip_z = ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0))
        relative = _relative_geometry(flip_z, flip_z)
        result = geometry.warp_raw_sensor_to_reference(132.0, 111.0, REAL_CALIBRATION, relative)
        self.assertEqual(_field(result, "status"), geometry.BEHIND_REFERENCE)
        self.assertTrue(result.requires_raw_escape)
        self.assertFalse(result.geometry_invalid)


class ExactExternalOneMillisecondTests(GeometryTestCase):
    ROOT_ENV = "REDRED_UZH_SHAPES_ROTATION_ROOT"
    EXPECTED_HASHES = {
        "events.txt": "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda",
        "groundtruth.txt": "bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb",
        "calib.txt": "ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd",
    }
    START_NS = 41_321_000_000
    END_NS = 41_322_000_000

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _timestamp_ns(token: str) -> int:
        whole, fraction = token.split(".", 1)
        if len(fraction) != 9 or not whole.isdigit() or not fraction.isdigit():
            raise AssertionError(f"non-canonical timestamp: {token!r}")
        return int(whole) * 1_000_000_000 + int(fraction)

    def _load_external(self):
        root_value = os.environ.get(self.ROOT_ENV)
        if not root_value:
            self.skipTest(f"set {self.ROOT_ENV} to run the pinned 509 MB integration")
        root = Path(root_value)
        paths = {name: root / name for name in self.EXPECTED_HASHES}
        for name, path in paths.items():
            self.assertTrue(path.is_file(), f"missing pinned external member: {path}")
            self.assertFalse(path.is_symlink(), f"external member must not be a symlink: {path}")
            self.assertEqual(self._sha256(path), self.EXPECTED_HASHES[name], name)
        return paths

    def test_exact_1ms_conservation_1094_plus_6(self):
        paths = self._load_external()

        calibration_tokens = paths["calib.txt"].read_text(encoding="ascii").split()
        self.assertEqual(len(calibration_tokens), 9)
        calibration_values = tuple(float(token) for token in calibration_tokens)
        calibration = geometry.RadtanCalibration(
            width=WIDTH,
            height=HEIGHT,
            fx=calibration_values[0],
            fy=calibration_values[1],
            cx=calibration_values[2],
            cy=calibration_values[3],
            k1=calibration_values[4],
            k2=calibration_values[5],
            p1=calibration_values[6],
            p2=calibration_values[7],
            k3=calibration_values[8],
        )

        pose_times = []
        pose_quaternions = []
        with paths["groundtruth.txt"].open("r", encoding="ascii", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                fields = line.rstrip("\n").split(" ")
                self.assertEqual(len(fields), 8, f"groundtruth line {line_number}")
                pose_times.append(self._timestamp_ns(fields[0]))
                pose_quaternions.append(tuple(float(value) for value in fields[4:8]))
        self.assertEqual(len(pose_times), 11_883)
        self.assertEqual(pose_times, sorted(pose_times))

        def interpolated_quaternion(timestamp_ns: int):
            right = bisect.bisect_left(pose_times, timestamp_ns)
            if right < len(pose_times) and pose_times[right] == timestamp_ns:
                return pose_quaternions[right]
            self.assertGreater(right, 0)
            self.assertLess(right, len(pose_times))
            left = right - 1
            alpha = (timestamp_ns - pose_times[left]) / (pose_times[right] - pose_times[left])
            return geometry.slerp_xyzw(
                pose_quaternions[left], pose_quaternions[right], alpha
            )

        reference_pose = geometry.WorldCameraPose(
            (0.0, 0.0, 0.0), interpolated_quaternion(self.START_NS)
        )
        selected = []
        with paths["events.txt"].open("r", encoding="ascii", newline="") as stream:
            for dataset_index, line in enumerate(stream):
                fields = line.rstrip("\n").split(" ")
                self.assertEqual(len(fields), 4, f"events dataset index {dataset_index}")
                timestamp_ns = self._timestamp_ns(fields[0])
                if self.START_NS <= timestamp_ns < self.END_NS:
                    selected.append((dataset_index, timestamp_ns, int(fields[1]), int(fields[2]), int(fields[3])))
                elif timestamp_ns >= self.END_NS and selected:
                    break
        self.assertEqual(len(selected), 1_100)
        self.assertEqual(selected[0][0], 13_856_250)
        self.assertEqual(selected[-1][0], 13_857_349)

        in_fov = 0
        outside = 0
        behind = 0
        results = []
        for dataset_index, timestamp_ns, u_raw, v_raw, polarity in selected:
            self.assertIn(polarity, (0, 1), dataset_index)
            current_pose = geometry.WorldCameraPose(
                (0.0, 0.0, 0.0), interpolated_quaternion(timestamp_ns)
            )
            relative = geometry.relative_geometry(
                reference_pose, current_pose
            )
            result = geometry.warp_raw_sensor_to_reference(
                u_raw, v_raw, calibration, relative
            )
            results.append((dataset_index, result))
            status = _field(result, "status")
            if status == "in_fov":
                in_fov += 1
            elif status == geometry.OUTSIDE_REFERENCE_IMAGE:
                outside += 1
            elif status == geometry.BEHIND_REFERENCE:
                behind += 1
            else:
                self.fail(f"event {dataset_index} was dropped/invalid: {status!r}")

        self.assertEqual(len(results), 1_100)
        self.assertEqual(in_fov, 1_094)
        self.assertEqual(outside, 6)
        self.assertEqual(behind, 0)
        self.assertEqual(in_fov + outside + behind, len(selected))


if __name__ == "__main__":
    unittest.main()
