#!/usr/bin/env python3
import unittest
from dataclasses import replace

from strict_protocol_oracle import Action, StrictW4Oracle, golden_schedule


class StrictW4OracleMutationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.oracle = StrictW4Oracle()

    def reject(self, actions: list[Action], code: str) -> None:
        result = self.oracle.check(actions)
        self.assertFalse(result.passed)
        self.assertIn(code, result.faults)

    def test_golden_reset_and_merged_schedule(self) -> None:
        result = self.oracle.check(golden_schedule())
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.retired, (0x9, 0x6))

    def test_missing_fall_cannot_be_overwritten_by_next_rise(self) -> None:
        actions = golden_schedule(); del actions[4]
        self.reject(actions, "EXTRA_RISE_OPEN_FRAME")

    def test_extra_normal_edge_pair(self) -> None:
        actions = golden_schedule()
        actions.extend([Action(60000, "rise", data=0), Action(68000, "fall", data=0)])
        self.reject(actions, "EXTRA_RISE_NO_ACCEPT")

    def test_high_duty_distortion(self) -> None:
        actions = golden_schedule(); actions[4] = replace(actions[4], time_ps=38000)
        self.reject(actions, "HIGH_DUTY_DISTORTION")

    def test_low_duty_distortion(self) -> None:
        actions = golden_schedule(); actions[6] = replace(actions[6], time_ps=43000)
        self.reject(actions, "LOW_DUTY_DISTORTION")

    def test_removed_merged_boundary(self) -> None:
        actions = golden_schedule(); del actions[4]; del actions[5]
        self.reject(actions, "HIGH_DUTY_DISTORTION")

    def test_unstable_symbol(self) -> None:
        actions = golden_schedule(); actions[3] = replace(actions[3], stable=False)
        self.reject(actions, "UNSTABLE_OR_UNKNOWN_SYMBOL")

    def test_unknown_symbol(self) -> None:
        actions = golden_schedule(); actions[7] = replace(actions[7], data=None)
        self.reject(actions, "UNSTABLE_OR_UNKNOWN_SYMBOL")

    def test_runt_high(self) -> None:
        actions = golden_schedule(); actions[4] = replace(actions[4], time_ps=30000)
        self.reject(actions, "RUNT_HIGH")

    def test_missing_rise(self) -> None:
        actions = golden_schedule(); del actions[3]
        self.reject(actions, "FALL_WITHOUT_RISE")

    def test_reset_with_inflight_is_outside_contract(self) -> None:
        actions = golden_schedule()
        actions.insert(4, Action(30000, "reset_assert"))
        self.reject(actions, "RESET_WITH_INFLIGHT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
