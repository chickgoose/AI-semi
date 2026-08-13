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
import tarfile
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = HERE / "designs.json"
DRIVER_TCL = HERE / "genus_driver.tcl"
GOLDEN_REFERENCE = HERE / "golden_reference.json"
RAW_GOLDEN_REFERENCE = HERE / "raw_golden_reference.json"
FUNCTIONAL_LOSS_REFERENCE = HERE / "functional_loss_reference.json"
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
    if document.get("common_constraints", {}).get("clock_gating_insertion") is not True:
        raise FlowError("design registry must use the golden clock-gating assumption")
    if document.get("evidence_cohort") != "a2_a3_k2_p6_endpoint_candidate":
        raise FlowError("endpoint candidate evidence cohort mismatch")
    return document


def load_golden_reference() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(GOLDEN_REFERENCE))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid golden reference manifest: {error}") from error
    if document.get("schema") != "k2_w2_ganghee_genus_golden_v1":
        raise FlowError("golden reference manifest schema mismatch")
    if document.get("archive_sha256") != (
            "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f"):
        raise FlowError("golden archive SHA is not the authoritative value")
    if document.get("genus_version") != "23.14-s090_1":
        raise FlowError("golden Genus version mismatch")
    if document.get("clock_gating_insertion") is not True:
        raise FlowError("golden clock-gating assumption mismatch")
    if document.get("cohort") != "buffered_ready_valid_reference":
        raise FlowError("buffered golden cohort mismatch")
    if document.get("library_path") != (
            "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/"
            "slow_vdd1v0_basicCells.lib"):
        raise FlowError("buffered golden exact library setting mismatch")
    anchors = document.get("anchors")
    if not isinstance(anchors, dict) or len(anchors) != 25:
        raise FlowError("golden anchor set must contain exactly 25 members")
    return document


def load_raw_golden_reference() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(RAW_GOLDEN_REFERENCE))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid raw golden reference manifest: {error}") from error
    if document.get("schema") != "k2_w2_ganghee_raw_genus_golden_v1":
        raise FlowError("raw golden reference manifest schema mismatch")
    if document.get("archive_sha256") != (
            "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3"):
        raise FlowError("raw golden archive SHA is not the authoritative value")
    if (document.get("cohort") != "raw_native_core_reference" or
            document.get("genus_version") != "23.14-s090_1" or
            document.get("clock_gating_insertion") is not True or
            document.get("library_basename") != "slow_vdd1v0_basicCells.lib"):
        raise FlowError("raw golden tool/library/cohort settings mismatch")
    if set(document.get("runs", {})) != {"fovea_raw", "cluster2_raw"}:
        raise FlowError("raw golden run set mismatch")
    anchors = document.get("anchors")
    if not isinstance(anchors, dict) or len(anchors) != 22:
        raise FlowError("raw golden anchor set must contain exactly 22 members")
    return document


def load_functional_loss_reference() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(FUNCTIONAL_LOSS_REFERENCE))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid functional loss reference manifest: {error}") from error
    if document.get("schema") != "k2_w2_functional_loss_reference_v1":
        raise FlowError("functional loss reference schema mismatch")
    if (document.get("archive_sha256") !=
            "22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39" or
            document.get("qualification") != "NON_OFFICIAL_WORKSPACE_DIFF" or
            document.get("claim_scope") !=
            "FULL50_GENERATED_ACCEPTED_DELIVERED_OVERRUN_ONLY_NOT_PPA"):
        raise FlowError("functional loss authority/scope mismatch")
    if document.get("excluded_artifacts") != ["eval-driver-final.log"]:
        raise FlowError("stale outer-driver exclusion mismatch")
    if set(document.get("candidates", {})) != {"fovea", "cluster2"}:
        raise FlowError("functional loss candidate set mismatch")
    if len(document.get("anchors", {})) != 10:
        raise FlowError("functional loss anchor set must contain exactly 10 members")
    return document


