#!/usr/bin/env python3
"""Emit schema-complete analyzer evidence for wrapper integration tests."""

import argparse
import csv
import itertools
import json
from pathlib import Path

MIXED_BOUNDS = [
    ("u_bernoulli", 0, 640), ("u_smooth", 640, 1280),
    ("s_persistent", 1280, 1536), ("s_rotating", 1536, 1792),
    ("h_a", 1792, 2560), ("h_b", 2560, 3328), ("h_a_replay", 3328, 4096),
]
PHASE_NAMES = ["sparse", "near_saturation", "overload", "post_sparse", "drain"]


def pairwise(common, metadata):
    permutation = metadata["logical_source_permutation"]
    pairs = list(itertools.combinations(range(16), 2))
    aggregates = [{"canonical_source_a": a, "canonical_source_b": b,
        "physical_source_a": permutation[a], "physical_source_b": permutation[b], "trial_count": 2,
        "evaluable_trials": 2, "dropped_trials": 0, "censored_trials": 0, "overlap_trials": 0,
        "mean_completion_latency_cycles": 2.0, "max_completion_latency_cycles": 2,
        "mean_service_skew_cycles": 1.0, "max_service_skew_cycles": 1} for a, b in pairs]
    trials = []
    for relation_id in range(240):
        a, b = pairs[relation_id % 120]; pa, pb = permutation[a], permutation[b]
        trials.append({"relation_id": relation_id, "repeat_index": relation_id // 120,
            "canonical_source_a": a, "canonical_source_b": b, "physical_source_a": pa,
            "physical_source_b": pb, "overlaps_previous_pair": False,
            "overlapping_prior_pair_count": 0, "event_state_a": "delivered", "event_state_b": "delivered",
            "source_a": pa, "source_b": pb, "delivery_a": 1, "delivery_b": 2,
            "completion_latency_cycles": 2, "service_skew_cycles": 1, "result": "evaluable"})
    return {**common, "generator_version": "4.0", "logical_source_permutation": permutation,
        "pair_count": 240, "evaluable_pairs": 240, "dropped_pairs": 0, "censored_pairs": 0,
        "nonevaluable_pairs": 0, "measurement_state": "COMPLETE", "a_first_pairs": 120,
        "b_first_pairs": 120, "same_cycle_pairs": 0, "overlap_pairs": 0,
        "max_overlapping_prior_pairs": 0, "isolation_state": "QUIESCENT",
        "worst_completion_pair": {"relation_id": 0}, "worst_skew_pair": {"relation_id": 0},
        "mean_pair_completion_latency_cycles": 2.0, "p95_pair_completion_latency_cycles": 2,
        "max_pair_completion_latency_cycles": 2, "mean_pair_service_skew_cycles": 1.0,
        "p95_pair_service_skew_cycles": 1, "max_pair_service_skew_cycles": 1,
        "pair_aggregates": aggregates, "trials": trials}


