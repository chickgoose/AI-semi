#!/usr/bin/env python3
"""Pinned-input and decision-exit contracts for the ECRF Wave-3 runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Sequence


EXPECTED_COMMON_COMMIT = "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be"
EXPECTED_GENERATOR_VERSION = "4.0"
EXPECTED_INPUT_SHA256 = {
    "benchmarks/clean_slate_aer/generate_trace.py":
        "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
    "benchmarks/clean_slate_aer/manifest.neutrality-n16.json":
        "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "benchmarks/clean_slate_aer/manifest.multilane-n16.json":
        "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
EXPECTED_RUN_COUNTS = {
    "benchmarks/clean_slate_aer/manifest.neutrality-n16.json": 50,
    "benchmarks/clean_slate_aer/manifest.multilane-n16.json": 22,
}


class ContractError(RuntimeError):
    """The runner cannot reproduce or safely classify the recorded result."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.strip() or "git rev-parse failed"
        raise ContractError(f"common commit unavailable: {diagnostic}")
    return result.stdout.strip()


def validate_common(
    root: Path,
    *,
    expected_commit: str = EXPECTED_COMMON_COMMIT,
    expected_hashes: Mapping[str, str] = EXPECTED_INPUT_SHA256,
    expected_counts: Mapping[str, int] = EXPECTED_RUN_COUNTS,
    head_resolver: Callable[[Path], str] = git_head,
) -> None:
    root = root.resolve()
    actual_commit = head_resolver(root)
    if actual_commit != expected_commit:
        raise ContractError(
            "common commit mismatch: "
            f"expected={expected_commit} actual={actual_commit}"
        )

    for relative, expected in expected_hashes.items():
        path = root / relative
        if not path.is_file():
            raise ContractError(f"missing pinned common input: {path}")
        actual = sha256(path)
        if actual != expected:
            raise ContractError(
                f"common input SHA-256 mismatch: {relative} "
                f"expected={expected} actual={actual}"
            )

    generator = root / "benchmarks/clean_slate_aer/generate_trace.py"
    match = re.search(
        r'^GENERATOR_VERSION\s*=\s*["\x27]([^"\x27]+)["\x27]',
        generator.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    actual_version = match.group(1) if match else "missing"
    if actual_version != EXPECTED_GENERATOR_VERSION:
        raise ContractError(
            "generator version mismatch: "
            f"expected={EXPECTED_GENERATOR_VERSION} actual={actual_version}"
        )

    for relative, expected in expected_counts.items():
        path = root / relative
        runs = json.loads(path.read_text(encoding="utf-8")).get("runs")
        actual = len(runs) if isinstance(runs, list) else -1
        if actual != expected:
            raise ContractError(
                f"suite count mismatch: {relative} "
                f"expected={expected} actual={actual}"
            )


def decision_exit(summary_path: Path, require_go: bool) -> tuple[int, str]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    decision = summary.get("decision")
    rtl_permitted = summary.get("rtl_permitted")
    if decision not in {"GO", "HOLD"} or not isinstance(rtl_permitted, bool):
        raise ContractError("invalid ECRF decision summary schema")
    if (decision == "GO") != rtl_permitted:
        raise ContractError(
            f"inconsistent ECRF decision: decision={decision} "
            f"rtl_permitted={rtl_permitted}"
        )
    exit_status = 3 if require_go and decision != "GO" else 0
    message = (
        "ECRF_DECISION_EXIT "
        f"evaluation_complete=1 decision={decision} "
        f"rtl_permitted={int(rtl_permitted)} "
        f"require_go={int(require_go)} exit={exit_status}"
    )
    return exit_status, message


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inputs = subparsers.add_parser("inputs")
    inputs.add_argument("--common-root", type=Path, required=True)
    decision = subparsers.add_parser("decision")
    decision.add_argument("--summary", type=Path, required=True)
    decision.add_argument("--require-go", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "inputs":
            validate_common(args.common_root)
            print(
                "ECRF_INPUT_CONTRACT_PASS "
                f"commit={EXPECTED_COMMON_COMMIT} pinned_files=3"
            )
            return 0
        exit_status, message = decision_exit(args.summary, args.require_go)
        print(message)
        return exit_status
    except (ContractError, json.JSONDecodeError, OSError) as error:
        print(f"ECRF_CONTRACT_FAIL {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