def verify_flow_tree(root: Path) -> dict[str, str]:
    required = [
        "physical/k2_w2_genus/designs.json",
        "physical/k2_w2_genus/golden_reference.json",
        "physical/k2_w2_genus/raw_golden_reference.json",
        "physical/k2_w2_genus/functional_loss_reference.json",
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


def require_ordered_tokens(text: str, tokens: list[str], label: str) -> None:
    cursor = 0
    for token in tokens:
        position = text.find(token, cursor)
        if position < 0:
            raise FlowError(f"{label} omits or reorders golden command: {token}")
        cursor = position + len(token)


def verify_driver_contract(golden: dict[str, Any]) -> None:
    text = stable_read(DRIVER_TCL).decode("utf-8", errors="strict")
    commands = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))
    require_ordered_tokens(commands, golden["command_order"], "candidate-neutral driver")
    if "set_db lp_insert_clock_gating true" not in commands:
        raise FlowError("candidate-neutral driver differs from golden clock-gating mode")


def read_golden_members(archive: Path, golden: dict[str, Any]) -> dict[str, bytes]:
    expected = golden["anchors"]
    payloads: dict[str, bytes] = {}
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members: dict[str, list[tarfile.TarInfo]] = {}
            for member in bundle.getmembers():
                members.setdefault(member.name, []).append(member)
            for name, identity in expected.items():
                matches = members.get(name, [])
                if len(matches) != 1 or not matches[0].isfile():
                    raise FlowError(f"golden archive member missing/duplicate/non-file: {name}")
                extracted = bundle.extractfile(matches[0])
                if extracted is None:
                    raise FlowError(f"cannot read golden archive member: {name}")
                payload = extracted.read()
                if len(payload) != identity[0] or sha256_bytes(payload) != identity[1]:
                    raise FlowError(f"golden archive member byte mismatch: {name}")
                payloads[name] = payload
    except (tarfile.TarError, OSError) as error:
        raise FlowError(f"invalid golden archive: {error}") from error
    return payloads


def verify_golden_archive(source: Path, snapshot: Path,
                          golden: dict[str, Any]) -> dict[str, Any]:
    resolved = source.resolve(strict=True)
    if resolved.name != golden["archive_filename"]:
        raise FlowError("golden archive filename mismatch; local-source substitution rejected")
    archive_hash = copy_stable(resolved, snapshot)
    if archive_hash != golden["archive_sha256"]:
        raise FlowError("golden archive SHA mismatch; local-source substitution rejected")
    payloads = read_golden_members(snapshot, golden)
    command_order = golden["command_order"]
    for family in ("fovea", "cluster2"):
        prefix = f"synth/pnr/resynth_{family}_buffered"
        tcl = payloads[f"{prefix}/genus_1.0.tcl"].decode("utf-8")
        cmd = payloads[f"{prefix}/genus_1.0.cmd"].decode("utf-8")
        log = payloads[f"{prefix}/genus_1.0.log"].decode("utf-8")
        require_ordered_tokens(tcl, command_order, f"golden {family} Tcl")
        if "set_db lp_insert_clock_gating true" not in tcl:
            raise FlowError(f"golden {family} Tcl clock-gating contract mismatch")
        if f"source {prefix}/genus_1.0.tcl" not in cmd:
            raise FlowError(f"golden {family} command transcript mismatch")
        if (f"Version: {golden['genus_version']}" not in log or
                "Error=0, Fatal=0" not in log or "Normal exit." not in log):
            raise FlowError(f"golden {family} log format/status mismatch")
        stem = f"aer_{family}_buffered_1.0"
        verify_report_payloads(
            f"aer_{family}_buffered",
            payloads[f"{prefix}/{stem}_area.rpt"],
            payloads[f"{prefix}/{stem}_gtiming.rpt"],
            payloads[f"{prefix}/{stem}_gpower.rpt"],
            label=f"golden {family}",
        )
    if sha256_bytes(stable_read(resolved)) != archive_hash:
        raise FlowError("golden archive changed during qualification")
    return {
        "cohort": golden["cohort"],
        "archive_filename": golden["archive_filename"],
        "archive_sha256": archive_hash,
        "manifest_sha256": sha256_bytes(stable_read(GOLDEN_REFERENCE)),
        "anchor_count": len(payloads),
        "anchor_sha256": {
            name: sha256_bytes(payload) for name, payload in sorted(payloads.items())
        },
        "genus_version": golden["genus_version"],
        "clock_gating_insertion": True,
        "report_format": "GANGHEE_GENUS_23P14_AREA_GTIMING_GPOWER",
    }


