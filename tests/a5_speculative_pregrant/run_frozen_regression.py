#!/usr/bin/env python3
"""Run A5 against the frozen 46-trace suite without modifying common assets."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


PREDICTOR_RE = re.compile(
    r"A5_PREDICTOR_METRICS attempts=(\d+) hits=(\d+) misses=(\d+) "
    r"confidence_fallbacks=(\d+) fairness_fallbacks=(\d+) "
    r"bypass_hits=(\d+)"
)


def run(command: list[str], *, cwd: Path, log: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=os.environ.copy(),
    )
    if log is not None:
        log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        sys.stdout.write(result.stdout)
        raise SystemExit(
            f"command failed ({result.returncode}): {' '.join(command)}"
        )
    return result.stdout


def append_csv(inputs: list[Path], output: Path) -> None:
    header: list[str] | None = None
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination)
        for path in inputs:
            with path.open(newline="", encoding="utf-8") as source:
                reader = csv.reader(source)
                current_header = next(reader)
                if header is None:
                    header = current_header
                    writer.writerow(header)
                elif current_header != header:
                    raise SystemExit(f"CSV header mismatch: {path}")
                writer.writerows(reader)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a5-frozen-regression"))
    parser.add_argument("--trace-dir", type=Path, default=Path("/tmp/a5-frozen-traces"))
    parser.add_argument("--verilator", default=os.environ.get("VERILATOR", "verilator"))
    parser.add_argument("--predictor-enabled", choices=(0, 1), type=int, default=1)
    parser.add_argument("--predictor-style", choices=(1, 2, 3), type=int, default=1)
    parser.add_argument("--history-bits", choices=(1, 2, 3, 4), type=int, default=4)
    parser.add_argument("--table-entries", choices=(1, 2, 4, 8, 16), type=int, default=16)
    parser.add_argument("--confidence-bits", choices=(1, 2, 3), type=int, default=2)
    parser.add_argument("--confidence-gated", choices=(0, 1), type=int, default=1)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[2]
    manifest = project / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
    generator = project / "benchmarks/clean_slate_aer/generate_trace.py"
    preparer = project / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
    aggregator = project / "benchmarks/clean_slate_aer/aggregate.py"
    binding = project / "tests/a5_speculative_pregrant/aer_a5_speculative_pregrant_binding.sv"
    args.output.mkdir(parents=True, exist_ok=True)
    args.trace_dir.mkdir(parents=True, exist_ok=True)

    run(
        [sys.executable, str(generator), "--manifest", str(manifest),
         "--output-dir", str(args.trace_dir)],
        cwd=project,
        log=args.output / "trace-generation.log",
    )

    executable = args.output / "verilated/a5_clean"
    if not args.skip_build:
        shutil.rmtree(args.output / "verilated", ignore_errors=True)
        run(
            [
                args.verilator,
                "--binary", "--timing", "--assert", "-Wall", "-Wno-fatal",
                "-Wno-TIMESCALEMOD", "-Wno-BLKSEQ", "-Wno-UNUSEDSIGNAL",
                "-Wno-PINCONNECTEMPTY", "--top-module", "aer_clean_tb",
                "-DAER_CLEAN_GANGHEE_NATIVE", "-GNUM_SOURCES=16",
                f"-DA5_BIND_ENABLE_PREDICTOR={args.predictor_enabled}",
                f"-DA5_BIND_PREDICTOR_STYLE={args.predictor_style}",
                f"-DA5_BIND_HISTORY_BITS={args.history_bits}",
                f"-DA5_BIND_TABLE_ENTRIES={args.table_entries}",
                f"-DA5_BIND_CONF_WIDTH={args.confidence_bits}",
                f"-DA5_BIND_CONFIDENCE_GATE={args.confidence_gated}",
                "-GADDR_WIDTH=16", "-GRETIRE_LANES=1",
                "-f", str(project / "tb/clean/files.f"),
                "-f", str(project / "rtl/candidates/a5_speculative_pregrant/a5_speculative_pregrant.f"),
                str(binding), "--Mdir", str(args.output / "verilated"),
                "-o", "a5_clean",
            ],
            cwd=project,
            log=args.output / "build.log",
        )
    if not executable.is_file():
        raise SystemExit(f"missing simulator executable: {executable}")

    suite = json.loads(manifest.read_text(encoding="utf-8"))
    predictor_rows: list[dict[str, object]] = []
    summary_paths: list[Path] = []
    event_paths: list[Path] = []
    for index, declared in enumerate(suite["runs"], start=1):
        name = declared["name"]
        trace = args.trace_dir / f"{name}.events.jsonl"
        run_manifest = args.trace_dir / f"{name}.manifest.json"
        metadata = json.loads(run_manifest.read_text(encoding="utf-8"))
        prepared = args.output / f"{name}.svtrace"
        run(
            [sys.executable, str(preparer), "--trace", str(trace),
             "--run-manifest", str(run_manifest), "--output", str(prepared),
             "--addr-width", "16"],
            cwd=project,
        )
        summary = args.output / f"{name}.csv"
        events = args.output / f"{name}.events.csv"
        log = args.output / f"{name}.log"
        output = run(
            [
                str(executable), f"+CLEAN_TEST={metadata['report_group']}",
                "+CANDIDATE=" + (
                    "a5_speculative_pregrant" if args.predictor_enabled
                    else "a5_deterministic_fallback"
                ),
                f"+METRICS={summary}", f"+EVENT_METRICS={events}",
                f"+TRACE_FILE={prepared}", f"+TRACE_NAME={metadata['report_group']}",
            ],
            cwd=project,
            log=log,
        )
        if f"AER_CLEAN_TEST_PASS {metadata['report_group']}" not in output:
            raise SystemExit(f"missing PASS marker: {name}")
        match = PREDICTOR_RE.search(output)
        if match is None:
            raise SystemExit(f"missing predictor metrics: {name}")
        attempts, hits, misses, confidence, fairness, bypass_hits = map(int, match.groups())
        opportunities = attempts + confidence + fairness
        predictor_rows.append(
            {
                "run": name,
                "workload": declared["workload"],
                "report_group": metadata["report_group"],
                "seed": declared["seed"],
                "trace_sha256": metadata["trace_sha256"],
                "attempts": attempts,
                "hits": hits,
                "misses": misses,
                "confidence_fallbacks": confidence,
                "fairness_fallbacks": fairness,
                "bypass_hits": bypass_hits,
                "non_idle_opportunities": opportunities,
                "accuracy": (hits / attempts) if attempts else "",
                "coverage": (attempts / opportunities) if opportunities else "",
            }
        )
        summary_paths.append(summary)
        event_paths.append(events)
        print(f"[{index:02d}/46] PASS {name} hits={hits}/{attempts}")

    predictor_csv = args.output / "a5-predictor-metrics.csv"
    with predictor_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(predictor_rows[0]))
        writer.writeheader()
        writer.writerows(predictor_rows)

    combined_summary = args.output / "a5-summary-all.csv"
    combined_events = args.output / "a5-events-all.csv"
    append_csv(summary_paths, combined_summary)
    append_csv(event_paths, combined_events)
    run(
        [sys.executable, str(aggregator), str(combined_summary),
         "--events", str(combined_events),
         "--event-output", str(args.output / "a5-event-runs.csv"),
         "--output", str(args.output / "a5-aggregate.csv"),
         "--fail-on-correctness"],
        cwd=project,
        log=args.output / "aggregate.log",
    )
    print(f"A5_FROZEN_REGRESSION_PASS runs={len(predictor_rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
