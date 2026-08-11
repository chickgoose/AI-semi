#!/usr/bin/env python3
"""W4 analytical tournament for the exact A4 moving core and A7 DDR link.

The script reads the named foreign commits through ``git show``.  It never
checks them out and never writes their worktrees.  Generated common traces and
all transient files live below a secure temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import pathlib
import runpy
import statistics
import subprocess
import sys
import tempfile
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable


A4_REPO = pathlib.Path("/home/chickgoose/projects/a4")
A7_REPO = pathlib.Path("/home/chickgoose/projects/a7")
COMMON_REPO = pathlib.Path("/home/chickgoose/projects/a1")
A4_COMMIT = "850fbcfa4ad168b1250223610780f11378f6c391"
A7_COMMIT = "31947a71ddfcf678f6cd593954df34b27806a63d"
COMMON_COMMIT = "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be"
A4_MODEL_PATH = "rtl/candidates/a4_moving_block_tree/model.py"
A7_PATHS = (
    "rtl/candidates/a7_event_triggered_ddr_burst_link/a7_ddr_burst_tx.sv",
    "rtl/candidates/a7_event_triggered_ddr_burst_link/a7_ddr_burst_rx.sv",
    "rtl/candidates/a7_event_triggered_ddr_burst_link/a7_event_triggered_ddr_burst_link.sv",
)
GENERATOR_PATH = "benchmarks/clean_slate_aer/generate_trace.py"
OFFICIAL_PATH = "scripts/common_suite_official.py"
EXPECTED_GENERATOR_SHA256 = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
EXPECTED_OFFICIAL_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
EXPECTED_MANIFESTS = {
    "full50": ("manifest.neutrality-n16.json", "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9", 50),
    "capacity22": ("manifest.multilane-n16.json", "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62", 22),
}
GIT = pathlib.Path("/usr/bin/git")


class TournamentError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


def run(arguments: list[str], cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(
        arguments, cwd=cwd, text=True, capture_output=True, check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode:
        raise TournamentError(
            f"command failed ({result.returncode}): {' '.join(arguments)}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout.strip()


def git_bytes(repo: pathlib.Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        [str(GIT), "-C", str(repo), "show", f"{commit}:{path}"],
        capture_output=True, check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode:
        raise TournamentError(result.stderr.decode(errors="replace"))
    return result.stdout


def verify_commit(repo: pathlib.Path, declared: str) -> None:
    actual = run([str(GIT), "-C", str(repo), "rev-parse", f"{declared}^{{commit}}"])
    if actual != declared:
        raise TournamentError(f"commit mismatch for {repo}: {actual}")


def materialize_common_snapshot(destination: pathlib.Path) -> pathlib.Path:
    """Materialize only the bound common commit, not the mutable A1 worktree."""

    archive = destination / "common.tar"
    snapshot = destination / "common"
    snapshot.mkdir()
    run([
        str(GIT), "-C", str(COMMON_REPO), "archive", "--format=tar",
        f"--output={archive}", COMMON_COMMIT,
    ])
    run(["/usr/bin/tar", "-xf", str(archive), "-C", str(snapshot)])
    archive.unlink()
    return snapshot


def load_exact_a4_model(temp_root: pathlib.Path):
    source = git_bytes(A4_REPO, A4_COMMIT, A4_MODEL_PATH)
    path = temp_root / "a4_exact_model.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("w4_a4_exact_model", path)
    if spec is None or spec.loader is None:
        raise TournamentError("could not import exact A4 model")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, sha256_bytes(source)


def percentile(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * pct / 100) - 1)]


def load_occurrences(path: pathlib.Path) -> list[tuple[int, int]]:
    occurrences = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            occurrences.append((event["occurrence_cycle"], event["logical_source"]))
    return occurrences


def encoded_node(event: Any | None) -> int:
    """Register-transition proxy: invalid blocks are represented by all zeroes."""

    if event is None:
        return 0
    return (1 << 36) | ((int(event.source) & 0xF) << 32) | (int(event.payload) & 0xFFFFFFFF)


def assert_address_only_event(event: Any | None) -> None:
    if event is None:
        return
    if not 0 <= int(event.source) < 16 or int(event.payload) != int(event.source):
        raise TournamentError("event violates zero-extended address-only identity")


@dataclass
class CoreRun:
    offered: int
    accepted: int
    delivered: int
    overrun: int
    cycles: int
    bubbles: int
    occurrence_latencies: list[int]
    accepted_latencies: list[int]
    delivered_addresses: list[int]
    delivered_cycles: list[int]
    state_toggle_proxy: int
    max_core_node_occupancy: int
    max_source_latches_occupied: int
    max_dut_visible_word: int


def run_core(model_module: Any, max_advance: int, occurrences: Iterable[tuple[int, int]]) -> CoreRun:
    model = model_module.MovingBlockTreeModel(16, max_advance)
    by_cycle: dict[int, list[int]] = {}
    offered = 0
    for cycle, source in occurrences:
        by_cycle.setdefault(cycle, []).append(source)
        offered += 1
    last_offer = max(by_cycle, default=-1)
    pending: list[int | None] = [None] * 16
    pending_created: list[int | None] = [None] * 16
    pending_scoreboard_id: list[int | None] = [None] * 16
    accepted_sidecar: list[deque[tuple[int, int, int]]] = [
        deque() for _ in range(16)
    ]
    next_scoreboard_id = 0
    accepted = delivered = overrun = bubbles = state_toggles = 0
    max_core_occupancy = 0
    max_source_latches = 0
    max_dut_word = 0
    occurrence_latencies: list[int] = []
    accepted_latencies: list[int] = []
    delivered_addresses: list[int] = []
    delivered_cycles: list[int] = []

    for cycle in range(100_000):
        for source in by_cycle.get(cycle, ()):
            next_scoreboard_id += 1
            if pending[source] is not None:
                overrun += 1
            else:
                # DUT-visible event identity is address-only.  The unique ID
                # exists only in this TB scoreboard sidecar and never enters a
                # model node or the state-toggle proxy.
                pending[source] = source
                pending_created[source] = cycle
                pending_scoreboard_id[source] = next_scoreboard_id
        max_source_latches = max(
            max_source_latches, sum(item is not None for item in pending)
        )
        valid = [item is not None for item in pending]
        payload = [
            source if item is not None else 0
            for source, item in enumerate(pending)
        ]
        before_nodes = [encoded_node(item) for item in model.nodes]
        before_phase = tuple(model.phase)
        had_work = any(valid) or model.occupancy() > 0
        result = model.step(valid, payload, True)
        for item in model.nodes:
            assert_address_only_event(item)
            if item is not None:
                max_dut_word = max(max_dut_word, int(item.payload))
        max_core_occupancy = max(max_core_occupancy, model.occupancy())
        after_nodes = [encoded_node(item) for item in model.nodes]
        state_toggles += sum((a ^ b).bit_count() for a, b in zip(before_nodes, after_nodes))
        state_toggles += sum(a != b for a, b in zip(before_phase, model.phase))
        if had_work and not result.retire_valid:
            bubbles += 1
        for source, did_accept in enumerate(result.source_ready):
            if did_accept:
                scoreboard_id = pending_scoreboard_id[source]
                if scoreboard_id is None or pending_created[source] is None:
                    raise TournamentError("accepted event lacks TB sidecar identity")
                accepted_sidecar[source].append(
                    (scoreboard_id, cycle, pending_created[source])
                )
                accepted += 1
                pending[source] = None
                pending_created[source] = None
                pending_scoreboard_id[source] = None
        if result.retired is not None:
            assert_address_only_event(result.retired)
            source = result.retired.source
            if not accepted_sidecar[source]:
                raise TournamentError("phantom or duplicate retirement")
            _scoreboard_id, accepted_cycle, occurrence_cycle = (
                accepted_sidecar[source].popleft()
            )
            accepted_latencies.append(cycle - accepted_cycle + 1)
            occurrence_latencies.append(cycle - occurrence_cycle + 1)
            delivered_addresses.append(result.retired.source)
            delivered_cycles.append(cycle)
            delivered += 1
        if cycle > last_offer and not any(item is not None for item in pending) and model.occupancy() == 0:
            if accepted != delivered or any(accepted_sidecar):
                raise TournamentError("core did not drain accepted events")
            return CoreRun(
                offered, accepted, delivered, overrun, cycle + 1, bubbles,
                occurrence_latencies, accepted_latencies, delivered_addresses,
                delivered_cycles, state_toggles, max_core_occupancy,
                max_source_latches, max_dut_word,
            )
    raise TournamentError("core drain limit exceeded")


def link_wire_toggles(
    kind: str,
    addresses: list[int],
    event_cycles: list[int],
    core_cycles: int,
    ratio: int,
) -> int:
    """Count old-commit external wire activity, including its idle DDR mux.

    Commit 31947a7 leaves ``burst_data_o`` driven by a ref-clock-selected mux
    even when its forwarded burst clock is stopped.  Therefore the two data
    pins can alternate low/high address symbols throughout idle; event-only
    transition accounting would undercount this exact RTL.
    """

    if kind == "parallel4":
        total = 0
        previous = 0
        for address in addresses:
            total += (previous ^ address).bit_count() + 2
            previous = address
        return total
    if kind != "ddr2":
        raise TournamentError(f"unknown link: {kind}")

    events = dict(zip(event_cycles, addresses))
    held_address = 0
    previous_pin_symbol = 0
    total = 0
    for core_cycle in range(core_cycles):
        for link_slot in range(ratio):
            address = events.get(core_cycle) if link_slot == 0 else None
            if address is not None:
                held_address = address
            low = held_address & 3
            high = (held_address >> 2) & 3
            total += (previous_pin_symbol ^ low).bit_count()
            total += (low ^ high).bit_count()
            if address is not None:
                total += 2  # one rising and one falling forwarded-clock edge
            previous_pin_symbol = high
    return total


def ddr_register_toggles(
    addresses: list[int], event_cycles: list[int], core_cycles: int, ratio: int
) -> int:
    """Exact old-commit 12-bit TX/RX state proxy under a legal launch envelope."""

    events = dict(zip(event_cycles, addresses))
    tx_address = low_symbol = retire_address = 0
    frame_enable = 0
    total = 0
    for core_cycle in range(core_cycles):
        for link_slot in range(ratio):
            address = events.get(core_cycle) if link_slot == 0 else None
            event_valid = address is not None
            if event_valid:
                total += (tx_address ^ address).bit_count()
                tx_address = address
            next_enable = int(event_valid)
            total += frame_enable != next_enable
            frame_enable = next_enable
            if event_valid:
                low = address & 3
                total += (low_symbol ^ low).bit_count()
                total += (retire_address ^ address).bit_count()
                total += 1  # retire_toggle_o
                low_symbol = low
                retire_address = address
    if frame_enable:
        total += 1  # next idle ref edge deasserts frame_enable_q
    return total


def aggregate_core(runs: list[CoreRun]) -> dict[str, Any]:
    latencies = [v for run_item in runs for v in run_item.occurrence_latencies]
    accepted_latencies = [v for run_item in runs for v in run_item.accepted_latencies]
    cycles = sum(run_item.cycles for run_item in runs)
    delivered = sum(run_item.delivered for run_item in runs)
    return {
        "runs": len(runs),
        "offered": sum(run_item.offered for run_item in runs),
        "core_accepted": sum(run_item.accepted for run_item in runs),
        "core_delivered": delivered,
        "overrun": sum(run_item.overrun for run_item in runs),
        "cycles": cycles,
        "throughput": round(delivered / cycles, 9),
        "output_bubbles": sum(run_item.bubbles for run_item in runs),
        "mean_core_e2e_latency": round(statistics.mean(latencies), 9),
        "p95_core_e2e_latency": percentile(latencies, 95),
        "p99_core_e2e_latency": percentile(latencies, 99),
        "max_core_e2e_latency": max(latencies),
        "mean_accept_to_core_delivery": round(statistics.mean(accepted_latencies), 9),
        "core_state_toggle_proxy": sum(run_item.state_toggle_proxy for run_item in runs),
        "max_core_node_occupancy": max(
            run_item.max_core_node_occupancy for run_item in runs
        ),
        "max_source_latches_occupied": max(
            run_item.max_source_latches_occupied for run_item in runs
        ),
        "max_dut_visible_word": max(run_item.max_dut_visible_word for run_item in runs),
        "addresses_by_run": [run_item.delivered_addresses for run_item in runs],
        "delivery_cycles_by_run": [run_item.delivered_cycles for run_item in runs],
        "core_cycles_by_run": [run_item.cycles for run_item in runs],
    }


def architecture_row(core: dict[str, Any], advance: int, link: str, ratio: int) -> dict[str, Any]:
    addresses_by_run = core["addresses_by_run"]
    cycles_by_run = core["delivery_cycles_by_run"]
    core_cycles_by_run = core["core_cycles_by_run"]
    is_ddr = link == "ddr2"
    # Exact A7 phase contract: admission at ref rising edge, burst rising edge
    # one quarter period later, and commit at the following burst falling edge.
    link_delay = Fraction(3, 4 * ratio) if is_ddr else Fraction(0, 1)
    # Aggregate percentiles are computed from exact core samples plus the
    # constant link commit delay; no survivor set changes at the link.
    # The core summary retains only its aggregate, so shift those exact order
    # statistics rather than fabricate a link queue distribution.
    wire_toggles = sum(
        link_wire_toggles(link, addresses, cycles, core_cycles, ratio)
        for addresses, cycles, core_cycles in zip(
            addresses_by_run, cycles_by_run, core_cycles_by_run
        )
    )
    register_toggles = (
        sum(
            ddr_register_toggles(addresses, cycles, core_cycles, ratio)
            for addresses, cycles, core_cycles in zip(
                addresses_by_run, cycles_by_run, core_cycles_by_run
            )
        )
        if is_ddr else 0
    )
    # Old A7 has two continuously toggling unit clocks at the link boundary:
    # ref_clk_i drives TX state/mux phase and sample_clk_i drives the gate
    # input.  Keep their unweighted edge count explicit because clock-tree
    # capacitance is unknown and must not be disguised as a data-bit toggle.
    internal_clock_edges = 4 * ratio * core["cycles"] if is_ddr else 0
    delivered = core["core_delivered"]
    analytical_rate_compatible = ratio == 1
    row = {
        key: value for key, value in core.items()
        if key not in {
            "addresses_by_run", "delivery_cycles_by_run", "core_cycles_by_run"
        }
    }
    row.update({
        "core": "moving_two_step" if advance == 2 else "fixed_one_step",
        "link": link,
        "link_ratio_R": ratio,
        "link_accepted": delivered,
        "link_delivered_capacity_envelope": delivered,
        "link_service_capacity_events_per_core_cycle": ratio,
        "link_service_utilization": round(delivered / (ratio * core["cycles"]), 9),
        "boundary_buffer_required_events": 0,
        "max_boundary_backlog_events": 0,
        "core_internal_event_slots": 31,
        "ingress_source_latch_slots": 16,
        "pins": 3 if is_ddr else 5,
        "core_state_bits": 1162,
        "link_state_bits": 12 if is_ddr else 0,
        "total_state_bits": 1174 if is_ddr else 1162,
        "child_control_checks_per_core_cycle": 60 if advance == 2 else 30,
        "child_control_touch_proxy": (60 if advance == 2 else 30) * core["cycles"],
        "max_local_merge_depth": advance,
        "link_wire_toggle_proxy": wire_toggles,
        "link_register_toggle_proxy": register_toggles,
        "link_internal_clock_edge_proxy": internal_clock_edges,
        "total_toggle_proxy": core["core_state_toggle_proxy"] + wire_toggles + register_toggles,
        "total_activity_proxy_including_unit_clock_edges": (
            core["core_state_toggle_proxy"] + wire_toggles
            + register_toggles + internal_clock_edges
        ),
        "mean_link_toggles_per_event": round((wire_toggles + register_toggles) / delivered, 9),
        "mean_link_activity_per_event_including_unit_clock_edges": round(
            (wire_toggles + register_toggles + internal_clock_edges) / delivered, 9
        ),
        "link_commit_delay_core_cycles": str(link_delay),
        "mean_end_to_end_latency": round(core["mean_core_e2e_latency"] + float(link_delay), 9),
        "p95_end_to_end_latency": core["p95_core_e2e_latency"] + float(link_delay),
        "p99_end_to_end_latency": core["p99_core_e2e_latency"] + float(link_delay),
        "throughput_bottleneck": "core_or_ingress_not_link",
        "analytical_rate_compatible": (
            analytical_rate_compatible if is_ddr else True
        ),
        "executed_composed_rtl_evidence": False,
        "composed_reset_path_evidence": False,
        "extra_capture_opportunities_per_valid_core_period": ratio - 1 if is_ddr else 0,
        "eligibility": (
            "ANALYTICAL_ONLY" if (not is_ddr or analytical_rate_compatible)
            else "HOLD_MISSING_ONE_LINK_PERIOD_LAUNCH_QUALIFIER"
        ),
    })
    return row


def generate_suite(snapshot: pathlib.Path, suite: str, output: pathlib.Path) -> tuple[list[str], pathlib.Path]:
    name, expected_sha, expected_count = EXPECTED_MANIFESTS[suite]
    manifest = snapshot / "benchmarks/clean_slate_aer" / name
    if sha256_path(manifest) != expected_sha:
        raise TournamentError(f"{suite}: manifest SHA mismatch")
    generator = snapshot / GENERATOR_PATH
    if sha256_path(generator) != EXPECTED_GENERATOR_SHA256:
        raise TournamentError("generator SHA mismatch")
    run([sys.executable, "-B", str(generator), "--manifest", str(manifest), "--output-dir", str(output)], snapshot)
    index_path = output / "generation-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    names = [item["run"]["name"] for item in index["runs"]]
    if index.get("generator_version") != "4.0" or len(names) != expected_count:
        raise TournamentError(f"{suite}: generator version/count mismatch")
    return names, index_path


def evaluate() -> dict[str, Any]:
    for repo, commit in ((A4_REPO, A4_COMMIT), (A7_REPO, A7_COMMIT), (COMMON_REPO, COMMON_COMMIT)):
        verify_commit(repo, commit)
    a7_hashes = {path: sha256_bytes(git_bytes(A7_REPO, A7_COMMIT, path)) for path in A7_PATHS}
    with tempfile.TemporaryDirectory(prefix="a9-w4-") as temp_name:
        temp_root = pathlib.Path(temp_name)
        model_module, a4_model_hash = load_exact_a4_model(temp_root)
        snapshot = materialize_common_snapshot(temp_root)
        if sha256_path(snapshot / OFFICIAL_PATH) != EXPECTED_OFFICIAL_SHA256:
            raise TournamentError("official policy SHA mismatch")
        policy = runpy.run_path(str(snapshot / OFFICIAL_PATH))
        suites: dict[str, Any] = {}
        for suite in ("full50", "capacity22"):
            generated = temp_root / f"generated-{suite}"
            names, index_path = generate_suite(snapshot, suite, generated)
            expected_names = list(policy["SUITES"][suite]["names"])
            if names != expected_names:
                raise TournamentError(f"{suite}: exact ordered run set mismatch")
            index = json.loads(index_path.read_text(encoding="utf-8"))
            fixed_runs = []
            moving_runs = []
            for item in index["runs"]:
                trace = generated / item["trace_file"]
                expected_trace_hash = policy["TRACE_SHA256"][item["run"]["name"]]
                if sha256_path(trace) != expected_trace_hash:
                    raise TournamentError(f"{suite}/{item['run']['name']}: trace SHA mismatch")
                occurrences = load_occurrences(trace)
                fixed_runs.append(run_core(model_module, 1, occurrences))
                moving_runs.append(run_core(model_module, 2, occurrences))
            cores = {"fixed_one_step": aggregate_core(fixed_runs), "moving_two_step": aggregate_core(moving_runs)}
            rows = []
            for ratio in (1, 2, 4):
                for advance, core_name in ((1, "fixed_one_step"), (2, "moving_two_step")):
                    for link in ("parallel4", "ddr2"):
                        rows.append(architecture_row(cores[core_name], advance, link, ratio))
            suites[suite] = {
                "run_count": len(names),
                "manifest": EXPECTED_MANIFESTS[suite][0],
                "manifest_sha256": EXPECTED_MANIFESTS[suite][1],
                "architectures": rows,
            }
    return {
        "schema_version": 1,
        "decision": "SIMPLE_SERIAL_COMPOSITION_NOT_NEW_ARCHITECTURE",
        "qualification": "LOCAL_CYCLE_ANALYTICAL_ONLY",
        "physical_qualification": "HOLD",
        "clock_boundary_rule": {
            "R1": "analytical legal-launch envelope is rate-compatible; no composed RTL or reset-path evidence",
            "R2_R4": "capacity envelope only; exact A4 level-valid to faster A7 ref clock duplicates/early-samples frames without an unimplemented one-link-period launch qualifier",
            "qualifier_cost": "unknown_and_not_included",
            "added_queue_or_adapter": False,
        },
        "provenance": {
            "a4_commit": A4_COMMIT,
            "a4_model_path": A4_MODEL_PATH,
            "a4_model_sha256": a4_model_hash,
            "a7_commit": A7_COMMIT,
            "a7_rtl_sha256": a7_hashes,
            "a7_scope": "frozen pre-ICG commit 31947a7 only",
            "a7_latest_observed_but_excluded": {
                "commit": "db3f04fe0e01699e63c596145fe71effc601e57c",
                "state_bits": 13,
                "difference": "latest fault-claim-gap closure; not substituted into frozen 31947a7 tournament",
                "structural_evidence_ancestor": "a349d64d8b8b3d4398a258926af493b5da1e3ac2",
            },
            "common_commit": COMMON_COMMIT,
            "generator_version": "4.0",
            "generator_sha256": EXPECTED_GENERATOR_SHA256,
            "official_policy_sha256": EXPECTED_OFFICIAL_SHA256,
        },
        "accounting": {
            "dut_visible_event_word": "32-bit zero-extension of 4-bit logical source/address; equality asserted in every occupied node and retirement",
            "tb_identity_sidecar": "monotonic occurrence ID and timing kept only in source-local scoreboard deques",
            "core_register_proxy": "31*(32 address + 4 source + 1 valid)+15 phase = 1162 bits",
            "a7_link_register_proxy": "TX(4 address + 1 enable)+RX(2 partial + 4 address + 1 toggle)=12 bits",
            "core_toggle_proxy": "Hamming transitions of valid event blocks (invalid encoded zero) plus phase bits",
            "link_toggle_proxy": "old-commit actual address sequence, forwarded-clock edges, 12 register transitions, burst_data low/high mux transitions during idle, plus separately exposed ref/sample unit-clock edges",
            "parallel_link_state": "zero added state; direct registered-core output boundary",
            "storage_boundary": "zero means only no added A4-to-A7 FIFO; A4 retains 31 internal slots and the workload driver retains 16 source latches",
        },
        "suites": suites,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    document = evaluate()
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output:
        if args.output.exists():
            raise SystemExit(f"refusing to replace output: {args.output}")
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    print("W4_A9_TOURNAMENT_PASS full50=50 capacity22=22", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
