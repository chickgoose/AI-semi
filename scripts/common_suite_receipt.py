#!/usr/bin/env python3
"""Publish a fail-closed receipt for one immutable full50/capacity22 attempt."""

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

SCHEMA_VERSION = 3
SIDECAR_SCHEMA_VERSION = 1
ANALYZER_WORKLOADS = {
    "pairwise_contention", "mixed_phase_always_ready",
    "phase_transition", "timing_pair",
}
PHASE_NAMES = ["sparse", "near_saturation", "overload", "post_sparse", "drain"]


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


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes, os.stat_result]:
    payload, info = _read_bytes_stable(path, label)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptError(f"{label} must be a JSON object: {path}")
    return value, payload, info


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiptError(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, label: str) -> str:
    value = _string(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
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


def _claim_inode(info: os.stat_result, path: Path, label: str,
                 inodes: dict[tuple[int, int], Path]) -> None:
    key = (info.st_dev, info.st_ino)
    if info.st_nlink != 1:
        raise ReceiptError(f"{label} uses a hard-linked inode: {path}")
    if key in inodes:
        raise ReceiptError(f"{label} reuses inode already claimed by {inodes[key]}")
    inodes[key] = path


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
        "name": row["name"], "workload": row["workload"], "seed": row["seed"],
        "geometry": row["geometry"], "load": str(Decimal(str(row["load"]))),
        "stim_cycles": row["stim_cycles"], "parameters": row.get("parameters", {}),
        "sink": row.get("sink", {"mode": "always"}),
    }


def _report_group(config: dict[str, Any]) -> str:
    if config["workload"] == "uniform":
        return "uniform"
    if config["workload"] == "mixed_phase_always_ready":
        return "mixed_phase_always_ready"
    return re.sub(r"_s[0-9]+$", "", config["name"])


def _artifact(root: Path, spec: Any, marker: os.stat_result, label: str,
              inodes: dict[tuple[int, int], Path]):
    if not isinstance(spec, dict) or set(spec) != {"path", "sha256"}:
        raise ReceiptError(f"{label} must contain exactly path and sha256")
    path = _contained(root, spec["path"], f"{label}.path")
    payload, info = _read_bytes_stable(path, label)
    _claim_inode(info, path, label, inodes)
    if not payload:
        raise ReceiptError(f"{label} is empty")
    if info.st_mtime_ns <= marker.st_mtime_ns:
        raise ReceiptError(f"{label} is not newer than its freshness marker")
    digest = _sha256(payload)
    if digest != _sha(spec["sha256"], f"{label}.sha256"):
        raise ReceiptError(f"{label} SHA256 mismatch")
    return path, payload, info, digest


def _csv_provenance(payload: bytes, metadata: dict[str, Any], name: str,
                    candidate: str) -> tuple[str, str, str]:
    try:
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ReceiptError(f"run {name} result is not valid CSV: {exc}") from exc
    required = {"candidate", "test", "seed"}
    if not rows or not required.issubset(rows[0]):
        raise ReceiptError(f"run {name} result lacks candidate/test/seed rows")
    triples = {(row.get("candidate", ""), row.get("test", ""), row.get("seed", "")) for row in rows}
    expected = (candidate, str(metadata["report_group"]), str(metadata["run"]["seed"]))
    if len(triples) != 1 or next(iter(triples)) != expected:
        raise ReceiptError(f"run {name} result candidate/test/seed provenance mismatch")
    return expected


def _analyzer_provenance(doc: dict[str, Any], metadata: dict[str, Any], csv_key,
                         name: str) -> None:
    common = (doc.get("candidate"), doc.get("test"), str(doc.get("seed", "")))
    if common != csv_key or doc.get("trace_sha256") != metadata["trace_sha256"]:
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
        provenance, classification = doc.get("provenance_validation"), doc.get("classification")
        if (doc.get("schema_version") != 1 or doc.get("event_identity_mode") != "address_only" or
                doc.get("sink_mode") != "always" or not isinstance(provenance, dict) or
                provenance.get("status") != "pass" or any(provenance.get(key) is not True for key in (
                    "trace_sha256", "phase_boundaries", "address_only_identity",
                    "source_local_order", "complete_uncensored_event_accounting"))):
            raise ReceiptError(f"run {name} mixed analyzer provenance did not pass")
        if (not isinstance(classification, dict) or
                classification.get("correctness_status") != "qualified_pass" or
                classification.get("analysis_status") not in {"pass", "capacity_loss"}):
            raise ReceiptError(f"run {name} mixed analyzer correctness is not qualified")
    elif workload == "phase_transition":
        phases = doc.get("phases")
        if (doc.get("recovery_censored") is not False or
                not isinstance(doc.get("recovery_to_zero_cycles"), int) or
                not isinstance(phases, list) or
                [row.get("phase") for row in phases if isinstance(row, dict)] != PHASE_NAMES or
                any(not isinstance(row, dict) or not {"generated", "source_overrun", "accepted",
                    "delivered_by_occurrence_phase", "delivered_in_phase_window", "backlog_peak",
                    "backlog_at_end"}.issubset(row) for row in phases)):
            raise ReceiptError(f"run {name} phase-transition analyzer schema is incomplete")
    elif workload == "timing_pair":
        values = [doc.get(key) for key in ("pair_count", "evaluable_pairs", "dropped_pairs", "censored_pairs")]
        if (any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values) or
                values[0] != values[1] + values[2] + values[3] or values[3] != 0):
            raise ReceiptError(f"run {name} timing-pair analyzer schema/accounting is incomplete")


