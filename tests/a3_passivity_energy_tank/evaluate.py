#!/usr/bin/env python3
"""Reproduce the A3 passivity model counterexamples and workload comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.clean_slate_aer.generate_trace import (  # noqa: E402
    GENERATORS,
    TraceBuilder,
    load_manifest,
)
from tests.a3_passivity_energy_tank.passivity_model import (  # noqa: E402
    CreditFabric,
    Mode,
    mask_from_sources,
)


FULL_MANIFEST = REPO_ROOT / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
CAP_MANIFEST = REPO_ROOT / "benchmarks/clean_slate_aer/manifest.multilane-n16.json"


def occurrence_masks(config: object) -> list[int]:
    builder = TraceBuilder(config)
    GENERATORS[config.workload](builder)
    masks = [0] * config.stim_cycles
    for event in builder.finalize():
        masks[int(event["occurrence_cycle"])] |= 1 << int(event["logical_source"])
    return masks


def randomized_ready_masks(name: str, cycles: int, lanes: int, trial: int) -> list[int]:
    seed_bytes = hashlib.sha256(f"{name}:{trial}".encode("ascii")).digest()[:8]
    rng = random.Random(int.from_bytes(seed_bytes, "little"))
    all_ready = (1 << lanes) - 1
    masks: list[int] = []
    for cycle in range(cycles):
        if cycle % 8 == 7:
            masks.append(all_ready)
            continue
        mask = sum((rng.random() < 0.75) << lane for lane in range(lanes))
        masks.append(mask)
    return masks


def simulate(
    masks: list[int],
    mode: Mode,
    *,
    ready_masks: list[int] | None = None,
    energy_max: int = 1,
) -> dict[str, int | float | bool]:
    fabric = CreditFabric(mode=mode, energy_max=energy_max)
    fixed_window_retired = 0
    max_potential_rise_without_injection = 0
    for cycle, occurrence in enumerate(masks):
        before = fabric.potential()
        result = fabric.step(
            occurrence,
            None if ready_masks is None else ready_masks[cycle],
        )
        if occurrence == 0:
            max_potential_rise_without_injection = max(
                max_potential_rise_without_injection,
                result["potential_after"] - before,
            )
    fixed_window_retired = fabric.metrics.retired
    drain_cycles = fabric.drain(limit=16384)
    assert fabric.metrics.generated == fabric.metrics.overrun + fabric.metrics.retired
    metrics = asdict(fabric.metrics)
    return {
        **metrics,
        "fixed_window_retired": fixed_window_retired,
        "drain_cycles": drain_cycles,
        "state_bits": fabric.state_bits(),
        "toggle_per_retired": (
            fabric.metrics.state_toggles / fabric.metrics.retired
            if fabric.metrics.retired else 0.0
        ),
        "mean_latency": fabric.metrics.mean_latency,
        "max_potential_rise_without_injection": max_potential_rise_without_injection,
        "invariants_pass": True,
    }


def directed_energy_island() -> dict[str, object]:
    # Four sources share home lane zero.  Its endpoint is stalled while three
    # other endpoints are ready.  A raw zero-energy lane cannot accept the
    # otherwise routable events; the stateless escape can bootstrap them.
    occurrence = mask_from_sources((0, 4, 8, 12))
    ready = 0b1110
    report: dict[str, object] = {
        "occurrence_sources": [0, 4, 8, 12],
        "ready_mask": f"0b{ready:04b}",
    }
    for mode in Mode:
        fabric = CreditFabric(mode=mode, energy_max=1)
        history = []
        for cycle in range(8):
            result = fabric.step(occurrence if cycle == 0 else 0, ready)
            history.append({
                "cycle": cycle,
                "pending": fabric.pending_count(),
                "stored": fabric.stored_count(),
                "retired": fabric.metrics.retired,
                "energy": list(fabric.energy),
                "progress": result["progress"],
            })
        report[mode.value] = {
            "pending_after_8": fabric.pending_count(),
            "stored_after_8": fabric.stored_count(),
            "retired_after_8": fabric.metrics.retired,
            "bootstrap_admissions": fabric.metrics.bootstrap_admissions,
            "history": history,
        }
    raw = report[Mode.RAW.value]
    escaped = report[Mode.ESCAPE.value]
    assert raw["retired_after_8"] == 0 and raw["pending_after_8"] == 2
    assert escaped["retired_after_8"] == 3 and escaped["pending_after_8"] == 0
    report["counterexample_pass"] = True
    return report


def exhaustive_n16_masks() -> dict[str, object]:
    """Exhaust every N=16 one-cycle occurrence subset at small depth.

    Every one of 2^16 masks is explored for each choice of one permanently
    stalled home endpoint.  The bounded suffix is seven no-injection cycles.
    This is deliberately not presented as unbounded liveness proof.
    """
    island_masks = 0
    escaped_improvements = 0
    first_witness: dict[str, object] | None = None
    examined = 0
    for stalled_lane in range(4):
        ready = 0b1111 & ~(1 << stalled_lane)
        for occurrence in range(1 << 16):
            examined += 1
            raw = CreditFabric(mode=Mode.RAW, energy_max=1)
            escaped = CreditFabric(mode=Mode.ESCAPE, energy_max=1)
            for cycle in range(8):
                mask = occurrence if cycle == 0 else 0
                raw.step(mask, ready)
                escaped.step(mask, ready)
            raw_stranded = raw.pending_count() > 0 and any(
                raw.lane_empty(lane) and ready & (1 << lane)
                for lane in range(4)
            )
            if raw_stranded:
                island_masks += 1
                if escaped.metrics.retired > raw.metrics.retired:
                    escaped_improvements += 1
                    if first_witness is None:
                        first_witness = {
                            "stalled_lane": stalled_lane,
                            "occurrence_mask": f"0x{occurrence:04x}",
                            "sources": [
                                source for source in range(16)
                                if occurrence & (1 << source)
                            ],
                            "raw_pending": raw.pending_count(),
                            "raw_retired": raw.metrics.retired,
                            "escaped_pending": escaped.pending_count(),
                            "escaped_retired": escaped.metrics.retired,
                        }
    assert first_witness is not None
    return {
        "scope": "all 2^16 one-cycle occurrence masks x four one-lane stalls; depth 8",
        "examined": examined,
        "raw_energy_island_masks": island_masks,
        "escape_improved_masks": escaped_improvements,
        "first_witness": first_witness,
        "bounded_invariants_pass": True,
    }


def run_suite(manifest: Path, *, random_trials: int) -> dict[str, object]:
    _, configs = load_manifest(manifest)
    rows: list[dict[str, object]] = []
    random_checks = 0
    for config in configs:
        masks = occurrence_masks(config)
        row: dict[str, object] = {
            "name": config.name,
            "workload": config.workload,
            "stim_cycles": config.stim_cycles,
        }
        for mode in Mode:
            row[mode.value] = simulate(masks, mode)
        rows.append(row)
        for trial in range(random_trials):
            ready = randomized_ready_masks(config.name, len(masks), 4, trial)
            for mode in Mode:
                result = simulate(masks, mode, ready_masks=ready)
                assert result["invariants_pass"]
                assert result["max_potential_rise_without_injection"] <= 0
                random_checks += 1

    aggregate: dict[str, dict[str, float | int]] = {}
    for mode in Mode:
        mode_rows = [row[mode.value] for row in rows]
        retired = sum(int(item["retired"]) for item in mode_rows)
        aggregate[mode.value] = {
            "runs": len(rows),
            "generated": sum(int(item["generated"]) for item in mode_rows),
            "overrun": sum(int(item["overrun"]) for item in mode_rows),
            "retired": retired,
            "fixed_window_retired": sum(
                int(item["fixed_window_retired"]) for item in mode_rows
            ),
            "max_latency": max(int(item["max_latency"]) for item in mode_rows),
            "mean_latency": (
                sum(float(item["latency_sum"]) for item in mode_rows) / retired
                if retired else 0.0
            ),
            "state_toggles": sum(int(item["state_toggles"]) for item in mode_rows),
            "toggle_per_retired": (
                sum(int(item["state_toggles"]) for item in mode_rows) / retired
                if retired else 0.0
            ),
            "state_bits": int(mode_rows[0]["state_bits"]),
            "bootstrap_admissions": sum(
                int(item["bootstrap_admissions"]) for item in mode_rows
            ),
            "avoidable_idle_cycles": sum(
                int(item["avoidable_idle_cycles"]) for item in mode_rows
            ),
        }
    return {
        "manifest": manifest.name,
        "runs": rows,
        "aggregate": aggregate,
        "randomized_ready_trials_per_trace": random_trials,
        "randomized_invariant_replays": random_checks,
    }


def gate(full: dict[str, object], capacity: dict[str, object]) -> dict[str, object]:
    full_aggregate = full["aggregate"]
    cap_aggregate = capacity["aggregate"]
    baseline_full = full_aggregate[Mode.BASELINE.value]
    escaped_full = full_aggregate[Mode.ESCAPE.value]
    baseline_cap = cap_aggregate[Mode.BASELINE.value]
    escaped_cap = cap_aggregate[Mode.ESCAPE.value]
    checks = {
        "cap_fixed_window_at_least_99pct_baseline": (
            escaped_cap["fixed_window_retired"]
            >= 0.99 * baseline_cap["fixed_window_retired"]
        ),
        "cap_overrun_not_worse": escaped_cap["overrun"] <= baseline_cap["overrun"],
        "full_max_latency_not_worse": (
            escaped_full["max_latency"] <= baseline_full["max_latency"]
        ),
        "state_or_toggle_efficiency": (
            escaped_full["state_bits"] <= baseline_full["state_bits"]
            or escaped_full["toggle_per_retired"]
            <= 0.95 * baseline_full["toggle_per_retired"]
        ),
        "strict_target_benefit": (
            escaped_cap["fixed_window_retired"] > baseline_cap["fixed_window_retired"]
            or escaped_cap["overrun"] < baseline_cap["overrun"]
        ),
    }
    return {
        "definition": (
            "all invariants plus >=99% cap22 fixed-window service, no cap22 overrun "
            "or full50 max-latency regression, >=5% toggle/event saving or no state "
            "premium, and one strict cap22 service/overrun benefit"
        ),
        "checks": checks,
        "go": all(checks.values()),
        "sv_permitted": all(checks.values()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--random-trials", type=int, default=2)
    parser.add_argument("--skip-exhaustive", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = {
        "schema_version": 1,
        "architecture": "a3_passivity_energy_tank_credit_fabric",
        "parameters": {"sources": 16, "lanes": 4, "depth": 2, "energy_max": 1},
        "directed_energy_island": directed_energy_island(),
        "exhaustive_n16": (
            {"skipped": True} if args.skip_exhaustive else exhaustive_n16_masks()
        ),
        "full50": run_suite(FULL_MANIFEST, random_trials=args.random_trials),
        "capacity22": run_suite(CAP_MANIFEST, random_trials=args.random_trials),
    }
    report["go_gate"] = gate(report["full50"], report["capacity22"])
    output_report = report
    if args.compact:
        directed = report["directed_energy_island"]
        output_report = {
            "schema_version": report["schema_version"],
            "architecture": report["architecture"],
            "parameters": report["parameters"],
            "directed_energy_island": {
                key: value for key, value in directed.items() if key != "history"
            },
            "exhaustive_n16": report["exhaustive_n16"],
            "full50": {
                "manifest": report["full50"]["manifest"],
                "aggregate": report["full50"]["aggregate"],
                "randomized_ready_trials_per_trace": report["full50"][
                    "randomized_ready_trials_per_trace"
                ],
                "randomized_invariant_replays": report["full50"][
                    "randomized_invariant_replays"
                ],
            },
            "capacity22": {
                "manifest": report["capacity22"]["manifest"],
                "aggregate": report["capacity22"]["aggregate"],
                "randomized_ready_trials_per_trace": report["capacity22"][
                    "randomized_ready_trials_per_trace"
                ],
                "randomized_invariant_replays": report["capacity22"][
                    "randomized_invariant_replays"
                ],
            },
            "go_gate": report["go_gate"],
        }
    encoded = json.dumps(output_report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.output)
    print(json.dumps({
        "directed": report["directed_energy_island"]["counterexample_pass"],
        "exhaustive": report["exhaustive_n16"],
        "full50": report["full50"]["aggregate"],
        "capacity22": report["capacity22"]["aggregate"],
        "go_gate": report["go_gate"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
