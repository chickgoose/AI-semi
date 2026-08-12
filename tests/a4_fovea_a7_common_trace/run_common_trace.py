#!/usr/bin/env python3
"""Fail-closed local RTL replay of official generator-v4 traces through Fovea+A7."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any


OWNER_COMMIT = "e9f27e6aed302491011a5deb803a7b42a0c712b3"
GENERATOR_SHA = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
PREPARER_SHA = "245078d3e1f6ed496a0de328f1568cb0a8302397ce9f5544021415f0afad2826"
OFFICIAL_SHA = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
MANIFEST_SHA = {
    "full50": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "capacity22": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
OWNER_FILES = {
    "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv":
        "b125dc3cfc51f5c898d41f9b82660c346aafc9c7613433cee622514eb3456ec7",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv":
        "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv":
        "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv":
        "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv":
        "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv":
        "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv":
        "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
}
FOVEA_FILES = {
    "arbiter2.v": "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
    "arbiter4_tree.v": "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
    "aer_tx16_trad_rowcol_fovea.v": "353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e",
}
EVENT_COLUMNS = [
    "candidate", "test", "seed", "load_pct", "tb_only_event_id",
    "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
    "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
]
SUMMARY_COLUMNS = [
    "candidate", "test", "seed", "load_pct", "stim_cycles", "generated",
    "source_overrun", "accepted", "delivered", "errors", "total_cycles",
    "avg_e2e_latency", "max_e2e_latency", "avg_internal_latency",
    "max_internal_latency", "throughput", "fairness", "max_request_wait",
    "avg_timing_error", "max_timing_error", "measurement_delivered",
    "measurement_cycles",
]
CANDIDATE = "a7-weighted-fovea-ddr"


class RunError(RuntimeError):
    pass


def expected_load_pct(load: Any) -> int:
    load_milli = Decimal(str(load)) * Decimal(1000)
    if load_milli != load_milli.to_integral_value():
        raise RunError("load cannot be represented in frozen millipercent format")
    return (int(load_milli) + 5) // 10


def validate_load_pct(actual: int, load: Any) -> None:
    if actual != expected_load_pct(load):
        raise RunError("load_pct does not use frozen nearest-integer rounding")


def bytes_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RunError(f"not a regular file: {path}")
    return bytes_digest(path.read_bytes())


def snapshot(source: Path, destination: Path, expected: str) -> Path:
    if digest(source) != expected:
        raise RunError(f"provenance mismatch: {source}")
    payload = source.read_bytes()
    if bytes_digest(payload) != expected:
        raise RunError(f"provenance changed while reading: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(payload)
    return destination


def run(command: list[str], log: Path) -> None:
    with log.open("xb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                close_fds=True, check=False)
    if result.returncode:
        raise RunError(f"command failed ({result.returncode}): {command[0]}")


def extract_owner(repo: Path, destination: Path) -> list[Path]:
    extracted = []
    for logical, expected in OWNER_FILES.items():
        result = subprocess.run(
            ["git", "-C", str(repo), "show", f"{OWNER_COMMIT}:{logical}"],
            capture_output=True, close_fds=True, check=False)
        if result.returncode or bytes_digest(result.stdout) != expected:
            raise RunError(f"pinned owner object mismatch: {logical}")
        path = destination / Path(logical).name
        with path.open("xb") as stream:
            stream.write(result.stdout)
        extracted.append(path)
    return extracted


def discover_verilator(explicit: Path | None) -> Path:
    raw = explicit or (Path(os.environ["AER_VERILATOR"])
                       if os.environ.get("AER_VERILATOR") else None)
    raw = raw or (Path(os.environ["VERILATOR"])
                  if os.environ.get("VERILATOR") else None)
    raw = raw or (Path(shutil.which("verilator")) if shutil.which("verilator") else None)
    raw = raw or Path("/tmp/a7-sim-bin/verilator")
    resolved = raw.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RunError("Verilator unavailable")
    return resolved


def load_official(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("a4_common_suite_official", path)
    if spec is None or spec.loader is None:
        raise RunError("cannot load pinned official suite specification")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.GENERATOR_VERSION != "4.0":
        raise RunError("official suite is not generator-v4")
    return module


def validate_generation(index: dict[str, Any], official: Any, suite: str,
                        trace_root: Path, manifest: Path) -> list[dict[str, Any]]:
    if set(index) != {"schema_version", "generator_version", "input_manifest", "runs"}:
        raise RunError("generation-index schema mismatch")
    rows = index["runs"]
    expected_names = list(official.SUITES[suite]["names"])
    names = [row.get("run", {}).get("name") for row in rows]
    if index["schema_version"] != 1 or index["generator_version"] != "4.0":
        raise RunError("generation-index version mismatch")
    if Path(index["input_manifest"]).name != manifest.name:
        raise RunError("generation-index manifest mismatch")
    if names != expected_names or len(names) != len(set(names)):
        raise RunError("official stem order/cardinality mismatch")
    for row in rows:
        name = row["run"]["name"]
        if (row.get("trace_sha256") != official.TRACE_SHA256[name] or
                row.get("event_identity_mode") != "address_only" or
                row.get("dut_address_fields") != ["logical_source"] or
                row.get("dut_payload_fields") != []):
            raise RunError(f"{name}: official address-only provenance mismatch")
        trace = trace_root / row["trace_file"]
        metadata = trace_root / f"{name}.manifest.json"
        if digest(trace) != row["trace_sha256"] or json.loads(metadata.read_text()) != row:
            raise RunError(f"{name}: generated trace/metadata mismatch")
    return rows


def validate_result(metadata: dict[str, Any], events: Path, summary: Path,
                    log: Path, expected_first_occurrence: int) -> dict[str, int | str]:
    name = metadata["run"]["name"]
    expected_count = metadata["event_count"]
    expected_seed = metadata["run"]["seed"]
    expected_load = expected_load_pct(metadata["run"]["load"])
    stim_cycles = metadata["run"]["stim_cycles"]
    with events.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != EVENT_COLUMNS:
            raise RunError(f"{name}: event result schema mismatch")
        rows = list(reader)
    if len(rows) != expected_count:
        raise RunError(f"{name}: event result cardinality mismatch")
    if not rows or int(rows[0]["occurrence_cycle"]) != expected_first_occurrence:
        raise RunError(f"{name}: first occurrence provenance mismatch")
    counts = {"delivered": 0, "source_overrun": 0, "accepted": 0, "pending": 0}
    delivered_in_measurement_from_events = 0
    for expected_id, row in enumerate(rows):
        try:
            event_id = int(row["tb_only_event_id"])
            source = int(row["logical_source"])
            source_count = int(row["source_count"])
            seed = int(row["seed"])
            load = int(row["load_pct"])
        except ValueError as exc:
            raise RunError(f"{name}: non-integer result provenance") from exc
        validate_load_pct(load, metadata["run"]["load"])
        if (event_id != expected_id or row["candidate"] != CANDIDATE or
                row["test"] != name or source_count != 16 or seed != expected_seed or
                load != expected_load or not 0 <= source < 16):
            raise RunError(f"{name}: result provenance mismatch event={expected_id}")
        state = row["event_state"]
        if state not in counts:
            raise RunError(f"{name}: unknown event state {state!r}")
        counts[state] += 1
        if state == "delivered":
            if not row["accept_cycle"] or not row["delivery_cycle"]:
                raise RunError(f"{name}: delivered event lacks cycle evidence")
            if int(row["delivery_cycle"]) - int(row["accept_cycle"]) != 2:
                raise RunError(f"{name}: consumer latency is not exactly +2")
            if int(row["delivery_cycle"]) < stim_cycles:
                delivered_in_measurement_from_events += 1
        elif row["accept_cycle"] or row["delivery_cycle"]:
            raise RunError(f"{name}: non-delivered event gained accept/delivery evidence")
    if counts["accepted"] or counts["pending"]:
        raise RunError(f"{name}: run did not fully drain")
    if counts["delivered"] + counts["source_overrun"] != expected_count:
        raise RunError(f"{name}: conservation mismatch")

    with summary.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != SUMMARY_COLUMNS:
            raise RunError(f"{name}: summary schema mismatch")
        summaries = list(reader)
    if len(summaries) != 1:
        raise RunError(f"{name}: summary cardinality mismatch")
    item = summaries[0]
    if (item["candidate"] != CANDIDATE or item["test"] != name or
            int(item["seed"]) != expected_seed or int(item["load_pct"]) != expected_load or
            int(item["stim_cycles"]) != stim_cycles or
            int(item["generated"]) != expected_count or
            int(item["source_overrun"]) != counts["source_overrun"] or
            int(item["accepted"]) != counts["delivered"] or
            int(item["delivered"]) != counts["delivered"] or
            int(item["errors"]) != 0 or float(item["avg_internal_latency"]) != 2.0 or
            int(item["max_internal_latency"]) != 2):
        raise RunError(f"{name}: summary/result mismatch")
    measurement_delivered = int(item["measurement_delivered"])
    measurement_cycles = int(item["measurement_cycles"])
    expected_throughput = Decimal(measurement_delivered) / Decimal(stim_cycles)
    if (measurement_cycles != stim_cycles or
            measurement_delivered != delivered_in_measurement_from_events or
            measurement_delivered > counts["delivered"] or
            abs(Decimal(item["throughput"]) - expected_throughput) > Decimal("0.000001")):
        raise RunError(f"{name}: frozen measurement-window mismatch")

    lines = log.read_text(encoding="utf-8").splitlines()
    marker = (f"A4_FOVEA_A7_COMMON_TRACE_PASS name={name} generated={expected_count} "
              f"accepted={counts['delivered']} delivered={counts['delivered']} "
              f"overrun={counts['source_overrun']} latency=2")
    if lines.count(marker) != 1:
        raise RunError(f"{name}: exact PASS marker missing or duplicated")
    phase_marker = "A4_COMMON_TRACE_RESET_PHASE_PASS fall_to_ref=4ns scope=initial_only"
    if lines.count(phase_marker) != 1:
        raise RunError(f"{name}: reset phase marker missing or duplicated")
    epoch_marker = "A4_COMMON_TRACE_EPOCH_PASS activation=negedge first_stim_cycle=0 sim_cycle=0"
    if lines.count(epoch_marker) != 1:
        raise RunError(f"{name}: traffic epoch marker missing or duplicated")
    first_occurrence_marker = (
        f"A4_COMMON_TRACE_FIRST_OCCURRENCE_PASS "
        f"occurrence={expected_first_occurrence} sim_cycle={expected_first_occurrence}")
    if lines.count(first_occurrence_marker) != 1:
        raise RunError(f"{name}: first occurrence epoch marker missing or duplicated")
    quiet_marker = (f"A4_COMMON_TRACE_QUIET_PASS cycles=8 "
                    f"accepted={counts['delivered']} delivered={counts['delivered']} "
                    f"overrun={counts['source_overrun']}")
    if lines.count(quiet_marker) != 1:
        raise RunError(f"{name}: post-drain quiet marker missing or duplicated")
    return {
        "generated": expected_count,
        "accepted": counts["delivered"],
        "delivered": counts["delivered"],
        "source_overrun": counts["source_overrun"],
        "delivered_in_measurement": measurement_delivered,
        "delivered_after_measurement": counts["delivered"] - measurement_delivered,
        "measurement_cycles": measurement_cycles,
        "throughput": item["throughput"],
        "accepted_not_delivered": counts["accepted"],
        "pending_at_end": counts["pending"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=(
        "smoke", "highload-smoke", "full50", "capacity22"),
                        default="smoke")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--a1-root", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--fovea-dir", type=Path,
        default=Path("/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures"))
    parser.add_argument("--verilator", type=Path)
    args = parser.parse_args(argv)
    runner_path = Path(__file__).resolve()
    tb_path = runner_path.with_name("a4_fovea_a7_common_trace_tb.sv")
    runner_pre = digest(runner_path)
    tb_pre = digest(tb_path)
    try:
        if args.output.exists():
            raise RunError(f"refusing to overwrite output: {args.output}")
        args.output.mkdir(parents=True, mode=0o700)
        verilator = discover_verilator(args.verilator)
        verilator_pre = digest(verilator)
        provenance = args.output / "provenance"
        tools = provenance / "tools"
        generator = snapshot(
            args.a1_root / "benchmarks/clean_slate_aer/generate_trace.py",
            tools / "generate_trace.py", GENERATOR_SHA)
        preparer = snapshot(
            args.a1_root / "benchmarks/clean_slate_aer/prepare_sv_trace.py",
            tools / "prepare_sv_trace.py", PREPARER_SHA)
        official_path = snapshot(args.a1_root / "scripts/common_suite_official.py",
                                 tools / "common_suite_official.py", OFFICIAL_SHA)
        official = load_official(official_path)
        suite = "full50" if args.suite == "smoke" else args.suite
        if args.suite == "highload-smoke":
            suite = "capacity22"
        manifest_name = ("manifest.neutrality-n16.json" if suite == "full50"
                         else "manifest.multilane-n16.json")
        manifest = snapshot(
            args.a1_root / "benchmarks/clean_slate_aer" / manifest_name,
            tools / manifest_name, MANIFEST_SHA[suite])
        if official.SUITES[suite]["manifest_sha256"] != MANIFEST_SHA[suite]:
            raise RunError("official suite/manifest provenance mismatch")
        tb_snapshot = snapshot(tb_path, provenance / tb_path.name, tb_pre)

        source_root = provenance / "sources"
        owner_root = source_root / "owner"
        owner_root.mkdir(parents=True)
        sources = extract_owner(args.repo, owner_root)
        fovea_root = source_root / "fovea"
        for name, expected in FOVEA_FILES.items():
            sources.append(snapshot(args.fovea_dir / name, fovea_root / name, expected))

        trace_root = args.output / "traces"
        run([sys.executable, str(generator), "--manifest", str(manifest),
             "--output-dir", str(trace_root)], args.output / "generator.log")
        index_path = trace_root / "generation-index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        generated_rows = validate_generation(index, official, suite, trace_root, manifest)
        rows = generated_rows
        if args.suite in ("smoke", "highload-smoke"):
            smoke_name = ("core_simultaneous_identity" if args.suite == "smoke"
                          else "uniform_l2p00_s2001")
            rows = [row for row in rows
                    if row["run"]["name"] == smoke_name]
            if len(rows) != 1:
                raise RunError("smoke trace missing or duplicated")

        build = args.output / "obj"
        compile_command = [
            str(verilator), "--binary", "--timing", "-Wall", "-Wno-BLKSEQ",
            "-Wno-SYNCASYNCNET", "-Wno-UNUSEDSIGNAL", "-Wno-UNOPTFLAT",
            "--top-module", "a4_fovea_a7_common_trace_tb", "--Mdir", str(build),
            "-o", "a4_common_trace",
            "-DA7_WEIGHTED_FOVEA_MODULE=aer_tx16_trad_rowcol_fovea",
            *map(str, sources), str(tb_snapshot),
        ]
        compile_log = args.output / "compile.log"
        run(compile_command, compile_log)
        binary = build / "a4_common_trace"
        reports = []
        totals = {key: 0 for key in (
            "generated", "accepted", "delivered", "source_overrun",
            "delivered_in_measurement", "delivered_after_measurement",
            "accepted_not_delivered", "pending_at_end",
        )}
        for row in rows:
            name = row["run"]["name"]
            run_root = args.output / "runs" / name
            run_root.mkdir(parents=True)
            prepared = run_root / f"{name}.svtrace"
            prepare_log = run_root / "prepare.log"
            run([sys.executable, str(preparer), "--trace",
                 str(trace_root / row["trace_file"]), "--run-manifest",
                 str(trace_root / f"{name}.manifest.json"), "--output", str(prepared),
                 "--addr-width", "4"], prepare_log)
            events = run_root / "trace.events.csv"
            summary = run_root / "trace.csv"
            log = run_root / "run.log"
            run([str(binary), f"+TRACE_FILE={prepared}", f"+TRACE_NAME={name}",
                 f"+EVENTS_OUT={events}", f"+SUMMARY_OUT={summary}"], log)
            trace_path = trace_root / row["trace_file"]
            with trace_path.open(encoding="utf-8") as stream:
                first_trace_event = json.loads(stream.readline())
            first_occurrence = first_trace_event.get("occurrence_cycle")
            if isinstance(first_occurrence, bool) or not isinstance(first_occurrence, int):
                raise RunError(f"{name}: invalid first trace occurrence")
            counts = validate_result(row, events, summary, log, first_occurrence)
            for key in totals:
                totals[key] += int(counts[key])
            reports.append({
                "name": name, "workload": row["run"]["workload"],
                "trace_sha256": row["trace_sha256"],
                "run_manifest_sha256": digest(trace_root / f"{name}.manifest.json"),
                "prepared_trace_sha256": digest(prepared),
                "events_sha256": digest(events), "summary_sha256": digest(summary),
                "run_log_sha256": digest(log), **counts,
            })

        version = subprocess.run([str(verilator), "--version"], capture_output=True,
                                 text=True, close_fds=True, check=False)
        if version.returncode or not version.stdout.strip():
            raise RunError("cannot capture Verilator version")
        if (digest(verilator) != verilator_pre or digest(runner_path) != runner_pre or
                digest(tb_path) != tb_pre):
            raise RunError("runner/TB/Verilator changed during execution")
        receipt = {
            "schema": "a4_fovea_a7_common_trace_v4",
            "status": "LOCAL_RTL_TRACE_REPLAY_PASS",
            "suite": args.suite,
            "executed_run_count": len(rows),
            "generated_official_run_count": len(generated_rows),
            "provenance": {
                "owner_hardening_commit": OWNER_COMMIT,
                "owner_synthesizable_files_sha256": OWNER_FILES,
                "canonical_fovea_files_sha256": FOVEA_FILES,
                "generator_sha256": digest(generator),
                "preparer_sha256": digest(preparer),
                "official_spec_sha256": digest(official_path),
                "manifest_sha256": digest(manifest),
                "generation_index_sha256": digest(index_path),
                "runner_sha256_pre_post": runner_pre,
                "tb_sha256_pre_post": tb_pre,
                "verilator_path": str(verilator),
                "verilator_sha256_pre_post": verilator_pre,
                "verilator_version": version.stdout.strip(),
                "compile_log_sha256": digest(compile_log),
                "compiled_binary_sha256": digest(binary),
            },
            "execution_scope": {
                "official_suite": suite,
                "official_stems_generated": len(generated_rows),
                "official_stems_executed": len(rows),
                "smoke_subset": args.suite in ("smoke", "highload-smoke"),
                "capacity22_means": "22_official_runs_not_queue_depth",
            },
            "functional_scope": {
                "event_identity": "address_only_logical_source",
                "sink": "always_ready_only",
                "consumer_observation": "pre_NBA_synchronous",
                "consumer_latency_cycles": 2,
                "reset": "initial_release_only_sample_fall_to_ref_rise_4ns",
                "conservation": "generated=delivered+source_overrun_after_full_drain",
                "ordering": "global_accepted_address_order_exact",
                "measurement_window": (
                    "stim_window_plus_final_service_edge_before_candidate_dependent_drain"),
                "measurement_delivered_definition": "delivery_cycle < stim_cycles",
                "throughput_definition": "delivered_in_measurement/stim_cycles",
                "load_pct_definition": "(load_milli+5)/10_integer",
                "traffic_epoch": "negedge_activation_with_sim_cycle_zero_assertion",
                "post_drain_quiet_guard_cycles": 8,
                "post_drain_quiet_guard": (
                    "retire_valid=0,protocol_fault=0,and_generation_transport_counts_stable"),
            },
            "capacity_accounting": {
                "candidate_event_queue_entries": 0,
                "benchmark_ingress_pending_slots": 16,
                "benchmark_ingress_rule": "one_pending_event_per_source",
                "candidate_sustained_output_cap_events_per_cycle": 1,
                "source_overrun_definition": "occurrence_while_source_pending",
                "free_queue_used": False,
                "totals": totals,
            },
            "hold_scope": [
                "frozen_aer_clean_tb_not_executed",
                "official_common_analyzers_and_receipt_not_executed",
                "midstream_reset_not_covered_by_official_full50_or_capacity22_replay",
                "owner_stale_no_live_negative_not_reexecuted_by_this_trace_replay",
                "sink_backpressure_and_unrelated_clocks_unsupported",
                "physical_and_PPA_qualification_not_claimed",
            ],
            "runs": reports,
        }
        receipt_path = args.output / "receipt.json"
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
        print(f"PASS status={receipt['status']} suite={args.suite} "
              f"runs={len(reports)} generated={totals['generated']} "
              f"delivered={totals['delivered']} overrun={totals['source_overrun']} "
              f"output={args.output}")
        print("HOLD frozen_common_tb analyzers receipt physical midstream_reset backpressure")
        return 0
    except (OSError, ValueError, RunError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
