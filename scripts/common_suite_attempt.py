#!/usr/bin/env python3
"""Create a private, non-destructive common-suite attempt namespace."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import sys
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def create(root: Path, suite: str, candidate: str) -> Path:
    for label, value in (("suite", suite), ("candidate", candidate)):
        if not SAFE.fullmatch(value):
            raise ValueError(f"{label} is not a safe path component")
    attempts = root / "attempts" / suite / candidate
    attempts.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    for _ in range(32):
        attempt = attempts / f"{timestamp}-p{os.getpid()}-{secrets.token_hex(6)}"
        try:
            attempt.mkdir(mode=0o700)
            (attempt / "runs").mkdir(mode=0o700)
            metadata = {
                "schema_version": 1, "suite": suite, "candidate": candidate,
                "attempt_id": attempt.name, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            descriptor = os.open(attempt / "attempt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(metadata, stream, indent=2, sort_keys=True); stream.write("\n")
                stream.flush(); os.fsync(stream.fileno())
            directory_fd = os.open(attempt, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            return attempt
        except FileExistsError:
            continue
    raise RuntimeError("could not allocate a unique attempt namespace")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        path = create(args.root, args.suite, args.candidate)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    print(path.resolve()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
