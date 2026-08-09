import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import validate_full_link_qualification as validator


HASH_A = "a" * 64
HASH_B = "b" * 64


def valid_record():
    cycles = 100
    delivered = 50
    native_bits = 37
    link_bits = 8
    clock_mhz = 200.0
    power_mw = 2.0
    events_per_cycle = delivered / cycles
    return {
        "schema_version": 2,
        "qualification_id": "candidate-n16-sparse",
        "status": "frozen",
        "candidate": {
            "id": "candidate",
            "repo_url": "ssh://example.invalid/candidate.git",
            "commit_sha": "1a2b3c4d",
            "bundle_sha256": HASH_A,
            "synthesis_top": "candidate_full_link",
            "filelist_sha256": HASH_B,
            "parameters": {"N": 16},
            "defines": [],
            "include_dirs": [],
        },
        "logical_contract": {
            "event_identity_mode": "address_only",
            "source_count": 16,
            "source_mapping": {
                "description": "logical_source equals native row-major index",
                "sha256": HASH_A,
                "bijective": True,
            },
            "one_pending_latch_per_source": True,
            "acceptance_rule": "source_req and source_ack at rising edge",
            "delivery_rule": "rx_valid at rising edge identifies one source",
        },
        "tb_seam": {
            "normalized_addr_width": 16,
            "normalized_source_width": 4,
            "retire_lanes": 1,
            "arbitrary_payload": False,
            "tb_only_event_id_in_dut": False,
            "ppa_excluded": True,
        },
        "physical_boundary": {
            "scope": "full_link_tx_link_rx",
            "includes_tx": True,
            "includes_link": True,
            "includes_rx": True,
            "native_boundary_pins": [
                {"name": "source_req", "direction": "input", "width": 16,
                 "role": "functional"},
                {"name": "source_ack", "direction": "output", "width": 16,
                 "role": "functional"},
                {"name": "rx_valid", "direction": "output", "width": 1,
                 "role": "functional"},
                {"name": "rx_addr", "direction": "output", "width": 4,
                 "role": "functional"},
                {"name": "clk", "direction": "input", "width": 1,
                 "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1,
                 "role": "reset"},
            ],
            "native_functional_pin_bits": native_bits,
            "link_encoding": {
                "description": "stateful six-bit code with valid/ready",
                "requires_runtime_decode": True,
            },
            "link_cut": {
                "name": "tx_to_rx",
                "count_each_signal_once": True,
                "pins": [
                    {"name": "valid", "direction": "output", "width": 1,
                     "role": "functional"},
                    {"name": "ready", "direction": "input", "width": 1,
                     "role": "functional"},
                    {"name": "code", "direction": "output", "width": 6,
                     "role": "functional"},
                    {"name": "link_clk", "direction": "input", "width": 1,
                     "role": "clock"},
                ],
                "functional_pin_bits": link_bits,
            },
        },
        "normalization": {
            "runtime_decode_in_tb": False,
            "uses_pending_to_disambiguate": False,
            "zero_feature_tb_binding_excluded": True,
            "scoreboard_only_fields": [
                "normalized_event_container", "retire_source", "tb_only_event_id"
            ],
            "free_wiring": [
                {
                    "name": "scoreboard_address_widen",
                    "operation": "zero_extension",
                    "description": "zero extend recovered four-bit source to 16 bits",
                }
            ],
        },
        "feature_declarations": {
            "codec": [
                {
                    "name": kind,
                    "charged_block": kind,
                    "hierarchy_path": f"candidate_full_link.u_{kind}",
                    "evidence_sha256": HASH_A,
                }
                for kind in ("encoder", "decoder")
            ],
            "serializer": [],
            "deserializer": [],
            "buffer": [],
            "cdc": [],
            "normalizer": [],
        },
        "charged_blocks": [
            {
                "name": kind,
                "kind": kind,
                "top": f"candidate_{kind}",
                "hierarchy_path": f"candidate_full_link.u_{kind}",
                "hierarchy_sha256": HASH_A,
                "rtl_sha256": HASH_A,
                "filelist_sha256": HASH_B,
                "included_in_area": True,
                "included_in_timing": True,
                "included_in_activity": True,
                "included_in_power": True,
            }
            for kind in ("tx", "link", "encoder", "decoder", "rx")
        ],
        "flow": {
            "tool_config_sha256": HASH_A,
            "sdc_sha256": HASH_B,
            "library_sha256": HASH_A,
            "pvt_rc_corner": "slow_0p9v_125c_rcworst",
            "post_elaboration_report_sha256": HASH_B,
            "synthesis_hierarchy_report_sha256": HASH_A,
            "synthesis_evidence_sha256": HASH_B,
            "mapped_netlist_sha256": HASH_A,
            "area_report_sha256": HASH_B,
            "stage_report_sha256": HASH_A,
            "setup_report_sha256": HASH_B,
            "hold_report_sha256": HASH_A,
            "route_report_sha256": HASH_B,
            "unconstrained_report_sha256": HASH_A,
            "drc_report_sha256": HASH_B,
            "results": {
                "mapped_cell_count": 123,
                "area_um2": 456.25,
                "pipeline_stage_count": 3,
                "setup_wns_ns": 0.05,
                "hold_wns_ns": 0.02,
                "detailed_route_completed": True,
                "unresolved_references": 0,
                "unconstrained_paths": 0,
                "drc_violations": 0,
            },
        },
        "activity": {
            "trace_sha256": HASH_A,
            "prepared_input_sha256": HASH_B,
            "format": "saif",
            "activity_sha256": HASH_A,
            "hierarchy_root": "candidate_full_link",
            "coverage_percent": 99.0,
            "window_start_cycle": 20,
            "window_end_cycle_exclusive": 120,
            "measurement_cycles": cycles,
            "clock_mhz": clock_mhz,
            "operating_point": "sparse",
            "power_evidence": "activity_annotated",
            "average_power_mw": power_mw,
            "delivered_events": delivered,
            "power_report_sha256": HASH_B,
            "common_result_sha256": HASH_A,
        },
        "metrics": {
            "events_per_cycle": events_per_cycle,
            "events_per_native_pin_cycle": delivered / (cycles * native_bits),
            "events_per_link_pin_cycle": delivered / (cycles * link_bits),
            "energy_nj_per_delivered_event": (
                power_mw / (clock_mhz * events_per_cycle)
            ),
        },
    }


