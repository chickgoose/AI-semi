#!/usr/bin/env python3
"""Join frozen traces, common-TB results, and observed A6 physical-link bits."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from a6_codec import decode_with_tokens, encode, link_metrics, raw_bits


def percentile(values: list[int], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return float(ordered[index])


def load_link(path: Path) -> tuple[str, dict[str, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 2 or not lines[1].startswith("# "):
        raise ValueError(f"malformed observed link file: {path}")
    fields = {}
    for item in lines[1][2:].split():
        key, value = item.split("=", 1)
        fields[key] = int(value)
    if fields["bits"] != len(lines[0]):
        raise ValueError(f"observed bit count mismatch: {path}")
    return lines[0], fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True, type=Path)
    parser.add_argument("--rtl-results", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    args = parser.parse_args()

    rows = []
    for manifest_path in sorted(args.trace_dir.glob("*.manifest.json")):
        name = manifest_path.name.removesuffix(".manifest.json")
        trace_path = args.trace_dir / f"{name}.events.jsonl"
        summary_path = args.rtl_results / f"{name}.csv"
        events_path = args.rtl_results / f"{name}.events.csv"
        link_path = args.rtl_results / f"{name}.link"
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        trace_bytes = trace_path.read_bytes()
        if hashlib.sha256(trace_bytes).hexdigest() != metadata["trace_sha256"]:
            raise ValueError(f"frozen trace SHA mismatch: {name}")
        offered = [json.loads(line) for line in trace_bytes.splitlines()]
        offered_addresses = [event["logical_source"] for event in offered]

        with summary_path.open(newline="", encoding="utf-8") as stream:
            rtl = next(csv.DictReader(stream))
        with events_path.open(newline="", encoding="utf-8") as stream:
            detail = list(csv.DictReader(stream))
        accepted_rows = [row for row in detail if row["accept_cycle"]]
        accepted_rows.sort(key=lambda row: int(row["accept_cycle"]))
        accept_cycles = [int(row["accept_cycle"]) for row in accepted_rows]
        if len(accept_cycles) != len(set(accept_cycles)):
            raise ValueError(f"more than one encoder acceptance per cycle: {name}")
        accepted_addresses = [int(row["logical_source"]) for row in accepted_rows]

        observed_bits, observed = load_link(link_path)
        decoded, tokens = decode_with_tokens(observed_bits)
        if decoded != accepted_addresses:
            raise ValueError(f"physical link does not reproduce acceptance order: {name}")
        if int(rtl["accepted"]) != len(decoded) or rtl["accepted"] != rtl["delivered"]:
            raise ValueError(f"accepted/delivered conservation failure: {name}")
        if int(rtl["errors"]) != 0:
            raise ValueError(f"common scoreboard error: {name}")

        raw_stream = raw_bits(accepted_addresses)
        raw_link = link_metrics(raw_stream, len(accepted_addresses))
        offered_encoded = encode(offered_addresses)
        offered_decoded, _ = decode_with_tokens(offered_encoded.bits)
        if offered_decoded != offered_addresses:
            raise ValueError(f"offered round-trip failure: {name}")
        delivered_rows = [row for row in detail if row["event_state"] == "delivered"]
        latencies = [int(row["delivery_cycle"]) - int(row["occurrence_cycle"])
                     for row in delivered_rows]
        events = len(decoded)
        pin_count = 5
        row = {
            "name": name,
            "workload": metadata["run"]["workload"],
            "report_group": metadata["report_group"],
            "trace_sha256": metadata["trace_sha256"],
            "generated": int(rtl["generated"]),
            "source_overrun": int(rtl["source_overrun"]),
            "accepted": int(rtl["accepted"]),
            "delivered": int(rtl["delivered"]),
            "errors": int(rtl["errors"]),
            "compressed_bits": observed["bits"],
            "raw_bits": 4 * events,
            "compression_ratio": (4 * events / observed["bits"]) if observed["bits"] else 0.0,
            "escape_ratio": tokens["raw"] / events if events else 0.0,
            "same_tokens": tokens["same"],
            "run_tokens": tokens["run"],
            "delta_tokens": tokens["delta_plus"] + tokens["delta_minus"],
            "raw_tokens": tokens["raw"],
            "link_bits_per_event": observed["bits"] / events if events else 0.0,
            "link_cycles": observed["cycles"],
            "events_per_link_cycle": events / observed["cycles"] if observed["cycles"] else 0.0,
            "events_per_pin_cycle": events / (pin_count * observed["cycles"]) if observed["cycles"] else 0.0,
            "data_toggles_per_event": observed["data_toggles"] / events if events else 0.0,
            "toggle_proxy_per_event": (observed["data_toggles"] + observed["control_toggles"]) / events if events else 0.0,
            "raw_events_per_pin_cycle": raw_link["events_per_pin_cycle"],
            "raw_toggle_proxy_per_event": raw_link["charged_toggles_per_event"],
            "offered_bits_per_event": len(offered_encoded.bits) / len(offered) if offered else 0.0,
            "offered_compression_ratio": (4 * len(offered) / len(offered_encoded.bits)) if offered_encoded.bits else 0.0,
            "offered_escape_ratio": offered_encoded.tokens["raw"] / len(offered) if offered else 0.0,
            "overrun_ratio": int(rtl["source_overrun"]) / int(rtl["generated"]) if int(rtl["generated"]) else 0.0,
            "throughput": float(rtl["throughput"]),
            "avg_e2e_latency": float(rtl["avg_e2e_latency"]),
            "p95_e2e_latency": percentile(latencies, 0.95),
            "p99_e2e_latency": percentile(latencies, 0.99),
            "max_e2e_latency": int(rtl["max_e2e_latency"]),
            "avg_internal_latency": float(rtl["avg_internal_latency"]),
            "total_cycles": int(rtl["total_cycles"]),
        }
        rows.append(row)

    if len(rows) != 46:
        raise ValueError(f"expected 46 frozen runs, found {len(rows)}")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    families = defaultdict(list)
    for row in rows:
        families[row["workload"]].append(row)
    family_summary = {}
    for family, members in sorted(families.items()):
        family_summary[family] = {
            "runs": len(members),
            "generated": sum(row["generated"] for row in members),
            "accepted": sum(row["accepted"] for row in members),
            "overrun_ratio": sum(row["source_overrun"] for row in members) /
                             sum(row["generated"] for row in members),
            "mean_compression_ratio": fmean(row["compression_ratio"] for row in members),
            "mean_escape_ratio": fmean(row["escape_ratio"] for row in members),
            "mean_bits_per_event": fmean(row["link_bits_per_event"] for row in members),
            "mean_events_per_pin_cycle": fmean(row["events_per_pin_cycle"] for row in members),
            "mean_toggle_proxy_per_event": fmean(row["toggle_proxy_per_event"] for row in members),
            "mean_raw_events_per_pin_cycle": fmean(row["raw_events_per_pin_cycle"] for row in members),
            "mean_raw_toggle_proxy_per_event": fmean(row["raw_toggle_proxy_per_event"] for row in members),
            "mean_offered_bits_per_event": fmean(row["offered_bits_per_event"] for row in members),
            "mean_offered_compression_ratio": fmean(row["offered_compression_ratio"] for row in members),
            "mean_offered_escape_ratio": fmean(row["offered_escape_ratio"] for row in members),
            "mean_throughput": fmean(row["throughput"] for row in members),
            "worst_p99_e2e_latency": max(row["p99_e2e_latency"] for row in members),
        }
    total_accepted = sum(row["accepted"] for row in rows)
    total_bits = sum(row["compressed_bits"] for row in rows)
    total_offered_bits = sum(round(row["offered_bits_per_event"] * row["generated"])
                             for row in rows)
    summary = {
        "candidate": "a6-lossless-codec",
        "runs": len(rows),
        "all_correct": all(row["errors"] == 0 and row["accepted"] == row["delivered"] for row in rows),
        "generated": sum(row["generated"] for row in rows),
        "accepted": total_accepted,
        "delivered": sum(row["delivered"] for row in rows),
        "source_overrun": sum(row["source_overrun"] for row in rows),
        "weighted_bits_per_event": total_bits / total_accepted,
        "weighted_compression_ratio": 4 * total_accepted / total_bits,
        "offered_weighted_bits_per_event": total_offered_bits /
                                           sum(row["generated"] for row in rows),
        "offered_weighted_compression_ratio": 4 * sum(row["generated"] for row in rows) /
                                               total_offered_bits,
        "offered_geomean_bits_per_event": math.exp(
            fmean(math.log(row["offered_bits_per_event"]) for row in rows)),
        "families": family_summary,
    }
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
    print(f"A6_EVALUATION_PASS runs={len(rows)} accepted={total_accepted} bits={total_bits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
