#!/usr/bin/env python3
"""Generate the frozen candidate-neutral K2 adversarial vector bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from k2_oracle import (ROW_WHEEL, SOURCE_COUNT, RETIRE_LANES, VECTOR_SCHEMA,
                       event_id, object_sha256, run_sha)


def occurrence(run: str, cycle: int, source: int) -> dict[str, Any]:
    return {
        "event_id": event_id(run, cycle, source),
        "source": source,
        "payload": {"address": source, "x": source % 4, "y": source // 4},
    }


def make_run(name: str, purpose: str, reset_n: list[bool], offers: dict[int, list[int]],
             ready: dict[int, tuple[bool, bool]] | None = None,
             *, tags: list[str], measurement: tuple[int, int],
             reset_policy: str = "initial_only") -> dict[str, Any]:
    ready = ready or {}
    cycles = []
    for cycle, reset in enumerate(reset_n):
        cycles.append({
            "cycle": cycle,
            "reset_n": reset,
            "retire_ready": list(ready.get(cycle, (True, True))) if reset else [False, False],
            "occurrences": [occurrence(name, cycle, source)
                            for source in offers.get(cycle, [])] if reset else [],
        })
    run = {
        "name": name,
        "origin": "a5_k2_adversarial_v1",
        "purpose": purpose,
        "tags": tags,
        "reset_policy": reset_policy,
        "stim_cycles": measurement[1],
        "measurement_window": list(measurement),
        "cycles": cycles,
    }
    run["run_sha256"] = run_sha(run)
    return run


def build_bundle() -> dict[str, Any]:
    runs: list[dict[str, Any]] = []

    persistent_name = "persistent_weight_120"
    persistent_reset = [False, False] + [True] * 94
    persistent_offers = {cycle: list(range(16)) for cycle in range(2, 86)}
    runs.append(make_run(
        persistent_name,
        "First 120 committed events under persistent all-source demand must be 10:50:50:10 and an exact scalar prefix.",
        persistent_reset, persistent_offers,
        tags=["required", "persistent_weight", "prefix", "capacity"], measurement=(2, 86)))

    sparse_name = "sparse_work_conservation"
    sparse_reset = [False, False] + [True] * 38
    sparse_offers = {cycle: [source] for cycle, source in
                     zip((3, 7, 11, 15, 19, 23, 27, 31), (0, 5, 10, 15, 3, 6, 9, 12))}
    runs.append(make_run(
        sparse_name, "Isolated events expose idle bubbles and occurrence-to-accept latency.",
        sparse_reset, sparse_offers,
        tags=["required", "sparse", "latency", "work_conservation"], measurement=(2, 34)))

    same_name = "same_row_distinct_pair"
    same_reset = [False, False] + [True] * 12
    runs.append(make_run(
        same_name, "Two pending sources in one row require distinct ordered winners.",
        same_reset, {2: [0, 1], 6: [4, 5], 10: [8, 9]},
        tags=["required", "duplicate", "prefix"], measurement=(2, 12)))

    stale_name = "stale_second_revalidation"
    stale_reset = [False, False] + [True] * 14
    runs.append(make_run(
        stale_name,
        "A previously prepared second source becomes stale after the committed prefix and new demand.",
        stale_reset, {2: [0, 8], 3: [4, 5], 5: [6, 8]},
        tags=["required", "stale_second", "prefix", "generation"], measurement=(2, 12)))

    future_name = "future_arrival_divergence_witness"
    future_reset = [False, False] + [True] * 10
    runs.append(make_run(
        future_name,
        "Expected witness: K2 cohort prefix and K1 future trace diverge after source 4 arrives.",
        future_reset, {2: [0, 8], 3: [4, 8]},
        tags=["required", "future_divergence_witness", "not_candidate_failure"], measurement=(2, 8)))

    stall_name = "ordered_lane_stall"
    stall_reset = [False, False] + [True] * 18
    stall_ready = {
        3: (False, True), 4: (False, True), 5: (False, True),
        6: (True, True), 10: (True, False), 11: (True, False), 12: (True, True),
    }
    runs.append(make_run(
        stall_name,
        "Lane 0 stall protects ordered retirement; both presented records remain stable.",
        stall_reset, {2: [0, 1], 8: [4, 5], 13: [8, 9]}, stall_ready,
        tags=["required", "lane_stall", "ordered_retire", "stability"], measurement=(2, 16)))

    reset_name = "reset_abort_no_phantom"
    reset_pattern = [False, False, True, True, False, False, True, True, True, True, True, True]
    reset_ready = {3: (False, False)}
    runs.append(make_run(
        reset_name,
        "Reset aborts pre-reset tentative/accepted work; only the post-reset sentinel may retire.",
        reset_pattern, {2: [0, 1, 2], 9: [15]}, reset_ready,
        tags=["required", "reset", "phantom", "drain"], measurement=(6, 12),
        reset_policy="abort_pre_reset"))

    bundle: dict[str, Any] = {
        "schema": VECTOR_SCHEMA,
        "schema_version": 1,
        "source_count": SOURCE_COUNT,
        "retire_lanes": RETIRE_LANES,
        "event_id_scope": "TB-only; never a synthesized DUT input",
        "cycle_semantics": "occurrences update one-entry source latches before observed accepts at the same indexed edge",
        "oracle_policy": {
            "name": "committed_event_weighted_wheel_rr_v1",
            "row_wheel": list(ROW_WHEEL),
            "row_for_source": "source_div_4",
            "column_rule": "round_robin",
            "initial_state": {"wheel_pos": 0, "column_rr": [0, 0, 0, 0]},
            "state_transition": "once per committed accepted event; never per physical cycle or attempted slot",
        },
        "runs": runs,
    }
    bundle["bundle_sha256"] = object_sha256(bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle()
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"A5_K2_VECTOR_BUNDLE_CREATED runs={len(bundle['runs'])} sha256={bundle['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
