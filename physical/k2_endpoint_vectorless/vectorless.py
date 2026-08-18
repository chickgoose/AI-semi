#!/usr/bin/env python3
"""Execute and fail-closed qualify the default-vectorless endpoint cohort.

The evidence manifest is deliberately only an index of native run_genus attempt
directories.  Status strings and hashes supplied by that index are never used
as proof.  Qualification re-reads the producer artifacts in their canonical
locations and re-derives every binding it can.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_PATH = HERE / "contract.json"
GENUS_DIR = ROOT / "physical/k2_w2_genus"
SERVER_PREFLIGHT = ROOT / "physical/k2_w2_server_env/preflight.py"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class VectorlessError(RuntimeError):
    pass


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise VectorlessError(f"cannot load provider: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_genus = _load_module("k2_endpoint_vectorless_genus", GENUS_DIR / "run_genus.py")
server_preflight = _load_module("k2_endpoint_vectorless_env", SERVER_PREFLIGHT)


ATTEMPT_KEYS = {
    "schema", "attempt", "design", "top", "boundary_cohort", "source_origin",
    "ranking_policy", "flow_git_head", "source_commit", "registry_sha256",
    "staged_manifest", "technology_authorities", "timing_cohort_manifest",
    "timing_cohort", "proven_environment", "flow_files_sha256",
    "evidence_cohorts", "filelist_path", "filelist_sha256", "sources",
    "include_files", "include_dirs", "defines", "parameters",
    "constraints_sha256", "library_source_sha256", "library_snapshot_sha256",
    "hold_library_sha256", "cell_lef_sha256", "shared_typical_qrc_sha256",
    "dffnsrx1_setup_preflight", "dffnsrx1_hold_preflight", "strict_sdc",
    "mmmc_template", "genus", "driver_tcl_sha256", "genus_command",
    "clock_gating_insertion", "scan_mapping",
}
RECEIPT_KEYS = {
    "schema", "status", "design", "top", "boundary_cohort", "source_origin",
    "ranking_policy", "attempt_sha256", "staged_manifest",
    "technology_authorities", "timing_cohort_manifest", "timing_cohort",
    "evidence_cohorts", "mapped_inventory", "endpoint_leaf_inventory",
    "mapped_sdf_sha256", "mapped_sdc_sha256", "report_sha256",
    "mapped_functional_gate_sha256", "innovus_handoff_sha256",
    "strict_sdc_sha256", "materialized_sdc_sha256", "dffnsrx1_preflight",
    "checks", "claim_boundary",
}
REPORT_KINDS = ("area", "gtiming", "gpower", "qor", "timing_intent", "clocks")


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_bound_bytes(payload: bytes, expected_sha256: Any, label: str) -> str:
    actual = sha256(payload)
    if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256):
        raise VectorlessError(f"{label} producer SHA is missing or malformed")
    if actual != expected_sha256:
        raise VectorlessError(f"{label} bytes/hash mismatch")
    return actual


def read(path: Path) -> bytes:
    try:
        return run_genus.stable_read(path)
    except (OSError, run_genus.FlowError) as error:
        raise VectorlessError(str(error)) from error


def read_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    payload = read(path)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VectorlessError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise VectorlessError(f"{label} must be an object")
    return payload, value


def write_exclusive(path: Path, payload: bytes) -> None:
    try:
        run_genus.write_exclusive(path, payload)
    except (OSError, run_genus.FlowError) as error:
        raise VectorlessError(str(error)) from error


def exact_keys(value: Any, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise VectorlessError(f"{label} keys mismatch: {observed} != {sorted(keys)}")


def expected_contract() -> dict[str, Any]:
    return read_json(CONTRACT_PATH, "vectorless contract")[1]


def validate_contract(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    exact_keys(contract, {
        "schema", "status", "evidence_class", "candidate_order",
        "comparability_policy", "timing_cohort", "period_ns",
        "input_delay_min_ns", "input_delay_max_ns", "output_load_pf",
        "genus_default_vectorless", "activity_policy", "technology", "tool",
        "reuse",
    }, "vectorless contract")
    if (contract.get("schema") != "k2_endpoint_vectorless_contract_v2" or
            contract.get("status") != "READY_FOR_SERVER_EXECUTION" or
            contract.get("evidence_class") !=
            "GENUS_MAPPED_COMPLETE_ENDPOINT_VECTORLESS" or
            contract.get("candidate_order") != ["fovea_a7", "a2_p6", "a3_p6"] or
            contract.get("comparability_policy") !=
            "EXACT_THREE_TECH_STAGED_COMPLETE_ENDPOINTS_SAME_SETTINGS" or
            contract.get("timing_cohort") != "three_endpoint_6p5ns" or
            contract.get("period_ns") != 6.5 or
            contract.get("input_delay_min_ns") != 0.1 or
            contract.get("input_delay_max_ns") != 0.5 or
            contract.get("output_load_pf") != 0.01 or
            contract.get("genus_default_vectorless") != {
                "primary_input_activity": 0.2,
                "sequential_element_activity": 0.2,
            }):
        raise VectorlessError("frozen default-vectorless cohort/settings mismatch")
    activity = contract.get("activity_policy", {})
    if activity != {
            "waveform_import_allowed": False,
            "vcd_allowed": False,
            "saif_allowed": False,
            "user_activity_file": None,
            "claim_boundary": "VECTORLESS_DIAGNOSTIC_NOT_ACTIVITY_ANNOTATED_POWER",
            }:
        raise VectorlessError("activity/vectorless separation changed")
    technology = contract.get("technology", {})
    exact_keys(technology, {"pdk", "power_liberty_role", "power_corner",
                            "setup_liberty", "hold_liberty", "macro_lef_sha256",
                            "shared_typical_qrc_sha256"}, "technology")
    if (technology.get("pdk") != "GPDK045/gsclib045" or
            technology.get("power_liberty_role") != "setup_liberty" or
            technology.get("power_corner") != {
                "voltage_v": 0.9, "temperature_c": 125.0}):
        raise VectorlessError("GPDK045 vectorless power corner mismatch")
    tool = contract.get("tool", {})
    exact_keys(tool, {"name", "version", "sha256", "observed_path",
                      "resolved_path"}, "tool")
    if (tool.get("name") != "Cadence Genus" or
            tool.get("version") != "23.14-s090_1" or
            tool.get("sha256") !=
            "41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa"):
        raise VectorlessError("Genus tool identity mismatch")
    reuse = contract.get("reuse", {})
    required = {"genus_runner", "base_driver", "candidate_registry",
                "timing_registry", "server_environment_contract",
                "staged_manifest"}
    exact_keys(reuse, required, "reuse")
    for label, identity in reuse.items():
        exact_keys(identity, {"path", "sha256"}, f"reuse.{label}")
        if (not isinstance(identity["path"], str) or
                not SHA256.fullmatch(identity["sha256"])):
            raise VectorlessError(f"invalid reuse.{label} identity")
        path = (root / identity["path"]).resolve(strict=True)
        try:
            path.relative_to(root.resolve(strict=True))
        except ValueError as error:
            raise VectorlessError(f"reuse.{label} escapes repository") from error
        if sha256(read(path)) != identity["sha256"]:
            raise VectorlessError(f"reuse.{label} SHA mismatch")
    server_contract = json.loads(read(
        root / reuse["server_environment_contract"]["path"]))
    try:
        server_preflight.validate_contract(server_contract)
        registry = run_genus.load_registry(root, contract["timing_cohort"])
    except (OSError, ValueError, run_genus.FlowError,
            server_preflight.PreflightError) as error:
        raise VectorlessError(f"provider validation failed: {error}") from error
    server_tech = server_contract["technology"]
    if (technology.get("setup_liberty") != {
            key: server_tech["setup_liberty"][key]
            for key in ("relative_path", "sha256")} or
            technology.get("hold_liberty") != {
            key: server_tech["hold_liberty"][key]
            for key in ("relative_path", "sha256")} or
            technology.get("macro_lef_sha256") != server_tech["macro_lef"]["sha256"] or
            technology.get("shared_typical_qrc_sha256") !=
            server_tech["setup_qrc"]["sha256"] or
            server_tech["setup_qrc"] != server_tech["hold_qrc"]):
        raise VectorlessError("technology settings diverge from server contract")
    selected = registry["selected_timing_cohort"]
    strict = selected["strict_timing_environment"]
    if (registry["goal_order"] != contract["candidate_order"] or
            selected["period_ns"] != contract["period_ns"] or
            strict["W2_INPUT_DELAY_MIN_NS"] != "0.10" or
            strict["W2_INPUT_DELAY_MAX_NS"] != "0.50" or
            strict["W2_OUTPUT_LOAD_PF"] != "0.01"):
        raise VectorlessError("Genus timing/endpoint registry differs from contract")
    return registry


def default_vectorless_driver(root: Path, contract: dict[str, Any]) -> bytes:
    payload = read(root / contract["reuse"]["base_driver"]["path"])
    lowered = payload.lower()
    forbidden = (b"read_vcd", b"read_saif", b"set_switching_activity")
    if any(token in lowered for token in forbidden):
        raise VectorlessError("base driver is not genuine Genus default-vectorless")
    if payload.count(b"report_power  > $OUT_DIR/${DESIGN}_gpower.rpt\n") != 1:
        raise VectorlessError("base driver power-report seam changed")
    return payload


def contract_identity(root: Path, contract: dict[str, Any],
                      registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_sha256": sha256(read(CONTRACT_PATH)),
        "driver_tcl_sha256": sha256(default_vectorless_driver(root, contract)),
        "candidate_registry_sha256": contract["reuse"]["candidate_registry"]["sha256"],
        "staged_manifest": registry["staged_manifest_identity"],
        "timing_cohort_manifest": registry["timing_cohort_manifest_identity"],
        "timing_cohort": registry["selected_timing_cohort"],
        "server_environment_contract_sha256":
            contract["reuse"]["server_environment_contract"]["sha256"],
    }


def preflight(root: Path, output: Path) -> Path:
    contract = expected_contract()
    registry = validate_contract(root, contract)
    result = {
        "schema": "k2_endpoint_vectorless_preflight_v2",
        "status": "HOLD_NO_REAL_SERVER_ARTIFACTS",
        "comparison_ready": False,
        "candidate_go": False,
        "reason": "local preflight validates contracts only; Cadence was not invoked",
        "evidence_class": contract["evidence_class"],
        "candidate_order": contract["candidate_order"],
        "comparability_policy": contract["comparability_policy"],
        "power_method": "GENUS_DEFAULT_VECTORLESS",
        "activity_annotated": False,
        "settings": {
            "technology": contract["technology"],
            "period_ns": contract["period_ns"],
            "input_delay_min_ns": contract["input_delay_min_ns"],
            "input_delay_max_ns": contract["input_delay_max_ns"],
            "output_load_pf": contract["output_load_pf"],
            "genus_default_vectorless": contract["genus_default_vectorless"],
            "activity_policy": contract["activity_policy"],
            "tool": contract["tool"],
        },
        "bindings": contract_identity(root, contract, registry),
    }
    write_exclusive(output, canonical(result))
    return output


def _attempt_directory(evidence_root: Path, relative: Any, label: str) -> Path:
    if (not isinstance(relative, str) or not relative or
            Path(relative).is_absolute() or ".." in Path(relative).parts):
        raise VectorlessError(f"invalid {label} attempt directory")
    base = evidence_root.resolve(strict=True)
    candidate = evidence_root / relative
    current = evidence_root
    for part in Path(relative).parts:
        current = current / part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise VectorlessError(f"{label} attempt path contains symlink")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise VectorlessError(f"{label} attempt directory escapes evidence root") from error
    if not resolved.is_dir():
        raise VectorlessError(f"{label} attempt directory is not a directory")
    return resolved


def _json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VectorlessError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise VectorlessError(f"{label} must be an object")
    return value


def reject_symlinks_in_attempt(attempt_dir: Path, label: str) -> None:
    """Reject symlinks on canonical artifact paths without policing tool scratch."""
    for name in ("bundle", "work", "logs"):
        path = attempt_dir / name
        if stat.S_ISLNK(path.lstat().st_mode):
            raise VectorlessError(f"{label} attempt contains symlink: {path}")
    # All recursively addressed source/model artifacts live below bundle.
    # Do not traverse work/* scratch databases: Cadence/Xcelium may own their
    # internal layout, while every qualified work artifact is a direct child
    # and stable_read rejects a final-component symlink.
    for directory, names, files in os.walk(attempt_dir / "bundle", followlinks=False):
        base = Path(directory)
        for name in [*names, *files]:
            path = base / name
            if stat.S_ISLNK(path.lstat().st_mode):
                raise VectorlessError(f"{label} attempt contains symlink: {path}")


def parse_power_report(payload: bytes, top: str,
                       defaults: dict[str, float]) -> dict[str, float]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise VectorlessError("power report is not UTF-8") from error
    lowered = text.lower()
    if any(token in lowered for token in
           ("read_vcd", "read_saif", ".vcd", ".saif", "activity file: imported")):
        raise VectorlessError("activity/VCD/SAIF report rejected as vectorless evidence")

    def one(pattern: str, label: str) -> str:
        rows = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        if len(rows) != 1:
            raise VectorlessError(f"power report requires exactly one {label} header")
        return rows[0].strip()

    if one(r"^\s*\*\s*Activity File\s*:\s*(.*?)\s*$", "Activity File") != "N.A.":
        raise VectorlessError("power report Activity File must be exactly N.A.")
    if one(r"^\s*\*\s*User-Defined Activity\s*:\s*(.*?)\s*$",
           "User-Defined Activity") != "N.A.":
        raise VectorlessError("power report User-Defined Activity must be exactly N.A.")
    sequential_text = one(
        r"^\s*\*\s*Sequential Element Activity\s*:\s*(\S+)\s*$",
        "Sequential Element Activity")
    primary_text = one(
        r"^\s*\*\s*Primary Input Activity\s*:\s*(\S+)\s*$",
        "Primary Input Activity")
    if sequential_text != "0.200000" or primary_text != "0.200000":
        raise VectorlessError("power report default activity formatting is not native Genus")
    sequential = float(sequential_text)
    primary = float(primary_text)
    if (sequential != defaults["sequential_element_activity"] or
            primary != defaults["primary_input_activity"]):
        raise VectorlessError("power report does not use exact Genus default activity")
    if "Generated by:           Genus(TM) Synthesis Solution" not in text:
        raise VectorlessError("power report lacks native Cadence Genus header")
    if f"Instance: /{top}" not in text and f"* Design: {top}" not in text:
        raise VectorlessError("power report top mismatch")
    if not re.search(r"(?mi)^\s*Power Unit:\s*W\s*$", text):
        raise VectorlessError("power report unit must be W")
    rows = re.findall(
        r"(?mi)^\s*Subtotal\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$", text)
    if len(rows) != 1:
        raise VectorlessError("power report must contain one Subtotal row")
    try:
        leakage_w, internal_w, switching_w, total_w = map(float, rows[0])
    except ValueError as error:
        raise VectorlessError("power report contains a nonnumeric subtotal") from error
    values = (leakage_w, internal_w, switching_w, total_w)
    if any(not math.isfinite(value) or value < 0 for value in values) or total_w <= 0:
        raise VectorlessError("power report contains invalid power")
    if not math.isclose(total_w, leakage_w + internal_w + switching_w,
                        rel_tol=2e-5, abs_tol=5e-10):
        raise VectorlessError("power components do not sum")
    return {
        "leakage_mw": leakage_w * 1000.0,
        "internal_mw": internal_w * 1000.0,
        "switching_mw": switching_w * 1000.0,
        "total_mw": total_w * 1000.0,
    }


def verify_genus_log(payload: bytes, top: str, version: str) -> None:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise VectorlessError("Genus log is not UTF-8") from error
    lowered = text.lower()
    if any(token in lowered for token in
           ("read_vcd", "read_saif", "set_switching_activity", ".vcd", ".saif")):
        raise VectorlessError("Genus log contains non-default activity commands")
    if (f"Version: {version}" not in text or
            f"W2_GENUS_PASS top={top}" not in text or
            "Normal exit." not in text or
            not re.search(r"Info=\d+, Warn=\d+, Error=0, Fatal=0", text)):
        raise VectorlessError("Genus log lacks version/PASS/zero-error/normal-exit evidence")
    if re.search(r"(?mi)^\s*(?:\*\*)?(?:Error|Fatal)\s*[:\[]", text):
        raise VectorlessError("Genus log contains an error/fatal diagnostic")


def validate_environment(payload: bytes, contract: dict[str, Any]) -> dict[str, Any]:
    document = _json_bytes(payload, "server environment receipt")
    exact_keys(document, {
        "schema", "campaign_launch_allowed", "qualification_status", "gates",
        "contract_sha256", "unresolved_environment_evidence", "corner_policy",
        "direct_server_observation", "environment_binding_sha256", "receipt",
        "receipt_sha256",
    }, "server environment receipt")
    exact_keys(document["gates"], {
        "direct_server_observation", "golden_environment", "source_archives",
        "tool_executables", "technology_files", "library_semantics",
        "site_and_cell_availability", "rc_policy",
    }, "server environment gates")
    for name in ("source_archives", "tool_executables", "technology_files",
                 "library_semantics", "site_and_cell_availability", "rc_policy"):
        exact_keys(document["gates"][name], {"status", "evidence"},
                   f"server environment gate {name}")
    exact_keys(document["gates"]["direct_server_observation"],
               {"status", "evidence", "reason"},
               "server environment direct observation")
    exact_keys(document["gates"]["golden_environment"],
               {"status", "evidence", "reason"},
               "server environment golden observation")
    tools = document["gates"]["tool_executables"]["evidence"]
    exact_keys(tools, {"genus", "innovus", "xrun"},
               "server environment tool inventory")
    for name, row in tools.items():
        exact_keys(row, {"path", "resolved_path", "entrypoint_kind", "sha256",
                         "size_bytes", "expected_version", "parsed_version",
                         "version_output", "warnings"},
                   f"server environment tool {name}")
    technology = document["gates"]["technology_files"]["evidence"]
    exact_keys(technology, {"setup_liberty", "hold_liberty", "tech_lef",
                            "macro_lef", "setup_qrc", "hold_qrc"},
               "server environment technology inventory")
    for name, row in technology.items():
        exact_keys(row, {"path", "sha256", "size_bytes"},
                   f"server environment technology {name}")
    server_contract = _json_bytes(read(
        ROOT / contract["reuse"]["server_environment_contract"]["path"]),
        "server environment contract")
    if document["direct_server_observation"] != server_contract[
            "direct_server_observation"]:
        raise VectorlessError("server direct-observation contract contradiction")
    try:
        server_preflight.verify_go_document(
            document, contract["reuse"]["server_environment_contract"]["sha256"])
    except server_preflight.PreflightError as error:
        raise VectorlessError(f"environment receipt is not PROVEN_ENVIRONMENT: {error}") from error
    return document


def _expected_reports(top: str) -> dict[str, str]:
    return {
        "area": f"{top}_area.rpt", "gtiming": f"{top}_gtiming.rpt",
        "gpower": f"{top}_gpower.rpt", "qor": f"{top}_qor.rpt",
        "timing_intent": f"{top}_timing_intent.rpt",
        "clocks": f"{top}_clocks.rpt",
    }


def _verify_static_attempt(attempt_dir: Path, key: str, design: dict[str, Any],
                           registry: dict[str, Any], contract: dict[str, Any],
                           driver_expected: bytes) -> dict[str, Any]:
    attempt_payload, attempt = read_json(attempt_dir / "attempt.json", f"{key} attempt")
    receipt_payload, receipt = read_json(attempt_dir / "receipt.json", f"{key} receipt")
    exact_keys(attempt, ATTEMPT_KEYS, f"{key} attempt")
    exact_keys(receipt, RECEIPT_KEYS, f"{key} receipt")
    top = design["top"]
    if (attempt["schema"] != "k2_w2_genus_exact_three_endpoint_attempt_v3" or
            receipt["schema"] != "k2_w2_genus_exact_three_endpoint_receipt_v3" or
            receipt["status"] != "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD"):
        raise VectorlessError(f"{key} non-Cadence or non-PASS producer receipt")
    reject_symlinks_in_attempt(attempt_dir, key)
    common = {
        "design": key, "top": top, "boundary_cohort": design["boundary_cohort"],
        "source_origin": design["source_origin"],
        "ranking_policy": registry["ranking_policy"],
    }
    if any(attempt[name] != value or receipt[name] != value
           for name, value in common.items()):
        raise VectorlessError(f"{key} attempt/receipt identity contradiction")
    if attempt["attempt"] != attempt_dir.name:
        raise VectorlessError(f"{key} attempt name/directory contradiction")
    expected_checks = {
        "source_and_filelist_hashes": "PASS",
        "authoritative_ganghee_archive": "PASS_EXACT_SHA_AND_ANCHORS",
        "authoritative_raw_ganghee_archive": "PASS_EXACT_SHA_AND_ANCHORS",
        "goal_registry": "PASS_EXACT_THREE_TECH_STAGED_COMPOSITIONS",
        "generic_or_native_top_substitution": "FORBIDDEN",
        "non_link_output_boundary": "PASS_IDENTICAL_EXACT",
        "r1_vs_p6_link_width": "PASS_INHERENT_3_BITS_VS_6_BITS",
        "diagnostic_registries_used_for_final_ranking": "FORBIDDEN",
        "functional_loss_reference": "PASS_NON_OFFICIAL_WORKSPACE_DIFF_LOSS_ONLY",
        "functional_loss_used_for_ppa": "FORBIDDEN",
        "exclusive_attempt_namespace": "PASS",
        "tool_and_library_pre_post_stability": "PASS",
        "unresolved_and_blackbox": "PASS_ZERO",
        "scan_cells": "PASS_ZERO",
        "mapped_netlist_export": "PASS",
        "strict_multiclock_sdc":
            "PASS_HASH_BOUND_RISE_FALL_MIN_MAX_RESET_GATING_PULSE_IO",
        "dffnsrx1_rx_mapping":
            "PASS_EXACT_COUNT_PINS_AND_NONZERO_RECOVERY_REMOVAL",
        "innovus_exact_sdc_consumption": "PENDING_REQUIRES_DOWNSTREAM_RECEIPT",
        "power_activity_gate": "HOLD_VECTORLESS_IS_NOT_ACTIVITY_QUALIFIED",
        "mapped_functional_gate": "PASS_STAGED_VS_MAPPED_SDF_VENDOR_MODELS",
        "report_only_publication":
            "REJECTED_REQUIRES_SOURCE_TOOL_NETLIST_SDC_CONNECTIVITY_FUNCTIONAL_GATE",
    }
    if (attempt["evidence_cohorts"] != receipt["evidence_cohorts"] or
            attempt["source_commit"] != registry["repository_commit"] or
            attempt["registry_sha256"] !=
            sha256(read(ROOT / "physical/k2_w2_genus/designs.json")) or
            receipt["dffnsrx1_preflight"] != {
                "setup": attempt["dffnsrx1_setup_preflight"],
                "hold": attempt["dffnsrx1_hold_preflight"],
            } or receipt["checks"] != expected_checks or
            receipt["claim_boundary"] !=
            "GENUS_MAPPED_TIMING_SCREENING_ONLY_POWER_AND_PHYSICAL_PPA_HOLD"):
        raise VectorlessError(f"{key} producer receipt contains unknown contradictions")
    try:
        expected_flow_files = run_genus.verify_flow_tree(ROOT, registry)
    except run_genus.FlowError as error:
        raise VectorlessError(f"{key} canonical flow closure is not clean: {error}") from error
    if attempt["flow_files_sha256"] != expected_flow_files:
        raise VectorlessError(f"{key} complete runner/RTL flow inventory contradiction")
    if (receipt["attempt_sha256"] != sha256(attempt_payload) or
            attempt["staged_manifest"] != registry["staged_manifest_identity"] or
            receipt["staged_manifest"] != registry["staged_manifest_identity"] or
            attempt["technology_authorities"] != registry["technology_authority_identities"] or
            receipt["technology_authorities"] != registry["technology_authority_identities"] or
            attempt["timing_cohort_manifest"] != registry["timing_cohort_manifest_identity"] or
            receipt["timing_cohort_manifest"] != registry["timing_cohort_manifest_identity"] or
            attempt["timing_cohort"] != registry["selected_timing_cohort"] or
            receipt["timing_cohort"] != registry["selected_timing_cohort"] or
            attempt["strict_sdc"] != design["strict_sdc"] or
            receipt["strict_sdc_sha256"] != design["strict_sdc"]["sha256"] or
            attempt["mmmc_template"] != registry["mmmc_template_identity"] or
            attempt["clock_gating_insertion"] is not True or
            attempt["scan_mapping"] is not False):
        raise VectorlessError(f"{key} staged cohort/technology contradiction")

    driver = read(attempt_dir / "bundle/genus_driver.tcl")
    if (driver != driver_expected or attempt["driver_tcl_sha256"] != sha256(driver) or
            any(token in driver.lower() for token in
                (b"read_vcd", b"read_saif", b"set_switching_activity"))):
        raise VectorlessError(f"{key} driver is not exact default-vectorless Genus")
    sdc = read(attempt_dir / "bundle/constraints.sdc")
    expected_sdc = run_genus.materialize_sdc(
        ROOT, design, registry["selected_timing_cohort"])
    if (sdc != expected_sdc or attempt["constraints_sha256"] != sha256(sdc) or
            receipt["materialized_sdc_sha256"] != sha256(sdc)):
        raise VectorlessError(f"{key} staged SDC/settings mismatch")

    expected_sources = [{"path": row["path"], "sha256": row["sha256"],
                         "origin": design["source_origin"]}
                        for row in design["sources"]]
    expected_includes = [{"path": row["path"], "sha256": row["sha256"],
                          "origin": design["source_origin"]}
                         for row in design["include_files"]]
    if (attempt["filelist_path"] != design["filelist"] or
            attempt["filelist_sha256"] != design["filelist_sha256"] or
            sha256(read(ROOT / design["filelist"])) != design["filelist_sha256"] or
            attempt["sources"] != expected_sources or
            attempt["include_files"] != expected_includes or
            attempt["include_dirs"] != design["include_dirs"] or
            attempt["defines"] != design["defines"] or
            attempt["parameters"] != design["parameters"]):
        raise VectorlessError(f"{key} filelist/RTL inventory contradiction")
    for row in expected_sources + expected_includes:
        if sha256(read(attempt_dir / "bundle/sources" / row["path"])) != row["sha256"]:
            raise VectorlessError(f"{key} staged RTL/include bytes mismatch: {row['path']}")

    log = read(attempt_dir / "logs/genus.log")
    verify_genus_log(log, top, contract["tool"]["version"])
    report_names = _expected_reports(top)
    if set(receipt["report_sha256"]) != set(report_names.values()):
        raise VectorlessError(f"{key} report inventory is incomplete or has unknown reports")
    reports: dict[str, bytes] = {}
    for kind, name in report_names.items():
        payload = read(attempt_dir / "work" / name)
        if not payload or receipt["report_sha256"][name] != sha256(payload):
            raise VectorlessError(f"{key} {kind} report binding mismatch")
        reports[kind] = payload
    power = parse_power_report(
        reports["gpower"], top, contract["genus_default_vectorless"])
    try:
        run_genus.verify_reports(
            attempt_dir / "work", top, log, contract["tool"]["version"])
    except run_genus.FlowError as error:
        raise VectorlessError(f"{key} native report/log verification failed: {error}") from error

    mapped = read(attempt_dir / "work" / f"{top}_netlist.v")
    mapped_sha = sha256(mapped)
    inventory = receipt["mapped_inventory"]
    if not isinstance(inventory, dict):
        raise VectorlessError(f"{key} mapped inventory is not an object")
    verify_bound_bytes(mapped, inventory.get("mapped_netlist_sha256"),
                       f"{key} mapped netlist")
    mapped_sdf = read(attempt_dir / "work" / f"{top}.sdf")
    mapped_sdc = read(attempt_dir / "work" / f"{top}_out.sdc")
    if (receipt["mapped_sdf_sha256"] != sha256(mapped_sdf) or
            receipt["mapped_sdc_sha256"] != sha256(mapped_sdc)):
        raise VectorlessError(f"{key} mapped SDF/SDC binding mismatch")

    endpoint_payload, endpoint = read_json(
        attempt_dir / "endpoint-connectivity-map.json", f"{key} endpoint map")
    exact_keys(endpoint, {"schema", "design", "top", "mapped_netlist_sha256",
                          "endpoint_link_roots", "preserved_name_prefixes",
                          "leaf_counts", "no_other_negedge_state_proven",
                          "instances"}, f"{key} endpoint map")
    endpoint_receipt = receipt["endpoint_leaf_inventory"]
    if (endpoint.get("schema") != "k2_w2_endpoint_connectivity_map_v1" or
            endpoint.get("design") != key or endpoint.get("top") != top or
            endpoint.get("mapped_netlist_sha256") != mapped_sha or
            endpoint.get("endpoint_link_roots") != design["endpoint_link_roots"] or
            endpoint.get("preserved_name_prefixes") !=
            design["endpoint_preserved_name_prefixes"] or
            endpoint.get("leaf_counts") != design["endpoint_expected_inventory"] or
            endpoint.get("no_other_negedge_state_proven") is not
            design["no_other_negedge_state_proven"] or
            endpoint_receipt != {
                "connectivity_map_sha256": sha256(endpoint_payload),
                "preserved_name_prefixes": design["endpoint_preserved_name_prefixes"],
                "leaf_counts": design["endpoint_expected_inventory"],
                "no_other_negedge_state_proven":
                    design["no_other_negedge_state_proven"],
            }):
        raise VectorlessError(f"{key} endpoint connectivity/inventory contradiction")

    functional_payload, functional = read_json(
        attempt_dir / "mapped-functional-gate.json", f"{key} mapped functional gate")
    functional_keys = {
        "schema", "status", "design", "top", "mapped_netlist_sha256", "method",
        "scenarios", "checks", "log_sha256", "model_sha256", "sdf_status",
        "sdf_sha256", "hook_sha256", "testbench_sha256", "simulator",
        "rtl_filelist_sha256",
    }
    exact_keys(functional, functional_keys, f"{key} mapped functional gate")
    if (receipt["mapped_functional_gate_sha256"] != sha256(functional_payload) or
            functional["schema"] != "k2_w2_mapped_functional_gate_v1" or
            functional["status"] != "PASS" or functional["design"] != key or
            functional["top"] != top or functional["mapped_netlist_sha256"] != mapped_sha or
            functional["method"] != "xcelium_vendor_models" or
            functional["sdf_status"] != "ANNOTATED" or
            functional["sdf_sha256"] != sha256(mapped_sdf) or
            functional["scenarios"] != design["required_mapped_functional_tests"] or
            functional["checks"] != {
                "accepted": "EXACT", "retired": "EXACT", "global_order": "EXACT",
                "conservation": "EXACT", "protocol_error": "ZERO",
                "reset_and_drain": "PASS",
            }):
        raise VectorlessError(f"{key} mapped functional GO contradiction")

    env_payload = read(attempt_dir / "bundle/server-environment.json")
    environment = validate_environment(env_payload, contract)
    exact_keys(attempt["proven_environment"], {"path", "sha256", "xrun"},
               f"{key} attempt proven environment")
    if (attempt["proven_environment"]["sha256"] != sha256(env_payload) or
            attempt["proven_environment"]["xrun"] != {
                "resolved_path": environment["gates"]["tool_executables"]["evidence"]["xrun"]["path"],
                "sha256": environment["gates"]["tool_executables"]["evidence"]["xrun"]["sha256"],
                "parsed_version": environment["gates"]["tool_executables"]["evidence"]["xrun"]["parsed_version"],
            }):
        raise VectorlessError(f"{key} PROVEN_ENVIRONMENT binding mismatch")

    expected_xrun = environment["gates"]["tool_executables"]["evidence"]["xrun"]
    simulator = functional["simulator"]
    exact_keys(simulator, {"requested_path", "resolved_path", "sha256",
                           "parsed_version"}, f"{key} mapped simulator")
    if (simulator["resolved_path"] != expected_xrun["resolved_path"] or
            simulator["requested_path"] != expected_xrun["resolved_path"] or
            simulator["sha256"] != expected_xrun["sha256"] or
            simulator["parsed_version"] != expected_xrun["parsed_version"]):
        raise VectorlessError(f"{key} mapped functional simulator contradiction")
    functional_artifacts = {
        "mapped functional log": (
            attempt_dir / "logs/mapped-functional-gate.log", functional["log_sha256"]),
        "mapped functional hook": (
            attempt_dir / "bundle/mapped_functional_hook", functional["hook_sha256"]),
        "mapped functional testbench": (
            attempt_dir / "bundle/mapped_functional_tb.sv", functional["testbench_sha256"]),
        "mapped functional RTL filelist": (
            attempt_dir / "bundle/mapped_functional_rtl.f",
            functional["rtl_filelist_sha256"]),
    }
    for label, (path, expected_hash) in functional_artifacts.items():
        verify_bound_bytes(read(path), expected_hash, f"{key} {label}")
    if (not isinstance(functional["model_sha256"], dict) or
            not functional["model_sha256"]):
        raise VectorlessError(f"{key} functional model inventory is empty")
    for name, expected_hash in functional["model_sha256"].items():
        if (not isinstance(name, str) or not name or Path(name).name != name):
            raise VectorlessError(f"{key} invalid functional model name")
        verify_bound_bytes(read(attempt_dir / "bundle/functional_models" / name),
                           expected_hash, f"{key} functional model {name}")

    tool = attempt["genus"]
    exact_keys(tool, {"requested_path", "resolved_path", "sha256", "parsed_version"},
               f"{key} Genus identity")
    expected_tool = contract["tool"]
    if (tool != {"requested_path": expected_tool["observed_path"],
                 "resolved_path": expected_tool["resolved_path"],
                 "sha256": expected_tool["sha256"],
                 "parsed_version": expected_tool["version"]} or
            attempt["genus_command"] != [expected_tool["observed_path"], "-batch",
                                         "-files", "bundle/genus_driver.tcl"]):
        raise VectorlessError(f"{key} exact Genus command/tool/version/path/hash mismatch")
    environment_genus = environment["gates"]["tool_executables"]["evidence"]["genus"]
    if (environment_genus.get("path") != expected_tool["observed_path"] or
            environment_genus.get("resolved_path") != expected_tool["resolved_path"] or
            environment_genus.get("sha256") != expected_tool["sha256"] or
            environment_genus.get("parsed_version") != expected_tool["version"]):
        raise VectorlessError(f"{key} server environment Genus identity contradiction")

    technology = {
        "setup_liberty": ("bundle/library.lib", "library_source_sha256"),
        "hold_liberty": ("bundle/hold_library.lib", "hold_library_sha256"),
        "macro_lef": ("bundle/cells.lef", "cell_lef_sha256"),
        "setup_qrc": ("bundle/shared_typical_qrc.tch", "shared_typical_qrc_sha256"),
    }
    technology_payloads: dict[str, bytes] = {}
    for role, (relative, field) in technology.items():
        payload = read(attempt_dir / relative)
        technology_payloads[role] = payload
        if sha256(payload) != attempt[field]:
            raise VectorlessError(f"{key} staged {role} bytes/hash mismatch")
    if (attempt["library_source_sha256"] != contract["technology"]["setup_liberty"]["sha256"] or
            attempt["library_snapshot_sha256"] != attempt["library_source_sha256"] or
            attempt["hold_library_sha256"] != contract["technology"]["hold_liberty"]["sha256"] or
            attempt["cell_lef_sha256"] != contract["technology"]["macro_lef_sha256"] or
            attempt["shared_typical_qrc_sha256"] !=
            contract["technology"]["shared_typical_qrc_sha256"]):
        raise VectorlessError(f"{key} staged technology contract mismatch")

    environment_technology = environment["gates"]["technology_files"]["evidence"]
    for role in ("setup_liberty", "hold_liberty", "macro_lef", "setup_qrc"):
        row = environment_technology[role]
        exact_keys(row, {"path", "sha256", "size_bytes"},
                   f"{key} environment technology {role}")
        if row["sha256"] != sha256(technology_payloads[role]):
            raise VectorlessError(f"{key} environment/staged {role} contradiction")
    if environment_technology["hold_qrc"] != environment_technology["setup_qrc"]:
        raise VectorlessError(f"{key} environment shared QRC contradiction")

    handoff_payload, handoff = read_json(
        attempt_dir / "innovus-handoff.json", f"{key} Innovus handoff")
    exact_keys(handoff, {
        "schema", "design", "top", "mapped_netlist_sha256", "mapped_sdf_sha256",
        "mapped_sdc_sha256", "strict_input_sdc_path", "strict_input_sdc_sha256",
        "materialized_input_sdc_path", "materialized_input_sdc_sha256",
        "timing_cohort_manifest", "timing_cohort", "mmmc_template",
        "setup_liberty_sha256", "hold_liberty_sha256", "cell_lef_sha256",
        "shared_setup_hold_qrc_sha256", "shared_qrc_limitation",
        "innovus_consumption_status",
    }, f"{key} Innovus handoff")
    if (receipt["innovus_handoff_sha256"] != sha256(handoff_payload) or
            handoff["schema"] != "k2_w2_innovus_strict_sdc_handoff_v1" or
            handoff["design"] != key or handoff["top"] != top or
            handoff["mapped_netlist_sha256"] != mapped_sha or
            handoff["mapped_sdf_sha256"] != sha256(mapped_sdf) or
            handoff["mapped_sdc_sha256"] != sha256(mapped_sdc) or
            handoff["strict_input_sdc_path"] != design["strict_sdc"]["path"] or
            handoff["strict_input_sdc_sha256"] != design["strict_sdc"]["sha256"] or
            handoff["materialized_input_sdc_path"] != "bundle/constraints.sdc" or
            handoff["materialized_input_sdc_sha256"] != sha256(sdc) or
            handoff["timing_cohort_manifest"] != registry["timing_cohort_manifest_identity"] or
            handoff["timing_cohort"] != registry["selected_timing_cohort"] or
            handoff["mmmc_template"] != registry["mmmc_template_identity"] or
            handoff["setup_liberty_sha256"] != attempt["library_source_sha256"] or
            handoff["hold_liberty_sha256"] != attempt["hold_library_sha256"] or
            handoff["cell_lef_sha256"] != attempt["cell_lef_sha256"] or
            handoff["shared_setup_hold_qrc_sha256"] !=
            attempt["shared_typical_qrc_sha256"] or
            handoff["shared_qrc_limitation"] !=
            "ONE_TYPICAL_GPDK045_TCH_FOR_SETUP_AND_HOLD" or
            handoff["innovus_consumption_status"] !=
            "PENDING_REQUIRES_EXACT_HASH_RECEIPT"):
        raise VectorlessError(f"{key} mapped handoff/technology contradiction")

    try:
        recomputed = run_genus.mapped_inventory(
            attempt_dir / "work" / f"{top}_netlist.v",
            attempt_dir / "bundle/library.lib", top,
            design["mapped_rx_contract"], design["mapped_posedge_contract"],
            design["endpoint_expected_inventory"], design["endpoint_link_roots"],
            design["endpoint_preserved_name_prefixes"])
    except run_genus.FlowError as error:
        raise VectorlessError(
            f"{key} mapped connectivity/inventory re-verification failed: {error}") from error
    instances = recomputed.pop("endpoint_instances")
    if recomputed != receipt["mapped_inventory"] or instances != endpoint["instances"]:
        raise VectorlessError(f"{key} independently recomputed endpoint inventory differs")

    return {
        "design": key, "top": top, "attempt_dir": attempt_dir,
        "attempt": attempt, "receipt": receipt, "environment": environment,
        "environment_sha256": sha256(env_payload), "power": power,
        "mapped_netlist_sha256": mapped_sha, "endpoint": endpoint,
        "technology_payloads": technology_payloads,
    }


def _hold(output: Path, contract: dict[str, Any], identity: dict[str, Any],
          evidence_sha: str, rows: list[dict[str, Any]], reason: str,
          status: str =
          "HOLD_PRODUCER_ENVIRONMENT_NOT_INDEPENDENTLY_REVERIFIED") -> Path:
    result = {
        "schema": "k2_endpoint_vectorless_qualification_v2",
        "status": status,
        "comparison_ready": False,
        "candidate_go": False,
        "reason": reason,
        "evidence_class": contract["evidence_class"],
        "power_method": "GENUS_DEFAULT_VECTORLESS",
        "activity_annotated": False,
        "activity_power_eligible": False,
        "claim_boundary": contract["activity_policy"]["claim_boundary"],
        "candidate_order": contract["candidate_order"],
        "comparability_policy": contract["comparability_policy"],
        "bindings": identity,
        "evidence_manifest_sha256": evidence_sha,
        "statically_verified_candidates": [row["design"] for row in rows],
    }
    write_exclusive(output, canonical(result))
    return output


def qualify(evidence_path: Path, output: Path, root: Path = ROOT) -> Path:
    contract = expected_contract()
    registry = validate_contract(root, contract)
    identity = contract_identity(root, contract, registry)
    evidence_payload, evidence = read_json(evidence_path, "evidence manifest")
    exact_keys(evidence, {"schema", "evidence_class", "candidate_order",
                          "comparability_policy", "bindings", "rows"},
               "evidence manifest")
    if (evidence["schema"] != "k2_endpoint_vectorless_evidence_v2" or
            evidence["evidence_class"] != contract["evidence_class"] or
            evidence["candidate_order"] != contract["candidate_order"] or
            evidence["comparability_policy"] != contract["comparability_policy"] or
            evidence["bindings"] != identity):
        raise VectorlessError("evidence cohort/contract binding mismatch")
    rows = evidence["rows"]
    if not isinstance(rows, list) or len(rows) != len(contract["candidate_order"]):
        raise VectorlessError("evidence must contain exact three-candidate cohort")
    evidence_root = evidence_path.resolve(strict=True).parent
    verified: list[dict[str, Any]] = []
    driver = default_vectorless_driver(root, contract)
    for index, key in enumerate(contract["candidate_order"]):
        row = rows[index]
        exact_keys(row, {"design", "top", "attempt_directory"}, f"row {index}")
        design = registry["designs"][key]
        if row["design"] != key or row["top"] != design["top"]:
            raise VectorlessError(f"row {index} candidate/top contradiction")
        attempt_dir = _attempt_directory(evidence_root, row["attempt_directory"], key)
        verified.append(_verify_static_attempt(
            attempt_dir, key, design, registry, contract, driver))

    environment_hashes = {row["environment_sha256"] for row in verified}
    if len(environment_hashes) != 1:
        raise VectorlessError("three candidates used different server environments")

    # Native v3 is a strong fail-closed producer, but its published receipt does
    # not bind the exact log bytes, the subprocess return code, or a complete
    # canonical ledger of every attempt artifact.  A downstream index must not
    # promote those missing producer facts into proof.  Keep this cohort HOLD
    # until run_genus itself publishes a new hash-pinned receipt schema.
    producer_requirements = [
        "producer receipt field genus_log_sha256",
        "producer receipt field genus_exit_code=0",
        "producer receipt field complete_artifact_inventory_sha256",
        "producer receipt field exact_executed_argv (not normalized argv)",
        "producer-bound mapped-functional stdout/log hashes and exit_code=0",
        "pinned functional-model authority set",
        "trusted producer attestation or verifier-owned immutable execution root",
    ]
    if all(row["receipt"]["schema"] ==
           "k2_w2_genus_exact_three_endpoint_receipt_v3" for row in verified):
        return _hold(
            output, contract, identity, sha256(evidence_payload), verified,
            "inherited run_genus v3 receipts are insufficient for GO; required "
            "producer-owned additions: " + ", ".join(producer_requirements),
            "HOLD_INHERITED_GENUS_V3_PRODUCER_RECEIPT_INCOMPLETE")
    raise VectorlessError("unsupported producer receipt schema; GO is fail-closed")


def build_evidence(output_root: Path, contract: dict[str, Any],
                   registry: dict[str, Any], attempts: list[tuple[str, Path]]) -> dict[str, Any]:
    root = output_root.resolve(strict=True)
    rows = []
    for key, attempt_dir in attempts:
        relative = attempt_dir.resolve(strict=True).relative_to(root).as_posix()
        rows.append({"design": key, "top": registry["designs"][key]["top"],
                     "attempt_directory": relative})
    return {
        "schema": "k2_endpoint_vectorless_evidence_v2",
        "evidence_class": contract["evidence_class"],
        "candidate_order": contract["candidate_order"],
        "comparability_policy": contract["comparability_policy"],
        "bindings": contract_identity(ROOT, contract, registry),
        "rows": rows,
    }


def execute(args: argparse.Namespace) -> tuple[Path, Path]:
    contract = expected_contract()
    registry = validate_contract(ROOT, contract)
    if not SAFE_NAME.fullmatch(args.attempt_prefix):
        raise VectorlessError("invalid attempt prefix")
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=False, exist_ok=False)
    attempts: list[tuple[str, Path]] = []
    for ordinal, key in enumerate(registry["goal_order"], start=1):
        attempt_name = f"{args.attempt_prefix}-{ordinal:02d}-{key}"
        receipt = run_genus.run_flow(
            ROOT, key, args.genus, args.library, args.hold_library,
            args.cell_lef, args.shared_qrc, output_root, attempt_name,
            args.mapped_functional_hook, args.functional_model,
            args.golden_archive, args.raw_golden_archive,
            args.functional_loss_archive, args.server_environment_receipt,
            contract["timing_cohort"])
        attempts.append((key, receipt.parent))
    evidence = build_evidence(output_root, contract, registry, attempts)
    evidence_path = output_root / "vectorless-evidence.json"
    write_exclusive(evidence_path, canonical(evidence))
    qualification = output_root / "vectorless-qualification.json"
    qualify(evidence_path, qualification)
    return evidence_path, qualification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("preflight")
    check.add_argument("--repo-root", type=Path, default=ROOT)
    check.add_argument("--output", type=Path, required=True)
    qualifier = sub.add_parser("qualify")
    qualifier.add_argument("--repo-root", type=Path, default=ROOT)
    qualifier.add_argument("--evidence", type=Path, required=True)
    qualifier.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("execute")
    run.add_argument("--genus", type=Path, required=True)
    run.add_argument("--library", type=Path, required=True)
    run.add_argument("--hold-library", type=Path, required=True)
    run.add_argument("--cell-lef", type=Path, required=True)
    run.add_argument("--shared-qrc", type=Path, required=True)
    run.add_argument("--golden-archive", type=Path, required=True)
    run.add_argument("--raw-golden-archive", type=Path, required=True)
    run.add_argument("--functional-loss-archive", type=Path, required=True)
    run.add_argument("--server-environment-receipt", type=Path, required=True)
    run.add_argument("--mapped-functional-hook", type=Path, required=True)
    run.add_argument("--functional-model", type=Path, action="append", required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--attempt-prefix", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"preflight", "qualify"}:
            root = args.repo_root.resolve(strict=True)
            if root != ROOT.resolve(strict=True):
                raise VectorlessError("entrypoint/repository root mismatch")
            if args.command == "preflight":
                path = preflight(root, args.output)
                print(f"K2_ENDPOINT_VECTORLESS_HOLD receipt={path}")
            else:
                path = qualify(args.evidence, args.output, root)
                status = json.loads(read(path))["status"]
                marker = "PASS" if status.startswith("GO_") else "HOLD"
                print(f"K2_ENDPOINT_VECTORLESS_{marker} receipt={path}")
        else:
            evidence, receipt = execute(args)
            status = json.loads(read(receipt))["status"]
            marker = "PASS" if status.startswith("GO_") else "HOLD"
            print(f"K2_ENDPOINT_VECTORLESS_{marker} evidence={evidence} receipt={receipt}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            run_genus.FlowError, VectorlessError) as error:
        print(f"K2_ENDPOINT_VECTORLESS_FAIL {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
