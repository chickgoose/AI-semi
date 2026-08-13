#!/usr/bin/env python3
"""Fail-closed plan/descriptor gate for tech-staged W2 Innovus runs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "k2_physical_innovus_cohorts.json"
AUTHORITY_PATH = HERE / "k2_physical_server_environment.json"
GENERIC_RUNNER = HERE / "run_k2_physical_innovus.sh"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MODULE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.MULTILINE)
DECLARATION = re.compile(
    r"^\s*(input|output|inout)\s+(?:(?:wire|logic|reg)\s+)?"
    r"(?:signed\s+)?(\[[^\]]+\])?\s*([^;]+);", re.MULTILINE,
)
GET_PORTS = re.compile(r"\[\s*get_ports\s+(?:\{([^}]*)\}|([^\]]+))\]")
INSTANCE = re.compile(
    r"\bDFFNSRX1\s+(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;",
    re.DOTALL,
)
CONNECTION = re.compile(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^()]+?)\s*\)")
TECH_CELLS = ("TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1")
CELL_INSTANCE = re.compile(
    rf"\b({'|'.join(TECH_CELLS)})\s+(\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
    r"\s*\((.*?)\)\s*;", re.DOTALL,
)


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    design: str
    top: str
    cohort: str
    period_ns: str
    netlist: Path
    netlist_sha256: str
    sdc: Path
    sdc_sha256: str
    output_dir: Path
    producer_path: Path
    producer_sha256: str
    handoff_path: Path
    handoff_sha256: str
    endpoint_map_path: Path
    endpoint_map_sha256: str
    mapped_functional_path: Path
    mapped_functional_sha256: str
    staged_manifest_path: Path
    staged_manifest_sha256: str
    environment_path: Path
    environment_sha256: str
    activity_path: Path
    activity_sha256: str
    activity_format: str
    activity_scope: str
    activity_window_start_ns: str
    activity_window_end_ns: str


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path) -> bytes:
    if not path.is_absolute():
        raise PlanError(f"input path is not absolute: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PlanError(f"cannot open immutable input {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PlanError(f"input is not a regular single-link file: {path}")
        chunks = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if identity(before) != identity(after):
            raise PlanError(f"input changed while read: {path}")
        payload = b"".join(chunks)
        if not payload:
            raise PlanError(f"input is empty: {path}")
        return payload
    finally:
        os.close(descriptor)


def load_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlanError(f"invalid {label} JSON: {error}") from error
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be a JSON object")
    return value


def bound_payload(document: Any, label: str) -> tuple[Path, str, bytes]:
    if not isinstance(document, dict) or set(document) != {"path", "sha256"}:
        raise PlanError(f"{label} must contain exactly path and sha256")
    if not isinstance(document["sha256"], str) or not SHA256.fullmatch(document["sha256"]):
        raise PlanError(f"{label} SHA-256 is malformed")
    path = Path(document["path"])
    payload = stable_read(path)
    if sha256(payload) != document["sha256"]:
        raise PlanError(f"{label} SHA-256 mismatch: {path}")
    return path, document["sha256"], payload


def tracked_json(path: Path, schema: str) -> dict[str, Any]:
    value = load_json(path.read_bytes(), path.name)
    if value.get("schema") != schema:
        raise PlanError(f"{path.name} schema mismatch")
    return value


def load_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    registry = tracked_json(REGISTRY_PATH, "k2_w2_innovus_cohort_registry_v3")
    authority = tracked_json(AUTHORITY_PATH, "k2_w2_innovus_server_environment_v2")
    if registry.get("server_environment_authority") != \
            "scripts/ppa/k2_physical_server_environment.json":
        raise PlanError("server environment authority path mismatch")
    cohort = registry.get("cohorts", {}).get("tech_staged_complete_compositions")
    if set(registry.get("cohorts", {})) != {"tech_staged_complete_compositions"}:
        raise PlanError("final registry must contain only the tech-staged cohort")
    pointer = registry.get("committed_techmap_manifest", {})
    if registry.get("integration_state") != "ready" or \
            not isinstance(pointer.get("repository_commit"), str) or \
            not re.fullmatch(r"[0-9a-f]{40}", pointer["repository_commit"]) or \
            not isinstance(pointer.get("sha256"), str) or \
            not SHA256.fullmatch(pointer["sha256"]):
        raise PlanError("launch blocked until one committed techmap manifest is hash-bound")
    for name, identity in registry.get("technology_stage_authorities", {}).items():
        if name not in {"r1", "p6"} or set(identity) != {
                "repository_commit", "path", "sha256"} or \
                not isinstance(identity["repository_commit"], str) or \
                not re.fullmatch(r"[0-9a-f]{40}", identity["repository_commit"]) or \
                not isinstance(identity["path"], str) or not identity["path"] or \
                not isinstance(identity["sha256"], str) or \
                not SHA256.fullmatch(identity["sha256"]):
            raise PlanError("launch blocked until R1/P6 authority commits and hashes bind")
    if set(registry.get("technology_stage_authorities", {})) != {"r1", "p6"}:
        raise PlanError("technology stage authority set mismatch")
    tops = {row["top"] for row in cohort["designs"].values()}
    if tops & set(registry["forbidden_final_tops"]):
        raise PlanError("tech-staged cohort contains a forbidden generic/debug top")
    for row in authority["constraint_templates"].values():
        path = HERE.parents[1] / row["path"]
        if sha256(path.read_bytes()) != row["sha256"]:
            raise PlanError(f"tracked constraint template changed: {row['path']}")
    mmmc = authority.get("mmmc_template", {})
    if set(mmmc) != {"path", "sha256"} or \
            not SHA256.fullmatch(str(mmmc.get("sha256", ""))) or \
            sha256((HERE.parents[1] / mmmc["path"]).read_bytes()) != mmmc["sha256"]:
        raise PlanError("tracked producer-aligned MMMC template changed")
    return registry, authority


def port_width(token: str | None) -> int:
    if token is None:
        return 1
    match = re.fullmatch(r"\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]", token)
    if not match:
        raise PlanError(f"nonconstant mapped-netlist port range: {token}")
    high, low = map(int, match.groups())
    return abs(high - low) + 1


def mapped_ports(text: str) -> dict[str, tuple[str, int]]:
    ports: dict[str, tuple[str, int]] = {}
    for direction, width_token, names in DECLARATION.findall(text):
        width = port_width(width_token or None)
        for raw in names.split(","):
            name = raw.strip().split("=", 1)[0].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) or name in ports:
                raise PlanError(f"invalid/duplicate mapped-netlist port: {name}")
            ports[name] = (direction, width)
    return ports


def validate_netlist(payload: bytes, top: str, contract: dict[str, Any],
                     common_ports: list[dict[str, Any]],
                     endpoint_contract: dict[str, Any],
                     endpoint_map: dict[str, Any]) -> dict[str, int]:
    try:
        text = payload.decode()
    except UnicodeDecodeError as error:
        raise PlanError("mapped netlist is not UTF-8") from error
    if top not in MODULE.findall(text):
        raise PlanError(f"mapped netlist does not define exact top {top}")
    body = re.search(rf"\bmodule\s+{re.escape(top)}\b.*?\bendmodule\b", text, re.DOTALL)
    if body is None:
        raise PlanError(f"cannot isolate mapped top module {top}")
    ports = mapped_ports(body.group(0))
    exact_ports = common_ports + contract["link_pins"]
    expected_ports = {row["name"]: (row["direction"], row["width"])
                      for row in exact_ports}
    if ports != expected_ports:
        extras = sorted(set(ports) - set(expected_ports))
        missing = sorted(set(expected_ports) - set(ports))
        raise PlanError(f"mapped netlist exact canonical port signature mismatch; "
                        f"extra={extras} missing={missing}")
    forbidden_aliases = {"load_i", "pending_i", "source_ready_o", "protocol_fault_o"}
    if forbidden_aliases & set(ports):
        raise PlanError("mapped netlist contains a forbidden final-top port alias")
    for row in exact_ports:
        expected = (row["direction"], row["width"])
        if ports.get(row["name"]) != expected:
            raise PlanError(f"mapped netlist port mismatch for {top}.{row['name']}")
    if re.search(r"\b(?:SDFF\w*|SCAN\w*)\b", text, re.IGNORECASE):
        raise PlanError("mapped netlist contains forbidden scan/SDFF cells")
    expected_counts = endpoint_contract.get("leaf_counts", {})
    prefixes = endpoint_contract.get("preserved_name_prefixes", {})
    if set(expected_counts) != set(TECH_CELLS) or set(prefixes) != set(TECH_CELLS) or \
            any(not isinstance(prefix, str) or not prefix for prefix in prefixes.values()) or \
            len(set(prefixes.values())) != len(TECH_CELLS) or \
            any(not isinstance(value, int) or value <= 0
                              for value in expected_counts.values()):
        raise PlanError("endpoint leaf inventory contract mismatch")
    records = []
    whole = {cell: 0 for cell in TECH_CELLS}
    for cell, raw_name, body_text in CELL_INSTANCE.findall(body.group(0)):
        name = raw_name.lstrip("\\")
        pins = dict(CONNECTION.findall(body_text))
        whole[cell] += 1
        if prefixes[cell] in name:
            records.append({"name": name, "cell": cell, "pins": pins})
    endpoint_counts = {cell: 0 for cell in TECH_CELLS}
    for row in records:
        endpoint_counts[row["cell"]] += 1
        if row["cell"] == "DFFNSRX1" and (
                row["pins"].get("CKN") != "link_clk_o" or
                row["pins"].get("RN") != "rst_n" or
                row["pins"].get("SN") not in {"1'b1", "1’h1", "1'h1"}):
            raise PlanError("endpoint DFFNSRX1 must bind CKN=link clock, RN=rst_n, SN=1")
    if endpoint_counts != expected_counts:
        raise PlanError("preserved endpoint leaf counts differ from exact contract")
    if endpoint_map.get("instances") != records or \
            endpoint_map.get("leaf_counts") != expected_counts or \
            endpoint_map.get("preserved_name_prefixes") != prefixes:
        raise PlanError("endpoint pre-map provenance/connectivity map mismatch")
    no_other_negedge = endpoint_contract.get("no_other_negedge_state_proven") is True
    if endpoint_map.get("no_other_negedge_state_proven") is not no_other_negedge:
        raise PlanError("endpoint negedge provenance claim mismatch")
    if no_other_negedge and whole["DFFNSRX1"] != expected_counts["DFFNSRX1"]:
        raise PlanError("whole-top DFFNS total violates proven no-other-negedge claim")
    if any(whole[cell] < expected_counts[cell] for cell in TECH_CELLS):
        raise PlanError("whole-top inventory cannot undercount endpoint leaves")
    return whole


def sdc_tokens(text: str) -> set[str]:
    result = set()
    for braced, plain in GET_PORTS.findall(text):
        result.update(token.strip('"') for token in (braced or plain).split())
    return result


def command_tokens(text: str, command: str) -> set[str]:
    result = set()
    for row in re.split(r"[;\n]", text.replace("\\\n", " ")):
        if re.search(rf"\b{re.escape(command)}\b", row):
            result.update(sdc_tokens(row))
    return result


def covered(tokens: set[str], name: str, width: int) -> bool:
    return name in tokens or name + "*" in tokens or name + "[*]" in tokens or \
        (width > 1 and all(f"{name}[{index}]" in tokens for index in range(width)))


def validate_sdc(payload: bytes, top: str, contract: dict[str, Any], period: str) -> None:
    try:
        text = payload.decode()
    except UnicodeDecodeError as error:
        raise PlanError("mapped SDC is not UTF-8") from error
    if re.search(r"\b(?:load_i|pending_i|source_ready_o|protocol_fault_o)\b", text):
        raise PlanError("mapped SDC contains a forbidden final-top port alias")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", period) or float(period) <= 0:
        raise PlanError("period_ns must be a canonical positive decimal")
    current = re.findall(r"^\s*current_design\s+\{?([A-Za-z_]\w*)\}?\s*$", text, re.MULTILINE)
    if current != [top]:
        raise PlanError("mapped SDC current_design/top mismatch")
    for port in contract["clocks"]["input"]:
        if not covered(command_tokens(text, "create_clock"), port, 1):
            raise PlanError(f"mapped SDC missing input clock {port}")
    for port in contract["clocks"]["generated"]:
        if not covered(command_tokens(text, "create_generated_clock"), port, 1):
            raise PlanError(f"mapped SDC missing generated clock {port}")
    for pin in contract["link_pins"]:
        if pin["name"] not in contract["clocks"]["generated"] and not covered(
                command_tokens(text, "set_output_delay"), pin["name"], pin["width"]):
            raise PlanError(f"mapped SDC does not constrain link pin {pin['name']}")
    periods = re.findall(r"\bcreate_clock\b[^\n;]*?-period\s+([0-9.]+)", text)
    if not periods or any(float(value) != float(period) for value in periods):
        raise PlanError("mapped SDC create_clock period mismatch")
    if "set_load" not in text or "set_input_transition" not in text or \
            "set_clock_gating_check" not in text or "-clock_fall" not in text:
        raise PlanError("mapped SDC lost load/driver/gating/DDR constraint classes")
    if re.search(r"set_false_path[^\n]*(?:rst_n|RN)", text):
        raise PlanError("mapped SDC false-paths reset recovery/removal")


def validate_staged_manifest(bound: dict[str, Any], registry: dict[str, Any],
                             authority: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    path, digest, payload = bound_payload(bound, "tech-staged manifest")
    manifest = load_json(payload, "tech-staged manifest")
    contract = registry["staged_manifest_contract"]
    if manifest.get("schema") != contract["schema"] or manifest.get("status") != contract["status"]:
        raise PlanError("tech-staged manifest schema/status mismatch")
    pointer = registry["committed_techmap_manifest"]
    if digest != pointer["sha256"] or \
            manifest.get("repository_commit") != pointer["repository_commit"]:
        raise PlanError("tech-staged manifest differs from committed registry pointer")
    cohort = registry["cohorts"]["tech_staged_complete_compositions"]
    common_ports = cohort["common_ports"]
    common_inputs = [row for row in common_ports if row["direction"] == "input"]
    common_outputs = [row for row in common_ports if row["direction"] == "output"]
    if manifest.get("goal_order") != contract["goal_order"] or \
            list(manifest.get("designs", {})) != contract["goal_order"] or \
            manifest.get("common_ports") != common_ports or \
            manifest.get("common_inputs") != common_inputs or \
            manifest.get("common_outputs") != common_outputs or \
            manifest.get("technology_authorities") != \
            registry["technology_stage_authorities"]:
        raise PlanError("tech-staged manifest goal order/canonical port contract mismatch")
    verify_committed_blob(path, pointer["repository_commit"], digest)
    if manifest.get("constraint_templates") != authority["constraint_templates"]:
        raise PlanError("tech-staged manifest constraint-template hashes mismatch")
    for design, row in manifest["designs"].items():
        expected_top = registry["cohorts"]["tech_staged_complete_compositions"]["designs"][design]["top"]
        expected_design = cohort["designs"][design]
        endpoint = expected_design["endpoint_leaf_contract"]
        if row.get("top") != expected_top or \
                row.get("required_ports") != common_ports or \
                row.get("link_pins") != expected_design["link_pins"] or \
                row.get("strict_sdc") != authority["constraint_templates"][
                    expected_design["constraint_template"]] or \
                row.get("endpoint_root") != expected_design["endpoint_root"] or \
                row.get("endpoint_leaf_contract") != endpoint:
            raise PlanError("tech-staged manifest top mismatch")
    return path, digest, manifest


def verify_committed_blob(path: Path, commit: str, expected_sha: str) -> None:
    """Bind a materialized package file to bytes stored in one Git commit object."""
    root_result = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if root_result.returncode:
        raise PlanError("techmap manifest is not materialized from a Git repository")
    root = Path(root_result.stdout.strip()).resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as error:
        raise PlanError("techmap manifest escapes its Git repository") from error
    object_result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{relative.as_posix()}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if object_result.returncode or sha256(object_result.stdout) != expected_sha:
        raise PlanError("techmap manifest is not the exact committed Git blob")


def validate_environment(bound: dict[str, Any], authority: dict[str, Any]) -> tuple[Path, str]:
    path, digest, payload = bound_payload(bound, "PROVEN_ENVIRONMENT receipt")
    receipt = load_json(payload, "PROVEN_ENVIRONMENT receipt")
    if receipt.get("schema") != "k2_w2_server_env_result_v1" or \
            receipt.get("qualification_status") != "PROVEN_ENVIRONMENT" or \
            receipt.get("campaign_launch_allowed") is not True:
        raise PlanError("server environment is not PROVEN_ENVIRONMENT")
    gates = receipt.get("gates", {})
    tool = gates.get("tool_executables", {}).get("evidence", {}).get("innovus", {})
    if any(tool.get(key) != authority["tool"][key] for key in ("path", "sha256")) or \
            tool.get("parsed_version") != authority["tool"]["version"]:
        raise PlanError("PROVEN_ENVIRONMENT Innovus identity mismatch")
    files = gates.get("technology_files", {}).get("evidence", {})
    for role in ("setup_liberty", "hold_liberty", "tech_lef", "macro_lef"):
        if files.get(role, {}).get("sha256") != authority["technology"][role]["sha256"]:
            raise PlanError(f"PROVEN_ENVIRONMENT {role} hash mismatch")
    for role in ("setup_qrc", "hold_qrc"):
        if files.get(role, {}).get("sha256") != authority["technology"]["shared_qrc"]["sha256"]:
            raise PlanError("PROVEN_ENVIRONMENT shared QRC hash mismatch")
    semantics = gates.get("library_semantics", {}).get("evidence", {})
    if semantics.get("setup", {}).get("pvt") != authority["technology"]["setup_liberty"]["pvt"] or \
            semantics.get("hold", {}).get("pvt") != authority["technology"]["hold_liberty"]["pvt"]:
        raise PlanError("PROVEN_ENVIRONMENT slow/fast PVT mismatch")
    if gates.get("dffnsrx1_contract", {}).get("evidence") != authority["rx_cell"]:
        raise PlanError("PROVEN_ENVIRONMENT DFFNSRX1 Liberty/LEF contract mismatch")
    return path, digest


def validate_genus(producer: dict[str, Any], design: str, top: str,
                   netlist_sha: str, sdc_sha: str, staged_bound: dict[str, str],
                   environment_bound: dict[str, str], template: dict[str, str],
                   endpoint_contract: dict[str, Any],
                   whole_inventory: dict[str, int],
                   authority: dict[str, Any]) -> tuple[Path, str, Path, str, Path, str,
                                                       Path, str]:
    if set(producer) != {"kind", "receipt", "innovus_handoff",
                         "endpoint_connectivity_map", "mapped_functional_gate"} or \
            producer.get("kind") != "k2_w2_genus_exact_three_endpoint_receipt_v3":
        raise PlanError("producer must be one authenticated Genus v3 receipt/handoff")
    path, digest, payload = bound_payload(producer["receipt"], "Genus v3 receipt")
    receipt = load_json(payload, "Genus v3 receipt")
    handoff_path, handoff_sha, handoff_payload = bound_payload(
        producer["innovus_handoff"], "Genus Innovus handoff")
    handoff = load_json(handoff_payload, "Genus Innovus handoff")
    endpoint_path, endpoint_sha, endpoint_payload = bound_payload(
        producer["endpoint_connectivity_map"], "endpoint connectivity map")
    endpoint_map = load_json(endpoint_payload, "endpoint connectivity map")
    functional_path, functional_sha, functional_payload = bound_payload(
        producer["mapped_functional_gate"], "mapped functional gate receipt")
    functional = load_json(functional_payload, "mapped functional gate receipt")
    if receipt.get("schema") != "k2_w2_genus_exact_three_endpoint_receipt_v3" or \
            receipt.get("status") != "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD" or \
            receipt.get("design") != design or receipt.get("top") != top or \
            receipt.get("boundary_cohort") != "tech_staged_complete_compositions" or \
            receipt.get("claim_boundary") != \
            "GENUS_MAPPED_TIMING_SCREENING_ONLY_POWER_AND_PHYSICAL_PPA_HOLD":
        raise PlanError("Genus v3 receipt design/top/status/screening boundary mismatch")
    staged_identity = receipt.get("staged_manifest", {})
    if staged_identity.get("sha256") != staged_bound["sha256"] or \
            handoff.get("schema") != "k2_w2_innovus_strict_sdc_handoff_v1" or \
            receipt.get("innovus_handoff_sha256") != handoff_sha or \
            handoff.get("design") != design or handoff.get("top") != top or \
            handoff.get("strict_input_sdc_sha256") != template["sha256"]:
        raise PlanError("Genus v3 receipt/manifest/handoff identity mismatch")
    inventory = receipt.get("mapped_inventory", {})
    endpoint_counts = endpoint_contract["leaf_counts"]
    mapped_types = inventory.get("mapped_cell_types", {})
    if not isinstance(mapped_types, dict) or \
            any(not isinstance(cell, str) or not isinstance(count, int) or count <= 0
                for cell, count in mapped_types.items()) or \
            inventory.get("mapped_cell_count") != sum(mapped_types.values()) or \
            any(mapped_types.get(cell) != count
                for cell, count in whole_inventory.items()):
        raise PlanError("Genus v3 whole-top mapped inventory accounting mismatch")
    if inventory.get("mapped_netlist_sha256") != netlist_sha or \
            receipt.get("mapped_sdc_sha256") != sdc_sha or \
            inventory.get("scan_cell_types") != [] or \
            inventory.get("required_rx_contract", {}).get("exact_instances") != \
            endpoint_counts["DFFNSRX1"] or \
            handoff.get("mapped_netlist_sha256") != netlist_sha or \
            handoff.get("mapped_sdc_sha256") != sdc_sha:
        raise PlanError("Genus v3 mapped inventory/netlist/SDC mismatch")
    endpoint_evidence = receipt.get("endpoint_leaf_inventory", {})
    if endpoint_evidence != {
            "connectivity_map_sha256": endpoint_sha,
            "preserved_name_prefixes": endpoint_contract["preserved_name_prefixes"],
            "leaf_counts": endpoint_counts,
            "no_other_negedge_state_proven":
                endpoint_contract["no_other_negedge_state_proven"],
            } or endpoint_map.get("schema") != "k2_w2_endpoint_connectivity_map_v1" or \
            endpoint_map.get("design") != design or endpoint_map.get("top") != top:
        raise PlanError("Genus v3 endpoint provenance evidence mismatch")
    scenarios = {
        "fovea_a7": ["held_pending", "conservation", "reset", "drain"],
        "a2_p6": ["ordered_pairs", "back_to_back", "reset"],
        "a3_p6": ["ordered_pairs", "back_to_back", "reset"],
    }[design]
    if functional.get("schema") != "k2_w2_mapped_functional_gate_v1" or \
            functional.get("status") != "PASS" or \
            functional.get("design") != design or functional.get("top") != top or \
            functional.get("mapped_netlist_sha256") != netlist_sha or \
            functional.get("method") not in {"xcelium_vendor_models", "formal_lec"} or \
            functional.get("scenarios") != scenarios or \
            functional.get("checks") != {
                "accepted": "EXACT", "retired": "EXACT", "global_order": "EXACT",
                "conservation": "EXACT", "protocol_error": "ZERO",
                "reset_and_drain": "PASS"} or \
            not isinstance(functional.get("log_sha256"), str) or \
            not SHA256.fullmatch(functional["log_sha256"]) or \
            not isinstance(functional.get("model_sha256"), dict) or \
            not functional["model_sha256"] or \
            any(not SHA256.fullmatch(value) for value in functional["model_sha256"].values()) or \
            functional.get("sdf_status") not in {"ANNOTATED", "UNAVAILABLE_EXPLICIT"}:
        raise PlanError("mapped staged-vs-netlist functional gate mismatch")
    sdf_sha = functional.get("sdf_sha256")
    if (functional["sdf_status"] == "ANNOTATED" and
            (not isinstance(sdf_sha, str) or not SHA256.fullmatch(sdf_sha))) or \
            (functional["sdf_status"] == "UNAVAILABLE_EXPLICIT" and sdf_sha is not None):
        raise PlanError("mapped functional SDF identity/status mismatch")
    if receipt.get("mapped_functional_gate_sha256") != functional_sha:
        raise PlanError("Genus v3 receipt does not bind mapped functional gate")
    checks = receipt.get("checks", {})
    if checks.get("dffnsrx1_rx_mapping") != \
            "PASS_EXACT_COUNT_PINS_AND_NONZERO_RECOVERY_REMOVAL" or \
            checks.get("power_activity_gate") != "HOLD_VECTORLESS_IS_NOT_ACTIVITY_QUALIFIED":
        raise PlanError("Genus v3 RX arcs or screening-only power boundary mismatch")
    # Fast Liberty, LEF and shared QRC are authenticated downstream provenance;
    # only the slow Liberty drove the Genus synthesis/timing screen.
    if handoff.get("innovus_consumption_status") != \
            "PENDING_REQUIRES_EXACT_HASH_RECEIPT":
        raise PlanError("Genus v3 handoff was already or ambiguously consumed")
    technology = authority["technology"]
    handoff_technology = {
        "setup_liberty_sha256": technology["setup_liberty"]["sha256"],
        "hold_liberty_sha256": technology["hold_liberty"]["sha256"],
        "cell_lef_sha256": technology["macro_lef"]["sha256"],
        "shared_setup_hold_qrc_sha256": technology["shared_qrc"]["sha256"],
        "shared_qrc_limitation": "ONE_TYPICAL_GPDK045_TCH_FOR_SETUP_AND_HOLD",
    }
    if any(handoff.get(key) != value for key, value in handoff_technology.items()):
        raise PlanError("Genus v3 handoff technology provenance mismatch")
    return (path, digest, handoff_path, handoff_sha, endpoint_path, endpoint_sha,
            functional_path, functional_sha)


def validate_activity(value: Any) -> tuple[Path, str, str, str, str, str]:
    expected = {"file", "format", "scope", "window_start_ns", "window_end_ns"}
    if not isinstance(value, dict) or set(value) != expected:
        raise PlanError("activity must contain exact file/format/scope/window fields")
    path, digest, _ = bound_payload(value["file"], "annotated activity")
    if value["format"] not in {"SAIF", "VCD"} or not value["scope"]:
        raise PlanError("activity must be scoped SAIF or VCD")
    try:
        start, end = float(value["window_start_ns"]), float(value["window_end_ns"])
    except (TypeError, ValueError) as error:
        raise PlanError("activity window is not numeric") from error
    if start < 0 or end <= start:
        raise PlanError("activity window is empty or reversed")
    return path, digest, value["format"], value["scope"], \
        value["window_start_ns"], value["window_end_ns"]


def validate_plan(plan_path: Path) -> list[Binding]:
    plan = load_json(stable_read(plan_path.absolute()), "Innovus plan")
    required = {"schema", "cohort", "purpose", "ranking_eligible",
                "staged_manifest", "server_environment", "runs"}
    if set(plan) != required or plan.get("schema") != "k2_w2_innovus_plan_v2":
        raise PlanError("Innovus plan schema/field set mismatch")
    registry, authority = load_contracts()
    cohort_name = "tech_staged_complete_compositions"
    if plan["cohort"] != cohort_name:
        raise PlanError("only tech-staged final cohort may be launched")
    cohort = registry["cohorts"][cohort_name]
    if plan["purpose"] != cohort["purpose"] or plan["ranking_eligible"] is not True:
        raise PlanError("plan purpose/ranking mismatch")
    manifest_path, manifest_sha, manifest = validate_staged_manifest(
        plan["staged_manifest"], registry, authority)
    environment_path, environment_sha = validate_environment(
        plan["server_environment"], authority)
    runs = plan["runs"]
    if not isinstance(runs, list) or [row.get("design") for row in runs] != cohort["exact_design_set"]:
        raise PlanError("plan must use the exact ordered tech-staged design set")
    bindings = []
    period_seen = None
    seen: set[Path] = set()
    outputs: set[Path] = set()
    for run in runs:
        fields = {"design", "top", "clocks", "link_pins", "period_ns",
                  "mapped_netlist", "mapped_sdc", "producer", "activity", "output_dir"}
        if not isinstance(run, dict) or set(run) != fields:
            raise PlanError("plan run field set mismatch")
        design = run["design"]
        contract = cohort["designs"][design]
        top = run["top"]
        if top != contract["top"] or top in registry["forbidden_final_tops"]:
            raise PlanError("generic/debug top substitution is forbidden")
        if run["clocks"] != contract["clocks"] or run["link_pins"] != contract["link_pins"]:
            raise PlanError("plan clock/link contract mismatch")
        period = run["period_ns"]
        if period_seen is None:
            period_seen = period
        elif period != period_seen:
            raise PlanError("final cohort must use one common period")
        netlist, netlist_sha, netlist_payload = bound_payload(run["mapped_netlist"], "mapped netlist")
        sdc, sdc_sha, sdc_payload = bound_payload(run["mapped_sdc"], "mapped SDC")
        staged_row = manifest["designs"][design]
        registry_endpoint = contract["endpoint_leaf_contract"]
        endpoint_contract = staged_row["endpoint_leaf_contract"]
        if endpoint_contract != registry_endpoint:
            raise PlanError("tech-staged endpoint contract diverges from registry")
        _, _, endpoint_payload = bound_payload(
            run["producer"]["endpoint_connectivity_map"],
            "endpoint connectivity map")
        endpoint_map = load_json(endpoint_payload, "endpoint connectivity map")
        whole_inventory = validate_netlist(
            netlist_payload, top, contract, cohort["common_ports"],
            endpoint_contract, endpoint_map)
        validate_sdc(sdc_payload, top, contract, period)
        template = authority["constraint_templates"][contract["constraint_template"]]
        (producer, producer_sha, handoff, handoff_sha, endpoint_map_path,
         endpoint_map_sha, mapped_functional, mapped_functional_sha) = validate_genus(
            run["producer"], design, top, netlist_sha, sdc_sha,
            plan["staged_manifest"], plan["server_environment"], template,
            endpoint_contract, whole_inventory, authority)
        activity = validate_activity(run["activity"])
        output = Path(run["output_dir"])
        if not output.is_absolute() or output.exists() or output in outputs:
            raise PlanError("output directory must be unique, absent, and absolute")
        outputs.add(output)
        for path in (netlist, sdc, producer, handoff, endpoint_map_path,
                     mapped_functional, activity[0]):
            if path in seen:
                raise PlanError("per-run evidence file reused across runs")
            seen.add(path)
        bindings.append(Binding(
            design, top, cohort_name, period, netlist, netlist_sha, sdc, sdc_sha,
            output, producer, producer_sha, handoff, handoff_sha,
            endpoint_map_path, endpoint_map_sha,
            mapped_functional, mapped_functional_sha,
            manifest_path, manifest_sha,
            environment_path, environment_sha, *activity,
        ))
    return bindings


def descriptor(binding: Binding) -> dict[str, Any]:
    value = asdict(binding)
    for key, item in list(value.items()):
        if isinstance(item, Path):
            value[key] = str(item)
    return {"schema": "k2_w2_innovus_execution_descriptor_v1", "binding": value,
            "registry_sha256": sha256(REGISTRY_PATH.read_bytes()),
            "authority_sha256": sha256(AUTHORITY_PATH.read_bytes())}


def verify_descriptor(path: Path, expected_sha: str, environment: dict[str, str]) -> Binding:
    payload = stable_read(path.absolute())
    if not SHA256.fullmatch(expected_sha) or sha256(payload) != expected_sha:
        raise PlanError("execution descriptor SHA mismatch")
    value = load_json(payload, "execution descriptor")
    if value.get("schema") != "k2_w2_innovus_execution_descriptor_v1" or \
            value.get("registry_sha256") != sha256(REGISTRY_PATH.read_bytes()) or \
            value.get("authority_sha256") != sha256(AUTHORITY_PATH.read_bytes()):
        raise PlanError("execution descriptor authority mismatch")
    raw = value.get("binding")
    if not isinstance(raw, dict) or set(raw) != {field.name for field in Binding.__dataclass_fields__.values()}:
        raise PlanError("execution descriptor binding field mismatch")
    for key in ("netlist", "sdc", "output_dir", "producer_path", "handoff_path",
                "endpoint_map_path",
                "mapped_functional_path",
                "staged_manifest_path",
                "environment_path", "activity_path"):
        raw[key] = Path(raw[key])
    binding = Binding(**raw)
    registry, authority = load_contracts()
    env_map = {
        "AER_TOP": binding.top, "AER_PNR_NETLIST": str(binding.netlist),
        "AER_PNR_SDC": str(binding.sdc), "AER_PNR_OUTPUT_DIR": str(binding.output_dir),
        "AER_W2_COHORT": binding.cohort, "AER_W2_DESIGN": binding.design,
        "AER_ACTIVITY_FILE": str(binding.activity_path),
        "AER_ACTIVITY_FORMAT": binding.activity_format,
        "AER_ACTIVITY_SCOPE": binding.activity_scope,
        "AER_ACTIVITY_WINDOW_START_NS": binding.activity_window_start_ns,
        "AER_ACTIVITY_WINDOW_END_NS": binding.activity_window_end_ns,
    }
    if any(environment.get(key) != expected for key, expected in env_map.items()):
        raise PlanError("execution environment differs from descriptor")
    identities = ((binding.netlist, binding.netlist_sha256), (binding.sdc, binding.sdc_sha256),
                  (binding.producer_path, binding.producer_sha256),
                  (binding.handoff_path, binding.handoff_sha256),
                  (binding.endpoint_map_path, binding.endpoint_map_sha256),
                  (binding.mapped_functional_path, binding.mapped_functional_sha256),
                  (binding.staged_manifest_path, binding.staged_manifest_sha256),
                  (binding.environment_path, binding.environment_sha256),
                  (binding.activity_path, binding.activity_sha256))
    for item, digest in identities:
        if sha256(stable_read(item)) != digest:
            raise PlanError(f"descriptor input changed: {item}")
    tech_paths = {
        "AER_SETUP_LIBRARY_FILE": "setup_liberty", "AER_HOLD_LIBRARY_FILE": "hold_liberty",
        "AER_TECH_LEF": "tech_lef", "AER_CELL_LEF": "macro_lef",
    }
    for variable, role in tech_paths.items():
        supplied = Path(environment.get(variable, ""))
        expected = authority["technology"][role]
        if supplied.name != Path(expected["relative_path"]).name or \
                sha256(stable_read(supplied)) != expected["sha256"]:
            raise PlanError(f"server technology identity mismatch: {role}")
    setup_qrc = Path(environment.get("AER_SETUP_QRC_TECH", ""))
    hold_qrc = Path(environment.get("AER_HOLD_QRC_TECH", ""))
    qrc = authority["technology"]["shared_qrc"]
    if setup_qrc != hold_qrc or setup_qrc.name != "gpdk045.tch" or \
            sha256(stable_read(setup_qrc)) != qrc["sha256"]:
        raise PlanError("server QRC must be exact shared gpdk045.tch")
    tool = Path(environment.get("AER_INNOVUS_BIN", "innovus"))
    if str(tool) != authority["tool"]["path"] or sha256(stable_read(tool)) != authority["tool"]["sha256"]:
        raise PlanError("Innovus executable identity mismatch")
    if binding.output_dir.exists():
        raise PlanError("descriptor output directory already exists")
    return binding


def execute_plan(bindings: list[Binding]) -> None:
    for binding in bindings:
        with tempfile.TemporaryDirectory(prefix="w2-innovus-descriptor-") as temporary:
            path = Path(temporary) / "execution.json"
            path.write_bytes(canonical(descriptor(binding)))
            path.chmod(0o444)
            digest = sha256(path.read_bytes())
            environment = os.environ.copy()
            environment.update({
                "AER_TOP": binding.top, "AER_PNR_NETLIST": str(binding.netlist),
                "AER_PNR_SDC": str(binding.sdc), "AER_PNR_OUTPUT_DIR": str(binding.output_dir),
                "AER_W2_COHORT": binding.cohort, "AER_W2_DESIGN": binding.design,
                "AER_ACTIVITY_FILE": str(binding.activity_path),
                "AER_ACTIVITY_FORMAT": binding.activity_format,
                "AER_ACTIVITY_SCOPE": binding.activity_scope,
                "AER_ACTIVITY_WINDOW_START_NS": binding.activity_window_start_ns,
                "AER_ACTIVITY_WINDOW_END_NS": binding.activity_window_end_ns,
                "AER_W2_EXECUTION_DESCRIPTOR": str(path),
                "AER_W2_EXECUTION_DESCRIPTOR_SHA256": digest,
            })
            result = subprocess.run([str(GENERIC_RUNNER)], env=environment, check=False)
            if result.returncode:
                raise PlanError(f"Innovus runner failed for {binding.design}: {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-descriptor", type=Path)
    parser.add_argument("--descriptor-sha256")
    args = parser.parse_args(argv)
    try:
        if args.verify_descriptor:
            if args.plan or args.validate_only or args.execute or not args.descriptor_sha256:
                raise PlanError("descriptor verification mode arguments mismatch")
            verify_descriptor(args.verify_descriptor, args.descriptor_sha256, dict(os.environ))
            print("W2_INNOVUS_EXECUTION_DESCRIPTOR_VALID")
            return 0
        if not args.plan or args.validate_only == args.execute:
            raise PlanError("plan mode requires exactly one of --validate-only/--execute")
        bindings = validate_plan(args.plan)
        if args.execute:
            execute_plan(bindings)
        print(f"W2_INNOVUS_PLAN_VALID cohort={bindings[0].cohort} runs={len(bindings)} "
              f"mode={'execute' if args.execute else 'validate-only'}")
        return 0
    except (OSError, PlanError, subprocess.SubprocessError) as error:
        print(f"W2_INNOVUS_PLAN_REJECTED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
