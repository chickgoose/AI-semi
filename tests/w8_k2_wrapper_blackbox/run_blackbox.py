#!/usr/bin/env python3
"""Materialize pinned A2/A3 RTL and run A8-owned Verilator black-box tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
PINS = HERE / "owner_pins.json"
VERILATOR_FALLBACK = Path("/tmp/a7-sim-bin/verilator")
MUTANTS = {
    1: "GLOBAL_ORDER",
    2: "DUPLICATE_SOURCE",
    3: "ACK_BIJECTION",
    4: "PREMATURE_DRAIN",
    5: "GLOBAL_ORDER",
    6: "RESET_PHANTOM",
}


class AuditError(RuntimeError):
    pass


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def run(command: list[str], cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False, timeout=timeout)


def git_blob(repo: Path, commit: str, logical: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{logical}"], cwd=repo,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AuditError(
            f"cannot materialize {commit}:{logical}: "
            + result.stderr.decode(errors="replace")
        )
    return result.stdout


def stable_read(path: Path) -> bytes:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise AuditError(f"worktree wrapper is not a regular file: {path}")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        payload = stream.read()
        after_read = os.fstat(stream.fileno())
    after = path.lstat()
    identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
    if not (identity(before) == identity(opened) == identity(after_read) == identity(after)):
        raise AuditError(f"worktree wrapper changed while being read: {path}")
    return payload


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def source_payload(owner: dict, row: dict) -> bytes:
    repo = Path(owner["repo"])
    if row.get("source", "git_blob") == "git_blob":
        payload = git_blob(repo, owner["commit"], row["path"])
    else:
        path = repo / row["path"]
        payload = stable_read(path)
    if sha(payload) != row["sha256"]:
        raise AuditError(f"owner source SHA mismatch: {row['path']}")
    return payload


def compile_owner(verilator: Path, name: str, owner: dict, root: Path) -> Path:
    materialized = []
    for row in owner["sources"]:
        destination = root / "sources" / name / Path(row["path"]).name
        write_new(destination, source_payload(owner, row))
        materialized.append(destination)
    obj = root / f"obj-{name}"
    command = [
        str(verilator), "--binary", "--timing", "-Wall", "-Wno-fatal",
        "-Wno-BLKSEQ", "-Wno-SYNCASYNCNET", "-Wno-UNUSEDSIGNAL",
        "-Wno-UNOPTFLAT", "--top-module", "a8_k2_blackbox_tb",
        "--Mdir", str(obj), "-o", "a8_k2_blackbox",
        f"-DA8_OWNER_{name.upper()}", *map(str, materialized),
        str(HERE / "a8_k2_blackbox_adapter.sv"),
        str(HERE / "a8_k2_blackbox_tb.sv"),
    ]
    completed = run(command, HERE)
    write_new(root / f"compile-{name}.log", completed.stdout.encode())
    if completed.returncode:
        raise AuditError(f"{name} compile failed:\n{completed.stdout[-4000:]}")
    return obj / "a8_k2_blackbox"


def main() -> int:
    pins = json.loads(PINS.read_text(encoding="utf-8"))
    if pins.get("schema_version") != 1 or set(pins.get("owners", {})) != {"a2", "a3"}:
        raise AuditError("owner pin registry schema mismatch")
    selected = shutil.which("verilator")
    verilator = Path(selected) if selected else VERILATOR_FALLBACK
    if not verilator.is_file() or not os.access(verilator, os.X_OK):
        raise AuditError("Verilator unavailable")
    resolved_verilator = verilator.resolve()
    tool_payload = stable_read(resolved_verilator)
    version = run([str(verilator), "--version"], HERE)
    if version.returncode or not version.stdout.strip():
        raise AuditError("cannot capture Verilator identity")
    print(f"A8_K2_TOOL_BOUND path={resolved_verilator} sha256={sha(tool_payload)} "
          f"version={version.stdout.strip()}")
    root = Path(tempfile.mkdtemp(prefix="a8-k2-wrapper-blackbox.", dir="/tmp"))
    try:
        for name, owner in pins["owners"].items():
            binary = compile_owner(verilator, name, owner, root)
            baseline = run([str(binary), "+MUTATION=0"], HERE)
            if baseline.returncode or "A8_K2_BLACKBOX_PASS mutation=0" not in baseline.stdout:
                raise AuditError(f"{name} baseline failed:\n{baseline.stdout[-4000:]}")
            print(f"A8_K2_OWNER_BASELINE_PASS owner={name}")
            for mode, diagnostic in MUTANTS.items():
                result = run([str(binary), f"+MUTATION={mode}"], HERE)
                marker = f"A8_K2_BLACKBOX_FAIL diagnostic={diagnostic}"
                if result.returncode == 0 or result.stdout.count(marker) != 1 or \
                        "A8_K2_BLACKBOX_PASS" in result.stdout:
                    raise AuditError(
                        f"{name} mutant escaped mode={mode} expected={diagnostic}:\n"
                        + result.stdout[-4000:]
                    )
                print(f"A8_K2_OWNER_MUTANT_CAUGHT owner={name} mode={mode} diagnostic={diagnostic}")
        print("A8_K2_WRAPPER_BLACKBOX_PASS owners=2 mutants=12")
        return 0
    except (OSError, ValueError, AuditError, subprocess.TimeoutExpired) as exc:
        print(f"A8_K2_WRAPPER_BLACKBOX_FAIL error={exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