def _load_attempt(artifact_root: Path, artifacts: dict[str, Any], suite: str,
                  inodes: dict[tuple[int, int], Path]):
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise ReceiptError("artifact root must be a real attempt directory")
    spec = artifacts.get("attempt")
    if not isinstance(spec, dict) or set(spec) != {"path", "sha256"} or spec["path"] != "attempt.json":
        raise ReceiptError("artifact manifest must bind attempt.json path and SHA256")
    path = _contained(artifact_root, spec["path"], "attempt.path")
    doc, payload, info = _read_json(path, "attempt manifest")
    _claim_inode(info, path, "attempt manifest", inodes)
    if _sha256(payload) != _sha(spec["sha256"], "attempt.sha256"):
        raise ReceiptError("attempt manifest SHA256 mismatch")
    required = {"schema_version", "suite", "candidate", "attempt_id", "created_at_utc",
                "candidate_manifest", "tools"}
    if set(doc) != required or doc["schema_version"] != 2 or doc["suite"] != suite:
        raise ReceiptError("attempt manifest schema/suite mismatch")
    candidate = _string(doc["candidate"], "attempt candidate")
    attempt_id = _string(doc["attempt_id"], "attempt_id")
    resolved = artifact_root.resolve()
    if (resolved.name != attempt_id or resolved.parent.name != candidate or
            resolved.parent.parent.name != suite or resolved.parent.parent.parent.name != "attempts"):
        raise ReceiptError("artifact root is not the declared unique attempt namespace")

    candidate_spec = doc["candidate_manifest"]
    if not isinstance(candidate_spec, dict) or set(candidate_spec) != {"path", "sha256"}:
        raise ReceiptError("attempt candidate_manifest schema mismatch")
    candidate_path = _contained(artifact_root, candidate_spec["path"], "candidate manifest path")
    candidate_doc, candidate_bytes, candidate_info = _read_json(candidate_path, "candidate manifest")
    _claim_inode(candidate_info, candidate_path, "candidate manifest", inodes)
    candidate_sha = _sha(candidate_spec["sha256"], "candidate manifest sha256")
    if (_sha256(candidate_bytes) != candidate_sha or candidate_doc.get("schema_version") != 1 or
            candidate_doc.get("candidate") != candidate):
        raise ReceiptError("candidate manifest identity mismatch")

    tools_doc = doc["tools"]
    if not isinstance(tools_doc, dict) or "runner" not in tools_doc:
        raise ReceiptError("attempt tool identities must include runner")
    tools = {}
    for key, tool_spec in tools_doc.items():
        if (not isinstance(tool_spec, dict) or set(tool_spec) != {"identity", "path", "sha256"} or
                tool_spec.get("identity") != key):
            raise ReceiptError(f"attempt tool identity schema mismatch for {key}")
        tool_path = _contained(artifact_root, tool_spec["path"], f"tool {key} path")
        tool_bytes, tool_info = _read_bytes_stable(tool_path, f"tool {key}")
        _claim_inode(tool_info, tool_path, f"tool {key}", inodes)
        tool_sha = _sha(tool_spec["sha256"], f"tool {key} sha256")
        if not tool_bytes or _sha256(tool_bytes) != tool_sha:
            raise ReceiptError(f"tool {key} identity mismatch")
        tools[key] = {"identity": key, "sha256": tool_sha}
    return doc, payload, candidate, candidate_sha, tools


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
    frozen, inodes = suites[suite], {}
    names = tuple(frozen["names"])

    manifest, manifest_bytes, _ = _read_json(suite_manifest_path, "official suite manifest")
    if suite_manifest_path.name != frozen["manifest_name"] or _sha256(manifest_bytes) != frozen["manifest_sha256"]:
        raise ReceiptError("official suite manifest filename or byte SHA256 mismatch")
    if manifest.get("schema_version") != 1:
        raise ReceiptError("official suite manifest schema_version must be 1")
    manifest_runs = _named(manifest.get("runs"), "official suite manifest.runs")
    _exact(manifest_runs, names, "official suite run set")

    index, index_bytes, _ = _read_json(generation_index_path, "generation index")
    if set(index) != {"schema_version", "generator_version", "input_manifest", "runs"}:
        raise ReceiptError("generation index schema has missing or extra top-level fields")
    if (index["schema_version"] != 1 or index["generator_version"] != generator_version or
            index["input_manifest"] != frozen["manifest_name"]):
        raise ReceiptError("generation index schema/provenance mismatch")
    indexed = _named(index["runs"], "generation index.runs", embedded=True)
    _exact(indexed, names, "generation index run set")

    if artifacts_path.parent.resolve() != artifact_root.resolve():
        raise ReceiptError("artifact manifest must reside in the attempt root")
    artifacts, artifacts_bytes, artifacts_info = _read_json(artifacts_path, "artifact manifest")
    _claim_inode(artifacts_info, artifacts_path, "artifact manifest", inodes)
    if (set(artifacts) != {"schema_version", "suite", "candidate", "attempt", "runs"} or
            artifacts["schema_version"] != SCHEMA_VERSION or artifacts["suite"] != suite):
        raise ReceiptError("artifact manifest schema_version/suite mismatch")
    attempt_doc, attempt_bytes, candidate, candidate_manifest_sha, tools = _load_attempt(
        artifact_root, artifacts, suite, inodes)
    if artifacts["candidate"] != candidate:
        raise ReceiptError("artifact and attempt candidate mismatch")
    artifact_runs = _named(artifacts["runs"], "artifact manifest.runs")
    _exact(artifact_runs, names, "artifact manifest run set")

    required_tool_names = {"runner"} | {indexed[name]["run"]["workload"] for name in names
                                        if indexed[name]["run"]["workload"] in ANALYZER_WORKLOADS}
    if not required_tool_names.issubset(tools):
        raise ReceiptError(f"attempt is missing tool identities: {sorted(required_tool_names - set(tools))}")

    trace_root, receipt_runs, result_shas = generation_index_path.parent, [], set()
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
        run_manifest_path = _contained(trace_root, f"{name}.manifest.json", f"run {name} manifest")
        run_manifest, run_manifest_bytes, _ = _read_json(run_manifest_path, f"run {name} manifest")
        canonical_bytes = (json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
        if run_manifest != metadata or run_manifest_bytes != canonical_bytes:
            raise ReceiptError(f"run {name} manifest bytes/content differ from generation index")
        run_manifest_sha = _sha256(run_manifest_bytes)
        trace_path = _contained(trace_root, metadata["trace_file"], f"run {name} trace")
        trace_bytes, trace_info = _read_bytes_stable(trace_path, f"run {name} trace")
        if _sha256(trace_bytes) != expected_trace:
            raise ReceiptError(f"run {name} trace SHA256 mismatch")

        row = artifact_runs[name]
        expected_row_keys = {"name", "freshness_marker", "result", "execution_sidecar"}
        if metadata["run"]["workload"] in ANALYZER_WORKLOADS:
            expected_row_keys.add("analyzer")
        if set(row) != expected_row_keys:
            raise ReceiptError(f"run {name} artifact row schema mismatch")
        marker_path = _contained(artifact_root, row.get("freshness_marker"), f"run {name} marker")
        marker_bytes, marker_info = _read_bytes_stable(marker_path, f"run {name} marker")
        _claim_inode(marker_info, marker_path, f"run {name} marker", inodes)
        if marker_bytes:
            raise ReceiptError(f"run {name} freshness marker must be empty")
        result_path, result_bytes, result_info, result_sha = _artifact(
            artifact_root, row.get("result"), marker_info, f"run {name} result", inodes)
        if result_sha in result_shas:
            raise ReceiptError(f"run {name} reuses a result SHA256")
        result_shas.add(result_sha)
        csv_key = _csv_provenance(result_bytes, metadata, name, candidate)

        workload, analyzer_entry, analyzer_sha, analyzer_info = metadata["run"]["workload"], None, None, None
        if workload in ANALYZER_WORKLOADS:
            analyzer_path, analyzer_bytes, analyzer_info, analyzer_sha = _artifact(
                artifact_root, row.get("analyzer"), marker_info, f"run {name} analyzer", inodes)
            try:
                analyzer_doc = json.loads(analyzer_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReceiptError(f"run {name} analyzer is invalid JSON: {exc}") from exc
            if not isinstance(analyzer_doc, dict):
                raise ReceiptError(f"run {name} analyzer must be an object")
            _analyzer_provenance(analyzer_doc, metadata, csv_key, name)
            analyzer_entry = {"path": str(analyzer_path), "sha256": analyzer_sha,
                              "size_bytes": analyzer_info.st_size, "mtime_ns": analyzer_info.st_mtime_ns}
        elif "analyzer" in row:
            raise ReceiptError(f"run {name} must not declare an analyzer")

        sidecar_path, sidecar_bytes, sidecar_info, sidecar_sha = _artifact(
            artifact_root, row.get("execution_sidecar"), marker_info,
            f"run {name} execution sidecar", inodes)
        if sidecar_info.st_mtime_ns <= max(result_info.st_mtime_ns,
                                           analyzer_info.st_mtime_ns if analyzer_info else 0):
            raise ReceiptError(f"run {name} execution sidecar predates bound outputs")
        try:
            sidecar = json.loads(sidecar_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReceiptError(f"run {name} execution sidecar is invalid JSON: {exc}") from exc
        bound_tools = {"runner": tools["runner"]}
        if workload in ANALYZER_WORKLOADS:
            bound_tools[workload] = tools[workload]
        expected_sidecar = {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "suite": suite, "attempt_id": attempt_doc["attempt_id"], "candidate": candidate,
            "run_name": name, "trace_sha256": expected_trace,
            "run_manifest_sha256": run_manifest_sha,
            "candidate_manifest_sha256": candidate_manifest_sha,
            "tools": bound_tools, "result_sha256": result_sha,
            "analyzer_sha256": analyzer_sha,
        }
        if sidecar != expected_sidecar:
            raise ReceiptError(f"run {name} execution sidecar binding mismatch")

        receipt_row = {
            "name": name, "workload": workload,
            "run_manifest": {"path": str(run_manifest_path), "sha256": run_manifest_sha},
            "trace": {"path": str(trace_path), "sha256": expected_trace, "size_bytes": trace_info.st_size},
            "freshness_marker": {"path": str(marker_path), "mtime_ns": marker_info.st_mtime_ns},
            "result": {"path": str(result_path), "sha256": result_sha,
                       "size_bytes": result_info.st_size, "mtime_ns": result_info.st_mtime_ns},
            "execution_sidecar": {"path": str(sidecar_path), "sha256": sidecar_sha,
                                  "size_bytes": sidecar_info.st_size, "mtime_ns": sidecar_info.st_mtime_ns},
        }
        if analyzer_entry is not None:
            receipt_row["analyzer"] = analyzer_entry
        receipt_runs.append(receipt_row)

    return {
        "receipt_schema_version": SCHEMA_VERSION, "status": "PASS", "suite": suite,
        "candidate": candidate, "validated_run_count": len(names),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "official_source_commit": official.SOURCE_COMMIT,
        "attempt": {"path": str((artifact_root / "attempt.json").resolve()),
                    "sha256": _sha256(attempt_bytes), "attempt_id": attempt_doc["attempt_id"]},
        "candidate_manifest_sha256": candidate_manifest_sha, "tools": tools,
        "inputs": {
            "official_manifest": {"path": str(suite_manifest_path.resolve()), "sha256": _sha256(manifest_bytes)},
            "generation_index": {"path": str(generation_index_path.resolve()), "sha256": _sha256(index_bytes)},
            "artifact_manifest": {"path": str(artifacts_path.resolve()), "sha256": _sha256(artifacts_bytes)},
        }, "runs": receipt_runs,
    }


def publish_new_atomic(path: Path, payload: bytes) -> None:
    """No-overwrite publish; exit 0 means file and directory fsync completed."""
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
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


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
        if args.output.parent.resolve() != args.artifact_root.resolve():
            raise ReceiptError("receipt output must reside in the attempt root")
        result = validate(args.generation_index, args.official_manifest, args.suite,
                          args.artifacts, args.artifact_root)
        publish_new_atomic(args.output, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    except ReceiptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"PASS receipt={args.output} runs={result['validated_run_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
