from __future__ import annotations

import math
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.statistics import (
    StatisticsFailure,
    analyze_multiple_windows,
    equal_timestamp_clusters,
    moving_block_cluster_draws,
    paired_effect_sizes,
)


def window(window_id, group, timestamps, sample_ids, baseline, candidate):
    return {
        "window_id": window_id,
        "dependence_group": group,
        "timestamps_ns": timestamps,
        "sample_ids": sample_ids,
        "baseline": baseline,
        "candidate": candidate,
    }


def bootstrap(*, resamples=120, block=1, seed="MCWTB-V3-STATISTICS-TEST"):
    return {
        "resamples": resamples,
        "block_length_clusters": block,
        "seed_text": seed,
    }


def gate(windows, *, threshold=0.1, alpha=0.05, hypotheses=None):
    if hypotheses is None:
        hypotheses = len(windows) + 1
    return {
        "gate_id": "MCWTB-V3-TEST-GATE",
        "familywise_alpha": alpha,
        "familywise_hypotheses": hypotheses,
        "minimum_relative_reduction_strictly_greater_than": threshold,
        "predeclared_window_ids": [row["window_id"] for row in windows],
        "predeclared_dependence_groups": {
            row["window_id"]: row["dependence_group"] for row in windows
        },
    }


class EqualTimestampMovingBlockTests(unittest.TestCase):
    def test_clusters_preserve_all_timestamp_ties(self):
        self.assertEqual(
            equal_timestamp_clusters([10, 10, 11, 12, 12, 12]),
            ((0, 1), (2,), (3, 4, 5)),
        )
        draws = moving_block_cluster_draws(
            [10, 10, 11, 12, 12, 12],
            block_length_clusters=2,
            resamples=64,
            seed_text="ties",
        )
        for draw in draws:
            counts = [draw.count(index) for index in range(6)]
            self.assertEqual(counts[0], counts[1])
            self.assertEqual(counts[3], counts[4])
            self.assertEqual(counts[4], counts[5])

    def test_moving_blocks_are_circular_and_adjacent(self):
        draws = moving_block_cluster_draws(
            [0, 1, 2, 3, 4],
            block_length_clusters=2,
            resamples=32,
            seed_text="adjacent",
        )
        for draw in draws:
            self.assertEqual(len(draw), 5)
            self.assertEqual(draw[1], (draw[0] + 1) % 5)
            self.assertEqual(draw[3], (draw[2] + 1) % 5)

    def test_draws_are_byte_policy_deterministic_and_stream_bound(self):
        args = dict(
            timestamps_ns=[0, 0, 1, 2, 3],
            block_length_clusters=2,
            resamples=20,
            seed_text="fixed-seed",
        )
        first = moving_block_cluster_draws(**args, stream_id="W10")
        second = moving_block_cluster_draws(**args, stream_id="W10")
        other = moving_block_cluster_draws(**args, stream_id="W30")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_cluster_inputs_fail_closed(self):
        invalid = (
            ([1, 0], "nondecreasing"),
            ([False, 1], "integer"),
            ([], "must not be empty"),
        )
        for timestamps, message in invalid:
            with self.subTest(timestamps=timestamps):
                with self.assertRaisesRegex(StatisticsFailure, message):
                    equal_timestamp_clusters(timestamps)
        with self.assertRaisesRegex(StatisticsFailure, "at least two"):
            moving_block_cluster_draws(
                [1, 1], block_length_clusters=1, resamples=2, seed_text="x"
            )
        with self.assertRaisesRegex(StatisticsFailure, "block length exceeds"):
            moving_block_cluster_draws(
                [1, 2], block_length_clusters=3, resamples=2, seed_text="x"
            )


