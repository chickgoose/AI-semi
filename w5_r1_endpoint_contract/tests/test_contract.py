from __future__ import annotations

import unittest
from fractions import Fraction

from contract_model import (
    DDR_ENDPOINT_STATE_BITS,
    FINAL_A7_CHARGED_DEPTH,
    FINAL_A7_DDR_CHARGED_CELLS,
    FINAL_A7_ENDPOINT_COMMIT,
    FINAL_A7_PARALLEL_CHARGED_CELLS,
    PARALLEL_ENDPOINT_STATE_BITS,
    ContractHarness,
    ContractViolation,
    Faults,
    PpaEvidence,
    R1Endpoint,
    qualify,
)


def armed_harness(endpoint: R1Endpoint | None = None) -> ContractHarness:
    harness = ContractHarness(endpoint)
    harness.arm_after_reset()
    return harness


def drain_observer(harness: ContractHarness) -> None:
    harness.drain()


class GoldenContractTest(unittest.TestCase):
    def test_first_release_edge_arms_but_does_not_accept(self) -> None:
        harness = ContractHarness()
        self.assertFalse(harness.run_cycle(True, 6))
        self.assertEqual(harness.monitor.accepted, 0)
        self.assertTrue(harness.run_cycle(True, 6))
        drain_observer(harness)
        harness.assert_conserved()

    def test_all_addresses_and_all_back_to_back_pairs(self) -> None:
        for first in range(16):
            single = armed_harness()
            single.run_cycle(True, first)
            drain_observer(single)
            single.assert_conserved()
            for second in range(16):
                pair = armed_harness()
                pair.run_cycle(True, first)
                pair.run_cycle(True, second)
                drain_observer(pair)
                pair.assert_conserved()
                self.assertEqual(pair.monitor.accepted, 2)

    def test_continuous_valid_changing_address_accepts_every_armed_posedge(self) -> None:
        harness = armed_harness()
        for address in range(16):
            self.assertTrue(harness.run_cycle(True, address))
        drain_observer(harness)
        harness.assert_conserved()
        self.assertEqual(
            (
                harness.monitor.accepted,
                harness.monitor.launches,
                harness.monitor.raw_commits,
                harness.monitor.available,
                harness.monitor.retired,
            ),
            (16, 16, 16, 16, 16),
        )

    def test_continuous_valid_same_address_is_new_each_handshake(self) -> None:
        harness = armed_harness()
        for _ in range(4):
            harness.run_cycle(True, 9)
        drain_observer(harness)
        harness.assert_conserved()
        self.assertEqual(harness.monitor.retired, 4)

    def test_stalled_valid_holds_through_reset_arming_only(self) -> None:
        harness = ContractHarness()
        self.assertFalse(harness.run_cycle(True, 6))
        self.assertTrue(harness.run_cycle(True, 6))
        self.assertTrue(harness.run_cycle(True, 11))
        drain_observer(harness)
        harness.assert_conserved()
        self.assertEqual(harness.monitor.accepted, 2)

        illegal = ContractHarness()
        illegal.run_cycle(True, 6)
        with self.assertRaisesRegex(ContractViolation, "changed while ready was low"):
            illegal.run_cycle(True, 7)

    def test_reset_after_full_observer_drain(self) -> None:
        harness = armed_harness()
        harness.run_cycle(True, 2)
        harness.assert_drain_consistent()
        with self.assertRaisesRegex(ContractViolation, "drain_idle is false"):
            harness.reset(require_drained=True)
        harness.run_cycle(False)  # output becomes available, not yet consumed
        harness.assert_drain_consistent()
        with self.assertRaisesRegex(ContractViolation, "drain_idle is false"):
            harness.reset(require_drained=True)
        drain_observer(harness)
        harness.reset(require_drained=True)
        # The first released edge stalls/arms; held valid then handshakes.
        self.assertFalse(harness.run_cycle(True, 13))
        self.assertTrue(harness.run_cycle(True, 13))
        drain_observer(harness)
        harness.assert_conserved()

    def test_availability_cycle_one_consumer_retire_cycle_two(self) -> None:
        harness = armed_harness()
        harness.run_cycle(True, 14)
        self.assertEqual((harness.monitor.available, harness.monitor.retired), (0, 0))
        self.assertFalse(harness.endpoint.drain_idle())
        harness.run_cycle(False)
        self.assertEqual((harness.monitor.available, harness.monitor.retired), (1, 0))
        self.assertFalse(harness.endpoint.drain_idle())
        harness.run_cycle(False)
        self.assertEqual((harness.monitor.available, harness.monitor.retired), (1, 1))
        self.assertTrue(harness.endpoint.drain_idle())
        harness.assert_conserved()

    def test_invalid_midframe_reset_aborts_without_phantom(self) -> None:
        harness = armed_harness()
        harness.ref_edge(True, 4)
        harness.frame_rise()
        with self.assertRaisesRegex(ContractViolation, "drain_idle is false"):
            harness.reset(require_drained=True)
        harness.reset(require_drained=False)
        harness.frame_fall()
        harness.run_cycle(False)  # reset-release arming edge
        harness.run_cycle(False)
        self.assertEqual(harness.monitor.retired, 0)
        self.assertEqual(harness.monitor.invalid_midframe_resets, 1)

    def test_same_cycle_launch_and_pending_valid_both_block_drain(self) -> None:
        harness = armed_harness()
        harness.ref_edge(True, 12)
        self.assertFalse(harness.endpoint.drain_idle())
        harness.assert_drain_consistent()
        harness.frame_rise()
        harness.frame_fall()
        harness.cycle += 1
        harness.run_cycle(False)
        self.assertEqual((harness.monitor.available, harness.monitor.retired), (1, 0))
        self.assertFalse(harness.endpoint.drain_idle())
        harness.assert_drain_consistent()

    def test_full_endpoint_state_and_pin_constants_are_frozen(self) -> None:
        self.assertEqual(R1Endpoint().declared_functional_state_bits, 20)
        self.assertEqual(DDR_ENDPOINT_STATE_BITS, 20)
        self.assertEqual(PARALLEL_ENDPOINT_STATE_BITS, 18)
        self.assertEqual(FINAL_A7_DDR_CHARGED_CELLS, 29)
        self.assertEqual(FINAL_A7_PARALLEL_CHARGED_CELLS, 27)
        self.assertEqual(FINAL_A7_CHARGED_DEPTH, 7)
        self.assertEqual(
            FINAL_A7_ENDPOINT_COMMIT,
            "42377ca81340951bfcd453b3bd664e673091f9f3",
        )


