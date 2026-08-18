#!/usr/bin/env python3
"""Fail-closed A2/A3 single-edge full50 campaign/evidence wrapper."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys
from types import ModuleType
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("campaign.json")
EXPECTED_SCHEMA_ID = "redred_single_edge_replay_receipt_v1"
EXPECTED_RECEIPT_SCHEMA = "a23_full_single_edge_replay_receipt_v1"
EXPECTED_CAMPAIGN_ID = "redred-a2-a3-single-edge-full50-v1"
EXPECTED_EVIDENCE_CLASS = "A23_FULL_SINGLE_EDGE_REPLAY_ACTUAL_RTL_V1"
EXPECTED_PRODUCER_ID = "a23_full_single_edge_replay"
EXPECTED_PRODUCER_PATH = "tests/a23_full_single_edge_replay"
EXPECTED_RTL_COMMIT = "4ce4836fab1309d3468db8e660d2da9af371f784"
EXPECTED_CANDIDATES = ("A2", "A3")
EXPECTED_ROLES = {"A2": "PRIMARY", "A3": "EXACT_PREFIX_FALLBACK"}
EXPECTED_ENDPOINTS = {
    "A2": "A2_SINGLE_EDGE_COMPLETE_ENDPOINT",
    "A3": "A3_SINGLE_EDGE_COMPLETE_ENDPOINT",
}
EXPECTED_RTL_SOURCES = {
    "A2": [
        "rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv",
        "rtl/technology/single_edge/w2_single_edge_pair_tx.sv",
        "rtl/technology/single_edge/w2_single_edge_pair_rx.sv",
        "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv",
        "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv",
    ],
    "A3": [
        "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv",
        "rtl/technology/single_edge/w2_single_edge_pair_tx.sv",
        "rtl/technology/single_edge/w2_single_edge_pair_rx.sv",
        "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv",
        "rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge_top.sv",
    ],
}
EXPECTED_CYCLE_SEMANTICS = (
    "common_TB_one_entry_occurrence_latch_before_indexed_accept_edge_"
    "nonblocking_clear"
)
EXPECTED_HARD_ERRORS = {
    "phantom", "duplicate", "corrupt", "reorder", "accepted_missing",
    "partial_retirement", "illegal_output", "drain_timeout", "reset_escape",
    "protocol_error",
}
ARTIFACT_KEYS = {"path", "sha256", "size_bytes"}
LATENCY_KEYS = {"count", "mean", "p50", "p95", "p99", "max"}
EVENT_KEYS = {
    "tb_only_event_id", "logical_source", "occurrence_cycle", "accept_cycle",
    "retire_cycle", "retired_logical_source", "accept_order", "retire_order",
    "event_state",
}
FORBIDDEN_PATH_MARKERS = (
    "a23_full_p6_replay", "parallel_event", "parallel-result", "parallel_result",
)


class CampaignError(RuntimeError):
    """Claimed evidence is malformed, inconsistent, missing, or tampered."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def object_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must be an object")
    if set(value) != keys:
        raise CampaignError(
            f"{label} keys differ: missing={sorted(keys - set(value))} "
            f"extra={sorted(set(value) - keys)}"
        )
    return value


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignError(f"{label} must be a nonempty string")
    return value


def sha_string(value: Any, label: str) -> str:
    digest = nonempty(value, label)
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise CampaignError(f"{label} must be lowercase SHA-256")
    return digest


