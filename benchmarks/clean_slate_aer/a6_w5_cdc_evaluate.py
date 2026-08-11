#!/usr/bin/env python3
"""A6 W5 RX burst-clock to downstream core-clock boundary evaluation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path


W4_PATH = Path(__file__).with_name("a6_w4_fixed_pin_replay_db3f04f.py")
W4_SPEC = importlib.util.spec_from_file_location("a6_w5_w4_base", W4_PATH)
assert W4_SPEC and W4_SPEC.loader
w4 = importlib.util.module_from_spec(W4_SPEC)
sys.modules[W4_SPEC.name] = w4
W4_SPEC.loader.exec_module(w4)


def phase_capture_counts(
    events: list[object], *, stim_cycles: int, link_ratio: int,
) -> list[int]:
    """Return RX commits observed between consecutive downstream core edges."""
    arrivals: dict[int, list[object]] = defaultdict(list)
    for event in events:
        arrivals[event.occurrence_cycle].append(event)
    queue: deque[object] = deque()
    commits: list[int] = []
    period = 0
    total_stim_periods = stim_cycles * link_ratio
    while period < total_stim_periods or queue:
        core_cycle = period // link_ratio
        if period % link_ratio == 0 and core_cycle < stim_cycles:
            queue.extend(arrivals.get(core_cycle, []))
        if period % link_ratio == 0:
            commits.append(0)
        if queue:
            queue.popleft()
            commits[-1] += 1
        period += 1
    return commits


def async_fifo_depth_without_backpressure(
    commits: list[int], *, pointer_visibility_core_cycles: int = 2,
) -> int:
    """Finite-trace FIFO occupancy with a one-event/core reader.

    The consumer cannot begin until the synchronized write pointer is visible.
    This is a capacity lower bound, not a proof for asynchronous clock drift.
    """
    backlog = 0
    maximum = 0
    for cycle, count in enumerate(commits):
        backlog += count
        maximum = max(maximum, backlog)
        if cycle >= pointer_visibility_core_cycles and backlog:
            backlog -= 1
    while backlog:
        backlog -= 1
    return maximum


def fifo_state_lower_bound(depth: int) -> dict[str, int]:
    """State for a conventional power-of-two dual-clock Gray-pointer FIFO."""
    if depth < 2 or depth & (depth - 1):
        raise ValueError("async FIFO depth must be a power of two >= 2")
    address_bits = int(math.log2(depth))
    pointer_bits = address_bits + 1
    payload = 4 * depth
    local_binary_and_gray = 4 * pointer_bits
    two_flop_cross_pointer_sync = 4 * pointer_bits
    output_and_flags = 4 + 1 + 2
    total = (payload + local_binary_and_gray
             + two_flop_cross_pointer_sync + output_and_flags)
    return {
        "depth": depth,
        "payload_bits": payload,
        "local_binary_and_gray_pointer_bits": local_binary_and_gray,
        "two_flop_cross_pointer_synchronizer_bits": two_flop_cross_pointer_sync,
        "registered_output_valid_and_flag_bits": output_and_flags,
        "state_bits_lower_bound": total,
    }


def next_power_of_two(value: int) -> int:
    return max(2, 1 << max(1, value - 1).bit_length())


def analyze_run(
    events: list[object], *, suite: str, run: str,
    stim_cycles: int, link_ratio: int,
) -> dict[str, object]:
    commits = phase_capture_counts(
        events, stim_cycles=stim_cycles, link_ratio=link_ratio)
    captured = sum(count & 1 for count in commits)
    generated = len(events)
    unsafe_intervals = sum(count > 1 for count in commits)
    fifo_depth = async_fifo_depth_without_backpressure(commits)
    rounded_depth = next_power_of_two(fifo_depth)
    return {
        "suite": suite,
        "run": run,
        "link_ratio": link_ratio,
        "generated": generated,
        "rx_committed": sum(commits),
        "max_rx_commits_between_core_edges": max(commits, default=0),
        "multi_commit_core_intervals": unsafe_intervals,
        "phase_capture_delivered": captured,
        "phase_capture_lost_by_toggle_alias": generated - captured,
        "phase_capture_sequence_exact": unsafe_intervals == 0,
        "finite_trace_async_fifo_depth_lower_bound": fifo_depth,
        "finite_trace_async_fifo_rounded_depth": rounded_depth,
        "finite_trace_async_fifo_state_bits_lower_bound": (
            fifo_state_lower_bound(rounded_depth)["state_bits_lower_bound"]),
    }


def aggregate(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["suite"]), int(row["link_ratio"]))].append(row)
    output = []
    for (suite, ratio), group in sorted(groups.items()):
        required = max(int(row["finite_trace_async_fifo_depth_lower_bound"])
                       for row in group)
        rounded = next_power_of_two(required)
        output.append({
            "suite": suite,
            "link_ratio": ratio,
            "runs": len(group),
            "events": sum(int(row["generated"]) for row in group),
            "phase_capture_exact_runs": sum(
                bool(row["phase_capture_sequence_exact"]) for row in group),
            "phase_capture_lost_by_toggle_alias": sum(
                int(row["phase_capture_lost_by_toggle_alias"]) for row in group),
            "worst_rx_commits_between_core_edges": max(
                int(row["max_rx_commits_between_core_edges"]) for row in group),
            "finite_trace_async_fifo_depth_lower_bound": required,
            "finite_trace_async_fifo_rounded_depth": rounded,
            "finite_trace_async_fifo_state_bits_lower_bound": (
                fifo_state_lower_bound(rounded)["state_bits_lower_bound"]),
        })
    return output


def evaluate(
    registry: Path, generator: Path, a7_repo: Path,
    suite_inputs: dict[str, tuple[Path, Path]],
) -> dict[str, object]:
    contract, runs_by_suite = w4.validate_inputs(
        registry, generator, a7_repo, suite_inputs)
    rows: list[dict[str, object]] = []
    for suite, runs in runs_by_suite.items():
        trace_dir = suite_inputs[suite][1]
        for run in runs:
            events = w4.base.load_events(
                trace_dir / f"{run['name']}.events.jsonl",
                contract["suites"][suite]["traces"][run["name"]]["sha256"],
            )
            for ratio in (1, 2, 4):
                rows.append(analyze_run(
                    events, suite=suite, run=run["name"],
                    stim_cycles=run["stim_cycles"], link_ratio=ratio))
    summaries = aggregate(rows)
    return {
        "schema_version": 1,
        "candidate": "a6_w5_rx_core_boundary",
        "a7_bound_commit": w4.BOUND_COMMIT,
        "registry_sha256": w4.base.sha256_file(registry),
        "comparison": {
            "phase_related_r1_capture": {
                "implemented": True,
                "synthesized_state_bits": 6,
                "state_breakdown": {
                    "seen_toggle": 1, "downstream_address": 4,
                    "downstream_valid": 1},
                "synchronizer_bits": 0,
                "reason_no_synchronizer": "clocks must be phase-related and STA-constrained; this is not an asynchronous CDC",
                "rx_commit_to_core_visible_latency_core_cycles": 0.25,
                "tx_admission_to_core_visible_latency_core_cycles": 1.0,
                "maximum_events_per_core_cycle": 1.0,
                "continuous_one_event_per_cycle": True,
                "sink_contract": "always ready; no backpressure in the primary restricted endpoint",
                "lossless_scope": "R=1 only, at most one RX commit between core edges, frozen 4 ns commit-to-core phase, drained reset sequence",
            },
            "bundled_data_two_phase_toggle_handshake": {
                "implemented": False,
                "state_bits_lower_bound": 15,
                "state_breakdown": {
                    "source_address_mailbox": 4, "request_toggle": 1,
                    "destination_request_sync": 2, "destination_seen": 1,
                    "destination_address_valid": 5, "source_ack_sync": 2},
                "latency": "2--3 destination cycles to visibility plus two source cycles before reuse",
                "equal_clock_sustainable_rate_upper_bound": 0.25,
                "lossless_requirement": "TX admission must stop until acknowledge returns",
                "current_a7_compatible": False,
                "rejection": "A7 event_ready is not driven by RX acknowledgement; a standalone mailbox can be overwritten",
            },
            "gray_pointer_async_fifo": {
                "implemented": False,
                "depth2_state_bits_lower_bound": fifo_state_lower_bound(2),
                "depth4_state_bits_lower_bound": fifo_state_lower_bound(4),
                "latency": "normally 2--3 destination cycles before first word, then up to one event/core cycle",
                "sustainable_rate": "min(write rate, read rate); finite depth cannot absorb permanent write>read load",
                "lossless_requirement": "full backpressure must propagate to TX or a proved finite arrival/backlog bound must size the FIFO",
                "current_a7_compatible": False,
                "rejection": "A7 RX exposes no ready/full path; overflow would be detection, not lossless delivery",
            },
        },
        "fair_fixed_endpoint_state_bits": {
            "consumer_boundary_added_to_every_link": 6,
            "parallel4_link_plus_consumer": 17,
            "ddr2_link_plus_consumer": 19,
            "serial1_link_plus_consumer": 22,
            "note": "parallel4 and DDR2 use the identical seen-toggle/address/valid observation boundary; upstream collector/FIFO state is excluded",
        },
        "suite_summary": summaries,
        "runs": rows,
        "recommendation": "GO_RESTRICTED_PHASE_RELATED_R1_ONLY",
        "arbitrary_clock_cdc_status": "HOLD_REQUIRES_END_TO_END_BACKPRESSURE",
        "inclusion_boundary": {
            "included": [
                "six bits of downstream core-clock state",
                "toggle-change detection and four-bit bundled-address capture",
                "standalone RTL, lockstep TB, executable trace model",
            ],
            "required_but_not_included": [
                "generated/related-clock STA proving the frozen phase and half-cycle bundled-data path",
                "A7 TX/RX and any upstream same-cycle event collector or queue",
                "clock tree, skew, recovery/removal, downstream logic and physical PPA",
            ],
        },
        "reset_rdc_contract": [
            "stop admission and fully drain the A7 link",
            "assert A7 reset (rst_n=0) only while burst clock is low",
            "assert synchronous core_reset_i and provide at least one core edge",
            "release A7 reset while core_reset_i remains asserted so retire_toggle is known zero",
            "release core_reset_i synchronously; no event may occur until that edge completes",
            "one-sided or in-flight reset is outside the lossless contract",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--a7-repo", type=Path, required=True)
    parser.add_argument("--full-manifest", type=Path, required=True)
    parser.add_argument("--full-trace-dir", type=Path, required=True)
    parser.add_argument("--cap-manifest", type=Path, required=True)
    parser.add_argument("--cap-trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(
        args.registry, args.generator, args.a7_repo,
        {"full50": (args.full_manifest, args.full_trace_dir),
         "capacity22": (args.cap_manifest, args.cap_trace_dir)},
    )
    w4.base.write_json(args.output, report)
    print(f"A6_W5_REPORT output={args.output} recommendation={report['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
