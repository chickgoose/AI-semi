#!/usr/bin/env python3
"""Validate and render the sealed K2 W2 server campaign; never run it locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_CAMPAIGN = HERE / "campaign.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
REQUIRED_INTERFACES = (
    "common_activity_v2", "genus_v2", "mapped_smoke_v2", "mapped_functional_v1",
    "raw_plan_builder_v2",
    "fair_plan_builder_v2", "innovus_plan_v2", "qualifier_v2",
    "postroute_power_v2", "final_receipt_v2",
)


class CampaignError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path) -> bytes:
    if not path.is_absolute():
        raise CampaignError(f"immutable input is not absolute: {path}")
    try:
        before_path = os.lstat(path)
    except OSError as error:
        raise CampaignError(f"cannot stat immutable input {path}: {error}") from error
    if (not stat.S_ISREG(before_path.st_mode) or stat.S_ISLNK(before_path.st_mode)
            or before_path.st_nlink != 1):
        raise CampaignError(f"immutable input is not one regular non-symlink: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CampaignError(f"cannot open immutable input {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        after_path = os.lstat(path)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if len({identity(before_path), identity(before), identity(after),
                identity(after_path)}) != 1:
            raise CampaignError(f"immutable input changed while read: {path}")
        payload = b"".join(chunks)
        if not payload:
            raise CampaignError(f"immutable input is empty: {path}")
        return payload
    finally:
        os.close(descriptor)


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = stable_read(path.resolve(strict=True))
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise CampaignError(f"JSON root is not an object: {path}")
    return value, payload


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CampaignError(f"{label} field set mismatch")
    return value


def bound(row: Any, label: str) -> tuple[Path, str, bytes]:
    exact(row, {"path", "sha256"}, label)
    path = Path(row["path"])
    sha = row["sha256"]
    if not path.is_absolute() or not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise CampaignError(f"{label} path/SHA is not exact")
    payload = stable_read(path)
    if digest(payload) != sha:
        raise CampaignError(f"{label} SHA mismatch")
    return path, sha, payload


def git_bytes(root: Path, commit: str, relative: str, label: str) -> bytes:
    if (not COMMIT.fullmatch(commit) or not isinstance(relative, str) or
            not relative or relative.startswith("/") or ".." in Path(relative).parts):
        raise CampaignError(f"{label} Git identity is unsafe")
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{relative}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise CampaignError(f"{label} is not an exact committed Git blob")
    return result.stdout


def parse_ansi_ports(payload: bytes, top: str) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="strict")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    match = re.search(rf"\bmodule\s+{re.escape(top)}\s*\((.*?)\)\s*;", text,
                      re.DOTALL)
    if match is None:
        raise CampaignError(f"staged top is absent from top_source: {top}")
    pattern = re.compile(
        r"^(input|output)\s+(?:(?:wire|logic|reg)\s+)?"
        r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?"
        r"([A-Za-z_][A-Za-z0-9_$]*)$")
    rows: list[dict[str, Any]] = []
    for raw in match.group(1).split(","):
        declaration = " ".join(raw.split())
        parsed = pattern.fullmatch(declaration)
        if parsed is None:
            raise CampaignError(f"ambiguous staged ANSI port: {declaration}")
        direction, msb, lsb, name = parsed.groups()
        width = 1 if msb is None else abs(int(msb) - int(lsb)) + 1
        rows.append({"direction": direction, "name": name, "width": width})
    if len({row["name"] for row in rows}) != len(rows):
        raise CampaignError("staged top has duplicate ports")
    return rows


def validate_ports(rows: Any, label: str, roles: bool = False) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise CampaignError(f"{label} must be nonempty")
    keys = {"direction", "name", "width"} | ({"role"} if roles else set())
    names: set[str] = set()
    for row in rows:
        exact(row, keys, label)
        if (row["direction"] not in {"input", "output"} or
                not isinstance(row["width"], int) or row["width"] <= 0 or
                not isinstance(row["name"], str) or row["name"] in names):
            raise CampaignError(f"{label} has invalid or duplicate port")
        if roles and row["role"] not in {"clock", "functional"}:
            raise CampaignError(f"{label} role mismatch")
        names.add(row["name"])
    return rows


def validate_clock(contract: Any, link_clock_port: str, label: str) -> None:
    exact(contract, {
        "schema", "primary_inputs", "generated_gated", "related",
        "asynchronous_clock_groups", "false_paths_between_clocks",
        "gating_checks", "min_high_low_pulse_checks",
        "virtual_clocks_count_as_physical",
    }, label)
    if contract["schema"] != "k2_w2_full_link_multiclock_v6":
        raise CampaignError(f"{label} shrank to an unsupported clock schema")
    primary = contract["primary_inputs"]
    if ([row.get("port") for row in primary] != ["ref_clk_i", "sample_clk_i"] or
            [row.get("name") for row in primary] != ["ref_clk", "sample_clk"] or
            any(row.get("period_ns") != "5.0" for row in primary) or
            primary[0].get("waveform_ns") != ["0.0", "2.5"] or
            primary[1].get("waveform_ns") != ["1.25", "3.75"]):
        raise CampaignError(f"{label} primary ref/sample clock contract mismatch")
    generated = contract["generated_gated"]
    if (not isinstance(generated, list) or len(generated) != 1 or
            generated[0].get("port") != link_clock_port or
            generated[0].get("source_port") != "sample_clk_i" or
            generated[0].get("divide_by") != 1 or generated[0].get("gated") is not True):
        raise CampaignError(f"{label} generated/gated link clock mismatch")
    required = {
        "related": True, "asynchronous_clock_groups": False,
        "false_paths_between_clocks": False, "gating_checks": True,
        "min_high_low_pulse_checks": True,
        "virtual_clocks_count_as_physical": False,
    }
    if any(contract[key] is not value for key, value in required.items()):
        raise CampaignError(f"{label} related/gating/pulse contract mismatch")


def validate_campaign(document: dict[str, Any], repo_root: Path) -> list[str]:
    if document.get("schema") != "k2_w2_server_campaign_v2":
        raise CampaignError("campaign schema mismatch")
    policy = document["execution_policy"]
    if (policy.get("package_executes_server_tools") is not False or
            policy.get("server_launch_mode") != "emit_only_never_execute" or
            policy.get("direct_runner_bypass") != "forbidden"):
        raise CampaignError("campaign is not render-only/fail-closed")
    expected_order = [
        "proven_environment", "canonical_stage", "common_activity",
        "genus", "mapped_proof", "innovus", "postroute_power",
        "qualifier", "final_receipt",
    ]
    if policy.get("sealed_stage_order") != expected_order:
        raise CampaignError("sealed campaign order mismatch")
    registry = document["authority"]["fair_top_registry"]
    registry_payload = stable_read((repo_root / registry["path"]).resolve())
    if digest(registry_payload) != registry["sha256"]:
        raise CampaignError("fair-top registry SHA mismatch")
    if document["authority"]["functional_loss_archive"]["usage"] != "loss_only_never_ppa":
        raise CampaignError("functional loss evidence escaped into PPA")
    tech = document["server_environment"]["technology"]
    if tech["setup_qrc"] != tech["hold_qrc"] or tech["second_qrc_required"] is not False:
        raise CampaignError("campaign must disclose one shared typical QRC")
    if tech["setup_liberty"]["path"] == tech["hold_liberty"]["path"]:
        raise CampaignError("setup and hold Liberty views must differ")
    expectation = document["staged_wrapper_expectation"]
    if expectation.get("schema") != "k2_w2_tech_staged_compositions_v1":
        raise CampaignError("alternate technology-staging schema is forbidden")
    shared = expectation.get("shared_consumer_contract", {})
    if (shared.get("required_genus_receipt_schema") !=
            "k2_w2_genus_exact_three_endpoint_receipt_v3" or
            shared.get("required_genus_receipt_status") !=
            "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD"):
        raise CampaignError("campaign invented a duplicate Genus receipt contract")
    common_inputs = validate_ports(expectation["common_inputs"], "common inputs")
    common_outputs = validate_ports(expectation["common_observation"], "common observation")
    if common_inputs != [
            {"direction": "input", "name": "ref_clk_i", "width": 1},
            {"direction": "input", "name": "sample_clk_i", "width": 1},
            {"direction": "input", "name": "rst_n", "width": 1},
            {"direction": "input", "name": "source_pending_i", "width": 16}]:
        raise CampaignError("canonical common input signature mismatch")
    if common_outputs != [
            {"direction": "output", "name": "source_accept_o", "width": 16},
            {"direction": "output", "name": "retire_valid_o", "width": 2},
            {"direction": "output", "name": "retire_addr0_o", "width": 4},
            {"direction": "output", "name": "retire_addr1_o", "width": 4},
            {"direction": "output", "name": "drain_idle_o", "width": 1},
            {"direction": "output", "name": "protocol_error_o", "width": 1}]:
        raise CampaignError("canonical common output signature mismatch")
    if sum(row["width"] for row in common_inputs) != 19:
        raise CampaignError("common input boundary is not 19 bits")
    if sum(row["width"] for row in common_outputs) != 28:
        raise CampaignError("common observation boundary is not 28 bits")
    common_names = {row["name"] for row in common_inputs + common_outputs}
    cut = expectation["link_cut"]
    if (cut.get("tag_name") != "AER_LINK_CUT" or cut.get("tag_value") != "tx_to_rx" or
            cut.get("native_and_link_views") != "parallel_non_additive" or
            cut.get("additive_native_plus_link_total_forbidden") is not True or
            cut.get("identical_external_load_on_every_output") is not True or
            cut.get("mapped_netlist_tag_proof_required") is not True):
        raise CampaignError("AER_LINK_CUT accounting policy mismatch")
    expected_counts = {
        "fovea_a7": (3, 2, 50), "a2_p6": (6, 5, 53),
        "a3_p6": (6, 5, 53),
    }
    expected_cells = {
        "fovea_a7": {"ICG": 1, "MX2": 2, "posedge_DFFRH": 2, "negedge_DFFNS": 5},
        "a2_p6": {"ICG": 1, "MX2": 5, "posedge_DFFRH": 5, "negedge_DFFNS": 12},
        "a3_p6": {"ICG": 1, "MX2": 5, "posedge_DFFRH": 5, "negedge_DFFNS": 12},
    }
    if set(expectation["designs"]) != set(expectation["exact_design_order"]):
        raise CampaignError("staged design set mismatch")
    for key, design in expectation["designs"].items():
        ports = validate_ports(design["link_ports"], f"{key} link ports", roles=True)
        if common_names & {row["name"] for row in ports}:
            raise CampaignError(f"{key} common/link boundary overlaps")
        cut_nets = design["link_cut_nets"]
        if not isinstance(cut_nets, list) or len(cut_nets) != len(ports):
            raise CampaignError(f"{key} cut mapping omitted or duplicated")
        mapped_ports: set[str] = set()
        mapped_nets: set[str] = set()
        for row in cut_nets:
            exact(row, {"cut_net", "physical_port", "width", "role", "tag"},
                  f"{key} cut mapping")
            if (row["cut_net"] in mapped_nets or row["physical_port"] in mapped_ports or
                    row["tag"] != "tx_to_rx"):
                raise CampaignError(f"{key} cut mapping is not bijective")
            port = next((item for item in ports if item["name"] == row["physical_port"]), None)
            if port is None or (port["width"], port["role"]) != (row["width"], row["role"]):
                raise CampaignError(f"{key} cut mapping does not match physical link port")
            mapped_nets.add(row["cut_net"])
            mapped_ports.add(row["physical_port"])
        physical = sum(row["width"] for row in ports)
        functional = sum(row["width"] for row in ports if row["role"] == "functional")
        expected_link, expected_functional, expected_total = expected_counts[key]
        accounting = design["accounting"]
        if (physical, functional) != (expected_link, expected_functional) or accounting != {
                "native_nonlink_physical_bits": 47,
                "native_nonlink_functional_bits": 45,
                "link_physical_bits": expected_link,
                "link_functional_bits": expected_functional,
                "total_physical_bits": expected_total,
                "combine_rule": "disjoint_native_nonlink_plus_link_once"}:
            raise CampaignError(f"{key} native/link exactly-once accounting mismatch")
        clock_port = next(row["name"] for row in ports if row["role"] == "clock")
        validate_clock(design["clock_contract"], clock_port, f"{key} clocks")
        if design.get("endpoint_hierarchy_inventory") != expected_cells[key]:
            raise CampaignError(f"{key} endpoint hierarchy inventory mismatch")
    raw = document["cohorts"]["raw_diagnostic"]
    fair = document["cohorts"]["fair_endpoints"]
    if (raw["ranking_eligible"] is not False or raw["cross_cohort_ranking"] is not False or
            raw["exact_design_order"] != ["fovea_raw", "cluster2_raw"] or
            fair["exact_design_order"] != ["fovea_a7", "a2_p6", "a3_p6"] or
            fair["generic_unequal_debug_wrappers_eligible"] is not False):
        raise CampaignError("campaign cohort separation mismatch")
    activity = document["functional_activity_contract"]
    if (activity.get("schema") != "k2_w2_frozen_common_activity_v1" or
            activity.get("workloads") != ["full50", "capacity22"] or
            activity.get("exact_candidate_order") != ["fovea_a7", "a2_p6", "a3_p6"] or
            activity.get("tb_modification_allowed") is not False or
            activity.get("synthetic_replay_allowed") is not False or
            activity.get("hidden_candidate_queue_allowed") is not False or
            activity.get("activity_formats") != ["VCD", "SAIF"] or
            activity.get("postroute_power_requires_same_saif_receipt") is not True or
            activity.get("raw_diagnostic_activity_used_for_fair_ranking") is not False):
        raise CampaignError("frozen common TB/activity contract mismatch")
    if activity.get("frozen_common_tb") is None:
        blockers_seed = ["frozen common TB manifest absent"]
    else:
        blockers_seed = []
    mapped_functional = document["mapped_functional_contract"]
    if (mapped_functional.get("schema") != "k2_w2_staged_mapped_functional_gate_v1" or
            mapped_functional.get("gate_methods") != ["vendor_functional_simulation", "formal_lec"] or
            mapped_functional.get("exact_candidate_order") != ["fovea_a7", "a2_p6", "a3_p6"] or
            mapped_functional.get("fovea_required_scenarios") !=
            ["held_pending", "conservation", "reset", "drain"] or
            mapped_functional.get("p6_required_scenarios") !=
            ["ordered_pairs", "back_to_back", "reset"] or
            mapped_functional.get("required_observations") !=
            ["exact_accepted", "exact_retired", "exact_order", "protocol_error_zero"] or
            mapped_functional.get("syntax_or_inventory_only_is_pass") is not False):
        raise CampaignError("mapped-functional hard-gate contract mismatch")
    if mapped_functional.get("vendor_model_manifest") is None:
        blockers_seed.append("vendor functional model manifest absent")
    blockers = list(document["known_readiness_blockers"])
    blockers.extend(blockers_seed)
    if tech["hold_liberty"]["sha256"] is None:
        blockers.append("fast hold Liberty SHA is absent")
    for name in REQUIRED_INTERFACES:
        if name not in document["tool_providers"]:
            blockers.append(f"required provider pin absent: {name}")
    if document["report_calibration"]["receipt"] is None:
        blockers.append("native report calibration receipt absent")
    if expectation["manifest"] is None:
        blockers.append("canonical staged v1 manifest absent")
    if expectation["shared_consumer_contract"].get("status") != "READY_SHARED_RECEIPT_CONTRACT":
        blockers.append("Genus/Innovus shared receipt contract unresolved")
    for name, authority in expectation["required_technology_authorities"].items():
        if (not COMMIT.fullmatch(authority.get("repository_commit") or "") or
                not authority.get("manifest_path") or
                not SHA256.fullmatch(authority.get("manifest_sha256") or "")):
            blockers.append(f"{name} committed technology authority absent")
    return sorted(set(blockers))


def validate_environment(row: Any, campaign: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    path, sha, payload = bound(row, "PROVEN_ENVIRONMENT receipt")
    try:
        receipt = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CampaignError(f"invalid PROVEN_ENVIRONMENT receipt: {error}") from error
    unsigned = dict(receipt)
    observed_receipt_sha = unsigned.pop("receipt_sha256", None)
    go = receipt.get("receipt", {})
    required_gates = {
        "source_archives", "tool_executables", "technology_files",
        "library_semantics", "site_and_cell_availability", "rc_policy",
    }
    gates = receipt.get("gates", {})
    if (receipt.get("schema") != campaign["server_environment"]["required_receipt_schema"] or
            receipt.get("qualification_status") != "PROVEN_ENVIRONMENT" or
            receipt.get("campaign_launch_allowed") is not True or
            receipt.get("unresolved_environment_evidence") != [] or
            observed_receipt_sha != digest(canonical(unsigned)) or
            go.get("schema") != "k2_w2_server_env_go_receipt_v1" or
            go.get("decision") != "GO" or go.get("evidence_status") != "PROVEN_SERVER_ENV" or
            not required_gates.issubset(gates)):
        raise CampaignError("environment receipt is not PROVEN_ENVIRONMENT")
    pinned = campaign["server_environment"]
    tools = gates["tool_executables"]
    if tools.get("status") != "PROVEN":
        raise CampaignError("environment tool gate is not PROVEN")
    for name, expected in pinned["tools"].items():
        actual = tools.get("evidence", {}).get(name, {})
        if (actual.get("path") != expected["path"] or
                actual.get("parsed_version") != expected["version"] or
                actual.get("sha256") != expected["sha256"]):
            raise CampaignError(f"environment tool identity mismatch: {name}")
    tech_gate = gates["technology_files"]
    if tech_gate.get("status") != "PROVEN":
        raise CampaignError("environment technology gate is not PROVEN")
    receipt_technology = tech_gate.get("evidence", {})
    if receipt_technology.get("setup_qrc") != receipt_technology.get("hold_qrc"):
        raise CampaignError("PROVEN_ENVIRONMENT invents a second QRC")
    for name, expected in pinned["technology"].items():
        if name in {"second_qrc_required", "rc_disclosure"}:
            continue
        receipt_name = "macro_lef" if name == "cell_lef" else name
        actual = receipt_technology.get(receipt_name)
        if not isinstance(actual, dict) or actual.get("path") != expected["path"]:
            raise CampaignError(f"environment technology path mismatch: {name}")
        if actual.get("sha256") != expected["sha256"]:
            raise CampaignError(f"environment technology SHA mismatch: {name}")
    archive_gate = gates["source_archives"]
    if archive_gate.get("status") != "PROVEN":
        raise CampaignError("environment source-archive gate is not PROVEN")
    archives = archive_gate.get("evidence", {})
    expected_archives = campaign["authority"]
    for actual_name, expected_name in (("raw_core", "raw_server_archive"),
                                       ("buffered_extension", "buffered_server_archive")):
        actual = archives.get(actual_name, {})
        if (actual.get("path") != expected_archives[expected_name]["path"] or
                actual.get("sha256") != expected_archives[expected_name]["sha256"]):
            raise CampaignError(f"environment archive identity mismatch: {expected_name}")
    return path, sha, receipt


def validate_calibration(row: Any, environment_sha: str,
                         campaign: dict[str, Any]) -> tuple[Path, str]:
    path, sha, payload = bound(row, "native-report calibration receipt")
    try:
        receipt = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CampaignError(f"invalid calibration receipt: {error}") from error
    required = {
        "schema", "status", "purpose", "ranking_eligible", "environment_receipt_sha256",
        "innovus", "pnr_tcl_sha256", "verifier_sha256", "native_reports",
        "machine_summary", "check_design_all", "check_design_all_class_inventory",
    }
    exact(receipt, required, "calibration receipt")
    if (receipt["schema"] != "k2_w2_native_report_calibration_v1" or
            receipt["status"] != "PASS" or receipt["purpose"] != "REPORT_FORMAT_CALIBRATION_ONLY" or
            receipt["ranking_eligible"] is not False or
            receipt["environment_receipt_sha256"] != environment_sha):
        raise CampaignError("native-report calibration status/provenance mismatch")
    env_tool = campaign["server_environment"]["tools"]["innovus"]
    if (receipt["innovus"].get("path") != env_tool["path"] or
            receipt["innovus"].get("version") != env_tool["version"] or
            receipt["innovus"].get("sha256") != env_tool["sha256"]):
        raise CampaignError("calibration Innovus identity mismatch")
    providers = campaign["tool_providers"]
    if (receipt["pnr_tcl_sha256"] != providers["innovus_pnr_tcl"]["sha256"] or
            receipt["verifier_sha256"] != providers["innovus_verifier"]["sha256"]):
        raise CampaignError("calibration Tcl/verifier is stale")
    reports = receipt["native_reports"]
    if not isinstance(reports, list) or len(reports) < 10:
        raise CampaignError("calibration native report ledger is incomplete")
    paths: set[str] = set()
    report_hashes: set[str] = set()
    for report in reports:
        exact(report, {"name", "path", "sha256"}, "calibration native report")
        if (report["path"] in paths or not Path(report["path"]).is_absolute() or
                not SHA256.fullmatch(report["sha256"])):
            raise CampaignError("calibration native report ledger is not unique/hash-bound")
        if digest(stable_read(Path(report["path"]))) != report["sha256"]:
            raise CampaignError("calibration native report SHA mismatch")
        paths.add(report["path"])
        report_hashes.add(report["sha256"])
    exact(receipt["machine_summary"], {"path", "sha256", "schema"}, "machine summary")
    if (receipt["machine_summary"]["schema"] != "k2_w2_innovus_machine_summary_v1" or
            not SHA256.fullmatch(receipt["machine_summary"]["sha256"]) or
            digest(stable_read(Path(receipt["machine_summary"]["path"]))) !=
            receipt["machine_summary"]["sha256"]):
        raise CampaignError("calibration machine summary is not bound")
    class_inventory = receipt["check_design_all_class_inventory"]
    if (not isinstance(class_inventory, list) or not class_inventory or
            any(not isinstance(name, str) or not name for name in class_inventory) or
            len(set(class_inventory)) != len(class_inventory) or
            class_inventory != sorted(class_inventory)):
        raise CampaignError("checkDesign -all calibrated class inventory is invalid")
    check = receipt["check_design_all"]
    if set(check) != {"pre_place", "post_route"}:
        raise CampaignError("checkDesign -all stages are incomplete")
    for stage, evidence in check.items():
        exact(evidence, {
            "command_status", "native_report_sha256", "machine_summary_sha256",
            "class_inventory_sha256", "class_counts", "total_nonzero_classes",
        }, f"checkDesign -all {stage}")
        classes = evidence["class_counts"]
        if (evidence["command_status"] != "PASS" or not isinstance(classes, dict) or not classes or
                list(classes) != class_inventory or
                any(not isinstance(value, int) or value != 0 for value in classes.values()) or
                evidence["total_nonzero_classes"] != 0 or
                evidence["class_inventory_sha256"] != digest(canonical(classes)) or
                evidence["native_report_sha256"] not in report_hashes or
                evidence["machine_summary_sha256"] != receipt["machine_summary"]["sha256"]):
            raise CampaignError(f"checkDesign -all {stage} does not gate every error class")
    return path, sha


def validate_staged(row: Any, repository_root: Path, repository_commit: str,
                    campaign: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    exact(row, {"path", "sha256", "git_commit"}, "tech-staged manifest binding")
    git_commit = row["git_commit"]
    path, sha, payload = bound(
        {"path": row["path"], "sha256": row["sha256"]}, "tech-staged manifest")
    try:
        relative_manifest = path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError as error:
        raise CampaignError("tech-staged manifest is outside its repository") from error
    if digest(git_bytes(repository_root, git_commit, relative_manifest,
                        "tech-staged manifest")) != sha:
        raise CampaignError("tech-staged manifest differs from its committed Git blob")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CampaignError(f"invalid staged manifest: {error}") from error
    expectation = campaign["staged_wrapper_expectation"]
    exact(manifest, {
        "schema", "status", "repository_commit",
        "goal_order", "common_ports", "common_inputs", "common_outputs",
        "constraint_templates", "technology_authorities", "designs",
    }, "tech-staged manifest")
    if (manifest.get("schema") != "k2_w2_tech_staged_compositions_v1" or
            expectation.get("schema") != "k2_w2_tech_staged_compositions_v1" or
            manifest.get("status") != "READY_FOR_GENUS_AND_INNOVUS" or
            not COMMIT.fullmatch(manifest.get("repository_commit", "")) or
            manifest.get("goal_order") != expectation["exact_design_order"] or
            list(manifest.get("designs", {})) != expectation["exact_design_order"]):
        raise CampaignError("tech-staged manifest identity/order mismatch")
    if set(manifest["technology_authorities"]) != {"r1", "p6"}:
        raise CampaignError("tech-staged technology authority set mismatch")
    for name, authority in manifest["technology_authorities"].items():
        exact(authority, {"repository_commit", "manifest_path", "manifest_sha256"},
              f"{name} technology authority")
        expected_authority = expectation["required_technology_authorities"][name]
        if (authority["repository_commit"] != expected_authority["repository_commit"] or
                authority["manifest_path"] != expected_authority["manifest_path"] or
                authority["manifest_sha256"] != expected_authority["manifest_sha256"]):
            raise CampaignError(f"{name} technology authority stale/substituted")
        authority_payload = git_bytes(
            repository_root, authority["repository_commit"], authority["manifest_path"],
            f"{name} technology authority")
        if digest(authority_payload) != authority["manifest_sha256"]:
            raise CampaignError(f"{name} technology authority blob mismatch")
    common = expectation["common_inputs"] + expectation["common_observation"]
    if (manifest["common_ports"] != common or
            manifest["common_inputs"] != expectation["common_inputs"] or
            manifest["common_outputs"] != expectation["common_observation"]):
        raise CampaignError("tech-staged canonical non-link port contract mismatch")
    if set(manifest["constraint_templates"]) != {"r1", "p6"}:
        raise CampaignError("tech-staged constraint-template set mismatch")
    forbidden = set(expectation["forbidden_final_tops"])
    tops: set[str] = set()
    source_commit = manifest["repository_commit"]
    expected_top = {
        "fovea_a7": ("w2_fovea_r1_physical_staging_top", "R1_TECH_STAGED", "R1_DDR"),
        "a2_p6": ("w2_a2_p6_physical_staging_top", "P6_TECH_STAGED", "P6_DDR"),
        "a3_p6": ("w2_a3_p6_physical_staging_top", "P6_TECH_STAGED", "P6_DDR"),
    }
    for key, design in manifest["designs"].items():
        expected = expectation["designs"][key]
        exact(design, {
            "top", "technology_stage", "link_kind", "filelist", "filelist_sha256",
            "sources", "top_source", "defines", "parameters",
            "required_ports", "link_pins", "link_cut_nets", "accounting",
            "clock_contract", "strict_sdc", "endpoint_inventory",
            "attribute_contract",
        }, f"staged {key}")
        top, stage, link_kind = expected_top[key]
        if (design["top"] != top or design["technology_stage"] != stage or
                design["link_kind"] != link_kind or design["top"] in forbidden or
                design["top"] in tops):
            raise CampaignError("generic unequal-debug or reused staged top")
        tops.add(design["top"])
        link_signature = [
            {name: row[name] for name in ("direction", "name", "width")}
            for row in expected["link_ports"]]
        if design["required_ports"] != common:
            raise CampaignError(f"{key} staged exact port closure mismatch")
        if design["link_pins"] != link_signature:
            raise CampaignError(f"{key} staged link-pin closure mismatch")
        for field in ("link_cut_nets", "accounting", "clock_contract"):
            if design[field] != expected[field]:
                raise CampaignError(f"{key} staged {field} mismatch")
        if design["endpoint_inventory"] != expected["endpoint_hierarchy_inventory"]:
            raise CampaignError(f"{key} staged endpoint_inventory mismatch")
        constraint_key = "r1" if key == "fovea_a7" else "p6"
        if design["strict_sdc"] != manifest["constraint_templates"][constraint_key]:
            raise CampaignError(f"{key} staged strict-SDC binding mismatch")
        if design["attribute_contract"] != {
                "tag": "AER_LINK_CUT", "value": "tx_to_rx",
                "direction_attribute": "AER_DIRECTION",
                "role_attribute": "AER_ROLE",
                "mapped_netlist_proof_required": True}:
            raise CampaignError(f"{key} staged AER_LINK_CUT proof contract mismatch")
        filelist_payload = git_bytes(repository_root, source_commit,
                                     design["filelist"], f"{key} staged filelist")
        if digest(filelist_payload) != design["filelist_sha256"]:
            raise CampaignError(f"{key} staged filelist SHA mismatch")
        sources = design["sources"]
        if not isinstance(sources, list) or not sources:
            raise CampaignError(f"{key} staged source closure is empty")
        source_paths = [source.get("path") for source in sources]
        listed = [line.strip() for line in filelist_payload.decode().splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
        if source_paths != listed or len(set(source_paths)) != len(source_paths):
            raise CampaignError(f"{key} staged filelist/source order mismatch")
        if design["top_source"] not in source_paths:
            raise CampaignError(f"{key} staged top source is absent from closure")
        if design["defines"] != ["SYNTHESIS"] or design["parameters"] != {}:
            raise CampaignError(f"{key} staged compile contract mismatch")
        top_payload = b""
        for source in sources:
            exact(source, {"path", "sha256"}, f"{key} staged source")
            source_payload = git_bytes(repository_root, source_commit,
                                       source["path"], f"{key} staged source")
            if digest(source_payload) != source["sha256"]:
                raise CampaignError(f"{key} staged source SHA mismatch")
            if source["path"] == design["top_source"]:
                top_payload = source_payload
        canonical_ports = (expectation["common_inputs"] +
                           [expectation["common_observation"][0]] + link_signature +
                           expectation["common_observation"][1:])
        if parse_ansi_ports(top_payload, design["top"]) != canonical_ports:
            raise CampaignError(f"{key} staged RTL port signature mismatch")
        top_text = top_payload.decode("utf-8")
        for cut_row in expected["link_cut_nets"]:
            window = re.compile(
                rf"AER_LINK_CUT\s*=\s*\"tx_to_rx\".*?"
                rf"AER_DIRECTION\s*=\s*\"output\".*?"
                rf"AER_ROLE\s*=\s*\"{cut_row['role']}\".*?"
                rf"\b{re.escape(cut_row['cut_net'])}\b", re.DOTALL)
            if window.search(top_text) is None:
                raise CampaignError(f"{key} staged source lacks exact AER_LINK_CUT attributes")
    return path, sha, manifest


def validate_common_tb(row: Any, campaign: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    path, sha, payload = bound(row, "frozen common TB manifest")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CampaignError(f"invalid frozen common TB manifest: {error}") from error
    exact(manifest, {
        "schema", "repository_commit", "tb_sources", "vector_bundle", "workloads",
        "candidate_order", "handshake_contract", "reset_contract", "retire_contract",
        "activity_window",
    }, "frozen common TB manifest")
    expected = campaign["functional_activity_contract"]
    if (manifest["schema"] != expected["schema"] or
            not COMMIT.fullmatch(manifest["repository_commit"]) or
            manifest["workloads"] != ["full50", "capacity22"] or
            manifest["candidate_order"] != ["fovea_a7", "a2_p6", "a3_p6"] or
            manifest["activity_window"].get("policy") !=
            "deterministic_identical_cycle_window_bound_to_metrics_receipt"):
        raise CampaignError("frozen common TB workload/activity identity mismatch")
    for field in ("tb_sources",):
        rows = manifest[field]
        if not isinstance(rows, list) or not rows:
            raise CampaignError("frozen common TB source closure is empty")
        for source in rows:
            bound(source, "frozen common TB source")
    bound(manifest["vector_bundle"], "frozen common vector bundle")
    if manifest["handshake_contract"] != {
            "accept_identity": "source_accept_o is the exact same-cycle committed source_pending_i bitmap",
            "source_ready_substitution": "forbidden"}:
        raise CampaignError("frozen common TB handshake contract mismatch")
    if manifest["reset_contract"] != {
            "active_low": True, "sampling": "frozen_tb_edges", "post_nba_resampling": False}:
        raise CampaignError("frozen common TB reset timing contract mismatch")
    if manifest["retire_contract"] != {
            "identity": "retire_valid_o plus retire_addr0_o/retire_addr1_o occurrence identity",
            "post_nba_resampling": False}:
        raise CampaignError("frozen common TB retire identity/timing mismatch")
    return path, sha, manifest


def validate_vendor_models(row: Any, campaign: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    path, sha, payload = bound(row, "vendor functional model manifest")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CampaignError(f"invalid vendor functional model manifest: {error}") from error
    exact(manifest, {"schema", "library_identity", "models", "sdf_policy"},
          "vendor functional model manifest")
    if (manifest["schema"] != "k2_w2_vendor_functional_models_v1" or
            manifest["sdf_policy"] != campaign["mapped_functional_contract"]["sdf_policy"]):
        raise CampaignError("vendor functional model schema/SDF policy mismatch")
    exact(manifest["library_identity"], {"path", "sha256"}, "vendor library identity")
    if not SHA256.fullmatch(manifest["library_identity"]["sha256"]):
        raise CampaignError("vendor library identity SHA absent")
    models = manifest["models"]
    if not isinstance(models, list) or not models:
        raise CampaignError("vendor functional model closure is empty")
    seen: set[str] = set()
    for model in models:
        model_path, _, _ = bound(model, "vendor functional model")
        if str(model_path) in seen:
            raise CampaignError("vendor functional model is duplicated")
        seen.add(str(model_path))
    return path, sha, manifest


def validate_integration(document: dict[str, Any], campaign: dict[str, Any]) -> dict[str, Any]:
    exact(document, {
        "schema", "repository_root", "repository_commit", "environment_receipt",
        "calibration_receipt", "staged_manifest", "frozen_common_tb_manifest",
        "vendor_model_manifest", "flow_interfaces",
    }, "integration receipt")
    if document["schema"] != "k2_w2_campaign_integration_v2":
        raise CampaignError("integration schema mismatch")
    if campaign["staged_wrapper_expectation"]["shared_consumer_contract"].get(
            "status") != "READY_SHARED_RECEIPT_CONTRACT":
        raise CampaignError("Genus/Innovus shared receipt contract is unresolved")
    if not Path(document["repository_root"]).is_absolute() or not COMMIT.fullmatch(
            document["repository_commit"]):
        raise CampaignError("integration repository identity is not exact")
    environment_path, environment_sha, _ = validate_environment(
        document["environment_receipt"], campaign)
    calibration_path, calibration_sha = validate_calibration(
        document["calibration_receipt"], environment_sha, campaign)
    repository_root = Path(document["repository_root"])
    if not repository_root.is_absolute() or not repository_root.is_dir():
        raise CampaignError("integration repository root is unavailable")
    head = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    dirty = subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if (head.returncode or dirty.returncode or head.stdout.strip() != document["repository_commit"] or
            dirty.stdout):
        raise CampaignError("integration repository is not the exact clean source commit")
    staged_path, staged_sha, staged = validate_staged(
        document["staged_manifest"], repository_root,
        document["repository_commit"], campaign)
    common_tb_path, common_tb_sha, common_tb = validate_common_tb(
        document["frozen_common_tb_manifest"], campaign)
    vendor_path, vendor_sha, vendor_models = validate_vendor_models(
        document["vendor_model_manifest"], campaign)
    interfaces = document["flow_interfaces"]
    if set(interfaces) != set(REQUIRED_INTERFACES):
        raise CampaignError("flow interface set mismatch")
    for name in REQUIRED_INTERFACES:
        provider = campaign["tool_providers"].get(name)
        if not isinstance(provider, dict) or not SHA256.fullmatch(provider.get("sha256", "")):
            raise CampaignError(f"required provider is not campaign-pinned: {name}")
        path, sha, _ = bound(interfaces[name], f"flow interface {name}")
        if sha != provider["sha256"] or path.name != Path(provider["path"]).name:
            raise CampaignError(f"flow interface is stale/substituted: {name}")
    return {
        "environment_path": environment_path, "environment_sha": environment_sha,
        "calibration_path": calibration_path, "calibration_sha": calibration_sha,
        "staged_path": staged_path, "staged_sha": staged_sha, "staged": staged,
        "common_tb_path": common_tb_path, "common_tb_sha": common_tb_sha,
        "common_tb": common_tb,
        "vendor_path": vendor_path, "vendor_sha": vendor_sha,
        "vendor_models": vendor_models,
    }


def shell(parts: list[str]) -> str:
    return " ".join(shlex.quote(value) for value in parts)


def render(campaign: dict[str, Any], integration: dict[str, Any], validated: dict[str, Any],
           attempt: Path) -> tuple[list[dict[str, Any]], str]:
    interfaces = integration["flow_interfaces"]
    env_receipt = str(validated["environment_path"])
    calibration = str(validated["calibration_path"])
    staged = str(validated["staged_path"])
    common_tb = str(validated["common_tb_path"])
    vendor_models = str(validated["vendor_path"])
    steps: list[dict[str, Any]] = []
    raw_plan = attempt / "plans/raw-diagnostic.json"
    raw_smoke = attempt / "receipts/raw-mapped-smoke.json"
    raw_build = [
        "python3", interfaces["raw_plan_builder_v2"]["path"],
        "--environment-receipt", env_receipt, "--calibration-receipt", calibration,
        "--archive", campaign["authority"]["raw_server_archive"]["path"],
        "--designs", "fovea_raw,cluster2_raw", "--periods-ns", "1.2,1.0",
        "--mapped-smoke-hook", interfaces["mapped_smoke_v2"]["path"],
        "--smoke-receipt", str(raw_smoke), "--output-root", str(attempt / "pnr/raw"),
        "--output", str(raw_plan),
    ]
    steps.append({"id": "raw_build_and_mapped_smoke", "cohort": "raw_diagnostic",
                  "ranking_eligible": False, "requires": ["sealed_preflight"],
                  "command": raw_build})
    steps.append({"id": "raw_immutable_pnr", "cohort": "raw_diagnostic",
                  "ranking_eligible": False, "requires": ["raw_build_and_mapped_smoke"],
                  "gate": "expected plan SHA plus raw mapped-smoke PASS",
                  "command": ["__RUN_IMMUTABLE_PLAN__", str(raw_plan), "raw"]})
    steps.append({"id": "raw_qualifier", "cohort": "raw_diagnostic",
                  "ranking_eligible": False, "requires": ["raw_immutable_pnr"],
                  "command": [
                      "python3", interfaces["qualifier_v2"]["path"],
                      "--environment-receipt", env_receipt, "--calibration-receipt", calibration,
                      "--cohort", "raw_diagnostic", "--plan", str(raw_plan),
                      "--bundle-root", str(attempt / "pnr/raw"), "--output",
                      str(attempt / "receipts/raw-qualified.json"),
                  ]})
    activity_receipts: dict[str, str] = {}
    for key in campaign["cohorts"]["fair_endpoints"]["exact_design_order"]:
        activity_receipt = attempt / "activity" / key / "receipt.json"
        activity_receipts[key] = str(activity_receipt)
        steps.append({
            "id": f"{key}_common_tb_metrics_activity", "cohort": "fair_endpoints",
            "ranking_eligible": True, "requires": ["sealed_preflight"],
            "gate": "unchanged full50/capacity22 TB; deterministic identical VCD/SAIF window",
            "command": [
                "python3", interfaces["common_activity_v2"]["path"],
                "--frozen-common-tb-manifest", common_tb, "--staged-manifest", staged,
                "--candidate", key, "--workloads", "full50,capacity22",
                "--activity-formats", "VCD,SAIF", "--output-root",
                str(attempt / "activity" / key), "--output", str(activity_receipt),
            ],
        })
    genus_receipts: list[str] = []
    mapped_functional_receipts: list[str] = []
    for key in campaign["cohorts"]["fair_endpoints"]["exact_design_order"]:
        genus_attempt = f"{key}-p5p0"
        receipt = attempt / "genus" / genus_attempt / "receipt.json"
        genus_receipts.append(str(receipt))
        command = [
            "python3", interfaces["genus_v2"]["path"],
            "--repo-root", integration["repository_root"], "--design", key,
            "--genus", campaign["server_environment"]["tools"]["genus"]["path"],
            "--library", campaign["server_environment"]["technology"]["setup_liberty"]["path"],
            "--hold-library", campaign["server_environment"]["technology"]["hold_liberty"]["path"],
            "--cell-lef", campaign["server_environment"]["technology"]["cell_lef"]["path"],
            "--shared-qrc", campaign["server_environment"]["technology"]["setup_qrc"]["path"],
            "--golden-archive", campaign["authority"]["buffered_server_archive"]["path"],
            "--raw-golden-archive", campaign["authority"]["raw_server_archive"]["path"],
            "--functional-loss-archive", campaign["authority"]["functional_loss_archive"]["path"],
            "--output-root", str(attempt / "genus"), "--attempt", genus_attempt,
            "--mapped-smoke-hook", interfaces["mapped_smoke_v2"]["path"],
        ]
        steps.append({"id": f"{key}_genus_v2_mapped_smoke", "cohort": "fair_endpoints",
                      "ranking_eligible": True,
                      "requires": [f"{key}_common_tb_metrics_activity"],
                      "gate": "required shared Genus receipt is emitted only after mapped-smoke PASS",
                      "command": command})
        mapped_receipt = attempt / "receipts" / f"{key}-mapped-functional.json"
        mapped_functional_receipts.append(str(mapped_receipt))
        scenarios = ("held_pending,conservation,reset,drain" if key == "fovea_a7"
                     else "ordered_pairs,back_to_back,reset")
        steps.append({
            "id": f"{key}_mapped_functional", "cohort": "fair_endpoints",
            "ranking_eligible": True, "requires": [f"{key}_genus_v2_mapped_smoke"],
            "gate": "vendor-model simulation with SDF when available or formal LEC; syntax/inventory never sufficient",
            "command": [
                "python3", interfaces["mapped_functional_v1"]["path"],
                "--staged-manifest", staged, "--genus-receipt", str(receipt),
                "--vendor-model-manifest", vendor_models, "--candidate", key,
                "--scenarios", scenarios,
                "--observations", "exact_accepted,exact_retired,exact_order,protocol_error_zero",
                "--sdf-policy", campaign["mapped_functional_contract"]["sdf_policy"],
                "--output-root", str(attempt / "mapped-functional" / key),
                "--output", str(mapped_receipt),
            ],
        })
    fair_plan = attempt / "plans/fair-endpoints.json"
    fair_build = [
        "python3", interfaces["fair_plan_builder_v2"]["path"],
        "--environment-receipt", env_receipt, "--calibration-receipt", calibration,
        "--staged-manifest", staged, "--genus-receipts", ",".join(genus_receipts),
        "--mapped-functional-receipts", ",".join(mapped_functional_receipts),
        "--expected-genus-schema", campaign["staged_wrapper_expectation"][
            "shared_consumer_contract"]["required_genus_receipt_schema"],
        "--require-mapped-link-cut", "tx_to_rx", "--output-root",
        str(attempt / "pnr/fair"), "--output", str(fair_plan),
    ]
    fair_requires = [f"{key}_mapped_functional" for key in
                     campaign["cohorts"]["fair_endpoints"]["exact_design_order"]]
    steps.append({"id": "fair_build_immutable_plan", "cohort": "fair_endpoints",
                  "ranking_eligible": True, "requires": fair_requires,
                  "command": fair_build})
    steps.append({"id": "fair_immutable_pnr", "cohort": "fair_endpoints",
                  "ranking_eligible": True, "requires": ["fair_build_immutable_plan"],
                  "gate": "expected plan SHA, Genus receipt_v2, mapped AER_LINK_CUT, calibration",
                  "command": ["__RUN_IMMUTABLE_PLAN__", str(fair_plan), "fair"]})
    power_receipt = attempt / "receipts/fair-postroute-power.json"
    steps.append({"id": "fair_postroute_activity_power", "cohort": "fair_endpoints",
                  "ranking_eligible": True, "requires": ["fair_immutable_pnr"],
                  "gate": "same per-candidate SAIF receipt and deterministic activity window",
                  "command": [
                      "python3", interfaces["postroute_power_v2"]["path"],
                      "--environment-receipt", env_receipt, "--plan", str(fair_plan),
                      "--activity-receipts", ",".join(activity_receipts.values()),
                      "--bundle-root", str(attempt / "pnr/fair"), "--output",
                      str(power_receipt),
                  ]})
    qualified_receipt = attempt / "receipts/fair-qualified.json"
    steps.append({"id": "fair_qualifier", "cohort": "fair_endpoints",
                  "ranking_eligible": True, "requires": ["fair_immutable_pnr"],
                  "command": [
                      "python3", interfaces["qualifier_v2"]["path"],
                      "--environment-receipt", env_receipt, "--calibration-receipt", calibration,
                      "--staged-manifest", staged, "--cohort", "fair_endpoints",
                      "--plan", str(fair_plan), "--bundle-root", str(attempt / "pnr/fair"),
                      "--power-receipt", str(power_receipt), "--output",
                      str(qualified_receipt),
                  ]})
    steps[-1]["requires"] = ["fair_postroute_activity_power"]
    steps.append({"id": "final_receipt", "cohort": "fair_endpoints",
                  "ranking_eligible": True, "requires": ["fair_qualifier"],
                  "command": [
                      "python3", interfaces["final_receipt_v2"]["path"],
                      "--environment-receipt", env_receipt, "--calibration-receipt", calibration,
                      "--staged-manifest", staged, "--common-tb-manifest", common_tb,
                      "--activity-receipts", ",".join(activity_receipts.values()),
                      "--genus-receipts", ",".join(genus_receipts), "--plan", str(fair_plan),
                      "--mapped-functional-receipts", ",".join(mapped_functional_receipts),
                      "--power-receipt", str(power_receipt), "--qualifier-receipt",
                      str(qualified_receipt), "--output",
                      str(attempt / "receipts/final.json"),
                  ]})
    lines = [
        "#!/usr/bin/env bash", "set -euo pipefail", "umask 077", "set -o noclobber",
        "assert_sha() { test \"$(sha256sum \"$1\" | awk '{print $1}')\" = \"$2\"; }",
        "assert_sealed_plan() { test -s \"$1.sha256\"; read -r expected extra < \"$1.sha256\"; test -z \"${extra:-}\"; test \"${#expected}\" -eq 64; assert_sha \"$1\" \"$expected\"; }",
        "test ! -e " + shlex.quote(str(attempt / "RUN_COMPLETE")),
        "mkdir -p " + " ".join(shlex.quote(str(attempt / name)) for name in
                                  ("plans", "receipts", "genus", "pnr", "activity",
                                   "mapped-functional")),
    ]
    for name in REQUIRED_INTERFACES:
        lines.append("assert_sha " + shlex.quote(interfaces[name]["path"]) + " " +
                     interfaces[name]["sha256"])
    for row in (integration["environment_receipt"], integration["calibration_receipt"],
                integration["staged_manifest"], integration["frozen_common_tb_manifest"]):
        lines.append("assert_sha " + shlex.quote(row["path"]) + " " + row["sha256"])
    lines.append("assert_sha " + shlex.quote(integration["vendor_model_manifest"]["path"]) +
                 " " + integration["vendor_model_manifest"]["sha256"])
    lines.append("test \"$(git -C " + shlex.quote(integration["repository_root"]) +
                 " rev-parse HEAD)\" = " + integration["repository_commit"])
    for step in steps:
        lines.append("# step=" + step["id"] + " requires=" + ",".join(step["requires"]))
        if step["command"][0] == "__RUN_IMMUTABLE_PLAN__":
            plan, cohort = step["command"][1:]
            lines.append("assert_sealed_plan " + shlex.quote(plan))
            lines.append(shell([
                "python3", interfaces["innovus_plan_v2"]["path"], "--plan", plan,
                "--validate-only",
            ]))
            lines.append("assert_sealed_plan " + shlex.quote(plan))
            lines.append(shell([
                "python3", interfaces["innovus_plan_v2"]["path"], "--plan", plan,
                "--execute",
            ]))
            lines.append("assert_sealed_plan " + shlex.quote(plan))
        else:
            lines.append(shell(step["command"]))
    lines.append("( : > " + shlex.quote(str(attempt / "RUN_COMPLETE")) + " )")
    return steps, "\n".join(lines) + "\n"


def write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def emit(args: argparse.Namespace) -> int:
    campaign, campaign_payload = load_json(args.campaign.resolve(strict=True))
    blockers = validate_campaign(campaign, args.repo_root.resolve(strict=True))
    if not ATTEMPT.fullmatch(args.attempt_id):
        raise CampaignError("unsafe attempt ID")
    attempt = args.attempt_root.resolve() / args.attempt_id
    attempt.mkdir(parents=True, exist_ok=False)
    integration_hash = None
    steps: list[dict[str, Any]] = []
    if args.integration is None:
        status = "BLOCKED"
        script = ("#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' " +
                  shlex.quote("K2_W2_CAMPAIGN_BLOCKED " + "; ".join(blockers)) +
                  " >&2\nexit 2\n")
    elif blockers:
        status = "HOLD"
        script = ("#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' " +
                  shlex.quote("K2_W2_CAMPAIGN_HOLD " + "; ".join(blockers)) +
                  " >&2\nexit 2\n")
    else:
        integration, integration_payload = load_json(args.integration.resolve(strict=True))
        validated = validate_integration(integration, campaign)
        integration_hash = digest(integration_payload)
        steps, script = render(campaign, integration, validated, attempt)
        status = "READY_COMMANDS_NOT_EXECUTED"
        blockers = []
    plan = {
        "schema": "k2_w2_campaign_launch_plan_v2", "campaign_id": campaign["campaign_id"],
        "attempt_id": args.attempt_id, "attempt_path": str(attempt), "status": status,
        "server_executed": False, "campaign_sha256": digest(campaign_payload),
        "integration_receipt_sha256": integration_hash, "readiness_blockers": blockers,
        "sealed_stage_order": campaign["execution_policy"]["sealed_stage_order"],
        "shared_rc_disclosure": campaign["server_environment"]["technology"]["rc_disclosure"],
        "accounting_rule": "47 native-nonlink bits plus the disjoint AER_LINK_CUT bits exactly once",
        "steps": steps,
    }
    write_exclusive(attempt / "launch-plan.json", canonical(plan), 0o444)
    write_exclusive(attempt / "commands.sh", script.encode(), 0o555)
    print(f"K2_W2_CAMPAIGN_PLAN status={status} attempt={attempt}")
    return 0 if status.startswith("READY") else 2


def check(args: argparse.Namespace) -> int:
    campaign, _ = load_json(args.campaign.resolve(strict=True))
    blockers = validate_campaign(campaign, args.repo_root.resolve(strict=True))
    if blockers:
        print("K2_W2_CAMPAIGN_HOLD " + "; ".join(blockers), file=sys.stderr)
        return 2
    print("K2_W2_CAMPAIGN_READY blockers=0 server_executed=false")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "emit"):
        sub = modes.add_parser(name)
        sub.add_argument("--campaign", type=Path, default=DEFAULT_CAMPAIGN)
        sub.add_argument("--repo-root", type=Path, default=HERE.parents[1])
        if name == "emit":
            sub.add_argument("--attempt-root", required=True, type=Path)
            sub.add_argument("--attempt-id", required=True)
            sub.add_argument("--integration", type=Path)
    args = parser.parse_args(argv)
    try:
        return check(args) if args.command == "check" else emit(args)
    except (CampaignError, FileExistsError, OSError) as error:
        print(f"K2_W2_CAMPAIGN_REJECTED {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
