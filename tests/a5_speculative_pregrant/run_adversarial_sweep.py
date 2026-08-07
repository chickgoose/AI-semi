#!/usr/bin/env python3
"""Compile/run candidate-only adversarial and predictor-size sweeps."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import re
import subprocess
import sys


METRIC_RE = re.compile(r"A5_ADVERSARIAL_METRICS (?P<body>.+)")


def parse_metrics(output: str) -> dict[str, str]:
    match = METRIC_RE.search(output)
    if match is None:
        raise RuntimeError("missing A5_ADVERSARIAL_METRICS")
    parsed: dict[str, str] = {}
    for item in match.group("body").split():
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a5-adversarial"))
    parser.add_argument("--iverilog", default=os.environ.get("IVERILOG", "iverilog"))
    parser.add_argument("--vvp", default=os.environ.get("VVP", "vvp"))
    parser.add_argument("--iverilog-base", default=os.environ.get("IVERILOG_BASE", ""))
    parser.add_argument("--vvp-module-dir", default=os.environ.get("VVP_MODULE_DIR", ""))
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[2]
    rtl = project / "rtl/candidates/a5_speculative_pregrant"
    tb = rtl / "tests/a5_adversarial_tb.sv"
    args.output.mkdir(parents=True, exist_ok=True)

    configs = [
        ("fallback", 0, 4, 16, 2),
        ("h1_t16_c2", 1, 1, 16, 2),
        ("h2_t16_c2", 1, 2, 16, 2),
        ("h4_t2_c2", 1, 4, 2, 2),
        ("h4_t4_c2", 1, 4, 4, 2),
        ("h4_t8_c2", 1, 4, 8, 2),
        ("h4_t16_c1", 1, 4, 16, 1),
        ("h4_t16_c2", 1, 4, 16, 2),
        ("h4_t16_c3", 1, 4, 16, 3),
    ]
    patterns = [
        ("alternating", 128, 2, 1, 0),
        ("anticorrelated", 128, 2, 1, 0),
        ("alias_collision", 128, 2, 1, 0),
        ("cold_start", 16, 2, 1, 0),
        ("moving_d1", 128, 2, 1, 0),
        ("moving_d2", 128, 2, 2, 0),
        ("moving_d4", 128, 2, 4, 0),
        ("moving_d8", 128, 2, 8, 0),
        ("moving_d4_affine", 128, 2, 4, 1),
    ]

    rows: list[dict[str, object]] = []
    executables: dict[str, Path] = {}
    for config, enabled, history_bits, table_entries, conf_bits in configs:
        executable = args.output / f"{config}.vvp"
        command = [args.iverilog]
        if args.iverilog_base:
            command += ["-B", args.iverilog_base]
        command += [
            "-g2012", "-Wall", "-s", "a5_adversarial_tb",
            "-P", f"a5_adversarial_tb.ENABLE_PREDICTOR={enabled}",
            "-P", f"a5_adversarial_tb.PRED_HISTORY_BITS={history_bits}",
            "-P", f"a5_adversarial_tb.PRED_TABLE_ENTRIES={table_entries}",
            "-P", f"a5_adversarial_tb.PRED_CONF_WIDTH={conf_bits}",
            str(rtl / "a5_transition_predictor.sv"),
            str(rtl / "a5_last_successor_predictor.sv"),
            str(rtl / "a5_speculative_pregrant_core.sv"),
            str(tb), "-o", str(executable),
        ]
        result = subprocess.run(command, cwd=project, text=True, capture_output=True)
        if result.returncode:
            sys.stdout.write(result.stdout + result.stderr)
            return result.returncode
        executables[config] = executable

    for config, enabled, history_bits, table_entries, conf_bits in configs:
        predictor_bits = (
            table_entries * (1 + history_bits + 4 + conf_bits) + 1 + 4
            if enabled else 0
        )
        total_state_bits = (27 + predictor_bits) if enabled else 25
        for case_name, events, gap, dwell, affine in patterns:
            pattern = "moving_hotspot" if case_name.startswith("moving_") else case_name
            command = [args.vvp]
            if args.vvp_module_dir:
                command += ["-M", args.vvp_module_dir]
            command += [
                str(executables[config]), f"+PATTERN={pattern}",
                f"+EVENTS={events}", f"+GAP={gap}", f"+DWELL={dwell}",
                f"+AFFINE={affine}",
            ]
            result = subprocess.run(command, cwd=project, text=True, capture_output=True)
            if result.returncode:
                sys.stdout.write(result.stdout + result.stderr)
                return result.returncode
            metric = parse_metrics(result.stdout)
            attempts = int(metric["attempts"])
            hits = int(metric["hits"])
            opportunities = (
                attempts + int(metric["confidence_fallbacks"])
                + int(metric["fairness_fallbacks"])
            )
            rows.append(
                {
                    "config": config,
                    "enabled": enabled,
                    "history_bits": history_bits,
                    "table_entries": table_entries,
                    "confidence_bits": conf_bits,
                    "predictor_state_bits": predictor_bits,
                    "total_algorithm_state_bits": total_state_bits,
                    "case": case_name,
                    "dwell": dwell,
                    "affine": affine,
                    "events": events,
                    "errors": int(metric["errors"]),
                    "overrun": int(metric["overrun"]),
                    "accepted": int(metric["accepted"]),
                    "delivered": int(metric["delivered"]),
                    "attempts": attempts,
                    "hits": hits,
                    "misses": int(metric["misses"]),
                    "accuracy": hits / attempts if attempts else "",
                    "coverage": attempts / opportunities if opportunities else "",
                    "same_cycle_recovery": int(metric["same_cycle_recovery"]),
                    "recovery_latency_cycles": int(metric["recovery_latency_cycles"]),
                    "fixed_window_throughput": float(metric["throughput"]),
                    "avg_e2e_latency": float(metric["avg_e2e"]),
                    "avg_hit_latency": float(metric["avg_hit_latency"]),
                    "avg_fallback_latency": float(metric["avg_fallback_latency"]),
                    "toggle_proxy": int(metric["toggles"]),
                }
            )
            print(f"PASS {config} {case_name}")

    baseline = {row["case"]: row for row in rows if row["config"] == "fallback"}
    for row in rows:
        reference = baseline[row["case"]]
        base_latency = float(reference["avg_e2e_latency"])
        row["latency_gain_cycles_vs_fallback"] = (
            base_latency - float(row["avg_e2e_latency"])
        )
        row["latency_gain_pct_vs_fallback"] = (
            100.0 * (base_latency - float(row["avg_e2e_latency"])) / base_latency
            if base_latency else 0.0
        )
        row["throughput_delta_vs_fallback"] = (
            float(row["fixed_window_throughput"])
            - float(reference["fixed_window_throughput"])
        )
        row["toggle_delta_vs_fallback"] = (
            int(row["toggle_proxy"]) - int(reference["toggle_proxy"])
        )

    output_csv = args.output / "a5-adversarial-sweep.csv"
    with output_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"A5_ADVERSARIAL_SWEEP_PASS runs={len(rows)} output={output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
