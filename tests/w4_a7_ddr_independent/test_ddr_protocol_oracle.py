#!/usr/bin/env python3
"""Mutation/falsifier regression for the independent A7 DDR oracle."""

from __future__ import annotations

import unittest

from ddr_protocol_oracle import (
    Action,
    DDRProtocolOracle,
    LegacyFaultChecker,
    golden_back_to_back,
    replace_action,
)


class DDRProtocolOracleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.oracle = DDRProtocolOracle()
        self.legacy = LegacyFaultChecker()

    def fault_codes(self, actions: list[Action]) -> set[str]:
        return {fault.code for fault in self.oracle.check(actions).faults}

    def test_golden_back_to_back_merge_passes_exactly(self) -> None:
        result = self.oracle.check(golden_back_to_back())
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.retired, ((0, 0x9), (1, 0x6)))
        self.assertEqual(result.aborted, ())

    def test_missing_rise_is_rejected(self) -> None:
        actions = golden_back_to_back()
        del actions[1]
        self.assertIn("FALL_WITHOUT_RISE", self.fault_codes(actions))

    def test_missing_fall_followed_by_next_rise_exposes_legacy_false_pass(self) -> None:
        actions = golden_back_to_back()
        del actions[2]
        self.assertIn("EXTRA_RISE", self.fault_codes(actions))
        self.assertTrue(self.legacy.passes(actions))

    def test_runt_high_is_rejected_by_both(self) -> None:
        actions = replace_action(golden_back_to_back(), 2, time_ps=1100)
        self.assertIn("RUNT_HIGH", self.fault_codes(actions))
        self.assertFalse(self.legacy.passes(actions))

    def test_extra_edge_pair_exposes_legacy_false_pass(self) -> None:
        actions = golden_back_to_back()
        actions[3:3] = [Action(6000, "rise", data=0), Action(7500, "fall", data=0)]
        self.assertTrue({"EXTRA_RISE", "FALL_WITHOUT_RISE"} & self.fault_codes(actions))
        self.assertTrue(self.legacy.passes(actions))

    def test_high_duty_distortion_exposes_legacy_false_pass(self) -> None:
        actions = replace_action(golden_back_to_back(), 2, time_ps=7000)
        self.assertIn("HIGH_DUTY_DISTORTION", self.fault_codes(actions))
        self.assertTrue(self.legacy.passes(actions))

    def test_low_duty_distortion_exposes_legacy_false_pass(self) -> None:
        actions = replace_action(golden_back_to_back(), 4, time_ps=8000)
        actions = replace_action(actions, 5, time_ps=12000)
        self.assertIn("LOW_DUTY_DISTORTION", self.fault_codes(actions))
        self.assertTrue(self.legacy.passes(actions))

    def test_back_to_back_boundary_merge_is_not_one_combined_frame(self) -> None:
        actions = golden_back_to_back()
        del actions[2]
        del actions[3]  # remove event-2 rise after prior deletion shifted indices
        self.assertIn("HIGH_SYMBOL", self.fault_codes(actions))
        self.assertIn("MISSING_FRAME", self.fault_codes(actions))
        self.assertTrue(self.legacy.passes(actions))

    def test_metastability_abstraction_exposes_legacy_false_pass(self) -> None:
        actions = replace_action(golden_back_to_back(), 1, stable=False, data=None)
        self.assertIn("METASTABILITY_ABSTRACT", self.fault_codes(actions))
        self.assertTrue(self.legacy.passes(actions))

    def test_rise_fall_ordering_is_fail_closed(self) -> None:
        actions = golden_back_to_back()
        actions[1], actions[2] = (
            Action(1000, "fall", data=0x2),
            Action(5000, "rise", data=0x1),
        )
        codes = self.fault_codes(actions)
        self.assertIn("FALL_WITHOUT_RISE", codes)
        self.assertIn("EXTRA_RISE", codes)

    def test_reset_mid_frame_aborts_without_phantom_and_recovers(self) -> None:
        actions = [
            Action(0, "accept", address=0x9, expected_rise_ps=1000, expected_fall_ps=5000, occurrence_id=0),
            Action(1000, "rise", data=0x1),
            Action(3000, "reset_assert"),
            Action(3000, "fall", data=0x2),
            Action(6000, "reset_release"),
            Action(8000, "accept", address=0x6, expected_rise_ps=9000, expected_fall_ps=13000, occurrence_id=1),
            Action(9000, "rise", data=0x2),
            Action(13000, "fall", data=0x1),
        ]
        result = self.oracle.check(actions)
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.aborted, (0,))
        self.assertEqual(result.retired, ((1, 0x6),))

    def test_post_reset_fall_is_phantom(self) -> None:
        actions = [
            Action(0, "accept", address=0x9, expected_rise_ps=1000, expected_fall_ps=5000, occurrence_id=0),
            Action(1000, "rise", data=0x1),
            Action(3000, "reset_assert"),
            Action(6000, "reset_release"),
            Action(7000, "fall", data=0x2),
        ]
        self.assertIn("FALL_WITHOUT_RISE", self.fault_codes(actions))

    def test_open_frame_at_end_is_missing_fall(self) -> None:
        actions = golden_back_to_back()[:2]
        self.assertIn("MISSING_FALL", self.fault_codes(actions))


if __name__ == "__main__":
    unittest.main(verbosity=2)
