#!/usr/bin/env python3
"""Materialize SHA-bound A7 owner blobs into a caller-owned temporary tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(repo: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


def validate_hex(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a full SHA-256")
    int(value, 16)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.binding.read_text(encoding="utf-8"))
    if set(document) != {
        "schema_version", "owner_repo", "owner_commit", "production_top",
        "parallel_top", "contract", "sources",
    } or document["schema_version"] != 1:
        raise ValueError("binding schema/fields mismatch")
    repo = Path(document["owner_repo"])
    commit = document["owner_commit"]
    resolved = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "rev-parse", f"{commit}^{{commit}}"],
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    if resolved != commit:
        raise ValueError(f"owner commit is not exact: {resolved}")
    records = [document["contract"], *document["sources"]]
    if not isinstance(document["sources"], list) or len(document["sources"]) != 7:
        raise ValueError("expected exactly seven owner RTL sources")
    seen: set[str] = set()
    materialized: list[Path] = []
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ValueError(f"record {index} schema mismatch")
        relative = record["path"]
        if not isinstance(relative, str) or not relative or relative in seen:
            raise ValueError(f"record {index} path missing/duplicate")
        seen.add(relative)
        expected = validate_hex(record["sha256"], f"record {index}")
        blob = git_blob(repo, commit, relative)
        if digest(blob) != expected:
            raise ValueError(f"blob SHA mismatch: {relative}")
        destination = args.output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blob)
        if relative.endswith(".sv"):
            materialized.append(destination)
    source_list = args.output_dir / "bound_sources.list"
    source_list.write_text("".join(f"{path}\n" for path in materialized), encoding="utf-8")
    print(f"W5_A8_OWNER_SNAPSHOT_BOUND commit={commit} sources={len(materialized)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
