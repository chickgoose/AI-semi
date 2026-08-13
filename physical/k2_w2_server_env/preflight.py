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


def write_result(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise PreflightError(f"output is a symlink: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
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
        ("Clock gates   (no test):", raw_c2_innovus),
        ("TLATNCAX2", raw_c2_innovus),
        ("MX2X1", buffered_netlist),
        ("DFF", buffered_netlist),
        ("site name: CoreSite, cell type: MX2X1", buffered_innovus),
    )
    missing = [token for token, text in required if not token or token not in text]
    if missing:
        raise PreflightError(f"golden evidence markers missing: {missing}")
    add_gate(gates, "golden_environment", "PROVEN_PARTIAL", {
        "genus_version": "23.14-s090_1",
        "innovus_version": "23.14-s088_1",
        "slow_pvt": [1.0, 0.9, 125.0],
        "site": "CoreSite",
        "cells": ["TLATNCAX2", "MX2X1"],
        "posedge_ff": True,
        "negedge_ff": None,
        "innovus_internal_executable": contract["tools"]["innovus"]["golden_executable_identity"],
    }, "negative-edge FF and byte identities of live server inputs are not present in historical golden logs")


def file_identity(path: Path, expected_sha: str | None) -> tuple[bytes, dict[str, Any]]:
    payload, metadata = stable_read(path)
    actual = sha_bytes(payload)
    if not expected_sha or not SHA_RE.fullmatch(expected_sha):
        raise PreflightError(f"un-pinned expected SHA for {path}")
    if actual != expected_sha:
        raise PreflightError(f"file SHA mismatch: {path}: {actual}")
    return payload, {"path": str(path), "sha256": actual, "size_bytes": metadata.st_size}


def cell_blocks(liberty: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in re.finditer(r"\bcell\s*\(\s*([^\s)]+)\s*\)\s*\{", liberty):
        depth = 1
        cursor = match.end()
        while cursor < len(liberty) and depth:
            depth += (liberty[cursor] == "{") - (liberty[cursor] == "}")
            cursor += 1
        if depth:
            raise PreflightError(f"unterminated Liberty cell: {match.group(1)}")
        blocks[match.group(1)] = liberty[match.end():cursor - 1]
    return blocks


def parse_pvt(liberty: str) -> list[float]:
    values = []
    for field in ("nom_process", "nom_voltage", "nom_temperature"):
        match = re.search(rf"\b{field}\s*:\s*([-+0-9.eE]+)\s*;", liberty)
        if not match:
            raise PreflightError(f"Liberty missing {field}")
        values.append(float(match.group(1)))
    return values


def inspect_liberty(payload: bytes, expected_pvt: list[float] | None,
                    required_cells: dict[str, str]) -> dict[str, Any]:
    text = payload.decode("utf-8", errors="strict")
    pvt = parse_pvt(text)
    if expected_pvt is None or pvt != [float(value) for value in expected_pvt]:
        raise PreflightError(f"Liberty PVT mismatch or unpinned: {pvt} != {expected_pvt}")
    cells = cell_blocks(text)
    for cell in required_cells.values():
        if cell not in cells:
            raise PreflightError(f"required Liberty cell missing: {cell}")
    edge_cells = {"posedge": [], "negedge": []}
    for name, block in cells.items():
        if "ff" not in block or "clocked_on" not in block:
            continue
        if re.search(r'clocked_on\s*:\s*"!?CK"', block):
            edge = "negedge" if re.search(r'clocked_on\s*:\s*"!CK"', block) else "posedge"
            edge_cells[edge].append(name)
    for edge, found in edge_cells.items():
        if not found:
            raise PreflightError(f"required {edge} FF evidence missing")
    return {"pvt": pvt, "required_cells": required_cells,
            "ff_edge_cells": {key: sorted(value) for key, value in edge_cells.items()}}


def macro_block(lef: str, name: str) -> str:
    match = re.search(rf"(?ms)^MACRO\s+{re.escape(name)}\s*$.*?^END\s+{re.escape(name)}\s*$", lef)
    if not match:
        raise PreflightError(f"required LEF macro missing: {name}")
    return match.group(0)


def inspect_lef(tech_payload: bytes, macro_payload: bytes, site: str,
                required_cells: dict[str, str], ff_cells: list[str]) -> dict[str, Any]:
    tech = tech_payload.decode("utf-8", errors="strict")
    macro = macro_payload.decode("utf-8", errors="strict")
    if not re.search(rf"(?m)^SITE\s+{re.escape(site)}\s*$", tech):
        raise PreflightError(f"required technology site missing: {site}")
    checked = []
    for cell in list(required_cells.values()) + ff_cells:
        block = macro_block(macro, cell)
        if not re.search(rf"(?m)^\s*SITE\s+{re.escape(site)}\s*;", block):
            raise PreflightError(f"macro {cell} is not legal on site {site}")
        checked.append(cell)
    return {"site": site, "site_legal_macros": checked}


def verify_tool(path: Path, contract_row: dict[str, Any]) -> dict[str, Any]:
    payload, metadata = stable_read(path)
    expected = contract_row.get("sha256")
    if not expected or sha_bytes(payload) != expected:
        raise PreflightError(f"tool executable SHA unpinned/mismatch: {path}")
    if not os.access(path, os.X_OK):
        raise PreflightError(f"tool is not executable: {path}")
    probe = subprocess.run([str(path), "-version"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, check=False)
    if probe.returncode or contract_row["version"] not in probe.stdout:
        raise PreflightError(f"tool version probe mismatch: {path}")
    return {"path": str(path), "sha256": sha_bytes(payload),
            "size_bytes": metadata.st_size, "version": contract_row["version"],
            "version_output": probe.stdout.strip()}


def verify_server(contract: dict[str, Any], pdk_root: Path, genus: Path,
                  innovus: Path, gates: dict[str, Any]) -> None:
    if pdk_root.is_symlink() or not pdk_root.is_dir():
        raise PreflightError(f"PDK root missing or symlinked: {pdk_root}")
    tools = {
        "genus": verify_tool(genus, contract["tools"]["genus"]),
        "innovus": verify_tool(innovus, contract["tools"]["innovus"]),
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
                           tech["required_cells"])
    fast = inspect_liberty(payloads["hold_liberty"], tech["hold_liberty"]["pvt"],
                           tech["required_cells"])
    shared_ff: dict[str, list[str]] = {}
    for edge in tech["required_ff_edges"]:
        shared_ff[edge] = sorted(set(slow["ff_edge_cells"][edge]) &
                                 set(fast["ff_edge_cells"][edge]))
        if not shared_ff[edge]:
            raise PreflightError(f"no common setup/hold {edge} FF cell")
    ff_cells = [shared_ff["posedge"][0], shared_ff["negedge"][0]]
    lef = inspect_lef(payloads["tech_lef"], payloads["macro_lef"],
                      tech["required_site"], tech["required_cells"], ff_cells)
    add_gate(gates, "technology_files", "PROVEN", identities)
    add_gate(gates, "library_semantics", "PROVEN", {
        "setup": slow, "hold": fast, "shared_ff_edge_cells": shared_ff})
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
        raw = args.raw_archive or Path(contract["source_archives"]["raw_core"]["default_path"])
        buffered = args.buffered_archive or Path(
            contract["source_archives"]["buffered_extension"]["default_path"])
        verify_golden(contract, raw, buffered, gates)
        server_args = (args.pdk_root, args.genus, args.innovus)
        if all(server_args):
            verify_server(contract, args.pdk_root, args.genus, args.innovus, gates)
            result["qualification_status"] = "PROVEN_ENVIRONMENT"
            result["campaign_launch_allowed"] = True
            result["unresolved_environment_evidence"] = []
        elif any(server_args):
            raise PreflightError("pdk-root, genus, and innovus must be supplied together")
        else:
            add_gate(gates, "live_server_inputs", "HOLD", None,
                     "local golden-only run: executable and technology byte evidence unavailable")
            result["qualification_status"] = "HOLD"
            result["unresolved_environment_evidence"] = [
                "Genus resolved executable path and byte SHA",
                "Innovus launcher executable byte SHA",
                "slow and fast Liberty byte SHAs",
                "fast Liberty PVT and direct posedge/negedge FF inspection",
                "technology and macro LEF byte SHAs and direct site legality",
                "gpdk045.tch byte SHA and direct qrc/qx directory inventory",
            ]
        result["corner_policy"] = contract["corner_policy"]
    except (PreflightError, OSError, tarfile.TarError, UnicodeError) as error:
        result["qualification_status"] = "FAIL"
        result["failure"] = str(error)
    write_result(args.output, result)
    if result["qualification_status"] == "PROVEN_ENVIRONMENT":
        return 0
    if result["qualification_status"] == "HOLD" and args.allow_hold:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
