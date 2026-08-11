#!/usr/bin/env python3
"""Conservative A6 replay bound to A7 W4 commit db3f04f."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path


BASE_PATH = Path(__file__).with_name("a6_w4_fixed_pin_replay.py")
BASE_SPEC = importlib.util.spec_from_file_location("a6_w4_fixed_pin_base", BASE_PATH)
assert BASE_SPEC and BASE_SPEC.loader
base = importlib.util.module_from_spec(BASE_SPEC)
sys.modules[BASE_SPEC.name] = base
BASE_SPEC.loader.exec_module(base)

BOUND_COMMIT = "db3f04fe0e01699e63c596145fe71effc601e57c"


LINKS = {
    "parallel4": base.LinkSpec(
        "parallel4", 4, 1, 1, 2, 11,
        {"tx_link_data": 4, "tx_enable": 1, "icg_enable_latch": 1,
         "rx_address": 4, "rx_retire_toggle": 1},
        4, 2,
        {"characterized_icg_mapping": 1,
         "forwarded_strobe_output_buffer": 1},
        "hold last four-bit address",
    ),
    "ddr2": base.LinkSpec(
        "ddr2", 2, 1, 1, 2, 13,
        {"tx_event_addr_q": 4, "tx_frame_enable_q": 1,
         "icg_enable_latched_q": 1, "rx_low_symbol_q": 2,
         "rx_retire_addr_o": 4, "rx_retire_toggle_o": 1},
        4, 2,
        {"characterized_icg_mapping": 1, "oddr_data_cells": 2,
         "iddr_data_cells": 2, "forwarded_clock_output_buffer": 1},
        "db3f04f RTL alternates retained low/high symbols every ref-clock period",
    ),
    "serial1": base.LinkSpec(
        "serial1", 1, 1, 2, 4, 16,
        {"tx_address_q": 4, "tx_busy_q": 1, "tx_second_pair_q": 1,
         "icg_enable_latch": 1, "rx_rise_bit_q": 1,
         "rx_first_pair_q": 2, "rx_second_pair_q": 1,
         "rx_address": 4, "rx_retire_toggle": 1},
        4, 2,
        {"characterized_icg_mapping": 1, "oddr_data_cells": 1,
         "iddr_data_cells": 1, "forwarded_clock_output_buffer": 1},
        "hold last serialized bit",
    ),
}

STRUCTURAL_PROXY = {
    "parallel4": {"functional_cells": 11, "state_bits": 11},
    "ddr2": {"functional_cells": 13, "state_bits": 13},
    "serial1": {"functional_cells": 26, "state_bits": 16},
}


def validate_inputs(
    registry_path: Path, generator_path: Path, a7_repo: Path,
    suite_inputs: dict[str, tuple[Path, Path]],
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1 or registry.get("a7_commit") != BOUND_COMMIT:
        raise base.ReplayError("invalid db3f04f W4 registry")
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
            raise base.ReplayError(f"A7 bound source mismatch: {relative}")

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


def activity_detail(
    events: list[base.Event], *, stim_cycles: int,
    spec: base.LinkSpec, link_ratio: int,
) -> dict[str, int]:
    """Split active/idle data activity and count the explicit ICG latch."""
    arrivals: dict[int, list[base.Event]] = defaultdict(list)
    for event in events:
        arrivals[event.occurrence_cycle].append(event)
    queue: deque[base.Event] = deque()
    active: base.Event | None = None
    remaining = 0
    retained_address = 0
    data_state = 0
    enable_previous = False
    latch_toggles = active_data = idle_data = 0
    period = 0
    total_stim_periods = stim_cycles * link_ratio

    while period < total_stim_periods or queue or active is not None:
        core_cycle = period // link_ratio
        if period % link_ratio == 0 and core_cycle < stim_cycles:
            queue.extend(arrivals.get(core_cycle, []))
        if active is None and queue:
            active = queue.popleft()
            remaining = spec.periods_per_event
        enabled = active is not None
        if enabled != enable_previous:
            latch_toggles += 1
        enable_previous = enabled

        if spec.name == "ddr2":
            if active is not None:
                retained_address = active.address
            low = retained_address & 3
            high = (retained_address >> 2) & 3
            toggles = base._toggle_width(data_state, low, 2)
            toggles += base._toggle_width(low, high, 2)
            data_state = high
            if enabled:
                active_data += toggles
            else:
                idle_data += toggles

        if active is not None:
            remaining -= 1
            if remaining == 0:
                active = None
        period += 1

    # A drain ending on an active period needs a subsequent source-clock period
    # to deassert frame enable and update the low-transparent ICG latch. Charge
    # this quiescence separately so delivery throughput is not silently changed.
    terminal_quiesce = int(enable_previous)
    if terminal_quiesce:
        latch_toggles += 1
    quiesce_data = 0
    if terminal_quiesce and spec.name == "ddr2":
        low = retained_address & 3
        high = (retained_address >> 2) & 3
        quiesce_data = (base._toggle_width(data_state, low, 2)
                         + base._toggle_width(low, high, 2))
    return {
        "active_data_toggles": active_data,
        "idle_data_toggles": idle_data,
        "icg_enable_latch_toggles": latch_toggles,
        "terminal_quiesce_periods": terminal_quiesce,
        "terminal_quiesce_data_toggles": quiesce_data,
        "terminal_quiesce_internal_clock_edges": (
            terminal_quiesce * spec.internal_clock_edges_per_period),
        "terminal_quiesce_icg_input_edges": (
            terminal_quiesce * spec.icg_input_edges_per_period),
    }


def augment_summary(rows: list[dict[str, object]], summaries: list[dict[str, object]]) -> None:
    grouped: dict[tuple[str, str, int], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["suite"]), str(row["link"]), int(row["link_ratio"]))].append(row)
    for summary in summaries:
        key = (str(summary["suite"]), str(summary["link"]), int(summary["link_ratio"]))
        group = grouped[key]
        events = sum(int(row["delivered"]) for row in group)
        for field in ("active_data_toggles", "idle_data_toggles",
                      "icg_enable_latch_toggles", "terminal_quiesce_data_toggles"):
            summary[f"{field}_per_event"] = sum(int(row[field]) for row in group) / events
        summary["terminal_quiesce_periods"] = sum(
            int(row["terminal_quiesce_periods"]) for row in group)
        periods_with_quiesce = sum(
            int(row["link_periods_including_terminal_quiesce"]) for row in group)
        summary["events_per_pin_cycle_including_terminal_quiesce"] = (
            events / (int(summary["pins"]) * periods_with_quiesce))
        summary["physical_link_toggles_per_event_including_terminal_quiesce_data"] = (
            sum(int(row["physical_link_toggles_including_terminal_quiesce_data"])
                for row in group) / events)
        summary["internal_clock_source_edges_per_event_including_quiesce"] = (
            sum(int(row["internal_clock_source_edges"])
                + int(row["terminal_quiesce_internal_clock_edges"]) for row in group)
            / events)
        summary["generic_structural_proxy"] = STRUCTURAL_PROXY[str(summary["link"])]


def evaluate(
    registry_path: Path, generator_path: Path, a7_repo: Path,
    suite_inputs: dict[str, tuple[Path, Path]],
) -> dict[str, object]:
    registry, runs_by_suite = validate_inputs(
        registry_path, generator_path, a7_repo, suite_inputs)
    rows: list[dict[str, object]] = []
    for suite, runs in runs_by_suite.items():
        trace_dir = suite_inputs[suite][1]
        for run in runs:
            events = base.load_events(
                trace_dir / f"{run['name']}.events.jsonl",
                registry["suites"][suite]["traces"][run["name"]]["sha256"],
            )
            for ratio in (1, 2, 4):
                for spec in LINKS.values():
                    result = base.replay(
                        events, suite=suite, run=run["name"],
                        stim_cycles=run["stim_cycles"], spec=spec,
                        link_ratio=ratio,
                    )
                    row = result.public(spec)
                    detail = activity_detail(
                        events, stim_cycles=run["stim_cycles"],
                        spec=spec, link_ratio=ratio)
                    if spec.name != "ddr2":
                        detail["active_data_toggles"] = result.physical_data_toggles
                    row.update(detail)
                    quiesce_periods = detail["terminal_quiesce_periods"]
                    quiesce_data = detail["terminal_quiesce_data_toggles"]
                    row["link_periods_including_terminal_quiesce"] = (
                        result.link_periods + quiesce_periods)
                    row["events_per_pin_cycle_including_terminal_quiesce"] = (
                        result.delivered
                        / (spec.pins * (result.link_periods + quiesce_periods)))
                    row["physical_link_toggles_including_terminal_quiesce_data"] = (
                        result.physical_link_toggles + quiesce_data)
                    row["physical_link_toggles_per_event_including_terminal_quiesce_data"] = (
                        (result.physical_link_toggles + quiesce_data)
                        / result.delivered)
                    rows.append(row)
    summaries = base.aggregate(rows, LINKS)
    augment_summary(rows, summaries)
    return {
        "schema_version": 2,
        "candidate": "a6_w4_a7_db3f04f_conservative_fixed_pin_followup",
        "a7_bound_commit": BOUND_COMMIT,
        "historical_audit_commit": "8fed98090427a3da9779de68266bdc1411a90f7e",
        "registry": str(registry_path.resolve()),
        "registry_sha256": base.sha256_file(registry_path),
        "historical_assumption_diff": {
            "ddr2_fixed_state_bits": {"historical": 12, "latest": 13,
                                      "reason": "explicit low-phase ICG enable latch"},
            "parallel4_fixed_state_bits": {"historical": 10, "latest": 11,
                                           "reason": "same-top reference now includes ICG latch"},
            "serial1_fixed_state_bits": {"historical": 16, "latest": 16,
                                         "reason": "total unchanged; breakdown is now bound RTL proxy"},
            "clock_gate": {"historical": "combinational expression/physical ICG required",
                           "latest": "synthesizable low-transparent latch boundary; characterized ICG still required"},
            "fault_oracle": {"historical": "not included",
                             "latest": "strict test-only action/edge/symbol oracle; no runtime state or containment"},
            "idle_activity": {"forwarded_clock": "stopped",
                              "data": "retained low/high halves still alternate",
                              "ref_and_sample_clocks": "continue unless separately gated"},
        },
        "clock_ratio_contract": {
            "R": "link reference periods per core cycle",
            "ratios": [1, 2, 4],
            "max_logical_events_per_core_cycle_proxy": {
                "parallel4": "R", "ddr2": "R", "serial1": "R/2"},
        },
        "link_specs": {name: asdict(spec) | {"pins": spec.pins}
                       for name, spec in LINKS.items()},
        "strict_oracle_scope": {
            "classification": "test_only_not_synthesized",
            "mutations": 10,
            "runtime_fault_detection_or_containment": False,
        },
        "suite_summary": summaries,
        "runs": rows,
        "decision": "HOLD_PHYSICAL_AND_FULL_ENDPOINT_PPA",
        "decision_reasons": [
            "generic 11/13/16-bit state and 11/13/26-cell results are structural proxies, not physical PPA",
            "characterized ICG, DDR I/O cells, CTS, pads, routing, CDC, PVT timing, and extracted power remain absent",
            "A7 has no ingress queue and official same-cycle multiplicity requires external collection/storage",
            "the DDR data mux still toggles retained address halves during idle while source clocks continue",
            "the strict oracle is test-only and adds no synthesizable runtime protection",
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
    print(f"A6_W4_DB3F04F_REPORT output={args.output} decision={report['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
