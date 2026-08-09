import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import validate_full_link_qualification as validator


def artifact(root, relative, content):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}


def json_artifact(root, relative, value):
    return artifact(
        root,
        relative,
        json.dumps(value, indent=2, sort_keys=True) + "\n",
    )


def read_json_artifact(root, reference):
    return json.loads((root / reference["path"]).read_text(encoding="utf-8"))


def valid_record(root):
    cycles = 100
    delivered = 50
    native_bits = 37
    link_bits = 8
    clock_mhz = 200.0
    power_mw = 2.0
    synthesis_top = "candidate_full_link"
    block_kinds = (
        "tx", "link", "encoder", "decoder", "serializer", "deserializer",
        "buffer", "cdc", "normalizer", "rx",
    )

    source_entries = []
    for kind in block_kinds:
        reference = artifact(root, f"rtl/{kind}.sv", f"module {kind}; endmodule\n")
        source_entries.append(reference)
    bundle = json_artifact(root, "manifest/bundle.json", {
        "schema_version": 1,
        "files": source_entries,
    })
    filelist = artifact(
        root,
        "manifest/filelist.f",
        "".join(f"rtl/{kind}.sv\n" for kind in block_kinds),
    )

    charged_blocks = []
    hierarchy_evidence = {}
    for kind in block_kinds:
        evidence = artifact(
            root,
            f"evidence/hierarchy_{kind}.rpt",
            f"{synthesis_top}.u_{kind} candidate_{kind} rtl/{kind}.sv\n",
        )
        hierarchy_evidence[kind] = evidence
        charged_blocks.append({
            "name": kind,
            "kind": kind,
            "top": f"candidate_{kind}",
            "hierarchy_path": f"{synthesis_top}.u_{kind}",
            "hierarchy_evidence": evidence,
            "source_files": [f"rtl/{kind}.sv"],
            "included_in_area": True,
            "included_in_timing": True,
            "included_in_activity": True,
            "included_in_power": True,
        })

    category_for_kind = {
        "encoder": "codec",
        "decoder": "codec",
        "serializer": "serializer",
        "deserializer": "deserializer",
        "buffer": "buffer",
        "cdc": "cdc",
        "normalizer": "normalizer",
    }
    declarations = {
        category: []
        for category in (
            "codec", "serializer", "deserializer", "buffer", "cdc", "normalizer"
        )
    }
    generated_features = []
    for kind, category in category_for_kind.items():
        entry = {
            "name": f"{kind}_feature",
            "charged_block": kind,
            "hierarchy_path": f"{synthesis_top}.u_{kind}",
            "evidence": hierarchy_evidence[kind],
        }
        declarations[category].append(entry)
        generated_features.append({
            "name": entry["name"],
            "category": category,
            "charged_block": kind,
            "hierarchy_path": entry["hierarchy_path"],
        })

    mapped_inventory = json_artifact(root, "evidence/mapped_hierarchy.json", {
        "schema_version": 1,
        "synthesis_top": synthesis_top,
        "blocks": [
            {
                "name": block["name"],
                "kind": block["kind"],
                "top": block["top"],
                "hierarchy_path": block["hierarchy_path"],
                "source_files": block["source_files"],
            }
            for block in charged_blocks
        ],
    })
    generated_inventory = json_artifact(root, "evidence/generated_features.json", {
        "schema_version": 1,
        "synthesis_top": synthesis_top,
        "features": generated_features,
    })

    flow_artifacts = {}
    for field in (
        "tool_config", "sdc", "library", "post_elaboration_report",
        "synthesis_hierarchy_report", "synthesis_evidence", "mapped_netlist",
        "area_report", "stage_report", "setup_report", "hold_report",
        "route_report", "unconstrained_report", "drc_report",
    ):
        flow_artifacts[field] = artifact(
            root, f"evidence/{field}.txt", f"verified {field}\n"
        )
    flow_artifacts["mapped_hierarchy_inventory"] = mapped_inventory
    flow_artifacts["generated_feature_inventory"] = generated_inventory

    activity_artifacts = {
        field: artifact(root, f"activity/{field}.dat", f"verified {field}\n")
        for field in (
            "trace", "prepared_input", "activity_artifact", "power_report",
            "common_result",
        )
    }
    events_per_cycle = delivered / cycles
    return {
        "schema_version": 3,
        "qualification_id": "candidate-n16-sparse",
        "status": "frozen",
        "candidate": {
            "id": "candidate",
            "repo_url": "ssh://example.invalid/candidate.git",
            "commit_sha": "1a2b3c4d",
            "bundle_inventory": bundle,
            "synthesis_top": synthesis_top,
            "filelist": filelist,
            "parameters": {"N": 16},
            "defines": [],
            "include_dirs": [],
        },
        "logical_contract": {
            "event_identity_mode": "address_only",
            "source_count": 16,
            "source_mapping": {
                "description": "logical source equals native row-major index",
                "artifact": artifact(
                    root, "contract/source_mapping.txt", "0..15 -> 0..15\n"
                ),
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
            "free_wiring": [{
                "name": "scoreboard_address_widen",
                "operation": "zero_extension",
                "description": "zero extend recovered source for scoreboard only",
            }],
        },
        "feature_declarations": declarations,
        "charged_blocks": charged_blocks,
        "flow": {
            **flow_artifacts,
            "pvt_rc_corner": "slow_0p9v_125c_rcworst",
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
            **activity_artifacts,
            "format": "saif",
            "hierarchy_root": synthesis_top,
            "coverage_percent": 99.0,
            "coverage_threshold_percent": 95.0,
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
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.record = valid_record(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self, record=None):
        return validator.validate_record(record or self.record, self.root)

    def rewrite_json_reference(self, owner, field, relative, value):
        owner[field] = json_artifact(self.root, relative, value)

    def test_schema_is_machine_readable_draft_2020_12_v3(self):
        schema = json.loads(
            (ROOT / "full_link_qualification.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 3)

    def test_valid_full_link_record_recomputes_metrics(self):
        result = self.validate()
        self.assertEqual(result["native_functional_pin_bits"], 37)
        self.assertEqual(result["link_functional_pin_bits"], 8)
        self.assertAlmostEqual(result["events_per_cycle"], 0.5)
        self.assertAlmostEqual(result["energy_nj_per_delivered_event"], 0.02)

    def test_schema_required_type_and_additional_properties_are_enforced(self):
        cases = []
        missing = copy.deepcopy(self.record)
        del missing["flow"]["area_report"]
        cases.append(("missing", missing, r"flow\.area_report is required"))
        wrong = copy.deepcopy(self.record)
        wrong["flow"]["results"]["mapped_cell_count"] = "123"
        cases.append(("type", wrong, r"mapped_cell_count must be of type integer"))
        extra = copy.deepcopy(self.record)
        extra["candidate"]["untracked_define"] = "FREE_FEATURE"
        cases.append(("extra", extra, r"untracked_define is an additional property"))
        for name, record, pattern in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(validator.QualificationError, pattern):
                    self.validate(record)

    def test_every_evidence_reference_requires_path_and_sha(self):
        for replacement in ({"sha256": "a" * 64}, {"path": "activity/power_report.dat"}):
            record = copy.deepcopy(self.record)
            record["activity"]["power_report"] = replacement
            with self.subTest(replacement=replacement):
                with self.assertRaisesRegex(validator.QualificationError, "is required"):
                    self.validate(record)

        record = copy.deepcopy(self.record)
        record["activity"]["power_report"]["unchecked_size"] = 123
        with self.assertRaisesRegex(validator.QualificationError, "additional property"):
            self.validate(record)

    def test_all_evidence_classes_are_actual_digest_checked(self):
        references = [
            self.record["candidate"]["bundle_inventory"],
            self.record["candidate"]["filelist"],
            self.record["logical_contract"]["source_mapping"]["artifact"],
            *(block["hierarchy_evidence"] for block in self.record["charged_blocks"]),
            *(
                declaration["evidence"]
                for declarations in self.record["feature_declarations"].values()
                for declaration in declarations
            ),
            *(
                self.record["flow"][field]
                for field in (
                    "tool_config", "sdc", "library", "post_elaboration_report",
                    "synthesis_hierarchy_report", "synthesis_evidence",
                    "mapped_netlist", "mapped_hierarchy_inventory",
                    "generated_feature_inventory", "area_report", "stage_report",
                    "setup_report", "hold_report", "route_report",
                    "unconstrained_report", "drc_report",
                )
            ),
            *(
                self.record["activity"][field]
                for field in (
                    "trace", "prepared_input", "activity_artifact", "power_report",
                    "common_result",
                )
            ),
        ]
        for index, reference in enumerate(references):
            original = reference["sha256"]
            reference["sha256"] = "0" * 64
            with self.subTest(path=reference["path"]):
                with self.assertRaisesRegex(validator.QualificationError, "digest mismatch"):
                    self.validate()
            reference["sha256"] = original

    def test_actual_digest_mismatch_rejects_power_and_common_result(self):
        for field in ("power_report", "common_result"):
            record = copy.deepcopy(self.record)
            record["activity"][field]["sha256"] = "0" * 64
            with self.subTest(field=field):
                with self.assertRaisesRegex(validator.QualificationError, "digest mismatch"):
                    self.validate(record)

    def test_symlink_and_non_regular_evidence_are_rejected(self):
        target = self.root / "activity" / "power_report.dat"
        link = self.root / "activity" / "power-link.dat"
        os.symlink(target.name, link)
        record = copy.deepcopy(self.record)
        record["activity"]["power_report"]["path"] = "activity/power-link.dat"
        with self.assertRaisesRegex(validator.QualificationError, "symlink"):
            self.validate(record)

        directory = self.root / "activity" / "not-a-file"
        directory.mkdir()
        record = copy.deepcopy(self.record)
        record["activity"]["common_result"] = {
            "path": "activity/not-a-file", "sha256": hashlib.sha256(b"").hexdigest()
        }
        with self.assertRaisesRegex(validator.QualificationError, "regular file"):
            self.validate(record)

    def test_mutation_during_stable_read_is_rejected(self):
        path = self.root / "activity" / "power_report.dat"
        original_read = validator.os.read
        changed = False

        def mutate_after_first_read(descriptor, count):
            nonlocal changed
            data = original_read(descriptor, count)
            if data and not changed:
                changed = True
                path.write_bytes(b"mutated while open\n")
            return data

        errors = []
        with mock.patch.object(validator.os, "read", side_effect=mutate_after_first_read):
            result = validator._stable_read_regular(path, "power", errors)
        self.assertIsNone(result)
        self.assertIn("changed during stable read", "\n".join(errors))

    def test_non_normalized_artifact_path_is_rejected(self):
        record = copy.deepcopy(self.record)
        record["activity"]["power_report"]["path"] = "activity//power_report.dat"
        with self.assertRaisesRegex(validator.QualificationError, "must be normalized"):
            self.validate(record)

    def test_bundle_inventory_verifies_each_source_digest(self):
        (self.root / "rtl" / "tx.sv").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(validator.QualificationError, "digest mismatch"):
            self.validate()

    def test_bundle_filelist_order_and_charged_source_closure_are_exact(self):
        record = copy.deepcopy(self.record)
        record["candidate"]["filelist"] = artifact(
            self.root,
            "manifest/reordered.f",
            "rtl/link.sv\nrtl/tx.sv\n" + "".join(
                f"rtl/{kind}.sv\n" for kind in (
                    "encoder", "decoder", "serializer", "deserializer", "buffer",
                    "cdc", "normalizer", "rx",
                )
            ),
        )
        with self.assertRaisesRegex(validator.QualificationError, "same source paths"):
            self.validate(record)

        record = copy.deepcopy(self.record)
        record["charged_blocks"][0]["source_files"] = ["rtl/link.sv"]
        with self.assertRaisesRegex(validator.QualificationError, "charged source closure"):
            self.validate(record)

    def test_mapped_hierarchy_must_match_charged_blocks(self):
        record = copy.deepcopy(self.record)
        inventory = read_json_artifact(self.root, record["flow"]["mapped_hierarchy_inventory"])
        inventory["blocks"][0]["hierarchy_path"] = "candidate_full_link.u_free_tx"
        self.rewrite_json_reference(
            record["flow"], "mapped_hierarchy_inventory",
            "evidence/mapped_hierarchy_bad.json", inventory,
        )
        with self.assertRaisesRegex(validator.QualificationError, "does not match charged block"):
            self.validate(record)

    def test_hidden_serializer_generated_feature_is_rejected(self):
        record = copy.deepcopy(self.record)
        inventory = read_json_artifact(self.root, record["flow"]["generated_feature_inventory"])
        inventory["features"].append({
            "name": "hidden_serializer",
            "category": "serializer",
            "charged_block": "serializer",
            "hierarchy_path": "candidate_full_link.u_hidden_serializer",
        })
        self.rewrite_json_reference(
            record["flow"], "generated_feature_inventory",
            "evidence/features_hidden_serializer.json", inventory,
        )
        with self.assertRaisesRegex(validator.QualificationError, "hidden_serializer"):
            self.validate(record)

    def test_hidden_fifo_source_is_rejected_as_uncharged(self):
        record = copy.deepcopy(self.record)
        hidden = artifact(self.root, "rtl/hidden_fifo.sv", "module hidden_fifo; endmodule\n")
        inventory = read_json_artifact(self.root, record["candidate"]["bundle_inventory"])
        inventory["files"].append(hidden)
        self.rewrite_json_reference(
            record["candidate"], "bundle_inventory", "manifest/bundle_hidden_fifo.json",
            inventory,
        )
        original = (self.root / record["candidate"]["filelist"]["path"]).read_text(
            encoding="utf-8"
        )
        record["candidate"]["filelist"] = artifact(
            self.root, "manifest/filelist_hidden_fifo.f", original + "rtl/hidden_fifo.sv\n"
        )
        with self.assertRaisesRegex(validator.QualificationError, "hidden_fifo"):
            self.validate(record)

    def test_hidden_cdc_mapped_hierarchy_is_rejected(self):
        record = copy.deepcopy(self.record)
        inventory = read_json_artifact(self.root, record["flow"]["mapped_hierarchy_inventory"])
        inventory["blocks"].append({
            "name": "hidden_cdc",
            "kind": "cdc",
            "top": "hidden_cdc",
            "hierarchy_path": "candidate_full_link.u_hidden_cdc",
            "source_files": ["rtl/cdc.sv"],
        })
        self.rewrite_json_reference(
            record["flow"], "mapped_hierarchy_inventory",
            "evidence/mapped_hierarchy_hidden_cdc.json", inventory,
        )
        with self.assertRaisesRegex(validator.QualificationError, "hidden_cdc"):
            self.validate(record)

    def test_feature_declaration_is_one_to_one_with_charged_hierarchy(self):
        record = copy.deepcopy(self.record)
        record["feature_declarations"]["buffer"] = []
        with self.assertRaisesRegex(validator.QualificationError, "no 1:1 feature declaration"):
            self.validate(record)

        record = copy.deepcopy(self.record)
        record["feature_declarations"]["codec"][0]["evidence"] = record[
            "charged_blocks"
        ][3]["hierarchy_evidence"]
        with self.assertRaisesRegex(validator.QualificationError, "evidence must match"):
            self.validate(record)

    def test_serializer_deserializer_pair_is_required(self):
        record = copy.deepcopy(self.record)
        record["feature_declarations"]["deserializer"] = []
        with self.assertRaisesRegex(validator.QualificationError, "must both be present"):
            self.validate(record)

    def test_activity_root_and_positive_frozen_coverage_are_enforced(self):
        record = copy.deepcopy(self.record)
        record["activity"]["hierarchy_root"] = "testbench"
        with self.assertRaisesRegex(validator.QualificationError, "must equal candidate synthesis_top"):
            self.validate(record)

        record = copy.deepcopy(self.record)
        record["activity"]["coverage_threshold_percent"] = 0.0
        with self.assertRaisesRegex(validator.QualificationError, "must be positive"):
            self.validate(record)

        record = copy.deepcopy(self.record)
        record["activity"]["coverage_percent"] = 90.0
        with self.assertRaisesRegex(validator.QualificationError, "below the frozen threshold"):
            self.validate(record)

    def test_physical_signoff_failures_are_rejected(self):
        for field, value in (
            ("setup_wns_ns", -0.01), ("hold_wns_ns", -0.01),
            ("detailed_route_completed", False), ("unresolved_references", 1),
            ("unconstrained_paths", 1), ("drc_violations", 1),
        ):
            record = copy.deepcopy(self.record)
            record["flow"]["results"][field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(validator.QualificationError, rf"flow\.results\.{field}"):
                    self.validate(record)

    def test_runtime_encoding_requires_charged_encoder_and_decoder(self):
        record = copy.deepcopy(self.record)
        record["charged_blocks"] = [
            block for block in record["charged_blocks"]
            if block["kind"] not in {"encoder", "decoder"}
        ]
        with self.assertRaisesRegex(validator.QualificationError, "requires charged encoder"):
            self.validate(record)

    def test_derived_metric_mismatch_is_rejected(self):
        record = copy.deepcopy(self.record)
        record["metrics"]["events_per_link_pin_cycle"] = 0.5
        with self.assertRaisesRegex(validator.QualificationError, "events_per_link_pin_cycle"):
            self.validate(record)

    def test_frozen_energy_row_cannot_use_vectorless_power(self):
        record = copy.deepcopy(self.record)
        record["activity"]["power_evidence"] = "vectorless_screening"
        with self.assertRaisesRegex(validator.QualificationError, "activity_annotated"):
            self.validate(record)

    def test_cli_uses_record_directory_for_artifacts(self):
        record_path = self.root / "qualification.json"
        record_path.write_text(json.dumps(self.record), encoding="utf-8")
        self.assertEqual(validator.main([str(record_path)]), 0)

    def test_input_is_not_mutated(self):
        original = copy.deepcopy(self.record)
        self.validate()
        self.assertEqual(self.record, original)


if __name__ == "__main__":
    unittest.main()
