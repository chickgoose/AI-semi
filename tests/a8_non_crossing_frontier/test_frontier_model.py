#!/usr/bin/env python3
"""Exhaustive and directed checks for the non-crossing frontier cycle model."""

from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = PROJECT_ROOT / "rtl/candidates/a8_non_crossing_frontier/model"
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODEL_DIR))
sys.path.insert(0, str(TEST_DIR))

from evaluate_frontier import evaluate  # noqa: E402
from non_crossing_frontier import (  # noqa: E402
    NonCrossingFrontierFabric,
    validate_partition,
)
from read_gate_status import validate_report  # noqa: E402


class FrontierDirectedTest(unittest.TestCase):
    def test_all_sources_have_one_contiguous_owner(self) -> None:
        model = NonCrossingFrontierFabric()
        for mask in (0x0000, 0x000F, 0xF000, 0xA5A5, 0xFFFF):
            for _ in range(64):
                result = model.step(mask)
                self.assertTrue(validate_partition(result.frontiers_after, 16, 4))
                owners = [model.owner(source) for source in range(16)]
                self.assertEqual(owners, sorted(owners))
                self.assertEqual(set(owners), set(range(4)))

    def test_bounded_emergency_preserves_non_crossing(self) -> None:
        model = NonCrossingFrontierFabric(
            hysteresis=16, cooldown_cycles=1, emergency_cycles=3
        )
        before = tuple(model.frontiers)
        for _ in range(3):
            result = model.step(0x000F, advance=False)
            self.assertTrue(validate_partition(result.frontiers_after, 16, 4))
        self.assertNotEqual(tuple(model.frontiers), before)
        self.assertEqual(model.frontiers[0], 0)
        self.assertEqual(model.frontiers[-1], 16)

    def test_frontier_changes_only_by_one_address_per_cycle(self) -> None:
        model = NonCrossingFrontierFabric()
        for mask in (0x000F, 0x00F0, 0x0F00, 0xF000) * 8:
            result = model.step(mask, advance=False)
            for old, new in zip(result.frontiers_before, result.frontiers_after):
                self.assertLessEqual(abs(old - new), 1)

    def test_go_gate_model_is_self_consistent(self) -> None:
        report = evaluate()
        self.assertTrue(report["research_complete"])
        self.assertFalse(report["go_gate"]["go"])
        self.assertEqual(report["decision"], "HOLD")
        self.assertEqual(
            report["completion_sentinel"], "A8_NCF_RESEARCH_COMPLETE_HOLD"
        )
        self.assertEqual(
            validate_report(report),
            ("HOLD", "A8_NCF_RESEARCH_COMPLETE_HOLD"),
        )
        for row in report["rows"]:
            self.assertEqual(row["accepted"], row["delivered"])
            self.assertEqual(row["generated"], row["accepted"] + row["overrun"])
            self.assertGreaterEqual(row["demand_normalized_fairness"], 0.0)
            self.assertLessEqual(row["demand_normalized_fairness"], 1.0)

    def test_machine_sentinel_mismatch_is_rejected(self) -> None:
        report = evaluate()
        report["completion_sentinel"] = "A8_NCF_RESEARCH_COMPLETE_GO"
        with self.assertRaisesRegex(ValueError, "sentinel disagrees"):
            validate_report(report)

    def test_machine_decision_rebound_is_rejected(self) -> None:
        report = evaluate()
        report["decision"] = "GO"
        report["completion_sentinel"] = "A8_NCF_RESEARCH_COMPLETE_GO"
        with self.assertRaisesRegex(ValueError, "decision disagrees"):
            validate_report(report)


class FrontierN16ExhaustiveTest(unittest.TestCase):
    def test_every_n16_request_subset_drains_without_starvation_or_crossing(self) -> None:
        worst_drain = 0
        worst_mask = 0
        saw_empty_territory = False
        saw_single_overloaded_territory = False
        for original_mask in range(1 << 16):
            model = NonCrossingFrontierFabric()
            remaining = original_mask
            initial_counts = []
            for lane in range(4):
                low = model.frontiers[lane]
                high = model.frontiers[lane + 1]
                count = sum(bool(remaining & (1 << source)) for source in range(low, high))
                initial_counts.append(count)
            saw_empty_territory |= 0 in initial_counts
            saw_single_overloaded_territory |= (
                sum(count > 0 for count in initial_counts) == 1 and max(initial_counts) > 1
            )
            cycles = 0
            while remaining and cycles < 64:
                result = model.step(remaining)
                self.assertTrue(validate_partition(result.frontiers_after, 16, 4))
                self.assertEqual(result.grant_mask & ~remaining, 0)
                remaining &= ~result.grant_mask
                cycles += 1
            self.assertEqual(remaining, 0, f"starvation mask=0x{original_mask:04x}")
            if cycles > worst_drain:
                worst_drain = cycles
                worst_mask = original_mask
        self.assertTrue(saw_empty_territory)
        self.assertTrue(saw_single_overloaded_territory)
        self.assertLessEqual(worst_drain, 16, f"worst mask=0x{worst_mask:04x}")
        print(
            f"N16_EXHAUSTIVE_DRAIN_PASS masks=65536 worst_cycles={worst_drain} "
            f"worst_mask=0x{worst_mask:04x}"
        )

    def test_every_static_n16_subset_has_no_sustained_frontier_oscillation(self) -> None:
        worst_reversals = 0
        worst_mask = 0
        for request_mask in range(1 << 16):
            model = NonCrossingFrontierFabric()
            reversals = 0
            for _ in range(32):
                result = model.step(request_mask, advance=False)
                self.assertTrue(validate_partition(result.frontiers_after, 16, 4))
                reversals += result.frontier_reversals
            if reversals > worst_reversals:
                worst_reversals = reversals
                worst_mask = request_mask
        self.assertLessEqual(
            worst_reversals,
            3,
            f"sustained oscillation mask=0x{worst_mask:04x} reversals={worst_reversals}",
        )
        print(
            f"N16_STATIC_OSCILLATION_PASS masks=65536 "
            f"worst_reversals={worst_reversals} worst_mask=0x{worst_mask:04x}"
        )

    def test_all_valid_n16_partitions_are_accepted(self) -> None:
        count = 0
        for internal in itertools.combinations(range(1, 16), 3):
            frontiers = (0, *internal, 16)
            model = NonCrossingFrontierFabric(initial_frontiers=frontiers)
            self.assertEqual(tuple(model.frontiers), frontiers)
            self.assertTrue(validate_partition(frontiers, 16, 4))
            count += 1
        self.assertEqual(count, 455)


if __name__ == "__main__":
    unittest.main(verbosity=2)
