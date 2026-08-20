"""Independent numerical and fail-closed tests for the v3 focus metric."""

from __future__ import annotations

import math
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.focus import (
    METRIC_ID,
    FocusMetricError,
    FocusSample,
    PaddedCanvas,
    compute_focus,
    compute_focus_by_arm,
)


CANVAS = PaddedCanvas(width=240, height=180, padding_px=16.0)


def sample(event_id: int, x: float, y: float, polarity: int) -> FocusSample:
    return FocusSample(event_id=event_id, x=x, y=y, polarity=polarity)


class FocusMetricTests(unittest.TestCase):
    def test_two_pair_analytic_golden_and_energy_decomposition(self):
        sigma = 2.0
        events = [
            sample(1, 10.0, 10.0, 0),
            sample(2, 14.0, 10.0, 0),
            sample(3, 20.0, 20.0, 1),
            sample(4, 20.0, 20.0, 1),
        ]
        result = compute_focus(events, sigma_px=sigma, canvas=CANVAS)

        # For each two-event channel the ordered-pair mean is its one pair's
        # exp(-d^2/(4 sigma^2)); combine channels with equal denominators.
        expected_score = (math.exp(-1.0) + 1.0) / 2.0
        self_constant = 1.0 / (4.0 * math.pi * sigma * sigma)
        self.assertEqual(result.metric_id, METRIC_ID)
        self.assertEqual(result.event_count, 4)
        self.assertEqual(result.total_mass, 4.0)
        self.assertAlmostEqual(result.score, expected_score, places=15)
        self.assertAlmostEqual(result.self_energy, 4.0 * self_constant, places=15)
        self.assertAlmostEqual(
            result.cross_event_energy,
            self_constant * 2.0 * (math.exp(-1.0) + 1.0),
            places=15,
        )
        self.assertAlmostEqual(
            result.raw_energy,
            result.self_energy + result.cross_event_energy,
            places=15,
        )
        self.assertAlmostEqual(
            result.maximum_cross_event_energy, self_constant * 4.0, places=15
        )

    def test_three_point_channel_uses_all_ordered_pairs(self):
        events = [
            sample(1, 0.0, 0.0, 0),
            sample(2, 1.0, 0.0, 0),
            sample(3, 3.0, 0.0, 0),
            sample(4, 20.0, 0.0, 1),
            sample(5, 21.0, 0.0, 1),
        ]
        result = compute_focus(events, sigma_px=1.0, canvas=CANVAS)
        e1 = math.exp(-1.0 / 4.0)
        e2 = math.exp(-4.0 / 4.0)
        e3 = math.exp(-9.0 / 4.0)
        # p0 has six ordered pairs and p1 has two; each unordered term appears twice.
        expected = (2.0 * (e1 + e2 + e3) + 2.0 * e1) / 8.0
        self.assertEqual(result.polarity_0.ordered_pair_count, 6)
        self.assertEqual(result.polarity_1.ordered_pair_count, 2)
        self.assertAlmostEqual(result.score, expected, places=15)

    def test_global_subpixel_translation_and_record_order_are_invariant(self):
        base = [
            sample(1, 30.125, 40.625, 0),
            sample(2, 31.375, 41.125, 0),
            sample(3, 90.25, 70.75, 1),
            sample(4, 92.0, 69.5, 1),
        ]
        shifted = [
            sample(value.event_id, value.x + 0.371, value.y + 0.619, value.polarity)
            for value in reversed(base)
        ]
        first = compute_focus(base, sigma_px=0.8, canvas=CANVAS)
        second = compute_focus(shifted, sigma_px=0.8, canvas=CANVAS)
        self.assertAlmostEqual(first.score, second.score, places=14)
        self.assertAlmostEqual(first.self_energy, second.self_energy, places=15)
        self.assertAlmostEqual(first.cross_event_energy, second.cross_event_energy, places=14)

    def test_grid_phase_sweep_has_no_integer_pixel_preference(self):
        base = [
            sample(1, 50.0, 50.0, 0),
            sample(2, 50.7, 50.2, 0),
            sample(3, 80.0, 80.0, 1),
            sample(4, 80.3, 81.1, 1),
        ]
        scores = []
        for dx in (0.0, 0.25, 0.5, 0.75):
            for dy in (0.0, 0.25, 0.5, 0.75):
                phase = [
                    sample(value.event_id, value.x + dx, value.y + dy, value.polarity)
                    for value in base
                ]
                scores.append(compute_focus(phase, sigma_px=1.0, canvas=CANVAS).score)
        self.assertLessEqual(max(scores) - min(scores), 2e-16)

    def test_opposite_polarities_never_overlap(self):
        coincident_cross_polarity = [
            sample(1, 10.0, 10.0, 0),
            sample(2, 30.0, 10.0, 0),
            sample(3, 10.0, 10.0, 1),
            sample(4, 30.0, 10.0, 1),
        ]
        separated_cross_polarity = [
            sample(1, 10.0, 10.0, 0),
            sample(2, 30.0, 10.0, 0),
            sample(3, 60.0, 60.0, 1),
            sample(4, 80.0, 60.0, 1),
        ]
        first = compute_focus(coincident_cross_polarity, sigma_px=2.0, canvas=CANVAS)
        second = compute_focus(separated_cross_polarity, sigma_px=2.0, canvas=CANVAS)
        self.assertAlmostEqual(first.score, second.score, places=15)

    def test_coincident_cloud_reaches_one_and_spread_cloud_is_lower(self):
        concentrated = [
            sample(1, 12.3, 15.7, 0), sample(2, 12.3, 15.7, 0),
            sample(3, 40.2, 44.8, 1), sample(4, 40.2, 44.8, 1),
        ]
        spread = [
            sample(1, 10.0, 10.0, 0), sample(2, 20.0, 20.0, 0),
            sample(3, 40.0, 40.0, 1), sample(4, 50.0, 50.0, 1),
        ]
        sharp = compute_focus(concentrated, sigma_px=1.0, canvas=CANVAS)
        diffuse = compute_focus(spread, sigma_px=1.0, canvas=CANVAS)
        self.assertEqual(sharp.score, 1.0)
        self.assertGreater(sharp.score, diffuse.score)

    def test_arm_comparison_enforces_equal_ids_polarity_and_unit_mass(self):
        arm_a = [
            sample(10, 10.0, 10.0, 0), sample(11, 11.0, 10.0, 0),
            sample(12, 20.0, 20.0, 1), sample(13, 21.0, 20.0, 1),
        ]
        arm_b = [
            sample(13, 20.5, 20.0, 1), sample(11, 10.5, 10.0, 0),
            sample(10, 10.0, 10.0, 0), sample(12, 20.0, 20.0, 1),
        ]
        results = compute_focus_by_arm(
            {"CONTROL": arm_a, "MC": arm_b}, sigma_px=1.0, canvas=CANVAS
        )
        self.assertEqual(set(results), {"CONTROL", "MC"})
        self.assertEqual(results["CONTROL"].total_mass, results["MC"].total_mass)
        self.assertGreater(results["MC"].score, results["CONTROL"].score)

        with self.assertRaisesRegex(FocusMetricError, "equal event IDs and polarities"):
            compute_focus_by_arm(
                {"CONTROL": arm_a, "BAD": arm_b[:-1]}, sigma_px=1.0, canvas=CANVAS
            )
        changed_polarity = list(arm_b)
        changed_polarity[0] = sample(13, 20.5, 20.0, 0)
        with self.assertRaisesRegex(FocusMetricError, "equal event IDs and polarities"):
            compute_focus_by_arm(
                {"CONTROL": arm_a, "BAD": changed_polarity},
                sigma_px=1.0,
                canvas=CANVAS,
            )

    def test_canvas_is_inclusive_and_outside_never_clips_or_drops(self):
        edge_events = [
            sample(1, CANVAS.x_min, CANVAS.y_min, 0),
            sample(2, CANVAS.x_max, CANVAS.y_max, 0),
            sample(3, CANVAS.x_min, CANVAS.y_max, 1),
            sample(4, CANVAS.x_max, CANVAS.y_min, 1),
        ]
        self.assertEqual(
            compute_focus(edge_events, sigma_px=1.0, canvas=CANVAS).event_count, 4
        )
        outside = list(edge_events)
        outside[0] = sample(1, math.nextafter(CANVAS.x_min, -math.inf), 0.0, 0)
        with self.assertRaisesRegex(FocusMetricError, "outside the fixed padded canvas"):
            compute_focus(outside, sigma_px=1.0, canvas=CANVAS)

    def test_degenerate_polarity_channels_fail_closed(self):
        cases = (
            [],
            [sample(1, 0.0, 0.0, 0)],
            [sample(1, 0.0, 0.0, 0), sample(2, 1.0, 0.0, 0)],
            [sample(1, 0.0, 0.0, 0), sample(2, 1.0, 0.0, 1)],
        )
        for events in cases:
            with self.subTest(count=len(events)):
                with self.assertRaises(FocusMetricError):
                    compute_focus(events, sigma_px=1.0, canvas=CANVAS)

    def test_invalid_parameters_and_samples_fail_closed(self):
        valid = [
            sample(1, 0.0, 0.0, 0), sample(2, 1.0, 0.0, 0),
            sample(3, 0.0, 1.0, 1), sample(4, 1.0, 1.0, 1),
        ]
        for sigma in (0.0, -1.0, math.inf, math.nan, True):
            with self.subTest(sigma=sigma):
                with self.assertRaises(FocusMetricError):
                    compute_focus(valid, sigma_px=sigma, canvas=CANVAS)
        with self.assertRaises(FocusMetricError):
            compute_focus(valid, sigma_px=1.0, canvas=CANVAS, minimum_events_per_polarity=1)
        with self.assertRaisesRegex(FocusMetricError, "duplicate event_id"):
            compute_focus(valid + [sample(1, 2.0, 2.0, 0)], sigma_px=1.0, canvas=CANVAS)
        with self.assertRaisesRegex(FocusMetricError, "polarity"):
            compute_focus(valid[:-1] + [sample(4, 1.0, 1.0, 2)], sigma_px=1.0, canvas=CANVAS)
        with self.assertRaisesRegex(FocusMetricError, "polarity"):
            compute_focus(
                valid[:-1] + [sample(4, 1.0, 1.0, 1.0)],
                sigma_px=1.0,
                canvas=CANVAS,
            )
        with self.assertRaisesRegex(FocusMetricError, "finite"):
            compute_focus(valid[:-1] + [sample(4, math.nan, 1.0, 1)], sigma_px=1.0, canvas=CANVAS)
        with self.assertRaisesRegex(FocusMetricError, "not a FocusSample"):
            compute_focus(valid[:-1] + [{"event_id": 4}], sigma_px=1.0, canvas=CANVAS)
        with self.assertRaisesRegex(FocusMetricError, "canvas must be"):
            compute_focus_by_arm({"CONTROL": valid}, sigma_px=1.0, canvas=None)

    def test_canvas_validation_fails_closed(self):
        invalid = (
            {"width": 0, "height": 180, "padding_px": 16.0},
            {"width": 240, "height": -1, "padding_px": 16.0},
            {"width": 240, "height": 180, "padding_px": -0.1},
            {"width": 240, "height": 180, "padding_px": math.nan},
            {"width": 240, "height": 180, "padding_px": 16.0, "origin_x": math.inf},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(FocusMetricError):
                    PaddedCanvas(**kwargs)


if __name__ == "__main__":
    unittest.main()
