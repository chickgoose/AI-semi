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


APPROVED_FLOW = json.loads(
    (ROOT / "approved_execution_registry.json").read_text(encoding="utf-8")
)["flows"][0]


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

    source_entries = [artifact(
        root, "rtl/full_link.sv", f"module {synthesis_top}; endmodule\n"
    )]
    for kind in block_kinds:
        reference = artifact(
            root, f"rtl/{kind}.sv", f"module candidate_{kind}; endmodule\n"
        )
        source_entries.append(reference)
    bundle = json_artifact(root, "manifest/bundle.json", {
        "schema_version": 1,
        "files": source_entries,
    })
    filelist = artifact(
        root,
        "manifest/filelist.f",
        "rtl/full_link.sv\n" + "".join(f"rtl/{kind}.sv\n" for kind in block_kinds),
    )

    wrapper_evidence = artifact(
        root, "evidence/hierarchy_wrapper.rpt",
        f"{synthesis_top} {synthesis_top} rtl/full_link.sv\n",
    )
    charged_blocks = [{
        "name": "wrapper", "kind": "wrapper", "top": synthesis_top,
        "hierarchy_path": synthesis_top,
        "hierarchy_evidence": wrapper_evidence,
        "source_files": ["rtl/full_link.sv"],
        "included_in_area": True, "included_in_timing": True,
        "included_in_activity": True, "included_in_power": True,
    }]
    hierarchy_rows = [{
        "name": "wrapper", "kind": "wrapper", "hierarchy_path": synthesis_top,
        "module": synthesis_top,
    }]
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
        hierarchy_rows.append({
            "name": kind,
            "kind": kind,
            "hierarchy_path": f"{synthesis_top}.u_{kind}",
            "module": f"candidate_{kind}",
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
    for kind, category in category_for_kind.items():
        entry = {
            "name": kind,
            "charged_block": kind,
            "hierarchy_path": f"{synthesis_top}.u_{kind}",
        }
        declarations[category].append(entry)

    mapped_text = [
        f"module {synthesis_top}(\n",
        "  input wire [15:0] source_req,\n",
        "  output wire [15:0] source_ack,\n",
        "  output wire rx_valid,\n",
        "  output wire [3:0] rx_addr,\n",
        "  input wire clk,\n",
        "  input wire rst_n\n",
        ");\n",
        "  (* AER_LINK_CUT=\"tx_to_rx\", AER_DIRECTION=\"output\", AER_ROLE=\"functional\" *) wire valid;\n",
        "  (* AER_LINK_CUT=\"tx_to_rx\", AER_DIRECTION=\"input\", AER_ROLE=\"functional\" *) wire ready;\n",
        "  (* AER_LINK_CUT=\"tx_to_rx\", AER_DIRECTION=\"output\", AER_ROLE=\"functional\" *) wire [5:0] code;\n",
        "  (* AER_LINK_CUT=\"tx_to_rx\", AER_DIRECTION=\"input\", AER_ROLE=\"clock\" *) wire link_clk;\n",
    ]
    mapped_text.extend(f"  candidate_{kind} u_{kind}();\n" for kind in block_kinds)
    mapped_text.append("endmodule\n")
    mapped_text.extend(f"module candidate_{kind}; endmodule\n" for kind in block_kinds)
    mapped_netlist = artifact(root, "evidence/mapped_netlist.v", "".join(mapped_text))
    hierarchy_source = json_artifact(root, "evidence/hierarchy_source.json", {
        "schema_version": 1,
        "synthesis_top": synthesis_top,
        "blocks": hierarchy_rows,
    })
    library = artifact(root, "flow/cells.lib", "library(test) {}\n")
    tool_config = artifact(root, "flow/tool_config.tcl", "set trusted 1\n")
    sdc = artifact(
        root, "flow/constraints.sdc",
        "create_clock -name core_clk -period 5.0 [get_ports clk]\n",
    )
    include = artifact(root, "flow/defines.vh", "`define TEST 1\n")
    generated_ip = artifact(
        root, "flow/generated_ip.sv", "module generated_unused_ip; endmodule\n"
    )
    synth_command_value = {
        "schema_version": 1,
        "synthesis_top": synthesis_top,
        "command": [
            "yosys", "-c", tool_config["path"], "-sdc", sdc["path"],
            "-f", filelist["path"], "-I", include["path"],
            "-ip", generated_ip["path"], "-lib", library["path"],
            "-top", synthesis_top, "-o", mapped_netlist["path"],
            "--hierarchy", hierarchy_source["path"],
            "--clock-port", "clk", "--clock-period-ns", "5.0",
            "--link-cut", "tx_to_rx", "--preserve-candidate-hierarchy",
        ],
        "filelist": filelist,
        "tool_config": tool_config,
        "sdc": sdc,
        "mapped_netlist": mapped_netlist,
        "hierarchy_source": hierarchy_source,
        "clock_port": "clk",
        "clock_period_ns": 5.0,
        "reset_ports": ["rst_n"],
        "link_cut_name": "tx_to_rx",
        "top_ownership": "candidate",
        "flatten_policy": "preserve_candidate_hierarchy",
        "include_files": [include],
        "generated_ip": [generated_ip],
        "libraries": [library],
    }
    synthesis_command = json_artifact(
        root, "flow/synthesis_command.json", synth_command_value
    )
    inventory_value = validator.inventory_generator.produce_inventory(
        bundle_data=(root / bundle["path"]).read_bytes(),
        filelist_data=(root / filelist["path"]).read_bytes(),
        mapped_netlist_data=(root / mapped_netlist["path"]).read_bytes(),
        hierarchy_source_data=(root / hierarchy_source["path"]).read_bytes(),
        synthesis_command_data=(root / synthesis_command["path"]).read_bytes(),
        input_paths={
            "bundle_inventory": bundle["path"],
            "filelist": filelist["path"],
            "mapped_netlist": mapped_netlist["path"],
            "hierarchy_source": hierarchy_source["path"],
            "synthesis_command": synthesis_command["path"],
        },
        source_loader=lambda path: (root / path).read_bytes(),
        output_path="evidence/inventory.json",
        generator_sha256=hashlib.sha256(
            Path(validator.inventory_generator.__file__).read_bytes()
        ).hexdigest(),
    )
    inventory = json_artifact(root, "evidence/inventory.json", inventory_value)

    def canonical(relative, evidence_type, values, inputs):
        raw_lines = []
        for field in validator.evidence_extractor.FIELD_TYPES[evidence_type]:
            value = values[field]
            if isinstance(value, bool):
                value = "true" if value else "false"
            raw_lines.append(f"{field}={value}\n")
        raw_path = relative.replace(".json", ".raw")
        raw = artifact(root, raw_path, "".join(raw_lines))
        sentinel = artifact(
            root, relative.replace(".json", ".success"), "FLOW_SUCCESS\n"
        )
        flow_manifest = json_artifact(
            root, relative.replace(".json", ".flow.json"), {
                "schema_version": 1,
                "flow_id": APPROVED_FLOW["flow_id"],
                "tool": copy.deepcopy(APPROVED_FLOW["tool"]),
                "flow_script": copy.deepcopy(APPROVED_FLOW["flow_script"]),
                "command": [
                    APPROVED_FLOW["command0"],
                    *(item for _, reference in inputs for item in ("--input", reference["path"])),
                    "--output", raw["path"],
                    "--success-sentinel", sentinel["path"],
                ],
                "exit_code": 0,
                "status": "success",
                "success_sentinel": {
                    "value": "FLOW_SUCCESS", "artifact": sentinel,
                },
                "inputs": [{"role": role, **reference} for role, reference in inputs],
                "outputs": [
                    {"role": "raw_report", **raw},
                    {"role": "success_sentinel", **sentinel},
                ],
            },
        )
        extracted = validator.evidence_extractor.produce_evidence(
            evidence_type=evidence_type,
            raw_data=(root / raw["path"]).read_bytes(),
            raw_path=raw["path"],
            flow_manifest=flow_manifest,
            context_inputs=inputs,
            output_path=relative,
            extractor_sha256=hashlib.sha256(
                Path(validator.evidence_extractor.__file__).read_bytes()
            ).hexdigest(),
        )
        return json_artifact(root, relative, extracted)

    flow_artifacts = {
        "tool_config": tool_config,
        "sdc": sdc,
        "library": library,
        "synthesis_command": synthesis_command,
        "hierarchy_source": hierarchy_source,
        "inventory": inventory,
        "synthesis_hierarchy_report": artifact(
            root, "evidence/synthesis_hierarchy.txt", "candidate_full_link\n"
        ),
        "synthesis_evidence": artifact(
            root, "evidence/synthesis_evidence.txt", "trusted synthesis\n"
        ),
        "mapped_netlist": mapped_netlist,
    }
    area_inputs = [
        ("mapped_netlist", mapped_netlist), ("tool_config", tool_config),
        ("library", library),
    ]
    timing_inputs = [
        ("mapped_netlist", mapped_netlist), ("sdc", sdc), ("library", library),
    ]
    flow_artifacts.update({
        "post_elaboration_report": canonical(
            "evidence/elaboration.json", "elaboration",
            {"unresolved_references": 0}, [("synthesis_command", synthesis_command)],
        ),
        "area_report": canonical(
            "evidence/area.json", "area",
            {"mapped_cell_count": 123, "area_um2": 456.25}, area_inputs,
        ),
        "stage_report": canonical(
            "evidence/stage.json", "stage", {"pipeline_stage_count": 3}, area_inputs,
        ),
        "setup_report": canonical(
            "evidence/setup.json", "setup", {"setup_wns_ns": 0.05}, timing_inputs,
        ),
        "hold_report": canonical(
            "evidence/hold.json", "hold", {"hold_wns_ns": 0.02}, timing_inputs,
        ),
        "route_report": canonical(
            "evidence/route.json", "route", {"detailed_route_completed": True},
            timing_inputs,
        ),
        "unconstrained_report": canonical(
            "evidence/unconstrained.json", "unconstrained",
            {"unconstrained_paths": 0}, timing_inputs,
        ),
        "drc_report": canonical(
            "evidence/drc.json", "drc", {"drc_violations": 0}, timing_inputs,
        ),
    })

    trace = artifact(root, "activity/trace.json", "trace\n")
    prepared = artifact(root, "activity/prepared.dat", "prepared\n")
    activity_input = [
        ("trace", trace), ("prepared_input", prepared),
        ("bundle_inventory", bundle),
    ]
    activity_evidence = canonical(
        "activity/activity.json", "activity",
        {
            "candidate_id": "candidate", "test_id": "uniform", "seed": 7,
            "hierarchy_root": synthesis_top, "format": "saif",
            "clock_port": "clk", "clock_period_ns": 5.0,
            "clock_mhz": clock_mhz,
            "coverage_percent": 99.0, "window_start_cycle": 20,
            "window_end_cycle_exclusive": 120, "measurement_cycles": cycles,
        },
        activity_input,
    )
    power_report = canonical(
        "activity/power.json", "power",
        {
            "candidate_id": "candidate", "test_id": "uniform", "seed": 7,
            "measurement_cycles": cycles, "average_power_mw": power_mw,
            "clock_port": "clk", "clock_period_ns": 5.0,
            "clock_mhz": clock_mhz,
            "errors": 0,
        },
        [
            ("activity", activity_evidence), ("mapped_netlist", mapped_netlist),
            ("library", library),
        ],
    )
    common_result = canonical(
        "activity/common_result.json", "common_result",
        {
            "candidate_id": "candidate", "test_id": "uniform", "seed": 7,
            "measurement_cycles": cycles, "delivered_events": delivered,
            "errors": 0,
        },
        activity_input,
    )
    events_per_cycle = delivered / cycles
    return {
        "schema_version": 5,
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
            "clock_port": "clk",
            "clock_period_ns": 5.0,
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
            "trace": trace,
            "prepared_input": prepared,
            "activity_artifact": activity_evidence,
            "power_report": power_report,
            "common_result": common_result,
            "format": "saif",
            "hierarchy_root": synthesis_top,
            "coverage_percent": 99.0,
            "coverage_threshold_percent": 95.0,
            "window_start_cycle": 20,
            "window_end_cycle_exclusive": 120,
            "measurement_cycles": cycles,
            "clock_port": "clk",
            "clock_period_ns": 5.0,
            "clock_mhz": clock_mhz,
            "operating_point": "sparse",
            "test_id": "uniform",
            "seed": 7,
            "errors": 0,
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

    def rebind_synthesis_input(self, record, field, relative):
        synthesis = read_json_artifact(
            self.root, record["flow"]["synthesis_command"]
        )
        old_reference = synthesis[field]
        synthesis[field] = record["flow"][field]
        synthesis["command"] = [
            record["flow"][field]["path"] if token == old_reference["path"] else token
            for token in synthesis["command"]
        ]
        old_synthesis = record["flow"]["synthesis_command"]
        record["flow"]["synthesis_command"] = json_artifact(
            self.root, relative, synthesis
        )
        inventory = read_json_artifact(self.root, record["flow"]["inventory"])
        for item in inventory["producer"]["inputs"]:
            if item["role"] == field:
                item.update(record["flow"][field])
            if item["role"] == "synthesis_command":
                item.update(record["flow"]["synthesis_command"])
        inventory["producer"]["command"] = [
            record["flow"][field]["path"] if token == old_reference["path"]
            else record["flow"]["synthesis_command"]["path"]
            if token == old_synthesis["path"] else token
            for token in inventory["producer"]["command"]
        ]
        return inventory

    def test_schema_is_machine_readable_draft_2020_12_v5(self):
        schema = json.loads(
            (ROOT / "full_link_qualification.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 5)

    def test_valid_full_link_record_recomputes_metrics(self):
        result = self.validate()
        self.assertEqual(result["native_functional_pin_bits"], 37)
        self.assertEqual(result["link_functional_pin_bits"], 8)
        self.assertAlmostEqual(result["events_per_cycle"], 0.5)
        self.assertAlmostEqual(result["energy_nj_per_delivered_event"], 0.02)
        inventory = read_json_artifact(self.root, self.record["flow"]["inventory"])
        self.assertEqual(inventory["module_graph"][0], {
            "hierarchy_path": "candidate_full_link",
            "module": "candidate_full_link",
            "owner": "candidate",
        })
        self.assertEqual(inventory["top_ownership"], "candidate")
        self.assertEqual(inventory["flatten_policy"], "preserve_candidate_hierarchy")

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
                self.record["flow"][field]
                for field in (
                    "tool_config", "sdc", "library", "post_elaboration_report",
                    "synthesis_command", "hierarchy_source", "inventory",
                    "synthesis_hierarchy_report", "synthesis_evidence",
                    "mapped_netlist", "area_report", "stage_report",
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
        for reference in references:
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
        target = self.root / self.record["activity"]["power_report"]["path"]
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

    def test_base_directory_ancestor_symlink_is_rejected(self):
        linked_base = self.root / "linked-base"
        os.symlink(self.root, linked_base)
        with self.assertRaisesRegex(validator.QualificationError, "ancestor.*symlink"):
            validator.validate_record(self.record, linked_base)

    def test_evidence_role_and_inode_reuse_are_rejected(self):
        record = copy.deepcopy(self.record)
        record["activity"]["common_result"] = copy.deepcopy(
            record["activity"]["power_report"]
        )
        with self.assertRaisesRegex(validator.QualificationError, "evidence-role reuse"):
            self.validate(record)

        record = copy.deepcopy(self.record)
        source = self.root / record["activity"]["power_report"]["path"]
        hardlink = self.root / "activity" / "common-hardlink.json"
        os.link(source, hardlink)
        record["activity"]["common_result"] = {
            "path": "activity/common-hardlink.json",
            "sha256": record["activity"]["power_report"]["sha256"],
        }
        with self.assertRaisesRegex(validator.QualificationError, "inode evidence-role reuse"):
            self.validate(record)

    def test_mutation_during_stable_read_is_rejected(self):
        path = self.root / self.record["activity"]["power_report"]["path"]
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

    def test_synthesis_command_closes_include_generated_ip_and_library(self):
        generated_ip = self.root / "flow" / "generated_ip.sv"
        generated_ip.write_text("module rebound_ip; endmodule\n", encoding="utf-8")
        with self.assertRaisesRegex(validator.QualificationError, "generated_ip.*digest"):
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
        with self.assertRaisesRegex(validator.QualificationError, "charged source hierarchy closure"):
            self.validate(record)

    def test_mapped_hierarchy_must_match_charged_blocks(self):
        record = copy.deepcopy(self.record)
        record["charged_blocks"][0]["hierarchy_path"] = "candidate_full_link.u_free_tx"
        with self.assertRaisesRegex(validator.QualificationError, "trusted inventory block"):
            self.validate(record)

    def test_self_consistent_omitted_serializer_is_rejected_by_trusted_producer(self):
        record = copy.deepcopy(self.record)
        record["charged_blocks"] = [
            block for block in record["charged_blocks"]
            if block["kind"] not in {"serializer", "deserializer"}
        ]
        record["feature_declarations"]["serializer"] = []
        record["feature_declarations"]["deserializer"] = []
        hierarchy = read_json_artifact(self.root, record["flow"]["hierarchy_source"])
        hierarchy["blocks"] = [
            block for block in hierarchy["blocks"]
            if block["kind"] not in {"serializer", "deserializer"}
        ]
        record["flow"]["hierarchy_source"] = json_artifact(
            self.root, "evidence/hierarchy_omits_serializer.json", hierarchy
        )
        inventory = self.rebind_synthesis_input(
            record, "hierarchy_source", "flow/synthesis_omits_serializer.json"
        )
        inventory["blocks"] = [
            block for block in inventory["blocks"]
            if block["kind"] not in {"serializer", "deserializer"}
        ]
        inventory["features"] = [
            feature for feature in inventory["features"]
            if feature["category"] not in {"serializer", "deserializer"}
        ]
        old_inventory_path = record["flow"]["inventory"]["path"]
        inventory["producer"]["command"] = [
            "evidence/inventory_omits_serializer.json"
            if token == old_inventory_path else token
            for token in inventory["producer"]["command"]
        ]
        self.rewrite_json_reference(
            record["flow"], "inventory", "evidence/inventory_omits_serializer.json",
            inventory,
        )
        with self.assertRaisesRegex(validator.QualificationError, "serializer"):
            self.validate(record)

    def test_hidden_netlist_instance_with_clean_inventory_is_rejected(self):
        record = copy.deepcopy(self.record)
        mapped = (self.root / record["flow"]["mapped_netlist"]["path"]).read_text(
            encoding="utf-8"
        )
        mapped = mapped.replace(
            ");\n",
            ");\n  generated_unused_ip u_hidden_fifo();\n",
            1,
        )
        record["flow"]["mapped_netlist"] = artifact(
            self.root, "evidence/mapped_with_hidden_fifo.v", mapped
        )
        inventory = self.rebind_synthesis_input(
            record, "mapped_netlist", "flow/synthesis_hidden_fifo.json"
        )
        old_inventory_path = record["flow"]["inventory"]["path"]
        inventory["producer"]["command"] = [
            "evidence/inventory_clean_hidden_fifo.json"
            if token == old_inventory_path else token
            for token in inventory["producer"]["command"]
        ]
        self.rewrite_json_reference(
            record["flow"], "inventory", "evidence/inventory_clean_hidden_fifo.json",
            inventory,
        )
        with self.assertRaisesRegex(validator.QualificationError, "u_hidden_fifo"):
            self.validate(record)

    def test_inventory_rebound_without_producer_command_rebind_is_rejected(self):
        record = copy.deepcopy(self.record)
        inventory = read_json_artifact(self.root, record["flow"]["inventory"])
        inventory["producer"]["inputs"][0]["sha256"] = "1" * 64
        self.rewrite_json_reference(
            record["flow"], "inventory", "evidence/inventory_rebound.json", inventory,
        )
        with self.assertRaisesRegex(validator.QualificationError, "trusted regenerated"):
            self.validate(record)

    def test_feature_declaration_is_one_to_one_with_charged_hierarchy(self):
        record = copy.deepcopy(self.record)
        record["feature_declarations"]["buffer"] = []
        with self.assertRaisesRegex(validator.QualificationError, "no 1:1 feature declaration"):
            self.validate(record)

        record = copy.deepcopy(self.record)
        record["feature_declarations"]["codec"][0]["hierarchy_path"] = (
            "candidate_full_link.u_wrong_encoder"
        )
        with self.assertRaisesRegex(validator.QualificationError, "hierarchy_path must match"):
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

    def test_activity_power_and_common_identity_fields_are_parsed(self):
        record = copy.deepcopy(self.record)
        record["candidate"]["id"] = "different-candidate"
        with self.assertRaisesRegex(
            validator.QualificationError, "candidate_id does not match candidate"
        ):
            self.validate(record)

        for field, value in (
            ("test_id", "different-test"), ("seed", 99),
            ("measurement_cycles", 101), ("errors", 1),
            ("delivered_events", 51), ("average_power_mw", 2.5),
        ):
            record = copy.deepcopy(self.record)
            record["activity"][field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    validator.QualificationError, rf"activity\.{field}.*parsed"
                ):
                    self.validate(record)

    def test_clock_exaggeration_is_rejected_against_sdc_and_power(self):
        record = copy.deepcopy(self.record)
        record["flow"]["clock_period_ns"] = 2.5
        record["activity"]["clock_period_ns"] = 2.5
        record["activity"]["clock_mhz"] = 400.0
        with self.assertRaisesRegex(
            validator.QualificationError, "parsed SDC clock|trusted inventory clock_period"
        ):
            self.validate(record)

    def test_sdc_rejects_additional_noncanonical_one_ns_clock(self):
        sdc = (
            b"create_clock -name core_clk -period 5.0 [get_ports clk]\n"
            b"create_clock -period 1.0 [get_ports fast_clk]\n"
        )
        with self.assertRaisesRegex(
            validator.evidence_extractor.EvidenceError,
            "exactly one create_clock command",
        ):
            validator.evidence_extractor.parse_sdc_clock(sdc)

    def test_sdc_rejects_generated_clock_token(self):
        sdc = (
            b"create_clock -name core_clk -period 5.0 [get_ports clk]\n"
            b"create_generated_clock -source [get_ports clk] [get_ports link_clk]\n"
        )
        with self.assertRaisesRegex(
            validator.evidence_extractor.EvidenceError,
            "no create_generated_clock command",
        ):
            validator.evidence_extractor.parse_sdc_clock(sdc)

    def test_one_bit_native_and_link_pin_shrink_are_rejected(self):
        record = copy.deepcopy(self.record)
        record["physical_boundary"]["native_boundary_pins"][0]["width"] = 1
        record["physical_boundary"]["native_functional_pin_bits"] = 22
        with self.assertRaisesRegex(
            validator.QualificationError, "mapped top-port inventory"
        ):
            self.validate(record)

        record = copy.deepcopy(self.record)
        record["physical_boundary"]["link_cut"]["pins"][2]["width"] = 1
        record["physical_boundary"]["link_cut"]["functional_pin_bits"] = 3
        with self.assertRaisesRegex(
            validator.QualificationError, "mapped link-cut pin inventory"
        ):
            self.validate(record)

    def test_rehashed_arbitrary_raw_summary_is_rejected(self):
        record = copy.deepcopy(self.record)
        report = read_json_artifact(self.root, record["flow"]["area_report"])
        old_raw = report["producer"]["inputs"][0]
        arbitrary = artifact(
            self.root, "evidence/area_arbitrary.raw", "verified success\n"
        )
        flow_ref = report["producer"]["inputs"][1]
        flow_manifest = read_json_artifact(self.root, flow_ref)
        flow_manifest["outputs"][0] = {"role": "raw_report", **arbitrary}
        flow_manifest["command"] = [
            arbitrary["path"] if token == old_raw["path"] else token
            for token in flow_manifest["command"]
        ]
        new_flow = json_artifact(
            self.root, "evidence/area_arbitrary.flow.json", flow_manifest
        )
        report["producer"]["inputs"][0] = {"role": "raw_report", **arbitrary}
        report["producer"]["inputs"][1] = {"role": "flow_manifest", **new_flow}
        report["producer"]["command"] = [
            arbitrary["path"] if token == old_raw["path"]
            else new_flow["path"] if token == flow_ref["path"] else token
            for token in report["producer"]["command"]
        ]
        self.rewrite_json_reference(
            record["flow"], "area_report", "evidence/area_arbitrary.json", report
        )
        with self.assertRaisesRegex(
            validator.QualificationError, "trusted raw-report extraction failed"
        ):
            self.validate(record)

    def test_raw_flow_manifest_requires_tool_version_success_and_output_hashes(self):
        record = copy.deepcopy(self.record)
        report = read_json_artifact(self.root, record["flow"]["area_report"])
        flow_ref = report["producer"]["inputs"][1]
        manifest = read_json_artifact(self.root, flow_ref)
        manifest["status"] = "failed"
        manifest["exit_code"] = 9
        manifest["tool"]["version"] = ""
        manifest["outputs"][0]["sha256"] = "0" * 64
        new_flow = json_artifact(
            self.root, "evidence/area_invalid_flow.json", manifest
        )
        report["producer"]["inputs"][1] = {"role": "flow_manifest", **new_flow}
        report["producer"]["command"] = [
            new_flow["path"] if token == flow_ref["path"] else token
            for token in report["producer"]["command"]
        ]
        self.rewrite_json_reference(
            record["flow"], "area_report", "evidence/area_invalid_flow_report.json",
            report,
        )
        with self.assertRaisesRegex(
            validator.QualificationError,
            "tool.version must be nonempty|exit_code must equal 0|status must equal success|outputs do not bind",
        ):
            self.validate(record)

    def test_self_described_nonexistent_flow_is_not_approved(self):
        record = copy.deepcopy(self.record)
        report = read_json_artifact(self.root, record["flow"]["area_report"])
        flow_ref = report["producer"]["inputs"][1]
        manifest = read_json_artifact(self.root, flow_ref)
        manifest["flow_id"] = "trusted-test-tool-v1"
        manifest["tool"] = {"name": "trusted-test-tool", "version": "1.0"}
        manifest["flow_script"] = {
            "path": "trusted-test-tool",
            "sha256": "0" * 64,
        }
        manifest["command"][0] = "trusted-test-tool"
        new_flow = json_artifact(
            self.root, "evidence/area_self_described_flow.json", manifest
        )
        report["producer"]["inputs"][1] = {"role": "flow_manifest", **new_flow}
        report["producer"]["command"] = [
            new_flow["path"] if token == flow_ref["path"] else token
            for token in report["producer"]["command"]
        ]
        self.rewrite_json_reference(
            record["flow"], "area_report",
            "evidence/area_self_described_flow_report.json", report,
        )
        with self.assertRaisesRegex(
            validator.QualificationError,
            "flow_id is not in the approved execution registry",
        ):
            self.validate(record)

    def test_approved_flow_identity_fields_require_exact_registry_match(self):
        cases = (
            ("tool", lambda item: item["tool"].update(version="1.0-fake"),
             "tool does not exactly match approved registry"),
            ("script", lambda item: item["flow_script"].update(sha256="f" * 64),
             "flow_script does not exactly match approved registry"),
            ("command0", lambda item: item["command"].__setitem__(0, "other-tool"),
             r"command\[0\] does not exactly match approved registry"),
        )
        for name, mutate, expected in cases:
            record = copy.deepcopy(self.record)
            report = read_json_artifact(self.root, record["flow"]["area_report"])
            flow_ref = report["producer"]["inputs"][1]
            manifest = read_json_artifact(self.root, flow_ref)
            mutate(manifest)
            new_flow = json_artifact(
                self.root, f"evidence/area_registry_{name}.json", manifest
            )
            report["producer"]["inputs"][1] = {"role": "flow_manifest", **new_flow}
            report["producer"]["command"] = [
                new_flow["path"] if token == flow_ref["path"] else token
                for token in report["producer"]["command"]
            ]
            self.rewrite_json_reference(
                record["flow"], "area_report",
                f"evidence/area_registry_{name}_report.json", report,
            )
            with self.subTest(name=name):
                with self.assertRaisesRegex(validator.QualificationError, expected):
                    self.validate(record)

    def test_wrapper_hidden_feature_is_rejected_by_full_module_graph(self):
        record = copy.deepcopy(self.record)
        mapped = (self.root / record["flow"]["mapped_netlist"]["path"]).read_text(
            encoding="utf-8"
        )
        mapped = mapped.replace(
            ");\n", ");\n  candidate_serializer u_wrapper_hidden_serializer();\n", 1
        )
        record["flow"]["mapped_netlist"] = artifact(
            self.root, "evidence/mapped_wrapper_hidden_serializer.v", mapped
        )
        inventory = self.rebind_synthesis_input(
            record, "mapped_netlist", "flow/synthesis_wrapper_hidden.json"
        )
        old_inventory_path = record["flow"]["inventory"]["path"]
        inventory["producer"]["command"] = [
            "evidence/inventory_wrapper_hidden.json"
            if token == old_inventory_path else token
            for token in inventory["producer"]["command"]
        ]
        self.rewrite_json_reference(
            record["flow"], "inventory", "evidence/inventory_wrapper_hidden.json",
            inventory,
        )
        with self.assertRaisesRegex(
            validator.QualificationError, "u_wrapper_hidden_serializer"
        ):
            self.validate(record)

    def test_rehashed_activity_hierarchy_coverage_window_is_rejected(self):
        record = copy.deepcopy(self.record)
        report = read_json_artifact(self.root, record["activity"]["activity_artifact"])
        report["values"]["hierarchy_root"] = "testbench"
        report["values"]["coverage_percent"] = 100.0
        report["values"]["window_start_cycle"] = 0
        report["values"]["window_end_cycle_exclusive"] = 100
        self.rewrite_json_reference(
            record["activity"], "activity_artifact",
            "activity/activity_rehashed.json", report,
        )
        record["activity"].update({
            "hierarchy_root": "testbench", "coverage_percent": 100.0,
            "window_start_cycle": 0, "window_end_cycle_exclusive": 100,
        })
        with self.assertRaisesRegex(
            validator.QualificationError, "trusted regenerated canonical evidence"
        ):
            self.validate(record)

    def test_rehashed_canonical_report_number_rebound_is_rejected(self):
        record = copy.deepcopy(self.record)
        report = read_json_artifact(self.root, record["flow"]["area_report"])
        report["values"]["area_um2"] = 1.0
        self.rewrite_json_reference(
            record["flow"], "area_report", "evidence/area_rehashed.json", report
        )
        record["flow"]["results"]["area_um2"] = 1.0
        with self.assertRaisesRegex(
            validator.QualificationError, "trusted regenerated canonical evidence"
        ):
            self.validate(record)

    def test_rehashed_contradictory_power_evidence_is_rejected(self):
        record = copy.deepcopy(self.record)
        report = read_json_artifact(self.root, record["activity"]["power_report"])
        report["values"]["average_power_mw"] = 0.25
        self.rewrite_json_reference(
            record["activity"], "power_report", "activity/power_rehashed.json", report
        )
        record["activity"]["average_power_mw"] = 0.25
        with self.assertRaisesRegex(
            validator.QualificationError, "trusted regenerated canonical evidence"
        ):
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

    def test_flow_owned_producer_clis_use_file_based_arguments(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            inventory_args = [
                "--bundle-inventory", self.record["candidate"]["bundle_inventory"]["path"],
                "--filelist", self.record["candidate"]["filelist"]["path"],
                "--mapped-netlist", self.record["flow"]["mapped_netlist"]["path"],
                "--hierarchy-source", self.record["flow"]["hierarchy_source"]["path"],
                "--synthesis-command", self.record["flow"]["synthesis_command"]["path"],
                "--output", "evidence/inventory_cli.json",
            ]
            self.assertEqual(validator.inventory_generator.main(inventory_args), 0)

            area = read_json_artifact(self.root, self.record["flow"]["area_report"])
            raw = area["producer"]["inputs"][0]
            flow_manifest = area["producer"]["inputs"][1]
            extractor_args = [
                "--type", "area", "--raw-report", raw["path"],
                "--flow-manifest", flow_manifest["path"],
            ]
            for binding in area["producer"]["inputs"][2:]:
                extractor_args.extend([
                    "--bind", binding["role"], binding["path"], binding["sha256"]
                ])
            extractor_args.extend(["--output", "evidence/area_cli.json"])
            self.assertEqual(validator.evidence_extractor.main(extractor_args), 0)
        finally:
            os.chdir(previous)

    def test_input_is_not_mutated(self):
        original = copy.deepcopy(self.record)
        self.validate()
        self.assertEqual(self.record, original)


if __name__ == "__main__":
    unittest.main()
