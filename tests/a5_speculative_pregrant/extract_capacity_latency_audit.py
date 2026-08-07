#!/usr/bin/env python3
"""Normalize current A2/A4/A6/A7/A8/A9 evidence without rerunning designs.

Missing evidence stays blank.  In particular, this script never synthesizes a
percentile from an average and never converts codec/link-internal counters into
logical event throughput.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
from typing import Iterable


FIELDS = [
    "track", "candidate", "trace", "workload", "seed", "evidence",
    "declared_offered_epc", "realized_offered_epc", "offered", "accepted",
    "delivered", "measurement_delivered", "measurement_cycles",
    "fixed_window_epc", "retire_lanes", "event_per_cycle_per_lane",
    "p50_e2e", "p95_e2e", "p99_e2e", "max_request_wait", "overrun",
    "source_boundary_slots", "internal_event_slots", "retire_ceiling_epc",
    "errors", "notes",
]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def nearest_rank(values: list[int], percentile: int) -> str:
    if not values:
        return ""
    ordered = sorted(values)
    return str(ordered[math.ceil(percentile * len(ordered) / 100) - 1])


def manifest_index(path: Path) -> dict[str, dict[str, object]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {str(run["name"]): run for run in manifest["runs"]}


def report_group(name: str) -> str:
    if name.startswith("uniform_"):
        return "uniform"
    if name.startswith("moving_hotspot_single_"):
        return "moving_hotspot_single"
    if name.startswith("moving_hotspot_multi_"):
        return name.rsplit("_s", 1)[0]
    if name.startswith("phase_transition_"):
        return "phase_transition"
    if name.startswith("timing_pair_"):
        return "timing_pair"
    return name


def trace_for_event(event: dict[str, str],
                    manifest: dict[str, dict[str, object]]) -> str:
    matches = [
        name for name, run in manifest.items()
        if str(run["seed"]) == event["seed"] and
        report_group(name) == event["test"] and
        int((Decimal(str(run["load"])) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)) == int(event["load_pct"])
    ]
    if len(matches) != 1:
        raise RuntimeError(f"event row does not identify one trace: {event} -> {matches}")
    return matches[0]


def delivered_latencies(event_rows: Iterable[dict[str, str]]) -> list[int]:
    return [
        int(row["delivery_cycle"]) - int(row["occurrence_cycle"])
        for row in event_rows
        if row.get("event_state") == "delivered" and row.get("delivery_cycle")
    ]


def canonical_summary(*, track: str, candidate: str, trace: str,
                      manifest: dict[str, dict[str, object]], lanes: int,
                      internal_slots: int, summary: dict[str, str],
                      event_rows: list[dict[str, str]] | None,
                      evidence: str, notes: str = "") -> dict[str, object]:
    declared = manifest[trace]
    stim = int(declared["stim_cycles"])
    offered = int(summary["generated"])
    fixed = float(summary["throughput"])
    latencies = delivered_latencies(event_rows or [])
    return {
        "track": track, "candidate": candidate, "trace": trace,
        "workload": declared["workload"], "seed": declared["seed"],
        "evidence": evidence, "declared_offered_epc": declared["load"],
        "realized_offered_epc": offered / stim, "offered": offered,
        "accepted": summary["accepted"], "delivered": summary["delivered"],
        "measurement_delivered": summary.get("measurement_delivered", ""),
        "measurement_cycles": summary.get("measurement_cycles", stim),
        "fixed_window_epc": fixed, "retire_lanes": lanes,
        "event_per_cycle_per_lane": fixed / lanes,
        "p50_e2e": nearest_rank(latencies, 50),
        "p95_e2e": nearest_rank(latencies, 95),
        "p99_e2e": nearest_rank(latencies, 99),
        "max_request_wait": summary["max_request_wait"],
        "overrun": summary["source_overrun"], "source_boundary_slots": 16,
        "internal_event_slots": internal_slots, "retire_ceiling_epc": lanes,
        "errors": summary["errors"], "notes": notes,
    }


def load_common_dir(track: str, candidate: str, directory: Path,
                    manifest: dict[str, dict[str, object]], lanes: int,
                    internal_slots: int, nested: bool) -> list[dict[str, object]]:
    output = []
    for trace in manifest:
        if nested:
            summary_path = directory / trace / "n16-seed1/trace.csv"
            event_path = directory / trace / "n16-seed1/trace.events.csv"
        else:
            summary_path = directory / f"{trace}.csv"
            event_path = directory / f"{trace}.events.csv"
        if not summary_path.is_file() or not event_path.is_file():
            continue
        summary_rows = rows(summary_path)
        if len(summary_rows) != 1:
            raise RuntimeError(f"expected one summary row: {summary_path}")
        output.append(canonical_summary(
            track=track, candidate=candidate, trace=trace, manifest=manifest,
            lanes=lanes, internal_slots=internal_slots,
            summary=summary_rows[0], event_rows=rows(event_path),
            evidence="exact_common_tb_per_trace"))
    return output


def load_a4(root: Path, manifest: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    result_dir = root / "a4/docs/research/results"
    summaries = {row["name"]: row for row in rows(result_dir / "a4_verilator_46.csv")}
    raw_rows = rows(result_dir / "a4_verilator_summary_all.csv")
    if len(raw_rows) != len(manifest):
        raise RuntimeError("A4 combined summary does not cover the 46-run manifest")
    raw_by_trace = dict(zip(manifest, raw_rows, strict=True))
    event_metrics = {
        trace_for_event(event, manifest): event
        for event in rows(result_dir / "a4_verilator_event_runs.csv")
    }
    output = []
    for trace, summary in summaries.items():
        synthetic = {
            "generated": summary["generated"], "accepted": summary["accepted"],
            "delivered": summary["delivered"], "throughput": summary["measurement_event_per_cycle"],
            "measurement_cycles": raw_by_trace[trace]["measurement_cycles"],
            "measurement_delivered": raw_by_trace[trace]["measurement_delivered"],
            "max_request_wait": summary["max_request_wait"],
            "source_overrun": summary["source_overrun"], "errors": summary["errors"],
        }
        normalized = canonical_summary(
            track="A4", candidate="a4-quadtree", trace=trace, manifest=manifest,
            lanes=1, internal_slots=5, summary=synthetic,
            event_rows=None, evidence="committed_exact_common_tb_per_trace",
            notes="five registered radix-4 merge-node slots")
        normalized["p50_e2e"] = event_metrics[trace]["p50_e2e_latency_cycles"]
        normalized["p95_e2e"] = event_metrics[trace]["p95_e2e_latency_cycles"]
        normalized["p99_e2e"] = event_metrics[trace]["p99_e2e_latency_cycles"]
        if (int(raw_by_trace[trace]["generated"]) != int(summary["generated"])):
            raise RuntimeError(f"A4 summary order mismatch at {trace}")
        output.append(normalized)
    return output


def load_a6(root: Path, manifest: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    path = root / "a6/reports/a6-lossless-aer-codec/v2_rtl_trace_metrics.csv"
    output = []
    for source in rows(path):
        trace = source["name"]
        declared = manifest[trace]
        stim = int(declared["stim_cycles"])
        fixed = float(source["throughput"])
        output.append({
            "track": "A6", "candidate": "a6-v2-lossless-codec", "trace": trace,
            "workload": declared["workload"], "seed": source["seed"],
            "evidence": "committed_per_trace_counts_no_percentile_events",
            "declared_offered_epc": declared["load"],
            "realized_offered_epc": int(source["offered"]) / stim,
            "offered": source["offered"], "accepted": source["accepted"],
            "delivered": source["delivered"], "measurement_delivered": "",
            "measurement_cycles": stim, "fixed_window_epc": fixed,
            "retire_lanes": 1, "event_per_cycle_per_lane": fixed,
            "p50_e2e": "", "p95_e2e": "", "p99_e2e": "",
            "max_request_wait": source["max_request_wait"],
            "overrun": source["overrun"], "source_boundary_slots": 16,
            "internal_event_slots": 16, "retire_ceiling_epc": 1,
            "errors": source["errors"],
            "notes": "avg/max E2E retained; codec/link counters are not logical throughput",
        })
    return output


def load_a7(root: Path, manifest: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    result_dir = root / "a7/reports/a7-parallel-event-compactor"
    aggregate = rows(result_dir / "aggregate.csv")
    aggregate_index = {(r["candidate"], r["test"], r["load_pct"]): r for r in aggregate}
    output = []
    for event in rows(result_dir / "event-runs.csv"):
        candidate = event["candidate"]
        lanes = int(candidate.rsplit("k", 1)[1])
        trace = trace_for_event(event, manifest)
        declared = manifest[trace]
        stim = int(declared["stim_cycles"])
        group = aggregate_index[(candidate, event["test"], event["load_pct"])]
        offered = int(event["event_rows"])
        delivered = int(event["delivered_event_rows"])
        output.append({
            "track": "A7", "candidate": candidate, "trace": trace,
            "workload": declared["workload"], "seed": event["seed"],
            "evidence": "exact_per_trace_counts_tails_group_fixed_window",
            "declared_offered_epc": declared["load"],
            "realized_offered_epc": offered / stim, "offered": offered,
            "accepted": delivered, "delivered": delivered,
            "measurement_delivered": "", "measurement_cycles": "",
            "fixed_window_epc": group["avg_throughput"], "retire_lanes": lanes,
            "event_per_cycle_per_lane": float(group["avg_throughput"]) / lanes,
            "p50_e2e": event["p50_e2e_latency_cycles"],
            "p95_e2e": event["p95_e2e_latency_cycles"],
            "p99_e2e": event["p99_e2e_latency_cycles"],
            "max_request_wait": group["worst_request_wait"],
            "overrun": offered - delivered, "source_boundary_slots": 16,
            "internal_event_slots": lanes, "retire_ceiling_epc": lanes,
            "errors": group["errors"],
            "notes": "fixed-window and max-wait are exact report-group aggregates",
        })
    return output


def write_csv(path: Path, output_rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-parent", type=Path,
                        default=Path("/home/chickgoose/projects"))
    parser.add_argument("--manifest", type=Path,
                        default=Path("benchmarks/clean_slate_aer/manifest.neutrality-n16.json"))
    parser.add_argument("--a2-dir", type=Path, default=Path("/tmp/a2-neutrality-results"))
    parser.add_argument("--a8-dir", type=Path,
                        default=Path("/tmp/a8-age-calendar-wheel-regression"))
    parser.add_argument("--a9-dir", type=Path,
                        help="optional full 46-run A9 directory; report-only evidence is otherwise omitted")
    parser.add_argument("--a9-lanes", type=int, choices=(1, 4), default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = Path(__file__).resolve().parents[2] / manifest_path
    manifest = manifest_index(manifest_path)
    output: list[dict[str, object]] = []
    output += load_common_dir("A2", "a2-adaptive-dual-path", args.a2_dir,
                              manifest, 1, 8, nested=True)
    output += load_a4(args.workspace_parent, manifest)
    output += load_a6(args.workspace_parent, manifest)
    output += load_a7(args.workspace_parent, manifest)
    output += load_common_dir("A8", "a8-age-calendar-wheel-b4", args.a8_dir,
                              manifest, 1, 1, nested=False)
    if args.a9_dir:
        output += load_common_dir("A9", f"a9-distributed-l{args.a9_lanes}",
                                  args.a9_dir, manifest, args.a9_lanes, 48,
                                  nested=False)
    write_csv(args.output, output)
    counts: dict[str, int] = {}
    for row in output:
        if int(row["errors"]) != 0:
            raise RuntimeError(f"correctness error in normalized row: {row['candidate']}/{row['trace']}")
        if int(row["accepted"]) != int(row["delivered"]):
            raise RuntimeError(f"undrained row: {row['candidate']}/{row['trace']}")
        if int(row["offered"]) != int(row["accepted"]) + int(row["overrun"]):
            raise RuntimeError(f"source conservation mismatch: {row['candidate']}/{row['trace']}")
        if float(row["fixed_window_epc"]) > int(row["retire_lanes"]) + 1e-9:
            raise RuntimeError(f"retire ceiling exceeded: {row['candidate']}/{row['trace']}")
        counts[str(row["candidate"])] = counts.get(str(row["candidate"]), 0) + 1
    incomplete = {name: count for name, count in counts.items() if count != 46}
    if incomplete:
        raise RuntimeError(f"incomplete 46-trace configurations: {incomplete}")
    print("A5_CAPACITY_LATENCY_EXTRACTION_PASS " +
          " ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    if not args.a9_dir:
        print("A5_CAPACITY_LATENCY_EVIDENCE_GAP A9=no_machine_readable_46_directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
