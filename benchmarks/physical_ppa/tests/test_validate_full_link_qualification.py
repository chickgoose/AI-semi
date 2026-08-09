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
        "schema_version": 1,
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
        "charged_blocks": [
            {
                "name": kind,
                "kind": kind,
                "top": f"candidate_{kind}",
                "filelist_sha256": HASH_B,
                "included_in_area": True,
                "included_in_timing": True,
                "included_in_activity": True,
                "included_in_power": True,
            }
            for kind in ("tx", "encoder", "decoder", "rx")
        ],
        "flow": {
            "tool_config_sha256": HASH_A,
            "sdc_sha256": HASH_B,
            "library_sha256": HASH_A,
            "pvt_rc_corner": "slow_0p9v_125c_rcworst",
            "post_elaboration_report_sha256": HASH_B,
            "unresolved_references": 0,
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
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)

    def test_valid_full_link_record_recomputes_metrics(self):
        result = validator.validate_record(valid_record())
        self.assertEqual(result["native_functional_pin_bits"], 37)
        self.assertEqual(result["link_functional_pin_bits"], 8)
        self.assertAlmostEqual(result["events_per_cycle"], 0.5)
        self.assertAlmostEqual(result["energy_nj_per_delivered_event"], 0.02)

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
