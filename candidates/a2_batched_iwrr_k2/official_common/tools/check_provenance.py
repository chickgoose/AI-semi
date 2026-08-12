#!/usr/bin/env python3
"""Fail-closed checker for the A2 official always-ready compile closure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


SENTINEL = "A2_K2_PROVENANCE_FAIL"
SCHEMA = "a2-k2-official-always-ready-provenance-v1"


def die(message: str) -> "NoReturn":
    print(f"{SENTINEL} {message}", file=sys.stderr)
    raise SystemExit(2)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        die(f"contract-read path={path} error={error}")
    if not isinstance(value, dict):
        die("contract-root-not-object")
    return value


def git_output(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, text=True, capture_output=True,
            check=False,
        )
    except OSError as error:
        die(f"git-exec error={error}")
    if result.returncode != 0:
        die(f"git {' '.join(args)} rc={result.returncode} stderr={result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--contract", type=Path)
    args = parser.parse_args()

    root = args.repo.resolve()
    contract_path = (args.contract.resolve() if args.contract else
        root / "candidates/a2_batched_iwrr_k2/official_common/provenance.json")
    contract = load_contract(contract_path)
    if contract.get("schema") != SCHEMA:
        die(f"schema expected={SCHEMA} got={contract.get('schema')}")

    normalized = contract.get("normalized_contract")
    expected_normalized = {
        "num_sources": 16,
        "address_width": 16,
        "retire_lanes": 2,
        "fifo_depth": 0,
        "event_identity": "selected-normalized-address-event",
        "accepted_counts": [0, 1, 2],
        "atomic_count2": True,
        "independent_lane_stall": False,
    }
    if normalized != expected_normalized:
        die("normalized-contract-mismatch")

    filelist_name = contract.get("compile_filelist")
    compile_files = contract.get("compile_files")
    if not isinstance(filelist_name, str) or not isinstance(compile_files, list):
        die("compile-filelist-schema")
    filelist_path = root / filelist_name
    try:
        filelist_lines = [line.strip() for line in
                          filelist_path.read_text(encoding="utf-8").splitlines()
                          if line.strip()]
    except OSError as error:
        die(f"filelist-read path={filelist_path} error={error}")
    if filelist_lines != compile_files:
        die(f"filelist-order expected={compile_files} got={filelist_lines}")

    hashes = contract.get("file_sha256")
    if not isinstance(hashes, dict) or not hashes:
        die("file-sha256-schema")
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            die("file-sha256-entry-schema")
        path = root / relative
        if not path.is_file():
            die(f"missing path={relative}")
        actual = sha256(path)
        if actual != expected:
            die(f"sha256 path={relative} expected={expected} got={actual}")

    owner = contract.get("owner")
    if not isinstance(owner, dict):
        die("owner-schema")
    commit = owner.get("commit")
    owner_path = owner.get("path")
    expected_blob = owner.get("git_blob")
    if not all(isinstance(value, str)
               for value in (commit, owner_path, expected_blob)):
        die("owner-fields")
    # Integration may cherry-pick the owner into a release branch, so ancestry
    # is not a valid identity test.  Bind the immutable commit object and its
    # exact owner blob instead; the live compile bytes were checked above.
    actual_blob = git_output(root, "rev-parse", f"{commit}:{owner_path}")
    if actual_blob != expected_blob:
        die(f"owner-blob expected={expected_blob} got={actual_blob}")

    print(f"A2_K2_PROVENANCE_PASS files={len(hashes)} owner={commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