class EffectSizeTests(unittest.TestCase):
    def test_paired_effect_sizes(self):
        result = paired_effect_sizes([2.0, 4.0], [1.0, 2.0])
        self.assertEqual(result["pairs"], 2)
        self.assertEqual(result["baseline_mean"], 3.0)
        self.assertEqual(result["candidate_mean"], 1.5)
        self.assertEqual(result["absolute_mean_reduction"], 1.5)
        self.assertEqual(result["relative_mean_reduction"], 0.5)
        self.assertAlmostEqual(
            result["paired_standardized_mean_difference"],
            1.5 / math.sqrt(0.5),
        )
        self.assertEqual(result["matched_rank_biserial"], 1.0)
        self.assertEqual(result["candidate_better_pairs"], 2)

    def test_zero_variance_is_explicit_not_infinite(self):
        result = paired_effect_sizes([2.0, 3.0], [1.0, 2.0])
        self.assertIsNone(result["paired_standardized_mean_difference"])
        self.assertEqual(
            result["paired_standardized_unavailable_reason"],
            "ZERO_PAIRED_DIFFERENCE_VARIANCE",
        )

    def test_invalid_effect_inputs_fail_closed(self):
        cases = (
            ([0.0, 0.0], [0.0, 0.0], "zero baseline"),
            ([1.0], [1.0, 2.0], "lengths differ"),
            ([1.0, float("nan")], [0.0, 0.0], "finite"),
            ([1.0, -1.0], [0.0, 0.0], ">= 0.0"),
            ([True, 1.0], [0.0, 0.0], "finite number"),
        )
        for baseline, candidate, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(StatisticsFailure, message):
                    paired_effect_sizes(baseline, candidate)


