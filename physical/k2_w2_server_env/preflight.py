#!/usr/bin/env python3
"""Fail-closed K2 W2 server environment preflight and canonical receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_CONTRACT = HERE / "contract.json"
SHA_RE = re.compile(r"[0-9a-f]{64}")


class PreflightError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_read(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        before_lstat = path.lstat()
    except FileNotFoundError as error:
        raise PreflightError(f"missing input: {path}") from error
    if stat.S_ISLNK(before_lstat.st_mode):
        raise PreflightError(f"symlink input forbidden: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PreflightError(f"input must be a regular single-link file: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if identity(before) != identity(after):
            raise PreflightError(f"input changed while read: {path}")
        return b"".join(chunks), before
    finally:
        os.close(descriptor)


def stable_read_tool_entrypoint(
        path: Path, contract_row: dict[str, Any]) -> tuple[bytes, os.stat_result, Path, str]:
    """Read a tool payload while preserving Cadence argv[0] wrapper semantics.

    Genus and Innovus are deliberately invoked through product-named symlinks to
    the same regular ``.cdnWrapperIndep`` file.  Hash the resolved regular file,
    but execute the immutable, contract-pinned entrypoint name.
    """
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise PreflightError(f"missing tool entrypoint: {path}") from error
    expected_kind = contract_row.get("entrypoint_kind", "regular")
    expected_resolved = contract_row.get("resolved_path")
    if not expected_resolved:
        raise PreflightError(f"tool resolved path is not pinned: {path}")
    if stat.S_ISLNK(before.st_mode):
        if expected_kind != "symlink_wrapper":
            raise PreflightError(f"unexpected tool symlink: {path}")
        link_text = os.readlink(path)
        resolved = path.resolve(strict=True)
        payload, metadata = stable_read(resolved)
        after = path.lstat()
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if identity(before) != identity(after) or os.readlink(path) != link_text:
            raise PreflightError(f"tool entrypoint changed while read: {path}")
    else:
        if expected_kind != "regular" or not stat.S_ISREG(before.st_mode):
            raise PreflightError(f"tool entrypoint kind mismatch: {path}")
        resolved = path.resolve(strict=True)
        payload, metadata = stable_read(path)
    if str(resolved) != expected_resolved:
        raise PreflightError(
            f"tool resolved path mismatch: {path}: {resolved} != {expected_resolved}")
    return payload, metadata, resolved, expected_kind


def write_result(path: Path, document: dict[str, Any],
                 exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise PreflightError(f"output is a symlink: {path}")
    if exclusive and path.exists():
        raise PreflightError(f"immutable receipt output already exists: {path}")
    create_mode = os.O_EXCL if exclusive else os.O_TRUNC
    flags = os.O_WRONLY | os.O_CREAT | create_mode | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o644)
    payload = canonical(document)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_json(path: Path) -> dict[str, Any]:
    payload, _ = stable_read(path)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise PreflightError(f"invalid JSON: {path}: {error}") from error


def add_gate(gates: dict[str, Any], name: str, status_value: str,
             evidence: Any, reason: str | None = None) -> None:
    row = {"status": status_value, "evidence": evidence}
    if reason:
        row["reason"] = reason
    gates[name] = row


def environment_binding(result: dict[str, Any]) -> dict[str, Any]:
    gates = result["gates"]
    required = (
        "source_archives", "tool_executables", "technology_files",
        "library_semantics", "site_and_cell_availability", "rc_policy")
    missing = [name for name in required if name not in gates]
    if missing:
        raise PreflightError(f"environment binding gates missing: {missing}")
    return {
        "contract_sha256": result["contract_sha256"],
        "gates": {name: gates[name] for name in required},
    }


def finalize_result(result: dict[str, Any]) -> None:
    is_go = (result.get("qualification_status") == "PROVEN_ENVIRONMENT" and
             result.get("campaign_launch_allowed") is True)
    binding_sha = None
    if is_go:
        binding_sha = sha_bytes(canonical(environment_binding(result)))
        result["environment_binding_sha256"] = binding_sha
    result["receipt"] = {
        "schema": "k2_w2_server_env_go_receipt_v1",
        "decision": "GO" if is_go else result.get("qualification_status", "FAIL"),
        "evidence_status": "PROVEN_SERVER_ENV" if is_go else "NOT_GO",
        "contract_sha256": result.get("contract_sha256"),
        "environment_binding_sha256": binding_sha,
    }
    result["receipt_sha256"] = sha_bytes(canonical(result))


def verify_go_document(document: dict[str, Any], expected_contract_sha: str) -> None:
    observed_receipt_sha = document.get("receipt_sha256")
    if not observed_receipt_sha or not SHA_RE.fullmatch(observed_receipt_sha):
        raise PreflightError("GO receipt hash is missing or malformed")
    unsigned = dict(document)
    del unsigned["receipt_sha256"]
    if sha_bytes(canonical(unsigned)) != observed_receipt_sha:
        raise PreflightError("GO receipt content hash mismatch")
    receipt = document.get("receipt", {})
    if (receipt.get("schema") != "k2_w2_server_env_go_receipt_v1" or
            receipt.get("decision") != "GO" or
            receipt.get("evidence_status") != "PROVEN_SERVER_ENV" or
            document.get("qualification_status") != "PROVEN_ENVIRONMENT" or
            document.get("campaign_launch_allowed") is not True):
        raise PreflightError("receipt is not a PROVEN_SERVER_ENV GO")
    if (document.get("contract_sha256") != expected_contract_sha or
            receipt.get("contract_sha256") != expected_contract_sha):
        raise PreflightError("GO receipt contract binding mismatch")
    binding_sha = sha_bytes(canonical(environment_binding(document)))
    if (document.get("environment_binding_sha256") != binding_sha or
            receipt.get("environment_binding_sha256") != binding_sha):
        raise PreflightError("GO receipt environment binding mismatch")


def archive_payloads(path: Path, expected_sha: str,
                     anchors: dict[str, str]) -> tuple[dict[str, bytes], dict[str, Any]]:
    payload, metadata = stable_read(path)
    actual = sha_bytes(payload)
    if actual != expected_sha:
        raise PreflightError(f"archive SHA mismatch: {path}: {actual}")
    members: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as stream:
        for member in stream.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise PreflightError(f"unsafe archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise PreflightError(f"non-regular archive member: {member.name}")
            if member.isfile() and member.name in anchors:
                extracted = stream.extractfile(member)
                if extracted is None:
                    raise PreflightError(f"unreadable archive member: {member.name}")
                members[member.name] = extracted.read()
    for name, expected in anchors.items():
        if name not in members or sha_bytes(members[name]) != expected:
            raise PreflightError(f"archive anchor mismatch: {name}")
    return members, {
        "path": str(path), "sha256": actual, "size_bytes": metadata.st_size,
        "anchors_verified": len(anchors),
    }


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema") != "k2_w2_server_env_contract_v1":
        raise PreflightError("contract schema mismatch")
    tech = contract["technology"]
    setup_qrc = tech["setup_qrc"]["relative_path"]
    hold_qrc = tech["hold_qrc"]["relative_path"]
    if setup_qrc != hold_qrc or Path(setup_qrc).name != "gpdk045.tch":
        raise PreflightError("setup/hold must share the single gpdk045.tch")
    if contract["corner_policy"]["setup_liberty"] == contract["corner_policy"]["hold_liberty"]:
        raise PreflightError("setup and hold Liberty must remain slow/fast distinct")
    if not contract["corner_policy"].get("shared_rc_limitation"):
        raise PreflightError("shared-RC limitation must be explicit")
    if tech["setup_liberty"].get("pvt") != [1.0, 0.9, 125.0]:
        raise PreflightError("slow setup Liberty PVT contract mismatch")
    if tech["hold_liberty"].get("pvt") != [1.0, 1.1, 0.0]:
        raise PreflightError("fast hold Liberty PVT contract mismatch")
    if tech.get("required_cells") != {
            "icg": "TLATNTSCAX2", "mux": "MX2X1",
            "posedge_ff": "DFFRHQX1", "negedge_ff": "DFFNSRX1"}:
        raise PreflightError("required mapped cell resolution mismatch")
    contracts = tech.get("cell_contracts", {})
    if set(contracts) != {"TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1"}:
        raise PreflightError("exact cell contract set mismatch")
    if contracts["TLATNTSCAX2"].get("integrated_clock_gating") != (
            "latch_posedge_precontrol"):
        raise PreflightError("ICG Liberty class mismatch")
    mapped = tech.get("mapped_inventory", {})
    if mapped.get("required_exact_counts") != {"TLATNTSCAX2": 1}:
        raise PreflightError("mapped ICG count must be exactly one TLATNTSCAX2")
    if set(mapped.get("forbidden_cell_patterns", [])) != {
            "TLATXL", "TLATNCAX2", "SDFF"}:
        raise PreflightError("mapped forbidden-cell policy mismatch")
    observation = contract.get("direct_server_observation")
    if not observation or observation.get("evidence_class") != "user_confirmed_live_shell_observation":
        raise PreflightError("direct server observation is missing")
    for role, expected in observation.get("technology_sha256", {}).items():
        if role not in tech or tech[role].get("sha256") != expected or not SHA_RE.fullmatch(expected):
            raise PreflightError(f"direct technology observation mismatch: {role}")
    if set(observation.get("technology_sha256", {})) != {
            "setup_liberty", "hold_liberty", "tech_lef", "macro_lef",
            "setup_qrc", "hold_qrc"}:
        raise PreflightError("direct technology observation is incomplete")
    for name in ("genus", "innovus", "xrun"):
        path = observation.get("tool_paths", {}).get(name)
        if not path or contract["tools"].get(name, {}).get("observed_path") != path:
            raise PreflightError(f"direct tool path observation mismatch: {name}")
        expected_sha = observation.get("tool_sha256", {}).get(name)
        if (not expected_sha or not SHA_RE.fullmatch(expected_sha) or
                contract["tools"][name].get("sha256") != expected_sha):
            raise PreflightError(f"direct tool SHA observation mismatch: {name}")
        expected_version = observation.get("tool_versions", {}).get(name)
        if not expected_version or contract["tools"][name].get("version") != expected_version:
            raise PreflightError(f"direct tool version observation mismatch: {name}")
        resolved = observation.get("tool_resolved_paths", {}).get(name)
        if not resolved or contract["tools"][name].get("resolved_path") != resolved:
            raise PreflightError(f"direct resolved tool path mismatch: {name}")
    warnings = observation.get("tool_warnings", [])
    if not any(row.get("tool") == "genus" and
               row.get("code") == "BUILD_EXPIRATION_BANNER" for row in warnings):
        raise PreflightError("Genus build-expiration warning policy is missing")


def verify_golden(contract: dict[str, Any], raw_path: Path,
                  buffered_path: Path, gates: dict[str, Any]) -> None:
    sources = contract["source_archives"]
    anchors = contract["golden_anchors"]
    raw, raw_id = archive_payloads(
        raw_path, sources["raw_core"]["sha256"], anchors["raw_core"])
    buffered, buffered_id = archive_payloads(
        buffered_path, sources["buffered_extension"]["sha256"],
        anchors["buffered_extension"])
    add_gate(gates, "source_archives", "PROVEN", {
        "raw_core": raw_id, "buffered_extension": buffered_id,
    })
    raw_genus = raw["synth/pnr/resynth_fovea_raw/genus_1.2.log"].decode()
    raw_innovus = raw["synth/pnr/resynth_fovea_raw/innovus_1.2.log"].decode()
    raw_mmmc = raw["synth/pnr/resynth_fovea_raw/mmmc_1.2.tcl"].decode()
    raw_c2_innovus = raw["synth/pnr/resynth_cluster2_raw/innovus_0.7.log"].decode()
    raw_fovea_netlist = raw[
        "synth/pnr/resynth_fovea_raw/aer_tx16_trad_rowcol_fovea_1.2_netlist.v"].decode()
    buffered_netlist = buffered[
        "synth/pnr/resynth_cluster2_buffered/aer_cluster2_buffered_1.0_netlist.v"].decode()
    buffered_innovus = buffered[
        "synth/pnr/resynth_cluster2_buffered/innovus_1.0.log"].decode()
    required = (
        ("Version: 23.14-s090_1", raw_genus),
        ("PVT values (1.000000, 0.900000, 125.000000)", raw_genus),
        ("Version:\tv23.14-s088_1", raw_innovus),
        (contract["tools"]["innovus"]["golden_executable_identity"], raw_innovus),
        ("gsclib045_tech.lef", raw_innovus),
        ("gsclib045_macro.lef", raw_innovus),
        ("gpdk045.tch", raw_mmmc),
        ("Clock gates (with test):", raw_c2_innovus),
        ("TLATNTSCAX2", raw_c2_innovus),
        ("TLATNTSCAX2", raw_fovea_netlist),
        ("TLATNTSCAX2", buffered_netlist),
        ("MX2X1", buffered_netlist),
        ("DFFRHQX1", buffered_netlist),
        ("site name: CoreSite, cell type: MX2X1", buffered_innovus),
    )
    missing = [token for token, text in required if not token or token not in text]
    if missing:
        raise PreflightError(f"golden evidence markers missing: {missing}")
    if len(re.findall(r"\bTLATNTSCAX2\b", raw_fovea_netlist)) != 1:
        raise PreflightError("raw Fovea golden must contain exactly one TLATNTSCAX2")
    if re.search(r"\b(?:TLATNCAX2|TLATXL|SDFF\w*)\s+", raw_fovea_netlist):
        raise PreflightError("raw Fovea golden contains forbidden mapped clock/scan cell")
    add_gate(gates, "golden_environment", "PROVEN_PARTIAL", {
        "genus_version": "23.14-s090_1",
        "innovus_version": "23.14-s088_1",
        "slow_pvt": [1.0, 0.9, 125.0],
        "site": "CoreSite",
        "cells": ["TLATNTSCAX2", "MX2X1", "DFFRHQX1"],
        "mapped_icg": "TLATNTSCAX2",
        "mapped_icg_raw_fovea_count": 1,
        "posedge_ff": "DFFRHQX1",
        "negedge_ff": None,
        "innovus_internal_executable": contract["tools"]["innovus"]["golden_executable_identity"],
    }, "DFFNSRX1 negative-edge evidence comes from direct live mapped/Liberty observation, not these historical golden archives")


def file_identity(path: Path, expected_sha: str | None) -> tuple[bytes, dict[str, Any]]:
    payload, metadata = stable_read(path)
    actual = sha_bytes(payload)
    if not expected_sha or not SHA_RE.fullmatch(expected_sha):
        raise PreflightError(f"un-pinned expected SHA for {path}")
    if actual != expected_sha:
        raise PreflightError(f"file SHA mismatch: {path}: {actual}")
    return payload, {"path": str(path), "sha256": actual, "size_bytes": metadata.st_size}


def named_brace_blocks(text: str, keyword: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    pattern = rf"\b{re.escape(keyword)}\s*\(\s*([^\s)]+)\s*\)\s*\{{"
    for match in re.finditer(pattern, text):
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            depth += (text[cursor] == "{") - (text[cursor] == "}")
            cursor += 1
        if depth:
            raise PreflightError(f"unterminated Liberty {keyword}: {match.group(1)}")
        if match.group(1) in blocks:
            raise PreflightError(f"duplicate Liberty {keyword}: {match.group(1)}")
        blocks[match.group(1)] = text[match.end():cursor - 1]
    return blocks


def cell_blocks(liberty: str) -> dict[str, str]:
    return named_brace_blocks(liberty, "cell")


def anonymous_brace_blocks(text: str, keyword: str) -> list[str]:
    blocks = []
    pattern = rf"\b{re.escape(keyword)}\s*(?:\([^)]*\))?\s*\{{"
    for match in re.finditer(pattern, text):
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            depth += (text[cursor] == "{") - (text[cursor] == "}")
            cursor += 1
        if depth:
            raise PreflightError(f"unterminated Liberty {keyword} group")
        blocks.append(text[match.end():cursor - 1])
    return blocks


def liberty_attribute(block: str, name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(name)}\s*:\s*(?:\"([^\"]+)\"|([^;\s]+))\s*;",
        block)
    return (match.group(1) or match.group(2)) if match else None


def normalize_logic_expression(value: str | None) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"[\s()]", "", value)
    return compact


def parse_pvt(liberty: str) -> list[float]:
    values = []
    for field in ("nom_process", "nom_voltage", "nom_temperature"):
        match = re.search(rf"\b{field}\s*:\s*([-+0-9.eE]+)\s*;", liberty)
        if not match:
            raise PreflightError(f"Liberty missing {field}")
        values.append(float(match.group(1)))
    return values


def verify_timing_contract(cell_name: str, cell: str,
                           timing_contract: dict[str, Any]) -> dict[str, Any]:
    pins = named_brace_blocks(cell, "pin")
    evidence: dict[str, Any] = {}
    for pin_name, requirement in timing_contract.items():
        if pin_name not in pins:
            raise PreflightError(f"timing pin missing in {cell_name}: {pin_name}")
        timing_blocks = anonymous_brace_blocks(pins[pin_name], "timing")
        observed = []
        for timing in timing_blocks:
            timing_type = liberty_attribute(timing, "timing_type")
            related_pin = liberty_attribute(timing, "related_pin")
            if timing_type:
                observed.append({"type": timing_type, "related_pin": related_pin})
        for timing_type in requirement["types"]:
            if not any(row["type"] == timing_type and
                       row["related_pin"] == requirement["related_pin"]
                       for row in observed):
                raise PreflightError(
                    f"required timing arc missing: {cell_name}.{pin_name} "
                    f"{timing_type} related to {requirement['related_pin']}")
        evidence[pin_name] = observed
    return evidence


def inspect_liberty(payload: bytes, expected_pvt: list[float] | None,
                    cell_contracts: dict[str, Any]) -> dict[str, Any]:
    text = payload.decode("utf-8", errors="strict")
    pvt = parse_pvt(text)
    if expected_pvt is None or pvt != [float(value) for value in expected_pvt]:
        raise PreflightError(f"Liberty PVT mismatch or unpinned: {pvt} != {expected_pvt}")
    cells = cell_blocks(text)
    evidence: dict[str, Any] = {}
    for cell_name, requirement in cell_contracts.items():
        if cell_name not in cells:
            raise PreflightError(f"required Liberty cell missing: {cell_name}")
        cell = cells[cell_name]
        pins = named_brace_blocks(cell, "pin")
        observed_pins = {
            name: liberty_attribute(block, "direction") for name, block in pins.items()}
        if observed_pins != requirement["liberty_pins"]:
            raise PreflightError(
                f"Liberty pin contract mismatch: {cell_name}: "
                f"{observed_pins} != {requirement['liberty_pins']}")
        row: dict[str, Any] = {"pins": observed_pins, "role": requirement["role"]}
        if "integrated_clock_gating" in requirement:
            observed_icg = liberty_attribute(cell, "clock_gating_integrated_cell")
            if observed_icg != requirement["integrated_clock_gating"]:
                raise PreflightError(
                    f"ICG Liberty class mismatch: {cell_name}: {observed_icg}")
            row["integrated_clock_gating"] = observed_icg
        if "ff" in requirement:
            groups = anonymous_brace_blocks(cell, "ff")
            if len(groups) != 1:
                raise PreflightError(f"expected one FF group in {cell_name}")
            observed_ff = {
                key: normalize_logic_expression(liberty_attribute(groups[0], key))
                for key in requirement["ff"]}
            expected_ff = {
                key: normalize_logic_expression(value)
                for key, value in requirement["ff"].items()}
            if observed_ff != expected_ff:
                raise PreflightError(
                    f"FF semantic mismatch: {cell_name}: {observed_ff} != {expected_ff}")
            row["ff"] = observed_ff
            row["timing"] = verify_timing_contract(
                cell_name, cell, requirement["timing"])
        evidence[cell_name] = row
    return {"pvt": pvt, "cells": evidence}


def macro_block(lef: str, name: str) -> str:
    match = re.search(rf"(?ms)^MACRO\s+{re.escape(name)}\s*$.*?^END\s+{re.escape(name)}\s*$", lef)
    if not match:
        raise PreflightError(f"required LEF macro missing: {name}")
    return match.group(0)


def lef_pin_contract(block: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for match in re.finditer(r"(?m)^\s*PIN\s+(\S+)\s*$", block):
        name = match.group(1)
        end = re.search(rf"(?m)^\s*END\s+{re.escape(name)}\s*$", block[match.end():])
        if not end:
            raise PreflightError(f"unterminated LEF pin: {name}")
        pin = block[match.end():match.end() + end.start()]
        direction = re.search(r"(?m)^\s*DIRECTION\s+(\S+)\s*;", pin)
        if not direction or name in pins:
            raise PreflightError(f"missing direction or duplicate LEF pin: {name}")
        pins[name] = direction.group(1).upper()
    return pins


def inspect_lef(tech_payload: bytes, macro_payload: bytes, site: str,
                cell_contracts: dict[str, Any]) -> dict[str, Any]:
    tech = tech_payload.decode("utf-8", errors="strict")
    macro = macro_payload.decode("utf-8", errors="strict")
    if not re.search(rf"(?m)^SITE\s+{re.escape(site)}\s*$", tech):
        raise PreflightError(f"required technology site missing: {site}")
    checked: dict[str, Any] = {}
    for cell, requirement in cell_contracts.items():
        block = macro_block(macro, cell)
        if not re.search(rf"(?m)^\s*SITE\s+{re.escape(site)}\s*;", block):
            raise PreflightError(f"macro {cell} is not legal on site {site}")
        pins = lef_pin_contract(block)
        if pins != requirement["lef_pins"]:
            raise PreflightError(
                f"LEF pin contract mismatch: {cell}: {pins} != "
                f"{requirement['lef_pins']}")
        checked[cell] = {"site": site, "pins": pins}
    return {"site": site, "site_legal_macros": checked}


def mapped_cell_instances(netlist: str, cell_name: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        rf"\b{re.escape(cell_name)}\s+(?:\\\S+|[A-Za-z_$][A-Za-z0-9_$]*)\s*"
        rf"\((.*?)\)\s*;", re.DOTALL)
    instances = []
    for match in pattern.finditer(netlist):
        ports = {
            pin: re.sub(r"\s+", "", expression)
            for pin, expression in re.findall(
                r"\.([A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*([^()]*)\s*\)",
                match.group(1))
        }
        instances.append({"ports": ports})
    return instances


def inspect_mapped_inventory(payload: bytes,
                             mapped_contract: dict[str, Any]) -> dict[str, Any]:
    netlist = payload.decode("utf-8", errors="strict")
    for pattern in mapped_contract["forbidden_cell_patterns"]:
        if re.search(
                rf"\b{re.escape(pattern)}[A-Za-z0-9_$]*\s+"
                rf"(?:\\\S+|[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", netlist):
            raise PreflightError(f"forbidden mapped cell present: {pattern}")
    inventory = {
        name: mapped_cell_instances(netlist, name)
        for name in mapped_contract["required_cells"]}
    for name in mapped_contract["required_cells"]:
        if not inventory[name]:
            raise PreflightError(f"required mapped cell absent: {name}")
    for name, count in mapped_contract["required_exact_counts"].items():
        if len(inventory[name]) != count:
            raise PreflightError(
                f"mapped exact-count mismatch: {name}: {len(inventory[name])} != {count}")

    icg = inventory["TLATNTSCAX2"][0]["ports"]
    if set(icg) != {"E", "SE", "CK", "ECK"} or icg["SE"] not in {
            "0", "1'b0", "1'h0", "1'd0"}:
        raise PreflightError("TLATNTSCAX2 mapped pins or SE=0 binding mismatch")
    for instance in inventory["DFFRHQX1"]:
        ports = instance["ports"]
        if not {"RN", "CK", "D", "Q"}.issubset(ports) or ports["RN"] != "rst_n":
            raise PreflightError("DFFRHQX1 mapped edge/reset binding mismatch")
    negedge_clocks = set()
    for instance in inventory["DFFNSRX1"]:
        ports = instance["ports"]
        if (not {"CKN", "D", "RN", "SN", "Q"}.issubset(ports) or
                ports["RN"] != "rst_n" or ports["SN"] != "1'b1" or
                ports["CKN"] in {"0", "1'b0", "1'b1", "1"}):
            raise PreflightError("DFFNSRX1 mapped CKN/RN/SN binding mismatch")
        negedge_clocks.add(ports["CKN"])
    if len(negedge_clocks) != 1:
        raise PreflightError("DFFNSRX1 instances do not share one link clock")
    for instance in inventory["MX2X1"]:
        if not {"A", "B", "S0", "Y"}.issubset(instance["ports"]):
            raise PreflightError("MX2X1 mapped pin binding mismatch")
    return {
        "cell_counts": {name: len(rows) for name, rows in inventory.items()},
        "icg_se_tied_zero": True,
        "negedge_clock_net": next(iter(negedge_clocks)),
        "negedge_reset_binding": {"RN": "rst_n", "SN": "1'b1"},
        "forbidden_cells_absent": mapped_contract["forbidden_cell_patterns"],
    }


def verify_tool(path: Path, contract_row: dict[str, Any]) -> dict[str, Any]:
    if str(path) != contract_row.get("observed_path"):
        raise PreflightError(
            f"tool path differs from direct observation: {path} != "
            f"{contract_row.get('observed_path')}")
    payload, metadata, resolved, entrypoint_kind = stable_read_tool_entrypoint(
        path, contract_row)
    expected = contract_row.get("sha256")
    if not expected or sha_bytes(payload) != expected:
        raise PreflightError(f"tool executable SHA unpinned/mismatch: {path}")
    if not os.access(path, os.X_OK):
        raise PreflightError(f"tool is not executable: {path}")
    probe = subprocess.run([str(path), "-version"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, check=False)
    if probe.returncode:
        raise PreflightError(
            f"tool version invocation failed: {path}: exit {probe.returncode}")
    versions = sorted(set(re.findall(
        r"(?<![A-Za-z0-9_.-])\d{2}\.\d{2}-s\d+(?:_\d+)?(?![A-Za-z0-9_.-])",
        probe.stdout)))
    if versions != [contract_row["version"]]:
        raise PreflightError(
            f"tool parsed version mismatch: {path}: {versions} != "
            f"{[contract_row['version']]}")
    expiration_lines = [line.strip() for line in probe.stdout.splitlines()
                        if re.search(r"expir", line, re.IGNORECASE)]
    warnings = [{
        "code": "TOOL_BANNER_EXPIRATION",
        "message": line,
        "disposition": "warning_only_after_zero_exit",
    } for line in expiration_lines]
    return {"path": str(path), "resolved_path": str(resolved),
            "entrypoint_kind": entrypoint_kind, "sha256": sha_bytes(payload),
            "size_bytes": metadata.st_size,
            "expected_version": contract_row["version"],
            "parsed_version": versions[0], "version_output": probe.stdout.strip(),
            "warnings": warnings}


def verify_server(contract: dict[str, Any], pdk_root: Path, genus: Path,
                  innovus: Path, xrun: Path, gates: dict[str, Any]) -> None:
    if str(pdk_root) != contract.get("server_pdk_root"):
        raise PreflightError(
            f"PDK root differs from contract: {pdk_root} != "
            f"{contract.get('server_pdk_root')}")
    if pdk_root.is_symlink() or not pdk_root.is_dir():
        raise PreflightError(f"PDK root missing or symlinked: {pdk_root}")
    tools = {
        "genus": verify_tool(genus, contract["tools"]["genus"]),
        "innovus": verify_tool(innovus, contract["tools"]["innovus"]),
        "xrun": verify_tool(xrun, contract["tools"]["xrun"]),
    }
    add_gate(gates, "tool_executables", "PROVEN", tools)
    tech = contract["technology"]
    payloads: dict[str, bytes] = {}
    identities: dict[str, Any] = {}
    for role in ("setup_liberty", "hold_liberty", "tech_lef", "macro_lef",
                 "setup_qrc"):
        row = tech[role]
        payloads[role], identities[role] = file_identity(
            pdk_root / row["relative_path"], row["sha256"])
    if tech["setup_qrc"] != tech["hold_qrc"]:
        raise PreflightError("shared QRC contract changed")
    identities["hold_qrc"] = identities["setup_qrc"]
    timing_dir = pdk_root / "timing"
    qrc_dir = pdk_root / "qrc/qx"
    if timing_dir.is_symlink() or not timing_dir.is_dir():
        raise PreflightError("timing directory missing or symlinked")
    if qrc_dir.is_symlink() or not qrc_dir.is_dir():
        raise PreflightError("QRC directory missing or symlinked")
    timing_entries = sorted(path.name for path in timing_dir.iterdir()
                            if path.name in tech["required_timing_directory_entries"])
    if timing_entries != sorted(tech["required_timing_directory_entries"]):
        raise PreflightError("required slow/fast timing directory entries missing")
    qrc_entries = sorted(path.name for path in qrc_dir.glob("*.tch"))
    if qrc_entries != tech["required_qrc_tch_entries"]:
        raise PreflightError(f"QRC technology set changed: {qrc_entries}")
    slow = inspect_liberty(payloads["setup_liberty"], tech["setup_liberty"]["pvt"],
                           tech["cell_contracts"])
    fast = inspect_liberty(payloads["hold_liberty"], tech["hold_liberty"]["pvt"],
                           tech["cell_contracts"])
    lef = inspect_lef(payloads["tech_lef"], payloads["macro_lef"],
                      tech["required_site"], tech["cell_contracts"])
    add_gate(gates, "technology_files", "PROVEN", identities)
    add_gate(gates, "library_semantics", "PROVEN", {
        "setup": slow, "hold": fast,
        "resolved_cells": tech["required_cells"],
        "mapped_inventory_contract": tech["mapped_inventory"]})
    add_gate(gates, "site_and_cell_availability", "PROVEN", lef)
    add_gate(gates, "rc_policy", "PROVEN_WITH_LIMITATION", {
        "setup_qrc": identities["setup_qrc"],
        "hold_qrc": identities["hold_qrc"],
        "shared_file": True,
        "limitation": contract["corner_policy"]["shared_rc_limitation"],
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--raw-archive", type=Path)
    parser.add_argument("--buffered-archive", type=Path)
    parser.add_argument("--pdk-root", type=Path)
    parser.add_argument("--genus", type=Path)
    parser.add_argument("--innovus", type=Path)
    parser.add_argument("--xrun", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-hold", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gates: dict[str, Any] = {}
    result: dict[str, Any] = {
        "schema": "k2_w2_server_env_result_v1",
        "campaign_launch_allowed": False,
        "qualification_status": "FAIL",
        "gates": gates,
    }
    try:
        contract = load_json(args.contract)
        validate_contract(contract)
        result["contract_sha256"] = sha_bytes(stable_read(args.contract)[0])
        result["direct_server_observation"] = contract["direct_server_observation"]
        add_gate(gates, "direct_server_observation", "PROVEN_DIRECT_OBSERVATION", {
            "observation_date": contract["direct_server_observation"]["observation_date"],
            "technology_sha256": contract["direct_server_observation"]["technology_sha256"],
            "tool_paths": contract["direct_server_observation"]["tool_paths"],
            "tool_sha256": contract["direct_server_observation"]["tool_sha256"],
            "tool_versions": contract["direct_server_observation"]["tool_versions"],
            "tool_warnings": contract["direct_server_observation"]["tool_warnings"],
            "cell_resolution": contract["direct_server_observation"]["cell_resolution"],
        }, "external live-shell evidence; strict preflight has not locally re-read these inputs")
        raw = args.raw_archive or Path(contract["source_archives"]["raw_core"]["default_path"])
        buffered = args.buffered_archive or Path(
            contract["source_archives"]["buffered_extension"]["default_path"])
        verify_golden(contract, raw, buffered, gates)
        server_args = (args.pdk_root, args.genus, args.innovus, args.xrun)
        if all(server_args):
            verify_server(contract, args.pdk_root, args.genus, args.innovus,
                          args.xrun, gates)
            result["qualification_status"] = "PROVEN_ENVIRONMENT"
            result["campaign_launch_allowed"] = True
            result["unresolved_environment_evidence"] = []
        elif any(server_args):
            raise PreflightError("pdk-root, genus, innovus, and xrun must be supplied together")
        else:
            add_gate(gates, "live_server_inputs", "HOLD", None,
                     "direct paths/technology SHAs are bound, but local bytes and runtime probes are unavailable")
            result["qualification_status"] = "HOLD"
            result["unresolved_environment_evidence"] = [
                "runtime invocation and exact parsed-version match for the three byte-pinned tools",
                "runtime capture of the Genus build-expiration banner warning (nonzero invocation remains FAIL)",
                "strict server re-read matching five observed technology files (six roles with shared QRC)",
                "strict Liberty semantic re-read of fast PVT and exact ICG/FF pins/arcs",
                "direct technology/macro LEF exact pins and CoreSite legality",
                "direct qrc/qx directory inventory",
            ]
        result["corner_policy"] = contract["corner_policy"]
    except (PreflightError, OSError, tarfile.TarError, UnicodeError) as error:
        result["qualification_status"] = "FAIL"
        result["failure"] = str(error)
    finalize_result(result)
    write_result(args.output, result,
                 exclusive=result["qualification_status"] == "PROVEN_ENVIRONMENT")
    if result["qualification_status"] == "PROVEN_ENVIRONMENT":
        return 0
    if result["qualification_status"] == "HOLD" and args.allow_hold:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
