#!/usr/bin/env python3
"""Qualify the authoritative 2026-08-13 Ganghee Genus/Innovus archive.

This parser intentionally reports evidence failures instead of treating an
archive named "golden" as a passing run.  The public entry point accepts only
the exact pinned tarball.  Tests exercise the raw-report parsers against copies
of the real archive members before and after focused mutations.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
from typing import Any


ARCHIVE_SHA256 = "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f"
ARCHIVE_SCHEMA = "k2_physical_w2_ganghee_golden_archive_v1"
RECEIPT_SCHEMA = "k2_physical_w2_ganghee_golden_receipt_v1"
EXPECTED_MEMBER_COUNT = 302
EXPECTED_TOOL_VERSIONS = {"genus": "23.14-s090_1", "innovus": "23.14-s088_1"}
EXPECTED_DESIGNS = {
    "fovea_buffered": {
        "directory": "synth/pnr/resynth_fovea_buffered",
        "top": "aer_fovea_buffered",
        "periods": ("0.8", "1.0", "1.2", "1.4", "1.6", "1.8", "2.0", "2.2", "2.5"),
    },
    "cluster2_buffered": {
        "directory": "synth/pnr/resynth_cluster2_buffered",
        "top": "aer_cluster2_buffered",
        "periods": ("0.8", "1.0", "1.3", "1.6", "2.0"),
    },
}
CONSTRAINT_CLASSES = (
    "unconstrained_paths", "no_clock", "no_input_delay",
    "no_output_delay", "no_drive", "no_load",
)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
ERROR_LINE = re.compile(r"^\s*(?:\*\*\s*)?(?:ERROR|FATAL)\s*:", re.I | re.M)


class GoldenQualificationError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def stable_read(path: Path) -> tuple[bytes, tuple[int, int, int, int, int]]:
    path = Path(os.path.abspath(path))
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise GoldenQualificationError(f"archive missing: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise GoldenQualificationError("archive must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise GoldenQualificationError(f"cannot open archive: {exc}") from exc
    try:
        before_fd = os.fstat(fd)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    after = os.lstat(path)
    if len({_identity(before), _identity(before_fd), _identity(after_fd), _identity(after)}) != 1:
        raise GoldenQualificationError("archive changed while reading")
    data = b"".join(chunks)
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise GoldenQualificationError("archive is empty or exceeds the size limit")
    return data, _identity(after)


def extract_members(archive_data: bytes) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise GoldenQualificationError(f"invalid gzip tar archive: {exc}") from exc
    with archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or name in ("", "."):
                raise GoldenQualificationError(f"unsafe archive member path: {name!r}")
            normalized = path.as_posix().rstrip("/")
            if member.isdir():
                continue
            if not member.isreg():
                raise GoldenQualificationError(f"non-regular archive member: {name}")
            if normalized in members:
                raise GoldenQualificationError(f"duplicate archive member: {normalized}")
            if member.size <= 0 or member.size > MAX_MEMBER_BYTES:
                raise GoldenQualificationError(f"invalid archive member size: {normalized}")
            handle = archive.extractfile(member)
            if handle is None:
                raise GoldenQualificationError(f"cannot read archive member: {normalized}")
            data = handle.read()
            if len(data) != member.size:
                raise GoldenQualificationError(f"truncated archive member: {normalized}")
            total += len(data)
            if total > MAX_ARCHIVE_BYTES:
                raise GoldenQualificationError("expanded archive exceeds the size limit")
            members[normalized] = data
    if len(members) != EXPECTED_MEMBER_COUNT:
        raise GoldenQualificationError(
            f"archive member inventory mismatch: expected {EXPECTED_MEMBER_COUNT}, got {len(members)}")
    return members


def decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldenQualificationError(f"{label}: not UTF-8") from exc


def gate(evidence: dict[str, Any], diagnostics: list[str]) -> dict[str, Any]:
    unique = sorted(set(diagnostics))
    return {"status": "PASS" if not unique else "FAIL", "diagnostics": unique, "evidence": evidence}


def required(members: dict[str, bytes], path: str, missing: list[str]) -> bytes:
    data = members.get(path)
    if data is None:
        missing.append(f"missing_artifact:{path}")
        return b""
    return data


def parse_header(data: bytes, tool: str, top: str, label: str) -> list[str]:
    if not data:
        return [f"{label}:missing"]
    value = decode(data, label)
    failures: list[str] = []
    if EXPECTED_TOOL_VERSIONS[tool] not in value:
        failures.append(f"{label}:tool_version_mismatch")
    if tool == "genus":
        if re.search(rf"^\s*Module:\s+{re.escape(top)}\s*$", value, re.M) is None:
            failures.append(f"{label}:top_mismatch")
    elif re.search(rf"^#\s*Design:\s+{re.escape(top)}\s*$", value, re.M) is None:
        failures.append(f"{label}:top_mismatch")
    return failures


def parse_slack(data: bytes, tool: str, label: str) -> tuple[dict[str, Any], list[str]]:
    if not data:
        return {}, [f"{label}:missing"]
    value = decode(data, label)
    line = re.search(r"^Path 1:\s+(MET|VIOLATED)\s+(.+)$", value, re.M)
    if line is None:
        return {}, [f"{label}:path1_missing"]
    if tool == "genus":
        slack_match = re.search(r"^\s*Slack:=\s*(-?[0-9]+)\s*$", value, re.M)
        slack_ns = None if slack_match is None else int(slack_match.group(1)) / 1000.0
    else:
        slack_match = re.search(r"^\s*=?\s*Slack Time\s+(-?[0-9]+(?:\.[0-9]+)?)\s*$", value, re.M)
        slack_ns = None if slack_match is None else float(slack_match.group(1))
    diagnostics: list[str] = []
    if slack_ns is None or not math.isfinite(slack_ns):
        diagnostics.append(f"{label}:wns_missing")
    elif slack_ns < 0.0 or line.group(1) != "MET":
        diagnostics.append(f"{label}:negative_or_violated_wns")
    return {
        "path_status": line.group(1),
        "reported_check": line.group(2).strip(),
        "wns_ns_from_worst_path": slack_ns,
    }, diagnostics


def parse_constraints(data: bytes, label: str) -> tuple[dict[str, int], list[str]]:
    if not data:
        return {}, [f"{label}:missing"]
    value = decode(data, label)
    if "TIMING CHECK SUMMARY" not in value or "TIMING CHECK DETAIL" not in value:
        return {}, [f"{label}:incomplete_summary"]
    counts = {name: 0 for name in CONSTRAINT_CLASSES}
    for name in CONSTRAINT_CLASSES:
        match = re.search(rf"\|\s*{re.escape(name)}\s*\|[^\n]*\|\s*([0-9]+)\s*\|", value)
        if match is not None:
            counts[name] = int(match.group(1))
    diagnostics = [f"{label}:{name}={count}" for name, count in counts.items() if count]
    detail = value.split("TIMING CHECK DETAIL", 1)[1]
    detail_counts = {
        "no_drive": len(re.findall(r"\|\s*No drive assertion\s*\|", detail)),
        "no_load": len(re.findall(r"\|\s*No load assertion\s*\|", detail)),
    }
    for name, detail_count in detail_counts.items():
        if counts[name] != detail_count:
            diagnostics.append(
                f"{label}:{name}_summary_detail_mismatch={counts[name]}:{detail_count}")
    return {**counts, "detail_counts": detail_counts}, diagnostics


def parse_errors(data: bytes, tool: str, label: str) -> tuple[dict[str, Any], list[str]]:
    if not data:
        return {}, [f"{label}:missing"]
    value = decode(data, label)
    severity_count = len(ERROR_LINE.findall(value))
    summary_errors: list[int] = []
    if tool == "innovus":
        summary_errors = [int(item) for item in re.findall(
            r"Message Summary:\s*[0-9]+ warning\(s\),\s*([0-9]+) error\(s\)", value)]
    diagnostics: list[str] = []
    if severity_count:
        diagnostics.append(f"{label}:severity_errors={severity_count}")
    if summary_errors and summary_errors[-1] != 0:
        diagnostics.append(f"{label}:final_summary_errors={summary_errors[-1]}")
    return {"severity_error_lines": severity_count,
            "final_summary_errors": summary_errors[-1] if summary_errors else None}, diagnostics


def parse_clean(data: bytes, tool: str, error_gate: dict[str, Any], label: str) -> tuple[dict[str, Any], list[str]]:
    if not data:
        return {}, [f"{label}:missing"]
    value = decode(data, label).rstrip()
    terminal = value.endswith("Normal exit.") if tool == "genus" else bool(
        re.search(r'--- Ending "Innovus" \([^\n]+\) ---$', value))
    error_free = error_gate["status"] == "PASS"
    diagnostics: list[str] = []
    if not terminal:
        diagnostics.append(f"{label}:terminal_marker_missing")
    if not error_free:
        diagnostics.append(f"{label}:tool_errors_preclude_clean_exit")
    return {"terminal_marker": terminal, "error_free": error_free,
            "external_process_exit_code": None}, diagnostics


def parse_scan_icg(genus_log: bytes, innovus_log: bytes, netlist: bytes,
                   label: str) -> tuple[dict[str, Any], list[str]]:
    if not genus_log or not innovus_log or not netlist:
        return {}, [f"{label}:missing_input"]
    genus = decode(genus_log, label + ".genus")
    innovus = decode(innovus_log, label + ".innovus")
    mapped = decode(netlist, label + ".netlist")
    scan_rows = [int(value) for value in re.findall(r"- Scan type\s+([0-9]+)\s", genus)]
    icg_rows = re.findall(r"ICGs:\s+([A-Za-z0-9_]+):\s*([0-9]+)", innovus)
    mapped_cells = re.findall(r"^\s*(TLATNTSCAX[0-9]+)\s+RC_CGIC_INST\b", mapped, re.M)
    mapped_inventory: dict[str, int] = {}
    for cell in mapped_cells:
        mapped_inventory[cell] = mapped_inventory.get(cell, 0) + 1
    placed_inventory = ({icg_rows[-1][0]: int(icg_rows[-1][1])} if icg_rows else {})
    no_scan_chain = "No scan chain specified/traced." in innovus
    diagnostics: list[str] = []
    if not scan_rows or any(scan_rows) or not no_scan_chain:
        diagnostics.append(f"{label}:scan_inventory_incomplete_or_nonzero")
    if not mapped_inventory or mapped_inventory != placed_inventory:
        diagnostics.append(f"{label}:icg_inventory_mismatch")
    return {"genus_scan_type_counts": scan_rows, "innovus_no_scan_chain": no_scan_chain,
            "mapped_icg_cells": mapped_inventory, "placed_icg_cells": placed_inventory}, diagnostics


def parse_zero_report(data: bytes, kind: str, label: str) -> tuple[dict[str, Any], list[str]]:
    if not data:
        return {}, [f"{label}:missing"]
    value = decode(data, label)
    if kind == "drc":
        sentinel = "No DRC violations were found"
        explicit_bad = re.search(r"(?:DRC\s+violations\s+were\s+found\s*:\s*[1-9]|"
                                 r"(?:violation|error)\s+count\s*[:=]\s*[1-9])", value, re.I)
    elif kind == "antenna":
        sentinel = "No Violations Found"
        explicit_bad = re.search(r"(?:Violations\s+Found\s*:\s*[1-9]|"
                                 r"(?:violation|error)\s+count\s*[:=]\s*[1-9])", value, re.I)
    else:
        raise AssertionError(kind)
    count = value.count(sentinel)
    clean = count == 1 and explicit_bad is None
    return {"zero_violations": clean, "zero_summary_count": count,
            "explicit_nonzero_summary": explicit_bad is not None}, \
        [] if clean else [f"{label}:zero_summary_missing_or_contradicted"]


def analyze_period(members: dict[str, bytes], design_name: str, config: dict[str, Any],
                   period: str) -> dict[str, Any]:
    directory = config["directory"]
    top = config["top"]
    missing: list[str] = []

    def item(name: str) -> bytes:
        return required(members, f"{directory}/{name}", missing)

    files = {
        "sdc": item(f"{top}_{period}.sdc"),
        "genus_tcl": item(f"genus_{period}.tcl"),
        "genus_cmd": item(f"genus_{period}.cmd"),
        "genus_log": item(f"genus_{period}.log"),
        "genus_timing": item(f"{top}_{period}_gtiming.rpt"),
        "mapped_netlist": item(f"{top}_{period}_netlist.v"),
        "mmmc_tcl": item(f"mmmc_{period}.tcl"),
        "innovus_tcl": item(f"run_{period}.tcl"),
        "innovus_cmd": item(f"innovus_{period}.cmd"),
        "innovus_log": item(f"innovus_{period}.log"),
        "setup_timing": item(f"{top}_{period}_setup_timing.rpt"),
        "hold_timing": item(f"{top}_{period}_hold_timing.rpt"),
        "check_timing": item(f"{top}_{period}_check_timing.rpt"),
        "drc": item(f"{top}_{period}_drc.rpt"),
        "antenna": item(f"{top}_{period}_antenna.rpt"),
    }
    provenance_diagnostics = list(missing)
    for role, tool in (("genus_timing", "genus"), ("setup_timing", "innovus"),
                       ("hold_timing", "innovus"), ("check_timing", "innovus"),
                       ("drc", "innovus"), ("antenna", "innovus")):
        provenance_diagnostics.extend(parse_header(files[role], tool, top, role))
    sdc = decode(files["sdc"], "sdc") if files["sdc"] else ""
    if re.search(rf"create_clock\s+-name\s+clk\s+-period\s+{re.escape(period)}\b", sdc) is None:
        provenance_diagnostics.append("sdc:period_mismatch")
    # Commands and absolute dependency names are archived, but the executable,
    # Liberty/LEF/QRC bytes, process exit codes, and post-route netlist are not.
    provenance_diagnostics.extend((
        "tool_executables_unhashed", "technology_inputs_unhashed",
        "process_exit_codes_unattached", "postroute_netlist_missing",
    ))
    provenance = gate({
        "top": top, "period_ns": float(period),
        "archived_command_records": bool(files["genus_cmd"] and files["innovus_cmd"]),
        "archived_flow_scripts": bool(files["genus_tcl"] and files["innovus_tcl"] and files["mmmc_tcl"]),
    }, provenance_diagnostics)

    genus_error_evidence, genus_error_diag = parse_errors(files["genus_log"], "genus", "genus_log")
    innovus_error_evidence, innovus_error_diag = parse_errors(files["innovus_log"], "innovus", "innovus_log")
    genus_errors = gate(genus_error_evidence, genus_error_diag)
    innovus_errors = gate(innovus_error_evidence, innovus_error_diag)

    genus_timing_evidence, genus_timing_diag = parse_slack(files["genus_timing"], "genus", "genus_timing")
    setup_evidence, setup_diag = parse_slack(files["setup_timing"], "innovus", "innovus_setup")
    hold_evidence, hold_diag = parse_slack(files["hold_timing"], "innovus", "innovus_early")
    timing_diag = genus_timing_diag + setup_diag + hold_diag + [
        "tns_not_reported", "violation_count_not_reported",
        "recovery_removal_not_independently_covered",
    ]
    timing = gate({"genus_setup": genus_timing_evidence, "innovus_late": setup_evidence,
                   "innovus_early": hold_evidence, "tns": None, "violation_count": None}, timing_diag)

    constraint_evidence, constraint_diag = parse_constraints(files["check_timing"], "innovus_check_timing")
    constraints = gate(constraint_evidence, constraint_diag)
    scan_evidence, scan_diag = parse_scan_icg(files["genus_log"], files["innovus_log"],
                                               files["mapped_netlist"], "scan_icg")
    scan_icg = gate(scan_evidence, scan_diag)
    drc_evidence, drc_diag = parse_zero_report(files["drc"], "drc", "innovus_drc")
    antenna_evidence, antenna_diag = parse_zero_report(files["antenna"], "antenna", "innovus_antenna")
    drc = gate(drc_evidence, drc_diag)
    antenna = gate(antenna_evidence, antenna_diag)
    connectivity = gate({"report_present": False}, ["connectivity_report_missing"])
    genus_clean_evidence, genus_clean_diag = parse_clean(files["genus_log"], "genus", genus_errors, "genus_clean")
    innovus_clean_evidence, innovus_clean_diag = parse_clean(files["innovus_log"], "innovus", innovus_errors,
                                                              "innovus_clean")
    clean_exit = gate({"genus": genus_clean_evidence, "innovus": innovus_clean_evidence},
                      genus_clean_diag + innovus_clean_diag + ["external_process_exit_codes_unattached"])
    gates = {
        "provenance": provenance, "genus_errors": genus_errors, "innovus_errors": innovus_errors,
        "timing": timing, "constraint_coverage": constraints, "scan_icg": scan_icg,
        "drc": drc, "connectivity": connectivity, "antenna": antenna, "clean_exit": clean_exit,
    }
    failed = sorted(name for name, result in gates.items() if result["status"] != "PASS")
    return {"design": design_name, "top": top, "period_ns": float(period),
            "status": "PASS" if not failed else "FAIL", "failed_gates": failed, "gates": gates}


def analyze_members(members: dict[str, bytes]) -> dict[str, Any]:
    periods: dict[str, dict[str, Any]] = {}
    for design_name, config in EXPECTED_DESIGNS.items():
        for period in config["periods"]:
            key = f"{design_name}@{period}ns"
            periods[key] = analyze_period(members, design_name, config, period)
    passed = sum(result["status"] == "PASS" for result in periods.values())
    return {"periods": periods, "summary": {"period_count": len(periods), "pass": passed,
                                               "fail": len(periods) - passed}}


def qualify_archive(archive_path: Path) -> dict[str, Any]:
    path = Path(os.path.abspath(archive_path))
    archive_data, identity = stable_read(path)
    actual_sha = sha256(archive_data)
    if actual_sha != ARCHIVE_SHA256:
        raise GoldenQualificationError(
            f"authoritative archive SHA-256 mismatch: expected {ARCHIVE_SHA256}, got {actual_sha}")
    members = extract_members(archive_data)
    analysis = analyze_members(members)
    current = os.lstat(path)
    if stat.S_ISLNK(current.st_mode) or _identity(current) != identity:
        raise GoldenQualificationError("archive changed before receipt publication")
    inventory = [{"path": name, "sha256": sha256(data), "size": len(data)}
                 for name, data in sorted(members.items())]
    status = "AUTHORITATIVE_RAW_FIXTURE_PASS" if analysis["summary"]["fail"] == 0 else \
        "AUTHORITATIVE_RAW_FIXTURE_FAIL"
    return {
        "schema": RECEIPT_SCHEMA,
        "fixture_schema": ARCHIVE_SCHEMA,
        "status": status,
        "archive": {"path": "ganghee-pnr-golden-20260813.tar.gz",
                    "sha256": actual_sha, "size": len(archive_data),
                    "member_count": len(members), "member_inventory": inventory,
                    "member_inventory_sha256": sha256(canonical(inventory))},
        "tools_observed": EXPECTED_TOOL_VERSIONS,
        **analysis,
        "claim_boundary": {
            "actual_raw_archive_interpretation": "GO",
            "physical_campaign_qualification": "GO" if analysis["summary"]["fail"] == 0 else "HOLD",
            "activity_annotated_power_and_energy": "HOLD_NOT_QUALIFIED",
            "signoff": "HOLD_NOT_QUALIFIED",
        },
    }


def write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = qualify_archive(args.archive)
        write_exclusive(args.output, canonical(receipt))
    except (GoldenQualificationError, OSError) as exc:
        print(f"K2_PHYSICAL_W2_GOLDEN_ERROR: {exc}", file=sys.stderr)
        return 1
    if receipt["summary"]["fail"]:
        print(f"K2_PHYSICAL_W2_GOLDEN_HOLD periods={receipt['summary']['period_count']} "
              f"failed={receipt['summary']['fail']} receipt={args.output}", file=sys.stderr)
        return 2
    print(f"K2_PHYSICAL_W2_GOLDEN_PASS periods={receipt['summary']['period_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