class MultipleWindowGateTests(unittest.TestCase):
    def test_constant_effect_passes_deterministically(self):
        windows = [
            window("W10", "G10", [0, 0, 1, 2], [1, 2, 3, 4],
                   [2, 4, 6, 8], [1, 2, 3, 4]),
            window("W30", "G30", [10, 11, 11, 12], [5, 6, 7, 8],
                   [3, 3, 9, 9], [1.5, 1.5, 4.5, 4.5]),
        ]
        kwargs = {"bootstrap": bootstrap(block=2), "gate": gate(windows, threshold=0.49)}
        first = analyze_multiple_windows(windows, **kwargs)
        second = analyze_multiple_windows(windows, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS_PREDECLARED_CONFIDENCE_GATES")
        self.assertEqual(first["aggregation"]["aggregate_relative_reduction"], 0.5)
        self.assertFalse(first["aggregation"]["events_pooled_as_independent"])

    def test_strict_gate_rejects_equality(self):
        windows = [window("W", "G", [0, 1], [1, 2], [2, 4], [1, 2])]
        result = analyze_multiple_windows(
            windows,
            bootstrap=bootstrap(resamples=80),
            gate=gate(windows, threshold=0.5),
        )
        self.assertEqual(result["status"], "FAIL_PREDECLARED_CONFIDENCE_GATES")
        self.assertFalse(result["confidence_gate"]["aggregate_pass"])

    def test_every_window_must_pass_even_when_aggregate_passes(self):
        windows = [
            window("GOOD", "A", [0, 1], [1, 2], [1, 1], [0, 0]),
            window("BAD", "B", [2, 3], [3, 4], [1, 1], [1, 1]),
        ]
        result = analyze_multiple_windows(
            windows,
            bootstrap=bootstrap(),
            gate=gate(windows, threshold=0.25),
        )
        self.assertGreater(result["aggregation"]["aggregate_relative_reduction"], 0.25)
        self.assertTrue(result["confidence_gate"]["aggregate_pass"])
        self.assertFalse(result["confidence_gate"]["all_windows_pass"])
        self.assertEqual(result["status"], "FAIL_PREDECLARED_CONFIDENCE_GATES")

    def test_nested_windows_count_once_at_dependence_group_level(self):
        windows = [
            window("OUTER", "SAME", [0, 1], [10, 11], [1, 1], [0, 0]),
            window("INNER", "SAME", [0, 1], [10, 11], [1, 1], [1, 1]),
            window("OTHER", "OTHER", [2, 3], [20, 21], [1, 1], [1, 1]),
        ]
        result = analyze_multiple_windows(
            windows,
            bootstrap=bootstrap(),
            gate=gate(windows, threshold=-1.0),
        )
        # SAME=(1+0)/2=0.5, OTHER=0, then equal group mean=0.25.
        self.assertEqual(result["aggregation"]["group_relative_reductions"]["SAME"], 0.5)
        self.assertEqual(result["aggregation"]["aggregate_relative_reduction"], 0.25)
        self.assertEqual(result["aggregation"]["window_count"], 3)
        self.assertEqual(result["aggregation"]["independent_unit_count"], 2)
        self.assertFalse(result["aggregation"]["nested_windows_counted_as_independent"])

    def test_overlap_across_independence_groups_is_fatal(self):
        windows = [
            window("A", "GA", [0, 1], [1, 2], [1, 1], [0, 0]),
            window("B", "GB", [2, 3], [2, 3], [1, 1], [0, 0]),
        ]
        with self.assertRaisesRegex(StatisticsFailure, "overlapping sample IDs"):
            analyze_multiple_windows(
                windows, bootstrap=bootstrap(), gate=gate(windows)
            )

    def test_event_count_does_not_weight_window_effect(self):
        windows = [
            window("SMALL", "SMALL", [0, 1], [1, 2], [1, 1], [0, 0]),
            window("LARGE", "LARGE", list(range(20)), list(range(100, 120)),
                   [1] * 20, [1] * 20),
        ]
        result = analyze_multiple_windows(
            windows,
            bootstrap=bootstrap(),
            gate=gate(windows, threshold=-1.0),
        )
        self.assertEqual(result["aggregation"]["aggregate_relative_reduction"], 0.5)

    def test_preregistered_windows_groups_and_hypotheses_are_exact(self):
        windows = [window("W", "G", [0, 1], [1, 2], [1, 1], [0, 0])]
        bad_order = gate(windows)
        bad_order["predeclared_window_ids"] = ["OTHER"]
        with self.assertRaisesRegex(StatisticsFailure, "predeclared order"):
            analyze_multiple_windows(
                windows, bootstrap=bootstrap(), gate=bad_order
            )
        bad_group = gate(windows)
        bad_group["predeclared_dependence_groups"] = {"W": "OTHER"}
        with self.assertRaisesRegex(StatisticsFailure, "dependence groups"):
            analyze_multiple_windows(
                windows, bootstrap=bootstrap(), gate=bad_group
            )
        with self.assertRaisesRegex(StatisticsFailure, "omits a tested gate"):
            analyze_multiple_windows(
                windows, bootstrap=bootstrap(), gate=gate(windows, hypotheses=1)
            )

    def test_unresolvable_confidence_tail_fails_closed(self):
        windows = [window("W", "G", [0, 1], [1, 2], [1, 1], [0, 0])]
        with self.assertRaisesRegex(StatisticsFailure, "cannot resolve"):
            analyze_multiple_windows(
                windows,
                bootstrap=bootstrap(resamples=10),
                gate=gate(windows, alpha=0.01, hypotheses=2),
            )

    def test_bootstrap_zero_baseline_replicate_fails_closed(self):
        windows = [
            window("SPARSE", "G", [0, 1], [1, 2], [0, 1], [0, 0])
        ]
        with self.assertRaisesRegex(StatisticsFailure, "bootstrap replicate.*zero baseline"):
            analyze_multiple_windows(
                windows,
                bootstrap=bootstrap(resamples=120, seed="zero-resample"),
                gate=gate(windows, threshold=-1.0),
            )

    def test_extra_keys_and_nonfinite_threshold_fail_closed(self):
        windows = [window("W", "G", [0, 1], [1, 2], [1, 1], [0, 0])]
        bad_bootstrap = bootstrap()
        bad_bootstrap["surprise"] = True
        with self.assertRaisesRegex(StatisticsFailure, "keys differ"):
            analyze_multiple_windows(
                windows, bootstrap=bad_bootstrap, gate=gate(windows)
            )
        bad_gate = gate(windows)
        bad_gate["minimum_relative_reduction_strictly_greater_than"] = float("nan")
        with self.assertRaisesRegex(StatisticsFailure, "finite"):
            analyze_multiple_windows(
                windows, bootstrap=bootstrap(), gate=bad_gate
            )


if __name__ == "__main__":
    unittest.main()
