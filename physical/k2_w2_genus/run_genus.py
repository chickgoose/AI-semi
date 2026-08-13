#!/usr/bin/env python3
"""Fail-closed, candidate-neutral Genus runner for the frozen K2 cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = HERE / "designs.json"
DRIVER_TCL = HERE / "genus_driver.tcl"
REQUIRED_REPORTS = (
    "check_elaborated.rpt",
    "check_mapped.rpt",
    "area.rpt",
    "qor.rpt",
    "timing.rpt",
    "clocks.rpt",
    "clock_gating.rpt",
    "power_vectorless.rpt",
)
SAFE_ATTEMPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
CELL_RE = re.compile(r"\bcell\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)")
MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.MULTILINE)
BLACKBOX_RE = re.compile(
    r"(?:\(\*[^*]*\bblackbox\b[^*]*\*\)|\bblackbox\b)", re.IGNORECASE)
INSTANCE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+"
    r"(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
SCAN_RE = re.compile(r"(?:^|_)(?:SDFF|SCAN)", re.IGNORECASE)
KEYWORDS = {"module", "if", "for", "case", "assign", "always", "function", "task"}


class FlowError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise FlowError(f"input is not a regular single-link file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            raise FlowError(f"input changed while read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def write_exclusive(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_stable(source: Path, destination: Path, mode: int = 0o444) -> str:
    payload = stable_read(source)
    write_exclusive(destination, payload, mode)
    return sha256_bytes(payload)


def git(root: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=not binary, check=False,
    )
    if result.returncode:
        error = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise FlowError(f"git {' '.join(args)} failed: {error.strip()}")
    return result.stdout


def load_registry() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(REGISTRY))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid design registry: {error}") from error
    if document.get("schema") != "k2_w2_genus_design_registry_v1":
        raise FlowError("design registry schema mismatch")
    if set(document.get("designs", {})) != {
            "a2_k2", "a3_k2", "p6_endpoint", "a2_p6", "a3_p6"}:
        raise FlowError("design registry must contain the exact five-design cohort")
    return document


def verify_flow_tree(root: Path) -> dict[str, str]:
    required = [
        "physical/k2_w2_genus/designs.json",
        "physical/k2_w2_genus/genus_driver.tcl",
        "physical/k2_w2_genus/run_genus.py",
        "physical/k2_w2_genus/filelists/a2_k2.f",
        "physical/k2_w2_genus/filelists/a3_k2.f",
        "physical/k2_w2_genus/filelists/p6_endpoint.f",
        "physical/k2_w2_genus/filelists/a2_p6.f",
        "physical/k2_w2_genus/filelists/a3_p6.f",
    ]
    for relative in required:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if tracked.returncode:
            raise FlowError(f"flow input is not tracked: {relative}")
    clean = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *required], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if clean.returncode:
        raise FlowError("flow registry/driver/runner/filelist differs from HEAD")
    return {relative: sha256_bytes(stable_read(root / relative)) for relative in required}


def verify_source_commit(root: Path, registry: dict[str, Any]) -> str:
    source_commit = registry["repository_commit"]
    head = str(git(root, "rev-parse", "HEAD")).strip()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_commit, head], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if ancestor.returncode:
        raise FlowError(f"source commit {source_commit} is not an ancestor of HEAD {head}")
    return head


def verify_design(root: Path, registry: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in registry["designs"]:
        raise FlowError(f"unknown design: {key}")
    design = registry["designs"][key]
    source_commit = registry["repository_commit"]
    filelist_path = root / design["filelist"]
    filelist_payload = stable_read(filelist_path)
    if sha256_bytes(filelist_payload) != design["filelist_sha256"]:
        raise FlowError(f"filelist SHA mismatch: {design['filelist']}")
    names = [line.strip() for line in filelist_payload.decode("utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    expected_names = [row["path"] for row in design["sources"]]
    if names != expected_names or len(names) != len(set(names)):
        raise FlowError(f"filelist/source order mismatch: {key}")
    for row in design["sources"]:
        relative = row["path"]
        working = stable_read(root / relative)
        committed = git(root, "show", f"{source_commit}:{relative}", binary=True)
        assert isinstance(committed, bytes)
        if working != committed or sha256_bytes(working) != row["sha256"]:
            raise FlowError(f"source byte mismatch: {relative}")
    if design.get("parameters") != {}:
        raise FlowError(f"unimplemented nonempty parameter map: {key}")
    if design.get("defines") != ["SYNTHESIS"]:
        raise FlowError(f"unexpected define set: {key}")
    return design


def make_sdc(registry: dict[str, Any], design: dict[str, Any]) -> bytes:
    common = registry["common_constraints"]
    period = float(common["period_ns"])
    if not math.isfinite(period) or period <= 0:
        raise FlowError("invalid common clock period")
    lines: list[str] = []
    for clock in design["clocks"]:
        rise, fall = map(float, clock["waveform_ns"])
        lines.append(
            f"create_clock -name {clock['name']} -period {period:.3f} "
            f"-waveform {{{rise:.3f} {fall:.3f}}} [get_ports {clock['port']}]"
        )
    clock_names = " ".join(clock["name"] for clock in design["clocks"])
    lines.append(
        f"set_clock_uncertainty {float(common['clock_uncertainty_ns']):.3f} "
        f"[get_clocks {{{clock_names}}}]"
    )
    reference = design["clocks"][0]["name"]
    if design["data_inputs"]:
        lines.append(
            f"set_input_delay {float(common['input_delay_ns']):.3f} -clock {reference} "
            f"[get_ports {{{' '.join(design['data_inputs'])}}}]"
        )
    reset_port = design["reset"]["port"]
    lines.append(f"set_input_delay 0.000 -clock {reference} [get_ports {reset_port}]")
    lines.append(
        f"set_output_delay {float(common['output_delay_ns']):.3f} -clock {reference} "
        f"[get_ports {{{' '.join(design['outputs'])}}}]"
    )
    lines.append(f"set_load {float(common['output_load_pf']):.3f} [all_outputs]")
    generated = design.get("generated_clock")
    if generated:
        lines.append(
            f"create_generated_clock -name {generated['name']} "
            f"-source [get_ports {generated['source_port']}] "
            f"-divide_by {int(generated['divide_by'])} "
            f"[get_ports {generated['target_port']}]"
        )
    lines.append("# No false paths, multicycle paths, or asynchronous clock grouping.")
    return ("\n".join(lines) + "\n").encode("utf-8")


def tool_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    payload = stable_read(resolved)
    if not os.access(resolved, os.X_OK):
        raise FlowError(f"tool is not executable: {resolved}")
    version = subprocess.run(
        [str(resolved), "-version"], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, check=False,
    )
    if version.returncode:
        raise FlowError(f"tool version probe failed: {resolved}")
    return {
        "requested_path": str(path),
        "resolved_path": str(resolved),
        "sha256": sha256_bytes(payload),
        "version_output": version.stdout.strip(),
    }


def mapped_inventory(mapped: Path, library: Path, expected_top: str) -> dict[str, Any]:
    mapped_payload = stable_read(mapped)
    library_payload = stable_read(library)
    text = mapped_payload.decode("utf-8", errors="strict")
    library_text = library_payload.decode("utf-8", errors="strict")
    modules = set(MODULE_RE.findall(text))
    if BLACKBOX_RE.search(text):
        raise FlowError("explicit blackbox marker in mapped netlist")
    if expected_top not in modules:
        raise FlowError(f"mapped netlist does not define expected top {expected_top}")
    library_cells = set(CELL_RE.findall(library_text))
    if not library_cells:
        raise FlowError("library contains no parseable cell declarations")
    instances: list[str] = []
    for cell in INSTANCE_RE.findall(text):
        if cell not in KEYWORDS:
            instances.append(cell)
    inventory: dict[str, int] = {}
    for cell in instances:
        if cell not in modules:
            inventory[cell] = inventory.get(cell, 0) + 1
    unknown = sorted(cell for cell in inventory if cell not in library_cells)
    if unknown:
        raise FlowError(f"unresolved/blackbox mapped cell types: {','.join(unknown)}")
    scan = sorted(cell for cell in inventory if SCAN_RE.search(cell))
    if scan:
        raise FlowError(f"scan cells are forbidden: {','.join(scan)}")
    if not inventory:
        raise FlowError("mapped netlist has zero library-cell instances")
    return {
        "mapped_netlist_sha256": sha256_bytes(mapped_payload),
        "library_cell_types_available": len(library_cells),
        "mapped_cell_count": sum(inventory.values()),
        "mapped_cell_types": dict(sorted(inventory.items())),
        "unresolved_or_blackbox_cell_types": [],
        "scan_cell_types": [],
    }


def verify_reports(output: Path, top: str, log_payload: bytes) -> dict[str, str]:
    complete = stable_read(output / "genus.complete").decode("utf-8").strip()
    if complete != f"W2_GENUS_COMPLETE top={top}":
        raise FlowError("Genus completion sentinel mismatch")
    if f"W2_GENUS_PASS top={top}" not in log_payload.decode("utf-8", errors="replace"):
        raise FlowError("Genus PASS sentinel missing from log")
    hashes: dict[str, str] = {}
    for name in REQUIRED_REPORTS:
        payload = stable_read(output / "reports" / name)
        if not payload:
            raise FlowError(f"empty Genus report: {name}")
        text = payload.decode("utf-8", errors="replace")
        if re.search(r"(^|\W)(error|fatal)(\W|$)", text, re.IGNORECASE):
            raise FlowError(f"error/fatal diagnostic in Genus report: {name}")
        if name.startswith("check_"):
            for line in text.splitlines():
                if not re.search(r"unresolved|blackbox", line, re.IGNORECASE):
                    continue
                numbers = [int(value) for value in re.findall(r"\b\d+\b", line)]
                explicitly_clean = (
                    bool(numbers) and all(value == 0 for value in numbers)
                ) or bool(re.search(
                    r"\b(no|none|zero)\b.*\b(unresolved|blackbox)\b",
                    line, re.IGNORECASE,
                ))
                if not explicitly_clean:
                    raise FlowError(f"unresolved/blackbox diagnostic in {name}: {line}")
        hashes[name] = sha256_bytes(payload)
    return hashes


def run_smoke(hook: Path | None, attempt: Path, top: str,
              mapped: Path, library: Path) -> dict[str, Any]:
    if hook is None:
        raise FlowError("mapped smoke hook is required")
    snapshot = attempt / "bundle" / "smoke_hook"
    hook_hash = copy_stable(hook.resolve(strict=True), snapshot, 0o555)
    smoke_json = attempt / "work" / "mapped_smoke.json"
    result = subprocess.run(
        [str(snapshot), "--top", top, "--netlist", str(mapped),
         "--library", str(library), "--output", str(smoke_json)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    write_exclusive(attempt / "logs" / "mapped_smoke.log", result.stdout.encode())
    if result.returncode or "W2_MAPPED_SMOKE_PASS" not in result.stdout:
        raise FlowError("mapped smoke hook failed or omitted PASS sentinel")
    try:
        document = json.loads(stable_read(smoke_json))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid mapped smoke JSON: {error}") from error
    mapped_hash = sha256_bytes(stable_read(mapped))
    library_hash = sha256_bytes(stable_read(library))
    if (document.get("schema") != "k2_w2_mapped_smoke_v1" or
            document.get("status") != "PASS" or document.get("top") != top or
            document.get("mapped_netlist_sha256") != mapped_hash or
            document.get("library_sha256") != library_hash):
        raise FlowError(
            "mapped smoke result is not bound to the mapped netlist/library/top")
    if sha256_bytes(stable_read(hook.resolve(strict=True))) != hook_hash:
        raise FlowError("mapped smoke hook changed during execution")
    return {
        "status": "PASS", "required": True, "hook_sha256": hook_hash,
        "result_sha256": sha256_bytes(stable_read(smoke_json)),
        "mapped_netlist_sha256": mapped_hash, "library_sha256": library_hash,
    }


def run_flow(root: Path, design_key: str, genus: Path, library: Path,
             output_root: Path, attempt_name: str,
             smoke_hook: Path | None) -> Path:
    root = root.resolve(strict=True)
    if not SAFE_ATTEMPT.fullmatch(attempt_name):
        raise FlowError("invalid attempt name")
    registry = load_registry()
    flow_files = verify_flow_tree(root)
    head = verify_source_commit(root, registry)
    design = verify_design(root, registry, design_key)
    attempt = output_root.resolve() / attempt_name
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "bundle" / "sources").mkdir(parents=True)
    (attempt / "work" / "reports").mkdir(parents=True)
    (attempt / "logs").mkdir(parents=True)

    tool_before = tool_identity(genus)
    library_source_hash = sha256_bytes(stable_read(library.resolve(strict=True)))
    library_snapshot = attempt / "bundle" / "library.lib"
    if copy_stable(library.resolve(strict=True), library_snapshot) != library_source_hash:
        raise FlowError("library snapshot SHA mismatch")
    source_snapshots = []
    source_paths = []
    for row in design["sources"]:
        destination = attempt / "bundle" / "sources" / row["path"]
        copied = copy_stable(root / row["path"], destination)
        if copied != row["sha256"]:
            raise FlowError(f"snapshotted source SHA mismatch: {row['path']}")
        source_snapshots.append({"path": row["path"], "sha256": copied})
        source_paths.append(str(destination))
    sdc = make_sdc(registry, design)
    sdc_path = attempt / "bundle" / "constraints.sdc"
    write_exclusive(sdc_path, sdc, 0o444)
    tcl_snapshot = attempt / "bundle" / "genus_driver.tcl"
    tcl_hash = copy_stable(DRIVER_TCL, tcl_snapshot)
    registry_hash = sha256_bytes(stable_read(REGISTRY))

    attempt_document = {
        "schema": "k2_w2_genus_attempt_v1",
        "attempt": attempt_name,
        "design": design_key,
        "top": design["top"],
        "flow_git_head": head,
        "source_commit": registry["repository_commit"],
        "registry_sha256": registry_hash,
        "flow_files_sha256": flow_files,
        "filelist_path": design["filelist"],
        "filelist_sha256": design["filelist_sha256"],
        "sources": source_snapshots,
        "defines": design["defines"],
        "parameters": design["parameters"],
        "constraints_sha256": sha256_bytes(sdc),
        "library_source_sha256": library_source_hash,
        "library_snapshot_sha256": library_source_hash,
        "genus": tool_before,
        "driver_tcl_sha256": tcl_hash,
        "genus_command": [tool_before["resolved_path"], "-batch", "-files",
                          "bundle/genus_driver.tcl"],
        "clock_gating_insertion": False,
        "scan_mapping": False,
    }
    write_exclusive(attempt / "attempt.json", canonical(attempt_document))

    environment = os.environ.copy()
    environment.update({
        "W2_TOP": design["top"],
        "W2_SOURCES": " ".join("{" + path + "}" for path in source_paths),
        "W2_DEFINES": " ".join(design["defines"]),
        "W2_LIBRARY": str(library_snapshot),
        "W2_SDC": str(sdc_path),
        "W2_OUTPUT": str(attempt / "work"),
    })
    run = subprocess.run(
        [tool_before["resolved_path"], "-batch", "-files", str(tcl_snapshot)],
        cwd=attempt, env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    write_exclusive(attempt / "logs" / "genus.log", run.stdout)
    if run.returncode:
        raise FlowError(f"Genus exited nonzero: {run.returncode}")
    tool_after = tool_identity(genus)
    if tool_after != tool_before:
        raise FlowError("Genus executable/version changed during execution")
    if sha256_bytes(stable_read(library.resolve(strict=True))) != library_source_hash:
        raise FlowError("source library changed during execution")
    report_hashes = verify_reports(attempt / "work", design["top"], run.stdout)
    inventory = mapped_inventory(
        attempt / "work" / "mapped.v", library_snapshot, design["top"])
    mapped_sdc_hash = sha256_bytes(stable_read(attempt / "work" / "mapped.sdc"))
    smoke = run_smoke(
        smoke_hook, attempt, design["top"], attempt / "work" / "mapped.v",
        library_snapshot,
    )
    receipt = {
        "schema": "k2_w2_genus_receipt_v1",
        "status": "PASS",
        "design": design_key,
        "top": design["top"],
        "attempt_sha256": sha256_bytes(stable_read(attempt / "attempt.json")),
        "mapped_inventory": inventory,
        "mapped_sdc_sha256": mapped_sdc_hash,
        "report_sha256": report_hashes,
        "mapped_smoke": smoke,
        "checks": {
            "source_and_filelist_hashes": "PASS",
            "exclusive_attempt_namespace": "PASS",
            "tool_and_library_pre_post_stability": "PASS",
            "unresolved_and_blackbox": "PASS_ZERO",
            "scan_cells": "PASS_ZERO",
            "mapped_netlist_export": "PASS",
        },
        "claim_boundary": "GENUS_MAPPED_SCREENING_ONLY_NOT_PHYSICAL_PPA",
    }
    receipt_path = attempt / "receipt.json"
    write_exclusive(receipt_path, canonical(receipt))
    return receipt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--design", required=True)
    parser.add_argument("--genus", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--mapped-smoke-hook", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = run_flow(
            args.repo_root, args.design, args.genus, args.library,
            args.output_root, args.attempt, args.mapped_smoke_hook,
        )
    except (FlowError, OSError, subprocess.SubprocessError) as error:
        print(f"K2_W2_GENUS_FAIL {error}", file=sys.stderr)
        return 2
    print(f"K2_W2_GENUS_PASS receipt={receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
