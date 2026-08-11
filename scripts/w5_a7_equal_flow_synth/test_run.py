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
        self.assertEqual(evidence["drain_guard_cells_each_style"], 4)
        self.assertEqual(
            evidence["drain_guard_cell_attribution"], "inherited_owner_accounting"
        )
        self.assertFalse(evidence["independently_derived_from_pinned_base_blobs"])

    def test_warning_allowlists_fail_on_mutation(self) -> None:
        accepted = run.audit_verilator_warnings(
            "%Warning-DECLFILENAME: benign extracted filename\n"
        )
        self.assertEqual(accepted["observed_allowed_counts"], {"DECLFILENAME": 1})
        with self.assertRaises(run.base.AuditError):
            run.audit_verilator_warnings("%Warning-WIDTH: unexpected\n")
        accepted_abc = run.audit_yosys_abc_warnings(
            run.ABC_ALLOWED_WARNING_LINES[0] + "\n"
        )
        self.assertEqual(sum(accepted_abc["observed_allowed_counts"].values()), 1)
        with self.assertRaises(run.base.AuditError):
            run.audit_yosys_abc_warnings("Warning: unexpected synthesis issue\n")
        with self.assertRaises(run.base.AuditError):
            run.audit_yosys_abc_warnings("unresolved module foo\n")

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
        self.assertFalse(report["diagnostic_policy"]["warning_free_claim"])
        self.assertEqual(
            report["diagnostic_policy"]["unexpected_warning_or_unresolved_policy"],
            "FAIL_CLOSED",
        )
        self.assertEqual(
            report["diagnostic_policy"]["verilator"]["observed_allowed_counts"],
            {"DECLFILENAME": 8},
        )
        for warning_audit in report["diagnostic_policy"]["yosys_abc_by_design"].values():
            self.assertEqual(sum(warning_audit["observed_allowed_counts"].values()), 1)
            self.assertEqual(warning_audit["unexpected_count"], 0)
        semantics = report["accounting_contract"]["link_count_semantics"]
        self.assertEqual(semantics["reported_values"], {"DDR": 3, "parallel": 5})
        self.assertFalse(semantics["physical_pad_count"])
        identity = report["provenance"]["execution_identity"]
        self.assertFalse(identity["vendored_helper"]["external_w4_import"])
        self.assertEqual(
            identity["runner"]["sha256"], run.base.sha256_file(run.LOCAL_RUNNER)
        )
        self.assertEqual(
            identity["vendored_helper"]["sha256"],
            run.base.sha256_file(run.LOCAL_HELPER),
        )
        self.assertEqual(identity["python"]["implementation"], "CPython")
        self.assertTrue(identity["python"]["executable_sha256"])
        self.assertNotIn("w4_a4_moving_block_synth", run.LOCAL_RUNNER.read_text())
        workspace = report["provenance"]["workspace_policy"]
        self.assertEqual(workspace["generated_work_directory"], "system_temporary_directory")
        self.assertFalse(workspace["repository_local_temporary_directories"])
        self.assertNotIn("dir=REPO_ROOT", run.LOCAL_RUNNER.read_text())

    def test_receipts_are_byte_reproducible(self) -> None:
        # The repository may be mounted read-only; only caller/system temp is writable.
        with tempfile.TemporaryDirectory(prefix="a3-w5-receipt-test-") as directory:
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
