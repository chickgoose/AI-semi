#!/usr/bin/env python3
"""Reproduce and require rejection of the three A8 evidence escapes."""

import argparse
import csv
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "tests/a7_weighted_fovea_ddr/validate_submission_evidence.py"


def invoke(csv_path: Path, log_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--events-csv", str(csv_path),
         "--run-log", str(log_path)], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def require(name: str, result: subprocess.CompletedProcess[str], diagnostic: str) -> None:
    marker = f"A7_W7_EVIDENCE_{diagnostic}_CAUGHT:"
    if result.returncode == 0 or result.stdout.count(marker) != 1:
        raise SystemExit(f"A7_W7_EVIDENCE_MUTANT_GATE_FAIL name={name} producer_rc=0 validator_rc={result.returncode} output={result.stdout!r}")
    print(f"A7_W7_EVIDENCE_MUTANT_EXPECTED_FAIL_PASS name={name} producer_rc=0 diagnostic={diagnostic}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-csv", required=True, type=Path)
    parser.add_argument("--run-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    swapped = args.output / "logical-source-mutant.csv"
    shutil.copyfile(args.events_csv, swapped)
    with swapped.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    # bitmap=3 has two legal in-range sources.  Change only logical_source to
    # the other live address so range/cardinality checks still pass and the
    # retire-address binding is what must reject the artifact.
    row = rows[2]
    original = int(row["logical_source"])
    row["logical_source"] = "1" if original == 0 else "0"
    with swapped.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    require("logical_source_address_swap", invoke(swapped, args.run_log), "ADDRESS_MISMATCH")

    # Model a producer that returns rc=0 but creates no CSV.
    require("rc0_without_csv", invoke(args.output / "absent.csv", args.run_log), "CSV_MISSING")

    no_sentinel = args.output / "rc0-without-sentinel.log"
    no_sentinel.write_text("producer returned zero without qualification sentinel\n", encoding="utf-8")
    require("rc0_without_exact_sentinel", invoke(args.events_csv, no_sentinel), "SENTINEL_MISSING")
    print("A7_W7_THREE_EVIDENCE_MUTANT_GATE_PASS count=3")


if __name__ == "__main__":
    main()
