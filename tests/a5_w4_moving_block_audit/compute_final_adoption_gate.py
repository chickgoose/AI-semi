#!/usr/bin/env python3
"""Combine pinned A5 statistics, A4 equivalence, and A3 same-flow cost."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from analyze_moving_block import git_blob, resolve_commit


A4_REPO_DEFAULT = Path("/home/chickgoose/projects/a4")
A3_REPO_DEFAULT = Path("/home/chickgoose/projects/a3")
A9_REPO_DEFAULT = Path("/home/chickgoose/projects/a9")
A4_COMMIT = "41f239dad4a342277f33d94bb3ed3db53e3497e0"
A3_COMMIT = "d1e979e1ce15a7e96e5aa6c32ef9b96c1d32d029"
A9_COMMIT = "3450ddf09a590e7e66d9f35dff91efad831dfa87"
A5_AUDIT_SHA256 = "1be66e390590593bb63afc41dc7964f8e417ca1851cad37c37a4d27bb7c1674f"
A4_LOCAL_PATH = "rtl/candidates/a4_moving_block_w4/results/w4_local_summary.json"
A4_FOLLOWUP_PATH = "rtl/candidates/a4_moving_block_w4/results/w4_functional_followup.json"
A3_COST_PATH = "reports/w4_a4_moving_block_synth.json"
A9_REPORT_PATH = "experiments/a9_w4_moving_block_ddr_tournament/REPORT.md"
A9_SUMMARY_PATH = "experiments/a9_w4_moving_block_ddr_tournament/W4_A9_SUMMARY.md"
PINNED_SHA256 = {
    A4_LOCAL_PATH: "b3124911730c9d634a3708d3bda3ea96833f2468538d627bbc90a6babca4bf1a",
    A4_FOLLOWUP_PATH: "40d81275ebee63380508d12dad240836f0e5ef84ae6c7f83a7ef6b601f41fbd4",
    A3_COST_PATH: "9b097a8b5d5152276fdf1342350c93f2aabf6763661e95ec314c2e777cf1b26f",
    A9_REPORT_PATH: "da35c135ff848e4440724f03ac404cda1cb93ed041f02903d3836f67eac9a766",
    A9_SUMMARY_PATH: "97f16ddc48529f08d114dae1711d63e2fb8b87a432715ee91809d3d5dcf95ce0",
}


class GateError(RuntimeError):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def pinned_blob(repository: Path, commit: str, path: str) -> bytes:
    resolve_commit(repository, commit)
    content = git_blob(repository, commit, path)
    if sha256_bytes(content) != PINNED_SHA256[path]:
        raise GateError(f"pinned input SHA mismatch: {commit}:{path}")
    return content


def pinned_json(repository: Path, commit: str, path: str) -> dict[str, Any]:
    return json.loads(pinned_blob(repository, commit, path))


def find_cost(cost: dict[str, Any], sources: int, advance: int) -> dict[str, Any]:
    matches = [row for row in cost["runs"]
               if row["num_sources"] == sources and row["max_advance"] == advance]
    if len(matches) != 1:
        raise GateError(f"missing unique cost row N={sources} advance={advance}")
    return matches[0]


def find_mapping(local: dict[str, Any], sources: int, design: str) -> dict[str, Any]:
    matches = [row for row in local["mapping"]
               if row["sources"] == sources and row["design"] == design]
    if len(matches) != 1:
        raise GateError(f"missing unique local mapping N={sources} design={design}")
    return matches[0]


def suite_gate(name: str, aggregate: dict[str, Any], same_flow_cost: dict[str, Any]) -> dict[str, Any]:
    fixed_throughput = aggregate["fixed_all_accepted_latency"]["count"]
    moving_throughput = aggregate["moving_all_accepted_latency"]["count"]
    # A4's frozen aggregate throughput uses accepted/drained events divided by
    # exact total run cycles. Counts alone are not a throughput substitute, so
    # the exact published ratios are frozen here and checked against suite name.
    published = {
        "full50": (0.729214327, 0.729999388),
        "capacity22": (0.789979031, 0.790855566),
    }
    fixed_epc, moving_epc = published[name]
    if (fixed_throughput, moving_throughput) != (
        aggregate["fixed_accepted"], aggregate["moving_accepted"]
    ):
        raise GateError(f"{name}: accepted latency population mismatch")
    throughput_ratio = moving_epc / fixed_epc
    churn = aggregate["fixed_only"] + aggregate["moving_only"]
    net = aggregate["accepted_delta"]
    matched_tail = {
        metric: {
            "fixed": aggregate["fixed_matched_latency"][metric],
            "moving": aggregate["moving_matched_latency"][metric],
            "delta": (aggregate["moving_matched_latency"][metric]
                      - aggregate["fixed_matched_latency"][metric]),
        }
        for metric in ("p95", "p99", "max")
    }
    cost_efficiency = {}
    for sources, ratios in same_flow_cost.items():
        cost_efficiency[f"n{sources}"] = {
            "throughput_ratio": throughput_ratio,
            "total_cell_cost_ratio": ratios["total_cells"],
            "comb_cell_cost_ratio": ratios["comb_cells"],
            "depth_cost_ratio": ratios["comb_depth_cells"],
            "wire_sink_cost_ratio": ratios["wire_data_sink_pin_proxy"],
            "throughput_per_total_cell_ratio": throughput_ratio / ratios["total_cells"],
            "throughput_per_comb_cell_ratio": throughput_ratio / ratios["comb_cells"],
            "throughput_per_wire_sink_ratio": (
                throughput_ratio / ratios["wire_data_sink_pin_proxy"]
            ),
            "break_even_throughput_gain_percent": (ratios["total_cells"] - 1) * 100,
            "observed_throughput_gain_percent": (throughput_ratio - 1) * 100,
        }
    bootstrap = aggregate["bootstrap"]
    return {
        "generated": aggregate["generated"],
        "accepted_delta": net,
        "discordant_accepted_ids": churn,
        "net_as_fraction_of_churn": net / churn,
        "accepted_set_jaccard": aggregate["accepted_jaccard"],
        "capacity_loss_reduction_fraction": aggregate["capacity_loss_reduction_fraction"],
        "accepted_delta_95ci": bootstrap["accepted_delta_total_95ci"],
        "capacity_direction_statistically_detected": bootstrap["accepted_delta_total_95ci"][0] > 0,
        "matched_tail": matched_tail,
        "matched_tail_non_regression": all(row["delta"] <= 0 for row in matched_tail.values()),
        "same_flow_efficiency": cost_efficiency,
        "same_flow_throughput_per_cell_break_even": all(
            row["throughput_per_total_cell_ratio"] >= 1
            for row in cost_efficiency.values()
        ),
    }


def require_line(text: str, line_number: int, fragment: str, label: str) -> None:
    lines = text.splitlines()
    if line_number > len(lines) or fragment not in lines[line_number - 1]:
        raise GateError(f"A9 pinned line changed: {label}:{line_number}")


def a9_citation_audit(report: str, summary: str) -> dict[str, Any]:
    require_line(report, 86, "Moving admits 41 additional events", "REPORT")
    require_line(report, 88, "survivor sets differ", "REPORT")
    require_line(report, 103, "Moving admits 35 additional events", "REPORT")
    require_line(summary, 7, "moving core changes full50 accepted", "W4_A9_SUMMARY")
    require_line(summary, 8, "throughput rises only", "W4_A9_SUMMARY")
    return {
        "verdict": "PARTIAL_CAVEAT_INADEQUATE_FOR_MATCHED_COHORT_CLAIM",
        "report_evidence": [
            {"line": 86, "finding": "calls net +41 'additional', implying a superset"},
            {"line": 88, "finding": "does acknowledge different survivor sets, without churn/matched metrics"},
            {"line": 103, "finding": "calls net +35 'additional' and gives no local cohort caveat"},
        ],
        "summary_evidence": [
            {"lines": "7-9", "finding": "quotes both count deltas and raw tails but omits survivor churn and matched p99"},
        ],
        "correct_restatement": {
            "full50": "net +41 after 11,023 discordant IDs; matched p99 46->46",
            "capacity22": "net +35 after 10,841 discordant IDs; matched p99 46->47",
        },
        "remaining_valid_scope": (
            "raw accepted/delivered/overrun/throughput and all-accepted latency are "
            "descriptive; they are not matched-cohort or accepted-superset evidence"
        ),
    }


def evaluate(a5_audit_path: Path, a4_repo: Path, a3_repo: Path,
             a9_repo: Path) -> dict[str, Any]:
    audit_bytes = a5_audit_path.read_bytes()
    if sha256_bytes(audit_bytes) != A5_AUDIT_SHA256:
        raise GateError("A5 matched-cohort audit SHA mismatch")
    audit = json.loads(audit_bytes)
    local = pinned_json(a4_repo, A4_COMMIT, A4_LOCAL_PATH)
    followup = pinned_json(a4_repo, A4_COMMIT, A4_FOLLOWUP_PATH)
    cost = pinned_json(a3_repo, A3_COMMIT, A3_COST_PATH)
    a9_report = pinned_blob(a9_repo, A9_COMMIT, A9_REPORT_PATH).decode("utf-8")
    a9_summary = pinned_blob(a9_repo, A9_COMMIT, A9_SUMMARY_PATH).decode("utf-8")

    if cost["status"] != "PASS" or cost["provenance"]["commit"] != local["baseline_commit"]:
        raise GateError("A3 same-flow cost is not bound to the A4 frozen baseline")
    functional_pass = (
        local["status"]["exact_lockstep"] == "PASS"
        and followup["summary"]["all_conservation_order_drain"] == "PASS"
        and followup["summary"]["all_signal_lockstep"] == "PASS"
    )
    same_flow_cost = {}
    for sources in (16, 64):
        fixed = find_cost(cost, sources, 1)
        moving = find_cost(cost, sources, 2)
        same_flow_cost[sources] = {
            field: moving[field] / fixed[field]
            for field in (
                "total_cells", "comb_cells", "comb_depth_cells",
                "wire_unique_bit_proxy", "wire_data_sink_pin_proxy",
            )
        }
        same_flow_cost[sources]["ff_bits"] = moving["ff_bits"] / fixed["ff_bits"]

    suites = {
        name: suite_gate(name, audit["suites"][name]["aggregate"], same_flow_cost)
        for name in ("full50", "capacity22")
    }
    optimized_diagnostic = {}
    for sources in (16, 64):
        fixed = find_cost(cost, sources, 1)
        optimized = find_mapping(local, sources, "shared_clearance_local_enable")
        optimized_diagnostic[f"n{sources}"] = {
            "cross_flow_total_cell_ratio_vs_a3_fixed": optimized["cells"] / fixed["total_cells"],
            "classification": "DIAGNOSTIC_ONLY_NOT_SAME_FLOW",
            "reason": "A4 optimized mapping and A3 fixed reference use different normalized sources/recipes",
        }

    hard_gates = {
        "exact_functional_equivalence": functional_pass,
        "capacity_direction_detected_both_suites": all(
            row["capacity_direction_statistically_detected"] for row in suites.values()
        ),
        "matched_tail_non_regression_both_suites": all(
            row["matched_tail_non_regression"] for row in suites.values()
        ),
        "same_flow_throughput_per_cell_break_even_both_suites": all(
            row["same_flow_throughput_per_cell_break_even"] for row in suites.values()
        ),
        "common_qualification_complete": local["status"]["common_qualification"] == "PASS",
        "physical_ppa_complete": local["status"]["physical_ppa_qualification"] == "PASS",
    }
    utility_pass = all(hard_gates[key] for key in (
        "exact_functional_equivalence",
        "capacity_direction_detected_both_suites",
        "matched_tail_non_regression_both_suites",
        "same_flow_throughput_per_cell_break_even_both_suites",
    ))
    qualification_complete = (
        hard_gates["common_qualification_complete"]
        and hard_gates["physical_ppa_complete"]
    )
    decision = (
        "REJECT_AS_DEFAULT_REPLACEMENT" if not utility_pass
        else "HOLD_PENDING_COMMON_AND_PHYSICAL" if not qualification_complete
        else "ADOPT"
    )
    return {
        "schema_version": 1,
        "decision": decision,
        "decision_scope": (
            "unweighted default replacement gate; an explicitly priced tail tradeoff may define a new gate"
        ),
        "hard_gates": hard_gates,
        "suites": suites,
        "same_flow_cost": {f"n{sources}": row for sources, row in same_flow_cost.items()},
        "a4_optimized_cost_diagnostic": optimized_diagnostic,
        "a9_citation_audit": a9_citation_audit(a9_report, a9_summary),
        "provenance": {
            "a5_audit_sha256": A5_AUDIT_SHA256,
            "a4_commit": A4_COMMIT,
            "a4_local_summary_sha256": PINNED_SHA256[A4_LOCAL_PATH],
            "a4_functional_followup_sha256": PINNED_SHA256[A4_FOLLOWUP_PATH],
            "a3_commit": A3_COMMIT,
            "a3_same_flow_cost_sha256": PINNED_SHA256[A3_COST_PATH],
            "a9_commit": A9_COMMIT,
            "a9_report_sha256": PINNED_SHA256[A9_REPORT_PATH],
            "foreign_current_head_consulted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    project = Path(__file__).resolve().parents[2]
    parser.add_argument("--a5-audit", type=Path,
                        default=project / "docs/research/results/a5_w4_moving_block_audit.json")
    parser.add_argument("--a4-repo", type=Path, default=A4_REPO_DEFAULT)
    parser.add_argument("--a3-repo", type=Path, default=A3_REPO_DEFAULT)
    parser.add_argument("--a9-repo", type=Path, default=A9_REPO_DEFAULT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = evaluate(args.a5_audit, args.a4_repo, args.a3_repo, args.a9_repo)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(f"A5_W4_FINAL_GATE_{document['decision']} output={args.output}")
    return {
        "ADOPT": 0,
        "HOLD_PENDING_COMMON_AND_PHYSICAL": 3,
        "REJECT_AS_DEFAULT_REPLACEMENT": 4,
    }[document["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
