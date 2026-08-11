#!/usr/bin/env python3
"""Tests for the A7 db3f04f conservative W4 follow-up."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODEL = Path(__file__).parents[1] / "a6_w4_fixed_pin_replay_db3f04f.py"
SPEC = importlib.util.spec_from_file_location("a6_w4_db3f04f", MODEL)
assert SPEC and SPEC.loader
w4 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = w4
SPEC.loader.exec_module(w4)


class LatestW4ContractTest(unittest.TestCase):
    def test_latest_state_is_11_13_16(self) -> None:
        self.assertEqual(
            {name: spec.fixed_rtl_state_bits for name, spec in w4.LINKS.items()},
            {"parallel4": 11, "ddr2": 13, "serial1": 16},
        )
        for spec in w4.LINKS.values():
            self.assertEqual(sum(spec.fixed_state_breakdown.values()),
                             spec.fixed_rtl_state_bits)

    def test_latest_structural_proxy_is_not_equalized(self) -> None:
        self.assertEqual(w4.STRUCTURAL_PROXY["parallel4"],
                         {"functional_cells": 11, "state_bits": 11})
        self.assertEqual(w4.STRUCTURAL_PROXY["ddr2"],
                         {"functional_cells": 13, "state_bits": 13})
        self.assertEqual(w4.STRUCTURAL_PROXY["serial1"],
                         {"functional_cells": 26, "state_bits": 16})

    def test_ddr_idle_data_activity_is_split_exactly(self) -> None:
        events = [w4.base.Event(0, 0, 6)]
        result = w4.base.replay(
            events, suite="unit", run="idle", stim_cycles=4,
            spec=w4.LINKS["ddr2"], link_ratio=1)
        detail = w4.activity_detail(
            events, stim_cycles=4, spec=w4.LINKS["ddr2"], link_ratio=1)
        self.assertEqual(detail["active_data_toggles"]
                         + detail["idle_data_toggles"],
                         result.physical_data_toggles)
        self.assertGreater(detail["idle_data_toggles"], 0)
        self.assertEqual(detail["icg_enable_latch_toggles"], 2)
        self.assertEqual(detail["terminal_quiesce_periods"], 0)

    def test_terminal_latch_deassert_and_clock_cost_are_charged(self) -> None:
        events = [w4.base.Event(0, 0, 9)]
        detail = w4.activity_detail(
            events, stim_cycles=1, spec=w4.LINKS["ddr2"], link_ratio=1)
        self.assertEqual(detail["icg_enable_latch_toggles"], 2)
        self.assertEqual(detail["terminal_quiesce_periods"], 1)
        self.assertEqual(detail["terminal_quiesce_internal_clock_edges"], 4)
        self.assertEqual(detail["terminal_quiesce_icg_input_edges"], 2)

    def test_back_to_back_frames_merge_one_gate_burst(self) -> None:
        events = [w4.base.Event(0, 0, 1), w4.base.Event(1, 1, 2)]
        detail = w4.activity_detail(
            events, stim_cycles=3, spec=w4.LINKS["ddr2"], link_ratio=1)
        self.assertEqual(detail["icg_enable_latch_toggles"], 2)


if __name__ == "__main__":
    unittest.main()