class MutationTest(unittest.TestCase):
    def assert_fault_rejected(self, faults: Faults, pattern: str) -> None:
        harness = armed_harness(R1Endpoint(faults))
        with self.assertRaisesRegex(ContractViolation, pattern):
            harness.run_cycle(True, 5)

    def test_valid_edge_detector_drops_legal_continuous_valid(self) -> None:
        harness = armed_harness(R1Endpoint(Faults(valid_edge_detector=True)))
        harness.run_cycle(True, 1)
        with self.assertRaisesRegex(ContractViolation, "acceptance must equal"):
            harness.run_cycle(True, 2)

    def test_duplicate_launch_is_rejected(self) -> None:
        self.assert_fault_rejected(Faults(duplicate_launch=True), "without an accepted")

    def test_duplicate_raw_commit_is_rejected(self) -> None:
        self.assert_fault_rejected(
            Faults(duplicate_raw_commit=True), "without one launched credit"
        )

    def test_duplicate_observer_retire_is_rejected(self) -> None:
        harness = armed_harness(R1Endpoint(Faults(duplicate_retire=True)))
        harness.run_cycle(True, 5)
        with self.assertRaisesRegex(ContractViolation, "phantom, duplicate"):
            drain_observer(harness)

    def test_dropped_raw_commit_is_rejected(self) -> None:
        self.assert_fault_rejected(Faults(drop_raw_commit=True), "did not complete")

    def test_hidden_reconstruction_is_rejected(self) -> None:
        self.assert_fault_rejected(
            Faults(corrupt_wire_hidden_reconstruction=True),
            "wire symbols do not carry",
        )

    def test_stale_post_reset_raw_commit_is_rejected(self) -> None:
        harness = armed_harness(R1Endpoint(Faults(stale_after_reset=True)))
        harness.ref_edge(True, 3)
        harness.frame_rise()
        harness.reset(require_drained=False)
        with self.assertRaisesRegex(ContractViolation, "without one launched credit"):
            harness.frame_fall()

    def test_phantom_frame_is_rejected_on_idle_cycle(self) -> None:
        harness = ContractHarness(R1Endpoint(Faults(phantom_frame=True)))
        with self.assertRaisesRegex(ContractViolation, "without an accepted"):
            harness.run_cycle(False)

    def test_wrong_launch_phase_is_rejected(self) -> None:
        self.assert_fault_rejected(Faults(wrong_launch_phase=True), "one quarter")

    def test_wrong_raw_commit_phase_is_rejected(self) -> None:
        self.assert_fault_rejected(
            Faults(wrong_raw_commit_phase=True), "exactly 3/4"
        )

    def test_wrong_observer_phase_is_rejected(self) -> None:
        harness = armed_harness(R1Endpoint(Faults(wrong_observer_phase=True)))
        harness.run_cycle(True, 8)
        with self.assertRaisesRegex(ContractViolation, "exactly one cycle"):
            drain_observer(harness)

    def test_omitted_same_cycle_launch_drain_guard_is_rejected(self) -> None:
        harness = armed_harness(R1Endpoint(Faults(omit_launch_drain_guard=True)))
        harness.ref_edge(True, 4)
        with self.assertRaisesRegex(ContractViolation, "every causal credit"):
            harness.assert_drain_consistent()

    def test_omitted_pending_valid_drain_guard_is_rejected(self) -> None:
        harness = armed_harness(
            R1Endpoint(Faults(omit_pending_valid_drain_guard=True))
        )
        harness.run_cycle(True, 4)
        harness.run_cycle(False)
        with self.assertRaisesRegex(ContractViolation, "every causal credit"):
            harness.assert_drain_consistent()

    def test_payload_or_wide_address_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "four-bit N16"):
            armed_harness().run_cycle(True, 0xA5)


