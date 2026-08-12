#!/usr/bin/env python3
"""Subprocess negative tests for provenance failure behavior."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CHECKER = HERE.parent / "tools/check_provenance.py"
CONTRACT = HERE.parent / "provenance.json"
SENTINEL = "A2_K2_PROVENANCE_FAIL"


def invoke(contract: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(ROOT),
         "--contract", str(contract)],
        text=True, capture_output=True, check=False,
    )


def require_failure(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 2:
        raise AssertionError(
            f"{label}: expected exit2 got={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}")
    if SENTINEL not in result.stderr:
        raise AssertionError(f"{label}: missing exact sentinel: {result.stderr!r}")


def main() -> int:
    baseline = invoke(CONTRACT)
    if baseline.returncode != 0 or "A2_K2_PROVENANCE_PASS" not in baseline.stdout:
        raise AssertionError(
            f"baseline failed rc={baseline.returncode} "
            f"stdout={baseline.stdout!r} stderr={baseline.stderr!r}")

    original = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="a2-k2-provenance-") as temp_name:
        temp = Path(temp_name)

        bad_hash = json.loads(json.dumps(original))
        target = bad_hash["owner"]["path"]
        bad_hash["file_sha256"][target] = "0" * 64
        path = temp / "bad-hash.json"
        path.write_text(json.dumps(bad_hash), encoding="utf-8")
        require_failure(invoke(path), "sha mutation")

        bad_schema = json.loads(json.dumps(original))
        bad_schema["schema"] = "wrong"
        path = temp / "bad-schema.json"
        path.write_text(json.dumps(bad_schema), encoding="utf-8")
        require_failure(invoke(path), "schema mutation")

        missing = json.loads(json.dumps(original))
        missing["file_sha256"]["does/not/exist.sv"] = "0" * 64
        path = temp / "missing.json"
        path.write_text(json.dumps(missing), encoding="utf-8")
        require_failure(invoke(path), "missing file")

        bad_commit = json.loads(json.dumps(original))
        bad_commit["owner"]["commit"] = "0" * 40
        path = temp / "bad-commit.json"
        path.write_text(json.dumps(bad_commit), encoding="utf-8")
        require_failure(invoke(path), "owner commit mutation")

        bad_blob = json.loads(json.dumps(original))
        bad_blob["owner"]["git_blob"] = "0" * 40
        path = temp / "bad-blob.json"
        path.write_text(json.dumps(bad_blob), encoding="utf-8")
        require_failure(invoke(path), "owner blob mutation")

    print("A2_K2_PROVENANCE_MUTATION_PASS cases=5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
