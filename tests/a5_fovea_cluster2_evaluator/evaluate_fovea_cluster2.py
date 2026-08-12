#!/usr/bin/env python3
"""Fail-closed, candidate-neutral Fovea versus Cluster2 evaluator.

The evaluator consumes exact generator-v4 traces and candidate-owned result
bundles.  It recomputes every outcome metric from per-event records; summary
CSVs and evidence manifests are provenance/accounting cross-checks, never the
source of a favorable result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "a5_fovea_cluster2_evaluation_v1"
EVIDENCE_SCHEMA = "a5_fovea_cluster2_evidence_v1"
RESET_SCHEMA = "a5_native_reset_evidence_v1"
POLICY_SCHEMA = "a5_native_row_policy_evidence_v1"
GENERATOR_SHA256 = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
OFFICIAL = {
    "full50": {
        "manifest": "manifest.neutrality-n16.json",
        "sha256": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
        "count": 50,
    },
    "capacity22": {
        "manifest": "manifest.multilane-n16.json",
        "sha256": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
        "count": 22,
    },
}
SUMMARY_COLUMNS = {
    "candidate", "test", "seed", "load_pct", "stim_cycles", "generated",
    "source_overrun", "accepted", "delivered", "errors", "total_cycles",
    "throughput", "measurement_delivered", "measurement_cycles",
}
EVENT_COLUMNS = {
    "candidate", "test", "seed", "load_pct", "tb_only_event_id",
    "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
    "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
}
RESET_TRUE = (
    "reset_after_complete_drain", "direct_native_valid_observed_during_reset",
    "normalized_ready_quiet", "normalized_retire_quiet", "no_stale_completion",
    "no_phantom_completion", "no_loss", "no_duplicate", "negative_control_caught",
)
VALID_STATES = {"source_overrun", "pending", "accepted", "delivered"}
IDEAL_ROW_SHARE = (1 / 12, 5 / 12, 5 / 12, 1 / 12)


class EvaluationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise EvaluationError(f"artifact is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"JSON root must be an object: {path}")
    return value


def integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise EvaluationError(f"{where} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(f"{where} must be an integer") from exc
    if str(value).strip() not in (str(parsed), f"+{parsed}"):
        raise EvaluationError(f"{where} is not a canonical integer")
    if parsed < minimum:
        raise EvaluationError(f"{where} must be >= {minimum}")
    return parsed


def finite(value: Any, where: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(f"{where} must be numeric") from exc
    if not math.isfinite(parsed):
        raise EvaluationError(f"{where} must be finite")
    return parsed


def nearest_rank(values: Iterable[int], percentile: int) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[rank - 1]


def jain(values: Iterable[float]) -> float | None:
    data = list(values)
    if not data:
        return None
    square_sum = sum(value * value for value in data)
    if square_sum == 0:
        return None
    return sum(data) ** 2 / (len(data) * square_sum)


def mean(values: Iterable[float]) -> float | None:
    data = list(values)
    return sum(data) / len(data) if data else None


def artifact(root: Path, descriptor: Any, where: str) -> Path:
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
        raise EvaluationError(f"{where} must contain only path and sha256")
    relative = Path(str(descriptor["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise EvaluationError(f"{where}.path must stay inside evidence root")
    path = root / relative
    observed = sha256(path)
    if observed != descriptor["sha256"]:
        raise EvaluationError(f"{where} SHA mismatch")
    return path


@dataclass(frozen=True)
class OfficialRun:
    suite: str
    name: str
    workload: str
    seed: int
    load: Decimal
    stim_cycles: int
    trace_sha256: str
    trace: tuple[dict[str, Any], ...]
    permutation: tuple[int, ...]


def materialize_official(generator: Path, manifest_root: Path,
                         temporary: Path) -> dict[str, dict[str, OfficialRun]]:
    if sha256(generator) != GENERATOR_SHA256:
        raise EvaluationError("official generator SHA mismatch")
    suites: dict[str, dict[str, OfficialRun]] = {}
    for suite, contract in OFFICIAL.items():
        manifest = manifest_root / contract["manifest"]
        if sha256(manifest) != contract["sha256"]:
            raise EvaluationError(f"{suite} manifest SHA mismatch")
        output = temporary / suite
        result = subprocess.run(
            [sys.executable, str(generator), "--manifest", str(manifest),
             "--output-dir", str(output)], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        if result.returncode:
            raise EvaluationError(f"official {suite} generation failed: {result.stdout[-2000:]}")
        index_path = output / "generation-index.json"
        index = load_json(index_path)
        rows = index.get("runs")
        if index.get("generator_version") != "4.0" or not isinstance(rows, list):
            raise EvaluationError(f"{suite} generation-index schema mismatch")
        if len(rows) != contract["count"]:
            raise EvaluationError(f"{suite} cardinality mismatch")
        result_runs: dict[str, OfficialRun] = {}
        for position, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("run"), dict):
                raise EvaluationError(f"{suite} generation row {position} malformed")
            run = row["run"]
            name = run.get("name")
            if not isinstance(name, str) or name in result_runs:
                raise EvaluationError(f"{suite} duplicate/invalid run name")
            trace_path = output / str(row.get("trace_file"))
            if sha256(trace_path) != row.get("trace_sha256"):
                raise EvaluationError(f"{suite}/{name} generated trace SHA mismatch")
            events: list[dict[str, Any]] = []
            for line_number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EvaluationError(f"{suite}/{name} trace line {line_number}: {exc}") from exc
                if not isinstance(event, dict):
                    raise EvaluationError(f"{suite}/{name} trace event must be an object")
                events.append(event)
            if len(events) != row.get("event_count"):
                raise EvaluationError(f"{suite}/{name} trace event count mismatch")
            expected_ids = list(range(len(events)))
            if [event.get("tb_only_event_id") for event in events] != expected_ids:
                raise EvaluationError(f"{suite}/{name} trace IDs are not contiguous")
            permutation = row.get("logical_source_permutation")
            if sorted(permutation or []) != list(range(16)):
                raise EvaluationError(f"{suite}/{name} invalid source permutation")
            result_runs[name] = OfficialRun(
                suite=suite, name=name, workload=str(run.get("workload")),
                seed=integer(run.get("seed"), f"{suite}/{name}.seed"),
                load=Decimal(str(run.get("load"))),
                stim_cycles=integer(run.get("stim_cycles"), f"{suite}/{name}.stim_cycles", minimum=1),
                trace_sha256=str(row["trace_sha256"]), trace=tuple(events),
                permutation=tuple(permutation),
            )
        suites[suite] = result_runs
    cap_names = list(suites["capacity22"])
    full_names = list(suites["full50"])
    if not set(cap_names) < set(full_names):
        raise EvaluationError("capacity22 must be a strict subset of full50")
    for name in cap_names:
        cap = suites["capacity22"][name]
        full = suites["full50"][name]
        if (cap.trace_sha256, cap.workload, cap.seed, cap.load, cap.stim_cycles) != (
                full.trace_sha256, full.workload, full.seed, full.load, full.stim_cycles):
            raise EvaluationError(f"capacity22 run differs from full50: {name}")
    return suites


@dataclass
class RunMetric:
    name: str
    workload: str
    load: float
    generated: int
    accepted: int
    delivered: int
    overrun: int
    measurement_delivered: int
    measurement_cycles: int
    throughput: float
    overrun_ratio: float
    acceptance_ratio: float
    p50: int | None
    p95: int | None
    p99: int | None
    maximum: int | None
    max_wait: int
    fairness: float | None
    min_source_ratio: float | None
    pair_relations: dict[int, dict[str, Any]]


def read_one_csv(path: Path, required: set[str], where: str) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise EvaluationError(f"{where} missing required columns")
        rows = list(reader)
    if len(rows) != 1:
        raise EvaluationError(f"{where} must contain exactly one data row")
    return rows[0]


def read_events(path: Path, where: str) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not EVENT_COLUMNS <= set(reader.fieldnames):
            raise EvaluationError(f"{where} missing required event columns")
        return list(reader)


def validate_run(candidate_id: str, official: OfficialRun, summary_path: Path,
                 events_path: Path) -> RunMetric:
    where = f"{candidate_id}/{official.suite}/{official.name}"
    summary = read_one_csv(summary_path, SUMMARY_COLUMNS, f"{where} summary")
    rows = read_events(events_path, f"{where} events")
    if len(rows) != len(official.trace):
        raise EvaluationError(f"{where} does not contain one event row per generated event")
    expected_load_pct = (int(official.load * 1000) + 5) // 10
    scalar_summary = {
        "candidate": candidate_id, "test": official.name,
        "seed": str(official.seed), "load_pct": str(expected_load_pct),
        "stim_cycles": str(official.stim_cycles), "measurement_cycles": str(official.stim_cycles),
        "generated": str(len(official.trace)), "errors": "0",
    }
    for key, expected in scalar_summary.items():
        if summary.get(key) != expected:
            raise EvaluationError(f"{where} summary {key} mismatch")

    states = defaultdict(int)
    generated_by_source = [0] * 16
    accepted_by_source = [0] * 16
    delivered_by_source = [0] * 16
    last_accept = [-1] * 16
    last_delivery = [-1] * 16
    latencies: list[int] = []
    waits: list[int] = []
    measured = 0
    relation_parts: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    seen_ids: set[int] = set()
    observation_end: int | None = None
    for position, (result, trace) in enumerate(zip(rows, official.trace)):
        event_id = integer(result.get("tb_only_event_id"), f"{where}.event_id")
        if event_id in seen_ids or event_id != position:
            raise EvaluationError(f"{where} duplicate/reordered event ID {event_id}")
        seen_ids.add(event_id)
        source = integer(result.get("logical_source"), f"{where}/{event_id}.source")
        occurrence = integer(result.get("occurrence_cycle"), f"{where}/{event_id}.occurrence")
        if source >= 16 or result.get("source_count") != "16":
            raise EvaluationError(f"{where}/{event_id} source range/count mismatch")
        if (source != trace.get("logical_source") or occurrence != trace.get("occurrence_cycle") or
                result.get("candidate") != candidate_id or result.get("test") != official.name or
                result.get("seed") != str(official.seed) or result.get("load_pct") != str(expected_load_pct)):
            raise EvaluationError(f"{where}/{event_id} trace/result provenance mismatch")
        end = integer(result.get("observation_end_cycle"), f"{where}/{event_id}.observation_end")
        if observation_end is None:
            observation_end = end
        elif end != observation_end:
            raise EvaluationError(f"{where} observation end is inconsistent")
        state = result.get("event_state")
        if state not in VALID_STATES:
            raise EvaluationError(f"{where}/{event_id} invalid state")
        states[state] += 1
        generated_by_source[source] += 1
        accept_text = result.get("accept_cycle", "")
        delivery_text = result.get("delivery_cycle", "")
        if state == "source_overrun":
            if accept_text or delivery_text:
                raise EvaluationError(f"{where}/{event_id} overrun has transport cycles")
            accept = delivery = None
        elif state == "delivered":
            accept = integer(accept_text, f"{where}/{event_id}.accept")
            delivery = integer(delivery_text, f"{where}/{event_id}.delivery")
            if accept < occurrence or delivery < accept or delivery > end:
                raise EvaluationError(f"{where}/{event_id} impossible transport chronology")
            if accept < last_accept[source] or delivery < last_delivery[source]:
                raise EvaluationError(f"{where}/{event_id} per-source order violation")
            last_accept[source], last_delivery[source] = accept, delivery
            accepted_by_source[source] += 1
            delivered_by_source[source] += 1
            latencies.append(delivery - occurrence)
            waits.append(accept - occurrence)
            if delivery < official.stim_cycles:
                measured += 1
        else:
            raise EvaluationError(f"{where}/{event_id} pending/accepted remains after claimed drain")
        relation_id = trace.get("relation_id")
        relation_role = trace.get("relation_role")
        if relation_id is not None:
            if relation_role not in ("a", "b") or relation_role in relation_parts[int(relation_id)]:
                raise EvaluationError(f"{where}/{event_id} malformed pair relation")
            relation_parts[int(relation_id)][relation_role] = {
                "state": state, "latency": (delivery - occurrence) if delivery is not None else None,
                "source": source,
            }

    generated = len(rows)
    overrun = states["source_overrun"]
    accepted = states["delivered"]
    delivered = states["delivered"]
    expected_summary = {
        "source_overrun": overrun, "accepted": accepted, "delivered": delivered,
        "measurement_delivered": measured,
    }
    for key, expected in expected_summary.items():
        if integer(summary.get(key), f"{where}.summary.{key}") != expected:
            raise EvaluationError(f"{where} summary/event {key} mismatch")
    if generated != accepted + overrun:
        raise EvaluationError(f"{where} generated accounting does not close")
    throughput = measured / official.stim_cycles
    if not math.isclose(finite(summary.get("throughput"), f"{where}.throughput"), throughput,
                        rel_tol=1e-6, abs_tol=1e-6):
        raise EvaluationError(f"{where} fixed-window throughput mismatch")
    ratios = [delivered_by_source[i] / generated_by_source[i]
              for i in range(16) if generated_by_source[i]]
    pair_relations: dict[int, dict[str, Any]] = {}
    for relation, parts in relation_parts.items():
        if set(parts) != {"a", "b"}:
            raise EvaluationError(f"{where} incomplete pair relation {relation}")
        complete = parts["a"]["state"] == parts["b"]["state"] == "delivered"
        pair_relations[relation] = {
            "complete": complete,
            "max_latency": max(parts["a"]["latency"], parts["b"]["latency"])
            if complete else None,
        }
    return RunMetric(
        name=official.name, workload=official.workload, load=float(official.load),
        generated=generated, accepted=accepted, delivered=delivered, overrun=overrun,
        measurement_delivered=measured, measurement_cycles=official.stim_cycles,
        throughput=throughput, overrun_ratio=overrun / generated if generated else 0.0,
        acceptance_ratio=accepted / generated if generated else 1.0,
        p50=nearest_rank(latencies, 50), p95=nearest_rank(latencies, 95),
        p99=nearest_rank(latencies, 99), maximum=max(latencies) if latencies else None,
        max_wait=max(waits) if waits else 0, fairness=jain(ratios),
        min_source_ratio=min(ratios) if ratios else None, pair_relations=pair_relations,
    )


def validate_reset(candidate: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema") != RESET_SCHEMA or document.get("candidate_id") != candidate["id"]:
        raise EvaluationError(f"{candidate['id']} reset identity/schema mismatch")
    if document.get("source_sha256") != candidate["source_sha256"]:
        raise EvaluationError(f"{candidate['id']} reset source provenance mismatch")
    for key in RESET_TRUE:
        if document.get(key) is not True:
            raise EvaluationError(f"{candidate['id']} reset assertion failed: {key}")
    generated = integer(document.get("generated"), "reset.generated", minimum=1)
    accepted = integer(document.get("accepted"), "reset.accepted", minimum=1)
    delivered = integer(document.get("delivered"), "reset.delivered", minimum=1)
    errors = integer(document.get("errors"), "reset.errors")
    quiet = integer(document.get("quiet_cycles"), "reset.quiet_cycles", minimum=8)
    if generated != accepted or accepted != delivered or errors != 0:
        raise EvaluationError(f"{candidate['id']} reset accounting does not close")
    return {"pass": True, "events": delivered, "quiet_cycles": quiet}


def validate_policy(candidate: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema") != POLICY_SCHEMA or document.get("candidate_id") != candidate["id"]:
        raise EvaluationError(f"{candidate['id']} policy identity/schema mismatch")
    if document.get("source_sha256") != candidate["source_sha256"]:
        raise EvaluationError(f"{candidate['id']} policy source provenance mismatch")
    if document.get("stimulus") != "continuous_all_16_sources":
        raise EvaluationError(f"{candidate['id']} policy stimulus mismatch")
    cycles = integer(document.get("cycles"), "policy.cycles", minimum=12)
    counts = document.get("row_service_events")
    if not isinstance(counts, list) or len(counts) != 4:
        raise EvaluationError(f"{candidate['id']} policy must report four row counts")
    counts = [integer(value, f"policy.row[{index}]") for index, value in enumerate(counts)]
    if sum(counts) == 0 or any(value > cycles * int(candidate["retire_lanes"]) for value in counts):
        raise EvaluationError(f"{candidate['id']} policy counts are impossible")
    shares = [value / sum(counts) for value in counts]
    maximum_error = max(abs(actual - ideal) for actual, ideal in zip(shares, IDEAL_ROW_SHARE))
    preserves = maximum_error <= 0.01
    if candidate["architecture"] == "fovea" and not preserves:
        raise EvaluationError("fovea failed its required 1:5:5:1 native-policy control")
    return {
        "row_service_events": counts, "row_service_share": shares,
        "ideal_1_5_5_1_share": list(IDEAL_ROW_SHARE),
        "max_absolute_share_error": maximum_error,
        "preserves_1_5_5_1": preserves,
        "classification": "preserved" if preserves else "transformed_by_native_cluster2",
    }


def validate_candidate(root: Path, official: dict[str, dict[str, OfficialRun]]) -> dict[str, Any]:
    evidence_path = root / "evidence.json"
    evidence_sha = sha256(evidence_path)
    evidence = load_json(evidence_path)
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise EvaluationError(f"{root} evidence schema mismatch")
    candidate = evidence.get("candidate")
    if not isinstance(candidate, dict):
        raise EvaluationError(f"{root} missing candidate identity")
    required = {"id", "architecture", "top", "source_sha256", "binding_sha256", "runner_sha256",
                "simulator_sha256", "source_count", "retire_lanes", "address_only"}
    if not required <= set(candidate):
        raise EvaluationError(f"{root} incomplete candidate identity")
    if candidate["architecture"] not in ("fovea", "cluster2"):
        raise EvaluationError(f"{root} invalid architecture")
    expected_top = {
        "fovea": "aer_tx16_trad_rowcol_fovea",
        "cluster2": "aer_tx16_trad_rowcol_fovea_cluster2",
    }[candidate["architecture"]]
    if candidate["top"] != expected_top:
        raise EvaluationError(f"{root} native top must be {expected_top}")
    if candidate["source_count"] != 16 or candidate["address_only"] is not True:
        raise EvaluationError(f"{root} is not N16 address-only evidence")
    retire_lanes = integer(candidate["retire_lanes"], "candidate.retire_lanes", minimum=1)
    expected_lanes = 1 if candidate["architecture"] == "fovea" else 8
    if retire_lanes != expected_lanes:
        raise EvaluationError(
            f"{root} native normalized retire lanes must be {expected_lanes}"
        )
    for key in ("source_sha256", "binding_sha256", "runner_sha256", "simulator_sha256"):
        if not isinstance(candidate[key], str) or len(candidate[key]) != 64:
            raise EvaluationError(f"{root} invalid {key}")
    identities = evidence.get("identity_artifacts")
    identity_keys = {
        "source_bundle": "source_sha256", "binding": "binding_sha256",
        "runner": "runner_sha256", "simulator": "simulator_sha256",
    }
    if not isinstance(identities, dict) or set(identities) != set(identity_keys):
        raise EvaluationError(f"{root} incomplete identity artifact bundle")
    for label, candidate_key in identity_keys.items():
        path = artifact(root, identities[label], f"identity_artifacts.{label}")
        if sha256(path) != candidate[candidate_key]:
            raise EvaluationError(f"{root} {label} does not bind candidate identity")
    suites = evidence.get("suites")
    if not isinstance(suites, dict) or set(suites) != set(OFFICIAL):
        raise EvaluationError(f"{root} must contain exactly full50 and capacity22")
    metrics: dict[str, dict[str, RunMetric]] = {}
    overlap_artifacts: dict[str, tuple[str, str]] = {}
    stable_artifacts: list[tuple[Path, str]] = []
    for suite in OFFICIAL:
        suite_doc = suites[suite]
        if not isinstance(suite_doc, dict) or suite_doc.get("manifest_sha256") != OFFICIAL[suite]["sha256"]:
            raise EvaluationError(f"{root}/{suite} manifest provenance mismatch")
        run_docs = suite_doc.get("runs")
        if not isinstance(run_docs, list) or len(run_docs) != OFFICIAL[suite]["count"]:
            raise EvaluationError(f"{root}/{suite} result cardinality mismatch")
        names = [item.get("name") for item in run_docs if isinstance(item, dict)]
        if names != list(official[suite]) or len(set(names)) != len(names):
            raise EvaluationError(f"{root}/{suite} names/order/duplicates mismatch")
        suite_metrics: dict[str, RunMetric] = {}
        for item in run_docs:
            name = item["name"]
            if item.get("trace_sha256") != official[suite][name].trace_sha256:
                raise EvaluationError(f"{root}/{suite}/{name} trace provenance mismatch")
            summary = artifact(root, item.get("summary"), f"{suite}/{name}.summary")
            events = artifact(root, item.get("events"), f"{suite}/{name}.events")
            stable_artifacts.extend((
                (summary, item["summary"]["sha256"]),
                (events, item["events"]["sha256"]),
            ))
            suite_metrics[name] = validate_run(candidate["id"], official[suite][name], summary, events)
            pair = (sha256(summary), sha256(events))
            if name in overlap_artifacts and overlap_artifacts[name] != pair:
                raise EvaluationError(f"{root} capacity22 overlap differs from full50: {name}")
            overlap_artifacts[name] = pair
        metrics[suite] = suite_metrics
    reset_path = artifact(root, evidence.get("reset"), "reset")
    policy_path = artifact(root, evidence.get("policy"), "policy")
    reset = validate_reset(candidate, load_json(reset_path))
    policy = validate_policy(candidate, load_json(policy_path))
    stable_artifacts.extend((
        (reset_path, evidence["reset"]["sha256"]),
        (policy_path, evidence["policy"]["sha256"]),
    ))
    if sha256(evidence_path) != evidence_sha:
        raise EvaluationError(f"{root} evidence changed during evaluation")
    for path, expected in stable_artifacts:
        if sha256(path) != expected:
            raise EvaluationError(f"{path} changed during evaluation")
    return {
        "root": root, "evidence_sha256": evidence_sha, "candidate": candidate,
        "runs": metrics, "reset": reset, "policy": policy,
    }


def aggregate_runs(runs: Iterable[RunMetric]) -> dict[str, Any]:
    data = list(runs)
    generated = sum(run.generated for run in data)
    overrun = sum(run.overrun for run in data)
    measurement_cycles = sum(run.measurement_cycles for run in data)
    measurement_delivered = sum(run.measurement_delivered for run in data)
    p99_values = [run.p99 for run in data if run.p99 is not None]
    return {
        "runs": len(data), "generated": generated,
        "accepted": sum(run.accepted for run in data),
        "delivered": sum(run.delivered for run in data), "source_overrun": overrun,
        "measurement_delivered": measurement_delivered,
        "measurement_cycles": measurement_cycles,
        "fixed_window_event_per_cycle": measurement_delivered / measurement_cycles,
        "overrun_ratio": overrun / generated if generated else 0.0,
        "mean_run_p99_e2e_latency": mean(p99_values),
        "worst_run_p99_e2e_latency": max(p99_values) if p99_values else None,
        "max_e2e_latency": max((run.maximum or 0) for run in data),
        "max_request_wait": max(run.max_wait for run in data),
        "mean_demand_normalized_fairness": mean(
            run.fairness for run in data if run.fairness is not None),
        "worst_demand_normalized_fairness": min(
            (run.fairness for run in data if run.fairness is not None), default=None),
        "min_source_delivery_ratio": min(
            (run.min_source_ratio for run in data if run.min_source_ratio is not None), default=None),
    }


def capacity_curve(runs: dict[str, RunMetric]) -> dict[str, Any]:
    loads: dict[float, list[RunMetric]] = defaultdict(list)
    for run in runs.values():
        if run.workload == "uniform":
            loads[run.load].append(run)
    rows = []
    for load in sorted(loads):
        group = loads[load]
        generated = sum(run.generated for run in group)
        measured = sum(run.measurement_delivered for run in group)
        cycles = sum(run.measurement_cycles for run in group)
        overrun = sum(run.overrun for run in group)
        completion = measured / generated if generated else 1.0
        rows.append({
            "load": load, "seeds": len(group), "fixed_window_event_per_cycle": measured / cycles,
            "window_completion_ratio": completion, "overrun_ratio": overrun / generated,
        })
    knee = next((row["load"] for row in rows
                 if row["window_completion_ratio"] < 0.95 or row["overrun_ratio"] > 0.05), None)
    return {"definition": "first_uniform_load_with_completion_lt_0.95_or_overrun_gt_0.05",
            "knee_load": knee, "curve": rows}


def pairwise(runs: dict[str, RunMetric]) -> dict[str, Any]:
    identity = runs.get("pairwise_contention_identity")
    affine = runs.get("pairwise_contention_affine")
    if identity is None or affine is None:
        raise EvaluationError("full50 pairwise identity/affine runs are required")
    if set(identity.pair_relations) != set(range(240)) or set(affine.pair_relations) != set(range(240)):
        raise EvaluationError("pairwise relation cardinality must be exactly 240 per mapping")
    if any(identity.pair_relations[key]["complete"] != affine.pair_relations[key]["complete"]
           for key in identity.pair_relations):
        churn = sum(identity.pair_relations[key]["complete"] != affine.pair_relations[key]["complete"]
                    for key in identity.pair_relations)
    else:
        churn = 0
    def one(run: RunMetric) -> dict[str, Any]:
        complete = [value for value in run.pair_relations.values() if value["complete"]]
        return {"relations": 240, "complete_relations": len(complete),
                "completion_ratio": len(complete) / 240,
                "p99_pair_max_latency": nearest_rank(
                    [value["max_latency"] for value in complete], 99)}
    left, right = one(identity), one(affine)
    return {"identity": left, "affine": right, "completion_delta": right["completion_ratio"] - left["completion_ratio"],
            "p99_latency_delta": ((right["p99_pair_max_latency"] or 0) -
                                   (left["p99_pair_max_latency"] or 0)),
            "relation_completion_churn": churn}


def candidate_metrics(validated: dict[str, Any]) -> dict[str, Any]:
    full = validated["runs"]["full50"]
    capacity = validated["runs"]["capacity22"]
    families = {}
    for label, workloads in {
        "spatial": {"matched_spatial"},
        "moving": {"moving_hotspot", "rotating_victim"},
        "fairness_stress": {"elephant_mouse"},
    }.items():
        families[label] = aggregate_runs(run for run in full.values() if run.workload in workloads)
    return {
        "correctness": {"full50": True, "capacity22": True, "reset": validated["reset"]},
        "full50": aggregate_runs(full.values()), "capacity22": aggregate_runs(capacity.values()),
        "capacity": capacity_curve(full), "families": families,
        "pairwise_mapping": pairwise(full), "row_policy": validated["policy"],
    }


def pareto(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # Every dimension is transformed so larger is better.  No hidden weighting.
    vectors = {}
    for candidate_id, value in metrics.items():
        pair = value["pairwise_mapping"]
        curve = value["capacity"]["curve"]
        observed_max_load = max(row["load"] for row in curve)
        knee_score = value["capacity"]["knee_load"]
        # No knee inside the frozen range is better than a knee at its maximum,
        # but must remain finite and JSON-representable.
        if knee_score is None:
            knee_score = observed_max_load + 0.001
        vectors[candidate_id] = {
            "correctness_hard_gate": 1.0,
            "reset_hard_gate": 1.0,
            "capacity_knee": knee_score,
            "capacity22_epc": value["capacity22"]["fixed_window_event_per_cycle"],
            "full50_epc": value["full50"]["fixed_window_event_per_cycle"],
            "negative_full50_overrun": -value["full50"]["overrun_ratio"],
            "negative_full50_p99": -(
                value["full50"]["worst_run_p99_e2e_latency"]
                if value["full50"]["worst_run_p99_e2e_latency"] is not None
                else 1_000_000_000
            ),
            "negative_full50_max_wait": -value["full50"]["max_request_wait"],
            "full50_fairness": value["full50"]["worst_demand_normalized_fairness"] or 0.0,
            "full50_min_source_delivery": value["full50"]["min_source_delivery_ratio"] or 0.0,
            "spatial_epc": value["families"]["spatial"]["fixed_window_event_per_cycle"],
            "negative_spatial_overrun": -value["families"]["spatial"]["overrun_ratio"],
            "negative_spatial_p99": -(
                value["families"]["spatial"]["worst_run_p99_e2e_latency"] or 0),
            "spatial_fairness": (
                value["families"]["spatial"]["worst_demand_normalized_fairness"] or 0.0),
            "spatial_min_source_delivery": (
                value["families"]["spatial"]["min_source_delivery_ratio"] or 0.0),
            "moving_epc": value["families"]["moving"]["fixed_window_event_per_cycle"],
            "negative_moving_overrun": -value["families"]["moving"]["overrun_ratio"],
            "negative_moving_p99": -(
                value["families"]["moving"]["worst_run_p99_e2e_latency"] or 0),
            "moving_fairness": (
                value["families"]["moving"]["worst_demand_normalized_fairness"] or 0.0),
            "moving_min_source_delivery": (
                value["families"]["moving"]["min_source_delivery_ratio"] or 0.0),
            "pairwise_worst_completion": min(
                pair["identity"]["completion_ratio"], pair["affine"]["completion_ratio"]),
            "negative_pairwise_p99": -max(
                pair["identity"]["p99_pair_max_latency"] or 0,
                pair["affine"]["p99_pair_max_latency"] or 0),
            "negative_mapping_churn": -pair["relation_completion_churn"],
            "negative_mapping_completion_delta": -abs(pair["completion_delta"]),
            "negative_mapping_p99_delta": -abs(pair["p99_latency_delta"]),
            "negative_policy_error": -value["row_policy"]["max_absolute_share_error"],
        }
    dominated_by: dict[str, list[str]] = {name: [] for name in vectors}
    for name, vector in vectors.items():
        for other, other_vector in vectors.items():
            if name == other:
                continue
            no_worse = all(other_vector[key] >= vector[key] for key in vector)
            strictly_better = any(other_vector[key] > vector[key] for key in vector)
            if no_worse and strictly_better:
                dominated_by[name].append(other)
    return {
        "rule": "unweighted_all-dimensions-no-worse_and_at-least-one-strictly-better",
        "dimensions_larger_is_better": vectors,
        "dominated_by": dominated_by,
        "frontier": sorted(name for name, dominators in dominated_by.items() if not dominators),
    }


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        # The temporary is newly owned by this invocation; a failed publication
        # must not leave something that resembles a completed receipt.
        temporary.unlink(missing_ok=True)
        raise
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def evaluate(fovea_root: Path, cluster2_root: Path, generator: Path,
             manifest_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="a5-w7-official-") as directory:
        official = materialize_official(generator, manifest_root, Path(directory))
        candidates = [validate_candidate(fovea_root, official),
                      validate_candidate(cluster2_root, official)]
    architectures = [item["candidate"]["architecture"] for item in candidates]
    ids = [item["candidate"]["id"] for item in candidates]
    if architectures != ["fovea", "cluster2"] or len(set(ids)) != 2:
        raise EvaluationError("arguments must be distinct fovea then cluster2 evidence")
    metrics = {item["candidate"]["id"]: candidate_metrics(item) for item in candidates}
    return {
        "schema": SCHEMA, "status": "LOCAL_EVALUATION_COMPLETE",
        "scope": "LOCAL_EXACT_GENERATOR_V4_ADDRESS_ONLY",
        "evaluator_sha256": sha256(Path(__file__).resolve()),
        "official": {suite: {"manifest_sha256": contract["sha256"],
                              "run_count": contract["count"]}
                     for suite, contract in OFFICIAL.items()},
        "candidate_evidence": {
            item["candidate"]["id"]: {
                "architecture": item["candidate"]["architecture"],
                "source_sha256": item["candidate"]["source_sha256"],
                "binding_sha256": item["candidate"]["binding_sha256"],
                "runner_sha256": item["candidate"]["runner_sha256"],
                "simulator_sha256": item["candidate"]["simulator_sha256"],
                "retire_lanes": item["candidate"]["retire_lanes"],
                "evidence_sha256": item["evidence_sha256"],
            } for item in candidates
        },
        "metrics": metrics, "pareto": pareto(metrics),
        "decision": "PARETO_ONLY_NO_SCALAR_WINNER",
        "hold_scope": [
            "generic_local_evaluation_not_physical_PPA",
            "always_ready_full50_capacity22_do_not_qualify_backpressure",
            "candidate_evidence_must_be_independently_receipted_before_contest_ranking",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fovea", type=Path, required=True)
    parser.add_argument("--cluster2", type=Path, required=True)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise EvaluationError(f"refusing to overwrite output: {args.output}")
        document = evaluate(args.fovea, args.cluster2, args.generator, args.manifest_root)
        atomic_json(args.output, document)
        print(f"A5_W7_FOVEA_CLUSTER2_EVALUATION_PASS output={args.output}")
        print("HOLD physical_PPA backpressure independent_execution_receipt")
        return 0
    except (EvaluationError, OSError, subprocess.SubprocessError) as exc:
        print(f"A5_W7_FOVEA_CLUSTER2_EVALUATION_FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
