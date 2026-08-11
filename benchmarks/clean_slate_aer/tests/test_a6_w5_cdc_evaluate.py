#!/usr/bin/env python3
"""Unit tests for the A6 W5 CDC/RDC comparison model."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODEL = Path(__file__).parents[1] / "a6_w5_cdc_evaluate.py"
SPEC = importlib.util.spec_from_file_location("a6_w5_cdc", MODEL)
assert SPEC and SPEC.loader
w5 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = w5
SPEC.loader.exec_module(w5)


def event(cycle: int, sequence: int, address: int = 0) -> object:
    return w5.w4.base.Event(cycle, sequence, address)


class CdcBoundaryModelTest(unittest.TestCase):
    def test_r1_phase_capture_is_exact_for_serialized_stream(self) -> None:
        events = [event(0, 0), event(0, 1), event(1, 2), event(4, 3)]
        row = w5.analyze_run(
            events, suite="unit", run="r1", stim_cycles=5, link_ratio=1)
        self.assertTrue(row["phase_capture_sequence_exact"])
        self.assertEqual(row["phase_capture_delivered"], len(events))
        self.assertEqual(row["max_rx_commits_between_core_edges"], 1)

    def test_two_toggles_between_core_edges_alias_to_zero(self) -> None:
        events = [event(0, 0), event(0, 1)]
        row = w5.analyze_run(
            events, suite="unit", run="r2", stim_cycles=1, link_ratio=2)
        self.assertFalse(row["phase_capture_sequence_exact"])
        self.assertEqual(row["phase_capture_delivered"], 0)
        self.assertEqual(row["phase_capture_lost_by_toggle_alias"], 2)

    def test_three_toggles_capture_only_last_occurrence(self) -> None:
        counts = w5.phase_capture_counts(
            [event(0, index) for index in range(3)],
            stim_cycles=1, link_ratio=4)
        self.assertEqual(counts, [3])
        row = w5.analyze_run(
            [event(0, index) for index in range(3)],
            suite="unit", run="r4", stim_cycles=1, link_ratio=4)
        self.assertEqual(row["phase_capture_delivered"], 1)
        self.assertEqual(row["phase_capture_lost_by_toggle_alias"], 2)

    def test_async_fifo_state_lower_bounds_charge_synchronizers(self) -> None:
        self.assertEqual(w5.fifo_state_lower_bound(2), {
            "depth": 2,
            "payload_bits": 8,
            "local_binary_and_gray_pointer_bits": 8,
            "two_flop_cross_pointer_synchronizer_bits": 8,
            "registered_output_valid_and_flag_bits": 7,
            "state_bits_lower_bound": 31,
        })
        self.assertEqual(w5.fifo_state_lower_bound(4)["state_bits_lower_bound"], 47)
        with self.assertRaises(ValueError):
            w5.fifo_state_lower_bound(3)

    def test_fifo_depth_model_charges_pointer_visibility_delay(self) -> None:
        self.assertEqual(
            w5.async_fifo_depth_without_backpressure([1, 1, 1, 1]), 3)
        self.assertEqual(
            w5.async_fifo_depth_without_backpressure([2, 2, 2, 2]), 7)

    def test_power_of_two_rounding(self) -> None:
        self.assertEqual(w5.next_power_of_two(1), 2)
        self.assertEqual(w5.next_power_of_two(3), 4)
        self.assertEqual(w5.next_power_of_two(5132), 8192)


if __name__ == "__main__":
    unittest.main()