def counter(value: Any, label: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        kind = "positive" if positive else "nonnegative"
        raise CampaignError(f"{label} must be a {kind} integer (bool is forbidden)")
    return value


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CampaignError(f"{label} must be a finite number (bool is forbidden)")
    number = float(value)
    if not math.isfinite(number):
        raise CampaignError(f"{label} must be finite")
    return number


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must contain a JSON object")
    return value


def checked_external_file(path: Path, expected_sha: str, label: str) -> tuple[Path, str]:
    digest = sha_string(expected_sha, f"{label} expected SHA-256")
    if path.is_symlink() or not path.is_file():
        raise CampaignError(f"{label} is missing, not a file, or symlinked: {path}")
    actual = file_sha256(path)
    if actual != digest:
        raise CampaignError(f"{label} SHA-256 mismatch: expected={digest} actual={actual}")
    return path.resolve(), actual


def checked_repo_ref(root: Path, value: Any, label: str) -> tuple[Path, dict[str, Any]]:
    row = exact(value, {"path", "sha256"}, label)
    raw = nonempty(row["path"], f"{label}.path")
    path = root / raw
    resolved, digest = checked_external_file(path, row["sha256"], label)
    return resolved, {"path": raw, "sha256": digest}


class ArtifactReader:
    def __init__(self, root: Path):
        if root.is_symlink() or not root.is_dir():
            raise CampaignError("artifact root is missing, not a directory, or symlinked")
        self.root = root.resolve()
        self.verified: dict[str, dict[str, Any]] = {}

    def read(self, value: Any, label: str) -> tuple[bytes, dict[str, Any]]:
        row = exact(value, ARTIFACT_KEYS, label)
        raw = nonempty(row["path"], f"{label}.path")
        posix = PurePosixPath(raw)
        if posix.is_absolute() or not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
            raise CampaignError(f"{label}.path must be a normalized artifact-root-relative path")
        lowered = raw.lower()
        if any(marker in lowered for marker in FORBIDDEN_PATH_MARKERS):
            raise CampaignError(f"{label}.path names forbidden P6/parallel evidence lineage")
        path = self.root.joinpath(*posix.parts)
        cursor = self.root
        for part in posix.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise CampaignError(f"{label}.path traverses a symlink")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as error:
            raise CampaignError(f"{label}.path escapes or is absent from artifact root") from error
        if path.is_symlink() or not resolved.is_file():
            raise CampaignError(f"{label} is not a regular nonsymlink artifact")
        expected_size = counter(row["size_bytes"], f"{label}.size_bytes", positive=True)
        data = resolved.read_bytes()
        if len(data) != expected_size:
            raise CampaignError(f"{label} size mismatch")
        expected_sha = sha_string(row["sha256"], f"{label}.sha256")
        actual_sha = bytes_sha256(data)
        if actual_sha != expected_sha:
            raise CampaignError(f"{label} SHA-256 mismatch")
        identity = {"path": raw, "sha256": actual_sha, "size_bytes": len(data)}
        prior = self.verified.get(raw)
        if prior is not None and prior != identity:
            raise CampaignError(f"artifact path {raw} has contradictory identities")
        self.verified[raw] = identity
        return data, identity


def load_registry(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("redred_single_edge_trace_registry", path)
    if spec is None or spec.loader is None:
        raise CampaignError("frozen trace registry cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(getattr(module, "FULL50", None), tuple) or not isinstance(
        getattr(module, "TRACE_SHA256", None), dict
    ):
        raise CampaignError("frozen trace registry lacks FULL50/TRACE_SHA256")
    return module


def validate_manifest(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    exact(manifest, {
        "schema", "campaign_id", "dataset", "measurement", "candidates",
        "producer", "interchange_schema", "forbidden_evidence",
    }, "campaign manifest")
    if manifest["schema"] != "redred_single_edge_campaign_manifest_v1" or \
            manifest["campaign_id"] != EXPECTED_CAMPAIGN_ID:
        raise CampaignError("campaign manifest identity differs")
    dataset = exact(manifest["dataset"], {
        "id", "display_name", "source_class", "organizer_official",
        "manifest", "trace_registry",
    }, "campaign dataset")
    if dataset["id"] != "full50" or dataset["display_name"] != "team-defined synthetic full50" or \
            dataset["source_class"] != "TEAM_DEFINED_SYNTHETIC" or \
            dataset["organizer_official"] is not False:
        raise CampaignError("full50 classification changed or was relabeled as official data")
    manifest_path, manifest_ref = checked_repo_ref(root, dataset["manifest"], "campaign dataset manifest")
    registry_path, registry_ref = checked_repo_ref(root, dataset["trace_registry"], "campaign trace registry")
    suite = load_json(manifest_path, "frozen full50 manifest")
    if suite.get("schema_version") != 1 or not isinstance(suite.get("runs"), list):
        raise CampaignError("frozen full50 manifest schema differs")
    names: list[str] = []
    windows: dict[str, int] = {}
    for index, row in enumerate(suite["runs"]):
        if not isinstance(row, dict):
            raise CampaignError(f"frozen full50 manifest run {index} is not an object")
        name = nonempty(row.get("name"), f"frozen full50 run {index}.name")
        if name in windows:
            raise CampaignError(f"duplicate frozen full50 run {name}")
        names.append(name)
        windows[name] = counter(row.get("stim_cycles"), f"frozen full50 {name}.stim_cycles", positive=True)
    registry = load_registry(registry_path)
    if tuple(names) != registry.FULL50 or len(names) != 50 or set(registry.TRACE_SHA256) != set(names):
        raise CampaignError("frozen trace registry and full50 manifest membership/order differ")
    for name, digest in registry.TRACE_SHA256.items():
        sha_string(digest, f"frozen trace registry {name}")
    measurement = exact(manifest["measurement"], {
        "cycle_semantics", "generated", "source_overrun", "accepted", "retired",
        "occurrence_to_accept", "accept_to_retire", "hard_conservation",
    }, "campaign measurement")
    if measurement["cycle_semantics"] != EXPECTED_CYCLE_SEMANTICS or \
            measurement["hard_conservation"] != [
                "generated=source_overrun+accepted", "accepted=retired"
            ]:
        raise CampaignError("campaign measurement or conservation changed")
    for key in ("generated", "source_overrun", "accepted", "retired",
                "occurrence_to_accept", "accept_to_retire"):
        nonempty(measurement[key], f"campaign measurement.{key}")
    candidates = manifest["candidates"]
    if not isinstance(candidates, list) or [row.get("id") for row in candidates if isinstance(row, dict)] != list(EXPECTED_CANDIDATES):
        raise CampaignError("campaign candidates must be exactly ordered A2,A3")
    for row in candidates:
        exact(row, {"id", "semantic_role", "endpoint_id"}, f"campaign candidate {row.get('id')}")
        candidate = row["id"]
        if row["semantic_role"] != EXPECTED_ROLES[candidate] or row["endpoint_id"] != EXPECTED_ENDPOINTS[candidate]:
            raise CampaignError(f"campaign candidate {candidate} identity differs")
    producer = exact(manifest["producer"], {
        "id", "path", "evidence_class", "rtl_source_commit",
    }, "campaign producer")
    if producer != {
        "id": EXPECTED_PRODUCER_ID, "path": EXPECTED_PRODUCER_PATH,
        "evidence_class": EXPECTED_EVIDENCE_CLASS,
        "rtl_source_commit": EXPECTED_RTL_COMMIT,
    }:
        raise CampaignError("campaign producer identity/evidence class/RTL commit differs")
    commit_check = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{EXPECTED_RTL_COMMIT}^{{commit}}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if commit_check.returncode:
        raise CampaignError("pinned single-edge RTL producer commit is unavailable")
    schema_path, schema_ref = checked_repo_ref(root, manifest["interchange_schema"], "campaign interchange schema")
    schema = load_json(schema_path, "campaign interchange schema")
    if schema.get("$id") != EXPECTED_SCHEMA_ID:
        raise CampaignError("campaign interchange schema ID differs")
    forbidden = exact(manifest["forbidden_evidence"], {
        "p6_results", "parallel_results", "full50_as_organizer_official",
    }, "campaign forbidden evidence")
    if forbidden != {
        "p6_results": "FORBIDDEN", "parallel_results": "FORBIDDEN",
        "full50_as_organizer_official": "FORBIDDEN",
    }:
        raise CampaignError("campaign forbidden-evidence boundary changed")
    return {
        "manifest_ref": manifest_ref, "registry_ref": registry_ref,
        "schema_ref": schema_ref, "registry": registry, "names": names,
        "windows": windows, "measurement": measurement,
    }


def latency_summary(values: Iterable[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {
        "count": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(0.50), "p95": percentile(0.95),
        "p99": percentile(0.99), "max": ordered[-1],
    }


def validate_latency(value: Any, expected: dict[str, Any], label: str) -> None:
    row = exact(value, LATENCY_KEYS, label)
    count_value = counter(row["count"], f"{label}.count")
    mean_value = finite_number(row["mean"], f"{label}.mean")
    quantiles = {key: counter(row[key], f"{label}.{key}") for key in ("p50", "p95", "p99", "max")}
    normalized = {"count": count_value, "mean": mean_value, **quantiles}
    if normalized != expected:
        raise CampaignError(f"{label} differs from recomputed event latency")


def parse_trace(data: bytes, name: str) -> list[tuple[int, int, int]]:
    rows: list[tuple[int, int, int]] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CampaignError(f"dataset run {name} trace is not UTF-8 JSONL") from error
    for line_number, line in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise CampaignError(f"dataset run {name} trace line {line_number} is invalid JSON") from error
        if not isinstance(row, dict):
            raise CampaignError(f"dataset run {name} trace line {line_number} is not an object")
        event_id = counter(row.get("tb_only_event_id"), f"dataset run {name} trace event id")
        source = counter(row.get("logical_source"), f"dataset run {name} logical_source")
        occurrence = counter(row.get("occurrence_cycle"), f"dataset run {name} occurrence_cycle")
        if event_id != len(rows) or source >= 16:
            raise CampaignError(f"dataset run {name} trace identity/order/source differs")
        rows.append((event_id, source, occurrence))
    if not rows:
        raise CampaignError(f"dataset run {name} trace is empty")
    return rows


def git_blob(root: Path, commit: str, logical_path: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{logical_path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise CampaignError(f"cannot read pinned RTL blob {commit}:{logical_path}")
    return process.stdout


def validate_inventory(
    data: bytes, candidate: str, reader: ArtifactReader, repo_root: Path,
) -> dict[str, Any]:
    try:
        inventory = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignError(f"candidate {candidate} RTL inventory is invalid JSON") from error
    inventory = exact(inventory, {"schema", "candidate_id", "interface", "sources"},
                      f"candidate {candidate} RTL inventory")
    if inventory["schema"] != "redred_single_edge_rtl_inventory_v1" or \
            inventory["candidate_id"] != candidate or inventory["interface"] != "single_edge":
        raise CampaignError(f"candidate {candidate} RTL inventory identity differs")
    sources = inventory["sources"]
    if not isinstance(sources, list) or len(sources) != len(EXPECTED_RTL_SOURCES[candidate]):
        raise CampaignError(f"candidate {candidate} RTL inventory source closure differs")
    normalized = []
    logical_paths: set[str] = set()
    for index, (source, expected_logical) in enumerate(zip(sources, EXPECTED_RTL_SOURCES[candidate])):
        source = exact(source, {"logical_path", "artifact"},
                       f"candidate {candidate} RTL inventory source {index}")
        logical = nonempty(source["logical_path"], "RTL logical_path")
        lowered = logical.lower()
        if logical != expected_logical or logical in logical_paths or "p6" in lowered or \
                "parallel" in lowered:
            raise CampaignError(f"candidate {candidate} RTL inventory has duplicate or forbidden lineage")
        logical_paths.add(logical)
        source_data, identity = reader.read(source["artifact"], f"candidate {candidate} RTL source {logical}")
        if source_data != git_blob(repo_root, EXPECTED_RTL_COMMIT, logical):
            raise CampaignError(
                f"candidate {candidate} RTL source {logical} differs from pinned producer commit"
            )
        normalized.append({"logical_path": logical, **identity})
    return {"source_count": len(normalized), "sources": normalized}


def validate_event_artifacts(
    candidate: str, name: str, trace_rows: list[tuple[int, int, int]],
    run: dict[str, Any], reader: ArtifactReader,
) -> tuple[dict[str, Any], list[int], list[int]]:
    events_data, events_ref = reader.read(run["events"], f"candidate {candidate}/{name} events")
    summary_data, summary_ref = reader.read(run["summary"], f"candidate {candidate}/{name} summary")
    log_data, log_ref = reader.read(run["simulator_log"], f"candidate {candidate}/{name} simulator log")
    marker = f"SINGLE_EDGE_REPLAY_PASS candidate={candidate} trace={name}".encode()
    if marker not in log_data or b"P6_REPLAY_PASS" in log_data or b"PARALLEL_REPLAY_PASS" in log_data:
        raise CampaignError(f"candidate {candidate}/{name} simulator log lacks independent single-edge PASS marker")
    try:
        event_lines = events_data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise CampaignError(f"candidate {candidate}/{name} events are not UTF-8 JSONL") from error
    if len(event_lines) != len(trace_rows):
        raise CampaignError(f"candidate {candidate}/{name} generated event count differs from trace")
    accepted_order = 0
    retired_order = 0
    overrun = 0
    occurrence_latencies: list[int] = []
    internal_latencies: list[int] = []
    fixed_window_retired = 0
    start = run["window_start_cycle"]
    end = run["window_end_cycle_exclusive"]
    for index, (line, trace_identity) in enumerate(zip(event_lines, trace_rows), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CampaignError(f"candidate {candidate}/{name} event line {index} is invalid JSON") from error
        event = exact(event, EVENT_KEYS, f"candidate {candidate}/{name} event line {index}")
        event_id = counter(event["tb_only_event_id"], "event id")
        source = counter(event["logical_source"], "logical_source")
        occurrence = counter(event["occurrence_cycle"], "occurrence_cycle")
        if (event_id, source, occurrence) != trace_identity:
            raise CampaignError(f"candidate {candidate}/{name} event identity/order differs from trace")
        state = event["event_state"]
        if state == "source_overrun":
            if any(event[key] is not None for key in (
                "accept_cycle", "retire_cycle", "retired_logical_source",
                "accept_order", "retire_order",
            )):
                raise CampaignError(f"candidate {candidate}/{name} source_overrun carries accepted/retired data")
            overrun += 1
        elif state == "retired":
            accept = counter(event["accept_cycle"], "accept_cycle")
            retire = counter(event["retire_cycle"], "retire_cycle")
            retired_source = counter(event["retired_logical_source"], "retired_logical_source")
            accept_index = counter(event["accept_order"], "accept_order")
            retire_index = counter(event["retire_order"], "retire_order")
            if occurrence > accept or accept > retire or retired_source != source or \
                    accept_index != accepted_order or retire_index != retired_order:
                raise CampaignError(f"candidate {candidate}/{name} accept/retire identity, order, or cycles differ")
            accepted_order += 1
            retired_order += 1
            occurrence_latencies.append(accept - occurrence)
            internal_latencies.append(retire - accept)
            if start <= retire < end:
                fixed_window_retired += 1
        else:
            raise CampaignError(f"candidate {candidate}/{name} event_state must be source_overrun or retired")
    try:
        summary = json.loads(summary_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignError(f"candidate {candidate}/{name} summary is invalid JSON") from error
    summary = exact(summary, {
        "schema", "candidate_id", "trace", "generated", "source_overrun",
        "accepted", "retired", "fixed_window_retired", "fixed_window_cycles",
        "occurrence_to_accept", "accept_to_retire", "hard_errors",
    }, f"candidate {candidate}/{name} summary")
    if summary["schema"] != "a23_full_single_edge_run_summary_v1" or \
            summary["candidate_id"] != candidate or summary["trace"] != name:
        raise CampaignError(f"candidate {candidate}/{name} summary identity differs")
    counts = {
        "generated": len(trace_rows), "source_overrun": overrun,
        "accepted": accepted_order, "retired": retired_order,
        "fixed_window_retired": fixed_window_retired,
        "fixed_window_cycles": end - start,
    }
    for key, expected in counts.items():
        if counter(summary[key], f"candidate {candidate}/{name} summary.{key}") != expected:
            raise CampaignError(f"candidate {candidate}/{name} summary {key} differs from artifacts")
    if counts["generated"] != counts["source_overrun"] + counts["accepted"]:
        raise CampaignError(f"candidate {candidate}/{name} violates generated=source_overrun+accepted")
    if counts["accepted"] != counts["retired"]:
        raise CampaignError(f"candidate {candidate}/{name} violates accepted=retired")
    hard_errors = exact(summary["hard_errors"], EXPECTED_HARD_ERRORS,
                        f"candidate {candidate}/{name} hard_errors")
    for key, value in hard_errors.items():
        if counter(value, f"candidate {candidate}/{name} hard_errors.{key}") != 0:
            raise CampaignError(f"candidate {candidate}/{name} hard error {key} is nonzero")
    occurrence_summary = latency_summary(occurrence_latencies)
    internal_summary = latency_summary(internal_latencies)
    validate_latency(summary["occurrence_to_accept"], occurrence_summary,
                     f"candidate {candidate}/{name} occurrence_to_accept")
    validate_latency(summary["accept_to_retire"], internal_summary,
                     f"candidate {candidate}/{name} accept_to_retire")
    metrics = {
        **counts,
        "occurrence_to_accept": occurrence_summary,
        "accept_to_retire": internal_summary,
        "artifacts": {"events": events_ref, "summary": summary_ref, "simulator_log": log_ref},
    }
    return metrics, occurrence_latencies, internal_latencies


def validate_receipt(
    receipt: dict[str, Any], context: dict[str, Any], reader: ArtifactReader,
    repo_root: Path,
) -> dict[str, Any]:
    exact(receipt, {
        "schema", "status", "evidence_class", "campaign_id", "interface",
        "producer", "dataset", "common_binding", "evidence_lineage", "candidates",
    }, "replay receipt")
    if receipt["schema"] != EXPECTED_RECEIPT_SCHEMA or receipt["status"] != "PASS" or \
            receipt["campaign_id"] != EXPECTED_CAMPAIGN_ID:
        raise CampaignError("replay receipt schema/status/campaign identity differs")
    evidence_class = receipt["evidence_class"]
    if evidence_class != EXPECTED_EVIDENCE_CLASS:
        raise CampaignError("replay receipt evidence_class differs")
    producer = exact(receipt["producer"], {
        "id", "path", "evidence_class", "rtl_source_commit",
    }, "replay producer")
    if producer != {
        "id": EXPECTED_PRODUCER_ID, "path": EXPECTED_PRODUCER_PATH,
        "evidence_class": EXPECTED_EVIDENCE_CLASS,
        "rtl_source_commit": EXPECTED_RTL_COMMIT,
    }:
        raise CampaignError("replay producer does not match the exact A23 producer/RTL commit")
    interface = exact(receipt["interface"], {
        "id", "clock_edge", "transport", "p6_used", "parallel_used", "boundary",
        "acceptance_observation", "retirement_observation",
    }, "replay interface")
    expected_interface = {
        "id": "single_edge", "clock_edge": "posedge_only",
        "transport": "one_retirement_per_rising_edge", "p6_used": False,
        "parallel_used": False,
        "boundary": "synchronous_source_admission_through_synchronous_retirement",
        "acceptance_observation": "actual_atomic_scheduler_commit",
        "retirement_observation": "actual_single_edge_receiver_retire_valid_and_address",
    }
    if interface != expected_interface:
        raise CampaignError("replay interface is not the independent canonical single-edge boundary")
    lineage = exact(receipt["evidence_lineage"], {
        "replay_kind", "independent_execution", "borrowed_p6_results",
        "borrowed_parallel_results", "source_result_paths",
    }, "replay evidence_lineage")
    if lineage != {
        "replay_kind": "A23_FULL_SINGLE_EDGE_REPLAY_ACTUAL_RTL", "independent_execution": True,
        "borrowed_p6_results": False, "borrowed_parallel_results": False,
        "source_result_paths": [],
    }:
        raise CampaignError("replay evidence lineage borrows or aliases P6/parallel results")
    dataset = exact(receipt["dataset"], {
        "id", "display_name", "source_class", "organizer_official", "run_count",
        "manifest", "trace_registry", "runs",
    }, "replay dataset")
    if dataset["id"] != "full50" or dataset["display_name"] != "team-defined synthetic full50" or \
            dataset["source_class"] != "TEAM_DEFINED_SYNTHETIC" or \
            dataset["organizer_official"] is not False or \
            counter(dataset["run_count"], "replay dataset.run_count") != 50:
        raise CampaignError("replay full50 was relabeled, resized, or presented as official data")
    manifest_data, manifest_ref = reader.read(dataset["manifest"], "replay dataset manifest")
    registry_data, registry_ref = reader.read(dataset["trace_registry"], "replay trace registry")
    if bytes_sha256(manifest_data) != context["manifest_ref"]["sha256"] or \
            bytes_sha256(registry_data) != context["registry_ref"]["sha256"]:
        raise CampaignError("replay dataset manifest/trace registry differ from frozen team full50 bytes")
    dataset_runs = dataset["runs"]
    if not isinstance(dataset_runs, list) or len(dataset_runs) != 50:
        raise CampaignError("replay dataset must contain exactly 50 ordered run bindings")
    trace_bindings: dict[str, dict[str, Any]] = {}
    for index, (run, expected_name) in enumerate(zip(dataset_runs, context["names"])):
        label = f"replay dataset run {index}"
        run = exact(run, {
            "name", "trace_sha256", "fixed_window_cycles", "window_start_cycle",
            "window_end_cycle_exclusive", "trace", "prepared_input",
        }, label)
        if run["name"] != expected_name or run["trace_sha256"] != context["registry"].TRACE_SHA256[expected_name]:
            raise CampaignError(f"{label} name/trace SHA differs from frozen full50")
        cycles = counter(run["fixed_window_cycles"], f"{label}.fixed_window_cycles", positive=True)
        start = counter(run["window_start_cycle"], f"{label}.window_start_cycle")
        end = counter(run["window_end_cycle_exclusive"], f"{label}.window_end_cycle_exclusive", positive=True)
        if cycles != context["windows"][expected_name] or end - start != cycles:
            raise CampaignError(f"{label} fixed measurement window differs")
        trace_data, trace_ref = reader.read(run["trace"], f"{label} trace")
        prepared_data, prepared_ref = reader.read(run["prepared_input"], f"{label} prepared input")
        if trace_ref["sha256"] != run["trace_sha256"] or not prepared_data:
            raise CampaignError(f"{label} trace/prepared artifact binding differs")
        trace_bindings[expected_name] = {
            "trace_sha256": trace_ref["sha256"],
            "prepared_input_sha256": prepared_ref["sha256"],
            "fixed_window_cycles": cycles, "window_start_cycle": start,
            "window_end_cycle_exclusive": end, "trace_rows": parse_trace(trace_data, expected_name),
            "artifacts": {"trace": trace_ref, "prepared_input": prepared_ref},
        }
    common = exact(receipt["common_binding"], {
        "cycle_semantics", "tool", "tool_version", "testbench", "runner",
    }, "replay common_binding")
    if common["cycle_semantics"] != EXPECTED_CYCLE_SEMANTICS:
        raise CampaignError("replay common cycle semantics differ")
    tool_version = nonempty(common["tool_version"], "replay common tool_version")
    common_refs = {}
    for key in ("tool", "testbench", "runner"):
        _, common_refs[key] = reader.read(common[key], f"replay common {key}")
    common_hash = object_sha256(common)
    candidates = receipt["candidates"]
    if not isinstance(candidates, dict) or list(candidates) != list(EXPECTED_CANDIDATES):
        raise CampaignError("replay candidates must be exactly ordered A2,A3")
    candidate_reports: dict[str, Any] = {}
    for candidate in EXPECTED_CANDIDATES:
        row = exact(candidates[candidate], {
            "candidate_id", "semantic_role", "endpoint_id", "common_binding_sha256",
            "rtl_inventory", "runs",
        }, f"candidate {candidate}")
        if row["candidate_id"] != candidate or row["semantic_role"] != EXPECTED_ROLES[candidate] or \
                row["endpoint_id"] != EXPECTED_ENDPOINTS[candidate] or \
                row["common_binding_sha256"] != common_hash:
            raise CampaignError(f"candidate {candidate} identity/common tool-TB binding differs")
        inventory_data, inventory_ref = reader.read(row["rtl_inventory"], f"candidate {candidate} RTL inventory")
        inventory = validate_inventory(inventory_data, candidate, reader, repo_root)
        runs = row["runs"]
        if not isinstance(runs, list) or len(runs) != 50:
            raise CampaignError(f"candidate {candidate} must contain exactly 50 actual run artifacts")
        occurrence_all: list[int] = []
        internal_all: list[int] = []
        per_run: dict[str, Any] = {}
        for index, (run, expected_name) in enumerate(zip(runs, context["names"])):
            label = f"candidate {candidate} run {index}"
            run = exact(run, {
                "name", "trace_sha256", "prepared_input_sha256", "fixed_window_cycles",
                "window_start_cycle", "window_end_cycle_exclusive", "events", "summary",
                "simulator_log",
            }, label)
            binding = trace_bindings[expected_name]
            for key in (
                "trace_sha256", "prepared_input_sha256", "fixed_window_cycles",
                "window_start_cycle", "window_end_cycle_exclusive",
            ):
                if run[key] != binding[key]:
                    raise CampaignError(f"{label} differs from common A2/A3 trace/window binding in {key}")
            if run["name"] != expected_name:
                raise CampaignError(f"{label} ordered trace name differs")
            metrics, occurrence_values, internal_values = validate_event_artifacts(
                candidate, expected_name, binding["trace_rows"], run, reader
            )
            per_run[expected_name] = metrics
            occurrence_all.extend(occurrence_values)
            internal_all.extend(internal_values)
        totals = {
            key: sum(per_run[name][key] for name in context["names"])
            for key in (
                "generated", "source_overrun", "accepted", "retired",
                "fixed_window_retired", "fixed_window_cycles",
            )
        }
        if totals["generated"] != totals["source_overrun"] + totals["accepted"] or \
                totals["accepted"] != totals["retired"]:
            raise CampaignError(f"candidate {candidate} aggregate conservation differs")
        candidate_reports[candidate] = {
            "semantic_role": EXPECTED_ROLES[candidate],
            "endpoint_id": EXPECTED_ENDPOINTS[candidate],
            "common_binding_sha256": common_hash,
            "rtl_inventory": {"artifact": inventory_ref, **inventory},
            "run_count": 50, "totals": totals,
            "occurrence_to_accept": latency_summary(occurrence_all),
            "accept_to_retire": latency_summary(internal_all),
            "runs": per_run,
        }
    return {
        "evidence_class": evidence_class,
        "dataset": {
            "id": "full50", "display_name": "team-defined synthetic full50",
            "source_class": "TEAM_DEFINED_SYNTHETIC", "organizer_official": False,
            "run_count": 50, "manifest": manifest_ref, "trace_registry": registry_ref,
        },
        "common_binding": {
            "sha256": common_hash, "tool_version": tool_version,
            "cycle_semantics": EXPECTED_CYCLE_SEMANTICS, **common_refs,
        },
        "candidates": candidate_reports,
        "verified_artifact_count": len(reader.verified),
    }


def hold_receipt(manifest_path: Path, manifest_sha: str, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "redred_single_edge_campaign_evidence_v1",
        "status": "HOLD",
        "campaign_id": EXPECTED_CAMPAIGN_ID,
        "single_edge_digital_gate": "HOLD_NO_ACTUAL_REPLAY_ARTIFACTS",
        "system_release": "HOLD",
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha},
        "interchange": {
            "required_schema_sha256": context["schema_ref"]["sha256"],
            "explicit_schema_input": None, "explicit_receipt_input": None,
            "artifact_root": None,
        },
        "dataset": {
            "id": "full50", "display_name": "team-defined synthetic full50",
            "source_class": "TEAM_DEFINED_SYNTHETIC", "organizer_official": False,
            "run_count": 50,
        },
        "evidence_lineage": {"p6_results": "FORBIDDEN", "parallel_results": "FORBIDDEN"},
        "candidates": {candidate: {"status": "HOLD", "artifacts": None} for candidate in EXPECTED_CANDIDATES},
        "hold_reasons": [
            "explicit replay schema, receipt, immutable caller hashes, and artifact root were not supplied",
            "actual A2/A3 single-edge full50 replay artifacts do not exist in this campaign package",
        ],
    }


def evaluate(
    manifest_path: Path, root: Path, replay_schema: Path | None,
    replay_schema_sha256: str | None, replay_receipt: Path | None,
    replay_receipt_sha256: str | None, artifact_root: Path | None,
) -> dict[str, Any]:
    manifest_path, manifest_sha = checked_external_file(
        manifest_path, file_sha256(manifest_path), "campaign manifest"
    )
    manifest = load_json(manifest_path, "campaign manifest")
    context = validate_manifest(manifest, root)
    supplied = [replay_schema, replay_schema_sha256, replay_receipt,
                replay_receipt_sha256, artifact_root]
    if not any(value is not None for value in supplied):
        return hold_receipt(manifest_path, manifest_sha, context)
    if any(value is None for value in supplied):
        raise CampaignError(
            "schema path/hash, receipt path/hash, and artifact root must be supplied together"
        )
    assert replay_schema is not None and replay_schema_sha256 is not None
    assert replay_receipt is not None and replay_receipt_sha256 is not None
    assert artifact_root is not None
    schema_path, schema_sha = checked_external_file(
        replay_schema, replay_schema_sha256, "explicit replay schema"
    )
    if schema_sha != context["schema_ref"]["sha256"]:
        raise CampaignError("explicit replay schema is not the pinned interoperability schema")
    schema_document = load_json(schema_path, "explicit replay schema")
    if schema_document.get("$id") != EXPECTED_SCHEMA_ID:
        raise CampaignError("explicit replay schema ID differs")
    receipt_path, receipt_sha = checked_external_file(
        replay_receipt, replay_receipt_sha256, "explicit replay receipt"
    )
    receipt = load_json(receipt_path, "explicit replay receipt")
    reader = ArtifactReader(artifact_root)
    validated = validate_receipt(receipt, context, reader, root)
    return {
        "schema": "redred_single_edge_campaign_evidence_v1",
        "status": "EVIDENCE_COMPLETE",
        "campaign_id": EXPECTED_CAMPAIGN_ID,
        "single_edge_digital_gate": "GO",
        "system_release": "HOLD_OUTSIDE_DIGITAL_CAMPAIGN_SCOPE",
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha},
        "interchange": {
            "schema": {"path": str(schema_path), "sha256": schema_sha},
            "receipt": {"path": str(receipt_path), "sha256": receipt_sha},
            "artifact_root": str(reader.root),
        },
        "dataset": validated["dataset"],
        "evidence_lineage": {
            "class": validated["evidence_class"], "p6_results": "FORBIDDEN",
            "parallel_results": "FORBIDDEN", "independent_single_edge_replay": True,
        },
        "common_binding": validated["common_binding"],
        "candidates": validated["candidates"],
        "verified_artifact_count": validated["verified_artifact_count"],
        "hold_reasons": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--replay-schema", type=Path)
    parser.add_argument("--replay-schema-sha256")
    parser.add_argument("--replay-receipt", type=Path)
    parser.add_argument("--replay-receipt-sha256")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-hold", action="store_true")
    args = parser.parse_args()
    try:
        report = evaluate(
            args.manifest.resolve(), args.repo_root.resolve(),
            args.replay_schema.resolve() if args.replay_schema else None,
            args.replay_schema_sha256,
            args.replay_receipt.resolve() if args.replay_receipt else None,
            args.replay_receipt_sha256,
            args.artifact_root.resolve() if args.artifact_root else None,
        )
        payload = canonical(report)
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
        sys.stdout.buffer.write(payload)
        if report["status"] == "HOLD":
            return 0 if args.allow_hold else 3
        return 0
    except (CampaignError, OSError) as error:
        print(f"REDRED_SINGLE_EDGE_CAMPAIGN_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
