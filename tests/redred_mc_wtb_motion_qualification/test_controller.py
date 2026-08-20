from __future__ import annotations

import math
import unittest

from benchmarks.redred_mc_wtb_motion_qualification import (
    MotionClass,
    MotionEvidence,
    MotionQualificationConfig,
    MotionQualificationError,
    MotionQualifier,
    Route,
    rotation_displacement_proxy_q,
)


def config(dwell: int = 2) -> MotionQualificationConfig:
    # Synthetic verification values only; not performance thresholds.
    return MotionQualificationConfig(30, 40, 70, 80, dwell)


class MotionQualifierTests(unittest.TestCase):
    def test_reliable_enable_requires_dwell_and_routes_by_class(self) -> None:
        dut = MotionQualifier(config())
        first = dut.step(MotionEvidence(0, 10, True))
        second = dut.step(MotionEvidence(1, 10, True))
        self.assertEqual(first.motion_class, MotionClass.UNRELIABLE)
        self.assertEqual(second.motion_class, MotionClass.LOW)
        self.assertEqual(second.route, Route.SENSOR_FIXED_BYPASS)

        self.assertEqual(dut.step(MotionEvidence(2, 50, True)).motion_class, MotionClass.LOW)
        mid = dut.step(MotionEvidence(3, 50, True))
        self.assertEqual(mid.motion_class, MotionClass.MID)
        self.assertTrue(mid.warp_enable)
        self.assertFalse(mid.tile_enable)

        self.assertEqual(dut.step(MotionEvidence(4, 90, True)).motion_class, MotionClass.MID)
        high = dut.step(MotionEvidence(5, 90, True))
        self.assertEqual(high.motion_class, MotionClass.HIGH)
        self.assertEqual(high.route, Route.MC_WTB_TILE)
        self.assertTrue(high.tile_enable)

    def test_hysteresis_rejects_boundary_chatter(self) -> None:
        dut = MotionQualifier(config(dwell=1))
        self.assertEqual(dut.step(MotionEvidence(0, 50, True)).motion_class, MotionClass.MID)
        for epoch, value in enumerate((39, 41, 35, 31), 1):
            self.assertEqual(dut.step(MotionEvidence(epoch, value, True)).motion_class, MotionClass.MID)
        self.assertEqual(dut.step(MotionEvidence(5, 30, True)).motion_class, MotionClass.LOW)
        for epoch, value in enumerate((35, 39, 31), 6):
            self.assertEqual(dut.step(MotionEvidence(epoch, value, True)).motion_class, MotionClass.LOW)

    def test_pose_fault_immediately_bypasses_and_recovery_requires_dwell(self) -> None:
        dut = MotionQualifier(config())
        dut.step(MotionEvidence(0, 90, True))
        self.assertEqual(dut.step(MotionEvidence(1, 90, True)).motion_class, MotionClass.HIGH)
        fault = dut.step(MotionEvidence(2, 90, False))
        self.assertEqual(fault.motion_class, MotionClass.UNRELIABLE)
        self.assertTrue(fault.safe_bypass)
        self.assertFalse(fault.warp_enable)
        self.assertEqual(dut.step(MotionEvidence(3, 90, True)).motion_class, MotionClass.UNRELIABLE)
        self.assertEqual(dut.step(MotionEvidence(4, 90, True)).motion_class, MotionClass.HIGH)

    def test_epochs_must_be_strictly_increasing(self) -> None:
        dut = MotionQualifier(config())
        dut.step(MotionEvidence(7, 10, True))
        with self.assertRaisesRegex(MotionQualificationError, "strictly increasing"):
            dut.step(MotionEvidence(7, 10, True))

    def test_threshold_contract_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(MotionQualificationError, "not ordered"):
            MotionQualificationConfig(40, 30, 70, 80, 1)

    def test_rotation_proxy_is_sign_invariant_and_quantized(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        angle = 0.01
        rotated = (0.0, math.sin(angle / 2.0), 0.0, math.cos(angle / 2.0))
        negated = tuple(-value for value in rotated)
        expected = round(200.0 * angle * 256)
        self.assertEqual(rotation_displacement_proxy_q(identity, rotated, 200.0, fractional_bits=8), expected)
        self.assertEqual(rotation_displacement_proxy_q(identity, negated, 200.0, fractional_bits=8), expected)


if __name__ == "__main__":
    unittest.main()
