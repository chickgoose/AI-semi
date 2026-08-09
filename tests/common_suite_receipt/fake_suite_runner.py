#!/usr/bin/env python3
"""Fake common-suite runner used only by receipt-wrapper integration tests."""

import csv
import json
import os
import sys
from decimal import Decimal
from pathlib import Path


def load_pct(load):
    return (int(Decimal(str(load)) * 1000) + 5) // 10


def main():
    if os.environ.get("FAKE_RUNNER_FAIL") == "1":
        return 7
    trace_root = Path(os.environ["AER_RECEIPT_TRACE_DIR"])
    output_root = Path(os.environ["AER_RECEIPT_OUTPUT_DIR"]) / "results"
    candidate = os.environ["AER_RECEIPT_CANDIDATE"]
    index = json.loads((trace_root / "generation-index.json").read_text())
    for metadata in index["runs"]:
        run = metadata["run"]; root = output_root / run["name"]
        root.mkdir(parents=True)
        with (root / "trace.events.csv").open("x", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["candidate", "test", "seed", "load_pct", "run_evidence"])
            writer.writerow([candidate, metadata["report_group"], run["seed"], load_pct(run["load"]), run["name"]])
        with (root / "trace.csv").open("x", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(["candidate", "test", "seed", "load_pct"])
            writer.writerow([candidate, metadata["report_group"], run["seed"], load_pct(run["load"])])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
