#!/usr/bin/env python3
"""Run the A5 activity-directory model on official 50/22 traces and proxies."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from activity_directory_model import ActivityDirectory, Event, FlatScan, simulate

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import common_suite_official as official


POINTERS = (1, 2, 4, 8)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def locate_generator(project: Path, explicit: Path | None) -> Path:
    candidates = ([explicit] if explicit else []) + [
        project.parent / "a1/benchmarks/clean_slate_aer/generate_trace.py",
        project / "benchmarks/clean_slate_aer/generate_trace.py",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and "pairwise_contention" in candidate.read_text():
            return candidate.resolve()
    raise SystemExit("official v4 generator not found; pass --generator")


def load_events(path: Path, scale: int = 1) -> list[Event]:
    events: list[Event] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            events.append(Event(int(row["tb_only_event_id"]),
                                int(row["logical_source"]) * scale,
                                int(row["occurrence_cycle"])))
    return events


def run_config(name: str, events: list[Event], source_count: int, stim_cycles: int,
               pointers: int | None) -> dict[str, object]:
    policy = FlatScan(source_count) if pointers is None else ActivityDirectory(
        source_count, pointers, watchdog_limit=16)
    result = simulate(name, events, source_count, stim_cycles, policy)
    row = result.summary()
    row["config"] = "flat" if pointers is None else f"L{pointers}"
    row["pointers"] = 0 if pointers is None else pointers
    row["selector_activity_proxy"] = (
        int(row["select_examined_bits"]) + int(row["truth_guard_bits"]) +
        int(row["tag_comparisons"]) + int(row["state_toggles"])
    )
    return row


def aggregate(rows: list[dict[str, object]], suite: str) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    configs = ("flat", "L1", "L2", "L4", "L8")
    for config in configs:
        selected = [row for row in rows if row["suite"] == suite and row["config"] == config]
        events = sum(int(row["delivered"]) for row in selected)
        accepted = sum(int(row["accepted"]) for row in selected)
        stim = sum(int(row["stim_cycles"]) for row in selected)
        fixed = sum(int(row["fixed_window_delivered"]) for row in selected)
        output.append({
            "suite": suite, "config": config, "runs": len(selected),
            "events": events, "accepted": accepted,
            "overrun": sum(int(row["source_overrun"]) for row in selected),
            "weighted_avg_wait": sum(float(row["avg_wait"]) * int(row["accepted"])
                                     for row in selected) / accepted if accepted else 0.0,
            "weighted_p95_wait": sum(float(row["p95_wait"]) * int(row["accepted"])
                                     for row in selected) / accepted if accepted else 0.0,
            "max_wait": max((int(row["max_wait"]) for row in selected), default=0),
            "fixed_window_throughput": fixed / stim if stim else 0.0,
            "fairness_mean": sum(float(row["fairness"]) for row in selected) / len(selected),
            "hint_hits": sum(int(row["hint_hits"]) for row in selected),
            "hint_misses": sum(int(row["hint_misses"]) for row in selected),
            "directory_updates": sum(int(row["directory_updates"]) for row in selected),
            "update_overflows": sum(int(row["update_overflows"]) for row in selected),
            "fallback_entries": sum(int(row["fallback_entries"]) for row in selected),
            "overflow_triggers": sum(int(row["overflow_triggers"]) for row in selected),
            "fallback_recovery_latency": (
                sum(int(row["fallback_recovery_cycles"]) for row in selected) /
                sum(int(row["fallback_entries"]) for row in selected)
                if sum(int(row["fallback_entries"]) for row in selected) else 0.0),
            "miss_recovery_latency": (
                sum(int(row["miss_recovery_cycles"]) for row in selected) /
                sum(int(row["hint_misses"]) for row in selected)
                if sum(int(row["hint_misses"]) for row in selected) else 0.0),
            "overflow_recovery_latency": (
                sum(int(row["overflow_recovery_cycles"]) for row in selected) /
                sum(int(row["overflow_triggers"]) for row in selected)
                if sum(int(row["overflow_triggers"]) for row in selected) else 0.0),
            "avg_hit_wait": (
                sum(float(row["avg_hit_wait"]) * int(row["hit_accepts"])
                    for row in selected) /
                sum(int(row["hit_accepts"]) for row in selected)
                if sum(int(row["hit_accepts"]) for row in selected) else 0.0),
            "avg_fallback_wait": (
                sum(float(row["avg_fallback_wait"]) * int(row["fallback_accepts"])
                    for row in selected) /
                sum(int(row["fallback_accepts"]) for row in selected)
                if sum(int(row["fallback_accepts"]) for row in selected) else 0.0),
            "avg_overflow_wait": (
                sum(float(row["avg_overflow_wait"]) * int(row["overflow_fallback_accepts"])
                    for row in selected) /
                sum(int(row["overflow_fallback_accepts"]) for row in selected)
                if sum(int(row["overflow_fallback_accepts"]) for row in selected) else 0.0),
            "avg_update_to_service": (
                sum(float(row["avg_update_to_service"]) * int(row["hit_accepts"])
                    for row in selected) /
                sum(int(row["hit_accepts"]) for row in selected)
                if sum(int(row["hit_accepts"]) for row in selected) else 0.0),
            "select_examined_bits_per_cycle": (
                sum(int(row["select_examined_bits"]) for row in selected) / stim if stim else 0.0),
            "truth_guard_bits_per_cycle": (
                sum(int(row["truth_guard_bits"]) for row in selected) / stim if stim else 0.0),
            "tag_comparisons_per_event": (
                sum(int(row["tag_comparisons"]) for row in selected) / accepted if accepted else 0.0),
            "state_toggles_per_event": (
                sum(int(row["state_toggles"]) for row in selected) / accepted if accepted else 0.0),
            "selector_activity_per_event": (
                sum(int(row["selector_activity_proxy"]) for row in selected) / accepted
                if accepted else 0.0),
            "state_bits": max((int(row["policy_state_bits"]) for row in selected), default=0),
            "hint_depth_proxy": max((int(row["hint_depth_proxy"]) for row in selected), default=0),
            "fallback_stage_depth_proxy": max(
                (int(row["fallback_stage_depth_proxy"]) for row in selected), default=0),
        })
    baseline = next(row for row in output if row["config"] == "flat")
    for row in output:
        row["throughput_delta_vs_flat"] = (
            float(row["fixed_window_throughput"]) -
            float(baseline["fixed_window_throughput"]))
        row["avg_wait_delta_vs_flat"] = (
            float(row["weighted_avg_wait"]) - float(baseline["weighted_avg_wait"]))
        row["examined_reduction_vs_flat"] = 1.0 - (
            float(row["select_examined_bits_per_cycle"]) /
            float(baseline["select_examined_bits_per_cycle"]))
    return output


def adversarial_events(pointers: int, source_count: int, cycles: int = 512) -> list[Event]:
    active = pointers + 1
    events: list[Event] = []
    event_id = 0
    for source in range(active):
        events.append(Event(event_id, source, 4)); event_id += 1
    for cycle in range(5, cycles):
        source = (cycle - 5) % active
        events.append(Event(event_id, source, cycle)); event_id += 1
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a5-activity-directory"))
    parser.add_argument("--generator", type=Path)
    parser.add_argument("--full-manifest", type=Path,
                        default=Path("tests/common_suite_receipt/fixtures/manifest.neutrality-n16.json"))
    parser.add_argument("--capacity-manifest", type=Path,
                        default=Path("tests/common_suite_receipt/fixtures/manifest.multilane-n16.json"))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True, exist_ok=True)
    traces = args.output / "traces"
    traces.mkdir(exist_ok=True)
    generator = locate_generator(project, args.generator)
    expected_full = official.SUITES["full50"]
    expected_capacity = official.SUITES["capacity22"]
    if sha256(args.full_manifest) != expected_full["manifest_sha256"]:
        raise SystemExit("full50 manifest bytes do not match committed official identity")
    if sha256(args.capacity_manifest) != expected_capacity["manifest_sha256"]:
        raise SystemExit("capacity22 manifest bytes do not match committed official identity")
    command = [sys.executable, str(generator), "--manifest", str(args.full_manifest.resolve()),
               "--output-dir", str(traces)]
    result = subprocess.run(command, cwd=project, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=os.environ.copy())
    (args.output / "generation.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        sys.stdout.write(result.stdout)
        return result.returncode

    full = json.loads(args.full_manifest.read_text(encoding="utf-8"))["runs"]
    capacity = json.loads(args.capacity_manifest.read_text(encoding="utf-8"))["runs"]
    cap_names = {row["name"] for row in capacity}
    if len(full) != 50 or len(cap_names) != 22:
        raise SystemExit(f"official cardinality mismatch full={len(full)} capacity={len(cap_names)}")
    if not cap_names <= {row["name"] for row in full}:
        raise SystemExit("capacity22 is not an exact subset of full50")
    if tuple(row["name"] for row in full) != expected_full["names"]:
        raise SystemExit("full50 names/order differ from committed official identity")
    if tuple(row["name"] for row in capacity) != expected_capacity["names"]:
        raise SystemExit("capacity22 names/order differ from committed official identity")

    per_run: list[dict[str, object]] = []
    for index, declared in enumerate(full, start=1):
        name = declared["name"]
        trace = traces / f"{name}.events.jsonl"
        run_manifest = json.loads((traces / f"{name}.manifest.json").read_text())
        if sha256(trace) != run_manifest["trace_sha256"]:
            raise SystemExit(f"trace hash mismatch: {name}")
        if run_manifest["trace_sha256"] != official.TRACE_SHA256[name]:
            raise SystemExit(f"trace differs from committed official SHA: {name}")
        events = load_events(trace)
        configs = [(None, "flat"), *((value, f"L{value}") for value in POINTERS)]
        for pointers, _ in configs:
            row = run_config(name, events, 16, int(declared["stim_cycles"]), pointers)
            row["suite"] = "full50_n16"
            row["workload"] = declared["workload"]
            row["trace_sha256"] = run_manifest["trace_sha256"]
            per_run.append(row)
            if name in cap_names:
                cap_row = dict(row); cap_row["suite"] = "capacity22_n16"; per_run.append(cap_row)

        scaled = load_events(trace, scale=4)
        for pointers, _ in configs:
            row = run_config(name, scaled, 64, int(declared["stim_cycles"]), pointers)
            row["suite"] = "full50_n64_scaling_proxy"
            row["workload"] = declared["workload"]
            row["trace_sha256"] = run_manifest["trace_sha256"]
            per_run.append(row)
        print(f"[{index:02d}/50] {name}")

    adversarial: list[dict[str, object]] = []
    for source_count in (16, 64):
        for pointers in POINTERS:
            events = adversarial_events(pointers, source_count)
            for config in (None, pointers):
                row = run_config(f"lplus1_L{pointers}_N{source_count}", events,
                                 source_count, 512, config)
                row["suite"] = "adversarial_lplus1"
                row["workload"] = "lplus1_cycling"
                row["tested_L"] = pointers
                adversarial.append(row)

    write_csv(args.output / "a5-activity-directory-per-run.csv", per_run)
    write_csv(args.output / "a5-activity-directory-adversarial.csv", adversarial)
    aggregate_rows: list[dict[str, object]] = []
    for suite in ("full50_n16", "capacity22_n16", "full50_n64_scaling_proxy"):
        aggregate_rows.extend(aggregate(per_run, suite))
    write_csv(args.output / "a5-activity-directory-aggregate.csv", aggregate_rows)

    # Conservative model gate: no correctness failures, official fixed-window
    # throughput loss <= 1%, and N64 selector-bit examination reduction >= 40%.
    cap_flat = next(row for row in aggregate_rows
                    if row["suite"] == "capacity22_n16" and row["config"] == "flat")
    candidates = []
    for config in ("L1", "L2", "L4", "L8"):
        full16 = next(row for row in aggregate_rows
                      if row["suite"] == "full50_n16" and row["config"] == config)
        cap16 = next(row for row in aggregate_rows
                     if row["suite"] == "capacity22_n16" and row["config"] == config)
        scale64 = next(row for row in aggregate_rows
                       if row["suite"] == "full50_n64_scaling_proxy" and row["config"] == config)
        candidates.append({
            "config": config,
            "correctness": all(int(row["accepted"]) == int(row["delivered"])
                               for row in per_run if row["config"] == config),
            "full50_throughput_delta": full16["throughput_delta_vs_flat"],
            "capacity22_throughput_delta": cap16["throughput_delta_vs_flat"],
            "capacity22_relative_throughput": (
                float(cap16["fixed_window_throughput"]) /
                float(cap_flat["fixed_window_throughput"])),
            "n64_examined_reduction": scale64["examined_reduction_vs_flat"],
            "n64_hint_depth": scale64["hint_depth_proxy"],
            "n64_fallback_depth": scale64["fallback_stage_depth_proxy"],
            "state_bits_n64": scale64["state_bits"],
        })
    passing = [row for row in candidates if row["correctness"] and
               float(row["capacity22_relative_throughput"]) >= 0.99 and
               float(row["n64_examined_reduction"]) >= 0.40]
    verdict = {
        "schema_version": 1,
        "gate": {"min_capacity22_relative_throughput": 0.99,
                 "min_n64_examined_reduction": 0.40,
                 "correctness_required": True},
        "candidates": candidates,
        "verdict": "GO" if passing else "HOLD",
        "selected": min(passing, key=lambda row: int(row["state_bits_n64"]))["config"]
                    if passing else None,
        "note": "N64 is a source-ID expansion scaling proxy, not an official N64 trace result.",
    }
    (args.output / "a5-activity-directory-go-gate.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"A5_ACTIVITY_DIRECTORY_SWEEP_PASS verdict={verdict['verdict']} "
          f"selected={verdict['selected']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
