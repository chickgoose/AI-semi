#!/usr/bin/env python3
"""Publish a fail-closed receipt for one immutable full50/capacity22 attempt."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import common_suite_official as official

SCHEMA_VERSION = 4
SIDECAR_SCHEMA_VERSION = 2
ANALYZER_WORKLOADS = {
    "pairwise_contention", "mixed_phase_always_ready",
    "phase_transition", "timing_pair",
}
PHASE_NAMES = ["sparse", "near_saturation", "overload", "post_sparse", "drain"]
MIXED_PHASE_BOUNDS = [
    ("u_bernoulli", 0, 640), ("u_smooth", 640, 1280),
    ("s_persistent", 1280, 1536), ("s_rotating", 1536, 1792),
    ("h_a", 1792, 2560), ("h_b", 2560, 3328),
    ("h_a_replay", 3328, 4096),
]


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


def _integer(value: Any, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReceiptError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ReceiptError(f"{label} must be <= {maximum}")
    return value


def _number(value: Any, label: str, minimum: float = 0.0,
            maximum: float | None = None, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReceiptError(f"{label} must be a finite number")
    if value < minimum or (maximum is not None and value > maximum):
        raise ReceiptError(f"{label} is outside [{minimum}, {maximum}]")
    return float(value)


def _canonical_sha(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _relative_name(value: Any, label: str) -> str:
    text = _string(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or text in {".", ""}:
        raise ReceiptError(f"{label} must be a normalized relative path")
    if str(path) != text:
        raise ReceiptError(f"{label} must be normalized")
    return text


def _validate_candidate_manifest(doc: dict[str, Any], candidate: str) -> None:
    required = {"schema_version", "candidate", "commit_sha", "bundle_sha256", "filelist",
                "top", "parameters", "defines", "includes", "source_count", "retire_lanes"}
    if set(doc) != required or doc.get("schema_version") != 2 or doc.get("candidate") != candidate:
        raise ReceiptError("candidate manifest schema/identity mismatch")
    commit = _string(doc.get("commit_sha"), "candidate commit_sha")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ReceiptError("candidate commit_sha must be a full lowercase Git SHA-1")
    files = doc.get("filelist")
    if not isinstance(files, list) or not files:
        raise ReceiptError("candidate filelist must be a non-empty array")
    normalized, seen = [], set()
    for position, row in enumerate(files):
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ReceiptError(f"candidate filelist[{position}] schema mismatch")
        path = _relative_name(row.get("path"), f"candidate filelist[{position}].path")
        if path in seen:
            raise ReceiptError(f"candidate filelist duplicates {path}")
        seen.add(path)
        normalized.append({"path": path, "sha256": _sha(row.get("sha256"),
                                                          f"candidate filelist[{position}].sha256")})
    if _sha(doc.get("bundle_sha256"), "candidate bundle_sha256") != _canonical_sha(normalized):
        raise ReceiptError("candidate bundle_sha256 does not bind the ordered filelist")
    _string(doc.get("top"), "candidate top")
    for key in ("parameters", "defines"):
        value = doc.get(key)
        if not isinstance(value, dict) or any(not isinstance(name, str) or not name for name in value):
            raise ReceiptError(f"candidate {key} must be an object with non-empty string keys")
        try:
            json.dumps(value, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ReceiptError(f"candidate {key} is not canonical JSON data") from exc
    includes = doc.get("includes")
    if not isinstance(includes, list) or len(includes) != len(set(includes)):
        raise ReceiptError("candidate includes must be a unique array")
    for position, path in enumerate(includes):
        _relative_name(path, f"candidate includes[{position}]")
    sources = _integer(doc.get("source_count"), "candidate source_count", 1)
    _integer(doc.get("retire_lanes"), "candidate retire_lanes", 1, sources)


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


def _tb_load_pct(load: Any) -> int:
    """Mirror aer_clean_tb: load_milli=int(load*1000), then round to percent."""
    try:
        load_milli = int(Decimal(str(load)) * 1000)
    except Exception as exc:
        raise ReceiptError("run load is not a valid decimal") from exc
    if load_milli < 0:
        raise ReceiptError("run load must be nonnegative")
    return (load_milli + 5) // 10


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
    required = {"candidate", "test", "seed", "load_pct"}
    if not rows or not required.issubset(rows[0]):
        raise ReceiptError(f"run {name} result lacks candidate/test/seed/load_pct rows")
    triples = {(row.get("candidate", ""), row.get("test", ""), row.get("seed", "")) for row in rows}
    expected = (candidate, str(metadata["report_group"]), str(metadata["run"]["seed"]))
    if len(triples) != 1 or next(iter(triples)) != expected:
        raise ReceiptError(f"run {name} result candidate/test/seed provenance mismatch")
    expected_load = Decimal(_tb_load_pct(metadata["run"]["load"]))
    try:
        loads = {Decimal(row.get("load_pct", "")) for row in rows}
    except Exception as exc:
        raise ReceiptError(f"run {name} result load_pct provenance is invalid") from exc
    if loads != {expected_load}:
        raise ReceiptError(f"run {name} result load_pct provenance mismatch")
    return expected


def _analyzer_provenance(doc: dict[str, Any], metadata: dict[str, Any], csv_key,
                         name: str) -> None:
    common = (doc.get("candidate"), doc.get("test"), str(doc.get("seed", "")))
    if common != csv_key or doc.get("trace_sha256") != metadata["trace_sha256"]:
        raise ReceiptError(f"run {name} analyzer provenance mismatch")
    workload = metadata["run"]["workload"]
    if workload == "pairwise_contention":
        required_top = {"candidate", "test", "seed", "trace_sha256", "generator_version",
            "logical_source_permutation", "pair_count", "evaluable_pairs", "dropped_pairs",
            "censored_pairs", "nonevaluable_pairs", "measurement_state",
            "mean_pair_completion_latency_cycles", "p95_pair_completion_latency_cycles",
            "max_pair_completion_latency_cycles", "mean_pair_service_skew_cycles",
            "p95_pair_service_skew_cycles", "max_pair_service_skew_cycles", "a_first_pairs",
            "b_first_pairs", "same_cycle_pairs", "overlap_pairs", "max_overlapping_prior_pairs",
            "isolation_state", "worst_completion_pair", "worst_skew_pair", "pair_aggregates", "trials"}
        if not required_top.issubset(doc):
            raise ReceiptError(f"run {name} pairwise analyzer schema is incomplete")
        if (doc.get("generator_version") != metadata["generator_version"] or
                doc.get("logical_source_permutation") != metadata["logical_source_permutation"]):
            raise ReceiptError(f"run {name} pairwise analyzer manifest provenance mismatch")
        pair_count = _integer(doc.get("pair_count"), f"run {name} pair_count")
        evaluable = _integer(doc.get("evaluable_pairs"), f"run {name} evaluable_pairs")
        if (pair_count != 240 or evaluable != 240 or doc.get("measurement_state") != "COMPLETE" or
                any(_integer(doc.get(key), f"run {name} {key}") != 0
                    for key in ("dropped_pairs", "censored_pairs", "nonevaluable_pairs"))):
            raise ReceiptError(f"run {name} pairwise analyzer is incomplete or censored")
        order_counts = [_integer(doc.get(key), f"run {name} {key}", 0, pair_count)
                        for key in ("a_first_pairs", "b_first_pairs", "same_cycle_pairs")]
        overlap = _integer(doc.get("overlap_pairs"), f"run {name} overlap_pairs", 0, pair_count)
        _integer(doc.get("max_overlapping_prior_pairs"),
                 f"run {name} max_overlapping_prior_pairs", 0, pair_count - 1)
        if sum(order_counts) != pair_count:
            raise ReceiptError(f"run {name} pairwise ordering cardinality mismatch")
        metrics = ("mean_pair_completion_latency_cycles", "p95_pair_completion_latency_cycles",
                   "max_pair_completion_latency_cycles", "mean_pair_service_skew_cycles",
                   "p95_pair_service_skew_cycles", "max_pair_service_skew_cycles")
        metric_values = [_number(doc.get(key), f"run {name} {key}") for key in metrics]
        if metric_values[1] > metric_values[2] or metric_values[4] > metric_values[5]:
            raise ReceiptError(f"run {name} pairwise metric percentile order mismatch")
        aggregates, trials = doc.get("pair_aggregates"), doc.get("trials")
        if not isinstance(aggregates, list) or len(aggregates) != 120 or not isinstance(trials, list) or len(trials) != 240:
            raise ReceiptError(f"run {name} pairwise N16 aggregate/trial cardinality mismatch")
        canonical_pairs = set()
        for position, row in enumerate(aggregates):
            if not isinstance(row, dict):
                raise ReceiptError(f"run {name} pairwise aggregate[{position}] must be an object")
            required_aggregate = {"canonical_source_a", "canonical_source_b", "physical_source_a",
                "physical_source_b", "trial_count", "evaluable_trials", "dropped_trials",
                "censored_trials", "overlap_trials", "mean_completion_latency_cycles",
                "max_completion_latency_cycles", "mean_service_skew_cycles", "max_service_skew_cycles"}
            if not required_aggregate.issubset(row):
                raise ReceiptError(f"run {name} pairwise aggregate schema mismatch")
            pair = (_integer(row.get("canonical_source_a"), "canonical_source_a", 0, 15),
                    _integer(row.get("canonical_source_b"), "canonical_source_b", 0, 15))
            if pair[0] >= pair[1] or pair in canonical_pairs:
                raise ReceiptError(f"run {name} pairwise aggregate canonical pair mismatch")
            canonical_pairs.add(pair)
            _integer(row.get("physical_source_a"), "physical_source_a", 0, 15)
            _integer(row.get("physical_source_b"), "physical_source_b", 0, 15)
            if any(_integer(row.get(key), f"run {name} aggregate {key}") != expected for key, expected in (
                    ("trial_count", 2), ("evaluable_trials", 2), ("dropped_trials", 0),
                    ("censored_trials", 0))):
                raise ReceiptError(f"run {name} pairwise aggregate accounting mismatch")
            _integer(row.get("overlap_trials"), f"run {name} aggregate overlap_trials", 0, 2)
            for key in ("mean_completion_latency_cycles", "max_completion_latency_cycles",
                        "mean_service_skew_cycles", "max_service_skew_cycles"):
                _number(row.get(key), f"run {name} aggregate {key}")
        required_trial = {"relation_id", "repeat_index", "canonical_source_a", "canonical_source_b",
            "physical_source_a", "physical_source_b", "overlaps_previous_pair",
            "overlapping_prior_pair_count", "event_state_a", "event_state_b", "source_a", "source_b",
            "delivery_a", "delivery_b", "completion_latency_cycles", "service_skew_cycles", "result"}
        if len(canonical_pairs) != 120 or any(not isinstance(row, dict) or row.get("result") != "evaluable" or
                                               not required_trial.issubset(row) for row in trials):
            raise ReceiptError(f"run {name} pairwise trial schema mismatch")
        relation_ids = set()
        for row in trials:
            relation_id = _integer(row.get("relation_id"), f"run {name} relation_id", 0, 239)
            relation_ids.add(relation_id)
            _integer(row.get("repeat_index"), f"run {name} repeat_index", 0, 1)
            for key in ("canonical_source_a", "canonical_source_b", "physical_source_a",
                        "physical_source_b", "source_a", "source_b"):
                _integer(row.get(key), f"run {name} trial {key}", 0, 15)
            if not isinstance(row.get("overlaps_previous_pair"), bool):
                raise ReceiptError(f"run {name} pairwise trial overlap flag is not boolean")
            _integer(row.get("overlapping_prior_pair_count"), f"run {name} prior overlap", 0, 239)
            for key in ("delivery_a", "delivery_b", "completion_latency_cycles", "service_skew_cycles"):
                _integer(row.get(key), f"run {name} trial {key}")
        if relation_ids != set(range(240)):
            raise ReceiptError(f"run {name} pairwise relation IDs are not exact")
        if overlap != sum(bool(row.get("overlaps_previous_pair")) for row in trials):
            raise ReceiptError(f"run {name} pairwise overlap accounting mismatch")
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
        matched, summary, phases = doc.get("matched_trace_validation"), doc.get("summary_evidence"), doc.get("phases")
        mixed_offset = _integer(doc.get("tb_cycle_offset"), f"run {name} tb_cycle_offset")
        if (_integer(doc.get("observation_end_cycle"), f"run {name} observation_end_cycle") <
                mixed_offset + metadata["run"]["stim_cycles"] - 1 or
                not isinstance(doc.get("matched_pair_deltas"), list) or
                len(doc["matched_pair_deltas"]) != 4):
            raise ReceiptError(f"run {name} mixed analyzer top-level schema is incomplete")
        matched_keys = {"status", "uniform_exact_event_count_and_source_histogram",
            "sustained_exact_event_source_and_fan_in_histograms", "sustained_frozen_dwell_and_rotation",
            "hotspot_derived_rank_stream", "hotspot_a_replay_exact_physical_replay"}
        summary_keys = {"status", "correctness_qualified", "scoreboard_errors", "conservation_validated",
                        "generated_equals_overrun_plus_accepted", "accepted_equals_delivered"}
        classification_keys = {"analysis_status", "correctness_status", "correctness_scope",
                               "capacity_status", "capacity_loss_events", "capacity_loss_ratio", "censored_events"}
        if (not isinstance(matched, dict) or set(matched) != matched_keys or matched.get("status") != "pass" or
                any(value is not True for key, value in matched.items() if key != "status") or
                not isinstance(summary, dict) or summary.get("status") != "qualified_pass" or
                summary.get("correctness_qualified") is not True or summary.get("scoreboard_errors") != 0 or
                summary.get("conservation_validated") is not True or set(summary) != summary_keys or
                summary.get("generated_equals_overrun_plus_accepted") is not True or
                summary.get("accepted_equals_delivered") is not True or set(classification) != classification_keys or
                not isinstance(phases, list) or
                len(phases) != len(MIXED_PHASE_BOUNDS)):
            raise ReceiptError(f"run {name} mixed analyzer schema/accounting is incomplete")
        total_generated = total_overrun = 0
        for row, (phase, start, end) in zip(phases, MIXED_PHASE_BOUNDS):
            if not isinstance(row, dict) or (row.get("phase"), row.get("start_cycle"),
                                              row.get("end_cycle_exclusive"), row.get("cycles")) != (
                                                phase, start, end, end - start):
                raise ReceiptError(f"run {name} mixed analyzer phase boundaries mismatch")
            required_phase = {"phase", "start_cycle", "end_cycle_exclusive", "cycles", "generated",
                "source_overrun", "accepted", "delivered", "offered_events_per_cycle",
                "accepted_events_per_cycle", "delivered_by_occurrence_events_per_cycle",
                "delivered_in_window", "retire_throughput_events_per_cycle", "capacity_loss_ratio",
                "latency_cycles", "service_gap_cycles", "backlog_at_start", "backlog_peak",
                "backlog_at_end", "backlog_recovery_to_zero_cycles",
                "phase_origin_last_delivery_after_boundary_cycles"}
            if set(row) != required_phase:
                raise ReceiptError(f"run {name} mixed analyzer phase schema mismatch")
            generated = _integer(row.get("generated"), f"run {name} {phase}.generated")
            overrun = _integer(row.get("source_overrun"), f"run {name} {phase}.source_overrun", 0, generated)
            accepted = _integer(row.get("accepted"), f"run {name} {phase}.accepted", 0, generated)
            delivered = _integer(row.get("delivered"), f"run {name} {phase}.delivered", 0, accepted)
            _integer(row.get("delivered_in_window"), f"run {name} {phase}.delivered_in_window", 0,
                     sum(_integer(other.get("delivered"), "mixed delivered") for other in phases
                         if isinstance(other, dict)))
            if generated != overrun + accepted or accepted != delivered:
                raise ReceiptError(f"run {name} mixed analyzer event conservation mismatch")
            rates = [_number(row.get(key), f"run {name} {phase}.{key}", 0, 16) for key in (
                "offered_events_per_cycle", "accepted_events_per_cycle",
                "delivered_by_occurrence_events_per_cycle", "retire_throughput_events_per_cycle")]
            expected_rates = [generated / (end-start), accepted / (end-start), delivered / (end-start),
                              row["delivered_in_window"] / (end-start)]
            loss_ratio = _number(row.get("capacity_loss_ratio"), f"run {name} {phase}.capacity_loss_ratio", 0, 1)
            if any(not math.isclose(actual, expected) for actual, expected in zip(rates, expected_rates)) or not math.isclose(
                    loss_ratio, overrun / generated if generated else 0.0):
                raise ReceiptError(f"run {name} mixed analyzer derived-rate mismatch")
            latency = row.get("latency_cycles")
            if (not isinstance(latency, dict) or set(latency) != {"samples", "mean", "p50", "p95", "p99", "max"} or
                    _integer(latency.get("samples"), "mixed latency samples") != delivered):
                raise ReceiptError(f"run {name} mixed analyzer latency schema mismatch")
            latency_values = [_number(latency.get(key), f"run {name} {phase}.latency.{key}", nullable=delivered == 0)
                              for key in ("mean", "p50", "p95", "p99", "max")]
            if delivered and not (latency_values[1] <= latency_values[2] <= latency_values[3] <= latency_values[4]):
                raise ReceiptError(f"run {name} mixed analyzer latency percentile order mismatch")
            service = row.get("service_gap_cycles")
            if not isinstance(service, dict) or set(service) != {"active_sources", "delivered_sources",
                    "unobserved_active_sources", "samples", "p95_cycles", "p99_cycles", "max_cycles"}:
                raise ReceiptError(f"run {name} mixed analyzer service-gap schema mismatch")
            active = _integer(service.get("active_sources"), "mixed active_sources", 0, 16)
            delivered_sources = _integer(service.get("delivered_sources"), "mixed delivered_sources", 0, active)
            if _integer(service.get("unobserved_active_sources"), "mixed unobserved", 0, active) != active - delivered_sources:
                raise ReceiptError(f"run {name} mixed analyzer service source accounting mismatch")
            _integer(service.get("samples"), "mixed service samples")
            service_values = [_number(service.get(key), f"run {name} {phase}.service.{key}",
                                      nullable=service["samples"] == 0)
                              for key in ("p95_cycles", "p99_cycles", "max_cycles")]
            if service["samples"] and not (service_values[0] <= service_values[1] <= service_values[2]):
                raise ReceiptError(f"run {name} mixed analyzer service percentile order mismatch")
            for key in ("backlog_at_start", "backlog_peak", "backlog_at_end",
                        "phase_origin_last_delivery_after_boundary_cycles"):
                _integer(row.get(key), f"run {name} {phase}.{key}")
            _integer(row.get("backlog_recovery_to_zero_cycles"),
                     f"run {name} {phase}.backlog_recovery_to_zero_cycles")
            total_generated += generated
            total_overrun += overrun
        if (_integer(classification.get("capacity_loss_events"), "mixed capacity_loss_events") != total_overrun or
                _integer(classification.get("censored_events"), "mixed censored_events") != 0 or
                not math.isclose(_number(classification.get("capacity_loss_ratio"), "mixed capacity_loss_ratio", 0, 1),
                                 total_overrun / total_generated if total_generated else 0.0)):
            raise ReceiptError(f"run {name} mixed analyzer classification accounting mismatch")
        expected_pairs = [("uniform_temporal", "u_bernoulli", "u_smooth"),
                          ("sustained_temporal", "s_persistent", "s_rotating"),
                          ("spatial_b_vs_a", "h_b", "h_a"),
                          ("spatial_replay_vs_a", "h_a_replay", "h_a")]
        delta_keys = {"pair", "left_phase", "right_phase", "sign_convention", "generated_delta",
            "capacity_loss_events_delta", "capacity_loss_ratio_delta", "retire_throughput_delta",
            "p95_latency_cycles_delta", "p99_latency_cycles_delta", "max_service_gap_cycles_delta",
            "backlog_peak_delta", "backlog_recovery_cycles_delta"}
        for row, expected_pair in zip(doc["matched_pair_deltas"], expected_pairs):
            if (not isinstance(row, dict) or set(row) != delta_keys or
                    (row.get("pair"), row.get("left_phase"), row.get("right_phase")) != expected_pair or
                    row.get("sign_convention") != "left_minus_right"):
                raise ReceiptError(f"run {name} mixed analyzer matched-delta schema mismatch")
            for key in delta_keys - {"pair", "left_phase", "right_phase", "sign_convention"}:
                _number(row.get(key), f"run {name} mixed delta {key}", -math.inf,
                        nullable=key in {"p95_latency_cycles_delta", "p99_latency_cycles_delta",
                                         "max_service_gap_cycles_delta", "backlog_recovery_cycles_delta"})
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
        _integer(doc.get("recovery_to_zero_cycles"), f"run {name} recovery_to_zero_cycles")
        stim = metadata["run"]["stim_cycles"]
        eighth = stim // 8
        bounds = [(0, 2 * eighth), (2 * eighth, 4 * eighth), (4 * eighth, 6 * eighth),
                  (6 * eighth, 7 * eighth), (7 * eighth, 8 * eighth)]
        for row, phase, (start, end) in zip(phases, PHASE_NAMES, bounds):
            if (row.get("phase"), row.get("start_cycle"), row.get("end_cycle_exclusive")) != (phase, start, end):
                raise ReceiptError(f"run {name} phase-transition boundaries mismatch")
            required_phase = {"phase", "start_cycle", "end_cycle_exclusive", "generated", "source_overrun",
                "accepted", "delivered_by_occurrence_phase", "delivered_in_phase_window",
                "completion_per_phase_cycle", "p95_e2e_latency_cycles", "backlog_peak", "backlog_at_end",
                "cumulative_overrun_at_end", "loss_adjusted_pressure_peak"}
            if set(row) != required_phase:
                raise ReceiptError(f"run {name} phase-transition phase schema mismatch")
            generated = _integer(row.get("generated"), f"run {name} {phase}.generated")
            overrun = _integer(row.get("source_overrun"), f"run {name} {phase}.source_overrun", 0, generated)
            accepted = _integer(row.get("accepted"), f"run {name} {phase}.accepted", 0, generated)
            delivered = _integer(row.get("delivered_by_occurrence_phase"),
                                 f"run {name} {phase}.delivered", 0, accepted)
            _integer(row.get("delivered_in_phase_window"), f"run {name} {phase}.window")
            if generated != overrun + accepted or delivered != accepted:
                raise ReceiptError(f"run {name} phase-transition event conservation mismatch")
            completion = _number(row.get("completion_per_phase_cycle"), f"run {name} {phase}.completion", 0, 16)
            if not math.isclose(completion, row["delivered_in_phase_window"] / (end-start)):
                raise ReceiptError(f"run {name} phase-transition completion rate mismatch")
            _number(row.get("p95_e2e_latency_cycles"), f"run {name} {phase}.p95",
                    nullable=delivered == 0)
            peak = _integer(row.get("backlog_peak"), f"run {name} {phase}.backlog_peak")
            _integer(row.get("backlog_at_end"), f"run {name} {phase}.backlog_at_end", 0, peak)
            _integer(row.get("cumulative_overrun_at_end"), f"run {name} {phase}.cumulative_overrun")
            _integer(row.get("loss_adjusted_pressure_peak"), f"run {name} {phase}.pressure_peak")
    elif workload == "timing_pair":
        timing_keys = {"candidate", "test", "seed", "trace_sha256", "pair_count", "evaluable_pairs",
                       "dropped_pairs", "censored_pairs", "mean_pair_timing_error_cycles",
                       "p95_pair_timing_error_cycles", "p99_pair_timing_error_cycles",
                       "max_pair_timing_error_cycles"}
        if set(doc) != timing_keys:
            raise ReceiptError(f"run {name} timing-pair analyzer schema/accounting is incomplete")
        values = [doc.get(key) for key in ("pair_count", "evaluable_pairs", "dropped_pairs", "censored_pairs")]
        if (any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values) or
                values[0] != 128 or values[0] != values[1] + values[2] + values[3] or values[3] != 0):
            raise ReceiptError(f"run {name} timing-pair analyzer schema/accounting is incomplete")
        metrics = [_number(doc.get(key), f"run {name} {key}", nullable=values[1] == 0) for key in (
            "mean_pair_timing_error_cycles", "p95_pair_timing_error_cycles",
            "p99_pair_timing_error_cycles", "max_pair_timing_error_cycles")]
        if values[1] and not (metrics[1] <= metrics[2] <= metrics[3]):
            raise ReceiptError(f"run {name} timing-pair percentile order mismatch")


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
                "candidate_manifest", "tools", "simulator"}
    if set(doc) != required or doc["schema_version"] != 3 or doc["suite"] != suite:
        raise ReceiptError("attempt manifest schema/suite mismatch")
    candidate = _string(doc["candidate"], "attempt candidate")
    attempt_id = _string(doc["attempt_id"], "attempt_id")
    resolved = artifact_root.resolve()
    if (resolved.name != attempt_id or resolved.parent.name != candidate or
            resolved.parent.parent.name != suite or resolved.parent.parent.parent.name != "attempts"):
        raise ReceiptError("artifact root is not the declared unique attempt namespace")

    candidate_spec = doc["candidate_manifest"]
    if not isinstance(candidate_spec, dict) or set(candidate_spec) != {"path", "sha256", "bundle_files"}:
        raise ReceiptError("attempt candidate_manifest schema mismatch")
    candidate_path = _contained(artifact_root, candidate_spec["path"], "candidate manifest path")
    candidate_doc, candidate_bytes, candidate_info = _read_json(candidate_path, "candidate manifest")
    _claim_inode(candidate_info, candidate_path, "candidate manifest", inodes)
    candidate_sha = _sha(candidate_spec["sha256"], "candidate manifest sha256")
    if _sha256(candidate_bytes) != candidate_sha:
        raise ReceiptError("candidate manifest identity mismatch")
    _validate_candidate_manifest(candidate_doc, candidate)
    bundle_files = candidate_spec.get("bundle_files")
    if not isinstance(bundle_files, list) or len(bundle_files) != len(candidate_doc["filelist"]):
        raise ReceiptError("attempt candidate bundle file cardinality mismatch")
    for position, (bundle_spec, declared) in enumerate(zip(bundle_files, candidate_doc["filelist"])):
        if (not isinstance(bundle_spec, dict) or set(bundle_spec) != {"logical_path", "path", "sha256"} or
                bundle_spec.get("logical_path") != declared["path"] or
                bundle_spec.get("sha256") != declared["sha256"]):
            raise ReceiptError(f"attempt candidate bundle file[{position}] schema mismatch")
        bundle_path = _contained(artifact_root, bundle_spec["path"],
                                 f"candidate bundle file[{position}] path")
        bundle_bytes, bundle_info = _read_bytes_stable(bundle_path, f"candidate bundle file[{position}]")
        _claim_inode(bundle_info, bundle_path, f"candidate bundle file[{position}]", inodes)
        if not bundle_bytes or _sha256(bundle_bytes) != declared["sha256"]:
            raise ReceiptError(f"candidate bundle file[{position}] identity mismatch")

    tools_doc = doc["tools"]
    if not isinstance(tools_doc, dict) or not {"runner", "generator"}.issubset(tools_doc):
        raise ReceiptError("attempt tool identities must include runner and generator")
    tools = {}
    for key, tool_spec in tools_doc.items():
        required_tool = {"identity", "entrypoint", "dependencies", "dependency_closure", "bundle_sha256"}
        if (not isinstance(tool_spec, dict) or set(tool_spec) != required_tool or
                tool_spec.get("identity") != key or tool_spec.get("dependency_closure") != "declared_complete"):
            raise ReceiptError(f"attempt tool identity schema mismatch for {key}")
        dependencies = tool_spec.get("dependencies")
        if not isinstance(dependencies, list):
            raise ReceiptError(f"attempt tool {key} dependencies must be an array")
        file_specs = [("entrypoint", tool_spec.get("entrypoint"))] + [
            (f"dependency[{position}]", spec) for position, spec in enumerate(dependencies)]
        normalized_files, logical_names = [], set()
        for role, file_spec in file_specs:
            if not isinstance(file_spec, dict) or set(file_spec) != {"logical_name", "path", "sha256"}:
                raise ReceiptError(f"attempt tool {key} {role} schema mismatch")
            logical = _string(file_spec.get("logical_name"), f"tool {key} {role} logical_name")
            if Path(logical).name != logical or logical in logical_names:
                raise ReceiptError(f"attempt tool {key} has invalid/duplicate logical filename")
            logical_names.add(logical)
            tool_path = _contained(artifact_root, file_spec["path"], f"tool {key} {role} path")
            tool_bytes, tool_info = _read_bytes_stable(tool_path, f"tool {key} {role}")
            _claim_inode(tool_info, tool_path, f"tool {key} {role}", inodes)
            tool_sha = _sha(file_spec["sha256"], f"tool {key} {role} sha256")
            if not tool_bytes or _sha256(tool_bytes) != tool_sha:
                raise ReceiptError(f"tool {key} identity mismatch")
            normalized_files.append({"logical_name": logical, "sha256": tool_sha})
        identity_payload = {"identity": key, "entrypoint": normalized_files[0],
                            "dependencies": normalized_files[1:],
                            "dependency_closure": "declared_complete"}
        bundle_sha = _sha(tool_spec.get("bundle_sha256"), f"tool {key} bundle_sha256")
        if bundle_sha != _canonical_sha(identity_payload):
            raise ReceiptError(f"tool {key} bundle identity mismatch")
        tools[key] = {"identity": key, "bundle_sha256": bundle_sha}

    simulator_spec = doc.get("simulator")
    if not isinstance(simulator_spec, dict) or set(simulator_spec) != {"identity", "executable", "version"}:
        raise ReceiptError("attempt simulator identity schema mismatch")
    simulator = {"identity": _string(simulator_spec.get("identity"), "simulator identity")}
    for key in ("executable", "version"):
        spec = simulator_spec.get(key)
        if not isinstance(spec, dict) or set(spec) != {"path", "sha256"}:
            raise ReceiptError(f"attempt simulator {key} schema mismatch")
        path = _contained(artifact_root, spec["path"], f"simulator {key} path")
        contents, info = _read_bytes_stable(path, f"simulator {key}")
        _claim_inode(info, path, f"simulator {key}", inodes)
        digest = _sha(spec.get("sha256"), f"simulator {key} sha256")
        if not contents or _sha256(contents) != digest:
            raise ReceiptError(f"simulator {key} identity mismatch")
        simulator[f"{key}_sha256"] = digest
    return doc, payload, candidate, candidate_sha, tools, simulator


def validate_official_generation(generation_index_path: Path, suite_manifest_path: Path,
                                 suite: str) -> dict[str, Any]:
    """Validate exact official v4 trace generation before any runner executes."""
    if suite not in official.SUITES:
        raise ReceiptError(f"unknown official suite: {suite}")
    frozen, names = official.SUITES[suite], tuple(official.SUITES[suite]["names"])
    manifest, manifest_bytes, _ = _read_json(suite_manifest_path, "official suite manifest")
    if (suite_manifest_path.name != frozen["manifest_name"] or
            _sha256(manifest_bytes) != frozen["manifest_sha256"] or manifest.get("schema_version") != 1):
        raise ReceiptError("official suite manifest identity mismatch")
    manifest_runs = _named(manifest.get("runs"), "official suite manifest.runs")
    _exact(manifest_runs, names, "official suite run set")
    index, index_bytes, _ = _read_json(generation_index_path, "generation index")
    if (set(index) != {"schema_version", "generator_version", "input_manifest", "runs"} or
            index["schema_version"] != 1 or index["generator_version"] != official.GENERATOR_VERSION or
            index["input_manifest"] != frozen["manifest_name"]):
        raise ReceiptError("generation index schema/provenance mismatch")
    indexed = _named(index["runs"], "generation index.runs", embedded=True)
    _exact(indexed, names, "generation index run set")
    trace_root, runs = generation_index_path.parent, []
    for name in names:
        metadata = indexed[name]
        canonical_run = _canonical_run(manifest_runs[name])
        if (metadata.get("schema_version") != 1 or
                metadata.get("generator_version") != official.GENERATOR_VERSION or
                metadata.get("run") != canonical_run):
            raise ReceiptError(f"run {name} generated manifest contract mismatch")
        expected_trace = official.TRACE_SHA256[name]
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
        trace_path = _contained(trace_root, metadata["trace_file"], f"run {name} trace")
        trace_bytes, _ = _read_bytes_stable(trace_path, f"run {name} trace")
        if _sha256(trace_bytes) != expected_trace:
            raise ReceiptError(f"run {name} trace SHA256 mismatch")
        runs.append({"name": name, "workload": canonical_run["workload"], "metadata": metadata,
                     "trace": trace_path, "run_manifest": run_manifest_path})
    return {"suite": suite, "names": names, "runs": runs, "manifest_sha256": _sha256(manifest_bytes),
            "generation_index_sha256": _sha256(index_bytes)}


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
    attempt_doc, attempt_bytes, candidate, candidate_manifest_sha, tools, simulator = _load_attempt(
        artifact_root, artifacts, suite, inodes)
    if artifacts["candidate"] != candidate:
        raise ReceiptError("artifact and attempt candidate mismatch")
    artifact_runs = _named(artifacts["runs"], "artifact manifest.runs")
    _exact(artifact_runs, names, "artifact manifest run set")

    required_tool_names = {"runner", "generator"} | {indexed[name]["run"]["workload"] for name in names
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
        bound_tools = {key: tools[key] for key in ("runner", "generator")}
        if workload in ANALYZER_WORKLOADS:
            bound_tools[workload] = tools[workload]
        expected_sidecar = {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "suite": suite, "attempt_id": attempt_doc["attempt_id"], "candidate": candidate,
            "run_name": name, "trace_sha256": expected_trace,
            "run_manifest_sha256": run_manifest_sha,
            "candidate_manifest_sha256": candidate_manifest_sha,
            "tools": bound_tools, "simulator": simulator, "result_sha256": result_sha,
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
        "simulator": simulator,
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
