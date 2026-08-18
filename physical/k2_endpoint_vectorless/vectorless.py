#!/usr/bin/env python3
"""Prepare, execute, and qualify the frozen complete-endpoint vectorless cohort.

The execute path delegates synthesis, source/boundary validation, mapped proof,
and report production to physical/k2_w2_genus/run_genus.py.  This module adds
only an exact vectorless switching-activity stanza and a separate evidence
class/qualifier.
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
import sys
import tempfile
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


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise VectorlessError(
            f"{label} keys mismatch: {sorted(value)} != {sorted(keys)}")


def expected_contract() -> dict[str, Any]:
    _, value = read_json(CONTRACT_PATH, "vectorless contract")
    return value


def validate_contract(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    if (contract.get("schema") != "k2_endpoint_vectorless_contract_v1" or
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
            contract.get("output_load_pf") != 0.01):
        raise VectorlessError("frozen cohort/settings contract mismatch")
    assumptions = contract.get("vectorless_assumptions")
    if assumptions != {
            "ref_clk_i": {"toggle_rate_per_period": 2.0,
                          "static_probability": 0.5},
            "sample_clk_i": {"toggle_rate_per_period": 2.0,
                             "static_probability": 0.5},
            "source_pending_i": {"toggle_rate_per_period": 0.2,
                                 "static_probability": 0.5},
            "rst_n": {"toggle_rate_per_period": 0.0,
                      "static_probability": 1.0}}:
        raise VectorlessError("vectorless activity assumptions changed")
    activity = contract.get("activity_policy", {})
    if (activity.get("waveform_import_allowed") is not False or
            activity.get("vcd_allowed") is not False or
            activity.get("saif_allowed") is not False or
            activity.get("user_activity_file") is not None or
            activity.get("claim_boundary") !=
            "VECTORLESS_DIAGNOSTIC_NOT_ACTIVITY_ANNOTATED_POWER"):
        raise VectorlessError("activity/vectorless separation changed")
    technology = contract.get("technology", {})
    if (technology.get("pdk") != "GPDK045/gsclib045" or
            technology.get("power_liberty_role") != "setup_liberty" or
            technology.get("power_corner") != {
                "voltage_v": 0.9, "temperature_c": 125.0}):
        raise VectorlessError("GPDK045 vectorless power corner mismatch")
    tool = contract.get("tool", {})
    if (tool.get("name") != "Cadence Genus" or
            tool.get("version") != "23.14-s090_1" or
            tool.get("sha256") !=
            "41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa"):
        raise VectorlessError("Genus tool identity mismatch")
    reuse = contract.get("reuse", {})
    required = {"genus_runner", "base_driver", "candidate_registry",
                "timing_registry", "server_environment_contract",
                "staged_manifest"}
    if set(reuse) != required:
        raise VectorlessError("provider closure mismatch")
    for label, artifact in reuse.items():
        exact_keys(artifact, {"path", "sha256"}, f"reuse.{label}")
        if not SHA256.fullmatch(artifact["sha256"]):
            raise VectorlessError(f"reuse.{label} SHA malformed")
        candidate = (root / artifact["path"]).resolve(strict=True)
        try:
            candidate.relative_to(root.resolve(strict=True))
        except ValueError as error:
            raise VectorlessError(f"reuse.{label} escapes repository") from error
        if sha256(read(candidate)) != artifact["sha256"]:
            raise VectorlessError(f"reuse.{label} SHA mismatch")
    server_contract = json.loads(read(
        root / reuse["server_environment_contract"]["path"]))
    try:
        server_preflight.validate_contract(server_contract)
    except server_preflight.PreflightError as error:
        raise VectorlessError(f"server environment contract invalid: {error}") from error
    server_tech = server_contract["technology"]
    if (technology.get("setup_liberty") != {
            key: server_tech["setup_liberty"][key]
            for key in ("relative_path", "sha256")} or
            technology.get("hold_liberty") != {
            key: server_tech["hold_liberty"][key]
            for key in ("relative_path", "sha256")} or
            technology.get("macro_lef_sha256") !=
            server_tech["macro_lef"]["sha256"] or
            technology.get("shared_typical_qrc_sha256") !=
            server_tech["setup_qrc"]["sha256"] or
            server_tech["setup_qrc"] != server_tech["hold_qrc"]):
        raise VectorlessError("technology settings diverge from server contract")
    try:
        registry = run_genus.load_registry(root, contract["timing_cohort"])
    except (OSError, ValueError, run_genus.FlowError) as error:
        raise VectorlessError(f"Genus registry validation failed: {error}") from error
    selected = registry["selected_timing_cohort"]
    strict = selected["strict_timing_environment"]
    if (registry["goal_order"] != contract["candidate_order"] or
            selected["period_ns"] != contract["period_ns"] or
            strict["W2_INPUT_DELAY_MIN_NS"] != "0.10" or
            strict["W2_INPUT_DELAY_MAX_NS"] != "0.50" or
            strict["W2_OUTPUT_LOAD_PF"] != "0.01"):
        raise VectorlessError("Genus timing/endpoint registry differs from contract")
    return registry


def switching_stanza(contract: dict[str, Any]) -> bytes:
    values = contract["vectorless_assumptions"]
    lines = [
        "# K2_ENDPOINT_VECTORLESS_BEGIN",
        "# Waveform import is forbidden in this evidence class.",
        "set_switching_activity -reset",
    ]
    for port in ("ref_clk_i", "sample_clk_i", "source_pending_i", "rst_n"):
        row = values[port]
        lines.append(
            "set_switching_activity -toggle_rate %.12g -static_probability %.12g "
            "[get_db ports %s]" %
            (row["toggle_rate_per_period"], row["static_probability"], port))
    lines.append("# K2_ENDPOINT_VECTORLESS_END")
    return ("\n".join(lines) + "\n").encode("utf-8")


def derived_driver(root: Path, contract: dict[str, Any]) -> bytes:
    base = read(root / contract["reuse"]["base_driver"]["path"])
    marker = b"report_power  > $OUT_DIR/${DESIGN}_gpower.rpt\n"
    if base.count(marker) != 1:
        raise VectorlessError("base Genus driver report_power seam changed")
    derived = base.replace(marker, switching_stanza(contract) + marker)
    lowered = derived.lower()
    if b"read_vcd" in lowered or b"read_saif" in lowered:
        raise VectorlessError("derived vectorless driver imports activity")
    return derived


def contract_identity(root: Path, contract: dict[str, Any],
                      registry: dict[str, Any]) -> dict[str, Any]:
    driver = derived_driver(root, contract)
    return {
        "contract_sha256": sha256(read(CONTRACT_PATH)),
        "derived_driver_sha256": sha256(driver),
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
    identity = contract_identity(root, contract, registry)
    result = {
        "schema": "k2_endpoint_vectorless_preflight_v1",
        "status": "HOLD_NO_REAL_SERVER_ARTIFACTS",
        "comparison_ready": False,
        "candidate_go": False,
        "reason": "local preflight validates contracts only; Cadence was not invoked",
        "evidence_class": contract["evidence_class"],
        "candidate_order": contract["candidate_order"],
        "comparability_policy": contract["comparability_policy"],
        "settings": {
            "technology": contract["technology"],
            "period_ns": contract["period_ns"],
            "input_delay_min_ns": contract["input_delay_min_ns"],
            "input_delay_max_ns": contract["input_delay_max_ns"],
            "output_load_pf": contract["output_load_pf"],
            "vectorless_assumptions": contract["vectorless_assumptions"],
            "activity_policy": contract["activity_policy"],
            "tool": contract["tool"],
        },
        "bindings": identity,
    }
    write_exclusive(output, canonical(result))
    return output


def artifact(root: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise VectorlessError(f"artifact escapes evidence root: {path}") from error
    return {"path": relative.as_posix(), "sha256": sha256(read(resolved))}


def resolve_artifact(evidence_root: Path, row: Any,
                     label: str) -> tuple[bytes, Path]:
    if not isinstance(row, dict):
        raise VectorlessError(f"{label} artifact must be an object")
    exact_keys(row, {"path", "sha256"}, label)
    if (not isinstance(row["path"], str) or not row["path"] or
            Path(row["path"]).is_absolute() or
            not SHA256.fullmatch(row["sha256"])):
        raise VectorlessError(f"invalid {label} artifact identity")
    path = (evidence_root / row["path"]).resolve(strict=True)
    try:
        path.relative_to(evidence_root.resolve(strict=True))
    except ValueError as error:
        raise VectorlessError(f"{label} artifact escapes evidence root") from error
    payload = read(path)
    if sha256(payload) != row["sha256"]:
        raise VectorlessError(f"{label} artifact SHA mismatch")
    return payload, path


def parse_power_report(payload: bytes, top: str) -> dict[str, float]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise VectorlessError("power report is not UTF-8") from error
    lowered = text.lower()
    forbidden = ("read_vcd", "read_saif", ".vcd", ".saif", "activity file: imported")
    if any(token in lowered for token in forbidden):
        raise VectorlessError("activity/VCD/SAIF report rejected as vectorless evidence")
    activity = re.findall(r"(?mi)^\s*\*?\s*Activity File\s*:\s*(.*?)\s*$", text)
    if activity and any(value.strip().upper() not in {"N.A.", "N/A", "NONE"}
                        for value in activity):
        raise VectorlessError("power report names an activity file")
    user = re.findall(
        r"(?mi)^\s*\*?\s*User-Defined Activity\s*:\s*(.*?)\s*$", text)
    if user and any(value.strip().upper() not in {"N.A.", "N/A", "NONE"}
                    for value in user):
        raise VectorlessError("power report contains user-defined activity")
    if f"Instance: /{top}" not in text and f"* Design: {top}" not in text:
        raise VectorlessError("power report top mismatch")
    if not re.search(r"(?mi)^\s*Power Unit:\s*W\s*$", text):
        raise VectorlessError("power report unit must be W")
    rows = re.findall(
        r"(?mi)^\s*Subtotal\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
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


def validate_environment(payload: bytes, contract: dict[str, Any]) -> dict[str, Any]:
    try:
        document = json.loads(payload)
        server_preflight.verify_go_document(
            document,
            contract["reuse"]["server_environment_contract"]["sha256"])
    except (UnicodeError, json.JSONDecodeError,
            server_preflight.PreflightError) as error:
        raise VectorlessError(f"environment receipt is not server GO: {error}") from error
    return document


def qualify(evidence_path: Path, output: Path, root: Path = ROOT) -> Path:
    contract = expected_contract()
    registry = validate_contract(root, contract)
    expected_identity = contract_identity(root, contract, registry)
    evidence_payload, evidence = read_json(evidence_path, "evidence manifest")
    exact_keys(evidence, {"schema", "execution_class", "evidence_class",
                          "candidate_order", "comparability_policy", "bindings",
                          "rows"}, "evidence manifest")
    if (evidence["schema"] != "k2_endpoint_vectorless_evidence_v1" or
            evidence["execution_class"] != "REAL_SERVER_CADENCE" or
            evidence["evidence_class"] != contract["evidence_class"] or
            evidence["candidate_order"] != contract["candidate_order"] or
            evidence["comparability_policy"] != contract["comparability_policy"] or
            evidence["bindings"] != expected_identity):
        raise VectorlessError("evidence cohort/contract binding mismatch")
    rows = evidence["rows"]
    if (not isinstance(rows, list) or
            [row.get("design") for row in rows if isinstance(row, dict)] !=
            contract["candidate_order"]):
        raise VectorlessError("evidence must contain exact ordered three-candidate cohort")
    evidence_root = evidence_path.resolve(strict=True).parent
    qualified_rows = []
    environment_sha: str | None = None
    driver_expected = derived_driver(root, contract)
    for row in rows:
        exact_keys(row, {"design", "top", "attempt", "receipt", "power_report",
                         "driver_tcl", "constraints_sdc", "genus_log",
                         "server_environment"}, f"row {row.get('design')}")
        key = row["design"]
        design = registry["designs"][key]
        if row["top"] != design["top"]:
            raise VectorlessError(f"{key} top mismatch")
        loaded: dict[str, tuple[bytes, Path]] = {}
        for name in ("attempt", "receipt", "power_report", "driver_tcl",
                     "constraints_sdc", "genus_log", "server_environment"):
            loaded[name] = resolve_artifact(
                evidence_root, row[name], f"{key}.{name}")
        attempt = json.loads(loaded["attempt"][0])
        receipt = json.loads(loaded["receipt"][0])
        environment = validate_environment(loaded["server_environment"][0], contract)
        current_environment_sha = sha256(loaded["server_environment"][0])
        if environment_sha is None:
            environment_sha = current_environment_sha
        elif environment_sha != current_environment_sha:
            raise VectorlessError("three candidates used different server environments")
        expected_sdc = run_genus.materialize_sdc(
            root, design, registry["selected_timing_cohort"])
        if loaded["constraints_sdc"][0] != expected_sdc:
            raise VectorlessError(f"{key} materialized SDC/settings mismatch")
        if loaded["driver_tcl"][0] != driver_expected:
            raise VectorlessError(f"{key} did not execute exact vectorless driver")
        log_lower = loaded["genus_log"][0].lower()
        if any(token in log_lower for token in (b"read_vcd", b"read_saif", b".vcd", b".saif")):
            raise VectorlessError(f"{key} Genus log contains activity import")
        try:
            report_hashes = receipt["report_sha256"]
            report_name = f"{design['top']}_gpower.rpt"
            expected_tool = contract["tool"]
            genus = attempt["genus"]
            proven = environment["gates"]
            env_tool = proven["tool_executables"]["evidence"]["genus"]
            env_tech = proven["technology_files"]["evidence"]
        except (KeyError, TypeError) as error:
            raise VectorlessError(f"{key} receipt/attempt evidence incomplete") from error
        if (attempt.get("schema") != "k2_w2_genus_exact_three_endpoint_attempt_v3" or
                attempt.get("design") != key or attempt.get("top") != design["top"] or
                attempt.get("boundary_cohort") != design["boundary_cohort"] or
                attempt.get("ranking_policy") != registry["ranking_policy"] or
                attempt.get("staged_manifest") != registry["staged_manifest_identity"] or
                attempt.get("timing_cohort") != registry["selected_timing_cohort"] or
                attempt.get("driver_tcl_sha256") != sha256(driver_expected) or
                attempt.get("constraints_sha256") != sha256(expected_sdc) or
                attempt.get("library_source_sha256") !=
                contract["technology"]["setup_liberty"]["sha256"] or
                attempt.get("hold_library_sha256") !=
                contract["technology"]["hold_liberty"]["sha256"] or
                attempt.get("cell_lef_sha256") !=
                contract["technology"]["macro_lef_sha256"] or
                attempt.get("shared_typical_qrc_sha256") !=
                contract["technology"]["shared_typical_qrc_sha256"]):
            raise VectorlessError(f"{key} attempt boundary/technology/settings mismatch")
        if (genus.get("requested_path") != expected_tool["observed_path"] or
                genus.get("resolved_path") != expected_tool["resolved_path"] or
                genus.get("sha256") != expected_tool["sha256"] or
                genus.get("parsed_version") != expected_tool["version"]):
            raise VectorlessError(f"{key} Genus identity mismatch")
        if (env_tool.get("path") != expected_tool["observed_path"] or
                env_tool.get("resolved_path") != expected_tool["resolved_path"] or
                env_tool.get("sha256") != expected_tool["sha256"] or
                env_tool.get("parsed_version") != expected_tool["version"] or
                env_tech["setup_liberty"].get("sha256") !=
                contract["technology"]["setup_liberty"]["sha256"] or
                env_tech["hold_liberty"].get("sha256") !=
                contract["technology"]["hold_liberty"]["sha256"] or
                env_tech["macro_lef"].get("sha256") !=
                contract["technology"]["macro_lef_sha256"] or
                env_tech["setup_qrc"].get("sha256") !=
                contract["technology"]["shared_typical_qrc_sha256"] or
                env_tech["hold_qrc"] != env_tech["setup_qrc"]):
            raise VectorlessError(f"{key} proven server tool/technology mismatch")
        if (receipt.get("schema") !=
                "k2_w2_genus_exact_three_endpoint_receipt_v3" or
                receipt.get("status") !=
                "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD" or
                receipt.get("design") != key or receipt.get("top") != design["top"] or
                receipt.get("boundary_cohort") != design["boundary_cohort"] or
                receipt.get("ranking_policy") != registry["ranking_policy"] or
                receipt.get("attempt_sha256") != sha256(loaded["attempt"][0]) or
                receipt.get("staged_manifest") != registry["staged_manifest_identity"] or
                receipt.get("timing_cohort") != registry["selected_timing_cohort"] or
                receipt.get("materialized_sdc_sha256") != sha256(expected_sdc) or
                report_hashes.get(report_name) != sha256(loaded["power_report"][0])):
            raise VectorlessError(f"{key} existing Genus receipt binding mismatch")
        mapped_hash = receipt.get("mapped_inventory", {}).get(
            "mapped_netlist_sha256")
        if not isinstance(mapped_hash, str) or not SHA256.fullmatch(mapped_hash):
            raise VectorlessError(f"{key} mapped complete-endpoint hash missing")
        power = parse_power_report(loaded["power_report"][0], design["top"])
        qualified_rows.append({
            "design": key,
            "top": design["top"],
            "boundary_cohort": design["boundary_cohort"],
            "mapped_netlist_sha256": mapped_hash,
            "power_report_sha256": sha256(loaded["power_report"][0]),
            **power,
        })
    result = {
        "schema": "k2_endpoint_vectorless_qualification_v1",
        "status": "QUALIFIED_VECTORLESS_POWER",
        "comparison_ready": True,
        "candidate_go": True,
        "evidence_class": contract["evidence_class"],
        "activity_annotated": False,
        "activity_power_eligible": False,
        "claim_boundary": contract["activity_policy"]["claim_boundary"],
        "candidate_order": contract["candidate_order"],
        "comparability_policy": contract["comparability_policy"],
        "bindings": expected_identity,
        "evidence_manifest_sha256": sha256(evidence_payload),
        "server_environment_sha256": environment_sha,
        "rows": qualified_rows,
    }
    write_exclusive(output, canonical(result))
    return output


def build_evidence(output_root: Path, contract: dict[str, Any],
                   registry: dict[str, Any], attempts: list[tuple[str, Path]]) -> dict[str, Any]:
    rows = []
    for key, attempt_dir in attempts:
        top = registry["designs"][key]["top"]
        rows.append({
            "design": key, "top": top,
            "attempt": artifact(output_root, attempt_dir / "attempt.json"),
            "receipt": artifact(output_root, attempt_dir / "receipt.json"),
            "power_report": artifact(
                output_root, attempt_dir / "work" / f"{top}_gpower.rpt"),
            "driver_tcl": artifact(output_root, attempt_dir / "bundle/genus_driver.tcl"),
            "constraints_sdc": artifact(output_root, attempt_dir / "bundle/constraints.sdc"),
            "genus_log": artifact(output_root, attempt_dir / "logs/genus.log"),
            "server_environment": artifact(
                output_root, attempt_dir / "bundle/server-environment.json"),
        })
    return {
        "schema": "k2_endpoint_vectorless_evidence_v1",
        "execution_class": "REAL_SERVER_CADENCE",
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
    driver = derived_driver(ROOT, contract)
    attempts: list[tuple[str, Path]] = []
    with tempfile.TemporaryDirectory(prefix="k2-endpoint-vectorless-") as temporary:
        driver_path = Path(temporary) / "genus_driver_vectorless.tcl"
        driver_path.write_bytes(driver)
        original_driver = run_genus.DRIVER_TCL
        run_genus.DRIVER_TCL = driver_path
        try:
            for ordinal, key in enumerate(registry["goal_order"], start=1):
                attempt_name = f"{args.attempt_prefix}-{ordinal:02d}-{key}"
                receipt = run_genus.run_flow(
                    ROOT, key, args.genus, args.library, args.hold_library,
                    args.cell_lef, args.shared_qrc, output_root, attempt_name,
                    args.mapped_functional_hook, args.functional_model,
                    args.golden_archive, args.raw_golden_archive,
                    args.functional_loss_archive,
                    args.server_environment_receipt, contract["timing_cohort"])
                attempts.append((key, receipt.parent))
        finally:
            run_genus.DRIVER_TCL = original_driver
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
    qualify_parser = sub.add_parser("qualify")
    qualify_parser.add_argument("--repo-root", type=Path, default=ROOT)
    qualify_parser.add_argument("--evidence", type=Path, required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
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
        if args.command == "preflight":
            root = args.repo_root.resolve(strict=True)
            if root != ROOT.resolve(strict=True):
                raise VectorlessError("entrypoint/repository root mismatch")
            path = preflight(root, args.output)
            print(f"K2_ENDPOINT_VECTORLESS_HOLD receipt={path}")
        elif args.command == "qualify":
            root = args.repo_root.resolve(strict=True)
            if root != ROOT.resolve(strict=True):
                raise VectorlessError("entrypoint/repository root mismatch")
            path = qualify(args.evidence, args.output, root)
            print(f"K2_ENDPOINT_VECTORLESS_PASS receipt={path}")
        else:
            evidence, receipt = execute(args)
            print(f"K2_ENDPOINT_VECTORLESS_PASS evidence={evidence} receipt={receipt}")
    except (OSError, ValueError, KeyError, json.JSONDecodeError,
            run_genus.FlowError, VectorlessError) as error:
        print(f"K2_ENDPOINT_VECTORLESS_FAIL {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
