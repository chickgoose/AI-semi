#!/usr/bin/env python3
"""Immutable A8 binding adapter for the atomic scalar-prefix transaction view."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE))
from oracle import PolicyState, scalar_prefix_k2  # noqa: E402


CONTRACT = "exact_weighted_scalar_prefix_k2"


class AtomicTransactionView:
    def __init__(self) -> None:
        self.policy = PolicyState()
        self.held: tuple[int, ...] | None = None
        self.held_post = PolicyState()

    def step(self, cycle: dict) -> dict:
        if bool(cycle.get("reset", False)):
            self.policy = PolicyState()
            self.held = None
            self.held_post = PolicyState()
            return {
                "grant_count": 0,
                "addresses": [None, None],
                "committed": [None, None],
                "held_after": [None, None],
            }

        if self.held is None:
            request = int(cycle["request"], 16)
            self.held, self.held_post = scalar_prefix_k2(request, self.policy)
        padded = list(self.held) + [None] * (2 - len(self.held))
        ready = bool(cycle["bundle_ready"])
        if ready:
            committed = padded
            held_after = [None, None]
            self.policy = self.held_post
            self.held = None
        else:
            committed = [None, None]
            held_after = padded
        return {
            "grant_count": sum(item is not None for item in padded),
            "addresses": padded,
            "committed": committed,
            "held_after": held_after,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    if args.contract != CONTRACT:
        raise SystemExit(f"unsupported contract {args.contract}")
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
        if case["contract"] != CONTRACT:
            continue
        view = AtomicTransactionView()
        cases[case["name"]] = [view.step(cycle) for cycle in case["cycles"]]

    document = {
        "schema_version": 2,
        "contract": CONTRACT,
        "vectors_sha256": hashlib.sha256(args.vectors.read_bytes()).hexdigest(),
        "provenance": provenance,
        "cases": cases,
    }
    args.result.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
