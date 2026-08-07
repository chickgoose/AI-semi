#!/usr/bin/env python3
"""Run A4 RTL on the unmodified frozen 46-trace TB with local Verilator."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


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
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.stdout


def append_csv(inputs: list[Path], output: Path) -> None:
    header: list[str] | None = None
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination, lineterminator="\n")
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


def write_checksums(paths: list[Path], output: Path) -> None:
    with output.open("w", encoding="utf-8") as stream:
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stream.write(f"{digest}  {path.name}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a4-verilator-frozen-46"))
    parser.add_argument("--trace-dir", type=Path, default=Path("/tmp/a4-verilator-traces"))
    parser.add_argument("--verilator", default=os.environ.get("VERILATOR", "verilator"))
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--design", choices=("a4", "flat"), default="a4")
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[2]
    manifest = project / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
    generator = project / "benchmarks/clean_slate_aer/generate_trace.py"
    preparer = project / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
    aggregator = project / "benchmarks/clean_slate_aer/aggregate.py"
    args.output.mkdir(parents=True, exist_ok=True)
    args.trace_dir.mkdir(parents=True, exist_ok=True)

    run(
        [sys.executable, str(generator), "--manifest", str(manifest),
         "--output-dir", str(args.trace_dir)],
        cwd=project,
        log=args.output / "trace-generation.log",
    )

    build_dir = args.output / "verilated"
    executable = build_dir / f"{args.design}_clean"
    if not args.skip_build:
        build_dir.mkdir(parents=True, exist_ok=True)
        command = [
                args.verilator,
                "--binary", "--timing", "--assert", "-Wall", "-Wno-fatal",
                "-Wno-TIMESCALEMOD", "-Wno-BLKSEQ", "-Wno-UNUSEDSIGNAL",
                "-Wno-PINCONNECTEMPTY", "-Wno-DECLFILENAME",
                "-Wno-SYNCASYNCNET", "--top-module", "aer_clean_tb",
                "-GNUM_SOURCES=16", "-GADDR_WIDTH=16", "-GRETIRE_LANES=1",
        ]
        if args.design == "a4":
            command.extend([
                "-f", str(project / "tests/a4/clean_tb.f"),
                "-f", str(project / "tb/filelists/a4_quadtree_fabric.f"),
                str(project / "tests/a4/a4_quadtree_properties.sv"),
            ])
        else:
            command.extend(["-f", str(project / "tb/clean/files.f")])
        command.extend(["--Mdir", str(build_dir), "-o", f"{args.design}_clean"])
        run(
            command,
            cwd=project,
            log=args.output / "build.log",
        )
    if not executable.is_file():
        raise SystemExit(f"missing simulator executable: {executable}")

    suite = json.loads(manifest.read_text(encoding="utf-8"))
    summary_paths: list[Path] = []
    event_paths: list[Path] = []
    result_rows: list[dict[str, object]] = []
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
        output = run(
            [
                str(executable), f"+CLEAN_TEST={metadata['report_group']}",
                f"+CANDIDATE={'a4-quadtree-verilator' if args.design == 'a4' else 'flat-rr-verilator'}",
                f"+METRICS={summary}", f"+EVENT_METRICS={events}",
                f"+TRACE_FILE={prepared}", f"+TRACE_NAME={metadata['report_group']}",
            ],
            cwd=project,
            log=args.output / f"{name}.log",
        )
        marker = f"AER_CLEAN_TEST_PASS {metadata['report_group']}"
        if marker not in output:
            raise SystemExit(f"missing PASS marker: {name}")
        with summary.open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        if int(row["errors"]) != 0 or int(row["accepted"]) != int(row["delivered"]):
            raise SystemExit(f"correctness failure in summary: {name}")
        result_rows.append({
            "name": name,
            "trace_sha256": metadata["trace_sha256"],
            "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(),
            "accepted": int(row["accepted"]),
            "delivered": int(row["delivered"]),
            "source_overrun": int(row["source_overrun"]),
        })
        summary_paths.append(summary)
        event_paths.append(events)
        print(f"[{index:02d}/46] PASS {name} accepted={row['accepted']} "
              f"overrun={row['source_overrun']}")

    prefix = "a4" if args.design == "a4" else "flat"
    combined_summary = args.output / f"{prefix}-summary-all.csv"
    combined_events = args.output / f"{prefix}-events-all.csv"
    append_csv(summary_paths, combined_summary)
    append_csv(event_paths, combined_events)
    run(
        [sys.executable, str(aggregator), str(combined_summary),
         "--events", str(combined_events),
         "--event-output", str(args.output / f"{prefix}-event-runs.csv"),
         "--output", str(args.output / f"{prefix}-aggregate.csv"),
         "--fail-on-correctness"],
        cwd=project,
        log=args.output / "aggregate.log",
    )
    with (args.output / f"{prefix}-run-manifest.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(result_rows)
    write_checksums(
        [combined_summary, combined_events, args.output / f"{prefix}-event-runs.csv",
         args.output / f"{prefix}-aggregate.csv"],
        args.output / f"{prefix}-result-checksums.txt",
    )
    print(f"A4_VERILATOR_FROZEN_REGRESSION_PASS design={args.design} "
          f"runs={len(result_rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
