"""Boundary and mutation-focused tests for world-grid quantization."""

import math
import unittest

from benchmarks.redred_cluster2_cav_bridge.world_grid import (
    COORDINATE_CONVENTION,
    MAX_GRID_DIMENSION,
    PREVIOUS_PI,
    WorldGridCoordinate,
    WorldGridError,
    quantize_world_ray,
)


class WorldGridQuantizationTest(unittest.TestCase):
    def test_cardinal_axes_seam_and_poles(self):
        expected = {
            (1.0, 0.0, 0.0): (4, 2),
            (0.0, 1.0, 0.0): (6, 2),
            (0.0, -1.0, 0.0): (2, 2),
            (-1.0, 0.0, 0.0): (0, 2),
            (0.0, 0.0, 1.0): (0, 0),
            (0.0, 0.0, -1.0): (0, 3),
        }
        for ray, coordinates in expected.items():
            with self.subTest(ray=ray):
                result = quantize_world_ray(ray, 8, 4)
                self.assertEqual((result.x, result.y), coordinates)
                self.assertEqual(result.index, result.y * 8 + result.x)
                self.assertEqual(result.coordinate_convention, COORDINATE_CONVENTION)

    def test_signed_zero_has_one_seam_and_poles_have_one_column(self):
        positive = quantize_world_ray((-1.0, 0.0, 0.0), 16, 8)
        negative = quantize_world_ray((-1.0, -0.0, 0.0), 16, 8)
        north = quantize_world_ray((-0.0, -0.0, 1.0), 16, 8)
        south = quantize_world_ray((0.0, -0.0, -1.0), 16, 8)
        self.assertEqual((positive.x, negative.x), (0, 0))
        self.assertEqual(positive.azimuth_rad, -math.pi)
        self.assertEqual(negative.azimuth_rad, -math.pi)
        self.assertEqual((north.x, south.x), (0, 0))

    def test_positive_minimum_subnormal_before_seam_uses_last_column(self):
        minimum_subnormal = float.fromhex("0x0.0000000000001p-1022")
        result = quantize_world_ray((-1.0, minimum_subnormal, 0.0), 16, 8)
        self.assertEqual(result.x, 15)
        self.assertEqual(result.azimuth_rad, PREVIOUS_PI)
        self.assertLess(result.azimuth_rad, math.pi)

    def test_returned_angles_bind_the_axis_convention(self):
        positive_x = quantize_world_ray((1.0, 0.0, 0.0), 8, 4)
        positive_y = quantize_world_ray((0.0, 1.0, 0.0), 8, 4)
        north = quantize_world_ray((0.0, 0.0, 1.0), 8, 4)
        self.assertEqual(positive_x.azimuth_rad, 0.0)
        self.assertEqual(positive_y.azimuth_rad, math.pi / 2.0)
        self.assertEqual(north.elevation_rad, math.pi / 2.0)

    def test_formula_orientation_floor_and_row_major_stride(self):
        # atan2(y, x)=pi/6.  Swapping atan2 arguments moves this from x=7 to 8.
        ray = (math.sqrt(3.0) / 2.0, 0.5, 0.0)
        result = quantize_world_ray(ray, 12, 5)
        self.assertEqual((result.x, result.y, result.index), (7, 2, 31))

        # u=0.20 at azimuth=-0.6*pi: floor gives x=0 while rounding gives x=1.
        angle = -0.6 * math.pi
        floor_probe = quantize_world_ray((math.cos(angle), math.sin(angle), 0.0), 4, 3)
        self.assertEqual((floor_probe.x, floor_probe.y), (0, 1))
        self.assertEqual(floor_probe.index, 4)

    def test_rows_increase_from_north_to_south(self):
        horizontal = math.sqrt(3.0) / 2.0
        north = quantize_world_ray((horizontal, 0.0, 0.5), 8, 6)
        south = quantize_world_ray((horizontal, 0.0, -0.5), 8, 6)
        self.assertEqual((north.y, south.y), (2, 4))
        self.assertLess(north.y, south.y)

    def test_one_by_one_and_near_boundaries_stay_in_range(self):
        for ray in (
            (1.0, 0.0, 0.0),
            (-1.0, 1.0e-15, 0.0),
            (-1.0, -1.0e-15, 0.0),
            (0.0, 0.0, 1.0),
            (0.0, 0.0, -1.0),
        ):
            norm = math.sqrt(sum(component * component for component in ray))
            normalized = tuple(component / norm for component in ray)
            with self.subTest(ray=ray):
                result = quantize_world_ray(normalized, 1, 1)
                self.assertEqual((result.x, result.y, result.index), (0, 0, 0))

    def test_rejects_bad_dimensions(self):
        invalid = (
            (0, 1), (-1, 1), (1, 0), (1, -1),
            (True, 1), (1, False), (1.0, 1),
            (MAX_GRID_DIMENSION + 1, 1),
            (1, 10 ** 10_000),
        )
        for width, height in invalid:
            with self.subTest(width=width, height=height):
                with self.assertRaises(WorldGridError):
                    quantize_world_ray((1.0, 0.0, 0.0), width, height)

    def test_rejects_malformed_nonfinite_and_nonunit_rays(self):
        invalid = (
            None,
            "1,0,0",
            (1.0, 0.0),
            (1.0, 0.0, 0.0, 0.0),
            (True, 0.0, 0.0),
            (float("nan"), 0.0, 0.0),
            (float("inf"), 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (1.0 + 2.0e-9, 0.0, 0.0),
            (10 ** 10_000, 0, 0),
        )
        for ray in invalid:
            with self.subTest(ray=ray):
                with self.assertRaises(WorldGridError):
                    quantize_world_ray(ray, 8, 4)

    def test_unit_tolerance_boundary_is_explicit(self):
        accepted = quantize_world_ray((1.0 + 0.5e-9, 0.0, 0.0), 8, 4)
        self.assertEqual((accepted.x, accepted.y), (4, 2))

    def test_result_dataclass_fails_closed_on_mutation_like_construction(self):
        valid = dict(
            x=2,
            y=1,
            index=6,
            width=4,
            height=3,
            azimuth_rad=0.0,
            elevation_rad=0.0,
        )
        WorldGridCoordinate(**valid)
        mutations = (
            dict(valid, x=4),
            dict(valid, y=3),
            dict(valid, index=5),
            dict(valid, azimuth_rad=math.pi),
            # Keep the suite runnable on Python 3.8 (math.nextafter arrived
            # later) while still probing just beyond the closed pole range.
            dict(valid, elevation_rad=math.pi / 2.0 + 1.0e-12),
            dict(valid, coordinate_convention="swapped axes"),
            # All fields are individually in range, but the coordinates do
            # not describe the supplied angles.
            dict(valid, x=1, index=5),
            dict(valid, y=0, index=2),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(WorldGridError):
                    WorldGridCoordinate(**mutation)

    def test_direct_constructor_revalidates_exact_pole_convention(self):
        north = dict(
            x=0,
            y=0,
            index=0,
            width=4,
            height=3,
            azimuth_rad=-math.pi,
            elevation_rad=math.pi / 2.0,
        )
        WorldGridCoordinate(**north)
        for mutation in (
            dict(north, x=1, index=1),
            dict(north, azimuth_rad=0.0),
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(WorldGridError):
                    WorldGridCoordinate(**mutation)


if __name__ == "__main__":
    unittest.main()
