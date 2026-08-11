#!/usr/bin/env python3

from __future__ import annotations

import itertools
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity_directory_model import ActivityDirectory, Event, FlatScan, mutation, simulate
from run_activity_directory_sweep import (
    CORRECTNESS_KEYS, REQUIRE_GO_EXIT, correctness_evidence,
    decision_exit_code, decision_sentinel,
)


class ActivityDirectoryTest(unittest.TestCase):
    def test_hold_is_not_a_pass_sentinel_and_require_go_fails_closed(self):
        self.assertEqual(decision_sentinel("HOLD"),
                         "A5_ACTIVITY_DIRECTORY_SWEEP_HOLD")
        self.assertNotIn("PASS", decision_sentinel("HOLD"))
        self.assertEqual(decision_exit_code("HOLD", False), 0)
        self.assertEqual(decision_exit_code("HOLD", True), REQUIRE_GO_EXIT)
        self.assertEqual(decision_exit_code("GO", True), 0)

    def test_gate_correctness_requires_every_evidence_field(self):
        complete = {"config": "L1", **{key: True for key in CORRECTNESS_KEYS}}
        evidence = correctness_evidence([complete], "L1")
        self.assertTrue(all(evidence.values()))
        for key in CORRECTNESS_KEYS:
            broken = dict(complete); broken[key] = False
            with self.subTest(key=key):
                observed = correctness_evidence([complete, broken], "L1")
                self.assertFalse(observed[key])
                self.assertFalse(all(observed.values()))

    def test_run_summary_binds_full_correctness_evidence(self):
        result = simulate("evidence", [Event(0, 0, 0), Event(1, 1, 0)], 2, 2,
                          ActivityDirectory(2, 1))
        summary = result.summary()
        self.assertTrue(all(summary[key] is True for key in CORRECTNESS_KEYS))
        result.delivered_ids.append(99)
        result.delivered_sequence.append((0, 99))
        broken = result.correctness_checks()
        self.assertFalse(broken["id_multiset_ok"])
        self.assertFalse(broken["no_phantom_ok"])

        duplicated = simulate("duplicate-evidence", [Event(2, 0, 0)], 1, 1,
                               ActivityDirectory(1, 1))
        duplicated.delivered_ids.append(2)
        duplicated.delivered_sequence.append((0, 2))
        self.assertFalse(duplicated.correctness_checks()["no_duplicate_ok"])

        reordered = simulate("order-evidence",
                             [Event(3, 0, 0), Event(4, 0, 2)], 1, 4,
                             ActivityDirectory(1, 1))
        reordered.delivered_sequence.reverse()
        self.assertFalse(reordered.correctness_checks()["source_order_ok"])

    def test_committed_official_manifest_identities_are_50_and_22(self):
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "scripts"))
        import common_suite_official as official
        fixtures = root / "tests/common_suite_receipt/fixtures"
        for suite, filename, cardinality in (
            ("full50", "manifest.neutrality-n16.json", 50),
            ("capacity22", "manifest.multilane-n16.json", 22),
        ):
            path = fixtures / filename
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             official.SUITES[suite]["manifest_sha256"])
            rows = json.loads(path.read_text())["runs"]
            self.assertEqual(len(rows), cardinality)
            self.assertEqual(tuple(row["name"] for row in rows),
                             official.SUITES[suite]["names"])

    def test_flat_and_clean_hint_conserve_events(self):
        events = [Event(index, index % 4, index // 2) for index in range(24)]
        for policy in (FlatScan(4), ActivityDirectory(4, 2)):
            result = simulate("clean", events, 4, 16, policy)
            self.assertEqual(result.generated, result.accepted + result.overrun)
            self.assertEqual(result.accepted, result.delivered)
            self.assertCountEqual(result.accepted_ids, result.delivered_ids)

    def test_every_hint_corruption_is_performance_only(self):
        events = [Event(index, index % 4, index // 3) for index in range(96)]
        for mode in ("false_empty", "out_of_range", "duplicate_hot",
                     "false_overflow_clear", "rotating_corrupt", "stale_valid"):
            with self.subTest(mode=mode):
                policy = ActivityDirectory(4, 2, watchdog_limit=3,
                                           mutation=mutation(mode))
                result = simulate(mode, events, 4, 40, policy, drain_limit=2048)
                self.assertEqual(result.accepted, result.delivered)
                self.assertCountEqual(result.accepted_ids, result.delivered_ids)
                self.assertLess(result.drain_cycles, 128)

    def test_false_empty_forces_bounded_exact_fallback(self):
        events = [Event(index, index, 0) for index in range(4)]
        policy = ActivityDirectory(4, 2, watchdog_limit=2,
                                   mutation=mutation("false_empty"))
        result = simulate("false-empty", events, 4, 2, policy, drain_limit=64)
        self.assertEqual(result.accepted, 4)
        self.assertGreaterEqual(result.metrics["hint_misses"], 1)
        self.assertEqual(result.metrics["fallback_recovery_cycles"],
                         result.metrics["fallback_entries"])
        self.assertLessEqual(result.drain_cycles, 8)

    def test_stale_valid_hot_hint_cannot_starve_victim(self):
        events = [Event(0, 1, 0)]
        events.extend(Event(index + 1, 0, index) for index in range(64))
        policy = ActivityDirectory(2, 1, watchdog_limit=3,
                                   mutation=mutation("stale_valid"))
        result = simulate("stale-hot", events, 2, 64, policy, drain_limit=256)
        victim_id = 0
        self.assertIn(victim_id, result.delivered_ids)
        victim_index = result.accepted_ids.index(victim_id)
        self.assertLessEqual(result.waits[victim_index], 10)

    def test_duplicate_pointer_never_duplicates_event(self):
        events = [Event(index, index % 3, index // 3) for index in range(30)]
        result = simulate("duplicate", events, 3, 16,
                          ActivityDirectory(3, 2, watchdog_limit=2,
                                            mutation=mutation("duplicate_hot")))
        self.assertEqual(len(result.delivered_ids), len(set(result.delivered_ids)))

    def test_exhaustive_small_n_sequences_under_mutation(self):
        # 8^4 arrival-mask sequences x six persistent corruptions.  Event IDs
        # are unique, while same-source arrivals into an occupied latch count as
        # specified source overrun rather than disappearing inside the DUT.
        modes = ("false_empty", "out_of_range", "duplicate_hot",
                 "false_overflow_clear", "rotating_corrupt", "stale_valid")
        for masks in itertools.product(range(8), repeat=4):
            events = []
            event_id = 0
            for cycle, mask in enumerate(masks):
                for source in range(3):
                    if mask & (1 << source):
                        events.append(Event(event_id, source, cycle)); event_id += 1
            for mode in modes:
                result = simulate("exhaustive", events, 3, 4,
                                  ActivityDirectory(3, 2, watchdog_limit=2,
                                                    mutation=mutation(mode)),
                                  drain_limit=96)
                self.assertEqual(result.generated, result.accepted + result.overrun)
                self.assertEqual(result.accepted, result.delivered)
                self.assertCountEqual(result.accepted_ids, result.delivered_ids)


if __name__ == "__main__":
    unittest.main()
