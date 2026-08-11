#!/usr/bin/env python3
"""Regression gates for the W4 exact-commit analytical tournament."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import w4_tournament as tournament


class TournamentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = tournament.evaluate()

    def rows(self, suite: str):
        return self.document["suites"][suite]["architectures"]

    def row(self, suite: str, core: str, link: str, ratio: int):
        matches = [
            item for item in self.rows(suite)
            if item["core"] == core and item["link"] == link
            and item["link_ratio_R"] == ratio
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_exact_suite_counts_and_a4_replay_anchors(self) -> None:
        self.assertEqual(self.document["suites"]["full50"]["run_count"], 50)
        self.assertEqual(self.document["suites"]["capacity22"]["run_count"], 22)
        anchors = {
            ("full50", "fixed_one_step"): (83514, 22902, 0.729214327, 2323775),
            ("full50", "moving_two_step"): (83555, 22861, 0.729999388, 1511352),
            ("capacity22", "fixed_one_step"): (42948, 22668, 0.789979031, 1215726),
            ("capacity22", "moving_two_step"): (42983, 22633, 0.790855566, 847126),
        }
        for (suite, core), expected in anchors.items():
            row = self.row(suite, core, "parallel4", 1)
            self.assertEqual(
                (
                    row["core_accepted"], row["overrun"], row["throughput"],
                    row["core_state_toggle_proxy"],
                ),
                expected,
            )
            self.assertEqual(row["max_dut_visible_word"], 15)

    def test_link_never_increases_delivery_or_hides_overrun(self) -> None:
        for suite in ("full50", "capacity22"):
            for core in ("fixed_one_step", "moving_two_step"):
                reference = self.row(suite, core, "parallel4", 1)
                for ratio in (1, 2, 4):
                    for link in ("parallel4", "ddr2"):
                        row = self.row(suite, core, link, ratio)
                        self.assertEqual(row["core_accepted"], reference["core_accepted"])
                        self.assertEqual(row["core_delivered"], reference["core_delivered"])
                        self.assertEqual(row["link_delivered_capacity_envelope"], reference["core_delivered"])
                        self.assertEqual(row["overrun"], reference["overrun"])
                        self.assertEqual(row["boundary_buffer_required_events"], 0)
                        self.assertEqual(row["max_boundary_backlog_events"], 0)
                        self.assertEqual(row["core_internal_event_slots"], 31)
                        self.assertEqual(row["ingress_source_latch_slots"], 16)
                        self.assertEqual(row["throughput_bottleneck"], "core_or_ingress_not_link")

    def test_pin_state_and_latency_costs_are_not_free(self) -> None:
        for suite in ("full50", "capacity22"):
            for core in ("fixed_one_step", "moving_two_step"):
                parallel = self.row(suite, core, "parallel4", 1)
                ddr = self.row(suite, core, "ddr2", 1)
                self.assertEqual((parallel["pins"], parallel["link_state_bits"]), (5, 0))
                self.assertEqual((ddr["pins"], ddr["link_state_bits"]), (3, 12))
                self.assertEqual(ddr["mean_end_to_end_latency"], parallel["mean_end_to_end_latency"] + 0.75)
                self.assertGreater(ddr["link_register_toggle_proxy"], 0)
                self.assertEqual(
                    ddr["link_internal_clock_edge_proxy"], 4 * ddr["cycles"]
                )
                self.assertEqual(parallel["link_internal_clock_edge_proxy"], 0)

    def test_faster_direct_ddr_boundary_fails_closed(self) -> None:
        for suite in ("full50", "capacity22"):
            for core in ("fixed_one_step", "moving_two_step"):
                r1 = self.row(suite, core, "ddr2", 1)
                self.assertTrue(r1["analytical_rate_compatible"])
                self.assertFalse(r1["executed_composed_rtl_evidence"])
                self.assertFalse(r1["composed_reset_path_evidence"])
                for ratio in (2, 4):
                    row = self.row(suite, core, "ddr2", ratio)
                    self.assertFalse(row["analytical_rate_compatible"])
                    self.assertEqual(row["extra_capture_opportunities_per_valid_core_period"], ratio - 1)
                    self.assertEqual(row["eligibility"], "HOLD_MISSING_ONE_LINK_PERIOD_LAUNCH_QUALIFIER")
                    self.assertEqual(row["link_state_bits"], 12)  # no invented adapter state

    def test_combination_is_declared_serial_not_novel(self) -> None:
        self.assertEqual(
            self.document["decision"],
            "SIMPLE_SERIAL_COMPOSITION_NOT_NEW_ARCHITECTURE",
        )
        self.assertFalse(self.document["clock_boundary_rule"]["added_queue_or_adapter"])

    def test_old_a7_scope_and_idle_mux_activity_are_explicit(self) -> None:
        provenance = self.document["provenance"]
        self.assertEqual(provenance["a7_commit"], tournament.A7_COMMIT)
        self.assertEqual(provenance["a7_scope"], "frozen pre-ICG commit 31947a7 only")
        self.assertEqual(
            provenance["a7_latest_observed_but_excluded"]["commit"],
            "db3f04fe0e01699e63c596145fe71effc601e57c",
        )
        self.assertEqual(
            provenance["a7_latest_observed_but_excluded"]["structural_evidence_ancestor"],
            "a349d64d8b8b3d4398a258926af493b5da1e3ac2",
        )
        self.assertEqual(provenance["a7_latest_observed_but_excluded"]["state_bits"], 13)
        self.assertEqual(self.document["clock_boundary_rule"]["qualifier_cost"], "unknown_and_not_included")
        # Address 1 alternates data symbols 01/00 on every ref period even
        # after its sole burst.  Old event-only accounting would return four.
        self.assertEqual(
            tournament.link_wire_toggles("ddr2", [1], [0], 3, 1), 8
        )

    def test_stale_sequence_payload_is_rejected(self) -> None:
        tournament.assert_address_only_event(SimpleNamespace(source=7, payload=7))
        with self.assertRaises(tournament.TournamentError):
            tournament.assert_address_only_event(
                SimpleNamespace(source=7, payload=(7 << 24) | 1)
            )


if __name__ == "__main__":
    unittest.main()
