"""Vendored W5-only process, hashing, git-object, and netlist helpers."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any


EXPECTED_YOSYS_SHA256 = "30aa795bec7533dac08bad56309edb6ac70dd33f017c28082d3c1dae1012112f"
EXPECTED_ABC_SHA256 = "21869d0f63b6a2962ad7e54044e7a694f6cc392db6443ad7bf70cdb8ad6ca16a"
EXPECTED_TCL_SHA256 = "6dfbe2faf2a776485be94cb87bed369337bcf9236ee4c955e45004f8253ade8a"
EXPECTED_YOSYS_VERSION = (
    "Yosys 0.52 (git sha1 fee39a3284c90249e1d9684cf6944ffbbcbb8f90)"
)
EXPECTED_ABC_VERSION = "UC Berkeley, ABC 1.01 (compiled May  4 2025 16:37:33)"


class AuditError(RuntimeError):
    """A fail-closed provenance, execution, or structural-analysis error."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_command(
    argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    if result.returncode:
        raise AuditError(
            f"command failed ({result.returncode}): {' '.join(argv)}\n"
            f"[last 16000 characters]\n{result.stdout[-16000:]}"
        )
    return result


def git_object(repo: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AuditError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def verify_tool(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AuditError(f"{label} is absent or not executable: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise AuditError(
            f"{label} SHA256 mismatch: expected {expected_sha256}, got {actual}"
        )


def numeric_bits(bits: list[Any]) -> list[int]:
    return [bit for bit in bits if isinstance(bit, int)]
