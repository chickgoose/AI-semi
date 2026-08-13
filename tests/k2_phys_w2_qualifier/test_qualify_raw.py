from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "physical/k2_w2_qualifier/qualify_raw.py"
SPEC = importlib.util.spec_from_file_location("k2_w2_qualify_raw", PARSER)
assert SPEC and SPEC.loader
QUALIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFIER)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Bundle:
    def __init__(self, root: Path):
        self.root = root
        self.run_id = "w2-fixture-0001"
        self.top = "candidate_top"
        self.refs: dict[str, dict[str, str]] = {}
        self.sources: list[dict[str, str]] = []
        self.tools: dict[str, dict] = {}

    def file(self, relative: str, content: str | bytes, executable: bool = False) -> dict[str, str]:
        data = content.encode() if isinstance(content, str) else content
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable:
            path.chmod(0o755)
        return {"path": relative, "sha256": digest(data)}

    def artifact(self, role: str, content: str | bytes) -> None:
        self.refs[role] = self.file(f"artifacts/{role}.txt", content)

    @staticmethod
    def coverage(stage: str) -> str:
        return "".join(f"W2_COVERAGE stage={stage} class={kind} count=0\n" for kind in
                       sorted(QUALIFIER.COVERAGE_CLASSES))

    @staticmethod
    def timing(stage: str) -> str:
        return "".join(f"W2_TIMING stage={stage} check={check} paths=5 violations=0 wns=0.125 tns=0.000\n"
                       for check in sorted(QUALIFIER.TIMING_CHECKS))

    @staticmethod
    def scan(stage: str) -> str:
        return (f"W2_SCAN_ICG stage={stage} scan_cells=0 scan_chains=0 dangling_scan_pins=0 "
                "recognized_icg=1 unrecognized_icg=0\n"
                f"W2_ICG stage={stage} cell=ICG_X1 count=1\n")

    def build(self) -> Path:
        genus_clean = f"W2_GENUS_CLEAN_END run_id={self.run_id} top={self.top}\n"
        innovus_clean = f"W2_INNOVUS_CLEAN_END run_id={self.run_id} top={self.top}\n"
        source = self.file("rtl/candidate.sv", f"module {self.top}; endmodule\n")
        self.sources = [source]
        genus_exe = self.file("tools/genus", "#!/bin/sh\nexit 0\n", executable=True)
        innovus_exe = self.file("tools/innovus", "#!/bin/sh\nexit 0\n", executable=True)
        self.tools = {
            "genus": {"version": "23.14-s090_1", "executable": genus_exe},
            "innovus": {"version": "23.14-s088_1", "executable": innovus_exe},
        }
        fixed = {
            "rtl_filelist": "rtl/candidate.sv\n", "sdc": "create_clock -period 5 clk\n",
            "genus_tcl": "puts genus\n", "innovus_tcl": "puts innovus\n",
            "mmmc_tcl": "puts mmmc\n", "liberty": "library(test) {}\n",
            "tech_lef": "VERSION 5.8 ;\n", "macro_lef": "VERSION 5.8 ;\n",
            "qrc": "qrc fixture\n", "genus_log": "Genus fixture\n" + genus_clean,
            "genus_check_design": "W2_DESIGN stage=genus unresolved=0 blackboxes=0 unmapped=0 mapped_instances=25\n",
            "genus_check_timing": self.coverage("genus"),
            "genus_timing": self.timing("genus"),
            "genus_mapped_netlist": f"module {self.top}; ICG_X1 u_icg(); endmodule\n",
            "genus_scan_icg": self.scan("genus"),
            "mapped_smoke": "W2_MAPPED_SMOKE status=PASS vectors=64 accepted=32 retired=32 mismatches=0 unknowns=0\n",
            "genus_clean": genus_clean, "innovus_log": "Innovus fixture\n" + innovus_clean,
            "innovus_check_timing": self.coverage("innovus"),
            "innovus_timing": self.timing("innovus"),
            "innovus_placement": "W2_PLACEMENT placed_instances=31 unplaced_instances=0 unplaced_ports=0 violations=0\n",
            "innovus_scan_icg": self.scan("innovus"),
            "innovus_drc": "W2_DRC violations=0\n",
            "innovus_connectivity": "W2_CONNECTIVITY opens=0 shorts=0 unconnected=0 violations=0\n",
            "innovus_antenna": "W2_ANTENNA violations=0\n",
            "innovus_postroute_netlist": f"module {self.top}; ICG_X1 u_icg(); endmodule\n",
            "innovus_clean": innovus_clean,
        }
        for role, content in fixed.items():
            self.artifact(role, content)
        commands = {
            "genus": {
                "argv": [str((self.root / genus_exe["path"]).resolve()), "-batch", "-files",
                         self.refs["genus_tcl"]["path"], "-log", self.refs["genus_log"]["path"]],
                "environment": {
                    "W2_TOP": self.top, "W2_RUN_ID": self.run_id,
                    "W2_RTL_FILELIST": self.refs["rtl_filelist"]["path"],
                    "W2_SDC": self.refs["sdc"]["path"],
                    "W2_LIB": self.refs["liberty"]["path"],
                },
            },
            "innovus": {
                "argv": [str((self.root / innovus_exe["path"]).resolve()), "-no_gui", "-files",
                         self.refs["innovus_tcl"]["path"], "-log", self.refs["innovus_log"]["path"]],
                "environment": {
                    "W2_TOP": self.top, "W2_RUN_ID": self.run_id,
                    "W2_MAPPED_NETLIST": self.refs["genus_mapped_netlist"]["path"],
                    "W2_MMMC": self.refs["mmmc_tcl"]["path"],
                    "W2_TECH_LEF": self.refs["tech_lef"]["path"],
                    "W2_MACRO_LEF": self.refs["macro_lef"]["path"],
                    "W2_QRC": self.refs["qrc"]["path"],
                },
            },
        }
        manifest = {
            "schema": QUALIFIER.SCHEMA, "run_id": self.run_id,
            "candidate": {"commit": "1" * 40, "top": self.top},
            "tools": self.tools, "commands": commands,
            "tool_exit": {"genus": 0, "innovus": 0},
            "expected_icg_cells": {"ICG_X1": 1},
            "sources": self.sources, "artifacts": self.refs,
        }
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return path


class QualifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="k2-phys-w2-")
        self.root = Path(self.temp.name)
        self.bundle = Bundle(self.root)
        self.manifest_path = self.bundle.build()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, value: dict) -> None:
        self.manifest_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    def mutate_artifact(self, role: str, transform) -> None:
        document = self.manifest()
        path = self.root / document["artifacts"][role]["path"]
        content = transform(path.read_text())
        path.write_text(content)
        document["artifacts"][role]["sha256"] = digest(content.encode())
        self.write_manifest(document)

    def reject(self, fragment: str) -> None:
        with self.assertRaisesRegex(QUALIFIER.QualificationError, fragment):
            QUALIFIER.qualify(self.root, self.manifest_path)

    def test_valid_bundle_produces_bounded_receipt(self) -> None:
        result = QUALIFIER.qualify(self.root, self.manifest_path)
        self.assertEqual(result["status"], "RAW_PHYSICAL_GATES_PASS_POWER_HOLD")
        self.assertEqual(result["claim_boundary"]["raw_genus_innovus_report_qualification"], "GO")
        self.assertEqual(result["claim_boundary"]["activity_annotated_power_and_energy"], "HOLD_NOT_IN_W2")

    def test_receipt_is_byte_reproducible(self) -> None:
        first = QUALIFIER.canonical(QUALIFIER.qualify(self.root, self.manifest_path))
        second = QUALIFIER.canonical(QUALIFIER.qualify(self.root, self.manifest_path))
        self.assertEqual(first, second)

    def test_changed_and_missing_artifact_fail(self) -> None:
        path = self.root / self.manifest()["artifacts"]["sdc"]["path"]
        path.write_text("changed\n")
        self.reject("SHA-256 mismatch")
        path.unlink()
        self.reject("missing artifact")

    def test_symlink_artifact_fails(self) -> None:
        document = self.manifest()
        path = self.root / document["artifacts"]["sdc"]["path"]
        target = self.root / "replacement.sdc"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        self.reject("regular non-symlink")

    def test_provenance_source_filelist_and_top_fail_closed(self) -> None:
        document = self.manifest()
        document["candidate"]["commit"] = "short"
        self.write_manifest(document)
        self.reject("candidate identity")
        self.setUp_rebuild()
        self.mutate_artifact("rtl_filelist", lambda _: "rtl/wrong.sv\n")
        self.reject("ordered source closure")
        self.setUp_rebuild()
        source_path = self.root / self.manifest()["sources"][0]["path"]
        source_path.write_text("module wrong_top; endmodule\n")
        document = self.manifest()
        document["sources"][0]["sha256"] = digest(source_path.read_bytes())
        self.write_manifest(document)
        self.reject("top is absent")

    def setUp_rebuild(self) -> None:
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="k2-phys-w2-")
        self.root = Path(self.temp.name)
        self.bundle = Bundle(self.root)
        self.manifest_path = self.bundle.build()

    def test_tool_version_executable_and_command_are_bound(self) -> None:
        document = self.manifest()
        document["tools"]["genus"]["version"] = "23.14-other"
        self.write_manifest(document)
        self.reject("tool version mismatch")
        self.setUp_rebuild()
        document = self.manifest()
        document["commands"]["innovus"]["argv"].append("-overwrite")
        self.write_manifest(document)
        self.reject("command argv mismatch")
        self.setUp_rebuild()
        tool = self.root / self.manifest()["tools"]["genus"]["executable"]["path"]
        tool.chmod(0o644)
        self.reject("not executable")

    def test_tool_errors_and_nonzero_exit_fail(self) -> None:
        for role, diagnostic in (("genus_log", "Error : injected\n"),
                                 ("innovus_log", "**ERROR: injected\n")):
            with self.subTest(role=role):
                self.setUp_rebuild()
                self.mutate_artifact(role, lambda value, d=diagnostic: d + value)
                self.reject("tool error/fatal")
        self.setUp_rebuild()
        document = self.manifest()
        document["tool_exit"]["innovus"] = 1
        self.write_manifest(document)
        self.reject("nonzero")

    def test_clean_marker_must_be_unique_final_and_run_bound(self) -> None:
        self.mutate_artifact("genus_log", lambda value: value + "late output\n")
        self.reject("not final")
        self.setUp_rebuild()
        self.mutate_artifact("innovus_clean", lambda value: value.replace(self.bundle.run_id, "stale-run-id"))
        self.reject("marker artifact mismatch")

    def test_design_unresolved_blackbox_unmapped_and_empty_mapping_fail(self) -> None:
        for field in ("unresolved", "blackboxes", "unmapped"):
            with self.subTest(field=field):
                self.setUp_rebuild()
                self.mutate_artifact("genus_check_design",
                                     lambda value, f=field: value.replace(f"{f}=0", f"{f}=1"))
                self.reject("unresolved, blackbox, or unmapped")
        self.setUp_rebuild()
        self.mutate_artifact("genus_check_design", lambda value: value.replace("mapped_instances=25", "mapped_instances=0"))
        self.reject("no mapped instances")

    def test_all_timing_checks_require_paths_and_clean_metrics(self) -> None:
        for stage in ("genus", "innovus"):
            role = f"{stage}_timing"
            for field, old, new, diagnostic in (
                    ("paths", "paths=5", "paths=0", "no analyzed paths"),
                    ("violations", "violations=0", "violations=1", "timing gate failed"),
                    ("wns", "wns=0.125", "wns=-0.001", "timing gate failed"),
                    ("tns_negative", "tns=0.000", "tns=-0.001", "timing gate failed"),
                    ("tns_positive", "tns=0.000", "tns=0.001", "timing gate failed")):
                with self.subTest(stage=stage, field=field):
                    self.setUp_rebuild()
                    self.mutate_artifact(role, lambda value, a=old, b=new: value.replace(a, b, 1))
                    self.reject(diagnostic)
            self.setUp_rebuild()
            self.mutate_artifact(role, lambda value: "\n".join(value.splitlines()[1:]) + "\n")
            self.reject("timing check inventory")

    def test_constraint_coverage_inventory_and_each_nonzero_class_fail(self) -> None:
        for stage in ("genus", "innovus"):
            role = f"{stage}_check_timing"
            for kind in sorted(QUALIFIER.COVERAGE_CLASSES):
                with self.subTest(stage=stage, kind=kind):
                    self.setUp_rebuild()
                    self.mutate_artifact(role, lambda value, k=kind:
                                         value.replace(f"class={k} count=0", f"class={k} count=1"))
                    self.reject("nonzero constraint coverage")
            self.setUp_rebuild()
            self.mutate_artifact(role, lambda value: "\n".join(value.splitlines()[1:]) + "\n")
            self.reject("coverage class inventory")

    def test_mapped_smoke_conservation_mismatch_and_unknown_fail(self) -> None:
        for old, new in (("retired=32", "retired=31"), ("mismatches=0", "mismatches=1"),
                         ("unknowns=0", "unknowns=1"), ("vectors=64", "vectors=0")):
            with self.subTest(new=new):
                self.setUp_rebuild()
                self.mutate_artifact("mapped_smoke", lambda value, a=old, b=new: value.replace(a, b))
                self.reject("smoke gate failed")

    def test_scan_and_icg_inventory_fail_at_both_stages(self) -> None:
        for stage in ("genus", "innovus"):
            role = f"{stage}_scan_icg"
            for old, new, diagnostic in (
                    ("scan_cells=0", "scan_cells=1", "scan or unrecognized"),
                    ("unrecognized_icg=0", "unrecognized_icg=1", "scan or unrecognized"),
                    ("cell=ICG_X1 count=1", "cell=ICG_X1 count=2", "ICG inventory mismatch")):
                with self.subTest(stage=stage, new=new):
                    self.setUp_rebuild()
                    self.mutate_artifact(role, lambda value, a=old, b=new: value.replace(a, b))
                    self.reject(diagnostic)

    def test_placement_drc_connectivity_and_antenna_fail(self) -> None:
        cases = (
            ("innovus_placement", "unplaced_instances=0", "unplaced_instances=1", "placement gate"),
            ("innovus_drc", "violations=0", "violations=1", "physical violations"),
            ("innovus_connectivity", "opens=0", "opens=1", "physical violations"),
            ("innovus_connectivity", "shorts=0", "shorts=1", "physical violations"),
            ("innovus_antenna", "violations=0", "violations=1", "physical violations"),
        )
        for role, old, new, diagnostic in cases:
            with self.subTest(role=role, new=new):
                self.setUp_rebuild()
                self.mutate_artifact(role, lambda value, a=old, b=new: value.replace(a, b))
                self.reject(diagnostic)

    def test_partial_or_sentinel_only_bundle_fails(self) -> None:
        document = self.manifest()
        del document["artifacts"]["innovus_timing"]
        self.write_manifest(document)
        self.reject("artifact role inventory")
        self.setUp_rebuild()
        path = self.root / self.manifest()["artifacts"]["innovus_timing"]["path"]
        path.unlink()
        self.reject("missing artifact")

    def test_manifest_outside_bundle_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-phys-w2-outside-") as directory:
            outside = Path(directory) / "manifest.json"
            outside.write_bytes(self.manifest_path.read_bytes())
            with self.assertRaisesRegex(QUALIFIER.QualificationError, "inside the artifact bundle"):
                QUALIFIER.qualify(self.root, outside)

    def test_cli_is_fail_closed_and_output_is_exclusive(self) -> None:
        output = self.root / "receipt.json"
        command = [str(PARSER), "--bundle-root", str(self.root), "--manifest",
                   str(self.manifest_path), "--output", str(output)]
        passed = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("K2_PHYSICAL_W2_PASS", passed.stdout)
        original = output.read_bytes()
        repeated = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("K2_PHYSICAL_W2_HOLD", repeated.stderr)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
