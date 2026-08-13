#!/usr/bin/env python3
"""Generate the fail-closed A2/A3 K2+P6 digital selection receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPLAY_PATH = Path("tests/a23_full_p6_replay/result.json")
COST_PATH = Path("audits/a7_k2_cost_closure/result.json")
EXPECTED_SHA256 = {
    REPLAY_PATH: "67c6dd0a2decda78edede6d285c81ae580faa7c5a4b949c74c5b19291a8858b2",
    COST_PATH: "993e34e229bf9d1b4810af4b540e4015dbebae60505c8f682641373fbb4fee0f",
}
CANDIDATES = ("a2", "a3")
SEMANTIC_GRADES = {
    "a2": "WEIGHTED_AGGREGATE_NOT_A5_SCALAR_PREFIX",
    "a3": "EXACT_SCALAR_PREFIX_K2",
}
SOURCE_PACKAGE_COMMIT = "a05b943c12fde313357f726b638d84dc747e23ca"
INTEGRATED_PACKAGE_COMMIT = "30377a34f290884ac687608646f335aa520e0610"
SOURCE_PUBLICATION_COMMIT = "867e55c781cfeb250b3456f3fae54e4a8b8371fe"
INTEGRATED_PUBLICATION_COMMIT = "33bbb7453cf19f6bc9b8401a40007d93805257d0"
REPLAY_SUBTREE = "tests/a23_full_p6_replay"


class SelectionError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def committed_json(root: Path, relative: Path) -> dict[str, Any]:
    path = root / relative
    expected = EXPECTED_SHA256[relative]
    if not path.is_file() or sha256(path) != expected:
        raise SelectionError(f"input SHA mismatch: {relative}")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(relative)], cwd=root,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if tracked.returncode:
        raise SelectionError(f"input is not tracked: {relative}")
    head = subprocess.run(
        ["git", "show", f"HEAD:{relative}"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if head.returncode or hashlib.sha256(head.stdout).hexdigest() != expected:
        raise SelectionError(f"working input differs from HEAD: {relative}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SelectionError(f"invalid JSON: {relative}: {error}") from error
    if not isinstance(document, dict):
        raise SelectionError(f"input root is not an object: {relative}")
    return document


def finite_positive(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SelectionError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise SelectionError(f"{label} is not finite and positive")
    return result


def verify_rebased_replay_provenance(
        root: Path, document: dict[str, Any]) -> dict[str, Any]:
    provenance = document.get("provenance", {})
    if provenance.get("package_commit") != SOURCE_PACKAGE_COMMIT:
        raise SelectionError("source replay package commit mismatch")
    for commit in (
            SOURCE_PACKAGE_COMMIT, INTEGRATED_PACKAGE_COMMIT,
            SOURCE_PUBLICATION_COMMIT, INTEGRATED_PUBLICATION_COMMIT):
        check = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if check.returncode:
            raise SelectionError(f"missing replay provenance commit: {commit}")
    for commit in (INTEGRATED_PACKAGE_COMMIT, INTEGRATED_PUBLICATION_COMMIT):
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if ancestor.returncode:
            raise SelectionError(f"integrated replay commit is not in HEAD: {commit}")
    for source, integrated in (
            (SOURCE_PACKAGE_COMMIT, INTEGRATED_PACKAGE_COMMIT),
            (SOURCE_PUBLICATION_COMMIT, INTEGRATED_PUBLICATION_COMMIT)):
        comparison = subprocess.run(
            ["git", "diff", "--quiet", source, integrated, "--", REPLAY_SUBTREE],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if comparison.returncode:
            raise SelectionError(
                f"rebased replay subtree differs: {source} vs {integrated}")
    return {
        "source_package_commit": SOURCE_PACKAGE_COMMIT,
        "integrated_package_commit": INTEGRATED_PACKAGE_COMMIT,
        "source_publication_commit": SOURCE_PUBLICATION_COMMIT,
        "integrated_publication_commit": INTEGRATED_PUBLICATION_COMMIT,
        "subtree": REPLAY_SUBTREE,
        "source_and_integrated_subtrees_byte_identical": True,
    }


def validate_replay(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if (document.get("schema") != "a23_full_p6_replay_result_v1" or
            document.get("status") != "PASS" or
            document.get("boundary") !=
            "actual_scheduler_plus_actual_phase_related_always_ready_P6" or
            document.get("ordered_link_adapter") is not False or
            document.get("observation_wrapper_state_bits") != 0):
        raise SelectionError("actual-P6 replay boundary/status mismatch")
    qualification = document.get("qualification", {})
    if (qualification.get("digital_RTL") != "GO" or
            qualification.get("physical") != "HOLD" or
            qualification.get("CDC_RDC") != "HOLD"):
        raise SelectionError("actual-P6 qualification boundary mismatch")
    accounting = document.get("execution_accounting", {})
    if (accounting.get("full50_actual_executions") != 150 or
            accounting.get("capacity22_additional_executions") != 0 or
            accounting.get("mutation_actual_RTL_executions") != 15):
        raise SelectionError("actual-P6 execution accounting mismatch")
    generator = document.get("generator", {})
    if generator.get("capacity22_is_full50_subset_view") is not True:
        raise SelectionError("capacity22 is not an exact subset view")

    mutations = document.get("mutations", [])
    selected: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        rows = [row for row in mutations if row.get("owner") == candidate]
        if (len(rows) != 5 or {row.get("mutation") for row in rows} !=
                {"drop", "duplicate", "swap", "microstep", "reset"} or
                not all(row.get("actual_rtl") is True and row.get("killed") is True
                        and isinstance(row.get("first_required_diagnostic"), str)
                        for row in rows)):
            raise SelectionError(f"{candidate} actual-RTL mutation gate mismatch")
        owner = document.get("owners", {}).get(candidate, {})
        suites: dict[str, Any] = {}
        for suite, expected_runs in (("full50", 50), ("capacity22", 22)):
            aggregate = owner.get(suite, {}).get("aggregate", {})
            totals = aggregate.get("totals", {})
            if aggregate.get("run_count") != expected_runs:
                raise SelectionError(f"{candidate}/{suite} run count mismatch")
            generated = totals.get("generated")
            overrun = totals.get("source_overrun")
            accepted = totals.get("accepted")
            retired = totals.get("retired")
            if not all(isinstance(value, int) for value in
                       (generated, overrun, accepted, retired)):
                raise SelectionError(f"{candidate}/{suite} totals are not integers")
            if generated != overrun + accepted or accepted != retired:
                raise SelectionError(f"{candidate}/{suite} conservation mismatch")
            epc = finite_positive(
                aggregate.get("fixed_window_events_per_cycle"),
                f"{candidate}/{suite} EPC",
            )
            suites[suite] = {
                "generated": generated, "source_overrun": overrun,
                "accepted": accepted, "retired": retired,
                "fixed_window_retired": totals.get("fixed_window_retired"),
                "fixed_window_cycles": totals.get("fixed_window_cycles"),
                "fixed_window_events_per_cycle": epc,
                "occurrence_to_accept": aggregate.get("occurrence_to_accept"),
                "accept_to_retire": aggregate.get("accept_to_retire"),
            }
        selected[candidate] = {
            "suites": suites,
            "actual_rtl_mutations_killed": 5,
            "reset_scope": "drain_then_reset_quiet_and_recovery_not_midflight_abort",
        }
    return selected


def validate_cost(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if (document.get("schema") != "a7_k2_cost_closure_report_v1" or
            document.get("status") != "STRUCTURAL_PROXY_COMPLETE_PHYSICAL_HOLD" or
            document.get("comparability", {}).get("same_recipe_and_tool") is not True or
            document.get("pareto", {}).get("full_p6") != ["a2", "a3"]):
        raise SelectionError("same-flow structural comparison mismatch")
    physical = document.get("physical_metrics", {})
    if (any(physical.get(key) is not None for key in
            ("area", "power", "energy_per_event", "fmax")) or
            "HOLD" not in str(physical.get("status"))):
        raise SelectionError("physical metrics must remain unavailable/HOLD")
    selected: dict[str, dict[str, Any]] = {}
    required = (
        "mapped_cells", "mapped_state_bits", "logic_depth_levels",
        "fanout_proxy_max", "fanout_proxy_p95", "nets_fanout_ge16",
        "sink_pin_wire_proxy",
    )
    for candidate in CANDIDATES:
        row = document.get("candidates", {}).get(candidate, {})
        if row.get("semantic_grade") != SEMANTIC_GRADES[candidate]:
            raise SelectionError(f"{candidate} semantic grade mismatch")
        metrics = row.get("full_composition_metrics", {})
        positive = tuple(key for key in required if key != "nets_fanout_ge16")
        if (any(not isinstance(metrics.get(key), int) or metrics[key] <= 0
                for key in positive) or
                not isinstance(metrics.get("nets_fanout_ge16"), int) or
                metrics["nets_fanout_ge16"] < 0):
            raise SelectionError(f"{candidate} structural metric mismatch")
        selected[candidate] = {
            "semantic_grade": row["semantic_grade"],
            "full_p6_metrics": {key: metrics[key] for key in required},
        }
    return selected


def pct_delta(winner: float, alternative: float) -> float:
    return round((winner / alternative - 1.0) * 100.0, 6)


def generate(root: Path) -> dict[str, Any]:
    replay_path = root / REPLAY_PATH
    cost_path = root / COST_PATH
    replay = committed_json(root, REPLAY_PATH)
    cost = committed_json(root, COST_PATH)
    replay_provenance = verify_rebased_replay_provenance(root, replay)
    functional = validate_replay(replay)
    structural = validate_cost(cost)

    for candidate in CANDIDATES:
        functional[candidate].update(structural[candidate])
        epc = functional[candidate]["suites"]["full50"][
            "fixed_window_events_per_cycle"]
        metrics = functional[candidate]["full_p6_metrics"]
        functional[candidate]["digital_efficiency_proxies"] = {
            "full50_epc_per_mapped_cell": round(epc / metrics["mapped_cells"], 12),
            "full50_epc_per_state_bit": round(epc / metrics["mapped_state_bits"], 12),
            "full50_epc_per_wire_proxy": round(epc / metrics["sink_pin_wire_proxy"], 12),
        }

    a2 = functional["a2"]
    a3 = functional["a3"]
    a2_full = a2["suites"]["full50"]
    a3_full = a3["suites"]["full50"]
    a2_cap = a2["suites"]["capacity22"]
    a3_cap = a3["suites"]["capacity22"]
    a2_cost = a2["full_p6_metrics"]
    a3_cost = a3["full_p6_metrics"]
    deltas = {
        "full50_epc_percent": pct_delta(
            a2_full["fixed_window_events_per_cycle"],
            a3_full["fixed_window_events_per_cycle"]),
        "full50_accepted_percent": pct_delta(a2_full["accepted"], a3_full["accepted"]),
        "capacity22_epc_percent": pct_delta(
            a2_cap["fixed_window_events_per_cycle"],
            a3_cap["fixed_window_events_per_cycle"]),
        "mapped_cells_percent": pct_delta(a2_cost["mapped_cells"], a3_cost["mapped_cells"]),
        "mapped_state_percent": pct_delta(
            a2_cost["mapped_state_bits"], a3_cost["mapped_state_bits"]),
        "logic_depth_percent": pct_delta(
            a2_cost["logic_depth_levels"], a3_cost["logic_depth_levels"]),
        "wire_proxy_percent": pct_delta(
            a2_cost["sink_pin_wire_proxy"], a3_cost["sink_pin_wire_proxy"]),
        "fanout_max_percent": pct_delta(
            a2_cost["fanout_proxy_max"], a3_cost["fanout_proxy_max"]),
    }
    if (deltas["full50_epc_percent"] < 5.0 or
            deltas["full50_accepted_percent"] < 5.0 or
            deltas["capacity22_epc_percent"] < 5.0 or
            deltas["mapped_cells_percent"] > 10.0 or
            deltas["mapped_state_percent"] > 15.0 or
            deltas["wire_proxy_percent"] > 15.0):
        raise SelectionError("A2 performance benefit does not clear the digital cost guard")

    winner = max(
        CANDIDATES,
        key=lambda key: functional[key]["suites"]["full50"][
            "fixed_window_events_per_cycle"],
    )
    if winner != "a2":
        raise SelectionError(f"selection policy no longer chooses A2: {winner}")

    return {
        "schema": "k2_final_digital_selection_v1",
        "status": "DIGITAL_SELECTION_COMPLETE_PHYSICAL_HOLD",
        "objective_scope": "Fovea_weight_preserving_A2_A3_K2_plus_P6",
        "inputs": {
            "actual_p6_replay": {
                "path": str(REPLAY_PATH), "sha256": sha256(replay_path),
                "rebased_provenance": replay_provenance},
            "same_flow_generic_cost": {
                "path": str(COST_PATH), "sha256": sha256(cost_path)},
        },
        "hard_gates": {
            "both_candidates_actual_RTL_functional": True,
            "both_candidates_conservation_and_order": True,
            "both_candidates_actual_RTL_mutations_killed": True,
            "full50_actual_executions_per_candidate": 50,
            "capacity22_exact_full50_subset_runs": 22,
            "same_full_p6_generic_boundary_and_tool": True,
            "both_candidates_on_structural_pareto": True,
            "physical_PPA_CDC_RDC": "HOLD_NOT_USED_FOR_DIGITAL_SELECTION",
        },
        "selection_policy": {
            "eligibility": "weighted-policy preservation plus all digital hard gates",
            "primary": "maximize actual-P6 full50 fixed-window events_per_cycle",
            "minimum_performance_margin_percent": 5.0,
            "cost_guard": {
                "mapped_cells_penalty_max_percent": 10.0,
                "mapped_state_penalty_max_percent": 15.0,
                "wire_proxy_penalty_max_percent": 15.0,
                "must_remain_on_full_p6_pareto": True,
            },
            "tie_break": "capacity22 EPC then full50 accepted then lower mapped cells",
        },
        "candidates": functional,
        "a2_relative_to_a3": deltas,
        "selected_candidate": "a2_batched_iwrr_k2_plus_p6",
        "selected_key": "a2",
        "selection_reason": (
            "A2 clears every digital hard gate, remains on the same-flow structural "
            "Pareto set, and provides more than five percent full50/capacity22 "
            "performance benefit while its mapped-cell, state, and wire-proxy "
            "penalties remain inside the declared digital cost guard."
        ),
        "retained_fallback": {
            "candidate": "a3_exact_scalar_prefix_k2_plus_p6",
            "reason": (
                "Choose A3 instead only if exact scalar-prefix semantics becomes a "
                "hard organizer requirement or later physical evidence reverses the "
                "digital tradeoff."
            ),
        },
        "claim_boundary": {
            "digital_functional_and_generic_structural": "GO",
            "standard_cell_area_fmax_power_energy_routing": "HOLD",
            "arbitrary_clock_CDC_RDC": "HOLD",
            "midflight_reset_abort_flush": "HOLD",
        },
    }


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.repo_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    print("K2_FINAL_SELECTION_PASS selected=a2 fallback=a3 physical=HOLD")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SelectionError, OSError, subprocess.SubprocessError) as error:
        print(f"K2_FINAL_SELECTION_FAIL {error}")
        raise SystemExit(2) from error
