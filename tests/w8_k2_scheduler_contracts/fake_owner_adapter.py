#!/usr/bin/env python3
"""Snapshot-local positive fixture for binding transport tests; not RTL evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from oracle import CycleInput, run_trace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    if args.challenge.parent.resolve() != args.snapshot.resolve():
        raise SystemExit("challenge is not from materialized snapshot")
    challenge_bytes = args.challenge.read_bytes()
    challenge = json.loads(challenge_bytes)
    provenance = {
        "owner_commit": challenge["owner_commit"],
        "artifact_sha256": challenge["artifact_sha256"],
        "snapshot_manifest_sha256": challenge["snapshot_manifest_sha256"],
        "challenge_sha256": hashlib.sha256(challenge_bytes).hexdigest(),
    }
    vectors = json.loads(args.vectors.read_text(encoding="utf-8"))
    cases = {}
    for case in vectors["cases"]:
        if case["contract"] != args.contract:
            continue
        trace = [
            CycleInput(
                request=int(cycle["request"], 16),
                bundle_ready=bool(cycle["bundle_ready"]),
                reset=bool(cycle.get("reset", False)),
                future_request=int(cycle.get("future_request", "0"), 16),
            )
            for cycle in case["cycles"]
        ]
        cases[case["name"]] = [
            {
                "grant_count": observation.grant_count,
                "addresses": list(observation.addresses),
                "committed": list(observation.committed),
                "held_after": list(observation.held_after),
            }
            for observation in run_trace(args.contract, trace)
        ]
    args.result.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "contract": args.contract,
                "vectors_sha256": hashlib.sha256(args.vectors.read_bytes()).hexdigest(),
                "provenance": provenance,
                "cases": cases,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
