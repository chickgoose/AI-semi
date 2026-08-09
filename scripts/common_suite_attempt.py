#!/usr/bin/env python3
"""Create an immutable, non-destructive common-suite attempt namespace."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _snapshot(path: Path, destination: Path) -> str:
    payload = path.read_bytes()
    if not payload:
        raise ValueError(f"provenance input is empty: {path}")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha(payload)


def create(root: Path, suite: str, candidate: str, candidate_manifest: Path,
           tools: dict[str, Path]) -> Path:
    for label, value in (("suite", suite), ("candidate", candidate)):
        if not SAFE.fullmatch(value):
            raise ValueError(f"{label} is not a safe path component")
    if "runner" not in tools:
        raise ValueError("tool identity must include runner")
    if any(not SAFE.fullmatch(name) for name in tools):
        raise ValueError("tool names must be safe path components")
    try:
        candidate_doc = json.loads(candidate_manifest.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read candidate manifest: {exc}") from exc
    if not isinstance(candidate_doc, dict) or candidate_doc.get("candidate") != candidate:
        raise ValueError("candidate manifest candidate does not match namespace")

    attempts = root / "attempts" / suite / candidate
    attempts.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    for _ in range(32):
        attempt = attempts / f"{timestamp}-p{os.getpid()}-{secrets.token_hex(6)}"
        try:
            attempt.mkdir(mode=0o700)
        except FileExistsError:
            continue
        (attempt / "runs").mkdir(mode=0o700)
        provenance = attempt / "provenance"
        tool_root = provenance / "tools"
        tool_root.mkdir(parents=True, mode=0o700)
        candidate_relative = Path("provenance/candidate.manifest.json")
        candidate_sha = _snapshot(candidate_manifest, attempt / candidate_relative)
        tool_rows = {}
        for name, path in sorted(tools.items()):
            relative = Path("provenance/tools") / f"{name}.snapshot"
            tool_rows[name] = {
                "identity": name,
                "path": str(relative),
                "sha256": _snapshot(path, attempt / relative),
            }
        metadata = {
            "schema_version": 2,
            "suite": suite,
            "candidate": candidate,
            "attempt_id": attempt.name,
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "candidate_manifest": {
                "path": str(candidate_relative), "sha256": candidate_sha,
            },
            "tools": tool_rows,
        }
        descriptor = os.open(attempt / "attempt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(metadata, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        for directory in (tool_root, provenance, attempt, attempts):
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return attempt
    raise RuntimeError("could not allocate a unique attempt namespace")


def _tool(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("tool must be NAME=PATH")
    return name, Path(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--tool", action="append", type=_tool, required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    tools = dict(args.tool)
    if len(tools) != len(args.tool):
        print("error: duplicate tool identity", file=sys.stderr)
        return 2
    try:
        path = create(args.root, args.suite, args.candidate, args.candidate_manifest, tools)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
