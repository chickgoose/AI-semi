#!/usr/bin/env python3
"""Fake common-suite runner used only by receipt-wrapper integration tests."""

import csv
import hashlib
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
    required = ("AER_RECEIPT_CANDIDATE_MANIFEST", "AER_RECEIPT_CANDIDATE_BUNDLE",
                "AER_RECEIPT_SIMULATOR", "AER_RECEIPT_COMPILE_MANIFEST", "AER_RECEIPT_COMPILE_LOG")
    if any(not os.environ.get(key) for key in required):
        return 8
    candidate_manifest = Path(os.environ["AER_RECEIPT_CANDIDATE_MANIFEST"])
    candidate_doc = json.loads(candidate_manifest.read_text())
    if candidate_manifest.parent != Path(os.environ["AER_RECEIPT_CANDIDATE_BUNDLE"]):
        return 9
    for row in candidate_doc["filelist"]:
        source = candidate_manifest.parent / row["path"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
            return 10
    simulator_path = Path(os.environ["AER_RECEIPT_SIMULATOR"])
    if hashlib.sha256(simulator_path.read_bytes()).hexdigest() != os.environ["AER_RECEIPT_SIMULATOR_SHA256"]:
        return 11
    simulator = {"identity": os.environ["AER_RECEIPT_SIMULATOR_IDENTITY"],
        "executable_sha256": os.environ["AER_RECEIPT_SIMULATOR_SHA256"],
        "version_sha256": os.environ["AER_RECEIPT_SIMULATOR_VERSION_SHA256"]}
    compile_doc = {"schema_version": 1,
        "candidate_manifest_sha256": os.environ["AER_RECEIPT_CANDIDATE_MANIFEST_SHA256"],
        "candidate_bundle_sha256": os.environ["AER_RECEIPT_CANDIDATE_BUNDLE_SHA256"],
        "filelist": candidate_doc["filelist"], "top": candidate_doc["top"],
        "parameters": candidate_doc["parameters"], "defines": candidate_doc["defines"],
        "includes": candidate_doc["includes"], "source_count": candidate_doc["source_count"],
        "retire_lanes": candidate_doc["retire_lanes"], "simulator": simulator}
    Path(os.environ["AER_RECEIPT_COMPILE_MANIFEST"]).write_text(
        json.dumps(compile_doc, indent=2, sort_keys=True) + "\n")
    Path(os.environ["AER_RECEIPT_COMPILE_LOG"]).write_text(
        f"candidate_manifest={candidate_manifest}\n"
        f"candidate_bundle={os.environ['AER_RECEIPT_CANDIDATE_BUNDLE']}\n"
        f"simulator={os.environ['AER_RECEIPT_SIMULATOR']}\n")
    if os.environ.get("FAKE_RUNNER_MUTATE_PATH"):
        Path(os.environ["FAKE_RUNNER_MUTATE_PATH"]).write_text("mutated during execution\n")
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