def mixed(common):
    phases = []
    for phase, start, end in MIXED_BOUNDS:
        phases.append({"phase": phase, "start_cycle": start, "end_cycle_exclusive": end, "cycles": end-start,
            "generated": 1, "source_overrun": 0, "accepted": 1, "delivered": 1,
            "offered_events_per_cycle": 1/(end-start), "accepted_events_per_cycle": 1/(end-start),
            "delivered_by_occurrence_events_per_cycle": 1/(end-start), "delivered_in_window": 1,
            "retire_throughput_events_per_cycle": 1/(end-start), "capacity_loss_ratio": 0.0,
            "latency_cycles": {"samples": 1, "mean": 1.0, "p50": 1, "p95": 1, "p99": 1, "max": 1},
            "service_gap_cycles": {"active_sources": 1, "delivered_sources": 1,
                "unobserved_active_sources": 0, "samples": 0, "p95_cycles": None,
                "p99_cycles": None, "max_cycles": None},
            "backlog_at_start": 0, "backlog_peak": 1, "backlog_at_end": 0,
            "backlog_recovery_to_zero_cycles": 0, "phase_origin_last_delivery_after_boundary_cycles": 0})
    pair_specs = (("uniform_temporal", "u_bernoulli", "u_smooth"),
                  ("sustained_temporal", "s_persistent", "s_rotating"),
                  ("spatial_b_vs_a", "h_b", "h_a"),
                  ("spatial_replay_vs_a", "h_a_replay", "h_a"))
    deltas = [{"pair": pair, "left_phase": left, "right_phase": right,
        "sign_convention": "left_minus_right", "generated_delta": 0, "capacity_loss_events_delta": 0,
        "capacity_loss_ratio_delta": 0.0, "retire_throughput_delta": 0.0,
        "p95_latency_cycles_delta": 0, "p99_latency_cycles_delta": 0,
        "max_service_gap_cycles_delta": None, "backlog_peak_delta": 0,
        "backlog_recovery_cycles_delta": 0} for pair, left, right in pair_specs]
    return {**common, "schema_version": 1, "event_identity_mode": "address_only", "sink_mode": "always",
        "tb_cycle_offset": 1, "observation_end_cycle": 5000,
        "provenance_validation": {"status": "pass", "trace_sha256": True, "phase_boundaries": True,
            "address_only_identity": True, "source_local_order": True,
            "complete_uncensored_event_accounting": True},
        "matched_trace_validation": {"status": "pass", "uniform_exact_event_count_and_source_histogram": True,
            "sustained_exact_event_source_and_fan_in_histograms": True,
            "sustained_frozen_dwell_and_rotation": True, "hotspot_derived_rank_stream": True,
            "hotspot_a_replay_exact_physical_replay": True},
        "summary_evidence": {"status": "qualified_pass", "correctness_qualified": True,
            "scoreboard_errors": 0, "conservation_validated": True,
            "generated_equals_overrun_plus_accepted": True, "accepted_equals_delivered": True},
        "classification": {"analysis_status": "pass", "correctness_status": "qualified_pass",
            "correctness_scope": "common summary errors plus exact event conservation", "capacity_status": "lossless",
            "capacity_loss_events": 0, "capacity_loss_ratio": 0.0, "censored_events": 0},
        "phases": phases, "matched_pair_deltas": deltas}


def phase(common, metadata):
    stim = metadata["run"]["stim_cycles"]; eighth = stim // 8
    bounds = [(0, 2*eighth), (2*eighth, 4*eighth), (4*eighth, 6*eighth),
              (6*eighth, 7*eighth), (7*eighth, 8*eighth)]
    rows = [{"phase": name, "start_cycle": start, "end_cycle_exclusive": end,
        "generated": 1, "source_overrun": 0, "accepted": 1, "delivered_by_occurrence_phase": 1,
        "delivered_in_phase_window": 1, "completion_per_phase_cycle": 1/(end-start),
        "p95_e2e_latency_cycles": 1, "backlog_peak": 1, "backlog_at_end": 0,
        "cumulative_overrun_at_end": 0, "loss_adjusted_pressure_peak": 1}
        for name, (start, end) in zip(PHASE_NAMES, bounds)]
    return {**common, "tb_cycle_offset": 1, "recovery_to_zero_cycles": 0,
            "recovery_censored": False, "recovery_lossless": True, "phases": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace"); parser.add_argument("--run-manifest", required=True)
    parser.add_argument("--events", required=True); parser.add_argument("--summary")
    parser.add_argument("--require-qualified", action="store_true"); parser.add_argument("--output", required=True)
    args = parser.parse_args()
    metadata = json.loads(Path(args.run_manifest).read_text()); run = metadata["run"]
    with Path(args.events).open(newline="") as stream:
        event = next(csv.DictReader(stream))
    common = {"candidate": event["candidate"], "test": event["test"], "seed": event["seed"],
              "trace_sha256": metadata["trace_sha256"]}
    if run["workload"] == "pairwise_contention": result = pairwise(common, metadata)
    elif run["workload"] == "mixed_phase_always_ready": result = mixed(common)
    elif run["workload"] == "phase_transition": result = phase(common, metadata)
    else: raise SystemExit("unsupported fake analyzer workload")
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
