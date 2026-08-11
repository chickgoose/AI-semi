#!/usr/bin/env python3
"""Independent, occurrence-ID matched audit of A4 moving-block W3 evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import random
import runpy
import statistics
import subprocess
import sys
from typing import Any, Iterable


A4_COMMIT = "850fbcfa4ad168b1250223610780f11378f6c391"
A4_MODEL_SHA256 = "fc0d57cbb66c94c1b903ce3e328f962b9ef5345400bab74dbd95fe657116a8bc"
A4_SUMMARY_SHA256 = "b96ceb25f1b01b8bb8c6de3e0ede25cce97764928cf5b576d21cfed005093f39"
EXPECTED_TRACE_MANIFESTS = {
    "full50": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "capacity22": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
A1_COMMIT = "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be"
A1_OFFICIAL_POLICY_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
BOOTSTRAP_SEED = 20260811
BOOTSTRAP_REPLICATES = 10000
PHASE_RANGES = (
    ("sparse", 0, 1024),
    ("near_saturation", 1024, 2048),
    ("overload", 2048, 3072),
    ("post_sparse", 3072, 3584),
    ("drain", 3584, 4096),
)


class AuditError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: Iterable[int | float], pct: float) -> int | float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * pct) - 1)]


def mean(values: Iterable[int | float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def metrics(values: Iterable[int]) -> dict[str, int | float | None]:
    values = list(values)
    return {
        "count": len(values),
        "mean": mean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def jain(values: Iterable[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    denominator = len(values) * sum(value * value for value in values)
    return sum(values) ** 2 / denominator if denominator else 1.0


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise AuditError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def load_a4_model(a4_root: Path):
    model_path = a4_root / "rtl/candidates/a4_moving_block_tree/model.py"
    summary_path = a4_root / (
        "rtl/candidates/a4_moving_block_tree/results/generator_v4_replay_summary.json"
    )
    if git_output(a4_root, "rev-parse", "HEAD") != A4_COMMIT:
        raise AuditError("A4 worktree HEAD is not frozen commit 850fbcf")
    if sha256(model_path) != A4_MODEL_SHA256:
        raise AuditError("A4 model SHA mismatch")
    if sha256(summary_path) != A4_SUMMARY_SHA256:
        raise AuditError("A4 frozen replay summary SHA mismatch")
    spec = importlib.util.spec_from_file_location("a4_w3_frozen_model", model_path)
    if spec is None or spec.loader is None:
        raise AuditError("cannot load frozen A4 model")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, json.loads(summary_path.read_text(encoding="utf-8"))


@dataclass
class ObservedEvent:
    event_id: int
    source: int
    occurrence: int
    relation_id: int | None
    relation_role: str | None
    state: str = "generated"
    accept_cycle: int | None = None
    delivery_cycle: int | None = None

    @property
    def e2e_latency(self) -> int | None:
        if self.delivery_cycle is None:
            return None
        return self.delivery_cycle - self.occurrence + 1


@dataclass
class Replay:
    name: str
    workload: str
    stim_cycles: int
    cycles: int
    output_bubbles: int
    events: dict[int, ObservedEvent]

    @property
    def accepted_ids(self) -> set[int]:
        return {event_id for event_id, event in self.events.items()
                if event.accept_cycle is not None}

    @property
    def accepted(self) -> int:
        return len(self.accepted_ids)

    @property
    def generated(self) -> int:
        return len(self.events)

    @property
    def overrun(self) -> int:
        return sum(event.state == "source_overrun" for event in self.events.values())

    def accepted_by_source(self) -> list[int]:
        return [sum(event.source == source and event.accept_cycle is not None
                    for event in self.events.values()) for source in range(16)]

    def offered_by_source(self) -> list[int]:
        return [sum(event.source == source for event in self.events.values())
                for source in range(16)]


def read_trace(trace_path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    if [row.get("tb_only_event_id") for row in rows] != list(range(len(rows))):
        raise AuditError(f"{trace_path}: event IDs are not exact contiguous IDs")
    if len({(row["occurrence_cycle"], row["logical_source"]) for row in rows}) != len(rows):
        raise AuditError(f"{trace_path}: repeated source occurrence in one cycle")
    return rows


def replay(model_class, rows: list[dict[str, Any]], metadata: dict[str, Any],
           max_advance: int) -> Replay:
    model = model_class(16, max_advance)
    events = {
        row["tb_only_event_id"]: ObservedEvent(
            row["tb_only_event_id"], row["logical_source"], row["occurrence_cycle"],
            row.get("relation_id"), row.get("relation_role"),
        ) for row in rows
    }
    by_cycle: dict[int, list[int]] = {}
    for event in events.values():
        by_cycle.setdefault(event.occurrence, []).append(event.event_id)
    last_offer = max(by_cycle, default=-1)
    pending: list[int | None] = [None] * 16
    output_bubbles = 0
    for cycle in range(20000):
        for event_id in by_cycle.get(cycle, ()):
            event = events[event_id]
            if pending[event.source] is None:
                pending[event.source] = event_id
                event.state = "pending"
            else:
                event.state = "source_overrun"
        valid = [event_id is not None for event_id in pending]
        payload = [event_id if event_id is not None else 0 for event_id in pending]
        had_work = any(valid) or model.occupancy() > 0
        result = model.step(valid, payload, True)
        if had_work and not result.retire_valid:
            output_bubbles += 1
        for source, did_accept in enumerate(result.source_ready):
            if not did_accept:
                continue
            event_id = pending[source]
            if event_id is None:
                raise AuditError("model accepted a source without a pending event")
            event = events[event_id]
            event.state = "accepted"
            event.accept_cycle = cycle
            pending[source] = None
        if result.retired is not None:
            event_id = int(result.retired.payload)
            event = events.get(event_id)
            if event is None or event.accept_cycle is None or event.delivery_cycle is not None:
                raise AuditError("phantom or duplicate retirement")
            if event.source != result.retired.source:
                raise AuditError("retired event source mismatch")
            event.state = "delivered"
            event.delivery_cycle = cycle
        if cycle > last_offer and all(item is None for item in pending) and model.occupancy() == 0:
            break
    else:
        raise AuditError("replay drain timeout")
    if any(event.accept_cycle is not None and event.delivery_cycle is None
           for event in events.values()):
        raise AuditError("accepted event failed to drain")
    return Replay(
        metadata["run"]["name"], metadata["run"]["workload"],
        metadata["run"]["stim_cycles"], cycle + 1, output_bubbles, events,
    )


def fairness_document(run: Replay) -> dict[str, Any]:
    offered = run.offered_by_source()
    accepted = run.accepted_by_source()
    ratios = [a / o for a, o in zip(accepted, offered) if o]
    return {
        "active_sources": len(ratios),
        "demand_normalized_jain": jain(ratios),
        "min_source_acceptance_ratio": min(ratios) if ratios else None,
        "max_source_acceptance_ratio": max(ratios) if ratios else None,
    }


def run_comparison(name: str, metadata: dict[str, Any], fixed: Replay,
                   moving: Replay) -> dict[str, Any]:
    fixed_ids = fixed.accepted_ids
    moving_ids = moving.accepted_ids
    matched = sorted(fixed_ids & moving_ids)
    fixed_lat = [fixed.events[event_id].e2e_latency for event_id in matched]
    moving_lat = [moving.events[event_id].e2e_latency for event_id in matched]
    if any(value is None for value in fixed_lat + moving_lat):
        raise AuditError("matched accepted cohort contains an undelivered event")
    fixed_lat = [int(value) for value in fixed_lat]
    moving_lat = [int(value) for value in moving_lat]
    latency_delta = [moving_value - fixed_value
                     for fixed_value, moving_value in zip(fixed_lat, moving_lat)]
    return {
        "name": name,
        "workload": metadata["run"]["workload"],
        "seed": metadata["run"]["seed"],
        "trace_sha256": metadata["trace_sha256"],
        "generated": fixed.generated,
        "fixed_accepted": fixed.accepted,
        "moving_accepted": moving.accepted,
        "accepted_delta": moving.accepted - fixed.accepted,
        "fixed_overrun": fixed.overrun,
        "moving_overrun": moving.overrun,
        "matched_accepted": len(matched),
        "fixed_only": len(fixed_ids - moving_ids),
        "moving_only": len(moving_ids - fixed_ids),
        "accepted_jaccard": len(matched) / len(fixed_ids | moving_ids),
        "fixed_matched_latency": metrics(fixed_lat),
        "moving_matched_latency": metrics(moving_lat),
        "paired_latency_delta_moving_minus_fixed": metrics(latency_delta),
        "fixed_fairness": fairness_document(fixed),
        "moving_fairness": fairness_document(moving),
        "fixed_cycles": fixed.cycles,
        "moving_cycles": moving.cycles,
        "fixed_output_bubbles": fixed.output_bubbles,
        "moving_output_bubbles": moving.output_bubbles,
    }


def histogram_percentile(histogram: Counter[int], pct: float) -> int:
    target = math.ceil(sum(histogram.values()) * pct)
    cumulative = 0
    for value in sorted(histogram):
        cumulative += histogram[value]
        if cumulative >= target:
            return value
    raise AuditError("empty bootstrap histogram")


def confidence_interval(values: list[int | float]) -> list[int | float]:
    return [percentile(values, 0.025), percentile(values, 0.975)]


def sign_test_pvalue(deltas: list[int]) -> float:
    positives = sum(delta > 0 for delta in deltas)
    negatives = sum(delta < 0 for delta in deltas)
    trials = positives + negatives
    if not trials:
        return 1.0
    tail = min(positives, negatives)
    probability = sum(math.comb(trials, index) for index in range(tail + 1)) / 2 ** trials
    return min(1.0, 2 * probability)


def bootstrap_suite(run_rows: list[dict[str, Any]], replay_pairs: list[tuple[Replay, Replay]],
                    seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    count = len(run_rows)
    accepted_samples: list[int] = []
    p95_samples: list[int] = []
    p99_samples: list[int] = []
    run_histograms = []
    for fixed, moving in replay_pairs:
        matched = fixed.accepted_ids & moving.accepted_ids
        fixed_hist = Counter(int(fixed.events[event_id].e2e_latency) for event_id in matched)
        moving_hist = Counter(int(moving.events[event_id].e2e_latency) for event_id in matched)
        run_histograms.append((fixed_hist, moving_hist))
    for _ in range(BOOTSTRAP_REPLICATES):
        indices = [rng.randrange(count) for _ in range(count)]
        accepted_samples.append(sum(int(run_rows[index]["accepted_delta"]) for index in indices))
        fixed_hist: Counter[int] = Counter()
        moving_hist: Counter[int] = Counter()
        for index in indices:
            fixed_hist.update(run_histograms[index][0])
            moving_hist.update(run_histograms[index][1])
        p95_samples.append(
            histogram_percentile(moving_hist, 0.95) - histogram_percentile(fixed_hist, 0.95)
        )
        p99_samples.append(
            histogram_percentile(moving_hist, 0.99) - histogram_percentile(fixed_hist, 0.99)
        )
    deltas = [int(row["accepted_delta"]) for row in run_rows]
    return {
        "method": "paired trace-cluster percentile bootstrap",
        "seed": seed,
        "replicates": BOOTSTRAP_REPLICATES,
        "sampling_unit": "exact run/trace; events within a trace remain clustered",
        "accepted_delta_total_95ci": confidence_interval(accepted_samples),
        "matched_p95_delta_95ci": confidence_interval(p95_samples),
        "matched_p99_delta_95ci": confidence_interval(p99_samples),
        "positive_delta_runs": sum(delta > 0 for delta in deltas),
        "negative_delta_runs": sum(delta < 0 for delta in deltas),
        "zero_delta_runs": sum(delta == 0 for delta in deltas),
        "two_sided_run_sign_test_p": sign_test_pvalue(deltas),
    }


def aggregate_suite(run_rows: list[dict[str, Any]], pairs: list[tuple[Replay, Replay]],
                    suite: str) -> dict[str, Any]:
    fixed_ids: list[tuple[int, int]] = []
    moving_ids: list[tuple[int, int]] = []
    fixed_lat: list[int] = []
    moving_lat: list[int] = []
    fixed_all_lat: list[int] = []
    moving_all_lat: list[int] = []
    fixed_only_lat: list[int] = []
    moving_only_lat: list[int] = []
    latency_deltas: list[int] = []
    offered_by_source = [0] * 16
    accepted_by_source = {"fixed": [0] * 16, "moving": [0] * 16}
    tail_by_workload: dict[str, dict[str, int]] = {}
    for run_index, (fixed, moving) in enumerate(pairs):
        fixed_ids.extend((run_index, value) for value in fixed.accepted_ids)
        moving_ids.extend((run_index, value) for value in moving.accepted_ids)
        matched = fixed.accepted_ids & moving.accepted_ids
        fixed_all_lat.extend(int(event.e2e_latency) for event in fixed.events.values()
                             if event.e2e_latency is not None)
        moving_all_lat.extend(int(event.e2e_latency) for event in moving.events.values()
                              if event.e2e_latency is not None)
        fixed_only_lat.extend(int(fixed.events[event_id].e2e_latency)
                              for event_id in fixed.accepted_ids - moving.accepted_ids)
        moving_only_lat.extend(int(moving.events[event_id].e2e_latency)
                               for event_id in moving.accepted_ids - fixed.accepted_ids)
        for event_id in matched:
            f_latency = int(fixed.events[event_id].e2e_latency)
            m_latency = int(moving.events[event_id].e2e_latency)
            fixed_lat.append(f_latency)
            moving_lat.append(m_latency)
            latency_deltas.append(m_latency - f_latency)
        for source, value in enumerate(fixed.offered_by_source()):
            offered_by_source[source] += value
        for source, value in enumerate(fixed.accepted_by_source()):
            accepted_by_source["fixed"][source] += value
        for source, value in enumerate(moving.accepted_by_source()):
            accepted_by_source["moving"][source] += value
    fixed_set, moving_set = set(fixed_ids), set(moving_ids)
    moving_p99 = int(percentile(moving_lat, 0.99))
    fixed_p99 = int(percentile(fixed_lat, 0.99))
    moving_tail_fixed_latencies: list[int] = []
    for row, (fixed, moving) in zip(run_rows, pairs):
        matched = fixed.accepted_ids & moving.accepted_ids
        entry = tail_by_workload.setdefault(row["workload"], {
            "matched": 0, "fixed_at_or_above_suite_p99": 0,
            "moving_at_or_above_suite_p99": 0,
            "moving_slower_than_fixed": 0,
        })
        entry["matched"] += len(matched)
        for event_id in matched:
            f_latency = int(fixed.events[event_id].e2e_latency)
            m_latency = int(moving.events[event_id].e2e_latency)
            entry["fixed_at_or_above_suite_p99"] += int(f_latency >= fixed_p99)
            entry["moving_at_or_above_suite_p99"] += int(m_latency >= moving_p99)
            entry["moving_slower_than_fixed"] += int(m_latency > f_latency)
            if m_latency >= moving_p99:
                moving_tail_fixed_latencies.append(f_latency)
    ratios = {
        model: [accepted / offered for accepted, offered in zip(counts, offered_by_source)
                if offered]
        for model, counts in accepted_by_source.items()
    }
    generated = sum(int(row["generated"]) for row in run_rows)
    fixed_accepted = sum(int(row["fixed_accepted"]) for row in run_rows)
    moving_accepted = sum(int(row["moving_accepted"]) for row in run_rows)
    return {
        "runs": len(run_rows),
        "generated": generated,
        "fixed_accepted": fixed_accepted,
        "moving_accepted": moving_accepted,
        "accepted_delta": moving_accepted - fixed_accepted,
        "accepted_rate_delta": (moving_accepted - fixed_accepted) / generated,
        "fixed_capacity_loss": generated - fixed_accepted,
        "moving_capacity_loss": generated - moving_accepted,
        "capacity_loss_reduction_fraction": (
            (moving_accepted - fixed_accepted) / (generated - fixed_accepted)
        ),
        "matched_accepted": len(fixed_set & moving_set),
        "fixed_only": len(fixed_set - moving_set),
        "moving_only": len(moving_set - fixed_set),
        "accepted_jaccard": len(fixed_set & moving_set) / len(fixed_set | moving_set),
        "fixed_all_accepted_latency": metrics(fixed_all_lat),
        "moving_all_accepted_latency": metrics(moving_all_lat),
        "fixed_matched_latency": metrics(fixed_lat),
        "moving_matched_latency": metrics(moving_lat),
        "fixed_only_latency": metrics(fixed_only_lat),
        "moving_only_latency": metrics(moving_only_lat),
        "paired_latency_delta_moving_minus_fixed": metrics(latency_deltas),
        "p99_cause": {
            "all_accepted_p99_delta": int(percentile(moving_all_lat, 0.99))
                                      - int(percentile(fixed_all_lat, 0.99)),
            "matched_p99_delta": moving_p99 - fixed_p99,
            "moving_matched_tail_threshold": moving_p99,
            "moving_matched_tail_event_count": len(moving_tail_fixed_latencies),
            "fixed_latency_of_same_moving_tail_events": metrics(moving_tail_fixed_latencies),
            "classification": (
                "SURVIVOR_SET_QUANTILE_COMPOSITION"
                if int(percentile(moving_all_lat, 0.99)) > int(percentile(fixed_all_lat, 0.99))
                and moving_p99 == fixed_p99
                else "MATCHED_COHORT_TAIL_SHIFT"
                if moving_p99 > fixed_p99 else "NO_P99_REGRESSION"
            ),
        },
        "pooled_demand_normalized_fairness": {
            "fixed": jain(ratios["fixed"]), "moving": jain(ratios["moving"]),
            "fixed_min_ratio": min(ratios["fixed"]),
            "moving_min_ratio": min(ratios["moving"]),
        },
        "macro_run_fairness": {
            "fixed_mean": mean(row["fixed_fairness"]["demand_normalized_jain"] for row in run_rows),
            "moving_mean": mean(row["moving_fairness"]["demand_normalized_jain"] for row in run_rows),
            "fixed_min": min(row["fixed_fairness"]["demand_normalized_jain"] for row in run_rows),
            "moving_min": min(row["moving_fairness"]["demand_normalized_jain"] for row in run_rows),
        },
        "tail_attribution_by_workload": tail_by_workload,
        "bootstrap": bootstrap_suite(run_rows, pairs, BOOTSTRAP_SEED + (0 if suite == "full50" else 1)),
    }


def phase_recovery(pairs_by_name: dict[str, tuple[Replay, Replay]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name in ("phase_transition_s3501", "phase_transition_s3502"):
        fixed, moving = pairs_by_name[name]
        rows = []
        for phase, start, end in PHASE_RANGES:
            ids = {event_id for event_id, event in fixed.events.items()
                   if start <= event.occurrence < end}
            matched = ids & fixed.accepted_ids & moving.accepted_ids
            fixed_lat = [int(fixed.events[event_id].e2e_latency) for event_id in matched]
            moving_lat = [int(moving.events[event_id].e2e_latency) for event_id in matched]
            rows.append({
                "phase": phase, "start": start, "end": end, "generated": len(ids),
                "fixed_accepted": len(ids & fixed.accepted_ids),
                "moving_accepted": len(ids & moving.accepted_ids),
                "matched": len(matched),
                "fixed_matched_latency": metrics(fixed_lat),
                "moving_matched_latency": metrics(moving_lat),
            })
        recovery_start = PHASE_RANGES[-1][1]
        def recovery(run: Replay) -> int:
            last_delivery = max(
                (event.delivery_cycle for event in run.events.values()
                 if event.delivery_cycle is not None), default=recovery_start - 1
            )
            return max(0, int(last_delivery) + 1 - recovery_start)
        output[name] = {
            "phases": rows,
            "fixed_recovery_to_zero_cycles": recovery(fixed),
            "moving_recovery_to_zero_cycles": recovery(moving),
        }
    return output


def pairwise_mapping(pairs_by_name: dict[str, tuple[Replay, Replay]],
                     metadata_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    canonical_pairs = list(itertools.combinations(range(16), 2))
    maps: dict[str, dict[str, dict[tuple[int, tuple[int, int]], int]]] = {}
    for mapping, name in (("identity", "pairwise_contention_identity"),
                          ("affine", "pairwise_contention_affine")):
        fixed, moving = pairs_by_name[name]
        metadata = metadata_by_name[name]
        repeats = metadata["run"]["parameters"]["pair_repeats"]
        map_results: dict[str, dict[tuple[int, tuple[int, int]], int]] = {}
        for model_name, run in (("fixed", fixed), ("moving", moving)):
            trials: dict[tuple[int, tuple[int, int]], int] = {}
            for relation_id in range(repeats * len(canonical_pairs)):
                event_ids = [event_id for event_id, event in run.events.items()
                             if event.relation_id == relation_id]
                if len(event_ids) != 2:
                    raise AuditError("pairwise relation does not contain two events")
                if not set(event_ids) <= run.accepted_ids:
                    continue
                completion = max(int(run.events[event_id].e2e_latency) for event_id in event_ids)
                key = (relation_id // len(canonical_pairs),
                       canonical_pairs[relation_id % len(canonical_pairs)])
                trials[key] = completion
            map_results[model_name] = trials
        maps[mapping] = map_results
    result: dict[str, Any] = {"per_mapping_moving_minus_fixed": {}}
    for mapping in ("identity", "affine"):
        common = set(maps[mapping]["fixed"]) & set(maps[mapping]["moving"])
        deltas = [maps[mapping]["moving"][key] - maps[mapping]["fixed"][key]
                  for key in sorted(common)]
        result["per_mapping_moving_minus_fixed"][mapping] = {
            "fixed_complete_pairs": len(maps[mapping]["fixed"]),
            "moving_complete_pairs": len(maps[mapping]["moving"]),
            "matched_complete_pairs": len(common),
            "completion_latency_delta": metrics(deltas),
        }
    result["cross_map_affine_minus_identity"] = {}
    for model_name in ("fixed", "moving"):
        common = set(maps["identity"][model_name]) & set(maps["affine"][model_name])
        deltas = [maps["affine"][model_name][key] - maps["identity"][model_name][key]
                  for key in sorted(common)]
        result["cross_map_affine_minus_identity"][model_name] = {
            "joined_canonical_pair_repeats": len(common),
            "completion_latency_delta": metrics(deltas),
        }
    return result


def load_official_policy(a1_root: Path) -> dict[str, Any]:
    policy_path = a1_root / "scripts/common_suite_official.py"
    if git_output(a1_root, "rev-parse", "HEAD") != A1_COMMIT:
        raise AuditError("A1 worktree HEAD is not the A4-frozen common commit")
    if sha256(policy_path) != A1_OFFICIAL_POLICY_SHA256:
        raise AuditError("official suite policy SHA mismatch")
    policy = runpy.run_path(str(policy_path))
    if policy.get("GENERATOR_VERSION") != "4.0":
        raise AuditError("official generator version mismatch")
    return policy


def validate_generation_index(root: Path, a1_root: Path, suite: str,
                              official: dict[str, Any]) -> list[dict[str, Any]]:
    index_path = root / suite / "generation-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("generator_version") != "4.0":
        raise AuditError(f"{suite}: generator version mismatch")
    config = official["SUITES"][suite]
    if index.get("input_manifest") != config["manifest_name"]:
        raise AuditError(f"{suite}: input manifest name mismatch")
    manifest = a1_root / "benchmarks/clean_slate_aer" / config["manifest_name"]
    if sha256(manifest) != EXPECTED_TRACE_MANIFESTS[suite]:
        raise AuditError(f"{suite}: official manifest SHA mismatch")
    if config["manifest_sha256"] != EXPECTED_TRACE_MANIFESTS[suite]:
        raise AuditError(f"{suite}: policy/manifest SHA mismatch")
    if len(index.get("runs", ())) != len(config["names"]):
        raise AuditError(f"{suite}: exact cardinality mismatch")
    names = tuple(item.get("run", {}).get("name") for item in index["runs"])
    if names != tuple(config["names"]):
        raise AuditError(f"{suite}: exact run name/order mismatch")
    for metadata in index["runs"]:
        trace = root / suite / metadata["trace_file"]
        if sha256(trace) != metadata["trace_sha256"]:
            raise AuditError(f"{suite}/{metadata['run']['name']}: trace SHA mismatch")
        if metadata["trace_sha256"] != official["TRACE_SHA256"][metadata["run"]["name"]]:
            raise AuditError(f"{suite}/{metadata['run']['name']}: non-official trace SHA")
        if metadata.get("event_identity_mode") != "address_only":
            raise AuditError(f"{suite}: non-address-only trace")
    return index["runs"]


def validate_frozen_aggregate(suite: str, pairs: list[tuple[Replay, Replay]],
                              frozen: dict[str, Any]) -> dict[str, Any]:
    reproduced: dict[str, Any] = {}
    for model_name, index in (("fixed", 0), ("moving", 1)):
        runs = [pair[index] for pair in pairs]
        latencies = [int(event.e2e_latency) for run in runs for event in run.events.values()
                     if event.e2e_latency is not None]
        observed = {
            "offered": sum(run.generated for run in runs),
            "accepted": sum(run.accepted for run in runs),
            "overrun": sum(run.overrun for run in runs),
            "retired": sum(run.accepted for run in runs),
            "cycles": sum(run.cycles for run in runs),
            "output_bubbles": sum(run.output_bubbles for run in runs),
            "p95_e2e_latency": percentile(latencies, 0.95),
            "p99_e2e_latency": percentile(latencies, 0.99),
        }
        for field, value in observed.items():
            if value != frozen[model_name][field]:
                raise AuditError(
                    f"{suite}/{model_name}: frozen {field} mismatch "
                    f"({value} != {frozen[model_name][field]})"
                )
        reproduced[model_name] = observed
    return reproduced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a4-root", type=Path, default=Path("/home/chickgoose/projects/a4"))
    parser.add_argument("--a1-root", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    module, frozen_summary = load_a4_model(args.a4_root.resolve())
    official = load_official_policy(args.a1_root.resolve())
    all_output: dict[str, Any] = {
        "schema_version": 1,
        "decision": "INDEPENDENT_AUDIT_ONLY",
        "provenance": {
            "a4_commit": A4_COMMIT,
            "a4_model_sha256": A4_MODEL_SHA256,
            "a4_frozen_summary_sha256": A4_SUMMARY_SHA256,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "a1_common_commit": A1_COMMIT,
            "a1_official_policy_sha256": A1_OFFICIAL_POLICY_SHA256,
        },
        "suites": {},
    }
    full_pairs_by_name: dict[str, tuple[Replay, Replay]] = {}
    full_metadata_by_name: dict[str, dict[str, Any]] = {}
    for suite in ("full50", "capacity22"):
        metadata_rows = validate_generation_index(
            args.generated_root, args.a1_root.resolve(), suite, official
        )
        run_rows = []
        replay_pairs = []
        for metadata in metadata_rows:
            name = metadata["run"]["name"]
            rows = read_trace(args.generated_root / suite / metadata["trace_file"])
            fixed = replay(module.MovingBlockTreeModel, rows, metadata, 1)
            moving = replay(module.MovingBlockTreeModel, rows, metadata, 2)
            replay_pairs.append((fixed, moving))
            run_rows.append(run_comparison(name, metadata, fixed, moving))
            if suite == "full50":
                full_pairs_by_name[name] = (fixed, moving)
                full_metadata_by_name[name] = metadata
        frozen = frozen_summary["full50" if suite == "full50" else "capacity22"]
        reproduction = validate_frozen_aggregate(suite, replay_pairs, frozen)
        aggregate = aggregate_suite(run_rows, replay_pairs, suite)
        all_output["suites"][suite] = {
            "generation_index_sha256": sha256(args.generated_root / suite / "generation-index.json"),
            "frozen_aggregate_reproduction": reproduction,
            "aggregate": aggregate,
            "runs": run_rows,
        }
    all_output["phase_recovery"] = phase_recovery(full_pairs_by_name)
    all_output["pairwise_mapping"] = pairwise_mapping(
        full_pairs_by_name, full_metadata_by_name
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(all_output, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(f"A5_W4_MOVING_BLOCK_AUDIT_PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
