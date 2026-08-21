from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
    validate_existing_registry,
    validate_registry,
)


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_comparison_contract()

    def test_frozen_contract_and_existing_registry_validate(self) -> None:
        self.assertEqual(
            self.contract.canonical_sha256,
            "a95eab8d29196175bb05e3b705f65ce8f0304929f69fece3ed71c974148bf631",
        )
        receipt = validate_existing_registry(self.contract)
        self.assertEqual(receipt.window_count, 24)
        self.assertEqual(receipt.canonical_sha256, self.contract.registry["sha256"])
        self.assertEqual(
            receipt.forbidden_interval_ns, (43320750000, 43322000000)
        )

    def test_score_free_accounting_contract_is_exact(self) -> None:
        accounting = self.contract.as_dict()["score_free_accounting"]
        self.assertEqual(
            set(accounting),
            {
                "schema",
                "freeze_boundary",
                "classification_population",
                "corrected_disposition",
                "corrected_disposition_classification",
                "corrected_reason_allowlist_by_arm",
                "raw_reason_classification_by_arm",
                "unknown_arm_disposition_reason",
                "set_rules",
                "rate_denominators",
                "query_event_bandwidth",
                "residence_bit_cycles",
                "delayed_fifo",
                "common_state_envelope",
                "pose_interface",
            },
        )
        self.assertEqual(
            accounting["schema"],
            "redred.mc_wtb.stage4_score_free_accounting/v1",
        )
        self.assertEqual(
            accounting["freeze_boundary"],
            "sealed_before_any_ray_loss_or_arm_score_access",
        )
        self.assertEqual(
            accounting["classification_population"],
            "accepted_query_event_ids_only",
        )
        self.assertEqual(accounting["corrected_disposition"], "corrected_world_ray")
        self.assertEqual(
            accounting["corrected_disposition_classification"],
            ["attempted_correction"],
        )
        self.assertEqual(
            accounting["corrected_reason_allowlist_by_arm"],
            {
                "zoh_freshness": ["fresh_zoh"],
                "causal_cav": ["causal_cav", "fresh_zoh_fallback"],
                "delayed_exact": ["bracket_interpolation"],
                "oracle_resampled_groundtruth_1khz": ["oracle_fresh_zoh"],
            },
        )
        self.assertEqual(
            accounting["raw_reason_classification_by_arm"],
            {
                "zoh_freshness": {
                    "stale_pose": "freshness_veto",
                    "no_occurrence_pose": "invalid_pose_bypass",
                    "invalid_pose": "invalid_pose_bypass",
                },
                "causal_cav": {
                    "stale_pose": "freshness_veto",
                    "no_occurrence_pose": "invalid_pose_bypass",
                    "invalid_pose": "invalid_pose_bypass",
                },
                "delayed_exact": {
                    "deadline_timeout": "operational_waste",
                    "fifo_full_forced_bypass": "operational_waste",
                    "invalid_pose": "operational_waste",
                    "missing_bracket": "invalid_pose_bypass",
                },
                "oracle_resampled_groundtruth_1khz": {
                    "stale_pose": "freshness_veto",
                    "no_occurrence_pose": "invalid_pose_bypass",
                    "invalid_pose": "invalid_pose_bypass",
                },
            },
        )
        self.assertEqual(
            accounting["unknown_arm_disposition_reason"], "protocol_failure"
        )
        self.assertEqual(
            accounting["set_rules"],
            {
                "attempted_correction_event_ids": (
                    "corrected_query_event_ids_union_operational_waste_event_ids"
                ),
                "raw_bypass_partition": (
                    "freshness_veto_union_invalid_pose_bypass_union_operational_waste"
                ),
                "raw_bypass_sets_pairwise_disjoint": True,
                "raw_bypass_partition_exhaustive": True,
            },
        )
        self.assertEqual(
            accounting["rate_denominators"],
            {
                "enable_rate": "accepted_query_event_ids",
                "freshness_veto_rate": "accepted_query_event_ids",
                "invalid_pose_bypass_rate": "accepted_query_event_ids",
                "operational_waste_rate": "attempted_correction_event_ids",
                "zero_attempted_denominator": "go_coverage_failure",
            },
        )
        self.assertEqual(
            accounting["query_event_bandwidth"],
            {
                "population": "accepted_query_event_ids_only",
                "record_bits": 102,
                "window_interval": (
                    "query_start_ns_inclusive_to_query_end_ns_exclusive"
                ),
                "rounding": "ceil_integer",
                "event_bandwidth_bits_per_second_rule": (
                    "ceil_integer(102*accepted_query_events*1000000000/"
                    "(query_end_ns_exclusive-query_start_ns_inclusive))"
                ),
            },
        )
        self.assertEqual(
            accounting["residence_bit_cycles"],
            {
                "population": (
                    "all_accepted_events_in_full_cycle_result_including_warmup_and_query"
                ),
                "record_bits": 102,
                "interval_convention": "half_open_cycle_intervals",
                "required_cycle_order": (
                    "occurrence_cycle_le_admission_cycle_le_retire_cycle"
                ),
                "buffer_bit_cycles_rule": (
                    "102*(sum_all_events(admission_cycle-occurrence_cycle)+"
                    "indicator_arm_is_delayed_exact*sum_all_events("
                    "retire_cycle-admission_cycle))"
                ),
                "added_to_static_state_bits": False,
            },
        )
        self.assertEqual(
            accounting["delayed_fifo"],
            {
                "bounded_entries": 1024,
                "full_action": "oldest_eligible_head_ordered_raw_bypass",
                "full_reason": "fifo_full_forced_bypass",
                "full_classification": "operational_waste",
                "external_or_unbounded_overflow_queue_allowed": False,
                "minimum_zero_loss_buffer_entries": {
                    "bounded_peak_authoritative_if": (
                        "fifo_full_forced_bypass_count_is_zero_and_full_"
                        "conservation_holds"
                    ),
                    "authoritative_bounded_value": "observed_peak_buffer_entries",
                    "bounded_peak_authoritative_if_any_fifo_full_forced_bypass": (
                        False
                    ),
                    "otherwise": (
                        "fail_closed_unless_separate_score_free_unbounded_depth_"
                        "replay_proves_depth"
                    ),
                    "unbounded_depth_replay_method": (
                        "same_arrivals_ordering_service_deadline_and_retirement_"
                        "without_fifo_pressure_action"
                    ),
                    "unbounded_depth_replay_may_change_bounded_decisions": False,
                    "nontermination_unbounded_growth_or_unaccounted_event": (
                        "hard_stop"
                    ),
                    "proven_depth_above_bounded_entries": "hard_stop",
                },
            },
        )

        state = accounting["common_state_envelope"]
        self.assertEqual(
            state["components_bits"],
            {
                "delayed_fifo_payload": 104448,
                "ingress_capture_payload": 612,
                "pose_ring_payload": 3072,
                "delayed_fifo_pointers_and_occupancy": 31,
                "ingress_serializer_count_and_cursor": 6,
                "pose_ring_write_pointer_and_valid_count": 9,
                "pose_ring_live_reference_counters": 176,
                "transform_pipeline_payload": 204,
                "atomic_pose_ingress_staging": 192,
                "global_cycle_and_deadline_counter": 21,
                "expected_and_retired_receipt_counters": 28,
            },
        )
        self.assertEqual(len(state["components_bits"]), 11)
        self.assertEqual(sum(state["components_bits"].values()), 108799)
        self.assertEqual(
            {key: value for key, value in state.items() if key != "components_bits"},
            {
                "scope": "conservatively_charged_to_every_arm",
                "component_count": 11,
                "incremental_state_bits": 108799,
                "live_reference_counter_entries": 16,
                "live_reference_counter_width_bits": 11,
                "maximum_simultaneous_live_references": 1032,
                "transport_sequence_tag_already_in_event_record": True,
                "causal_pose_index_already_in_event_record": True,
                "full_source_event_id_hardware_state_bits": 0,
                "verification_hash_state_bits": 0,
                "evidence_class": "logical_comparison_accounting_not_ppa",
            },
        )
        self.assertEqual(
            accounting["pose_interface"],
            {
                "scope": "conservatively_charged_to_every_arm",
                "packet_bits": 192,
                "packets_per_second": 1000,
                "pose_bandwidth_bits_per_second": 192000,
                "evidence_class": (
                    "logical_comparison_accounting_not_physical_interface_measurement"
                ),
            },
        )
        gates = self.contract.as_dict()["go_to_epoch_integration"]
        self.assertLessEqual(
            state["incremental_state_bits"], gates["maximum_incremental_state_bits"]
        )
        self.assertLessEqual(
            accounting["pose_interface"]["pose_bandwidth_bits_per_second"],
            gates["maximum_pose_bandwidth_bits_per_second"],
        )

    def test_event_record_identity_contract_is_exact(self) -> None:
        timing = self.contract.as_dict()["timing"]
        identity = timing["event_record_identity"]
        self.assertEqual(
            identity,
            {
                "payload_field": "transport_sequence_tag_not_dataset_event_index",
                "transport_sequence_tag_bits": 24,
                "transport_sequence_tag_modulus": 1 << 24,
                "transport_sequence_tag_rule": "source_event_id_modulo_2^24",
                "independent_reset_domain": "each_independently_simulated_window",
                "per_window_transport_sequence_tag_uniqueness_required": True,
                "max_source_event_id_span_per_window_rule": (
                    "serialized_max_source_event_id_minus_min_source_event_id_"
                    "strictly_less_than_2^23"
                ),
                "global_selected_transport_tags_unique": True,
                "global_selected_transport_tag_scope": (
                    "frozen_24_window_assay_artifact"
                ),
                "cross_window_source_event_id_range_used_as_live_span": False,
                "full_source_event_id_scope": (
                    "score_free_artifacts_and_receipts_verification_only"
                ),
                "full_source_event_id_hardware_state_bits": 0,
                "timestamp_bits": 36,
                "timestamp_role": "retained_functional_motion_data",
                "maximum_simultaneous_live_records": 1032,
                "maximum_simultaneous_live_records_role": (
                    "capacity_fact_only_not_wrap_safety_evidence"
                ),
                "serial_number_half_range": 1 << 23,
                "wrap_safety_source_event_id_span_rule": (
                    "every_simultaneously_live_or_replayable_set_max_source_"
                    "event_id_minus_min_source_event_id_strictly_less_than_2^23"
                ),
                "cycle_observer_alias_policy": (
                    "verify_transport_tags_fail_closed_on_alias_and_never_use_"
                    "full_source_event_ids_to_mask_collision"
                ),
                "mismatch_collision_or_span_violation": (
                    "fail_closed_before_scoring"
                ),
            },
        )
        self.assertEqual(timing["event_record_bits"], 102)
        self.assertEqual(
            identity["maximum_simultaneous_live_records"],
            self.contract.as_dict()["score_free_accounting"]
            ["common_state_envelope"]["maximum_simultaneous_live_references"],
        )
        self.assertEqual(
            identity["maximum_simultaneous_live_records_role"],
            "capacity_fact_only_not_wrap_safety_evidence",
        )

    def test_contract_rejects_duplicate_key_extra_field_and_wrong_type(self) -> None:
        original = self.contract.as_dict()
        cases = []
        cases.append(
            '{"schema":"a","schema":"b"}'
        )
        extra = copy.deepcopy(original)
        extra["unfrozen"] = 1
        cases.append(json.dumps(extra))
        wrong_type = copy.deepcopy(original)
        wrong_type["registry"]["window_count"] = True
        cases.append(json.dumps(wrong_type))
        for index, payload in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "contract.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ContractError):
                    load_comparison_contract(path)

    def test_registry_rejects_hash_mutation_and_forbidden_overlap(self) -> None:
        rows = [dict(row) for row in window_registry()]
        changed = copy.deepcopy(rows)
        changed[0]["query_start_ns_inclusive"] += 1
        with self.assertRaisesRegex(ContractError, "hash"):
            validate_registry(self.contract, changed)

        overlap = copy.deepcopy(rows)
        overlap[18] = {
            "window_id": overlap[18]["window_id"],
            "warmup_start_ns_inclusive": 43320750000,
            "query_start_ns_inclusive": 43321000000,
            "query_end_ns_exclusive": 43322000000,
        }
        with self.assertRaisesRegex(ContractError, "forbidden"):
            validate_registry(self.contract, overlap)

    def test_canonical_json_is_order_independent_for_objects_and_ordered_for_arrays(self) -> None:
        left = {"z": 1, "a": [2, 3]}
        right = {"a": [2, 3], "z": 1}
        self.assertEqual(canonical_json_bytes(left), b'{"a":[2,3],"z":1}\n')
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))
        self.assertNotEqual(
            canonical_sha256({"a": [2, 3]}), canonical_sha256({"a": [3, 2]})
        )
        with self.assertRaises(ContractError):
            canonical_json_bytes({"not_finite": float("nan")})


if __name__ == "__main__":
    unittest.main()