def verify_raw_golden_archive(source: Path, snapshot: Path,
                              golden: dict[str, Any]) -> dict[str, Any]:
    resolved = source.resolve(strict=True)
    if resolved.name != golden["archive_filename"]:
        raise FlowError("raw golden filename mismatch; local-source substitution rejected")
    archive_hash = copy_stable(resolved, snapshot)
    if archive_hash != golden["archive_sha256"]:
        raise FlowError("raw golden archive SHA mismatch; report-only/local substitution rejected")
    payloads = read_golden_members(snapshot, golden)
    for run_name, run in golden["runs"].items():
        prefix = run["prefix"]
        period = run["period"]
        top = run["top"]
        stem = f"{top}_{period}"
        tcl_name = f"{prefix}/genus_{period}.tcl"
        cmd_name = f"{prefix}/genus_{period}.cmd"
        log_name = f"{prefix}/genus_{period}.log"
        tcl = payloads[tcl_name].decode("utf-8")
        cmd = payloads[cmd_name].decode("utf-8")
        log = payloads[log_name].decode("utf-8")
        require_ordered_tokens(tcl, golden["command_order"], f"raw {run_name} Tcl")
        if (f"set LIB_FILE {golden['library_path']}" not in tcl or
                "set_db lp_insert_clock_gating true" not in tcl or
                run["read_hdl"] not in tcl):
            raise FlowError(f"raw {run_name} exact library/source settings mismatch")
        if f"source {prefix}/genus_{period}.tcl" not in cmd:
            raise FlowError(f"raw {run_name} command transcript mismatch")
        if (f"Version: {golden['genus_version']}" not in log or
                "Error=0, Fatal=0" not in log or "Normal exit." not in log):
            raise FlowError(f"raw {run_name} log format/status mismatch")
        area = payloads[f"{prefix}/{stem}_area.rpt"]
        timing = payloads[f"{prefix}/{stem}_gtiming.rpt"]
        power = payloads[f"{prefix}/{stem}_gpower.rpt"]
        verify_report_payloads(top, area, timing, power, label=f"raw {run_name}")
        netlist = payloads[f"{prefix}/{stem}_netlist.v"]
        modules = set(MODULE_RE.findall(netlist.decode("utf-8", errors="strict")))
        if top not in modules:
            raise FlowError(f"raw {run_name} netlist does not define its exact top")
        if not payloads[f"{prefix}/{stem}_out.sdc"]:
            raise FlowError(f"raw {run_name} mapped SDC is empty")
    if sha256_bytes(stable_read(resolved)) != archive_hash:
        raise FlowError("raw golden archive changed during qualification")
    return {
        "cohort": golden["cohort"],
        "archive_filename": golden["archive_filename"],
        "archive_sha256": archive_hash,
        "manifest_sha256": sha256_bytes(stable_read(RAW_GOLDEN_REFERENCE)),
        "anchor_count": len(payloads),
        "anchor_sha256": {
            name: sha256_bytes(payload) for name, payload in sorted(payloads.items())
        },
        "genus_version": golden["genus_version"],
        "library_path": golden["library_path"],
        "clock_gating_insertion": True,
        "report_format": "GANGHEE_RAW_GENUS_23P14_AREA_GTIMING_GPOWER",
        "artifact_completeness": "TCL_LOG_REPORT_NETLIST_SDC_SOURCE_COMPLETE",
    }


