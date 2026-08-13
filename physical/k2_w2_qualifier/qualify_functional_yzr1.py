#!/usr/bin/env python3
"""Fail-closed, loss-only qualification of the yZr1 functional archive."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from qualify_ganghee_golden import (
    GoldenQualificationError,
    canonical,
    extract_members,
    sha256,
    stable_read,
    write_exclusive,
)


ARCHIVE_SHA256 = "22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39"
ARCHIVE_LABEL = "eval-fovea-cluster2.yZr1kmYL.tar.gz"
EXPECTED_MEMBER_COUNT = 344
EXPECTED_LEDGER_COUNT = 338
EXPECTED_ATTEMPT = "/tmp/aer-eval-47e1f2f/eval-fovea-cluster2.yZr1kmYL"
SCHEMA = "k2_functional_yzr1_loss_only_receipt_v1"
ROOT_FILES = {
    "capacity-stems.txt", "cluster2-run.log", "fovea-run.log",
    "full-stems.txt", "provenance.txt", "result-artifacts.sha256",
}
EXPECTED_PROVENANCE = {
    "snapshot_head": "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be",
    "binding_reset_quiet_arming_patch": "workspace-diff",
    "snapshot_archive_sha256": "3a970fd551e9ec7e2cc645e559889e30eaac4d9ec64152631e2e336e2c9664c3",
    "canonical_rtl_date_kst": "2026-08-09",
    "attempt": EXPECTED_ATTEMPT,
    "hostname": "snu.polaris.09",
    "start_utc": "2026-08-13T01:56:31Z",
    "finish_utc": "2026-08-13T01:58:34Z",
    "tool": "xrun(64)\t23.09-s013",
}
EXPECTED_TOTALS = {
    "fovea": {"generated": 106416, "source_overrun": 28187,
              "accepted": 78229, "delivered": 78229, "errors": 0},
    "cluster2": {"generated": 106416, "source_overrun": 12259,
                 "accepted": 94157, "delivered": 94157, "errors": 0},
}
CSV_FIELDS = (
    "candidate", "test", "seed", "load_pct", "stim_cycles", "generated",
    "source_overrun", "accepted", "delivered", "errors", "total_cycles",
    "avg_e2e_latency", "max_e2e_latency", "avg_internal_latency",
    "max_internal_latency", "throughput", "fairness", "max_request_wait",
    "avg_timing_error", "max_timing_error", "measurement_delivered",
    "measurement_cycles",
)
LEDGER_LINE = re.compile(r"^([0-9a-f]{64})  (/.+)$")


def decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldenQualificationError(f"{label}: not UTF-8") from exc


def require_member(members: dict[str, bytes], name: str) -> bytes:
    try:
        return members[name]
    except KeyError as exc:
        raise GoldenQualificationError(f"missing archive member: {name}") from exc


def parse_provenance(data: bytes) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in decode(data, "provenance").splitlines():
        if line.startswith("TOOL:\t"):
            key, value = "tool", line[len("TOOL:\t"):]
        elif "=" in line:
            key, value = line.split("=", 1)
        else:
            raise GoldenQualificationError("provenance: malformed line")
        if key in rows or not key or not value:
            raise GoldenQualificationError("provenance: duplicate or empty field")
        rows[key] = value
    if rows != EXPECTED_PROVENANCE:
        raise GoldenQualificationError("provenance: exact yZr1 field binding mismatch")
    if "0FfaT8kp" in data.decode("utf-8", errors="ignore"):
        raise GoldenQualificationError("provenance: stale 0Ffa attempt reference")
    return rows


def parse_stems(data: bytes, label: str, expected_count: int) -> list[str]:
    stems = [line.strip() for line in decode(data, label).splitlines() if line.strip()]
    if len(stems) != expected_count or len(set(stems)) != expected_count:
        raise GoldenQualificationError(f"{label}: expected {expected_count} unique stems")
    if any(re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", stem) is None for stem in stems):
        raise GoldenQualificationError(f"{label}: invalid stem")
    return stems


def parse_ledger(data: bytes, members: dict[str, bytes], attempt: str) -> dict[str, Any]:
    rows = decode(data, "result ledger").splitlines()
    if len(rows) != EXPECTED_LEDGER_COUNT:
        raise GoldenQualificationError(
            f"result ledger: expected {EXPECTED_LEDGER_COUNT} rows, got {len(rows)}")
    prefix = attempt + "/"
    seen: set[str] = set()
    for index, line in enumerate(rows, 1):
        match = LEDGER_LINE.fullmatch(line)
        if match is None:
            raise GoldenQualificationError(f"result ledger:{index}: malformed row")
        expected, absolute = match.groups()
        if not absolute.startswith(prefix):
            raise GoldenQualificationError(f"result ledger:{index}: attempt path mismatch")
        relative = absolute[len(prefix):]
        if not relative.startswith("results/") or relative in seen:
            raise GoldenQualificationError(f"result ledger:{index}: duplicate or out-of-scope path")
        artifact = require_member(members, relative)
        if sha256(artifact) != expected:
            raise GoldenQualificationError(f"result ledger:{index}: artifact SHA-256 mismatch: {relative}")
        seen.add(relative)
    result_members = {name for name in members if name.startswith("results/")}
    if seen != result_members:
        raise GoldenQualificationError("result ledger: partial or extra results artifact closure")
    return {"rows": len(rows), "verified": len(seen),
            "sha256": sha256(data), "artifact_set_sha256": sha256(canonical(sorted(seen)))}


def parse_metrics_csv(data: bytes, label: str, expected_candidate_id: str) -> dict[str, Any]:
    try:
        reader = csv.DictReader(io.StringIO(decode(data, label), newline=""))
        rows = list(reader)
    except csv.Error as exc:
        raise GoldenQualificationError(f"{label}: malformed CSV: {exc}") from exc
    if tuple(reader.fieldnames or ()) != CSV_FIELDS or len(rows) != 1:
        raise GoldenQualificationError(f"{label}: field inventory or row count mismatch")
    row = rows[0]
    numeric: dict[str, int] = {}
    for key in ("generated", "source_overrun", "accepted", "delivered", "errors"):
        if re.fullmatch(r"[0-9]+", row[key]) is None:
            raise GoldenQualificationError(f"{label}: invalid {key}")
        numeric[key] = int(row[key])
    if numeric["generated"] != numeric["source_overrun"] + numeric["accepted"]:
        raise GoldenQualificationError(f"{label}: generated != source_overrun + accepted")
    if numeric["accepted"] != numeric["delivered"] or numeric["errors"] != 0:
        raise GoldenQualificationError(f"{label}: accepted/delivered conservation or errors gate failed")
    if row["candidate"] != expected_candidate_id:
        raise GoldenQualificationError(f"{label}: candidate mismatch")
    return {**numeric, "test": row["test"], "seed": row["seed"]}


def parse_candidate_run_log(data: bytes, candidate: str, full_stems: list[str]) -> dict[str, Any]:
    value = decode(data, f"{candidate} run log")
    if re.search(r"(?:RUN_FAIL|AER_CLEAN_TEST_FAIL|AER_RESET_DRAIN_FAIL)", value):
        raise GoldenQualificationError(f"{candidate} run log: failure marker")
    run_pass = re.findall(rf"^RUN_PASS candidate={re.escape(candidate)} stem=([^\s]+)$", value, re.M)
    if run_pass != full_stems:
        raise GoldenQualificationError(f"{candidate} run log: ordered 50/50 RUN_PASS inventory mismatch")
    if len(re.findall(r"^AER_CLEAN_TEST_PASS\s+", value, re.M)) != 51:
        raise GoldenQualificationError(f"{candidate} run log: expected 50 trace plus one reset PASS")
    reset = re.findall(
        r"^AER_RESET_DRAIN_PASS generated=([0-9]+) accepted=([0-9]+) delivered=([0-9]+)$",
        value, re.M)
    if reset != [("16", "16", "16")]:
        raise GoldenQualificationError(f"{candidate} run log: reset PASS mismatch")
    complete = f"CANDIDATE_COMPLETE key={candidate} pairwise_status=0"
    nonempty = [line for line in value.splitlines() if line.strip()]
    if nonempty[-1] != complete or nonempty.count(complete) != 1:
        raise GoldenQualificationError(f"{candidate} run log: final completion marker mismatch")
    metrics = re.findall(r"^AER_CLEAN_METRICS\s+(.+)$", value, re.M)
    if len(metrics) != 51 or any(
            re.search(r"(?:^|\s)errors=0(?:\s|$)", row) is None for row in metrics):
        raise GoldenQualificationError(f"{candidate} run log: metrics/error inventory mismatch")
    return {"run_pass": len(run_pass), "test_pass": 51, "reset_pass": True,
            "pairwise_status": 0, "sha256": sha256(data)}


def validate_trace_log(data: bytes, stem: str, candidate: str) -> None:
    value = decode(data, f"{candidate}/{stem}/trace.log")
    if len(re.findall(r"^AER_CLEAN_TEST_PASS\s+", value, re.M)) != 1:
        raise GoldenQualificationError(f"{candidate}/{stem}: trace PASS marker mismatch")
    if re.search(r"AER_CLEAN_TEST_FAIL|\*E,", value):
        raise GoldenQualificationError(f"{candidate}/{stem}: trace failure/error marker")
    if len(re.findall(r"^AER_CLEAN_METRICS\s+", value, re.M)) != 1:
        raise GoldenQualificationError(f"{candidate}/{stem}: trace metrics marker mismatch")


def validate_candidate(members: dict[str, bytes], candidate: str,
                       full_stems: list[str], capacity_stems: list[str]) -> dict[str, Any]:
    candidate_id = ("ganghee-native-coordinate-source-projection" if candidate == "fovea"
                    else "ganghee-cluster2-row-bitmap")
    prefix = f"results/{candidate}/runs/"
    observed = {name[len(prefix):].split("/", 1)[0]
                for name in members if name.startswith(prefix)}
    if observed != set(full_stems):
        raise GoldenQualificationError(f"{candidate}: result run directory inventory mismatch")
    totals = {key: 0 for key in ("generated", "source_overrun", "accepted", "delivered", "errors")}
    capacity_totals = dict(totals)
    run_rows: list[dict[str, Any]] = []
    for stem in full_stems:
        base = f"{prefix}{stem}"
        row = parse_metrics_csv(require_member(members, f"{base}/trace.csv"),
                                f"{candidate}/{stem}/trace.csv", candidate_id)
        require_member(members, f"{base}/trace.events.csv")
        validate_trace_log(require_member(members, f"{base}/trace.log"), stem, candidate)
        for key in totals:
            totals[key] += row[key]
            if stem in capacity_stems:
                capacity_totals[key] += row[key]
        run_rows.append({"stem": stem, **row})
    if totals != EXPECTED_TOTALS[candidate]:
        raise GoldenQualificationError(f"{candidate}: exact full50 totals mismatch")
    pair_status = decode(require_member(
        members, f"results/{candidate}/pairwise-cross-map.status"), "pairwise status")
    if pair_status != "0\n":
        raise GoldenQualificationError(f"{candidate}: pairwise status is not zero")
    reset = parse_metrics_csv(require_member(
        members, f"results/{candidate}/reset/basic_reset_drain.csv"),
        f"{candidate} reset CSV", candidate_id)
    if reset != {"generated": 16, "source_overrun": 0, "accepted": 16,
                 "delivered": 16, "errors": 0, "test": "basic_reset_drain", "seed": "1"}:
        raise GoldenQualificationError(f"{candidate}: reset CSV mismatch")
    run_log = parse_candidate_run_log(require_member(members, f"{candidate}-run.log"),
                                      candidate, full_stems)
    return {"full50_runs": len(run_rows), "capacity22_runs": len(capacity_stems),
            "full50_totals": totals, "capacity22_totals": capacity_totals,
            "source_overrun_semantics": "INGRESS_CAPACITY_LOSS_NOT_ACCEPTED_EVENT_CORRUPTION",
            "accepted_equals_delivered": True, "reset": reset, "run_log": run_log}


def analyze_members(members: dict[str, bytes]) -> dict[str, Any]:
    if len(members) != EXPECTED_MEMBER_COUNT:
        raise GoldenQualificationError("functional archive member count mismatch")
    if {name for name in members if "/" not in name} != ROOT_FILES:
        raise GoldenQualificationError("functional archive root inventory mismatch")
    if any(name.endswith("eval-driver-final.log") for name in members):
        raise GoldenQualificationError("stale outer eval-driver-final.log must not be trusted or packaged")
    provenance = parse_provenance(require_member(members, "provenance.txt"))
    full_stems = parse_stems(require_member(members, "full-stems.txt"), "full stems", 50)
    capacity_stems = parse_stems(require_member(members, "capacity-stems.txt"), "capacity stems", 22)
    if not set(capacity_stems).issubset(full_stems):
        raise GoldenQualificationError("capacity stems are not a subset of full stems")
    ledger = parse_ledger(require_member(members, "result-artifacts.sha256"), members,
                          provenance["attempt"])
    candidates = {name: validate_candidate(members, name, full_stems, capacity_stems)
                  for name in ("fovea", "cluster2")}
    return {"provenance": provenance, "full_stems": full_stems,
            "capacity_stems": capacity_stems, "ledger": ledger, "candidates": candidates}


def qualify_archive(archive_path: Path) -> dict[str, Any]:
    data, identity = stable_read(Path(os.path.abspath(archive_path)))
    actual = sha256(data)
    if actual != ARCHIVE_SHA256:
        raise GoldenQualificationError(
            f"functional archive SHA-256 mismatch: expected {ARCHIVE_SHA256}, got {actual}")
    members = extract_members(data, EXPECTED_MEMBER_COUNT)
    analysis = analyze_members(members)
    path = Path(os.path.abspath(archive_path))
    _, final_identity = stable_read(path)
    if final_identity != identity:
        raise GoldenQualificationError("functional archive changed before receipt publication")
    inventory = [{"path": name, "sha256": sha256(content), "size": len(content)}
                 for name, content in sorted(members.items())]
    qualifier_sources = {}
    for source in (Path(__file__), SCRIPT_DIRECTORY / "qualify_ganghee_golden.py"):
        source_data, _ = stable_read(source)
        qualifier_sources[source.name] = sha256(source_data)
    return {
        "schema": SCHEMA,
        "status": "WORKSPACE_DIFF_FUNCTIONAL_LOSS_EVIDENCE_GO",
        "archive": {"path": ARCHIVE_LABEL, "sha256": actual, "size": len(data),
                    "member_count": len(members),
                    "member_inventory_sha256": sha256(canonical(inventory))},
        "qualifier_sources": qualifier_sources,
        **analysis,
        "excluded_untrusted_evidence": {
            "outer_eval_driver_final_log": "STALE_0Ffa_NOT_IN_ARCHIVE_NOT_BOUND",
        },
        "claim_boundary": {
            "loss_accounting_on_full50_capacity22": "GO",
            "accepted_event_conservation": "GO",
            "local_functional_runs_and_reset": "GO",
            "official_common_receipt": "HOLD_WORKSPACE_DIFF_NON_OFFICIAL",
            "ppa_area_timing_power_energy": "FORBIDDEN_NOT_EVIDENCED",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = qualify_archive(args.archive)
        write_exclusive(args.output, canonical(receipt))
    except (GoldenQualificationError, OSError) as exc:
        print(f"K2_FUNCTIONAL_YZR1_HOLD: {exc}", file=sys.stderr)
        return 1
    print("K2_FUNCTIONAL_YZR1_LOSS_ONLY_GO runs=100 reset=2 ledger=338 ppa=FORBIDDEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
