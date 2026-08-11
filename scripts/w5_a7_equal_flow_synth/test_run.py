#!/usr/bin/env python3
"""Self-checks for the W5 A7 equal-flow synthesis audit."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.w5_a7_equal_flow_synth import run


class W5A7EqualFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.objects = run.pinned_inputs(Path("/home/chickgoose/projects/a7"))

    def test_pinned_production_w5_commit(self) -> None:
        self.assertEqual(set(self.objects), set(run.PINS))
        self.assertIn(
            "phase-related synchronous half-cycle path",
            self.objects[run.PRODUCTION_CONTRACT].decode(),
        )

    def test_r1_continuous_valid_is_not_edge_suppressed(self) -> None:
        evidence = run.verify_r1_contract(self.objects)
        self.assertEqual(evidence["legal_handshake_cycles"], [0, 1, 2, 4])
        self.assertEqual(evidence["edge_suppressed_cycles"], [0, 4])
        self.assertFalse(evidence["one_shot_qualifier_required"])
        self.assertEqual(evidence["valid_edge_one_shot_state_bits_charged"], 0)
        self.assertEqual(
            evidence["production_reset_arming_qualifier_state_bits_charged"], 1
        )

    def test_ready_low_holds_transaction_until_handshake(self) -> None:
        self.assertEqual(
            run.r1_handshakes([1, 1, 1, 1], [0, 0, 1, 1]), [2, 3]
        )

    def test_fail_closed_drain_guards_launch_and_pending_valid(self) -> None:
        evidence = run.verify_drain_contract(self.objects)
        self.assertTrue(evidence["same_cycle_launch_guarded"])
        self.assertTrue(evidence["registered_pending_valid_guarded_until_sink_sample"])
        self.assertEqual(evidence["charged_functional_cells_each_style"], 4)

    def test_frozen_phase_and_continuous_observer(self) -> None:
        evidence = run.verify_phase_contract(self.objects)
        self.assertEqual(evidence["commit_to_observation_setup_ns"], 4.0)
        self.assertEqual(evidence["endpoint_output_available_ref_cycles_after_launch"], 1)
        self.assertEqual(
            evidence["real_synchronous_sink_consumes_ref_cycles_after_launch"], 2
        )
        self.assertEqual(evidence["continuous_consumer_pulse_witness"], [1] * 6)
        self.assertFalse(evidence["two_ff_cdc_claim"])
        self.assertFalse(evidence["unrelated_clocks_supported"])

    def test_independent_wrapper_is_pinned(self) -> None:
        self.assertEqual(
            run.digest(run.LOCAL_WRAPPER.read_bytes()), run.LOCAL_WRAPPER_SHA256
        )

    def test_published_state_pin_and_physical_hold(self) -> None:
        report = json.loads(
            (run.REPO_ROOT / "reports/w5_a7_equal_flow_synth.json").read_text()
        )
        rows = {row["design"]: row for row in report["runs"]}
        self.assertEqual(rows["complete_parallel4_tx_rx"]["sequential_bits"], 18)
        self.assertEqual(rows["a7_ddr2_tx_icg_rx_r1"]["sequential_bits"], 20)
        self.assertEqual(
            rows["complete_parallel4_tx_rx"]["functional_cells_scopeinfo_removed"], 27
        )
        self.assertEqual(
            rows["a7_ddr2_tx_icg_rx_r1"]["functional_cells_scopeinfo_removed"], 29
        )
        self.assertEqual(
            rows["complete_parallel4_tx_rx"]["shared_consumer_observer_state_bits"], 6
        )
        self.assertEqual(
            rows["a7_ddr2_tx_icg_rx_r1"]["shared_consumer_observer_state_bits"], 6
        )
        self.assertEqual(rows["complete_parallel4_tx_rx"]["charged_link_signal_pins"], 5)
        self.assertEqual(rows["a7_ddr2_tx_icg_rx_r1"]["charged_link_signal_pins"], 3)
        self.assertEqual(report["decision"], "PHYSICAL_HOLD")
        self.assertEqual(report["gate"]["cdc_claim"], "NONE")
        self.assertEqual(
            report["provenance"]["a7_production_w5_commit"], run.A7_FINAL_COMMIT
        )
        self.assertNotIn("ca1a209", json.dumps(report))
        self.assertEqual(report["production_digital_regression"]["status"], "PASS")
        self.assertEqual(
            report["production_digital_regression"]["exact_pass_markers"],
            list(run.DIGITAL_PASS_MARKERS),
        )

    def test_receipts_are_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory(dir=run.REPO_ROOT) as directory:
            output = Path(directory) / "receipt.json"
            self.assertEqual(run.main(["--output", str(output)]), 0)
            self.assertEqual(
                output.read_bytes(),
                (run.REPO_ROOT / "reports/w5_a7_equal_flow_synth.json").read_bytes(),
            )
            self.assertEqual(
                output.with_suffix(".md").read_bytes(),
                (run.REPO_ROOT / "reports/w5_a7_equal_flow_synth.md").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