def verify_reference_cohort_separation(raw: dict[str, Any],
                                       buffered: dict[str, Any]) -> None:
    if raw["cohort"] == buffered["cohort"]:
        raise FlowError("raw and buffered reference cohorts collapsed")
    shared = (
        "rtl/ganghee_cluster2/arbiter2.v",
        "rtl/ganghee_cluster2/arbiter4_tree.v",
        "rtl/ganghee_cluster2/aer_tx16_trad_rowcol_fovea.v",
        "rtl/ganghee_cluster2/aer_tx16_trad_rowcol_fovea_cluster2.v",
    )
    for path in shared:
        if raw["anchor_sha256"].get(path) != buffered["anchor_sha256"].get(path):
            raise FlowError(f"raw/buffered shared native source mismatch: {path}")


def verify_functional_loss_archive(source: Path, snapshot: Path,
                                   reference: dict[str, Any]) -> dict[str, Any]:
    resolved = source.resolve(strict=True)
    if resolved.name != reference["archive_filename"]:
        raise FlowError("functional loss archive filename mismatch")
    archive_hash = copy_stable(resolved, snapshot)
    if archive_hash != reference["archive_sha256"]:
        raise FlowError("functional loss archive SHA mismatch")
    anchors = read_golden_members(snapshot, reference)
    provenance = anchors["provenance.txt"].decode("utf-8", errors="strict")
    required_provenance = (
        f"snapshot_head={reference['snapshot_head']}",
        "binding_reset_quiet_arming_patch=workspace-diff",
        f"snapshot_archive_sha256={reference['snapshot_archive_sha256']}",
        f"attempt={reference['ledger_prefix'].rstrip('/')}",
        f"TOOL:\t{reference['simulator']}",
    )
    if any(line not in provenance.splitlines() for line in required_provenance):
        raise FlowError("functional loss provenance mismatch")

    try:
        with tarfile.open(snapshot, mode="r:gz") as bundle:
            regular: dict[str, tarfile.TarInfo] = {}
            duplicates: set[str] = set()
            for member in bundle.getmembers():
                if member.name in regular:
                    duplicates.add(member.name)
                elif member.isfile():
                    regular[member.name] = member
            if duplicates:
                raise FlowError("functional loss archive contains duplicate regular members")
            if "eval-driver-final.log" in regular:
                raise FlowError("stale outer eval-driver-final.log must not be bound")
            ledger = anchors["result-artifacts.sha256"].decode("utf-8").splitlines()
            if len(ledger) != reference["ledger_entries"]:
                raise FlowError("functional loss ledger cardinality mismatch")
            seen: set[str] = set()
            for line in ledger:
                match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
                if not match or not match.group(2).startswith(reference["ledger_prefix"]):
                    raise FlowError("functional loss ledger prefix/schema mismatch")
                relative = match.group(2)[len(reference["ledger_prefix"]):]
                if (not relative.startswith("results/") or relative in seen or
                        relative not in regular):
                    raise FlowError("functional loss ledger missing/duplicate/unattached member")
                extracted = bundle.extractfile(regular[relative])
                if extracted is None or sha256_bytes(extracted.read()) != match.group(1):
                    raise FlowError(f"functional loss ledger SHA mismatch: {relative}")
                seen.add(relative)
    except (tarfile.TarError, OSError) as error:
        raise FlowError(f"invalid functional loss archive: {error}") from error

    metric_pattern = re.compile(
        r"AER_CLEAN_METRICS .*?generated=(\d+) overrun=(\d+) "
        r"accepted=(\d+) delivered=(\d+)")
    measured: dict[str, Any] = {}
    for candidate, expected in reference["candidates"].items():
        log = anchors[f"{candidate}-run.log"].decode("utf-8", errors="strict")
        rows = [tuple(map(int, match.groups())) for match in metric_pattern.finditer(log)]
        if len(rows) != expected["run_passes"] + 1:
            raise FlowError(f"functional {candidate} metric cardinality mismatch")
        full50 = tuple(map(sum, zip(*rows[:expected["run_passes"]])))
        actual = {
            "generated": full50[0], "overrun": full50[1],
            "accepted": full50[2], "delivered": full50[3],
        }
        if actual != expected["full50"]:
            raise FlowError(f"functional {candidate} full50 loss totals mismatch")
        reset = rows[-1]
        if reset != (
                expected["reset_generated"], 0, expected["reset_accepted"],
                expected["reset_delivered"]):
            raise FlowError(f"functional {candidate} reset accounting mismatch")
        if (log.count(f"RUN_PASS candidate={candidate} ") != expected["run_passes"] or
                f"CANDIDATE_COMPLETE key={candidate} pairwise_status=0" not in log or
                "AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16" not in log):
            raise FlowError(f"functional {candidate} run/reset/pairwise status mismatch")
        status = anchors[f"results/{candidate}/pairwise-cross-map.status"]
        if status != f"{expected['pairwise_status']}\n".encode("ascii"):
            raise FlowError(f"functional {candidate} pairwise artifact mismatch")
        aggregate = anchors[f"results/{candidate}/full50-nonmixed48.aggregate.json"]
        try:
            json.loads(aggregate)
        except json.JSONDecodeError as error:
            raise FlowError(f"functional {candidate} aggregate is invalid") from error
        measured[candidate] = actual
    if sha256_bytes(stable_read(resolved)) != archive_hash:
        raise FlowError("functional loss archive changed during qualification")
    return {
        "cohort": reference["cohort"],
        "qualification": reference["qualification"],
        "claim_scope": reference["claim_scope"],
        "archive_sha256": archive_hash,
        "manifest_sha256": sha256_bytes(stable_read(FUNCTIONAL_LOSS_REFERENCE)),
        "ledger": "PASS_338_OF_338_EXACT_PREFIX",
        "outer_driver_log": "EXCLUDED_STALE",
        "candidate_logs": "PASS_50_OF_50_EACH_RESET_AND_PAIRWISE",
        "full50_loss_totals": measured,
        "ppa_use": "FORBIDDEN",
    }


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


