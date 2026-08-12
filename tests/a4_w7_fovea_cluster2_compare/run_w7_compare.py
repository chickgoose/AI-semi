#!/usr/bin/env python3
"""Compile-once/run-many Xcelium comparison for Ganghee Fovea and Cluster2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence


FULL_MANIFEST_SHA = "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9"
CAPACITY_MANIFEST_SHA = "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62"
OFFICIAL_SHA = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
GENERATOR_SHA = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
PREPARER_SHA = "245078d3e1f6ed496a0de328f1568cb0a8302397ce9f5544021415f0afad2826"
BAD_XCELIUM = re.compile(
    r"(?im)(?:^\s*(?:xrun|xmelab|xmsim|ncsim):\s*\*[EF],|"
    r"^\s*UVM_(?:ERROR|FATAL)\b|AER_CLEAN_TEST_FAIL|"
    r"Assertion[^\n]*(?:fail|error)|\$fatal)")
SUMMARY_REQUIRED = {
    "candidate", "test", "seed", "load_pct", "stim_cycles", "generated",
    "source_overrun", "accepted", "delivered", "errors", "measurement_delivered",
    "measurement_cycles", "throughput",
}
EVENT_REQUIRED = {
    "candidate", "test", "seed", "load_pct", "tb_only_event_id",
    "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
    "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
}


class W7Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    key: str
    report_candidate: str
    top: str
    rtl: Path | None
    filelist: Path | None
    define: str
    module_define: str
    binding: Path
    retire_lanes: int


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise W7Error(f"not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise W7Error(f"cannot read JSON {path}: {exc}") from exc


def run(command: Sequence[str], driver_log: Path, *, allowed=(0,)) -> int:
    with driver_log.open("xb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                close_fds=True, check=False)
    if result.returncode not in allowed:
        tail = driver_log.read_text(encoding="utf-8", errors="replace")[-1200:].strip()
        raise W7Error(f"command failed ({result.returncode}): {command[0]}: {tail}")
    return result.returncode


def scan_xcelium(log: Path, pass_marker: str | None = None) -> None:
    text = log.read_text(encoding="utf-8", errors="replace")
    match = BAD_XCELIUM.search(text)
    if match:
        raise W7Error(f"Xcelium diagnostic in {log}: {match.group(0)!r}")
    if pass_marker is not None and text.splitlines().count(pass_marker) != 1:
        raise W7Error(f"missing or duplicate exact PASS marker in {log}: {pass_marker}")


def discover_xrun(explicit: Path | None) -> Path:
    selected = explicit
    if selected is None and os.environ.get("AER_XRUN_BIN"):
        selected = Path(os.environ["AER_XRUN_BIN"])
    if selected is None and os.environ.get("XRUN"):
        selected = Path(os.environ["XRUN"])
    if selected is None:
        found = shutil.which("xrun")
        selected = Path(found) if found else None
    if selected is None:
        raise W7Error("Xcelium unavailable; set --xrun, AER_XRUN_BIN, or XRUN")
    resolved = selected.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise W7Error(f"Xcelium executable unavailable: {selected}")
    return resolved


def load_official(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("a4_w7_official", path)
    if spec is None or spec.loader is None:
        raise W7Error("cannot import official suite specification")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.GENERATOR_VERSION != "4.0":
        raise W7Error("official generator version is not 4.0")
    return module


def require_protected_clean(a1: Path, paths: Sequence[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(a1.resolve())
        except ValueError as exc:
            raise W7Error(f"protected tool escapes A1 root: {path}") from exc
        check = subprocess.run(
            ["git", "-C", str(a1), "diff", "--quiet", "HEAD", "--", str(relative)],
            close_fds=True, check=False)
        tracked = subprocess.run(
            ["git", "-C", str(a1), "ls-files", "--error-unmatch", str(relative)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, check=False)
        if check.returncode != 0 or tracked.returncode != 0:
            raise W7Error(f"protected common file is modified or untracked: {relative}")
        result[str(relative)] = sha256(path)
    return result


def filelist_sources(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    resolved = path.resolve()
    seen = set() if seen is None else seen
    if resolved in seen:
        raise W7Error(f"recursive/duplicate nested filelist: {resolved}")
    seen.add(resolved)
    evidence = {str(resolved): sha256(resolved)}
    tokens = shlex.split(resolved.read_text(encoding="utf-8"), comments=True)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-f":
            index += 1
            if index >= len(tokens):
                raise W7Error(f"{resolved}: -f lacks an absolute nested filelist")
            nested = Path(tokens[index])
            if not nested.is_absolute():
                raise W7Error(f"{resolved}: nested filelist must be absolute: {nested}")
            evidence.update(filelist_sources(nested, seen))
        elif token.startswith("+incdir+"):
            directories = [Path(item) for item in token[len("+incdir+"):].split("+") if item]
            if not directories or any(not item.is_absolute() for item in directories):
                raise W7Error(f"{resolved}: include directories must be absolute")
            for directory in directories:
                if directory.is_symlink() or not directory.is_dir():
                    raise W7Error(f"{resolved}: invalid include directory {directory}")
                for include in sorted(item for item in directory.rglob("*") if item.is_file()):
                    evidence[str(include.resolve())] = sha256(include.resolve())
        elif token.startswith("+define+"):
            pass
        elif token.startswith("-"):
            raise W7Error(f"{resolved}: unsupported provenance-opaque filelist option {token}")
        else:
            source = Path(token)
            if not source.is_absolute():
                raise W7Error(f"{resolved}: source entry must be absolute: {source}")
            evidence[str(source.resolve())] = sha256(source.resolve())
        index += 1
    return evidence


def validate_candidate(candidate: Candidate) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate.top):
        raise W7Error(f"{candidate.key}: invalid top {candidate.top!r}")
    if (candidate.rtl is None) == (candidate.filelist is None):
        raise W7Error(f"{candidate.key}: set exactly one RTL or filelist")
    evidence: dict[str, Any] = {
        "top": candidate.top,
        "binding": {"path": str(candidate.binding), "sha256": sha256(candidate.binding)},
    }
    if candidate.rtl is not None:
        evidence["rtl"] = {"path": str(candidate.rtl.resolve()),
                           "sha256": sha256(candidate.rtl.resolve())}
    else:
        assert candidate.filelist is not None
        evidence["filelist_sources_sha256"] = filelist_sources(candidate.filelist)
    return evidence


def validate_generation(trace_root: Path, official: Any) -> list[dict[str, Any]]:
    index = load_json(trace_root / "generation-index.json")
    rows = index.get("runs")
    if index.get("schema_version") != 1 or index.get("generator_version") != "4.0":
        raise W7Error("generation-index version mismatch")
    if not isinstance(rows, list):
        raise W7Error("generation-index runs missing")
    names = [row.get("run", {}).get("name") for row in rows]
    if names != list(official.FULL50) or len(names) != len(set(names)) or len(names) != 50:
        raise W7Error("full50 exact order/cardinality mismatch")
    for row in rows:
        name = row["run"]["name"]
        trace = trace_root / row["trace_file"]
        manifest = trace_root / f"{name}.manifest.json"
        if (row.get("trace_sha256") != official.TRACE_SHA256[name] or
                row.get("event_identity_mode") != "address_only" or
                row.get("dut_address_fields") != ["logical_source"] or
                row.get("dut_payload_fields") != [] or
                sha256(trace) != official.TRACE_SHA256[name] or
                load_json(manifest) != row):
            raise W7Error(f"{name}: frozen trace/address-only provenance mismatch")
    capacity = list(official.CAPACITY22)
    if len(capacity) != 22 or len(capacity) != len(set(capacity)) or not set(capacity) < set(names):
        raise W7Error("capacity22 is not an exact proper subset of full50")
    return rows


def compile_candidate(xrun: Path, a1: Path, output: Path,
                      candidate: Candidate) -> tuple[str, Path]:
    candidate_root = output / "candidates" / candidate.key
    candidate_root.mkdir(parents=True)
    snapshot = f"a4_w7_{candidate.key}_n16"
    tool_log = candidate_root / "elaborate.log"
    command = [
        str(xrun), "-64bit", "-sv", "-timescale", "1ns/1ps",
        "-top", "aer_clean_tb", "-snapshot", snapshot, "-elaborate",
        "-xmlibdirname", str(candidate_root / "xcelium.d"),
        "-define", candidate.define,
        "-define", f"{candidate.module_define}={candidate.top}",
        "-defparam", "aer_clean_tb.NUM_SOURCES=16",
        "-defparam", "aer_clean_tb.ADDR_WIDTH=16",
        "-defparam", f"aer_clean_tb.RETIRE_LANES={candidate.retire_lanes}",
        "-defparam", "aer_clean_tb.FIFO_DEPTH=0",
        "-f", str(a1 / "tb/clean/files.f"), str(candidate.binding),
    ]
    if candidate.rtl is not None:
        command.append(str(candidate.rtl.resolve()))
    else:
        assert candidate.filelist is not None
        command.extend(["-f", str(candidate.filelist.resolve())])
    command.extend(["-l", str(tool_log)])
    run(command, candidate_root / "elaborate.driver.log")
    if not tool_log.is_file():
        raise W7Error(f"{candidate.key}: Xcelium did not create elaborate.log")
    scan_xcelium(tool_log)
    return snapshot, candidate_root / "xcelium.d"


def expected_load_pct(metadata: dict[str, Any]) -> int:
    return int((Decimal(str(metadata["run"]["load"])) * 1000 + 5) // 10)


def validate_outputs(metadata: dict[str, Any], candidate: Candidate,
                     summary: Path, events: Path) -> dict[str, Any]:
    if any(path.is_symlink() or not path.is_file() for path in (summary, events)):
        raise W7Error(f"{candidate.key}: missing regular result for {metadata['run']['name']}")
    with summary.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not SUMMARY_REQUIRED.issubset(reader.fieldnames or []):
            raise W7Error(f"{candidate.key}: summary schema mismatch")
        rows = list(reader)
    if len(rows) != 1:
        raise W7Error(f"{candidate.key}: summary cardinality mismatch")
    row = rows[0]
    report_group = metadata.get("report_group", metadata["run"]["name"])
    load_pct = expected_load_pct(metadata)
    if (row["candidate"] != candidate.report_candidate or row["test"] != report_group or
            int(row["seed"]) != metadata["run"]["seed"] or
            int(row["load_pct"]) != load_pct or int(row["errors"]) != 0 or
            int(row["generated"]) != metadata["event_count"] or
            int(row["accepted"]) != int(row["delivered"])):
        raise W7Error(f"{candidate.key}: summary provenance/correctness mismatch")
    measured, cycles = int(row["measurement_delivered"]), int(row["measurement_cycles"])
    if cycles != metadata["run"]["stim_cycles"] or measured > int(row["delivered"]):
        raise W7Error(f"{candidate.key}: measurement window mismatch")
    if abs(Decimal(row["throughput"]) - Decimal(measured) / Decimal(cycles)) > Decimal("0.000001"):
        raise W7Error(f"{candidate.key}: throughput disagrees with frozen counters")
    with events.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not EVENT_REQUIRED.issubset(reader.fieldnames or []):
            raise W7Error(f"{candidate.key}: event schema mismatch")
        event_rows = list(reader)
    if len(event_rows) != metadata["event_count"]:
        raise W7Error(f"{candidate.key}: event cardinality mismatch")
    states = {"source_overrun": 0, "pending": 0, "accepted": 0, "delivered": 0}
    for event_id, event in enumerate(event_rows):
        state = event["event_state"]
        if state not in states or int(event["tb_only_event_id"]) != event_id:
            raise W7Error(f"{candidate.key}: invalid event identity/state")
        if (event["candidate"] != candidate.report_candidate or
                event["test"] != report_group or int(event["seed"]) != metadata["run"]["seed"] or
                int(event["load_pct"]) != load_pct or int(event["source_count"]) != 16):
            raise W7Error(f"{candidate.key}: event provenance mismatch")
        states[state] += 1
    if (states["source_overrun"] != int(row["source_overrun"]) or
            states["accepted"] != 0 or states["pending"] != 0 or
            states["delivered"] != int(row["delivered"]) or
            states["delivered"] + states["source_overrun"] != metadata["event_count"]):
        raise W7Error(f"{candidate.key}: event conservation/drain mismatch")
    return {
        "summary": str(summary), "summary_sha256": sha256(summary),
        "events": str(events), "events_sha256": sha256(events),
        "generated": int(row["generated"]), "source_overrun": int(row["source_overrun"]),
        "accepted": int(row["accepted"]), "delivered": int(row["delivered"]),
        "measurement_delivered": measured, "measurement_cycles": cycles,
    }


def simulate(xrun: Path, output: Path, candidate: Candidate, snapshot: str,
             xmlib: Path, *, metadata: dict[str, Any] | None = None,
             prepared: Path | None = None) -> dict[str, Any]:
    name = "basic_reset_drain" if metadata is None else metadata["run"]["name"]
    report_group = name if metadata is None else metadata.get("report_group", name)
    run_root = output / "candidates" / candidate.key / "runs" / name
    run_root.mkdir(parents=True)
    summary, events, tool_log = run_root / "trace.csv", run_root / "trace.events.csv", run_root / "xrun.log"
    command = [
        str(xrun), "-64bit", "-R", "-snapshot", snapshot,
        "-xmlibdirname", str(xmlib), f"+CLEAN_TEST={'basic_reset_drain' if metadata is None else 'trace'}",
        f"+CANDIDATE={candidate.report_candidate}", f"+METRICS={summary}",
        f"+EVENT_METRICS={events}", "+SEED=1", "-l", str(tool_log),
    ]
    if metadata is not None:
        assert prepared is not None
        command.extend([f"+TRACE_FILE={prepared}", f"+TRACE_NAME={report_group}"])
    run(command, run_root / "xrun.driver.log")
    if not tool_log.is_file():
        raise W7Error(f"{candidate.key}/{name}: Xcelium log missing")
    scan_xcelium(tool_log, f"AER_CLEAN_TEST_PASS {report_group}")
    if metadata is None:
        text = tool_log.read_text(encoding="utf-8", errors="replace")
        if text.splitlines().count("AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16") != 1:
            raise W7Error(f"{candidate.key}: reset PASS evidence missing")
        with summary.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 1 or int(rows[0].get("errors", "-1")) != 0:
            raise W7Error(f"{candidate.key}: reset summary failed")
        return {"log_sha256": sha256(tool_log), "summary_sha256": sha256(summary),
                "events_sha256": sha256(events)}
    return validate_outputs(metadata, candidate, summary, events)


def analyzer_for(workload: str) -> str | None:
    return {
        "pairwise_contention": "pairwise_contention_metrics.py",
        "phase_transition": "phase_metrics.py",
        "timing_pair": "timing_pair_metrics.py",
        "mixed_phase_always_ready": "mixed_phase_always_ready_metrics.py",
    }.get(workload)


def run_analyzers(a1: Path, output: Path, candidate: Candidate,
                  rows: list[dict[str, Any]], trace_root: Path) -> tuple[list[dict[str, Any]], int]:
    candidate_root = output / "candidates" / candidate.key
    reports: list[dict[str, Any]] = []
    for metadata in rows:
        name, workload = metadata["run"]["name"], metadata["run"]["workload"]
        analyzer = analyzer_for(workload)
        if analyzer is None:
            continue
        run_root = candidate_root / "runs" / name
        report = run_root / f"{name}.analysis.json"
        command = [sys.executable, str(a1 / "benchmarks/clean_slate_aer" / analyzer)]
        if workload == "mixed_phase_always_ready":
            command.extend(["--run-manifest", str(trace_root / f"{name}.manifest.json"),
                            "--events", str(run_root / "trace.events.csv"),
                            "--summary", str(run_root / "trace.csv"), "--require-qualified"])
        else:
            command.extend(["--trace", str(trace_root / metadata["trace_file"]),
                            "--run-manifest", str(trace_root / f"{name}.manifest.json"),
                            "--events", str(run_root / "trace.events.csv")])
        command.extend(["--output", str(report)])
        run(command, run_root / f"{name}.analysis.log")
        doc = load_json(report)
        if doc.get("candidate") != candidate.report_candidate:
            raise W7Error(f"{candidate.key}/{name}: analyzer candidate mismatch")
        reports.append({"name": name, "workload": workload, "path": str(report),
                        "sha256": sha256(report)})
    expected = {"pairwise_contention": 2, "phase_transition": 2,
                "timing_pair": 2, "mixed_phase_always_ready": 2}
    actual = {key: sum(row["workload"] == key for row in reports) for key in expected}
    if actual != expected or len(reports) != 8:
        raise W7Error(f"{candidate.key}: analyzer cardinality mismatch {actual}")
    by_name = {row["name"]: row for row in reports}
    cross = candidate_root / "analysis" / "pairwise_identity_vs_affine.json"
    cross.parent.mkdir(parents=True)
    run([
        sys.executable, str(a1 / "benchmarks/clean_slate_aer/pairwise_cross_map_compare.py"),
        "--identity-manifest", str(trace_root / "pairwise_contention_identity.manifest.json"),
        "--identity-report", by_name["pairwise_contention_identity"]["path"],
        "--affine-manifest", str(trace_root / "pairwise_contention_affine.manifest.json"),
        "--affine-report", by_name["pairwise_contention_affine"]["path"],
        "--output", str(cross),
    ], candidate_root / "analysis" / "pairwise_cross_map.log")
    doc = load_json(cross)
    if doc.get("candidate") != candidate.report_candidate:
        raise W7Error(f"{candidate.key}: cross-map candidate mismatch")
    if not isinstance(doc.get("rankable"), bool):
        raise W7Error(f"{candidate.key}: cross-map rankable state missing")
    cross_status = 0 if doc["rankable"] else 3
    reports.append({"name": "pairwise_identity_vs_affine", "workload": "cross_map",
                    "path": str(cross), "sha256": sha256(cross), "exit_code": cross_status})
    return reports, cross_status


def aggregate_view(a1: Path, output: Path, candidate: Candidate,
                   names: Sequence[str], view: str) -> dict[str, Any]:
    root = output / "candidates" / candidate.key
    analysis = root / "analysis"
    summary_out, event_out = analysis / f"{view}.aggregate.csv", analysis / f"{view}.event-runs.csv"
    # The frozen report_group intentionally aliases the two mixed-map runs,
    # including their seed/load. aggregate.py correctly rejects duplicate event
    # IDs inside one run key, so partition only that collision while retaining
    # ordinary multi-seed grouping. Concatenate analyzer-produced CSV batches;
    # never rewrite common input semantics.
    groups: dict[tuple[str, str], list[tuple[list[str], set[str]]]] = {}
    for name in names:
        with (root / "runs" / name / "trace.csv").open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        group = groups.setdefault((row["test"], row["load_pct"]), [([], set())])
        for batch, seeds in group:
            if row["seed"] not in seeds:
                batch.append(name)
                seeds.add(row["seed"])
                break
        else:
            group.append(([name], {row["seed"]}))
    batches = [batch for group in groups.values() for batch, _ in group if batch]
    summary_parts, event_parts = [], []
    for index, batch in enumerate(batches):
        part_summary = analysis / f".{view}.part{index}.aggregate.csv"
        part_events = analysis / f".{view}.part{index}.event-runs.csv"
        inputs = [root / "runs" / name / "trace.csv" for name in batch]
        events = [root / "runs" / name / "trace.events.csv" for name in batch]
        command = [sys.executable, str(a1 / "benchmarks/clean_slate_aer/aggregate.py"),
                   *map(str, inputs)]
        for path in events:
            command.extend(["--events", str(path)])
        command.extend(["--output", str(part_summary), "--event-output", str(part_events),
                        "--fail-on-correctness"])
        run(command, analysis / f"{view}.part{index}.aggregate.log")
        summary_parts.append(part_summary)
        event_parts.append(part_events)
    for destination, parts in ((summary_out, summary_parts), (event_out, event_parts)):
        header: str | None = None
        with destination.open("x", encoding="utf-8", newline="") as stream:
            for part in parts:
                lines = part.read_text(encoding="utf-8").splitlines()
                if not lines:
                    raise W7Error(f"{candidate.key}/{view}: empty aggregate part")
                if header is None:
                    header = lines[0]
                    stream.write(header + "\n")
                elif lines[0] != header:
                    raise W7Error(f"{candidate.key}/{view}: aggregate part schema mismatch")
                for line in lines[1:]:
                    stream.write(line + "\n")
    if any(path.is_symlink() or not path.is_file() for path in (summary_out, event_out)):
        raise W7Error(f"{candidate.key}/{view}: aggregate outputs missing")
    return {"stems": list(names), "run_count": len(names), "aggregate_batches": len(batches),
            "aggregate": str(summary_out), "aggregate_sha256": sha256(summary_out),
            "event_runs": str(event_out), "event_runs_sha256": sha256(event_out)}


def comparison_totals(results: dict[str, Any], names: Sequence[str]) -> dict[str, Any]:
    selected = [row for row in results["runs"] if row["name"] in set(names)]
    if len(selected) != len(names) or {row["name"] for row in selected} != set(names):
        raise W7Error("comparison view run set mismatch")
    totals = {key: sum(int(row[key]) for row in selected) for key in (
        "generated", "source_overrun", "accepted", "delivered",
        "measurement_delivered", "measurement_cycles",
    )}
    totals["window_throughput"] = (
        totals["measurement_delivered"] / totals["measurement_cycles"]
        if totals["measurement_cycles"] else 0.0)
    return totals


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--a1-root", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--xrun", type=Path)
    parser.add_argument("--fovea-top", required=True)
    parser.add_argument("--fovea-rtl", type=Path)
    parser.add_argument("--fovea-filelist", type=Path)
    parser.add_argument("--cluster2-top", required=True)
    parser.add_argument("--cluster2-rtl", type=Path)
    parser.add_argument("--cluster2-filelist", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise W7Error(f"refusing to overwrite output: {args.output}")
        args.output.mkdir(parents=True, mode=0o700)
        xrun = discover_xrun(args.xrun)
        xrun_pre = sha256(xrun)
        a1 = args.a1_root.resolve()
        official_path = a1 / "scripts/common_suite_official.py"
        full_manifest = a1 / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
        cap_manifest = a1 / "benchmarks/clean_slate_aer/manifest.multilane-n16.json"
        generator = a1 / "benchmarks/clean_slate_aer/generate_trace.py"
        preparer = a1 / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
        analyzer_names = ["aggregate.py", "pairwise_contention_metrics.py", "phase_metrics.py",
                          "timing_pair_metrics.py", "mixed_phase_always_ready_metrics.py",
                          "pairwise_cross_map_compare.py"]
        protected = [official_path, full_manifest, cap_manifest, generator, preparer,
                     a1 / "tb/clean/files.f", a1 / "tb/clean/aer_clean_tb.sv",
                     a1 / "tb/clean/aer_bench_if.sv", a1 / "tb/clean/aer_clean_assertions.sv",
                     *[a1 / "benchmarks/clean_slate_aer" / name for name in analyzer_names]]
        if sha256(official_path) != OFFICIAL_SHA or sha256(full_manifest) != FULL_MANIFEST_SHA or sha256(cap_manifest) != CAPACITY_MANIFEST_SHA or sha256(generator) != GENERATOR_SHA or sha256(preparer) != PREPARER_SHA:
            raise W7Error("frozen official/generator/preparer/manifest hash mismatch")
        official = load_official(official_path)
        if (official.SUITES["full50"]["manifest_sha256"] != FULL_MANIFEST_SHA or
                official.SUITES["capacity22"]["manifest_sha256"] != CAPACITY_MANIFEST_SHA):
            raise W7Error("official suite manifest identity mismatch")
        candidates = [
            Candidate("fovea", "ganghee-native-coordinate-source-projection", args.fovea_top,
                      args.fovea_rtl, args.fovea_filelist, "AER_CLEAN_GANGHEE_NATIVE",
                      "AER_GANGHEE_NATIVE_MODULE", a1 / "tb/clean/native/aer_ganghee_native_binding.sv", 1),
            Candidate("cluster2", "ganghee-cluster2-row-bitmap", args.cluster2_top,
                      args.cluster2_rtl, args.cluster2_filelist, "AER_CLEAN_GANGHEE_CLUSTER2",
                      "AER_GANGHEE_CLUSTER2_MODULE", a1 / "tb/clean/native/aer_ganghee_cluster2_binding.sv", 8),
        ]
        protected.extend(candidate.binding for candidate in candidates)
        common_pre = require_protected_clean(a1, protected)
        candidate_sources = {candidate.key: validate_candidate(candidate) for candidate in candidates}
        trace_root = args.output / "traces"
        run([sys.executable, str(generator), "--manifest", str(full_manifest),
             "--output-dir", str(trace_root)], args.output / "generate.log")
        rows = validate_generation(trace_root, official)
        prepared_root = args.output / "prepared"
        prepared_root.mkdir()
        prepared: dict[str, Path] = {}
        for metadata in rows:
            name = metadata["run"]["name"]
            destination = prepared_root / f"{name}.svtrace"
            run([sys.executable, str(preparer), "--trace", str(trace_root / metadata["trace_file"]),
                 "--run-manifest", str(trace_root / f"{name}.manifest.json"),
                 "--output", str(destination), "--addr-width", "16"],
                prepared_root / f"{name}.prepare.log")
            prepared[name] = destination
        all_results: dict[str, Any] = {}
        deferred_nonrankable = False
        for candidate in candidates:
            snapshot, xmlib = compile_candidate(xrun, a1, args.output, candidate)
            reset = simulate(xrun, args.output, candidate, snapshot, xmlib)
            run_rows = []
            for metadata in rows:
                name = metadata["run"]["name"]
                run_rows.append({"name": name, **simulate(
                    xrun, args.output, candidate, snapshot, xmlib,
                    metadata=metadata, prepared=prepared[name])})
            reports, cross_status = run_analyzers(a1, args.output, candidate, rows, trace_root)
            deferred_nonrankable |= cross_status == 3
            views = {
                "full50": aggregate_view(a1, args.output, candidate, official.FULL50, "full50"),
                "capacity22": aggregate_view(a1, args.output, candidate, official.CAPACITY22, "capacity22"),
            }
            cap_special = {name for name in official.CAPACITY22 if analyzer_for(
                next(row for row in rows if row["run"]["name"] == name)["run"]["workload"])}
            if len(cap_special) != 6:
                raise W7Error(f"{candidate.key}: capacity22 special analyzer view is not exact six")
            all_results[candidate.key] = {
                "compile_count": 1, "run_count": 51, "reset": reset,
                "runs": run_rows, "analyzers": reports,
                "analyzer_cardinality": {"full50_special": 8, "capacity22_subset_special": 6,
                                         "cross_map": 1},
                "views": views,
            }
        common_post = require_protected_clean(a1, protected)
        candidate_sources_post = {candidate.key: validate_candidate(candidate)
                                  for candidate in candidates}
        if (common_pre != common_post or candidate_sources != candidate_sources_post or
                sha256(xrun) != xrun_pre):
            raise W7Error("tool/common/candidate provenance changed during run")
        head = subprocess.run(["git", "-C", str(a1), "rev-parse", "HEAD"],
                              capture_output=True, text=True, close_fds=True, check=True).stdout.strip()
        comparison: dict[str, Any] = {}
        for view, names in (("full50", official.FULL50), ("capacity22", official.CAPACITY22)):
            fovea_totals = comparison_totals(all_results["fovea"], names)
            cluster_totals = comparison_totals(all_results["cluster2"], names)
            comparison[view] = {
                "fovea": fovea_totals, "cluster2": cluster_totals,
                "cluster2_minus_fovea": {
                    key: cluster_totals[key] - fovea_totals[key]
                    for key in ("source_overrun", "accepted", "delivered",
                                "measurement_delivered", "window_throughput")
                },
            }
        receipt = {
            "schema": "a4_w7_fovea_cluster2_compile_once_v1",
            "status": "HOLD_NONRANKABLE_CROSS_MAP" if deferred_nonrankable else "LOCAL_XCELIUM_PROCESS_PASS",
            "process_contract": {
                "compile_count_per_candidate": 1, "runs_per_candidate": 51,
                "reset_runs_per_candidate": 1, "trace_runs_per_candidate": 50,
                "capacity22": "analysis_only_exact_subset_of_same_full50_results_no_rerun",
                "fifo_depth": 0, "source_count": 16, "address_only": True,
                "fovea_retire_lanes": 1, "cluster2_retire_lanes": 8,
                "cross_map_exit3_policy": "defer_until_both_candidates_and_all_analyzers_complete",
            },
            "provenance": {
                "a1_head": head, "protected_common_sha256": common_post,
                "candidate_sources": candidate_sources, "xrun_path": str(xrun),
                "xrun_sha256": xrun_pre, "official_full50_manifest_sha256": FULL_MANIFEST_SHA,
                "capacity22_view_manifest_sha256": CAPACITY_MANIFEST_SHA,
                "generation_index_sha256": sha256(trace_root / "generation-index.json"),
            },
            "comparison": comparison,
            "candidates": all_results,
        }
        with (args.output / "receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
        print(f"A4_W7_PROCESS_{'HOLD_NONRANKABLE' if deferred_nonrankable else 'PASS'} "
              "candidates=2 compiles=2 reset_runs=2 trace_runs=100 full50=50 capacity22_view=22")
        return 3 if deferred_nonrankable else 0
    except (OSError, ValueError, KeyError, W7Error, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
