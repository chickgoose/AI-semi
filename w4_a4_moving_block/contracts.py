#!/usr/bin/env python3
"""Fail-closed provenance checks for independent A4 W4 qualification."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

COMMON_COMMIT = "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be"
A4_COMMIT = "850fbcfa4ad168b1250223610780f11378f6c391"
PINS = {
    "common": {
        "benchmarks/clean_slate_aer/generate_trace.py": "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
        "benchmarks/clean_slate_aer/manifest.neutrality-n16.json": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
        "benchmarks/clean_slate_aer/manifest.multilane-n16.json": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
    },
    "a4": {
        "rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.sv": "18e00a2acba587af7f81f2f1608268f4c37d9068a3e7e3f2b29611c4f8ea5677",
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git head unavailable: {root}")
    return result.stdout.strip()


def git_blob(root: Path, commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{relative}"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"pinned git object unavailable: {commit}:{relative}")
    return result.stdout


def validate(root: Path, expected_head: str, pins: dict[str, str]) -> None:
    if head(root) != expected_head:
        raise RuntimeError(f"commit mismatch: {root} expected={expected_head} actual={head(root)}")
    for relative, expected in pins.items():
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"missing pinned input: {path}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {relative} expected={expected} actual={actual}")


def materialize_a4(root: Path, output: Path) -> None:
    relative, expected = next(iter(PINS["a4"].items()))
    content = git_blob(root, A4_COMMIT, relative)
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"pinned A4 blob SHA mismatch: expected={expected} actual={actual}"
        )
    output.write_bytes(content)


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: contracts.py COMMON_ROOT A4_ROOT RTL_OUTPUT", file=sys.stderr)
        return 64
    try:
        validate(Path(sys.argv[1]).resolve(), COMMON_COMMIT, PINS["common"])
        a4_root = Path(sys.argv[2]).resolve()
        materialize_a4(a4_root, Path(sys.argv[3]).resolve())
    except (OSError, RuntimeError) as error:
        print(f"W4_CONTRACT_FAIL {error}", file=sys.stderr)
        return 2
    print(f"W4_CONTRACT_PASS common={COMMON_COMMIT} a4_object={A4_COMMIT} pins=4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