def verify_report_payloads(top: str, area_payload: bytes, timing_payload: bytes,
                           power_payload: bytes, label: str) -> None:
    try:
        area = area_payload.decode("utf-8", errors="strict")
        timing = timing_payload.decode("utf-8", errors="strict")
        power = power_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise FlowError(f"{label} report is not UTF-8 text") from error
    common = f"Generated by:           Genus(TM) Synthesis Solution"
    if (common not in area or f"Module:                 {top}" not in area or
            "Cell-Count  Cell-Area" not in area or
            not re.search(rf"(?m)^\s*{re.escape(top)}\s+NA\s+\d+\s+\d", area)):
        raise FlowError(f"{label} area report format mismatch")
    if (common not in timing or f"Module:                 {top}" not in timing or
            not re.search(r"(?m)^Path\s+1:\s+(?:MET|VIOLATED)\s+\(", timing) or
            "Slack:=" not in timing):
        raise FlowError(f"{label} timing report format mismatch")
    if (f"Instance: /{top}" not in power or "Power Unit: W" not in power or
            "Category" not in power or
            not re.search(r"(?m)^\s*Subtotal\s+\S+\s+\S+\s+\S+\s+\S+", power)):
        raise FlowError(f"{label} power report format mismatch")


def verify_reports(output: Path, top: str, log_payload: bytes,
                   expected_version: str) -> dict[str, str]:
    log = log_payload.decode("utf-8", errors="replace")
    if f"W2_GENUS_PASS top={top}" not in log:
        raise FlowError("Genus PASS sentinel missing from log")
    if (f"Version: {expected_version}" not in log or "Normal exit." not in log or
            not re.search(r"Info=\d+, Warn=\d+, Error=0, Fatal=0", log)):
        raise FlowError("Genus log lacks golden version/zero-error/normal-exit evidence")
    if re.search(r"(?mi)^\s*(?:Error|Fatal)\s*[:\[]", log):
        raise FlowError("Genus log contains an error/fatal diagnostic")
    names = {
        "area": f"{top}_area.rpt",
        "gtiming": f"{top}_gtiming.rpt",
        "gpower": f"{top}_gpower.rpt",
    }
    payloads = {kind: stable_read(output / name) for kind, name in names.items()}
    if any(not payload for payload in payloads.values()):
        raise FlowError("empty Genus report")
    verify_report_payloads(
        top, payloads["area"], payloads["gtiming"], payloads["gpower"],
        label="candidate",
    )
    return {names[kind]: sha256_bytes(payload) for kind, payload in payloads.items()}


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
             smoke_hook: Path | None, golden_archive: Path,
             raw_golden_archive: Path,
             functional_loss_archive: Path) -> Path:
    root = root.resolve(strict=True)
    if not SAFE_ATTEMPT.fullmatch(attempt_name):
        raise FlowError("invalid attempt name")
    registry = load_registry()
    golden = load_golden_reference()
    raw_golden = load_raw_golden_reference()
    functional_loss = load_functional_loss_reference()
    if (raw_golden["genus_version"] != golden["genus_version"] or
            raw_golden["library_path"] != golden["library_path"] or
            raw_golden["clock_gating_insertion"] !=
            golden["clock_gating_insertion"]):
        raise FlowError("raw and buffered golden tool/library settings differ")
    verify_driver_contract(golden)
    flow_files = verify_flow_tree(root)
    head = verify_source_commit(root, registry)
    design = verify_design(root, registry, design_key)
    attempt = output_root.resolve() / attempt_name
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "bundle" / "sources").mkdir(parents=True)
    (attempt / "work").mkdir(parents=True)
    (attempt / "logs").mkdir(parents=True)

    golden_identity = verify_golden_archive(
        golden_archive, attempt / "bundle" / golden["archive_filename"], golden)
    raw_golden_identity = verify_raw_golden_archive(
        raw_golden_archive,
        attempt / "bundle" / raw_golden["archive_filename"], raw_golden)
    verify_reference_cohort_separation(raw_golden_identity, golden_identity)
    functional_loss_identity = verify_functional_loss_archive(
        functional_loss_archive,
        attempt / "bundle" / functional_loss["archive_filename"], functional_loss)
    tool_before = tool_identity(genus)
    if golden["genus_version"] not in tool_before["version_output"]:
        raise FlowError("Genus version does not match authoritative golden archive")
    if library.resolve(strict=True).name != golden["library_basename"]:
        raise FlowError("Liberty basename does not match authoritative golden Tcl")
    library_source_hash = sha256_bytes(stable_read(library.resolve(strict=True)))
    library_snapshot = attempt / "bundle" / "library.lib"
    if copy_stable(library.resolve(strict=True), library_snapshot) != library_source_hash:
        raise FlowError("library snapshot SHA mismatch")
    source_snapshots = []
    source_paths_v = []
    source_paths_sv = []
    for row in design["sources"]:
        destination = attempt / "bundle" / "sources" / row["path"]
        copied = copy_stable(root / row["path"], destination)
        if copied != row["sha256"]:
            raise FlowError(f"snapshotted source SHA mismatch: {row['path']}")
        source_snapshots.append({"path": row["path"], "sha256": copied})
        if destination.suffix == ".v":
            source_paths_v.append(str(destination))
        elif destination.suffix == ".sv":
            source_paths_sv.append(str(destination))
        else:
            raise FlowError(f"unsupported HDL source suffix: {row['path']}")
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
        "evidence_cohorts": {
            "raw_reference": raw_golden_identity,
            "buffered_reference": golden_identity,
            "endpoint_candidate": {
                "cohort": registry["evidence_cohort"],
                "design": design_key,
                "source_commit": registry["repository_commit"],
            },
            "functional_loss_reference": functional_loss_identity,
        },
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
        "clock_gating_insertion": True,
        "scan_mapping": False,
    }
    write_exclusive(attempt / "attempt.json", canonical(attempt_document))

    environment = os.environ.copy()
    environment.update({
        "W2_TOP": design["top"],
        "W2_SOURCES_V": " ".join("{" + path + "}" for path in source_paths_v),
        "W2_SOURCES_SV": " ".join("{" + path + "}" for path in source_paths_sv),
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
    report_hashes = verify_reports(
        attempt / "work", design["top"], run.stdout, golden["genus_version"])
    inventory = mapped_inventory(
        attempt / "work" / f"{design['top']}_netlist.v",
        library_snapshot, design["top"])
    mapped_sdc_hash = sha256_bytes(stable_read(
        attempt / "work" / f"{design['top']}_out.sdc"))
    smoke = run_smoke(
        smoke_hook, attempt, design["top"],
        attempt / "work" / f"{design['top']}_netlist.v", library_snapshot,
    )
    receipt = {
        "schema": "k2_w2_genus_receipt_v1",
        "status": "PASS",
        "design": design_key,
        "top": design["top"],
        "attempt_sha256": sha256_bytes(stable_read(attempt / "attempt.json")),
        "evidence_cohorts": {
            "raw_reference": raw_golden_identity,
            "buffered_reference": golden_identity,
            "endpoint_candidate": {
                "cohort": registry["evidence_cohort"],
                "design": design_key,
                "source_commit": registry["repository_commit"],
            },
            "functional_loss_reference": functional_loss_identity,
        },
        "mapped_inventory": inventory,
        "mapped_sdc_sha256": mapped_sdc_hash,
        "report_sha256": report_hashes,
        "mapped_smoke": smoke,
        "checks": {
            "source_and_filelist_hashes": "PASS",
            "authoritative_ganghee_archive": "PASS_EXACT_SHA_AND_ANCHORS",
            "authoritative_raw_ganghee_archive": "PASS_EXACT_SHA_AND_ANCHORS",
            "raw_buffered_endpoint_cohort_separation": "PASS",
            "functional_loss_reference": "PASS_NON_OFFICIAL_WORKSPACE_DIFF_LOSS_ONLY",
            "functional_loss_used_for_ppa": "FORBIDDEN",
            "exclusive_attempt_namespace": "PASS",
            "tool_and_library_pre_post_stability": "PASS",
            "unresolved_and_blackbox": "PASS_ZERO",
            "scan_cells": "PASS_ZERO",
            "mapped_netlist_export": "PASS",
            "report_only_publication": "REJECTED_REQUIRES_SOURCE_TOOL_NETLIST_SDC_INVENTORY_SMOKE",
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
    parser.add_argument("--golden-archive", type=Path, required=True)
    parser.add_argument("--raw-golden-archive", type=Path, required=True)
    parser.add_argument("--functional-loss-archive", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--mapped-smoke-hook", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = run_flow(
            args.repo_root, args.design, args.genus, args.library,
            args.output_root, args.attempt, args.mapped_smoke_hook,
            args.golden_archive, args.raw_golden_archive,
            args.functional_loss_archive,
        )
    except (FlowError, OSError, subprocess.SubprocessError) as error:
        print(f"K2_W2_GENUS_FAIL {error}", file=sys.stderr)
        return 2
    print(f"K2_W2_GENUS_PASS receipt={receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
