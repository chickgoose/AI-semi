from __future__ import annotations

import math
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.cohort import Event
from benchmarks.redred_uzh_mc_wtb_motion_v3.evaluate import (
    EvaluationError,
    _cluster_focus_prefix,
    _focus_score_for_blocks,
    evaluate_dataset_cohort,
    evaluate_window,
)
from benchmarks.redred_uzh_mc_wtb_motion_v3.focus import FocusSample, PaddedCanvas, compute_focus
from benchmarks.redred_uzh_mc_wtb_motion_v3.geometry_reference import (
    FOVDecision,
    IN_FOV,
    PoseSeries,
    RadtanCalibration,
    ReferenceWarp,
    TimedPoseTWC,
)


def pose_series() -> PoseSeries:
    poses = []
    for timestamp_ns in range(0, 6_000_001, 100_000):
        angle = timestamp_ns * 2.0e-7
        poses.append(
            TimedPoseTWC(
                timestamp_ns,
                (0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0)),
            )
        )
    return PoseSeries(poses)


def event_cloud(start_id: int, timestamps: list[int], reference_ns: int) -> tuple[Event, ...]:
    # Four stable reference-camera edge locations per polarity.  The raw
    # sensor locations move with the camera; a correct warp collapses repeats.
    points = (
        (-0.18, -0.12, 0), (-0.07, 0.14, 0), (0.09, -0.15, 0), (0.20, 0.11, 0),
        (-0.15, 0.04, 1), (-0.03, -0.18, 1), (0.11, 0.17, 1), (0.19, -0.03, 1),
    )
    output = []
    event_id = start_id
    reference_angle = reference_ns * 2.0e-7
    for timestamp_ns in timestamps:
        delta = timestamp_ns * 2.0e-7 - reference_angle
        cosine, sine = math.cos(-delta), math.sin(-delta)
        for x_ref, y_ref, polarity in points:
            x_sensor = cosine * x_ref - sine * y_ref
            y_sensor = sine * x_ref + cosine * y_ref
            x = round(100.0 * x_sensor + 100.0)
            y = round(100.0 * y_sensor + 100.0)
            output.append(
                Event(event_id, f"{timestamp_ns / 1e9:.9f}", timestamp_ns, x, y, polarity, b"fixture\n")
            )
            event_id += 1
    return tuple(output)


class EvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference_ns = 2_000_000
        self.calibration = RadtanCalibration(
            201, 201, 100.0, 100.0, 100.0, 100.0,
            0.0, 0.0, 0.0, 0.0, 0.0,
        )
        self.anchor = event_cloud(0, [1_600_000, 1_700_000, 1_800_000, 1_900_000], self.reference_ns)
        self.query = event_cloud(1000, [2_000_000 + 100_000 * i for i in range(20)], self.reference_ns)

    def test_correct_motion_wins_primary_focus_and_negative_controls(self) -> None:
        result = evaluate_window(
            cohort_id="synthetic",
            anchor_events=self.anchor,
            query_events=self.query,
            poses=pose_series(),
            calibration=self.calibration,
            reference_timestamp_ns=self.reference_ns,
            delay_ns=500_000,
            resamples=200,
            block_length_clusters=2,
            minimum_relative_reduction=0.0,
        )
        primary = result["primary_angular_nn"]
        self.assertGreater(primary["point"]["relative_mean_reduction"], 0.0)
        self.assertGreater(primary["relative_reduction_lower_bound"], 0.0)
        self.assertTrue(all(result["focus"]["gate"].values()))
        self.assertTrue(result["motion_component_gate"])
        self.assertFalse(result["candidate_gate_all_components"])
        self.assertEqual(result["overall_verdict"], "HOLD_RETIRE_CONTROL_NOT_EVALUATED")
        self.assertEqual(result["event_identity"]["query_count"], len(self.query))
        for counts in result["coverage"].values():
            self.assertEqual(sum(counts.values()), len(self.query))

    def test_duplicate_query_identity_fails_closed(self) -> None:
        duplicate = list(self.query)
        duplicate[-1] = Event(
            duplicate[0].dataset_event_index,
            duplicate[-1].timestamp_seconds_exact,
            duplicate[-1].timestamp_ns,
            duplicate[-1].x,
            duplicate[-1].y,
            duplicate[-1].polarity_01,
            duplicate[-1].raw_line,
        )
        with self.assertRaisesRegex(Exception, "duplicates"):
            evaluate_window(
                cohort_id="duplicate",
                anchor_events=self.anchor,
                query_events=duplicate,
                poses=pose_series(),
                calibration=self.calibration,
                reference_timestamp_ns=self.reference_ns,
                delay_ns=500_000,
                resamples=20,
                block_length_clusters=1,
            )

    def test_repository_holdout_is_sealed_by_default(self) -> None:
        with self.assertRaisesRegex(EvaluationError, "remains sealed"):
            evaluate_dataset_cohort(
                "/path/is/not/opened",
                "shapes_rotation_holdout_43_321",
            )

    def test_fast_focus_resample_matches_direct_duplicate_occurrences(self) -> None:
        coordinates = ((1.0, 1.0, 0), (2.0, 1.0, 0), (1.0, 3.0, 1),
                       (2.0, 3.0, 1), (4.0, 1.0, 0), (4.0, 3.0, 1))
        values = tuple(
            ReferenceWarp(
                index, index, 0, x, y, polarity, IN_FOV, (0.0, 0.0, 1.0),
                x, y, FOVDecision("continuous_extent", True, True, True, round(x), round(y)),
                0, 0, 0, 0,
            )
            for index, (x, y, polarity) in enumerate(coordinates)
        )
        clusters = tuple((index,) for index in range(len(values)))
        prefix, counts = _cluster_focus_prefix(values, clusters, 1.0)
        starts = (4, 1, 5)
        fast = _focus_score_for_blocks(prefix, counts, starts, 2)
        indices = (4, 5, 1, 2, 5, 0)
        direct = compute_focus(
            tuple(
                FocusSample(ordinal, values[index].reference_x, values[index].reference_y, values[index].polarity)
                for ordinal, index in enumerate(indices)
            ),
            sigma_px=1.0,
            canvas=PaddedCanvas(10, 10, 0.0),
        ).score
        self.assertAlmostEqual(fast, direct, places=14)


if __name__ == "__main__":
    unittest.main()