def add_feature(record, category, kind=None):
    block_kind = kind or category
    name = f"{category}_feature"
    hierarchy_path = f"candidate_full_link.u_{name}"
    record["charged_blocks"].append({
        "name": name,
        "kind": block_kind,
        "top": f"candidate_{name}",
        "hierarchy_path": hierarchy_path,
        "hierarchy_sha256": HASH_A,
        "rtl_sha256": HASH_B,
        "filelist_sha256": HASH_A,
        "included_in_area": True,
        "included_in_timing": True,
        "included_in_activity": True,
        "included_in_power": True,
    })
    record["feature_declarations"][category].append({
        "name": name,
        "charged_block": name,
        "hierarchy_path": hierarchy_path,
        "evidence_sha256": HASH_A,
    })
    return name


class FullLinkQualificationTest(unittest.TestCase):
    def test_schema_is_machine_readable_draft_2020_12(self):
        schema = json.loads(
            (ROOT / "full_link_qualification.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)

    def test_valid_full_link_record_recomputes_metrics(self):
        result = validator.validate_record(valid_record())
        self.assertEqual(result["native_functional_pin_bits"], 37)
        self.assertEqual(result["link_functional_pin_bits"], 8)
        self.assertAlmostEqual(result["events_per_cycle"], 0.5)
        self.assertAlmostEqual(result["energy_nj_per_delivered_event"], 0.02)

    def test_schema_required_type_and_additional_properties_are_enforced(self):
        cases = []

        missing = valid_record()
        del missing["flow"]["area_report_sha256"]
        cases.append(("missing", missing, r"flow\.area_report_sha256 is required"))

        wrong_type = valid_record()
        wrong_type["flow"]["results"]["mapped_cell_count"] = "123"
        cases.append((
            "wrong_type", wrong_type,
            r"mapped_cell_count must be of type integer",
        ))

        extra_root = valid_record()
        extra_root["unaccounted_result"] = HASH_A
        cases.append((
            "extra_root", extra_root,
            r"unaccounted_result is an additional property",
        ))

        extra_nested = valid_record()
        extra_nested["candidate"]["untracked_define"] = "FREE_FEATURE"
        cases.append((
            "extra_nested", extra_nested,
            r"candidate\.untracked_define is an additional property",
        ))

        for name, record, pattern in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(validator.QualificationError, pattern):
                    validator.validate_record(record)

    def test_all_physical_feature_categories_map_one_to_one(self):
        record = valid_record()
        add_feature(record, "serializer")
        add_feature(record, "deserializer")
        add_feature(record, "buffer")
        add_feature(record, "cdc")
        add_feature(record, "normalizer")
        validator.validate_record(record)

    def test_feature_declaration_cannot_reference_missing_or_wrong_block(self):
        missing = valid_record()
        missing["feature_declarations"]["buffer"].append({
            "name": "missing_buffer",
            "charged_block": "missing_buffer",
            "hierarchy_path": "candidate_full_link.u_missing_buffer",
            "evidence_sha256": HASH_A,
        })
        with self.assertRaisesRegex(
            validator.QualificationError, "references unknown charged block"
        ):
            validator.validate_record(missing)

        wrong = valid_record()
        encoder = wrong["feature_declarations"]["codec"].pop(0)
        wrong["feature_declarations"]["buffer"].append(encoder)
        with self.assertRaisesRegex(
            validator.QualificationError, "category buffer cannot declare"
        ):
            validator.validate_record(wrong)

    def test_feature_block_cannot_be_undeclared_or_multiply_declared(self):
        undeclared = valid_record()
        block_name = add_feature(undeclared, "buffer")
        undeclared["feature_declarations"]["buffer"] = []
        with self.assertRaisesRegex(
            validator.QualificationError,
            rf"feature charged block {block_name!r} has no 1:1",
        ):
            validator.validate_record(undeclared)

        duplicate = valid_record()
        duplicate["feature_declarations"]["codec"].append({
            "name": "encoder_alias",
            "charged_block": "encoder",
            "hierarchy_path": "candidate_full_link.u_encoder",
            "evidence_sha256": HASH_A,
        })
        with self.assertRaisesRegex(
            validator.QualificationError, "charged block 'encoder' is declared more than once"
        ):
            validator.validate_record(duplicate)

        reused_name = valid_record()
        add_feature(reused_name, "buffer")
        add_feature(reused_name, "cdc")
        reused_name["feature_declarations"]["cdc"][0]["name"] = "buffer_feature"
        with self.assertRaisesRegex(
            validator.QualificationError, "feature declaration name 'buffer_feature' is reused"
        ):
            validator.validate_record(reused_name)

    def test_feature_evidence_must_match_charged_hierarchy(self):
        record = valid_record()
        record["feature_declarations"]["codec"][0]["evidence_sha256"] = HASH_B
        with self.assertRaisesRegex(
            validator.QualificationError,
            "evidence_sha256 must match charged block 'encoder' hierarchy_sha256",
        ):
            validator.validate_record(record)

    def test_charged_hierarchy_must_be_inside_synthesis_top(self):
        record = valid_record()
        record["charged_blocks"][0]["hierarchy_path"] = "testbench.free_tx"
        with self.assertRaisesRegex(
            validator.QualificationError, "hierarchy_path is outside synthesis_top"
        ):
            validator.validate_record(record)

    def test_serializer_requires_charged_deserializer_peer(self):
        record = valid_record()
        add_feature(record, "serializer")
        with self.assertRaisesRegex(
            validator.QualificationError,
            "serializer and deserializer feature declarations must both be present",
        ):
            validator.validate_record(record)

    def test_synthesis_and_result_evidence_hashes_are_required(self):
        flow_hashes = (
            "synthesis_hierarchy_report_sha256",
            "synthesis_evidence_sha256",
            "mapped_netlist_sha256",
            "area_report_sha256",
            "stage_report_sha256",
            "setup_report_sha256",
            "hold_report_sha256",
            "route_report_sha256",
            "unconstrained_report_sha256",
            "drc_report_sha256",
        )
        for field in flow_hashes:
            record = valid_record()
            record["flow"][field] = "not-a-sha"
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    validator.QualificationError, rf"flow\.{field}"
                ):
                    validator.validate_record(record)

        for field in ("power_report_sha256", "common_result_sha256"):
            record = valid_record()
            record["activity"][field] = "not-a-sha"
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    validator.QualificationError, rf"activity\.{field}"
                ):
                    validator.validate_record(record)

    def test_physical_signoff_failures_are_rejected(self):
        cases = (
            ("setup_wns_ns", -0.01),
            ("hold_wns_ns", -0.01),
            ("detailed_route_completed", False),
            ("unresolved_references", 1),
            ("unconstrained_paths", 1),
            ("drc_violations", 1),
        )
        for field, value in cases:
            record = valid_record()
            record["flow"]["results"][field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    validator.QualificationError, rf"flow\.results\.{field}"
                ):
                    validator.validate_record(record)

    def test_zero_feature_tb_binding_stays_outside_ppa(self):
        record = valid_record()
        record["normalization"]["zero_feature_tb_binding_excluded"] = False
        with self.assertRaisesRegex(
            validator.QualificationError, "zero_feature_tb_binding_excluded"
        ):
            validator.validate_record(record)

    def test_normalized_tb_or_runtime_decode_cannot_be_free(self):
        record = valid_record()
        record["tb_seam"]["ppa_excluded"] = False
        record["normalization"]["runtime_decode_in_tb"] = True
        with self.assertRaisesRegex(
            validator.QualificationError, "(?s)ppa_excluded.*runtime_decode_in_tb"
        ):
            validator.validate_record(record)

    def test_pin_totals_are_derived_from_enumerated_physical_ports(self):
        record = valid_record()
        record["physical_boundary"]["native_functional_pin_bits"] = 53
        with self.assertRaisesRegex(
            validator.QualificationError, "does not match pin list"
        ):
            validator.validate_record(record)

    def test_runtime_encoding_requires_charged_encoder_and_decoder(self):
        record = valid_record()
        record["charged_blocks"] = [
            block
            for block in record["charged_blocks"]
            if block["kind"] not in {"encoder", "decoder"}
        ]
        with self.assertRaisesRegex(
            validator.QualificationError,
            "requires charged encoder and decoder",
        ):
            validator.validate_record(record)

    def test_derived_metric_mismatch_is_rejected(self):
        record = valid_record()
        record["metrics"]["events_per_link_pin_cycle"] = 0.5
        with self.assertRaisesRegex(
            validator.QualificationError, "events_per_link_pin_cycle"
        ):
            validator.validate_record(record)

    def test_frozen_energy_row_cannot_use_vectorless_power(self):
        record = valid_record()
        record["activity"]["power_evidence"] = "vectorless_screening"
        with self.assertRaisesRegex(
            validator.QualificationError, "activity_annotated"
        ):
            validator.validate_record(record)

    def test_input_is_not_mutated(self):
        record = valid_record()
        original = copy.deepcopy(record)
        validator.validate_record(record)
        self.assertEqual(record, original)


if __name__ == "__main__":
    unittest.main()