class DecisionGateTest(unittest.TestCase):
    @staticmethod
    def evidence(**updates) -> PpaEvidence:
        fields = dict(
            mandatory_endpoint_tests_passed=True,
            throughput_ratio_to_parallel=Fraction(1, 1),
            raw_ddr_commit_delay_cycles=Fraction(3, 4),
            output_availability_delay_cycles=Fraction(1, 1),
            synchronous_consume_delay_cycles=Fraction(2, 1),
            latency_delta_to_parallel_cycles=Fraction(0, 1),
            ddr_functional_pins=3,
            parallel_functional_pins=5,
            ddr_launch_state_bits=1,
            ddr_tx_state_bits=5,
            ddr_icg_state_bits=1,
            ddr_rx_state_bits=7,
            ddr_observer_state_bits=6,
            ddr_declared_total_state_bits=20,
            parallel_declared_total_state_bits=18,
            ddr_charged_functional_cells=29,
            parallel_charged_functional_cells=27,
            drain_guard_charged=True,
            boundary_queue_entries=0,
            payload_bits=0,
            hidden_reconstruction=False,
            post_route=True,
            setup_wns_ns=0.1,
            hold_wns_ns=0.05,
            forwarded_clock_qualified=True,
            endpoint_area=120.0,
            parallel_area=100.0,
            energy_per_event=1.05,
            parallel_energy_per_event=1.0,
            max_area_penalty_per_pin_saved=10.0,
            max_energy_penalty_fraction=0.05,
        )
        fields.update(updates)
        return PpaEvidence(**fields)

    def test_complete_predeclared_evidence_can_go(self) -> None:
        decision = qualify(self.evidence())
        self.assertEqual(
            (decision.functional, decision.physical, decision.adoption),
            ("GO", "GO", "GO"),
        )

    def test_missing_economic_budget_holds_adoption(self) -> None:
        decision = qualify(self.evidence(
            max_area_penalty_per_pin_saved=None,
            max_energy_penalty_fraction=None,
        ))
        self.assertEqual(
            (decision.functional, decision.physical, decision.adoption),
            ("GO", "GO", "HOLD"),
        )

    def test_accounting_and_physical_mutations_hold(self) -> None:
        mutations = (
            {"throughput_ratio_to_parallel": Fraction(99, 100)},
            {"raw_ddr_commit_delay_cycles": Fraction(1, 1)},
            {"output_availability_delay_cycles": Fraction(3, 4)},
            {"synchronous_consume_delay_cycles": Fraction(1, 1)},
            {"latency_delta_to_parallel_cycles": Fraction(1, 4)},
            {"ddr_functional_pins": 4},
            {"ddr_declared_total_state_bits": 19},
            {"parallel_declared_total_state_bits": 17},
            {"ddr_charged_functional_cells": 0},
            {"parallel_charged_functional_cells": 0},
            {"drain_guard_charged": False},
            {"ddr_observer_state_bits": 0, "ddr_declared_total_state_bits": 14},
            {"boundary_queue_entries": 1},
            {"payload_bits": 1},
            {"hidden_reconstruction": True},
            {"post_route": False},
            {"setup_wns_ns": -0.01},
            {"hold_wns_ns": -0.01},
            {"forwarded_clock_qualified": False},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertEqual(qualify(self.evidence(**mutation)).adoption, "HOLD")


if __name__ == "__main__":
    unittest.main()
