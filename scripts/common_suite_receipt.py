#!/usr/bin/env python3
"""Validate and atomically publish a fail-closed common-suite receipt.

The tool intentionally does not discover runs or artifacts.  Every expected run
and every produced artifact must be named by an immutable input manifest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROVENANCE_KEY = "_common_suite_provenance"


class ReceiptError(ValueError):
    """Raised when suite evidence is incomplete, stale, or inconsistent."""


def _read_bytes_stable(path: Path, description: str) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ReceiptError(f"{description} is not a regular non-symlink file: {path}")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            payload = stream.read()
            after_read = os.fstat(stream.fileno())
        after_path = path.lstat()
    except OSError as exc:
        raise ReceiptError(f"cannot read {description} {path}: {exc}") from exc

    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )
    if not (
        identity(before)
        == identity(opened)
        == identity(after_read)
        == identity(after_path)
    ):
        raise ReceiptError(f"{description} changed while being validated: {path}")
    return payload, after_path


def _read_json(path: Path, description: str) -> tuple[dict[str, Any], bytes]:
    payload, _ = _read_bytes_stable(path, description)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"invalid JSON in {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptError(f"{description} must be a JSON object: {path}")
    return value, payload


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_sha(value: Any, description: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReceiptError(f"{description} must be a lowercase SHA256 hex digest")
    return value


def _require_string(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiptError(f"{description} must be a non-empty string")
    return value


def _unique_by_name(rows: Any, description: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ReceiptError(f"{description} must be an array")
    result: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReceiptError(f"{description}[{position}] must be an object")
        name = _require_string(row.get("name"), f"{description}[{position}].name")
        if name in result:
            raise ReceiptError(f"duplicate run name in {description}: {name}")
        result[name] = row
    return result


def _index_by_name(rows: Any) -> dict[str, dict[str, Any]]:
    description = "generation index.runs"
    if not isinstance(rows, list):
        raise ReceiptError(f"{description} must be an array")
    result: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReceiptError(f"{description}[{position}] must be an object")
        run = row.get("run")
        if not isinstance(run, dict):
            raise ReceiptError(f"{description}[{position}].run must be an object")
        name = _require_string(run.get("name"), f"{description}[{position}].run.name")
        if name in result:
            raise ReceiptError(f"duplicate run name in {description}: {name}")
        result[name] = row
    return result


def _resolve_relative(root: Path, value: Any, description: str) -> Path:
    relative = Path(_require_string(value, description))
    if relative.is_absolute() or ".." in relative.parts:
        raise ReceiptError(f"{description} must be a contained relative path")
    root_resolved = root.resolve()
    path = root_resolved / relative
    resolved = path.resolve(strict=False)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ReceiptError(f"{description} escapes its root: {relative}")
    component = root_resolved
    for part in relative.parts:
        component = component / part
        try:
            if stat.S_ISLNK(component.lstat().st_mode):
                raise ReceiptError(f"{description} contains a symlink: {relative}")
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ReceiptError(f"cannot inspect {description} {relative}: {exc}") from exc
    return path


def _check_exact_names(
    actual: dict[str, dict[str, Any]],
    expected: dict[str, dict[str, Any]],
    description: str,
) -> None:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise ReceiptError(
            f"{description} run set mismatch; missing={missing}, extra={extra}"
        )


def _artifact(
    artifact_root: Path,
    spec: Any,
    marker_stat: os.stat_result,
    description: str,
) -> tuple[Path, bytes, os.stat_result, str]:
    if not isinstance(spec, dict):
        raise ReceiptError(f"{description} must be an object")
    path = _resolve_relative(artifact_root, spec.get("path"), f"{description}.path")
    expected_sha = _require_sha(spec.get("sha256"), f"{description}.sha256")
    payload, artifact_stat = _read_bytes_stable(path, description)
    if not payload:
        raise ReceiptError(f"{description} is empty: {path}")
    if artifact_stat.st_mtime_ns <= marker_stat.st_mtime_ns:
        raise ReceiptError(f"{description} is not newer than its freshness marker: {path}")
    actual_sha = _sha256(payload)
    if actual_sha != expected_sha:
        raise ReceiptError(
            f"{description} SHA256 mismatch: expected {expected_sha}, got {actual_sha}"
        )
    return path, payload, artifact_stat, actual_sha


def validate(
    generation_index_path: Path,
    expected_runs_path: Path,
    artifacts_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    index, index_bytes = _read_json(generation_index_path, "generation index")
    expected_doc, expected_bytes = _read_json(expected_runs_path, "expected runs")
    artifacts_doc, artifacts_bytes = _read_json(artifacts_path, "artifact manifest")

    for description, document in (
        ("expected runs", expected_doc),
        ("artifact manifest", artifacts_doc),
    ):
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ReceiptError(f"{description} schema_version must be {SCHEMA_VERSION}")

    suite_id = _require_string(expected_doc.get("suite_id"), "expected runs suite_id")
    if artifacts_doc.get("suite_id") != suite_id:
        raise ReceiptError("artifact manifest suite_id does not match expected runs")

    expected_count = expected_doc.get("expected_run_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 1:
        raise ReceiptError("expected_run_count must be a positive integer")

    expected = _unique_by_name(expected_doc.get("runs"), "expected runs.runs")
    indexed = _index_by_name(index.get("runs"))
    artifacts = _unique_by_name(artifacts_doc.get("runs"), "artifact manifest.runs")
    if len(expected) != expected_count:
        raise ReceiptError(
            f"expected_run_count is {expected_count}, but expected runs contains {len(expected)}"
        )
    if len(indexed) != expected_count:
        raise ReceiptError(
            f"generation index contains {len(indexed)} runs; expected {expected_count}"
        )
    if len(artifacts) != expected_count:
        raise ReceiptError(
            f"artifact manifest contains {len(artifacts)} runs; expected {expected_count}"
        )
    _check_exact_names(indexed, expected, "generation index")
    _check_exact_names(artifacts, expected, "artifact manifest")

    declared_artifact_paths: set[Path] = set()
    declared_markers: set[Path] = set()
    for name in sorted(expected):
        artifact_row = artifacts[name]
        marker_path = _resolve_relative(
            artifact_root,
            artifact_row.get("freshness_marker"),
            f"artifact run {name}.freshness_marker",
        )
        if marker_path in declared_markers:
            raise ReceiptError(f"duplicate freshness marker across runs: {marker_path}")
        declared_markers.add(marker_path)
        run_paths = []
        for kind in ("result", "analyzer"):
            spec = artifact_row.get(kind)
            if not isinstance(spec, dict):
                raise ReceiptError(f"artifact run {name}.{kind} must be an object")
            run_paths.append(
                _resolve_relative(
                    artifact_root,
                    spec.get("path"),
                    f"artifact run {name}.{kind}.path",
                )
            )
        if run_paths[0] == run_paths[1]:
            raise ReceiptError(f"result and analyzer paths are identical for run {name}")
        for artifact_path in run_paths:
            if artifact_path in declared_artifact_paths:
                raise ReceiptError(f"duplicate artifact path across runs: {artifact_path}")
            declared_artifact_paths.add(artifact_path)

    index_provenance = expected_doc.get("index_provenance", {})
    if not isinstance(index_provenance, dict):
        raise ReceiptError("index_provenance must be an object")
    for key, value in index_provenance.items():
        if index.get(key) != value:
            raise ReceiptError(f"generation index provenance mismatch for {key}")

    trace_root = generation_index_path.parent
    seen_trace_files: set[str] = set()
    seen_artifact_paths: set[Path] = set()
    seen_markers: set[Path] = set()
    receipt_runs: list[dict[str, Any]] = []

    for name in sorted(expected):
        expected_row = expected[name]
        index_row = indexed[name]
        expected_trace_file = _require_string(
            expected_row.get("trace_file"), f"expected run {name}.trace_file"
        )
        expected_trace_sha = _require_sha(
            expected_row.get("trace_sha256"), f"expected run {name}.trace_sha256"
        )
        if index_row.get("trace_file") != expected_trace_file:
            raise ReceiptError(f"trace filename mismatch for run {name}")
        if index_row.get("trace_sha256") != expected_trace_sha:
            raise ReceiptError(f"indexed trace SHA256 mismatch for run {name}")
        if expected_trace_file in seen_trace_files:
            raise ReceiptError(f"duplicate trace_file across runs: {expected_trace_file}")
        seen_trace_files.add(expected_trace_file)

        trace_path = _resolve_relative(trace_root, expected_trace_file, f"run {name} trace_file")
        trace_bytes, trace_stat = _read_bytes_stable(trace_path, f"run {name} trace")
        if _sha256(trace_bytes) != expected_trace_sha:
            raise ReceiptError(f"trace content SHA256 mismatch for run {name}")

        artifact_row = artifacts[name]
        marker_path = _resolve_relative(
            artifact_root,
            artifact_row.get("freshness_marker"),
            f"artifact run {name}.freshness_marker",
        )
        if marker_path in seen_markers:
            raise ReceiptError(f"duplicate freshness marker across runs: {marker_path}")
        seen_markers.add(marker_path)
        marker_payload, marker_stat = _read_bytes_stable(
            marker_path, f"run {name} freshness marker"
        )
        if marker_payload:
            raise ReceiptError(f"freshness marker must be empty: {marker_path}")

        result_path, result_bytes, result_stat, result_sha = _artifact(
            artifact_root, artifact_row.get("result"), marker_stat, f"run {name} result"
        )
        analyzer_path, analyzer_bytes, analyzer_stat, analyzer_sha = _artifact(
            artifact_root,
            artifact_row.get("analyzer"),
            marker_stat,
            f"run {name} analyzer",
        )
        for artifact_path in (result_path, analyzer_path):
            if artifact_path in seen_artifact_paths:
                raise ReceiptError(f"duplicate artifact path across runs: {artifact_path}")
            seen_artifact_paths.add(artifact_path)
        if result_path == analyzer_path:
            raise ReceiptError(f"result and analyzer paths are identical for run {name}")

        try:
            analyzer_doc = json.loads(analyzer_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReceiptError(f"run {name} analyzer is not valid JSON: {exc}") from exc
        if not isinstance(analyzer_doc, dict):
            raise ReceiptError(f"run {name} analyzer must be a JSON object")
        provenance = analyzer_doc.get(PROVENANCE_KEY)
        expected_provenance = {
            "schema_version": SCHEMA_VERSION,
            "run_name": name,
            "trace_sha256": expected_trace_sha,
            "result_sha256": result_sha,
        }
        if provenance != expected_provenance:
            raise ReceiptError(f"analyzer provenance mismatch for run {name}")

        receipt_runs.append(
            {
                "name": name,
                "trace": {
                    "path": str(trace_path),
                    "sha256": expected_trace_sha,
                    "size_bytes": trace_stat.st_size,
                },
                "freshness_marker": {
                    "path": str(marker_path),
                    "mtime_ns": marker_stat.st_mtime_ns,
                },
                "result": {
                    "path": str(result_path),
                    "sha256": result_sha,
                    "size_bytes": result_stat.st_size,
                    "mtime_ns": result_stat.st_mtime_ns,
                },
                "analyzer": {
                    "path": str(analyzer_path),
                    "sha256": analyzer_sha,
                    "size_bytes": analyzer_stat.st_size,
                    "mtime_ns": analyzer_stat.st_mtime_ns,
                },
            }
        )

    return {
        "receipt_schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "suite_id": suite_id,
        "validated_run_count": expected_count,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputs": {
            "generation_index": {
                "path": str(generation_index_path.resolve()),
                "sha256": _sha256(index_bytes),
            },
            "expected_runs": {
                "path": str(expected_runs_path.resolve()),
                "sha256": _sha256(expected_bytes),
            },
            "artifact_manifest": {
                "path": str(artifacts_path.resolve()),
                "sha256": _sha256(artifacts_bytes),
            },
        },
        "runs": receipt_runs,
    }


def publish_new_atomic(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir():
        raise ReceiptError(f"receipt output parent does not exist: {path.parent}")
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError as exc:
            raise ReceiptError(f"refusing to overwrite existing receipt: {path}") from exc
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ReceiptError(f"cannot atomically publish receipt {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-index", type=Path, required=True)
    parser.add_argument("--expected-runs", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        receipt = validate(
            args.generation_index,
            args.expected_runs,
            args.artifacts,
            args.artifact_root,
        )
        payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        publish_new_atomic(args.output, payload)
    except ReceiptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"PASS receipt={args.output} runs={receipt['validated_run_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
