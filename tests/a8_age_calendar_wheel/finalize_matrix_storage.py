#!/usr/bin/env python3
"""Checksum A8 matrix outputs and gzip only reproducible raw event CSV files."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, required=True)
    args = parser.parse_args()

    summary = args.result_root / "matrix-summary.csv"
    if not summary.is_file():
        parser.error("matrix-summary.csv must exist before storage finalization")

    result_dirs = sorted(
        path for path in args.result_root.iterdir()
        if path.is_dir() and "-n" in path.name
        and path.name.split("-n")[0] in {"rr", "exact", "b1", "b2", "b4", "b8"}
        and path.name.split("-n")[-1].isdigit()
    )
    if len(result_dirs) != 18:
        parser.error(f"expected 18 result directories, found {len(result_dirs)}")

    manifest: dict[str, object] = {
        "schema_version": 1,
        "summary_sha256": sha256(summary),
        "raw_event_policy": "gzip after aggregate/event-run/timing summary validation",
        "files": [],
    }
    files: list[dict[str, object]] = []
    raw_events: list[Path] = []
    for result_dir in result_dirs:
        required = (result_dir / "aggregate.csv", result_dir / "event-aggregate.csv",
                    result_dir / "timing-pair.json")
        if any(not path.is_file() for path in required):
            parser.error(f"missing summary output in {result_dir}")
        raw = sorted(result_dir.glob("*.events.csv"))
        if len(raw) != 11:
            parser.error(f"expected 11 raw event CSVs in {result_dir}, found {len(raw)}")
        raw_events.extend(raw)
        checksum_files = set(required) | set(raw) | set(result_dir.glob("*.csv"))
        for path in sorted(checksum_files):
            if path.name.endswith(".events.csv") or path in required or path.name == "event-aggregate.csv":
                files.append({
                    "path": str(path.relative_to(args.result_root)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                })
    manifest["files"] = files
    checksum_path = args.result_root / "result-checksums.json"
    checksum_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for source in raw_events:
        destination = source.with_suffix(source.suffix + ".gz")
        with source.open("rb") as input_handle, gzip.open(destination, "wb", compresslevel=6) as output_handle:
            shutil.copyfileobj(input_handle, output_handle)
        digest = hashlib.sha256()
        with gzip.open(destination, "rb") as check_handle:
            for chunk in iter(lambda: check_handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != sha256(source):
            raise RuntimeError(f"gzip verification failed for {source}")
        source.unlink()

    print(
        f"A8_STORAGE_FINALIZE_PASS result_dirs={len(result_dirs)} "
        f"raw_events_gzipped={len(raw_events)} checksum={checksum_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
