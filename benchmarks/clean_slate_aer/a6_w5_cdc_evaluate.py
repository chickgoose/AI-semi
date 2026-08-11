#!/usr/bin/env python3
"""A6 W5 RX burst-clock to downstream core-clock boundary evaluation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path


BASE_PATH = Path(__file__).with_name("a6_w4_fixed_pin_replay.py")
BASE_SPEC = importlib.util.spec_from_file_location("a6_w5_replay_base", BASE_PATH)
assert BASE_SPEC and BASE_SPEC.loader
base = importlib.util.module_from_spec(BASE_SPEC)
sys.modules[BASE_SPEC.name] = base
BASE_SPEC.loader.exec_module(base)

BOUND_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"


def validate_inputs(
    registry_path: Path, generator_path: Path, a7_repo: Path,
    suite_inputs: dict[str, tuple[Path, Path]],
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1 or registry.get("a7_commit") != BOUND_COMMIT:
        raise base.ReplayError("invalid 42377ca W5 production registry")
    if base.sha256_file(generator_path) != registry["generator"]["sha256"]:
        raise base.ReplayError("generator SHA mismatch")
    version_line = next(
        (line for line in generator_path.read_text(encoding="utf-8").splitlines()
         if line.startswith("GENERATOR_VERSION")), "")
    if f'"{registry["generator"]["version"]}"' not in version_line:
        raise base.ReplayError("generator version mismatch")
    for relative, expected in registry["a7_sources"].items():
        content = subprocess.check_output(
            ["git", "-C", str(a7_repo), "show", f"{BOUND_COMMIT}:{relative}"])
        if hashlib.sha256(content).hexdigest() != expected:
            raise base.ReplayError(f"A7 production source mismatch: {relative}")

    runs_by_suite: dict[str, list[dict[str, object]]] = {}
    for suite, (manifest_path, trace_dir) in suite_inputs.items():
        contract = registry["suites"][suite]
        if base.sha256_file(manifest_path) != contract["manifest_sha256"]:
            raise base.ReplayError(f"{suite}: manifest SHA mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = [run["name"] for run in manifest["runs"]]
        if names != contract["run_names"] or set(names) != set(contract["traces"]):
            raise base.ReplayError(f"{suite}: run set mismatch")
        for name in names:
            trace = trace_dir / f"{name}.events.jsonl"
            if (not trace.is_file()
                    or base.sha256_file(trace) != contract["traces"][name]["sha256"]):
                raise base.ReplayError(f"{suite}/{name}: trace SHA mismatch")
        runs_by_suite[suite] = manifest["runs"]
    return registry, runs_by_suite


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
    contract, runs_by_suite = validate_inputs(
        registry, generator, a7_repo, suite_inputs)
    rows: list[dict[str, object]] = []
    for suite, runs in runs_by_suite.items():
        trace_dir = suite_inputs[suite][1]
        for run in runs:
            events = base.load_events(
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
        "a7_bound_commit": BOUND_COMMIT,
        "registry_sha256": base.sha256_file(registry),
        "comparison": {
            "phase_related_r1_capture": {
                "implemented": True,
                "implementation": "A7 42377ca production a7_r1_retire_observer",
                "observer_state_bits": 6,
                "observer_state_breakdown": {
                    "seen_toggle": 1, "downstream_address": 4,
                    "downstream_valid": 1},
                "complete_production_endpoint_state_bits": {
                    "parallel4": 18, "ddr2": 20},
                "complete_production_endpoint_charged_functional_cells": {
                    "parallel4": 27, "ddr2": 29},
                "reset_release_arming_state_bits": 1,
                "synchronizer_bits": 0,
                "reason_no_synchronizer": "clocks must be phase-related and STA-constrained; this is not an asynchronous CDC",
                "rx_commit_to_registered_availability_cycles": 0.25,
                "tx_admission_to_registered_availability_cycles": 1.0,
                "tx_admission_to_synchronous_consumer_retirement_cycles": 2.0,
                "maximum_events_per_core_cycle": 1.0,
                "continuous_one_event_per_cycle": True,
                "sink_contract": "always ready; no retire backpressure or queue in the production endpoint",
                "lossless_scope": "R=1 only, at most one RX commit between ref edges, frozen 4 ns commit-to-ref phase, charged reset arming and drained reset sequence",
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
        "production_fixed_endpoint_state_bits": {
            "common_retire_observer": 6,
            "common_reset_release_arming": 1,
            "parallel4_complete_endpoint": 18,
            "ddr2_complete_endpoint": 20,
            "note": "42377ca production parallel4 and DDR2 include identical launch arming, pending-valid drain guard, and seen-toggle/address/valid observation boundaries; upstream collector/FIFO state is excluded",
        },
        "production_structural_proxy": {
            "parallel4": {"pins": 5, "state_bits": 18,
                          "pre_guard_functional_cells": 23,
                          "drain_guard_cells": 4,
                          "charged_functional_cells": 27},
            "ddr2": {"pins": 3, "state_bits": 20,
                     "pre_guard_functional_cells": 25,
                     "drain_guard_cells": 4,
                     "charged_functional_cells": 29},
            "physical_status": "HOLD",
        },
        "suite_summary": summaries,
        "runs": rows,
        "recommendation": "GO_PRODUCTION_PHASE_RELATED_R1_DIGITAL_ONLY",
        "physical_status": "HOLD",
        "arbitrary_clock_cdc_status": "HOLD_REQUIRES_END_TO_END_BACKPRESSURE",
        "inclusion_boundary": {
            "included": [
                "42377ca production DDR2 and complete parallel reference RTL",
                "one-bit launch arming and six-bit ref-clock retire observer",
                "four-cell common drain guard covering launch, frame, raw pending toggle and registered retire valid",
                "bound production digital regression and structural comparison",
                "A6 executable full50/capacity22 trace model",
            ],
            "required_but_not_included": [
                "generated/related-clock STA proving the frozen phase and half-cycle bundled-data path",
                "any upstream same-cycle event collector or queue",
                "clock tree, skew, recovery/removal, downstream logic and physical PPA",
            ],
        },
        "reset_rdc_contract": [
            "stop admission and fully drain the A7 link",
            "assert A7 reset (rst_n=0) only while burst clock is low",
            "require drain_idle_o only after no same-cycle launch, active frame, raw pending toggle, or registered retire_valid remains",
            "release rst_n while ref_clk_i and sample_clk_i are low after a sample falling edge",
            "allow the first safe ref rising edge to charge reset_release_armed_q; no handshake occurs on that edge",
            "begin ready-valid admission only after event_ready_o rises from that arming edge",
            "the observer and raw RX share the same reset epoch; in-flight reset is outside the lossless contract",
        ],
        "superseded_bindings": [
            "ee590cc standalone synchronous-reset observer is historical only",
            "ca1a209 production binding is superseded because drain omitted launch/registered-valid and the consumer timing was misclassified",
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
    base.write_json(args.output, report)
    print(f"A6_W5_REPORT output={args.output} recommendation={report['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
