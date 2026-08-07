#!/usr/bin/env python3
"""Measure the 46-trace prediction utility ceiling and small-state Pareto."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    name: str
    enabled: int
    style: int
    history: int = 4
    entries: int = 16
    confidence: int = 2
    gated: int = 1

    @property
    def state_bits(self) -> int:
        if not self.enabled:
            return 0
        if self.style == 2:
            return self.entries * (1 + self.history + 4) + 5
        if self.style == 3:
            return 0  # ideal information is deliberately not implemented
        return self.entries * (1 + self.history + 4 + self.confidence) + 5


def read_one(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 1:
        raise RuntimeError(f"expected one row in {path}, got {len(rows)}")
    return rows[0]


def run_config(project: Path, root: Path, traces: Path, verilator: str,
               config: Config) -> None:
    output = root / config.name
    command = [
        sys.executable,
        str(project / "tests/a5_speculative_pregrant/run_frozen_regression.py"),
        "--output", str(output), "--trace-dir", str(traces),
        "--verilator", verilator,
        "--predictor-enabled", str(config.enabled),
        "--predictor-style", str(config.style),
        "--history-bits", str(config.history),
        "--table-entries", str(config.entries),
        "--confidence-bits", str(config.confidence),
        "--confidence-gated", str(config.gated),
    ]
    result = subprocess.run(command, cwd=project, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (root / f"{config.name}.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        sys.stdout.write(result.stdout)
        raise SystemExit(result.returncode)
    print(f"PASS {config.name}")


def load_config(root: Path, config: Config) -> list[dict[str, object]]:
    pred_path = root / config.name / "a5-predictor-metrics.csv"
    with pred_path.open(newline="", encoding="utf-8") as source:
        pred_rows = list(csv.DictReader(source))
    rows: list[dict[str, object]] = []
    for pred in pred_rows:
        summary = read_one(root / config.name / f"{pred['run']}.csv")
        accepted = int(summary["accepted"])
        delivered = int(summary["delivered"])
        errors = int(summary["errors"])
        if errors or accepted != delivered:
            raise RuntimeError(f"correctness failure {config.name}/{pred['run']}")
        attempts = int(pred["attempts"])
        hits = int(pred["hits"])
        bypass = int(pred["bypass_hits"])
        rows.append({
            "config": config.name, "run": pred["run"],
            "workload": pred["workload"], "report_group": pred["report_group"],
            "trace_sha256": pred["trace_sha256"], "state_bits": config.state_bits,
            "accepted": accepted, "delivered": delivered, "errors": errors,
            "attempts": attempts, "hits": hits, "misses": int(pred["misses"]),
            "bypass_hits": bypass,
            "accuracy": hits / attempts if attempts else "",
            "attempt_coverage": attempts / accepted if accepted else "",
            "bypass_coverage": bypass / accepted if accepted else "",
            "update_opportunities": max(0, accepted - 1),
            "updates_per_bypass": (max(0, accepted - 1) / bypass) if bypass else "inf",
            "avg_e2e_latency": float(summary["avg_e2e_latency"]),
            "max_e2e_latency": int(summary["max_e2e_latency"]),
            "fixed_window_throughput": float(summary["throughput"]),
            "overrun": int(summary["source_overrun"]),
            "fairness": float(summary["fairness"]),
        })
    return rows


def aggregate(config: Config, rows: list[dict[str, object]],
              fallback_by_run: dict[str, dict[str, object]]) -> dict[str, object]:
    events = sum(int(row["delivered"]) for row in rows)
    latency_sum = sum(float(row["avg_e2e_latency"]) * int(row["delivered"])
                      for row in rows)
    attempts = sum(int(row["attempts"]) for row in rows)
    hits = sum(int(row["hits"]) for row in rows)
    bypass = sum(int(row["bypass_hits"]) for row in rows)
    updates = sum(int(row["update_opportunities"]) for row in rows)
    fallback_events = sum(int(row["delivered"]) for row in fallback_by_run.values())
    common_weight_gain = sum(
        (float(fallback_by_run[str(row["run"])]["avg_e2e_latency"]) -
         float(row["avg_e2e_latency"])) *
        int(fallback_by_run[str(row["run"])]["delivered"])
        for row in rows
    )
    return {
        "config": config.name, "enabled": config.enabled, "style": config.style,
        "history_bits": config.history,
        "table_entries": config.entries, "confidence_bits": config.confidence,
        "confidence_gated": config.gated, "predictor_state_bits": config.state_bits,
        "runs": len(rows), "events": events, "attempts": attempts, "hits": hits,
        "bypass_hits": bypass, "accuracy": hits / attempts if attempts else "",
        "bypass_coverage": bypass / events if events else "",
        "weighted_avg_e2e": latency_sum / events if events else 0.0,
        "latency_gain_cycles": common_weight_gain / fallback_events if fallback_events else 0.0,
        "update_opportunities": updates,
        "updates_per_bypass": updates / bypass if bypass else "inf",
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a5-prediction-ceiling"))
    parser.add_argument("--trace-dir", type=Path, default=Path("/tmp/a5-frozen-traces"))
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True, exist_ok=True)

    primary = [
        Config("fallback", 0, 1), Config("oracle", 1, 3),
        Config("last_successor", 1, 2), Config("markov_h4_t16_c2_g1", 1, 1),
    ]
    sweep = [
        Config(f"markov_h4_t{t}_c2_g1", 1, 1, 4, t, 2, 1)
        for t in (1, 2, 4, 8)
    ] + [
        Config(f"markov_h4_t{t}_c{c}_g{g}", 1, 1, 4, t, c, g)
        for t in (4, 16) for c, g in ((1, 1), (3, 1), (1, 0), (2, 0))
    ] + [
        Config(f"markov_h{h}_t{1 << h}_c2_g1", 1, 1, h, 1 << h, 2, 1)
        for h in (1, 2, 3)
    ]
    configs = primary + [item for item in sweep if item.name not in {p.name for p in primary}]
    for config in configs:
        if not (args.reuse and (args.output / config.name / "a5-summary-all.csv").is_file()):
            run_config(project, args.output, args.trace_dir, args.verilator, config)

    loaded = {config.name: load_config(args.output, config) for config in configs}
    fallback_by_run = {row["run"]: row for row in loaded["fallback"]}
    oracle_by_run = {row["run"]: row for row in loaded["oracle"]}
    per_trace: list[dict[str, object]] = []
    for config in primary:
        for row in loaded[config.name]:
            baseline = fallback_by_run[row["run"]]
            oracle = oracle_by_run[row["run"]]
            row = dict(row)
            row["latency_gain_vs_fallback"] = (
                float(baseline["avg_e2e_latency"]) - float(row["avg_e2e_latency"]))
            row["throughput_delta_vs_fallback"] = (
                float(row["fixed_window_throughput"]) -
                float(baseline["fixed_window_throughput"]))
            oracle_gain = (float(baseline["avg_e2e_latency"]) -
                           float(oracle["avg_e2e_latency"]))
            row["oracle_latency_gain"] = oracle_gain
            row["oracle_gain_captured"] = (
                float(row["latency_gain_vs_fallback"]) / oracle_gain
                if oracle_gain > 0 else "")
            per_trace.append(row)
    write_csv(args.output / "a5-ceiling-per-trace.csv", per_trace)

    aggregate_rows = [aggregate(config, loaded[config.name], fallback_by_run)
                      for config in configs]
    for row in aggregate_rows:
        row["pareto"] = (row["enabled"] == 1 and row["style"] == 1) and not any(
            other["predictor_state_bits"] <= row["predictor_state_bits"] and
            other["latency_gain_cycles"] >= row["latency_gain_cycles"] and
            (other["predictor_state_bits"] < row["predictor_state_bits"] or
             other["latency_gain_cycles"] > row["latency_gain_cycles"])
            for other in aggregate_rows
            if other["enabled"] == 1 and other["style"] == 1)
    write_csv(args.output / "a5-ceiling-pareto.csv", aggregate_rows)
    print(f"A5_PREDICTION_CEILING_PASS configs={len(configs)} runs={len(configs)*46}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
