#!/usr/bin/env python3
"""Fail-closed qualification of raw Genus/Innovus physical artifacts.

The parser deliberately consumes a small machine-readable trailer in each raw
report.  Human-oriented report prose is retained and hashed, but is never used
as an ambiguous source of a PASS decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


SCHEMA = "k2_physical_w2_run_manifest_v1"
RECEIPT_SCHEMA = "k2_physical_w2_qualification_receipt_v1"
EXPECTED_TOOL_VERSIONS = {
    "genus": "23.14-s090_1",
    "innovus": "23.14-s088_1",
}
REQUIRED_ARTIFACT_ROLES = {
    "rtl_filelist", "sdc", "genus_tcl", "innovus_tcl", "mmmc_tcl",
    "liberty", "tech_lef", "macro_lef", "qrc",
    "genus_log", "genus_check_design", "genus_check_timing",
    "genus_timing", "genus_mapped_netlist", "genus_scan_icg",
    "mapped_smoke", "genus_clean",
    "innovus_log", "innovus_check_timing", "innovus_timing",
    "innovus_placement", "innovus_scan_icg", "innovus_drc",
    "innovus_connectivity", "innovus_antenna",
    "innovus_postroute_netlist", "innovus_clean",
}
TEXT_REPORT_ROLES = REQUIRED_ARTIFACT_ROLES - {
    "liberty", "tech_lef", "macro_lef", "qrc",
}
TIMING_CHECKS = {"setup", "hold", "recovery", "removal"}
COVERAGE_CLASSES = {
    "unconstrained_paths", "no_clock", "no_input_delay",
    "no_output_delay", "no_drive", "no_load",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
KEY_VALUE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
ERROR_PATTERNS = (
    re.compile(r"^\s*(?:Error|Fatal)\s*:", re.I),
    re.compile(r"^\s*\*\*(?:ERROR|FATAL)\b", re.I),
    re.compile(r"^\s*\*[EF],", re.I),
    re.compile(r"^\s*(?:AER|W2)_[A-Z0-9_]*(?:ERROR|FATAL)\b"),
    re.compile(r"segmentation fault|core dumped|license checkout failed|aborted due to errors", re.I),
    re.compile(r"Message Summary:.*\b[1-9][0-9]* error\(s\)", re.I),
)


class QualificationError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def stable_read(path: Path, label: str) -> tuple[bytes, tuple[int, int, int, int, int]]:
    try:
        before_path = os.lstat(path)
    except OSError as exc:
        raise QualificationError(f"{label}: missing artifact: {exc}") from exc
    if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
        raise QualificationError(f"{label}: artifact must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise QualificationError(f"{label}: cannot open artifact: {exc}") from exc
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
    try:
        after_path = os.lstat(path)
    except OSError as exc:
        raise QualificationError(f"{label}: artifact vanished while reading") from exc
    identities = {_identity(before_path), _identity(before_fd), _identity(after_fd), _identity(after_path)}
    if len(identities) != 1:
        raise QualificationError(f"{label}: artifact changed while reading")
    data = b"".join(chunks)
    if not data:
        raise QualificationError(f"{label}: artifact is empty")
    return data, _identity(after_path)


def resolve_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise QualificationError(f"{label}: path is missing")
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_reference(root: Path, reference: Any, label: str,
                   snapshots: dict[Path, tuple[int, int, int, int, int]]) -> tuple[Path, bytes]:
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise QualificationError(f"{label}: reference must contain exactly path and sha256")
    expected = reference["sha256"]
    if not isinstance(expected, str) or HEX64.fullmatch(expected) is None:
        raise QualificationError(f"{label}: invalid SHA-256")
    # abspath normalizes dot components without dereferencing the final path.
    # Path.resolve() would hide a symlink from the lstat/O_NOFOLLOW gate below.
    path = Path(os.path.abspath(resolve_path(root, reference["path"], label)))
    if path in snapshots:
        raise QualificationError(f"{label}: duplicate artifact path")
    data, identity = stable_read(path, label)
    if sha256(data) != expected:
        raise QualificationError(f"{label}: SHA-256 mismatch")
    snapshots[path] = identity
    return path, data


def text(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QualificationError(f"{label}: report is not UTF-8") from exc


def records(data: bytes, prefix: str, label: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for line in text(data, label).splitlines():
        if not line.startswith(prefix + " "):
            continue
        fields = KEY_VALUE.findall(line[len(prefix) + 1:])
        reconstructed = " ".join(f"{key}={value}" for key, value in fields)
        if reconstructed != line[len(prefix) + 1:]:
            raise QualificationError(f"{label}: malformed {prefix} record")
        row = dict(fields)
        if len(row) != len(fields):
            raise QualificationError(f"{label}: duplicate field in {prefix} record")
        result.append(row)
    return result


def one_record(data: bytes, prefix: str, label: str,
               keys: set[str]) -> dict[str, str]:
    rows = records(data, prefix, label)
    if len(rows) != 1 or set(rows[0]) != keys:
        raise QualificationError(f"{label}: expected exactly one complete {prefix} record")
    return rows[0]


def integer(value: str, label: str) -> int:
    if re.fullmatch(r"[0-9]+", value) is None:
        raise QualificationError(f"{label}: expected a nonnegative integer")
    return int(value)


def number(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise QualificationError(f"{label}: expected a number") from exc
    if not math.isfinite(parsed):
        raise QualificationError(f"{label}: expected a finite number")
    return parsed


def reject_tool_errors(reports: dict[str, bytes]) -> dict[str, int]:
    warning_counts: dict[str, int] = {}
    for role, data in reports.items():
        lines = text(data, role).splitlines()
        for line_number, line in enumerate(lines, 1):
            if any(pattern.search(line) for pattern in ERROR_PATTERNS):
                raise QualificationError(f"{role}:{line_number}: tool error/fatal diagnostic")
        warning_counts[role] = sum(
            bool(re.search(r"(?:^|\s)(?:Warning\s*:|\*\*WARN:|WARNING\s+[A-Z])", line, re.I))
            for line in lines
        )
    return warning_counts


def parse_design(data: bytes) -> dict[str, int]:
    row = one_record(data, "W2_DESIGN", "genus_check_design",
                     {"stage", "unresolved", "blackboxes", "unmapped", "mapped_instances"})
    if row["stage"] != "genus":
        raise QualificationError("genus_check_design: incorrect stage")
    parsed = {key: integer(row[key], f"genus design {key}")
              for key in ("unresolved", "blackboxes", "unmapped", "mapped_instances")}
    if any(parsed[key] != 0 for key in ("unresolved", "blackboxes", "unmapped")):
        raise QualificationError("genus design contains unresolved, blackbox, or unmapped objects")
    if parsed["mapped_instances"] <= 0:
        raise QualificationError("genus design has no mapped instances")
    return parsed


def parse_coverage(data: bytes, stage: str, label: str) -> dict[str, int]:
    rows = records(data, "W2_COVERAGE", label)
    parsed: dict[str, int] = {}
    for row in rows:
        if set(row) != {"stage", "class", "count"} or row["stage"] != stage:
            raise QualificationError(f"{label}: malformed/stage-mismatched coverage record")
        kind = row["class"]
        if kind in parsed:
            raise QualificationError(f"{label}: duplicate coverage class {kind}")
        parsed[kind] = integer(row["count"], f"{label} {kind}")
    if set(parsed) != COVERAGE_CLASSES:
        raise QualificationError(f"{label}: coverage class inventory mismatch")
    if any(parsed.values()):
        failing = sorted(key for key, value in parsed.items() if value)
        raise QualificationError(f"{label}: nonzero constraint coverage classes: {failing}")
    return parsed


def parse_timing(data: bytes, stage: str, label: str) -> dict[str, dict[str, float | int]]:
    rows = records(data, "W2_TIMING", label)
    parsed: dict[str, dict[str, float | int]] = {}
    for row in rows:
        if set(row) != {"stage", "check", "paths", "violations", "wns", "tns"} or row["stage"] != stage:
            raise QualificationError(f"{label}: malformed/stage-mismatched timing record")
        check = row["check"]
        if check in parsed:
            raise QualificationError(f"{label}: duplicate timing check {check}")
        values: dict[str, float | int] = {
            "paths": integer(row["paths"], f"{label} {check} paths"),
            "violations": integer(row["violations"], f"{label} {check} violations"),
            "wns": number(row["wns"], f"{label} {check} wns"),
            "tns": number(row["tns"], f"{label} {check} tns"),
        }
        parsed[check] = values
    if set(parsed) != TIMING_CHECKS:
        raise QualificationError(f"{label}: timing check inventory mismatch")
    for check, values in parsed.items():
        if values["paths"] <= 0:
            raise QualificationError(f"{label}: {check} has no analyzed paths")
        if values["violations"] != 0 or values["wns"] < 0.0 or values["tns"] != 0.0:
            raise QualificationError(f"{label}: {check} timing gate failed")
    return parsed


def parse_scan_icg(data: bytes, stage: str, expected: dict[str, int], label: str) -> dict[str, Any]:
    summary = one_record(data, "W2_SCAN_ICG", label,
                         {"stage", "scan_cells", "scan_chains", "dangling_scan_pins",
                          "recognized_icg", "unrecognized_icg"})
    if summary["stage"] != stage:
        raise QualificationError(f"{label}: incorrect stage")
    counts = {key: integer(summary[key], f"{label} {key}") for key in
              ("scan_cells", "scan_chains", "dangling_scan_pins", "recognized_icg", "unrecognized_icg")}
    if any(counts[key] for key in ("scan_cells", "scan_chains", "dangling_scan_pins", "unrecognized_icg")):
        raise QualificationError(f"{label}: scan or unrecognized ICG inventory is nonzero")
    rows = records(data, "W2_ICG", label)
    observed: dict[str, int] = {}
    for row in rows:
        if set(row) != {"stage", "cell", "count"} or row["stage"] != stage:
            raise QualificationError(f"{label}: malformed ICG row")
        if row["cell"] in observed:
            raise QualificationError(f"{label}: duplicate ICG cell row")
        observed[row["cell"]] = integer(row["count"], f"{label} ICG count")
    if observed != expected or counts["recognized_icg"] != sum(expected.values()):
        raise QualificationError(f"{label}: ICG inventory mismatch")
    return {**counts, "cells": observed}


def parse_smoke(data: bytes) -> dict[str, Any]:
    row = one_record(data, "W2_MAPPED_SMOKE", "mapped_smoke",
                     {"status", "vectors", "accepted", "retired", "mismatches", "unknowns"})
    counts = {key: integer(row[key], f"mapped smoke {key}") for key in
              ("vectors", "accepted", "retired", "mismatches", "unknowns")}
    if (row["status"] != "PASS" or counts["vectors"] <= 0 or counts["accepted"] <= 0 or
            counts["accepted"] != counts["retired"] or counts["mismatches"] != 0 or
            counts["unknowns"] != 0):
        raise QualificationError("mapped functional smoke gate failed")
    return {"status": row["status"], **counts}


def parse_placement(data: bytes) -> dict[str, int]:
    row = one_record(data, "W2_PLACEMENT", "innovus_placement",
                     {"placed_instances", "unplaced_instances", "unplaced_ports", "violations"})
    parsed = {key: integer(value, f"placement {key}") for key, value in row.items()}
    if parsed["placed_instances"] <= 0 or any(parsed[key] for key in
                                               ("unplaced_instances", "unplaced_ports", "violations")):
        raise QualificationError("Innovus placement gate failed")
    return parsed


def parse_physical(data: bytes, prefix: str, label: str, keys: set[str]) -> dict[str, int]:
    row = one_record(data, prefix, label, keys)
    parsed = {key: integer(value, f"{label} {key}") for key, value in row.items()}
    if any(parsed.values()):
        raise QualificationError(f"{label}: nonzero physical violations")
    return parsed


def verify_clean(data: bytes, log: bytes, stage: str, run_id: str, top: str) -> str:
    marker = f"W2_{stage.upper()}_CLEAN_END run_id={run_id} top={top}"
    if text(data, f"{stage}_clean") != marker + "\n":
        raise QualificationError(f"{stage}: clean marker artifact mismatch")
    nonempty = [line for line in text(log, f"{stage}_log").splitlines() if line.strip()]
    if not nonempty or nonempty[-1] != marker or nonempty.count(marker) != 1:
        raise QualificationError(f"{stage}: clean marker is missing, duplicate, or not final")
    return marker


def verify_command(name: str, command: Any, tool_path: Path,
                   artifacts: dict[str, Any], top: str, run_id: str) -> dict[str, Any]:
    if not isinstance(command, dict) or set(command) != {"argv", "environment"}:
        raise QualificationError(f"{name}: command must contain exactly argv and environment")
    argv = command["argv"]
    expected_argv = ([str(tool_path), "-batch", "-files", artifacts["genus_tcl"]["path"],
                      "-log", artifacts["genus_log"]["path"]] if name == "genus" else
                     [str(tool_path), "-no_gui", "-files", artifacts["innovus_tcl"]["path"],
                      "-log", artifacts["innovus_log"]["path"]])
    if argv != expected_argv:
        raise QualificationError(f"{name}: command argv mismatch")
    expected_environment = ({
        "W2_TOP": top, "W2_RUN_ID": run_id,
        "W2_RTL_FILELIST": artifacts["rtl_filelist"]["path"],
        "W2_SDC": artifacts["sdc"]["path"],
        "W2_LIB": artifacts["liberty"]["path"],
    } if name == "genus" else {
        "W2_TOP": top, "W2_RUN_ID": run_id,
        "W2_MAPPED_NETLIST": artifacts["genus_mapped_netlist"]["path"],
        "W2_MMMC": artifacts["mmmc_tcl"]["path"],
        "W2_TECH_LEF": artifacts["tech_lef"]["path"],
        "W2_MACRO_LEF": artifacts["macro_lef"]["path"],
        "W2_QRC": artifacts["qrc"]["path"],
    })
    if command["environment"] != expected_environment:
        raise QualificationError(f"{name}: command environment mismatch")
    return {"argv": argv, "environment": command["environment"],
            "sha256": sha256(canonical(command))}


def qualify(bundle_root: Path, manifest_path: Path) -> dict[str, Any]:
    root = bundle_root.resolve()
    snapshots: dict[Path, tuple[int, int, int, int, int]] = {}
    manifest_resolved = Path(os.path.abspath(manifest_path))
    try:
        manifest_relative = manifest_resolved.relative_to(root)
    except ValueError as exc:
        raise QualificationError("manifest must be inside the artifact bundle") from exc
    manifest_data, manifest_identity = stable_read(manifest_resolved, "manifest")
    snapshots[manifest_resolved] = manifest_identity
    try:
        manifest = json.loads(manifest_data)
    except json.JSONDecodeError as exc:
        raise QualificationError(f"manifest: invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
            "schema", "run_id", "candidate", "tools", "commands", "tool_exit",
            "expected_icg_cells", "sources", "artifacts"}:
        raise QualificationError("manifest: root field inventory mismatch")
    if manifest["schema"] != SCHEMA:
        raise QualificationError("manifest: schema mismatch")
    run_id = manifest["run_id"]
    candidate = manifest["candidate"]
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        raise QualificationError("manifest: invalid run_id")
    if (not isinstance(candidate, dict) or set(candidate) != {"commit", "top"} or
            not isinstance(candidate["commit"], str) or COMMIT.fullmatch(candidate["commit"]) is None or
            not isinstance(candidate["top"], str) or
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", candidate["top"]) is None):
        raise QualificationError("manifest: invalid candidate identity")
    top = candidate["top"]

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != REQUIRED_ARTIFACT_ROLES:
        raise QualificationError("manifest: artifact role inventory mismatch")
    artifact_data: dict[str, bytes] = {}
    artifact_paths: dict[str, Path] = {}
    for role in sorted(REQUIRED_ARTIFACT_ROLES):
        path, data = read_reference(root, artifacts[role], f"artifact.{role}", snapshots)
        artifact_paths[role] = path
        artifact_data[role] = data

    sources = manifest["sources"]
    if not isinstance(sources, list) or not sources:
        raise QualificationError("manifest: sources must be a nonempty list")
    source_rows: list[dict[str, Any]] = []
    source_text = ""
    for index, reference in enumerate(sources):
        path, data = read_reference(root, reference, f"source[{index}]", snapshots)
        source_rows.append({"path": reference["path"], "sha256": sha256(data), "size": len(data)})
        source_text += text(data, f"source[{index}]")
    listed = [line.strip() for line in text(artifact_data["rtl_filelist"], "rtl_filelist").splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    if listed != [row["path"] for row in sources]:
        raise QualificationError("RTL filelist does not exactly match ordered source closure")
    if re.search(rf"\bmodule\s+{re.escape(top)}\b", source_text) is None:
        raise QualificationError("candidate top is absent from source closure")

    tools = manifest["tools"]
    if not isinstance(tools, dict) or set(tools) != set(EXPECTED_TOOL_VERSIONS):
        raise QualificationError("manifest: tool inventory mismatch")
    tool_receipt: dict[str, Any] = {}
    tool_paths: dict[str, Path] = {}
    for name, expected_version in EXPECTED_TOOL_VERSIONS.items():
        row = tools[name]
        if not isinstance(row, dict) or set(row) != {"version", "executable"} or row["version"] != expected_version:
            raise QualificationError(f"{name}: tool version mismatch")
        path, data = read_reference(root, row["executable"], f"tool.{name}.executable", snapshots)
        if not os.access(path, os.X_OK):
            raise QualificationError(f"{name}: executable is not executable")
        tool_paths[name] = path
        tool_receipt[name] = {"version": row["version"], "path": row["executable"]["path"],
                              "sha256": sha256(data)}

    exits = manifest["tool_exit"]
    if exits != {"genus": 0, "innovus": 0}:
        raise QualificationError("tool exit inventory is missing or nonzero")
    commands = manifest["commands"]
    if not isinstance(commands, dict) or set(commands) != {"genus", "innovus"}:
        raise QualificationError("manifest: command inventory mismatch")
    command_receipt = {name: verify_command(name, commands[name], tool_paths[name], artifacts,
                                             top, run_id) for name in ("genus", "innovus")}

    expected_icg = manifest["expected_icg_cells"]
    if (not isinstance(expected_icg, dict) or
            any(not isinstance(key, str) or not key or not isinstance(value, int) or
                isinstance(value, bool) or value <= 0 for key, value in expected_icg.items())):
        raise QualificationError("manifest: invalid expected ICG inventory")

    warnings = reject_tool_errors({role: artifact_data[role] for role in TEXT_REPORT_ROLES})
    design = parse_design(artifact_data["genus_check_design"])
    coverage = {
        "genus": parse_coverage(artifact_data["genus_check_timing"], "genus", "genus_check_timing"),
        "innovus": parse_coverage(artifact_data["innovus_check_timing"], "innovus", "innovus_check_timing"),
    }
    timing = {
        "genus": parse_timing(artifact_data["genus_timing"], "genus", "genus_timing"),
        "innovus": parse_timing(artifact_data["innovus_timing"], "innovus", "innovus_timing"),
    }
    scan_icg = {
        "genus": parse_scan_icg(artifact_data["genus_scan_icg"], "genus", expected_icg,
                                 "genus_scan_icg"),
        "innovus": parse_scan_icg(artifact_data["innovus_scan_icg"], "innovus", expected_icg,
                                   "innovus_scan_icg"),
    }
    smoke = parse_smoke(artifact_data["mapped_smoke"])
    placement = parse_placement(artifact_data["innovus_placement"])
    physical = {
        "drc": parse_physical(artifact_data["innovus_drc"], "W2_DRC", "innovus_drc",
                              {"violations"}),
        "connectivity": parse_physical(artifact_data["innovus_connectivity"],
                                       "W2_CONNECTIVITY", "innovus_connectivity",
                                       {"opens", "shorts", "unconnected", "violations"}),
        "antenna": parse_physical(artifact_data["innovus_antenna"], "W2_ANTENNA",
                                  "innovus_antenna", {"violations"}),
    }
    clean = {
        "genus": verify_clean(artifact_data["genus_clean"], artifact_data["genus_log"],
                              "genus", run_id, top),
        "innovus": verify_clean(artifact_data["innovus_clean"], artifact_data["innovus_log"],
                                "innovus", run_id, top),
    }

    # Re-check identities after all parsing so concurrent replacement cannot
    # race the receipt publication.
    for path, expected_identity in snapshots.items():
        try:
            current = os.lstat(path)
        except OSError as exc:
            raise QualificationError(f"artifact vanished before receipt: {path}") from exc
        if stat.S_ISLNK(current.st_mode) or _identity(current) != expected_identity:
            raise QualificationError(f"artifact changed before receipt: {path}")

    artifact_receipt = {
        role: {"path": artifacts[role]["path"], "sha256": sha256(artifact_data[role]),
               "size": len(artifact_data[role])}
        for role in sorted(REQUIRED_ARTIFACT_ROLES)
    }
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "RAW_PHYSICAL_GATES_PASS_POWER_HOLD",
        "candidate": candidate,
        "run_id": run_id,
        "provenance": {
            "manifest": {"path": manifest_relative.as_posix(), "sha256": sha256(manifest_data)},
            "tools": tool_receipt,
            "commands": command_receipt,
            "sources": source_rows,
            "artifacts": artifact_receipt,
        },
        "gates": {
            "mapped_design": design, "mapped_functional_smoke": smoke,
            "constraint_coverage": coverage, "timing": timing,
            "scan_icg": scan_icg, "placement": placement,
            "physical_verification": physical, "clean_exit": clean,
            "warning_line_counts_not_qualified_as_errors": warnings,
        },
        "claim_boundary": {
            "raw_genus_innovus_report_qualification": "GO",
            "activity_annotated_power_and_energy": "HOLD_NOT_IN_W2",
            "signoff_sta_and_foundry_signoff_drc": "HOLD_NOT_IN_W2",
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
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = qualify(args.bundle_root, args.manifest)
        write_exclusive(args.output, canonical(receipt))
    except (QualificationError, OSError) as exc:
        print(f"K2_PHYSICAL_W2_HOLD: {exc}", file=sys.stderr)
        return 1
    print(f"K2_PHYSICAL_W2_PASS run_id={receipt['run_id']} top={receipt['candidate']['top']} power=HOLD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
