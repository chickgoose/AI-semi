#!/usr/bin/env python3
"""Pinned actual-RTL replay for the A2/A3 single-edge endpoints."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT / "tests/a23_full_single_edge_replay"
PINS = PACKAGE / "pins.json"
TB = PACKAGE / "tb/a23_full_single_edge_replay_tb.sv"
RUN_ALL = PACKAGE / "run_all.sh"
GENERATOR = PROJECT / "benchmarks/clean_slate_aer/generate_trace.py"
PREPARER = PROJECT / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
OFFICIAL = PROJECT / "scripts/common_suite_official.py"
FULL_MANIFEST = PROJECT / "tests/common_suite_receipt/fixtures/manifest.neutrality-n16.json"
DEFAULT_VERILATOR = Path("/tmp/a7-toolchain/usr/bin/verilator")
MUTATION_NAMES = ("drop", "duplicate", "reorder", "reset_escape")
DIAGNOSTIC_RE = re.compile(r"A23_SE_[A-Z0-9_]+_(?:FAIL|PASS)")

sys.path.insert(0, str(PROJECT / "scripts"))
import common_suite_official as official  # noqa: E402


class ReplayError(RuntimeError):
    """A hard replay or evidence-integrity failure."""


class ReplayUnavailable(RuntimeError):
    """An integration prerequisite is absent or deliberately not locked."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT.resolve()))
    except ValueError as error:
        raise ReplayError(f"path escapes repository: {path}") from error


