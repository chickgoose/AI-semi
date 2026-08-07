#!/usr/bin/env python3
"""Run and audit all frozen traces against the A6 v2 candidate binding."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from a6_v2_codec import Block, decode_block


SUMMARY_PATTERN = re.compile(
    r"bits=(\d+) data_cycles=(\d+) delimiter_cycles=(\d+) blocks=(\d+) "
    r"data_toggles=(\d+) control_toggles=(\d+)"
)


def read_single_csv(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"expected one metrics row in {path}")
    return rows[0]


def raw_toggle_proxy(addresses: list[int]) -> int:
    previous_data = 0
    previous_count = 0
    previous_ready = 0
    toggles = 0
    for address in addresses:
        bits = format(address, "04b")
        for pair in (bits[:2], bits[2:]):
            data = int(pair, 2)
            count = 2
            ready = 1
            toggles += (previous_data ^ data).bit_count()
            toggles += (previous_count ^ count).bit_count()
            toggles += previous_ready ^ ready
            previous_data, previous_count, previous_ready = data, count, ready
    toggles += previous_data.bit_count() + previous_count.bit_count()
    return toggles


def audit_link(path: Path, accepted: list[int]) -> dict[str, int | float]:
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines or not lines[-1].startswith("# "):
        raise ValueError(f"missing link summary in {path}")
    match = SUMMARY_PATTERN.search(lines[-1])
    if not match:
        raise ValueError(f"malformed link summary in {path}")
    bits, data_cycles, delimiter_cycles, block_total, data_toggles, control_toggles = (
        int(value) for value in match.groups()
    )
    decoded: list[int] = []
    previous = None
    raw_blocks = token_blocks = dictionary_blocks = 0
    counted_bits = 0
    for line in lines[:-1]:
        bit_string, count_text = line.split(" events=")
        event_count = int(count_text)
        if len(bit_string) > 4 * event_count:
            raise AssertionError(f"expanding block in {path}: {len(bit_string)}>{4*event_count}")
        if len(bit_string) % 4 == 0:
            mode = "raw"
            raw_blocks += 1
        elif bit_string[0] == "0":
            mode = "token"
            token_blocks += 1
        else:
            mode = "dictionary"
            dictionary_blocks += 1
        values = decode_block(Block(bit_string, mode, event_count), previous)
        decoded.extend(values)
        previous = values[-1]
        counted_bits += len(bit_string)
    if decoded != accepted:
        raise AssertionError(f"link round-trip differs from accepted order in {path}")
    if counted_bits != bits or len(lines) - 1 != block_total:
        raise AssertionError(f"link observer summary mismatch in {path}")
    if delimiter_cycles != block_total:
        raise AssertionError(f"each block must have exactly one delimiter in {path}")
    events = len(accepted)
    link_cycles = data_cycles + delimiter_cycles
    raw_toggles = raw_toggle_proxy(accepted)
    return {
        "link_bits": bits,
        "data_cycles": data_cycles,
        "delimiter_cycles": delimiter_cycles,
        "blocks": block_total,
        "raw_blocks": raw_blocks,
        "token_blocks": token_blocks,
        "dictionary_blocks": dictionary_blocks,
        "raw_bypass_ratio": raw_blocks / block_total if block_total else 0.0,
        "bits_per_event": bits / events if events else 0.0,
        "compression_ratio": 4 * events / bits if bits else 0.0,
        "events_per_link_cycle": events / link_cycles if link_cycles else 0.0,
        "events_per_pin_cycle": events / (5 * link_cycles) if link_cycles else 0.0,
        "toggle_per_event": (data_toggles + control_toggles) / events if events else 0.0,
        "raw_toggle_per_event": raw_toggles / events if events else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True, type=Path)
    parser.add_argument("--sim-binary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    prepare = project_root / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []

    manifests = sorted(args.trace_dir.glob("*.manifest.json"))
    if len(manifests) != 46:
        raise ValueError("expected exactly 46 frozen trace manifests")
    for manifest_path in manifests:
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        run = metadata["run"]
        name = run["name"]
        trace_path = args.trace_dir / metadata["trace_file"]
        svtrace = args.output_dir / f"{name}.svtrace"
        metrics = args.output_dir / f"{name}.csv"
        events_path = args.output_dir / f"{name}.events.csv"
        link_path = args.output_dir / f"{name}.link.txt"
        log_path = args.output_dir / f"{name}.log"
        subprocess.run(
            ["python3", str(prepare), "--trace", str(trace_path),
             "--run-manifest", str(manifest_path), "--output", str(svtrace),
             "--addr-width", "6"], check=True, capture_output=True, text=True)
        command = [
            str(args.sim_binary), "+CLEAN_TEST=trace",
            "+CANDIDATE=a6-v2-lossless-codec", f"+METRICS={metrics}",
            f"+EVENT_METRICS={events_path}", f"+A6_V2_LINK_METRICS={link_path}",
            f"+TRACE_FILE={svtrace}", f"+TRACE_NAME={metadata['report_group']}",
            f"+SEED={run['seed']}",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        metric = read_single_csv(metrics)
        with events_path.open(newline="", encoding="utf-8") as stream:
            event_rows = list(csv.DictReader(stream))
        accepted_rows = [row for row in event_rows if row["event_state"] == "delivered"]
        accepted_rows.sort(key=lambda row: int(row["accept_cycle"]))
        accepted = [int(row["logical_source"]) for row in accepted_rows]
        if len(accepted) != int(metric["accepted"]):
            raise AssertionError(f"accepted event metrics mismatch for {name}")
        link = audit_link(link_path, accepted)
        row: dict[str, object] = {
            "name": name,
            "workload": run["workload"],
            "seed": run["seed"],
            "offered": int(metric["generated"]),
            "overrun": int(metric["source_overrun"]),
            "accepted": int(metric["accepted"]),
            "delivered": int(metric["delivered"]),
            "errors": int(metric["errors"]),
            "throughput": float(metric["throughput"]),
            "avg_e2e_latency": float(metric["avg_e2e_latency"]),
            "max_e2e_latency": int(metric["max_e2e_latency"]),
            "max_request_wait": int(metric["max_request_wait"]),
        }
        row.update(link)
        rows.append(row)
        print(f"A6_V2_TRACE_PASS {name} accepted={row['accepted']} bpe={row['bits_per_event']:.3f}")

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    family_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        family_rows[str(row["workload"])].append(row)
    families = {}
    for family, members in sorted(family_rows.items()):
        accepted = sum(int(row["accepted"]) for row in members)
        bits = sum(int(row["link_bits"]) for row in members)
        blocks = sum(int(row["blocks"]) for row in members)
        raw_blocks = sum(int(row["raw_blocks"]) for row in members)
        families[family] = {
            "runs": len(members),
            "offered": sum(int(row["offered"]) for row in members),
            "overrun": sum(int(row["overrun"]) for row in members),
            "accepted": accepted,
            "bits_per_event": bits / accepted if accepted else 0.0,
            "compression_ratio": 4 * accepted / bits if bits else 0.0,
            "raw_bypass_ratio": raw_blocks / blocks if blocks else 0.0,
            "events_per_pin_cycle": fmean(float(row["events_per_pin_cycle"]) for row in members),
            "toggle_per_event": fmean(float(row["toggle_per_event"]) for row in members),
            "raw_toggle_per_event": fmean(float(row["raw_toggle_per_event"]) for row in members),
            "throughput": fmean(float(row["throughput"]) for row in members),
            "avg_e2e_latency": fmean(float(row["avg_e2e_latency"]) for row in members),
            "max_e2e_latency": max(int(row["max_e2e_latency"]) for row in members),
        }
    total_accepted = sum(int(row["accepted"]) for row in rows)
    total_bits = sum(int(row["link_bits"]) for row in rows)
    total_blocks = sum(int(row["blocks"]) for row in rows)
    summary = {
        "runs": len(rows),
        "roundtrip_failures": 0,
        "expanding_blocks": 0,
        "total_offered": sum(int(row["offered"]) for row in rows),
        "total_overrun": sum(int(row["overrun"]) for row in rows),
        "total_accepted": total_accepted,
        "weighted_bits_per_event": total_bits / total_accepted,
        "weighted_compression_ratio": 4 * total_accepted / total_bits,
        "raw_bypass_ratio": sum(int(row["raw_blocks"]) for row in rows) / total_blocks,
        "families": families,
    }
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print("A6_V2_RTL_EVAL_PASS runs=46 roundtrip_failures=0 expanding_blocks=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
