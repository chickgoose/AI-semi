#!/usr/bin/env python3
"""Fail-closed preflight/receipt validator for the A7 W4 physical experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "physical/a7_event_triggered_ddr_burst_link_w4/experiment_contract.json"
REQUIRED_ARTIFACTS = {
    "genus_check_design", "genus_mapped_netlist", "genus_mapped_sdc",
    "genus_cell_usage", "genus_rise_timing", "genus_fall_timing",
    "genus_recovery_removal", "innovus_route", "innovus_postroute_netlist",
    "innovus_spef", "innovus_setup", "innovus_hold",
    "innovus_recovery_removal", "innovus_pulse_skew", "cdc_report",
    "rdc_report", "power_sparse", "power_saturated"
}


class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.items.append(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, problems: Problems) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        problems.items.append(f"cannot read JSON {path}: {error}")
        return {}
    problems.require(isinstance(document, dict), f"JSON root must be object: {path}")
    return document if isinstance(document, dict) else {}


def validate_artifact(item: Any, label: str, problems: Problems) -> Path | None:
    if not isinstance(item, dict):
        problems.items.append(f"{label}: artifact record missing")
        return None
    raw_path, expected = item.get("path"), item.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        problems.items.append(f"{label}: path missing")
        return None
    path = Path(raw_path)
    problems.require(path.is_file(), f"{label}: file not found: {path}")
    problems.require(isinstance(expected, str) and len(expected) == 64,
                     f"{label}: sha256 missing")
    if path.is_file() and isinstance(expected, str):
        problems.require(sha256(path) == expected, f"{label}: sha256 mismatch")
    return path if path.is_file() else None


def validate_contract(path: Path, problems: Problems) -> dict[str, Any]:
    contract = load_json(path, problems)
    problems.require(contract.get("schema_version") == 1, "contract schema_version must be 1")
    problems.require(contract.get("status_without_server_eda") == "PHYSICAL_HOLD",
                     "contract must remain PHYSICAL_HOLD without server EDA")
    problems.require(contract.get("rtl_commit") == "db3f04fe0e01699e63c596145fe71effc601e57c",
                     "RTL commit is not frozen to db3f04f")
    roles = contract.get("technology_mapping", {}).get("required_roles")
    problems.require(roles == ["icg", "tx_ddr", "rx_ddr"], "technology roles changed")
    for source in contract.get("sources", []):
        source_path = ROOT / str(source.get("path", ""))
        problems.require(source_path.is_file(), f"frozen RTL missing: {source_path}")
        if source_path.is_file():
            problems.require(sha256(source_path) == source.get("sha256"),
                             f"frozen RTL hash mismatch: {source_path}")
    schedule_path = ROOT / contract.get("activity", {}).get("schedule", "")
    problems.require(schedule_path.is_file(), "activity schedule missing")
    if schedule_path.is_file():
        problems.require(sha256(schedule_path) == contract["activity"].get("schedule_sha256"),
                         "activity schedule hash mismatch")
    filelist_path = ROOT / str(contract.get("filelist", ""))
    problems.require(filelist_path.is_file(), "physical RTL filelist missing")
    if filelist_path.is_file():
        problems.require(sha256(filelist_path) == contract.get("filelist_sha256"),
                         "physical RTL filelist hash mismatch")
    sdc_path = ROOT / str(contract.get("sdc", ""))
    problems.require(sdc_path.is_file(), "candidate SDC missing")
    if sdc_path.is_file():
        problems.require(sha256(sdc_path) == contract.get("sdc_sha256"),
                         "candidate SDC hash mismatch")
        sdc = sdc_path.read_text(encoding="utf-8")
        for token in ("create_generated_clock", "set_min_pulse_width -high",
                      "set_min_pulse_width -low", "set_clock_uncertainty"):
            problems.require(token in sdc, f"SDC missing {token}")
        problems.require("set_false_path -from [get_ports rst_n]" not in sdc,
                         "blanket reset false path is prohibited")
    problems.require(contract.get("flow", {}).get("server_eda_executed") is False,
                     "repository contract must not claim server EDA execution")
    return contract


def validate_site(site_path: Path, contract_path: Path, contract: dict[str, Any],
                  allow_fixture: bool, problems: Problems) -> dict[str, Any]:
    site = load_json(site_path, problems)
    problems.require(site.get("schema_version") == 1, "site schema_version must be 1")
    problems.require(site.get("contract_sha256") == sha256(contract_path),
                     "site contract_sha256 mismatch")
    problems.require(site.get("candidate_commit") == contract.get("rtl_commit"),
                     "site candidate commit mismatch")
    if allow_fixture:
        problems.require(site.get("synthetic_fixture") is True,
                         "fixture mode requires site synthetic_fixture=true")
    else:
        problems.require(site.get("synthetic_fixture") is False,
                         "production mode requires site synthetic_fixture=false")
    for name in ("genus", "innovus", "cdc_rdc_tool"):
        tool = site.get("tools", {}).get(name, {})
        executable = Path(str(tool.get("executable", "")))
        problems.require(executable.is_file() and os.access(executable, os.X_OK),
                         f"{name}: executable missing or not executable")
        problems.require(isinstance(tool.get("version"), str) and bool(tool.get("version")),
                         f"{name}: version missing")
        if executable.is_file():
            problems.require(tool.get("sha256") == sha256(executable),
                             f"{name}: executable sha256 mismatch")
    corner = site.get("corner", {})
    for field in ("name", "derates_id"):
        problems.require(isinstance(corner.get(field), str) and bool(corner.get(field)),
                         f"corner.{field} missing")
    for field in ("voltage_v", "temperature_c"):
        problems.require(isinstance(corner.get(field), (int, float)), f"corner.{field} missing")
    corner_paths = {}
    for field in ("setup_liberty", "hold_liberty", "tech_lef", "qrc_tech"):
        corner_paths[field] = validate_artifact(corner.get(field), f"corner.{field}", problems)
    liberty_texts = []
    for field in ("setup_liberty", "hold_liberty"):
        if corner_paths[field]:
            liberty_texts.append(corner_paths[field].read_text(encoding="utf-8", errors="ignore"))
    declared_cells: dict[str, list[str]] = {}
    for role in contract.get("technology_mapping", {}).get("required_roles", []):
        record = site.get("technology_cells", {}).get(role, {})
        names = record.get("names", [])
        problems.require(isinstance(names, list) and names and all(isinstance(x, str) and x for x in names),
                         f"technology_cells.{role}.names missing")
        declared_cells[role] = names if isinstance(names, list) else []
        validate_artifact(record.get("evidence"), f"technology_cells.{role}.evidence", problems)
        for cell_name in declared_cells[role]:
            for corner_index, liberty_text in enumerate(liberty_texts):
                problems.require(re.search(rf"\bcell\s*\(\s*{re.escape(cell_name)}\s*\)", liberty_text) is not None,
                                 f"{role}: cell {cell_name} absent from Liberty index {corner_index}")
    for corner_index, liberty_text in enumerate(liberty_texts):
        problems.require("clock_gating_integrated_cell" in liberty_text,
                         f"Liberty index {corner_index} lacks clock_gating_integrated_cell evidence")
    frozen_boundary = contract.get("physical_boundary", {})
    boundary = site.get("boundary", {})
    for field in ("id", "per_output_pin_load_pf", "clock_input_transition_ns",
                  "data_input_transition_ns"):
        problems.require(boundary.get(field) == frozen_boundary.get(field),
                         f"boundary.{field} differs from contract")
    schedule = load_json(ROOT / contract["activity"]["schedule"], problems)
    expected_windows = {w["id"]: w for w in schedule.get("windows", [])}
    site_windows = {w.get("id"): w for w in site.get("activity_windows", [])
                    if isinstance(w, dict)}
    problems.require(set(site_windows) == set(expected_windows), "activity window set mismatch")
    for window_id, expected in expected_windows.items():
        window = site_windows.get(window_id, {})
        validate_artifact(window.get("activity"), f"activity.{window_id}", problems)
        problems.require(window.get("schedule_sha256") == contract["activity"]["schedule_sha256"],
                         f"activity.{window_id}: schedule hash mismatch")
        problems.require(window.get("measurement_start_cycle") == schedule.get("warmup_cycles"),
                         f"activity.{window_id}: start cycle mismatch")
        problems.require(window.get("measurement_cycles") == schedule.get("measurement_cycles"),
                         f"activity.{window_id}: measurement cycles mismatch")
        problems.require(window.get("completed_events") == expected.get("required_completed_events"),
                         f"activity.{window_id}: completed event count mismatch")
        problems.require(window.get("reset_toggles_in_window") == 0,
                         f"activity.{window_id}: reset toggled in measurement window")
        problems.require(window.get("scope") == contract.get("top"),
                         f"activity.{window_id}: scope mismatch")
    return site


def nonnegative(record: dict[str, Any], fields: tuple[str, ...], prefix: str,
                problems: Problems) -> None:
    for field in fields:
        value = record.get(field)
        problems.require(isinstance(value, (int, float)) and value >= 0,
                         f"{prefix}.{field} must be numeric and >= 0")


def validate_results(receipt_path: Path, site_path: Path, contract_path: Path,
                     contract: dict[str, Any], site: dict[str, Any], allow_fixture: bool,
                     problems: Problems) -> None:
    receipt = load_json(receipt_path, problems)
    problems.require(receipt.get("schema_version") == 1, "receipt schema_version must be 1")
    if allow_fixture:
        problems.require(receipt.get("synthetic_fixture") is True,
                         "fixture mode requires receipt synthetic_fixture=true")
    else:
        problems.require(receipt.get("synthetic_fixture") is False,
                         "production mode requires receipt synthetic_fixture=false")
    problems.require(receipt.get("contract_sha256") == sha256(contract_path),
                     "receipt contract hash mismatch")
    problems.require(receipt.get("site_manifest_sha256") == sha256(site_path),
                     "receipt site manifest hash mismatch")
    problems.require(receipt.get("boundary_id") == contract["physical_boundary"]["id"],
                     "receipt boundary mismatch")
    problems.require(receipt.get("synthesis_mode") == "per_target_resynthesis",
                     "receipt synthesis mode must be per_target_resynthesis")
    problems.require(receipt.get("target_period_ns") == contract["timing"]["reference_period_ns"],
                     "receipt target period mismatch")
    problems.require(receipt.get("corner_name") == site.get("corner", {}).get("name"),
                     "receipt corner name mismatch")
    genus = receipt.get("genus", {})
    problems.require(genus.get("unresolved_references") == 0, "Genus unresolved references nonzero")
    problems.require(genus.get("unmapped_cells") == 0, "Genus unmapped cells nonzero")
    for role in contract["technology_mapping"]["required_roles"]:
        mapped = genus.get("mapped_roles", {}).get(role, [])
        declared = site.get("technology_cells", {}).get(role, {}).get("names", [])
        problems.require(bool(mapped) and set(mapped).issubset(set(declared)),
                         f"Genus mapped role {role} missing or not site-declared")
    nonnegative(genus, ("rise_setup_wns_ns", "rise_hold_wns_ns", "fall_setup_wns_ns",
                         "fall_hold_wns_ns", "rise_to_fall_halfcycle_wns_ns",
                         "fall_to_rise_halfcycle_wns_ns", "clock_gating_setup_wns_ns",
                         "clock_gating_hold_wns_ns", "recovery_wns_ns", "removal_wns_ns"),
                "genus", problems)
    problems.require(genus.get("unconstrained_paths") == 0, "Genus unconstrained paths nonzero")
    innovus = receipt.get("innovus", {})
    for field in ("placement_complete", "cts_complete", "detailed_route_complete", "extraction_complete"):
        problems.require(innovus.get(field) is True, f"Innovus {field} is not true")
    nonnegative(innovus, ("rise_setup_wns_ns", "rise_hold_wns_ns", "fall_setup_wns_ns",
                           "fall_hold_wns_ns", "rise_to_fall_halfcycle_wns_ns",
                           "fall_to_rise_halfcycle_wns_ns", "clock_gating_setup_wns_ns",
                           "clock_gating_hold_wns_ns", "recovery_wns_ns", "removal_wns_ns"),
                "innovus", problems)
    min_high, min_low = contract["timing"]["minimum_high_low_ns"]
    problems.require(isinstance(innovus.get("minimum_high_pulse_ns"), (int, float)) and
                     innovus["minimum_high_pulse_ns"] >= min_high, "post-route high pulse below contract")
    problems.require(isinstance(innovus.get("minimum_low_pulse_ns"), (int, float)) and
                     innovus["minimum_low_pulse_ns"] >= min_low, "post-route low pulse below contract")
    problems.require(isinstance(innovus.get("maximum_clock_skew_ns"), (int, float)) and
                     innovus["maximum_clock_skew_ns"] <= contract["timing"]["maximum_clock_skew_or_uncertainty_ns"],
                     "post-route clock skew exceeds contract")
    for field in ("unconstrained_paths", "drc_violations", "antenna_violations"):
        problems.require(innovus.get(field) == 0, f"Innovus {field} must be zero")
    cdc = receipt.get("cdc_rdc", {})
    problems.require(cdc.get("boundary_classification") == contract["cdc_rdc"]["required_boundary_classification"],
                     "CDC boundary classification mismatch")
    problems.require(cdc.get("internal_unwaived_cdc") == 0, "unwaived internal CDC nonzero")
    problems.require(cdc.get("unwaived_rdc") == 0, "unwaived RDC nonzero")
    schedule = load_json(ROOT / contract["activity"]["schedule"], problems)
    expected = {w["id"]: w for w in schedule["windows"]}
    power = {w.get("id"): w for w in receipt.get("power_windows", []) if isinstance(w, dict)}
    site_power = {w.get("id"): w for w in site.get("activity_windows", []) if isinstance(w, dict)}
    problems.require(set(power) == set(expected), "power window set mismatch")
    duration_ns = schedule["measurement_cycles"] * schedule["clock_period_ns"]
    for window_id, spec in expected.items():
        row = power.get(window_id, {})
        p_mw = row.get("total_power_mw")
        events = row.get("completed_events")
        energy = row.get("energy_pj_per_event")
        problems.require(isinstance(p_mw, (int, float)) and p_mw > 0,
                         f"power.{window_id}: total power must be > 0")
        problems.require(events == spec["required_completed_events"] and events > 0,
                         f"power.{window_id}: completed events mismatch or zero")
        problems.require(row.get("measurement_duration_ns") == duration_ns,
                         f"power.{window_id}: duration mismatch")
        problems.require(isinstance(row.get("annotation_coverage_percent"), (int, float)) and
                         row["annotation_coverage_percent"] >= contract["activity"]["minimum_annotation_coverage_percent"],
                         f"power.{window_id}: activity coverage too low")
        problems.require(row.get("vectorless") is False, f"power.{window_id}: vectorless power cannot qualify")
        problems.require(row.get("boundary_id") == contract["physical_boundary"]["id"],
                         f"power.{window_id}: boundary mismatch")
        problems.require(row.get("per_output_pin_load_pf") == contract["physical_boundary"]["per_output_pin_load_pf"],
                         f"power.{window_id}: pin load mismatch")
        problems.require(row.get("clock_tree_included") is True,
                         f"power.{window_id}: clock tree is not included")
        problems.require(row.get("full_boundary_included") is True,
                         f"power.{window_id}: full boundary is not included")
        problems.require(row.get("activity_sha256") == site_power.get(window_id, {}).get("activity", {}).get("sha256"),
                         f"power.{window_id}: activity hash differs from site preflight")
        if isinstance(p_mw, (int, float)) and isinstance(events, int) and events > 0:
            calculated = p_mw * duration_ns / events
            problems.require(isinstance(energy, (int, float)) and math.isclose(energy, calculated, rel_tol=1e-9),
                             f"power.{window_id}: energy/event formula mismatch")
    artifacts = receipt.get("artifacts", {})
    problems.require(set(artifacts) == REQUIRED_ARTIFACTS, "receipt artifact set mismatch")
    for name in REQUIRED_ARTIFACTS:
        validate_artifact(artifacts.get(name), f"receipt.artifacts.{name}", problems)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--site-manifest", type=Path)
    parser.add_argument("--results-receipt", type=Path)
    parser.add_argument("--allow-synthetic-fixture", action="store_true")
    args = parser.parse_args()
    problems = Problems()
    contract = validate_contract(args.contract, problems)
    site: dict[str, Any] = {}
    if not args.contract_only:
        problems.require(args.site_manifest is not None, "site manifest required unless --contract-only")
        if args.site_manifest:
            site = validate_site(args.site_manifest, args.contract, contract,
                                 args.allow_synthetic_fixture, problems)
    if args.results_receipt:
        problems.require(args.site_manifest is not None, "results receipt requires site manifest")
        if args.site_manifest:
            validate_results(args.results_receipt, args.site_manifest, args.contract,
                             contract, site, args.allow_synthetic_fixture, problems)
    if problems.items:
        for item in problems.items:
            print(f"A7_W4_PHYSICAL_PREFLIGHT_ERROR: {item}", file=sys.stderr)
        print("A7_W4_PHYSICAL_HOLD", file=sys.stderr)
        return 1
    print("A7_W4_PHYSICAL_CONTRACT_PASS")
    if args.contract_only:
        print("A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN")
    elif args.results_receipt:
        if args.allow_synthetic_fixture:
            print("A7_W4_SYNTHETIC_RECEIPT_FIXTURE_PASS")
            print("A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN")
        else:
            print("A7_W4_PHYSICAL_RECEIPT_QUALIFIED")
    else:
        print("A7_W4_SITE_PREFLIGHT_PASS")
        print("A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
