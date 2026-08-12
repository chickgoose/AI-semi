#!/usr/bin/env python3
"""Read-only contract comparison against immutable A5/A8 evaluator commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from model import PairedCorticalColumnK2  # noqa: E402

A5_COMMIT = "41c425bec79aca6c84f5856ca7dee2a4865a6447"
A8_COMMIT = "1248a19e1f3bea4c519645460cb810b19fab4c5d"
A5_ORACLE = "tests/a5_k2_common_evaluator/k2_oracle.py"
A8_ORACLE = "tests/w8_k2_scheduler_contracts/oracle.py"


def git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {result.stdout}")
    return result.stdout


def blob(commit: str, path: str) -> str:
    resolved = git("rev-parse", f"{commit}^{{commit}}").strip()
    if resolved != commit:
        raise RuntimeError(f"immutable evaluator commit mismatch: {commit}")
    return git("show", f"{commit}:{path}")


def tuple_constant(source: str, name: str) -> list[int]:
    match = re.search(rf"^{name}\s*=\s*\(([^)]*)\)", source, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"missing evaluator constant: {name}")
    return [int(value.strip()) for value in match.group(1).split(",") if value.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"cross-check refuses existing output: {args.output}")
    a5_source = blob(A5_COMMIT, A5_ORACLE)
    a8_source = blob(A8_COMMIT, A8_ORACLE)
    a5_wheel = tuple_constant(a5_source, "ROW_WHEEL")
    a8_calendar = tuple_constant(a8_source, "PAIRED_ROW_PROPOSAL_ROWS")
    if a5_wheel != [0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3]:
        raise RuntimeError("A5 frozen wheel changed")
    if a8_calendar != [0, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 3]:
        raise RuntimeError("A8 frozen paired calendar changed")
    required_a8 = (
        "grant_count", "bundle_ready", "ATOMIC_BUNDLE_PARTIAL_COMMIT",
        "CALENDAR_ADVANCE_ON_UNCOMMITTED_LANE",
    )
    if not all(marker in a8_source for marker in required_a8):
        raise RuntimeError("A8 atomic boundary markers absent")

    model = PairedCorticalColumnK2()
    candidate_rows = []
    for _ in range(6):
        result = model.step(0xFFFF, True)
        candidate_rows.extend(
            source >> 2 for source in (result.grant_addr0, result.grant_addr1)
        )
    counts = [candidate_rows.count(row) for row in range(4)]
    if counts != [1, 5, 5, 1]:
        raise RuntimeError(f"candidate aggregate changed: {counts}")
    document = {
        "schema": "a4_pcck2_evaluator_crosscheck_v1",
        "candidate_semantic_grade": "AGGREGATE_ONLY",
        "common": {
            "source_count": 16, "maximum_grants": 2,
            "event_identity": "address_only_with_tb_sidecar",
            "persistent_row_aggregate": counts,
        },
        "a5": {
            "commit": A5_COMMIT,
            "oracle_path": A5_ORACLE,
            "oracle_sha256": hashlib.sha256(a5_source.encode()).hexdigest(),
            "expected_scalar_wheel": a5_wheel,
            "candidate_committed_rows_first_epoch": candidate_rows,
            "scheduler_transport": "NEEDS_ORDERED_LINK_ADAPTER",
            "adapter": "a4_pcck2_ordered_link_adapter.sv",
            "scalar_prefix_equivalent": False,
            "evaluator_grade": "NOT_FULL_PREFIX__NO_A5_PASS_CLAIMED",
        },
        "a8": {
            "commit": A8_COMMIT,
            "oracle_path": A8_ORACLE,
            "oracle_sha256": hashlib.sha256(a8_source.encode()).hexdigest(),
            "expected_paired_rows": a8_calendar,
            "candidate_committed_rows_first_epoch": candidate_rows,
            "atomic_bundle_pin_semantics_aligned": True,
            "calendar_sequence_equivalent": candidate_rows == a8_calendar,
            "signed_equal_and_opposite_debt_equivalent": False,
            "owner_binding_grade": "UNBOUND_AGGREGATE_ONLY",
        },
        "limits": [
            "Transport compatibility does not upgrade scheduler semantics.",
            "No A5 or A8 file was modified or imported into candidate RTL.",
            "Aggregate preservation is not scalar-prefix equivalence.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("A4_PCCK2_CONTRACT_CROSSCHECK_PASS semantic_grade=AGGREGATE_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
