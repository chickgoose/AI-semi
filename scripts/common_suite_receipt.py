#!/usr/bin/env python3
"""Fail-closed receipt for the committed full50 and capacity22 suites."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import common_suite_official as official

SCHEMA_VERSION = 2
ANALYZER_WORKLOADS = {"pairwise_contention", "mixed_phase_always_ready"}


class ReceiptError(ValueError):
    pass


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_bytes_stable(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ReceiptError(f"{label} is not a regular non-symlink file: {path}")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            payload = stream.read()
            after_read = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise ReceiptError(f"cannot read {label} {path}: {exc}") from exc
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if not (identity(before) == identity(opened) == identity(after_read) == identity(after)):
        raise ReceiptError(f"{label} changed while being validated: {path}")
    return payload, after


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    payload, _ = _read_bytes_stable(path, label)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptError(f"{label} must be a JSON object: {path}")
    return value, payload


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiptError(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    value = _string(value, label)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ReceiptError(f"{label} must be a lowercase SHA256 digest")
    return value


def _contained(root: Path, value: Any, label: str) -> Path:
    relative = Path(_string(value, label))
    if relative.is_absolute() or ".." in relative.parts:
        raise ReceiptError(f"{label} must be a contained relative path")
    root = root.resolve()
    path = root / relative
    if root not in path.resolve(strict=False).parents:
        raise ReceiptError(f"{label} escapes root")
    component = root
    for part in relative.parts:
        component /= part
        try:
            if stat.S_ISLNK(component.lstat().st_mode):
                raise ReceiptError(f"{label} contains a symlink: {relative}")
        except FileNotFoundError:
            break
    return path


def _named(rows: Any, label: str, *, embedded: bool = False) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ReceiptError(f"{label} must be an array")
    result = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReceiptError(f"{label}[{position}] must be an object")
        target = row.get("run") if embedded else row
        if not isinstance(target, dict):
            raise ReceiptError(f"{label}[{position}].run must be an object")
        name = _string(target.get("name"), f"{label}[{position}].name")
        if name in result:
            raise ReceiptError(f"duplicate run name in {label}: {name}")
        result[name] = row
    return result


def _exact(actual: dict[str, Any], names: tuple[str, ...], label: str) -> None:
    missing = sorted(set(names) - set(actual))
    extra = sorted(set(actual) - set(names))
    if missing or extra:
        raise ReceiptError(f"{label} mismatch; missing={missing}, extra={extra}")


def _canonical_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row["name"],
        "workload": row["workload"],
        "seed": row["seed"],
        "geometry": row["geometry"],
        "load": str(Decimal(str(row["load"]))),
        "stim_cycles": row["stim_cycles"],
        "parameters": row.get("parameters", {}),
        "sink": row.get("sink", {"mode": "always"}),
    }


def _report_group(config: dict[str, Any]) -> str:
    if config["workload"] == "uniform":
        return "uniform"
    if config["workload"] == "mixed_phase_always_ready":
        return "mixed_phase_always_ready"
    return re.sub(r"_s[0-9]+$", "", config["name"])


def _artifact(root: Path, spec: Any, marker: os.stat_result, label: str):
    if not isinstance(spec, dict):
        raise ReceiptError(f"{label} must be an object")
    path = _contained(root, spec.get("path"), f"{label}.path")
    payload, info = _read_bytes_stable(path, label)
    if not payload:
        raise ReceiptError(f"{label} is empty")
    if info.st_mtime_ns <= marker.st_mtime_ns:
        raise ReceiptError(f"{label} is not newer than its freshness marker")
    digest = _sha256(payload)
    if digest != _sha(spec.get("sha256"), f"{label}.sha256"):
        raise ReceiptError(f"{label} SHA256 mismatch")
    return path, payload, info, digest


def _csv_provenance(payload: bytes, metadata: dict[str, Any], name: str) -> tuple[str, str, str]:
    try:
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ReceiptError(f"run {name} result is not valid CSV: {exc}") from exc
    required = {"candidate", "test", "seed"}
    if not rows or not required.issubset(rows[0]):
        raise ReceiptError(f"run {name} result lacks candidate/test/seed rows")
    triples = {(r.get("candidate", ""), r.get("test", ""), r.get("seed", "")) for r in rows}
    expected = (next(iter(triples))[0], str(metadata["report_group"]), str(metadata["run"]["seed"]))
    if len(triples) != 1 or "" in next(iter(triples)) or next(iter(triples)) != expected:
        raise ReceiptError(f"run {name} result candidate/test/seed provenance mismatch")
    return next(iter(triples))


def _analyzer_provenance(doc: dict[str, Any], metadata: dict[str, Any], csv_key, name: str) -> None:
    expected_common = (csv_key[0], metadata["report_group"], str(metadata["run"]["seed"]))
    actual_common = (doc.get("candidate"), doc.get("test"), str(doc.get("seed", "")))
    if actual_common != expected_common or doc.get("trace_sha256") != metadata["trace_sha256"]:
        raise ReceiptError(f"run {name} analyzer provenance mismatch")
    workload = metadata["run"]["workload"]
    if workload == "pairwise_contention":
        if (doc.get("generator_version") != metadata["generator_version"] or
                doc.get("logical_source_permutation") != metadata["logical_source_permutation"]):
            raise ReceiptError(f"run {name} pairwise analyzer manifest provenance mismatch")
        if (doc.get("measurement_state") != "COMPLETE" or
                doc.get("evaluable_pairs") != doc.get("pair_count") or
                any(doc.get(key) != 0 for key in ("dropped_pairs", "censored_pairs", "nonevaluable_pairs"))):
            raise ReceiptError(f"run {name} pairwise analyzer is incomplete or censored")
    elif workload == "mixed_phase_always_ready":
        provenance = doc.get("provenance_validation")
        classification = doc.get("classification")
        if (doc.get("schema_version") != 1 or doc.get("event_identity_mode") != "address_only" or
                doc.get("sink_mode") != "always" or not isinstance(provenance, dict) or
                provenance.get("status") != "pass" or
                any(provenance.get(key) is not True for key in (
                    "trace_sha256", "phase_boundaries", "address_only_identity",
                    "source_local_order", "complete_uncensored_event_accounting"))):
            raise ReceiptError(f"run {name} mixed analyzer provenance did not pass")
        if (not isinstance(classification, dict) or
                classification.get("correctness_status") != "qualified_pass" or
                classification.get("analysis_status") not in {"pass", "capacity_loss"}):
            raise ReceiptError(f"run {name} mixed analyzer correctness is not qualified")


def validate(generation_index_path: Path, suite_manifest_path: Path, suite: str,
             artifacts_path: Path, artifact_root: Path,
             suites: dict[str, Any] | None = None,
             trace_hashes: dict[str, str] | None = None,
             generator_version: str | None = None) -> dict[str, Any]:
    suites = official.SUITES if suites is None else suites
    trace_hashes = official.TRACE_SHA256 if trace_hashes is None else trace_hashes
    generator_version = official.GENERATOR_VERSION if generator_version is None else generator_version
    if suite not in suites:
        raise ReceiptError(f"unknown official suite: {suite}")
    frozen = suites[suite]
    names = tuple(frozen["names"])

    manifest, manifest_bytes = _read_json(suite_manifest_path, "official suite manifest")
    if suite_manifest_path.name != frozen["manifest_name"] or _sha256(manifest_bytes) != frozen["manifest_sha256"]:
        raise ReceiptError("official suite manifest filename or byte SHA256 mismatch")
    if manifest.get("schema_version") != 1:
        raise ReceiptError("official suite manifest schema_version must be 1")
    manifest_runs = _named(manifest.get("runs"), "official suite manifest.runs")
    _exact(manifest_runs, names, "official suite run set")

    index, index_bytes = _read_json(generation_index_path, "generation index")
    if set(index) != {"schema_version", "generator_version", "input_manifest", "runs"}:
        raise ReceiptError("generation index schema has missing or extra top-level fields")
    if (index["schema_version"] != 1 or index["generator_version"] != generator_version or
            index["input_manifest"] != frozen["manifest_name"]):
        raise ReceiptError("generation index schema/provenance mismatch")
    indexed = _named(index["runs"], "generation index.runs", embedded=True)
    _exact(indexed, names, "generation index run set")

    artifacts, artifacts_bytes = _read_json(artifacts_path, "artifact manifest")
    if artifacts.get("schema_version") != SCHEMA_VERSION or artifacts.get("suite") != suite:
        raise ReceiptError(f"artifact manifest schema_version/suite mismatch")
    artifact_runs = _named(artifacts.get("runs"), "artifact manifest.runs")
    _exact(artifact_runs, names, "artifact manifest run set")

    trace_root = generation_index_path.parent
    used_paths: set[Path] = set()
    receipt_runs = []
    suite_candidate: str | None = None
    for name in names:
        metadata = indexed[name]
        if metadata.get("schema_version") != 1 or metadata.get("generator_version") != generator_version:
            raise ReceiptError(f"run {name} generated manifest schema mismatch")
        canonical_run = _canonical_run(manifest_runs[name])
        if metadata.get("run") != canonical_run:
            raise ReceiptError(f"run {name} embedded run config differs from official manifest")
        expected_trace = trace_hashes[name]
        if (metadata.get("trace_file") != f"{name}.events.jsonl" or
                metadata.get("trace_sha256") != expected_trace or
                metadata.get("report_group") != _report_group(canonical_run) or
                metadata.get("event_identity_mode") != "address_only" or
                metadata.get("dut_address_fields") != ["logical_source"] or
                metadata.get("dut_payload_fields") != []):
            raise ReceiptError(f"run {name} generated metadata contract mismatch")

        per_manifest_path = _contained(trace_root, f"{name}.manifest.json", f"run {name} manifest")
        per_manifest, per_manifest_bytes = _read_json(per_manifest_path, f"run {name} manifest")
        canonical_bytes = (json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
        if per_manifest != metadata or per_manifest_bytes != canonical_bytes:
            raise ReceiptError(f"run {name} manifest bytes/content differ from generation index")
        trace_path = _contained(trace_root, metadata["trace_file"], f"run {name} trace")
        trace_bytes, trace_stat = _read_bytes_stable(trace_path, f"run {name} trace")
        if _sha256(trace_bytes) != expected_trace:
            raise ReceiptError(f"run {name} trace SHA256 mismatch")

        row = artifact_runs[name]
        marker_path = _contained(artifact_root, row.get("freshness_marker"), f"run {name} marker")
        marker_bytes, marker_stat = _read_bytes_stable(marker_path, f"run {name} marker")
        if marker_bytes:
            raise ReceiptError(f"run {name} freshness marker must be empty")
        result_path, result_bytes, result_stat, result_sha = _artifact(
            artifact_root, row.get("result"), marker_stat, f"run {name} result")
        csv_key = _csv_provenance(result_bytes, metadata, name)
        if suite_candidate is None:
            suite_candidate = csv_key[0]
        elif csv_key[0] != suite_candidate:
            raise ReceiptError(f"run {name} candidate differs across suite")

        analyzer_entry = None
        workload = metadata["run"]["workload"]
        if workload in ANALYZER_WORKLOADS:
            analyzer_path, analyzer_bytes, analyzer_stat, analyzer_sha = _artifact(
                artifact_root, row.get("analyzer"), marker_stat, f"run {name} analyzer")
            try:
                analyzer_doc = json.loads(analyzer_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReceiptError(f"run {name} analyzer is invalid JSON: {exc}") from exc
            if not isinstance(analyzer_doc, dict):
                raise ReceiptError(f"run {name} analyzer must be an object")
            _analyzer_provenance(analyzer_doc, metadata, csv_key, name)
            analyzer_entry = {"path": str(analyzer_path), "sha256": analyzer_sha,
                              "size_bytes": analyzer_stat.st_size, "mtime_ns": analyzer_stat.st_mtime_ns}
            paths = (marker_path, result_path, analyzer_path)
        else:
            if "analyzer" in row:
                raise ReceiptError(f"run {name} must not declare an analyzer")
            paths = (marker_path, result_path)
        if any(path in used_paths for path in paths):
            raise ReceiptError(f"run {name} reuses an artifact or marker path")
        used_paths.update(paths)
        receipt_row = {
            "name": name, "workload": workload,
            "run_manifest": {"path": str(per_manifest_path), "sha256": _sha256(per_manifest_bytes)},
            "trace": {"path": str(trace_path), "sha256": expected_trace, "size_bytes": trace_stat.st_size},
            "freshness_marker": {"path": str(marker_path), "mtime_ns": marker_stat.st_mtime_ns},
            "result": {"path": str(result_path), "sha256": result_sha,
                       "size_bytes": result_stat.st_size, "mtime_ns": result_stat.st_mtime_ns},
        }
        if analyzer_entry is not None:
            receipt_row["analyzer"] = analyzer_entry
        receipt_runs.append(receipt_row)

    return {
        "receipt_schema_version": SCHEMA_VERSION, "status": "PASS", "suite": suite,
        "candidate": suite_candidate, "validated_run_count": len(names),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "official_source_commit": official.SOURCE_COMMIT,
        "inputs": {
            "official_manifest": {"path": str(suite_manifest_path.resolve()), "sha256": _sha256(manifest_bytes)},
            "generation_index": {"path": str(generation_index_path.resolve()), "sha256": _sha256(index_bytes)},
            "artifact_manifest": {"path": str(artifacts_path.resolve()), "sha256": _sha256(artifacts_bytes)},
        }, "runs": receipt_runs,
    }


def publish_new_atomic(path: Path, payload: bytes) -> None:
    """Publish without overwrite; exit success implies file and directory fsync passed.

    If the final directory fsync fails after link(2), the final name may exist even
    though this function raises. Callers must accept a receipt only after exit 0.
    """
    if not path.parent.is_dir():
        raise ReceiptError(f"receipt output parent does not exist: {path.parent}")
    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
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
        if temporary_name:
            try: Path(temporary_name).unlink()
            except FileNotFoundError: pass


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=tuple(official.SUITES), required=True)
    parser.add_argument("--official-manifest", type=Path, required=True)
    parser.add_argument("--generation-index", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = validate(args.generation_index, args.official_manifest, args.suite,
                          args.artifacts, args.artifact_root)
        publish_new_atomic(args.output, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    except ReceiptError as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    print(f"PASS receipt={args.output} runs={result['validated_run_count']}"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
