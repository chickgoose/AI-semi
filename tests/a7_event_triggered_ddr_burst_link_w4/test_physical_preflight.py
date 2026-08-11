#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts/physical/a7_w4_physical_preflight.py"
CONTRACT = ROOT / "physical/a7_event_triggered_ddr_burst_link_w4/experiment_contract.json"
TEMPLATE = ROOT / "physical/a7_event_triggered_ddr_burst_link_w4/site_manifest.template.json"
ARTIFACT_NAMES = {
    "genus_check_design", "genus_mapped_netlist", "genus_mapped_sdc",
    "genus_cell_usage", "genus_rise_timing", "genus_fall_timing",
    "genus_recovery_removal", "innovus_route", "innovus_postroute_netlist",
    "innovus_spef", "innovus_setup", "innovus_hold",
    "innovus_recovery_removal", "innovus_pulse_skew", "cdc_report",
    "rdc_report", "power_sparse", "power_saturated"
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PhysicalPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="a7-w4-physical-fixture-")
        self.work = Path(self.temp.name)
        self.contract = json.loads(CONTRACT.read_text())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def file(self, name: str, text: str = "fixture\n") -> dict[str, str]:
        path = self.work / name
        path.write_text(text)
        return {"path": str(path), "sha256": digest(path)}

    def write_json(self, name: str, document: dict) -> Path:
        path = self.work / name
        path.write_text(json.dumps(document, indent=2) + "\n")
        return path

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(PREFLIGHT), *args], cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def valid_site(self) -> tuple[dict, Path]:
        liberty = self.file("corner.lib", """
library (fixture) {
  cell (ICG_X1) { clock_gating_integrated_cell : latch_posedge; }
  cell (ODDR_X1) { area : 1.0; }
  cell (IDDR_X1) { area : 1.0; }
}
""")
        true_path = Path("/bin/true")
        tool = {"executable": str(true_path), "sha256": digest(true_path), "version": "fixture-1"}
        schedule = json.loads((ROOT / self.contract["activity"]["schedule"]).read_text())
        activity = []
        for window in schedule["windows"]:
            activity.append({
                "id": window["id"],
                "activity": self.file(f"{window['id']}.saif"),
                "schedule_sha256": self.contract["activity"]["schedule_sha256"],
                "measurement_start_cycle": schedule["warmup_cycles"],
                "measurement_cycles": schedule["measurement_cycles"],
                "completed_events": window["required_completed_events"],
                "reset_toggles_in_window": 0,
                "scope": self.contract["top"]
            })
        site = {
            "schema_version": 1, "synthetic_fixture": True,
            "contract_sha256": digest(CONTRACT),
            "candidate_commit": self.contract["rtl_commit"],
            "tools": {"genus": tool, "innovus": tool, "cdc_rdc_tool": tool},
            "corner": {
                "name": "fixture_slow_rc", "voltage_v": 0.8, "temperature_c": 125,
                "setup_liberty": liberty, "hold_liberty": liberty,
                "tech_lef": self.file("tech.lef"), "qrc_tech": self.file("qrc.tch"),
                "derates_id": "fixture_ocv_v1"
            },
            "technology_cells": {
                role: {"names": [cell], "evidence": self.file(f"{role}.cells.rpt")}
                for role, cell in (("icg", "ICG_X1"), ("tx_ddr", "ODDR_X1"),
                                   ("rx_ddr", "IDDR_X1"))
            },
            "boundary": {
                field: self.contract["physical_boundary"][field]
                for field in ("id", "per_output_pin_load_pf", "clock_input_transition_ns",
                              "data_input_transition_ns")
            },
            "activity_windows": activity
        }
        return site, self.write_json("site.json", site)

    def valid_receipt(self, site_path: Path) -> tuple[dict, Path]:
        site = json.loads(site_path.read_text())
        timing = {
            "rise_setup_wns_ns": 0.1, "rise_hold_wns_ns": 0.1,
            "fall_setup_wns_ns": 0.1, "fall_hold_wns_ns": 0.1,
            "rise_to_fall_halfcycle_wns_ns": 0.1,
            "fall_to_rise_halfcycle_wns_ns": 0.1,
            "clock_gating_setup_wns_ns": 0.1, "clock_gating_hold_wns_ns": 0.1,
            "recovery_wns_ns": 0.1, "removal_wns_ns": 0.1,
            "unconstrained_paths": 0
        }
        genus = dict(timing)
        genus.update({"unresolved_references": 0, "unmapped_cells": 0,
                      "mapped_roles": {"icg": ["ICG_X1"], "tx_ddr": ["ODDR_X1"],
                                       "rx_ddr": ["IDDR_X1"]}})
        innovus = dict(timing)
        innovus.update({
            "placement_complete": True, "cts_complete": True,
            "detailed_route_complete": True, "extraction_complete": True,
            "minimum_high_pulse_ns": 7.1, "minimum_low_pulse_ns": 7.1,
            "maximum_clock_skew_ns": 0.2, "drc_violations": 0,
            "antenna_violations": 0
        })
        power = []
        site_activity = {row["id"]: row for row in site["activity_windows"]}
        for window_id, events, power_mw in (("sparse_1_of_8", 64, 1.0),
                                            ("saturated_1_per_cycle", 512, 2.0)):
            duration = 8192.0
            power.append({
                "id": window_id, "total_power_mw": power_mw,
                "completed_events": events, "measurement_duration_ns": duration,
                "annotation_coverage_percent": 99.0, "vectorless": False,
                "boundary_id": self.contract["physical_boundary"]["id"],
                "per_output_pin_load_pf": 0.01,
                "clock_tree_included": True, "full_boundary_included": True,
                "activity_sha256": site_activity[window_id]["activity"]["sha256"],
                "energy_pj_per_event": power_mw * duration / events
            })
        receipt = {
            "schema_version": 1, "synthetic_fixture": True,
            "contract_sha256": digest(CONTRACT), "site_manifest_sha256": digest(site_path),
            "boundary_id": self.contract["physical_boundary"]["id"],
            "synthesis_mode": "per_target_resynthesis", "target_period_ns": 16.0,
            "corner_name": site["corner"]["name"],
            "genus": genus, "innovus": innovus,
            "cdc_rdc": {"boundary_classification": "EXPLICIT_ASYNC_OUTPUT_BOUNDARY",
                        "internal_unwaived_cdc": 0, "unwaived_rdc": 0},
            "power_windows": power,
            "artifacts": {name: self.file(f"{name}.rpt") for name in ARTIFACT_NAMES}
        }
        return receipt, self.write_json("receipt.json", receipt)

    def test_contract_only_passes_but_stays_hold(self) -> None:
        result = self.run_cli("--contract-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN", result.stdout)

    def test_unfilled_site_template_fails_closed(self) -> None:
        result = self.run_cli("--site-manifest", str(TEMPLATE))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("A7_W4_PHYSICAL_HOLD", result.stderr)

    def test_complete_synthetic_site_preflight(self) -> None:
        _, site_path = self.valid_site()
        result = self.run_cli("--site-manifest", str(site_path), "--allow-synthetic-fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("A7_W4_SITE_PREFLIGHT_PASS", result.stdout)
        self.assertIn("A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN", result.stdout)

    def test_fixture_flag_cannot_admit_production_labeled_site(self) -> None:
        site, _ = self.valid_site()
        site["synthetic_fixture"] = False
        path = self.write_json("site-production-label-with-fixture-flag.json", site)
        result = self.run_cli("--site-manifest", str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture mode requires site synthetic_fixture=true", result.stderr)

    def test_synthetic_site_requires_fixture_flag(self) -> None:
        _, site_path = self.valid_site()
        result = self.run_cli("--site-manifest", str(site_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production mode requires site synthetic_fixture=false", result.stderr)

    def test_fixture_flag_requires_synthetic_receipt(self) -> None:
        _, site_path = self.valid_site()
        receipt, _ = self.valid_receipt(site_path)
        receipt["synthetic_fixture"] = False
        path = self.write_json("receipt-production-label-with-fixture-flag.json", receipt)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                              str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture mode requires receipt synthetic_fixture=true", result.stderr)

    def test_missing_required_cell_role_fails(self) -> None:
        for role in ("icg", "tx_ddr", "rx_ddr"):
            with self.subTest(role=role):
                site, _ = self.valid_site()
                site["technology_cells"][role]["names"] = []
                path = self.write_json(f"site-no-{role}.json", site)
                result = self.run_cli("--site-manifest", str(path), "--allow-synthetic-fixture")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"technology_cells.{role}.names missing", result.stderr)

    def test_complete_synthetic_receipt(self) -> None:
        _, site_path = self.valid_site()
        _, receipt_path = self.valid_receipt(site_path)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                          str(receipt_path), "--allow-synthetic-fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("A7_W4_SYNTHETIC_RECEIPT_FIXTURE_PASS", result.stdout)
        self.assertIn("A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN", result.stdout)

    def test_negative_halfcycle_slack_fails(self) -> None:
        _, site_path = self.valid_site()
        receipt, _ = self.valid_receipt(site_path)
        receipt["innovus"]["rise_to_fall_halfcycle_wns_ns"] = -0.001
        path = self.write_json("receipt-bad-halfcycle.json", receipt)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                              str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rise_to_fall_halfcycle_wns_ns", result.stderr)

    def test_unwaived_cdc_fails(self) -> None:
        _, site_path = self.valid_site()
        receipt, _ = self.valid_receipt(site_path)
        receipt["cdc_rdc"]["internal_unwaived_cdc"] = 1
        path = self.write_json("receipt-bad-cdc.json", receipt)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                              str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unwaived internal CDC", result.stderr)

    def test_boundary_pin_load_mismatch_fails(self) -> None:
        site, _ = self.valid_site()
        site["boundary"]["per_output_pin_load_pf"] = 0.02
        path = self.write_json("site-bad-load.json", site)
        result = self.run_cli("--site-manifest", str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("boundary.per_output_pin_load_pf", result.stderr)

    def test_negative_recovery_slack_fails(self) -> None:
        _, site_path = self.valid_site()
        receipt, _ = self.valid_receipt(site_path)
        receipt["innovus"]["recovery_wns_ns"] = -0.001
        path = self.write_json("receipt-bad-recovery.json", receipt)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                          str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("innovus.recovery_wns_ns", result.stderr)

    def test_energy_mismatch_fails(self) -> None:
        _, site_path = self.valid_site()
        receipt, _ = self.valid_receipt(site_path)
        receipt["power_windows"][0]["energy_pj_per_event"] = 0.0
        path = self.write_json("receipt-bad-energy.json", receipt)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                          str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("energy/event formula mismatch", result.stderr)

    def test_missing_report_artifact_fails(self) -> None:
        _, site_path = self.valid_site()
        receipt, _ = self.valid_receipt(site_path)
        del receipt["artifacts"]["innovus_hold"]
        path = self.write_json("receipt-missing-report.json", receipt)
        result = self.run_cli("--site-manifest", str(site_path), "--results-receipt",
                          str(path), "--allow-synthetic-fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("receipt artifact set mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
