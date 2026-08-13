#!/usr/bin/env python3
"""Produce real Xcelium VCD/SAIF for the immutable W2 5 ns common workload."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import activity_lib as lib


def run(command: list[str], log: Path, cwd: Path | None = None) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("xb") as stream:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                close_fds=True,
            )
    except FileExistsError as exc:
        raise lib.ActivityError(f"refusing to overwrite execution log: {log}") from exc
    if completed.returncode != 0:
        raise lib.ActivityError(
            f"command failed rc={completed.returncode}: {command[0]} (see {log})"
        )


def verify_xrun(path: Path, registry: dict[str, Any], output: Path) -> dict[str, Any]:
    pinned = registry["xcelium"]
    if str(path) != pinned["path"]:
        raise lib.ActivityError(f"Xcelium path differs from registry: {path}")
    lib.require_digest(path, pinned["sha256"])
    if not os.access(path, os.X_OK):
        raise lib.ActivityError("pinned Xcelium is not executable")
    log = output / "provenance/tools/xrun-version.log"
    run([str(path), "-version"], log)
    versions = sorted(set(re.findall(
        r"(?<![A-Za-z0-9_.-])\d{2}\.\d{2}-s\d+(?:_\d+)?(?![A-Za-z0-9_.-])",
        lib.stable_bytes(log).decode("utf-8", errors="replace"),
    )))
    if versions != [pinned["version"]]:
        raise lib.ActivityError(f"Xcelium version mismatch: {versions}")
    return {
        "path": str(path),
        "sha256": pinned["sha256"],
        "version": versions[0],
        "version_log": lib.artifact(log, output),
    }


def verify_python(output: Path) -> dict[str, Any]:
    path = Path(sys.executable).resolve(strict=True)
    if not os.access(path, os.X_OK):
        raise lib.ActivityError("current Python executable is not executable")
    executable_sha = lib.digest(path)
    log = output / "provenance/tools/python-version.log"
    run([str(path), "--version"], log)
    versions = lib.stable_bytes(log).decode("utf-8", errors="strict").strip().splitlines()
    if len(versions) != 1 or re.fullmatch(
        r"Python 3\.(?:1[1-9]|[2-9][0-9])(?:\.\d+)?(?: .*)?", versions[0]
    ) is None:
        raise lib.ActivityError(f"unsupported Python version banner: {versions}")
    return {
        "path": str(path),
        "sha256": executable_sha,
        "version": versions[0],
        "version_log": lib.artifact(log, output),
    }


def snapshot_inputs(
    repo: Path,
    staged: Path,
    output: Path,
    registry: dict[str, Any],
    closures: dict[str, list[str]],
) -> dict[str, Any]:
    provenance = output / "provenance"
    repository_records: dict[str, Any] = {}
    registry_destination = provenance / "repository" / lib.REGISTRY_RELATIVE
    lib.write_exclusive(
        registry_destination, lib.stable_bytes(repo / lib.REGISTRY_RELATIVE)
    )
    repository_records[lib.REGISTRY_RELATIVE.as_posix()] = lib.artifact(
        registry_destination, output
    )
    for relative, expected in registry["pinned_repository_inputs"].items():
        destination = provenance / "repository" / relative
        lib.snapshot(repo / relative, destination, expected)
        repository_records[relative] = lib.artifact(destination, output)
    for suite in registry["official_suites"].values():
        relative = suite["manifest"]
        if relative not in repository_records:
            destination = provenance / "repository" / relative
            lib.snapshot(repo / relative, destination, suite["manifest_sha256"])
            repository_records[relative] = lib.artifact(destination, output)

    staged_records: dict[str, Any] = {}
    staged_paths = set(registry["staged_source_hashes"])
    staged_paths.add("rtl/technology/physical_staging/physical_staging_manifest.json")
    for candidate in registry["candidates"].values():
        staged_paths.add(candidate["staged_filelist"])
    for relative in sorted(staged_paths):
        if relative == "rtl/technology/physical_staging/physical_staging_manifest.json":
            expected = registry["staged_manifest"]["sha256"]
        elif relative in registry["staged_source_hashes"]:
            expected = registry["staged_source_hashes"][relative]
        else:
            owner = next(
                item for item in registry["candidates"].values()
                if item["staged_filelist"] == relative
            )
            expected = owner["staged_filelist_sha256"]
        destination = provenance / "staged" / relative
        lib.snapshot(staged / relative, destination, expected)
        staged_records[relative] = lib.artifact(destination, output)

    closure_records: dict[str, Any] = {}
    tb_closure_records: dict[str, Any] = {}
    for name, sources in closures.items():
        candidate = registry["candidates"][name]
        closure_records[name] = {
            "top": candidate["top"],
            "staged_filelist": staged_records[candidate["staged_filelist"]],
            "sources": [staged_records[source] for source in sources],
            "include": staged_records["rtl/technology/p6/w2_p6_tech_select.svh"],
        }
        tb_filelist = candidate["tb_filelist"]
        tb_sources = [
            line.strip()
            for line in lib.stable_bytes(provenance / "repository" / tb_filelist)
            .decode("utf-8")
            .splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        tb_closure_records[name] = {
            "filelist": repository_records[tb_filelist],
            "sources": [repository_records[relative] for relative in tb_sources],
        }
    return {
        "repository": repository_records,
        "staged": staged_records,
        "candidate_closures": closure_records,
        "tb_closures": tb_closure_records,
    }


def generate_suites(
    output: Path,
    snapshot_records: dict[str, Any],
    registry: dict[str, Any],
    official: Any,
    python: Path,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    repo_snapshot = output / "provenance/repository"
    generator = repo_snapshot / "benchmarks/clean_slate_aer/generate_trace.py"
    generated: dict[str, dict[str, dict[str, Any]]] = {}
    suite_artifacts: dict[str, Any] = {}
    for suite, identity in registry["official_suites"].items():
        manifest = repo_snapshot / identity["manifest"]
        trace_root = output / "traces" / suite
        log = output / "provenance/logs" / f"generate-{suite}.log"
        run(
            [str(python), str(generator), "--manifest", str(manifest),
             "--output-dir", str(trace_root)],
            log,
        )
        generated[suite] = lib.validate_generation(
            trace_root, suite, manifest, official
        )
        suite_artifacts[suite] = {
            "manifest": snapshot_records["repository"][identity["manifest"]],
            "generation_index": lib.artifact(trace_root / "generation-index.json", output),
            "generation_log": lib.artifact(log, output),
            "ordered_names": list(generated[suite]),
        }
    subset = lib.prove_capacity_subset(
        generated["full50"], generated["capacity22"], official
    )
    workload = registry["activity_workload"]
    for suite in ("full50", "capacity22"):
        record = generated[suite].get(workload["name"])
        if record is None:
            raise lib.ActivityError(f"activity workload missing from {suite}")
        if (
            lib.digest(record["trace"]) != workload["trace_sha256"]
            or lib.digest(record["manifest"]) != workload["run_manifest_sha256"]
        ):
            raise lib.ActivityError(f"activity workload bytes differ in {suite}")
    return generated, {
        "suites": suite_artifacts,
        "capacity22_subset_proof": subset,
        "activity_workload": workload,
    }


def compile_inputs(
    output: Path,
    registry: dict[str, Any],
    closures: dict[str, list[str]],
    candidate: str,
) -> list[str]:
    staged_snapshot = output / "provenance/staged"
    repo_snapshot = output / "provenance/repository"
    sources = [str(staged_snapshot / relative) for relative in closures[candidate]]
    tb_filelist = repo_snapshot / registry["candidates"][candidate]["tb_filelist"]
    tb_sources: list[str] = []
    for raw in lib.stable_bytes(tb_filelist).decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("+", "-")):
            raise lib.ActivityError("TB filelist options are forbidden")
        tb_sources.append(str(repo_snapshot / line))
    if len(tb_sources) != 6 or len(tb_sources) != len(set(tb_sources)):
        raise lib.ActivityError("TB filelist exact source count/order mismatch")
    return [
        "+define+W2_P6_TECH_GENERIC",
        f"+incdir+{staged_snapshot / 'rtl/technology/p6'}",
        *sources,
        *tb_sources,
    ]


def produce_candidate(
    output: Path,
    xrun: Path,
    tools: dict[str, Any],
    python: Path,
    registry: dict[str, Any],
    closures: dict[str, list[str]],
    snapshot_records: dict[str, Any],
    generated: dict[str, dict[str, dict[str, Any]]],
    candidate: str,
) -> Path:
    row = registry["candidates"][candidate]
    run_root = output / "candidates" / candidate
    run_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    activity = generated["full50"][lib.ACTIVITY_WORKLOAD]
    preparer = output / "provenance/repository/benchmarks/clean_slate_aer/prepare_sv_trace.py"
    prepared = run_root / "input.svtrace"
    prepare_log = run_root / "prepare.log"
    run(
        [str(python), str(preparer), "--trace", str(activity["trace"]),
         "--run-manifest", str(activity["manifest"]), "--output", str(prepared),
         "--addr-width", "16"],
        prepare_log,
    )
    snapshot_name = f"w2_5ns_{candidate}_common_activity"
    library = run_root / "xcelium.d"
    elaborate_log = run_root / "elaborate.log"
    elaborate_driver_log = run_root / "elaborate-driver.log"
    elaborate_command = [
        str(xrun), "-64bit", "-sv", "-timescale", "1ns/1ps",
        "-top", "aer_clean_tb", "-snapshot", snapshot_name, "-elaborate",
        "-access", "+r", "-xmlibdirname", str(library),
        "-defparam", "aer_clean_tb.NUM_SOURCES=16",
        "-defparam", "aer_clean_tb.ADDR_WIDTH=16",
        "-defparam", "aer_clean_tb.RETIRE_LANES=2",
        "-defparam", "aer_clean_tb.FIFO_DEPTH=0",
        *compile_inputs(output, registry, closures, candidate),
        "-l", str(elaborate_log),
    ]
    run(elaborate_command, elaborate_driver_log, cwd=output)

    raw_vcd = run_root / "raw.vcd"
    window_path = run_root / "window.txt"
    summary = run_root / "summary.csv"
    events = run_root / "events.csv"
    run_log = run_root / "run.log"
    run_driver_log = run_root / "run-driver.log"
    for target in (raw_vcd, window_path, summary, events, run_log, run_driver_log):
        if target.exists():
            raise lib.ActivityError(f"runtime output unexpectedly exists: {target}")
    execute_command = [
        str(xrun), "-64bit", "-R", "-snapshot", snapshot_name,
        "-xmlibdirname", str(library), "+CLEAN_TEST=trace",
        f"+CANDIDATE={row['candidate_id']}",
        f"+TRACE_NAME={lib.ACTIVITY_WORKLOAD}", f"+TRACE_FILE={prepared}",
        f"+METRICS={summary}", f"+EVENT_METRICS={events}",
        f"+ACTIVITY_RAW_VCD={raw_vcd}", f"+ACTIVITY_WINDOW={window_path}",
        "-l", str(run_log),
    ]
    run(execute_command, run_driver_log, cwd=output)
    log_text = lib.stable_bytes(run_log).decode("utf-8", errors="replace")
    if log_text.count(lib.REQUIRED_PASS) != 1:
        raise lib.ActivityError(f"{candidate}: exact common PASS marker missing/duplicated")
    probe_marker = f"W2_5NS_ACTIVITY_PROBE_PASS candidate={row['candidate_id']}"
    if log_text.count(probe_marker) != 1:
        raise lib.ActivityError(f"{candidate}: exact probe PASS marker missing/duplicated")

    summary_row = lib.parse_summary(summary, row["candidate_id"])
    lib.validate_events(events, row["candidate_id"], summary_row)
    window = lib.validate_window(
        window_path, row["candidate_id"], int(summary_row["measurement_cycles"])
    )
    vcd = run_root / "activity.vcd"
    saif = run_root / "activity.saif"
    lib.rebase_vcd(
        raw_vcd, vcd, window["start_tick_1ps"], window["end_tick_1ps"]
    )
    saif_statistics = lib.vcd_to_saif(vcd, saif, row["candidate_id"])
    if saif_statistics["duration_tick_1ps"] != window["duration_tick_1ps"]:
        raise lib.ActivityError(f"{candidate}: SAIF duration differs from window")

    artifact_paths = {
        "prepared_trace": prepared,
        "prepare_log": prepare_log,
        "elaborate_log": elaborate_log,
        "elaborate_driver_log": elaborate_driver_log,
        "run_log": run_log,
        "run_driver_log": run_driver_log,
        "common_summary": summary,
        "common_events": events,
        "window": window_path,
        "raw_vcd": raw_vcd,
        "activity_vcd": vcd,
        "activity_saif": saif,
    }
    receipt = {
        "schema": "k2_w2_5ns_candidate_activity_receipt_v1",
        "status": "PROVEN_REAL_XCELIUM_ACTIVITY",
        "candidate": candidate,
        "candidate_id": row["candidate_id"],
        "top": row["top"],
        "scope": lib.EXPECTED_SCOPE,
        "power_mode": "activity_annotated",
        "vectorless": False,
        "clock": {"ref_period_ps": 5000, "sample_period_ps": 5000, "sample_phase_ps": 1250},
        "workload": {
            "name": lib.ACTIVITY_WORKLOAD,
            "trace_sha256": lib.digest(activity["trace"]),
            "run_manifest_sha256": lib.digest(activity["manifest"]),
            "full50_member": True,
            "capacity22_member": True,
        },
        "workload_artifacts": {
            "trace": lib.artifact(activity["trace"], output),
            "run_manifest": lib.artifact(activity["manifest"], output),
        },
        "candidate_source_identity": snapshot_records["candidate_closures"][candidate],
        "tb_source_identity": snapshot_records["tb_closures"][candidate],
        "tools": tools,
        "commands": {"elaborate": elaborate_command, "execute": execute_command},
        "summary": {
            key: int(summary_row[key]) for key in (
                "generated", "source_overrun", "accepted", "delivered", "errors",
                "measurement_delivered", "measurement_cycles",
            )
        },
        "window": window,
        "saif_statistics": saif_statistics,
        "artifacts": {
            name: lib.artifact(path, output) for name, path in artifact_paths.items()
        },
    }
    receipt_path = run_root / "receipt.json"
    lib.verify_candidate_receipt(receipt, output)
    lib.write_exclusive(
        receipt_path,
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    lib.seal_receipt(receipt_path)
    return receipt_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--staged-root", type=Path, default=Path("/tmp/k2-phys-w2-techmap")
    )
    parser.add_argument(
        "--xrun", type=Path,
        default=Path("/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun"),
    )
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    staged = args.staged_root.resolve(strict=True)
    if args.output.exists() or args.output.is_symlink():
        raise lib.ActivityError(f"refusing existing output root: {args.output}")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    output = args.output.resolve(strict=True)

    registry = lib.load_registry(repo)
    lib.verify_repository_inputs(repo, registry)
    closures = lib.verify_staged_inputs(staged, registry)
    snapshots = snapshot_inputs(repo, staged, output, registry, closures)
    official = lib.load_official(output / "provenance/repository", registry)
    python_tool = verify_python(output)
    xcelium_tool = verify_xrun(args.xrun, registry, output)
    tools = {"python": python_tool, "xcelium": xcelium_tool}
    generated, suite_identity = generate_suites(
        output, snapshots, registry, official, Path(python_tool["path"])
    )
    candidate_receipts: dict[str, Any] = {}
    for candidate in ("fovea", "a2", "a3"):
        receipt_path = produce_candidate(
            output, args.xrun, tools, Path(python_tool["path"]), registry,
            closures, snapshots, generated, candidate,
        )
        candidate_receipts[candidate] = lib.artifact(receipt_path, output)
    campaign = {
        "schema": "k2_w2_5ns_common_activity_campaign_receipt_v1",
        "status": "PROVEN_REAL_XCELIUM_ACTIVITY_THREE_CANDIDATES",
        "power_mode": "activity_annotated",
        "vectorless": False,
        "registry": lib.artifact(output / "provenance/repository" / lib.REGISTRY_RELATIVE, output),
        "tools": tools,
        "suite_identity": suite_identity,
        "candidate_receipts": candidate_receipts,
    }
    lib.require_digest(args.xrun, registry["xcelium"]["sha256"])
    lib.require_digest(Path(python_tool["path"]), python_tool["sha256"])
    lib.verify_campaign_receipt(campaign, output)
    receipt_path = output / "campaign-receipt.json"
    lib.write_exclusive(
        receipt_path,
        (json.dumps(campaign, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    lib.seal_receipt(receipt_path)
    receipt_sha = lib.digest(receipt_path)
    sentinel = output / "campaign.success"
    lib.write_exclusive(
        sentinel,
        f"W2_5NS_COMMON_ACTIVITY_SUCCESS receipt_sha256={receipt_sha}\n".encode(),
    )
    lib.seal_receipt(sentinel)
    print(
        "W2_5NS_COMMON_ACTIVITY_SUCCESS "
        f"candidates=3 full50=50 capacity22_subset=22 output={output}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except lib.ActivityError as exc:
        print(f"W2_5NS_COMMON_ACTIVITY_FAIL error={exc}", file=sys.stderr)
        raise SystemExit(1)