def run(
    command: list[str], *, cwd: Path, log: Path,
    expect_success: bool = True, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command, cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False, env=env,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if expect_success and process.returncode:
        raise ReplayError(
            f"command failed exit={process.returncode}: {' '.join(command)}\n"
            f"{process.stdout[-4000:]}"
        )
    return process


def load_document() -> dict[str, Any]:
    try:
        document = json.loads(PINS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReplayError(f"cannot load pins: {error}") from error
    if document.get("schema") != "a23_full_single_edge_replay_pins_v1":
        raise ReplayError("pin schema mismatch")
    if set(document.get("owners", {})) != {"a2", "a3"}:
        raise ReplayError("owner roster must be exactly a2/a3")
    return document


def require_integration_paths(document: dict[str, Any]) -> None:
    missing: list[str] = []
    for owner, config in document["owners"].items():
        for key in ("top", "filelist", "mutation_target"):
            value = config.get(key)
            if not isinstance(value, str) or not value:
                raise ReplayError(f"invalid {owner}/{key} path contract")
            if not (PROJECT / value).is_file():
                missing.append(value)
    if missing:
        raise ReplayUnavailable(
            "missing actual single-edge RTL paths: " + ", ".join(sorted(set(missing)))
        )


def filelist_sources(relative_filelist: str) -> tuple[list[Path], list[Path]]:
    sources: list[Path] = []
    filelists: list[Path] = []
    active: set[Path] = set()

    def expand(filelist: Path) -> None:
        resolved_filelist = filelist.resolve()
        relative(resolved_filelist)
        if resolved_filelist in active:
            raise ReplayError(f"recursive actual RTL filelist: {relative(filelist)}")
        if resolved_filelist in {path.resolve() for path in filelists}:
            return
        if not filelist.is_file():
            raise ReplayUnavailable(f"missing actual RTL filelist: {relative(filelist)}")
        active.add(resolved_filelist)
        filelists.append(filelist)
        for line_number, line in enumerate(
            filelist.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("-f "):
                nested = stripped[3:].strip()
                if not nested or any(char.isspace() for char in nested):
                    raise ReplayError(
                        f"invalid nested filelist: {relative(filelist)}:{line_number}"
                    )
                expand(PROJECT / nested)
                continue
            if stripped.startswith(("+", "-")) or any(
                char.isspace() for char in stripped
            ):
                raise ReplayError(
                    f"filelist must contain one repository-relative RTL path per line: "
                    f"{relative(filelist)}:{line_number}"
                )
            candidate = PROJECT / stripped
            relative(candidate)
            if not candidate.is_file():
                raise ReplayUnavailable(
                    f"missing actual RTL filelist member: {relative(filelist)} -> {stripped}"
                )
            sources.append(candidate)
        active.remove(resolved_filelist)

    expand(PROJECT / relative_filelist)
    if not sources:
        raise ReplayError(f"actual RTL filelist is empty: {relative_filelist}")
    if len({path.resolve() for path in sources}) != len(sources):
        raise ReplayError(f"actual RTL filelist contains duplicate sources: {relative_filelist}")
    return sources, filelists


def tool_version(path: Path, args: list[str]) -> str:
    process = subprocess.run(
        [str(path), *args], cwd=PROJECT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if process.returncode:
        raise ReplayUnavailable(f"cannot execute pinned tool: {path}")
    return process.stdout.splitlines()[0].strip()


def verify_tools(document: dict[str, Any], verilator: Path) -> dict[str, Any]:
    configured = document.get("tools", {})
    role_paths = {
        "python": Path(sys.executable).resolve(),
        "verilator": verilator.resolve(),
        "verilator_bin": verilator.resolve().with_name("verilator_bin"),
        "make": Path(shutil.which("make") or "/missing/make").resolve(),
        "cxx": Path(shutil.which("g++") or "/missing/g++").resolve(),
    }
    verified: dict[str, Any] = {}
    for role, actual in role_paths.items():
        expected = configured.get(role)
        if not actual.is_file():
            raise ReplayUnavailable(f"missing required tool role={role} path={actual}")
        if not isinstance(expected, dict):
            raise ReplayUnavailable(f"tool role is not pinned: {role}")
        if str(actual) != expected.get("path"):
            raise ReplayUnavailable(
                f"tool path mismatch role={role} expected={expected.get('path')} actual={actual}"
            )
        actual_sha = sha256(actual)
        if actual_sha != expected.get("sha256"):
            raise ReplayUnavailable(f"tool SHA-256 mismatch role={role} path={actual}")
        version_args = expected.get("version_args")
        if not isinstance(version_args, list) or not all(
            isinstance(value, str) for value in version_args
        ):
            raise ReplayError(f"invalid version_args for tool role={role}")
        actual_version = tool_version(actual, version_args)
        if actual_version != expected.get("version"):
            raise ReplayUnavailable(f"tool version mismatch role={role}: {actual_version}")
        verified[role] = {
            "path": str(actual), "sha256": actual_sha, "version": actual_version,
        }
    return verified


def verify_file_pins(
    document: dict[str, Any], sources_by_owner: dict[str, list[Path]],
    filelists_by_owner: dict[str, list[Path]],
) -> dict[str, str]:
    pinned = document.get("files", {})
    required = {
        relative(TB), relative(Path(__file__)), relative(RUN_ALL),
        relative(PACKAGE / "README.md"),
        relative(PACKAGE / "test_contract.py"),
        relative(GENERATOR), relative(PREPARER), relative(OFFICIAL),
        relative(FULL_MANIFEST),
    }
    for owner, config in document["owners"].items():
        required.update({config["top"], config["filelist"], config["wrapper"],
                         config["mutation_target"], config["scheduler"]})
        required.update(relative(path) for path in sources_by_owner[owner])
        required.update(relative(path) for path in filelists_by_owner[owner])
    missing_pins = sorted(required - set(pinned))
    unlocked = sorted(
        path for path in required
        if not isinstance(pinned.get(path), str) or
        re.fullmatch(r"[0-9a-f]{64}", pinned.get(path, "")) is None
    )
    if missing_pins or unlocked:
        details = []
        if missing_pins:
            details.append(f"absent={missing_pins}")
        if unlocked:
            details.append(f"unlocked={unlocked}")
        raise ReplayUnavailable("actual replay file pins incomplete: " + " ".join(details))
    verified: dict[str, str] = {}
    for path_string in sorted(required):
        path = PROJECT / path_string
        if not path.is_file():
            raise ReplayUnavailable(f"pinned replay input is missing: {path_string}")
        actual = sha256(path)
        if actual != pinned[path_string]:
            raise ReplayUnavailable(f"replay input pin mismatch: {path_string}")
        verified[path_string] = actual
    return verified


def verify_mutation_contract(
    document: dict[str, Any], sources_by_owner: dict[str, list[Path]],
) -> None:
    mutations = document.get("mutations", {})
    anchor_pins = document.get("mutation_anchor_sha256", {})
    target_pin = anchor_pins.get("target_sha256")
    if not isinstance(target_pin, str) or re.fullmatch(r"[0-9a-f]{64}", target_pin) is None:
        raise ReplayUnavailable("mutation target SHA-256 is not locked")
    if set(mutations) != {"a2", "a3"}:
        raise ReplayError("mutation owner roster mismatch")
    for owner in ("a2", "a3"):
        if tuple(mutations[owner]) != MUTATION_NAMES:
            raise ReplayError(f"mutation roster/order mismatch for {owner}")
        allowed = {relative(path) for path in sources_by_owner[owner]}
        for name, spec in mutations[owner].items():
            if spec.get("target") not in allowed:
                raise ReplayUnavailable(
                    f"{owner}/{name} target is not actual endpoint RTL from its filelist"
                )
            for field in ("old", "new"):
                value = spec.get(field)
                if not isinstance(value, str) or not value:
                    raise ReplayUnavailable(
                        f"literal RTL mutation anchor is not locked: {owner}/{name}/{field}"
                    )
            if spec["old"] == spec["new"]:
                raise ReplayError(f"mutation is a no-op: {owner}/{name}")
            target_text = (PROJECT / spec["target"]).read_text(encoding="utf-8")
            if sha256(PROJECT / spec["target"]) != target_pin:
                raise ReplayUnavailable(f"mutation target SHA-256 mismatch: {owner}/{name}")
            if target_text.count(spec["old"]) != 1:
                raise ReplayUnavailable(
                    f"literal mutation anchor count is not one: {owner}/{name}"
                )
            expected_anchors = anchor_pins.get(name, {})
            for field in ("old", "new"):
                actual = hashlib.sha256(spec[field].encode()).hexdigest()
                if actual != expected_anchors.get(field):
                    raise ReplayUnavailable(
                        f"literal mutation anchor SHA-256 mismatch: {owner}/{name}/{field}"
                    )


def verify_rtl_git_provenance(
    document: dict[str, Any], verified_files: dict[str, str],
) -> dict[str, Any]:
    provenance = document.get("rtl_provenance")
    if not isinstance(provenance, dict):
        raise ReplayUnavailable("actual RTL Git provenance is not pinned")
    source_commit = provenance.get("source_commit")
    integration_commit = provenance.get("integration_commit")
    source_tree = provenance.get("source_tree")
    integration_tree = provenance.get("integration_tree")
    for label, value in (
        ("source_commit", source_commit), ("integration_commit", integration_commit),
        ("source_tree", source_tree), ("integration_tree", integration_tree),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ReplayUnavailable(f"invalid actual RTL Git pin: {label}")
    trees: dict[str, str] = {}
    for label, commit, expected_tree in (
        ("source_commit", source_commit, source_tree),
        ("integration_commit", integration_commit, integration_tree),
    ):
        process = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=PROJECT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if process.returncode:
            raise ReplayUnavailable(f"actual RTL Git object is unavailable: {label}")
        trees[label] = process.stdout.strip()
        if trees[label] != expected_tree:
            raise ReplayUnavailable(f"actual RTL Git tree mismatch: {label}")

    rtl_paths = sorted(path for path in verified_files if path.startswith("rtl/"))
    for path in rtl_paths:
        for label, commit in (
            ("source_commit", source_commit),
            ("integration_commit", integration_commit),
        ):
            process = subprocess.run(
                ["git", "show", f"{commit}:{path}"], cwd=PROJECT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if process.returncode:
                raise ReplayUnavailable(f"{label} lacks pinned actual RTL path: {path}")
            commit_sha = hashlib.sha256(process.stdout).hexdigest()
            if commit_sha != verified_files[path]:
                raise ReplayUnavailable(f"working RTL differs from {label}: {path}")
    return {
        "source_commit": source_commit,
        "integration_commit": integration_commit,
        "source_tree": source_tree,
        "integration_tree": integration_tree,
        "verified_rtl_paths": rtl_paths,
    }


def verify_clean_tracked(required_paths: Iterable[str]) -> str:
    selected = sorted(set(required_paths) | {relative(PINS)})
    for path in selected:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path], cwd=PROJECT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if tracked.returncode:
            raise ReplayUnavailable(f"replay input is not tracked by Git: {path}")
    changed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *selected],
        cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if changed:
        raise ReplayUnavailable("replay inputs must be byte-clean against HEAD")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def validate_integration(
    document: dict[str, Any], verilator: Path, *, allow_dirty: bool,
) -> tuple[
    dict[str, list[Path]], dict[str, str], dict[str, Any], dict[str, Any], str,
]:
    require_integration_paths(document)
    expanded = {
        owner: filelist_sources(config["filelist"])
        for owner, config in document["owners"].items()
    }
    sources = {owner: value[0] for owner, value in expanded.items()}
    filelists = {owner: value[1] for owner, value in expanded.items()}
    verified_files = verify_file_pins(document, sources, filelists)
    verify_mutation_contract(document, sources)
    verified_rtl_git = verify_rtl_git_provenance(document, verified_files)
    verified_tools = verify_tools(document, verilator)
    if allow_dirty:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()
    else:
        commit = verify_clean_tracked(verified_files)
    return sources, verified_files, verified_tools, verified_rtl_git, commit


def prepare_traces(work: Path) -> dict[str, Path]:
    traces = work / "generator-v4"
    run(
        [sys.executable, str(GENERATOR), "--manifest", str(FULL_MANIFEST),
         "--output-dir", str(traces)],
        cwd=PROJECT, log=work / "logs/generator-v4.log",
    )
    prepared: dict[str, Path] = {}
    for name in official.FULL50:
        event_path = traces / f"{name}.events.jsonl"
        manifest_path = traces / f"{name}.manifest.json"
        if sha256(event_path) != official.TRACE_SHA256[name]:
            raise ReplayError(f"generator-v4 trace SHA mismatch: {name}")
        output = work / "prepared" / f"{name}.trace"
        run(
            [sys.executable, str(PREPARER), "--trace", str(event_path),
             "--run-manifest", str(manifest_path), "--output", str(output),
             "--addr-width", "4"],
            cwd=PROJECT, log=work / f"logs/prepare-{name}.log",
        )
        prepared[name] = output
    if tuple(prepared) != official.FULL50 or len(prepared) != 50:
        raise ReplayError("full50 execution roster is not exactly the official 50 traces")
    return prepared


def mutated_sources(
    work: Path, document: dict[str, Any], owner: str, mutation: str,
    baseline_sources: list[Path],
) -> tuple[list[Path], dict[str, Any]]:
    spec = document["mutations"][owner][mutation]
    target = (PROJECT / spec["target"]).resolve()
    original = target.read_text(encoding="utf-8")
    if original.count(spec["old"]) != 1:
        raise ReplayError(f"mutation anchor drifted: {owner}/{mutation}")
    changed = original.replace(spec["old"], spec["new"])
    destination = work / "mutated-rtl" / owner / mutation / target.name
    destination.parent.mkdir(parents=True, exist_ok=False)
    destination.write_text(changed, encoding="utf-8")
    if sha256(destination) == sha256(target):
        raise ReplayError(f"mutation did not change source bytes: {owner}/{mutation}")
    replaced = [destination if path.resolve() == target else path for path in baseline_sources]
    return replaced, {
        "target": relative(target),
        "base_sha256": sha256(target),
        "old_anchor_sha256": hashlib.sha256(spec["old"].encode()).hexdigest(),
        "new_anchor_sha256": hashlib.sha256(spec["new"].encode()).hexdigest(),
        "mutant_sha256": sha256(destination),
        "literal_replacement_count": 1,
    }


def compile_simulator(
    work: Path, verilator: Path, document: dict[str, Any], owner: str,
    sources: list[Path], mutation: str = "none",
) -> tuple[Path, Path]:
    build = work / "build" / owner / mutation
    build.mkdir(parents=True, exist_ok=False)
    binary = build / "sim"
    config = document["owners"][owner]
    command = [
        str(verilator), "--binary", "--timing", "--assert", "-Wall",
        "-Wno-fatal", "-Wno-BLKSEQ", "-Wno-WIDTHEXPAND",
        "-Wno-WIDTHTRUNC", "-Wno-UNUSEDSIGNAL", "-Wno-SYNCASYNCNET",
        f"-D{config['define']}", "--top-module", "a23_full_single_edge_replay_tb",
        "--Mdir", str(build), "-o", "sim", *[str(path) for path in sources],
        str(PROJECT / config["wrapper"]), str(TB),
    ]
    log = work / f"logs/build-{owner}-{mutation}.log"
    env = os.environ.copy()
    env["MAKE"] = document["tools"]["make"]["path"]
    env["CXX"] = document["tools"]["cxx"]["path"]
    run(command, cwd=PROJECT, log=log, env=env)
    if not binary.is_file():
        raise ReplayError(f"Verilator did not create simulator: {owner}/{mutation}")
    return binary, log


def parse_single_csv(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        expected = {
            "owner", "trace", "generated", "source_overrun", "accepted",
            "retired", "fixed_window_retired", "fixed_window_cycles",
            "observation_cycles", "count2_commits", "reset_test",
            "pre_reset_clean_drain",
        }
        if set(reader.fieldnames or ()) != expected:
            raise ReplayError(f"summary schema mismatch: {path}")
        rows = list(reader)
    if len(rows) != 1:
        raise ReplayError(f"summary must contain exactly one row: {path}")
    return rows[0]


def latency_summary(values: Iterable[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {
        "count": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(0.50), "p95": percentile(0.95),
        "p99": percentile(0.99), "max": ordered[-1],
    }


def parse_run(
    summary_path: Path, event_path: Path, owner: str, trace: str,
    expected_reset: bool,
) -> dict[str, Any]:
    summary = parse_single_csv(summary_path)
    with event_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        expected_fields = {
            "owner", "trace", "tb_event_id", "logical_source",
            "occurrence_cycle", "accept_cycle", "retire_cycle",
            "deadline_cycle", "event_state",
        }
        if set(reader.fieldnames or ()) != expected_fields:
            raise ReplayError(f"event schema mismatch: {event_path}")
        events = list(reader)
    if summary["owner"] != owner or summary["trace"] != trace:
        raise ReplayError(f"summary owner/trace mismatch: {summary_path}")
    occurrence_accept: list[int] = []
    accept_retire: list[int] = []
    overruns = 0
    retired = 0
    for expected_id, event in enumerate(events):
        if event["owner"] != owner or event["trace"] != trace:
            raise ReplayError(f"event owner/trace mismatch: {event_path}")
        if int(event["tb_event_id"]) != expected_id:
            raise ReplayError(f"event identities are not contiguous: {event_path}")
        source = int(event["logical_source"])
        occurrence = int(event["occurrence_cycle"])
        deadline = int(event["deadline_cycle"])
        if not 0 <= source < 16 or occurrence < 0 or deadline < occurrence:
            raise ReplayError(f"invalid event provenance: {event_path}")
        if event["event_state"] == "source_overrun":
            if int(event["accept_cycle"]) != -1 or int(event["retire_cycle"]) != -1:
                raise ReplayError(f"source_overrun carries endpoint timing: {event_path}")
            overruns += 1
        elif event["event_state"] == "retired":
            accept = int(event["accept_cycle"])
            retire = int(event["retire_cycle"])
            if not occurrence <= accept <= retire:
                raise ReplayError(f"negative/inverted endpoint latency: {event_path}")
            occurrence_accept.append(accept - occurrence)
            accept_retire.append(retire - accept)
            retired += 1
        else:
            raise ReplayError(f"nonterminal passing event: {event_path}")
    numeric = {
        key: int(summary[key]) for key in (
            "generated", "source_overrun", "accepted", "retired",
            "fixed_window_retired", "fixed_window_cycles",
            "observation_cycles", "count2_commits", "reset_test",
            "pre_reset_clean_drain",
        )
    }
    if numeric["generated"] != len(events):
        raise ReplayError(f"summary/event cardinality mismatch: {summary_path}")
    if numeric["source_overrun"] != overruns:
        raise ReplayError(f"source_overrun event mismatch: {summary_path}")
    if numeric["accepted"] != retired or numeric["retired"] != retired:
        raise ReplayError(f"accepted/retired event mismatch: {summary_path}")
    if numeric["generated"] != numeric["source_overrun"] + numeric["accepted"]:
        raise ReplayError(f"occurrence conservation mismatch: {summary_path}")
    if numeric["accepted"] != numeric["retired"]:
        raise ReplayError(f"endpoint conservation mismatch: {summary_path}")
    if numeric["fixed_window_retired"] > numeric["retired"]:
        raise ReplayError(f"fixed-window count exceeds retirement: {summary_path}")
    if numeric["reset_test"] != int(expected_reset):
        raise ReplayError(f"reset provenance mismatch: {summary_path}")
    if numeric["pre_reset_clean_drain"] != int(expected_reset):
        raise ReplayError(
            f"reset run lacks explicit clean-drain-before-reset proof: {summary_path}"
        )
    return {
        **numeric,
        "occurrence_to_accept": latency_summary(occurrence_accept),
        "accept_to_retire": latency_summary(accept_retire),
        "fixed_window_events_per_cycle": round(
            numeric["fixed_window_retired"] / max(1, numeric["fixed_window_cycles"]), 9
        ),
        "summary_sha256": sha256(summary_path),
        "events_sha256": sha256(event_path),
        "_occurrence_accept": occurrence_accept,
        "_accept_retire": accept_retire,
    }


def execute_case(
    work: Path, binary: Path, owner: str, trace_name: str,
    mode: str, trace: Path | None, mutation: str = "none",
    expect_success: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None, Path]:
    case = work / "artifacts" / owner / mutation / trace_name
    case.mkdir(parents=True, exist_ok=True)
    events = case / "events.csv"
    summary = case / "summary.csv"
    command = [
        str(binary), f"+OWNER={owner}", f"+TRACE_NAME={trace_name}",
        f"+MODE={mode}", f"+MUTATION={mutation}",
        f"+EVENT_OUTPUT={events}", f"+SUMMARY_OUTPUT={summary}",
    ]
    if trace is not None:
        command.append(f"+TRACE_FILE={trace}")
    log = case / "simulation.log"
    process = run(
        command, cwd=PROJECT, log=log, expect_success=expect_success,
    )
    if not expect_success:
        return process, None, log
    if "A23_SE_ACTUAL_RTL_PASS" not in process.stdout:
        raise ReplayError(f"missing actual-RTL PASS sentinel: {owner}/{trace_name}")
    artifact = parse_run(summary, events, owner, trace_name, mode == "reset")
    return process, artifact, log


def aggregate(runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    selected = list(runs)
    occurrence = [value for item in selected for value in item["_occurrence_accept"]]
    internal = [value for item in selected for value in item["_accept_retire"]]
    totals = {
        key: sum(item[key] for item in selected)
        for key in ("generated", "source_overrun", "accepted", "retired",
                    "fixed_window_retired", "fixed_window_cycles", "count2_commits")
    }
    if len(selected) != 50:
        raise ReplayError("owner aggregate does not contain 50 actual executions")
    if totals["generated"] != totals["source_overrun"] + totals["accepted"]:
        raise ReplayError("aggregate occurrence conservation mismatch")
    if totals["accepted"] != totals["retired"]:
        raise ReplayError("aggregate endpoint conservation mismatch")
    return {
        "actual_execution_count": len(selected), "totals": totals,
        "occurrence_to_accept": latency_summary(occurrence),
        "accept_to_retire": latency_summary(internal),
        "fixed_window_events_per_cycle": round(
            totals["fixed_window_retired"] / max(1, totals["fixed_window_cycles"]), 9
        ),
    }


def public(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def first_diagnostic(output: str) -> str | None:
    match = DIAGNOSTIC_RE.search(output)
    return match.group(0) if match else None


def execute_campaign(
    work: Path, output: Path, document: dict[str, Any], verilator: Path,
    sources_by_owner: dict[str, list[Path]], verified_files: dict[str, str],
    verified_tools: dict[str, Any], verified_rtl_git: dict[str, Any], commit: str,
) -> None:
    prepared = prepare_traces(work)
    owners: dict[str, Any] = {}
    mutation_results: list[dict[str, Any]] = []
    for owner in ("a2", "a3"):
        print(f"A23_SE_OWNER_START owner={owner}", flush=True)
        baseline, baseline_build_log = compile_simulator(
            work, verilator, document, owner, sources_by_owner[owner],
        )
        runs: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(official.FULL50, start=1):
            _, artifact, _ = execute_case(
                work, baseline, owner, name, "full", prepared[name],
            )
            assert artifact is not None
            runs[name] = artifact
            if index % 10 == 0:
                print(f"A23_SE_PROGRESS owner={owner} full50={index}/50", flush=True)
        _, reset_artifact, reset_log = execute_case(
            work, baseline, owner, "reset_drain_epochs", "reset", None,
        )
        assert reset_artifact is not None
        _, activation, activation_log = execute_case(
            work, baseline, owner, "directed_distinct_pair", "pair", None,
        )
        assert activation is not None
        if activation["count2_commits"] < 1:
            raise ReplayError(f"source mutation activation case failed: {owner}")

        owners[owner] = {
            "baseline_build_log_sha256": sha256(baseline_build_log),
            "full50": {
                "actual_execution_count": 50,
                "aggregate": aggregate(runs.values()),
                "runs": {
                    name: {
                        "trace_sha256": official.TRACE_SHA256[name],
                        "prepared_trace_sha256": sha256(prepared[name]),
                        **public(runs[name]),
                    }
                    for name in official.FULL50
                },
            },
            "reset": {**public(reset_artifact), "simulation_log_sha256": sha256(reset_log)},
            "mutation_activation": {
                **public(activation), "simulation_log_sha256": sha256(activation_log),
            },
        }

        for mutation in MUTATION_NAMES:
            changed_sources, identity = mutated_sources(
                work, document, owner, mutation, sources_by_owner[owner],
            )
            mutant, build_log = compile_simulator(
                work, verilator, document, owner, changed_sources, mutation,
            )
            mode = "reset" if mutation == "reset_escape" else "pair"
            trace_name = "reset_drain_epochs" if mode == "reset" else "directed_distinct_pair"
            process, _, simulation_log = execute_case(
                work, mutant, owner, trace_name, mode, None,
                mutation=mutation, expect_success=False,
            )
            expected = {
                "drop": "A23_SE_DROP_FAIL",
                "duplicate": "A23_SE_DUPLICATE_FAIL",
                "reorder": "A23_SE_REORDER_FAIL",
                "reset_escape": "A23_SE_RESET_ESCAPE_FAIL",
            }[mutation]
            actual_first = first_diagnostic(process.stdout)
            killed = (
                process.returncode != 0 and actual_first == expected and
                "A23_SE_ACTUAL_RTL_PASS" not in process.stdout
            )
            if not killed:
                raise ReplayError(
                    f"source mutation survived or wrong first diagnostic: "
                    f"{owner}/{mutation} exit={process.returncode} "
                    f"expected={expected} actual={actual_first}"
                )
            mutation_results.append({
                "owner": owner, "mutation": mutation,
                "actual_endpoint_RTL_source_rewrite": True,
                "compiled_successfully": True, "executed": True, "killed": True,
                "exit_code": process.returncode,
                "first_diagnostic": actual_first,
                "build_log_sha256": sha256(build_log),
                "simulation_log_sha256": sha256(simulation_log),
                "source_identity": identity,
            })
            print(f"A23_SE_MUTATION_KILLED owner={owner} mutation={mutation}", flush=True)

    result = {
        "schema": "a23_full_single_edge_replay_result_v1",
        "status": "PASS",
        "boundary": "actual_A2_A3_scheduler_plus_actual_single_edge_endpoint",
        "acceptance_observation": "actual_endpoint_atomic_source_accept_count_and_ordered_addresses",
        "retirement_scoreboard": "actual_single_edge_retire_prefix_in_global_accept_order",
        "event_identity_scope": "TB_identity_bound_to_observable_logical_source_stream",
        "source_overrun_semantics": "same_source_occurrence_while_one_entry_source_latch_occupied",
        "reset_qualification": "reset_only_after_external_clean_drain_and_no_protocol_error",
        "conservation": [
            "generated = source_overrun + accepted",
            "after bounded drain: accepted = retired",
        ],
        "generator": {
            "version": official.GENERATOR_VERSION,
            "source_commit": official.SOURCE_COMMIT,
            "full50_manifest_sha256": official.SUITES["full50"]["manifest_sha256"],
            "trace_count": 50,
        },
        "execution_accounting": {
            "owners": 2, "full50_actual_RTL_executions": 100,
            "reset_actual_RTL_executions": 2,
            "mutation_activation_actual_RTL_executions": 2,
            "mutation_actual_RTL_executions": 8,
            "receipt_only_executions": 0,
        },
        "owners": owners,
        "mutations": mutation_results,
        "provenance": {
            "package_commit": commit,
            "pins_path": relative(PINS), "pins_sha256": sha256(PINS),
            "verified_files": verified_files, "verified_tools": verified_tools,
            "actual_rtl_git": verified_rtl_git,
        },
        "qualification": {
            "single_edge_digital_RTL": "GO",
            "physical": "HOLD", "power": "HOLD", "CDC_RDC": "HOLD",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"A23_FULL_SINGLE_EDGE_REPLAY_PASS owners=2 full50_actual=100 "
        f"reset_actual=2 mutations_actual=8 output={output}", flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verilator", type=Path, default=DEFAULT_VERILATOR)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.preflight and (args.work_dir is None or args.output is None):
        parser.error("--work-dir and --output are required for actual execution")
    try:
        document = load_document()
        sources, files, tools, rtl_git, commit = validate_integration(
            document, args.verilator, allow_dirty=args.allow_dirty,
        )
        if args.preflight:
            print("A23_FULL_SINGLE_EDGE_PREFLIGHT_READY owners=2 full50_required=100")
            return 0
        assert args.work_dir is not None and args.output is not None and commit is not None
        work = args.work_dir.resolve()
        output = args.output.resolve()
        if work.exists() or output.exists():
            raise ReplayError("work-dir and output must not already exist")
        work.mkdir(parents=True)
        execute_campaign(
            work, output, document, args.verilator, sources, files, tools,
            rtl_git, commit,
        )
        return 0
    except ReplayUnavailable as error:
        print(f"A23_FULL_SINGLE_EDGE_HOLD_NOT_RUN {error}", file=sys.stderr)
        return 3
    except (ReplayError, OSError, subprocess.SubprocessError) as error:
        print(f"A23_FULL_SINGLE_EDGE_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
