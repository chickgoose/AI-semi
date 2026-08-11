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
A4_GATE_COMMIT = "0d024152be37846a4fae73c65bcc2cfa73393844"
A3_COMMIT = "2696aef01b1df455e19a84cae800719941d2df66"
A9_COMMIT = "3450ddf09a590e7e66d9f35dff91efad831dfa87"
A5_AUDIT_SHA256 = "1be66e390590593bb63afc41dc7964f8e417ca1851cad37c37a4d27bb7c1674f"
A4_LOCAL_PATH = "rtl/candidates/a4_moving_block_w4/results/w4_local_summary.json"
A4_FOLLOWUP_PATH = "rtl/candidates/a4_moving_block_w4/results/w4_functional_followup.json"
A4_GATE_PATH = "rtl/candidates/a4_moving_block_w4/results/w4_max1_gate_freeze.json"
A3_COST_PATH = "reports/w4_a4_final_economics.json"
A9_REPORT_PATH = "experiments/a9_w4_moving_block_ddr_tournament/REPORT.md"
A9_SUMMARY_PATH = "experiments/a9_w4_moving_block_ddr_tournament/W4_A9_SUMMARY.md"
PINNED_SHA256 = {
    A4_LOCAL_PATH: "b3124911730c9d634a3708d3bda3ea96833f2468538d627bbc90a6babca4bf1a",
    A4_FOLLOWUP_PATH: "40d81275ebee63380508d12dad240836f0e5ef84ae6c7f83a7ef6b601f41fbd4",
    A4_GATE_PATH: "f123ab43e2e203b7a4eb9a0e8612b5d2f9dcd14890718697bca6b319f51b7618",
    A3_COST_PATH: "77ebf3cea5abe0edf13619c01c2081786166e9237da4391fe221744e1577f550",
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


def find_cost(cost: dict[str, Any], sources: int, variant: str) -> dict[str, Any]:
    matches = [row for row in cost["runs"]
               if row["num_sources"] == sources and row["variant"] == variant]
    if len(matches) != 1:
        raise GateError(f"missing unique cost row N={sources} variant={variant}")
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
            "net_count_cost_ratio": ratios["net_count"],
            "net_bit_cost_ratio": ratios["net_bit_count"],
            "max_data_fanout_ratio": ratios["max_fanout_data"],
            "fanout_ge16_net_ratio": ratios["data_nets_fanout_ge16"],
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
    predeclared_gate = pinned_json(a4_repo, A4_GATE_COMMIT, A4_GATE_PATH)
    cost = pinned_json(a3_repo, A3_COMMIT, A3_COST_PATH)
    a9_report = pinned_blob(a9_repo, A9_COMMIT, A9_REPORT_PATH).decode("utf-8")
    a9_summary = pinned_blob(a9_repo, A9_COMMIT, A9_SUMMARY_PATH).decode("utf-8")

    if (cost["status"] != "PASS"
            or cost["provenance"]["a4_commit"] != A4_COMMIT
            or not predeclared_gate["frozen_before_a3_same_flow_result_review"]):
        raise GateError("A3 selected/MAX1 receipt or predeclared A4 gate is not pinned")
    functional_pass = (
        local["status"]["exact_lockstep"] == "PASS"
        and followup["summary"]["all_conservation_order_drain"] == "PASS"
        and followup["summary"]["all_signal_lockstep"] == "PASS"
    )
    same_flow_cost = {}
    for sources in (16, 64):
        fixed = find_cost(cost, sources, "w3_max_advance1")
        moving = find_cost(cost, sources, "shared_clearance_local_enable")
        same_flow_cost[sources] = {
            field: moving[field] / fixed[field]
            for field in (
                "total_cells", "comb_cells", "comb_depth_cells",
                "net_count", "net_bit_count", "max_fanout_data",
                "data_nets_fanout_ge16", "wire_unique_bit_proxy",
                "wire_data_sink_pin_proxy",
            )
        }
        same_flow_cost[sources]["ff_bits"] = moving["ff_bits"] / fixed["ff_bits"]
        same_flow_cost[sources]["raw"] = {
            "max1": {field: fixed[field] for field in (
                "total_cells", "comb_cells", "comb_depth_cells", "net_count",
                "net_bit_count", "max_fanout_data", "data_nets_fanout_ge16",
                "wire_data_sink_pin_proxy", "ff_bits",
            )},
            "selected": {field: moving[field] for field in (
                "total_cells", "comb_cells", "comb_depth_cells", "net_count",
                "net_bit_count", "max_fanout_data", "data_nets_fanout_ge16",
                "wire_data_sink_pin_proxy", "ff_bits",
            )},
        }
        fixed_dffe = fixed["cell_types"].get("$_DFFE_PN0P_", 0)
        selected_dffe = moving["cell_types"].get("$_DFFE_PN0P_", 0)
        same_flow_cost[sources]["conservative_enable"] = {
            "effective_total_cell_ratio": (
                (moving["total_cells"] + selected_dffe)
                / (fixed["total_cells"] + fixed_dffe)
            ),
            "effective_comb_cell_ratio": (
                (moving["comb_cells"] + selected_dffe)
                / (fixed["comb_cells"] + fixed_dffe)
            ),
        }

    ceiling = predeclared_gate["same_flow_local_cost_ceiling_per_size"]
    conservative_ceiling = predeclared_gate["conservative_enable_cost_ceiling_per_size"]
    predeclared_checks: dict[str, Any] = {}
    for sources, ratios in same_flow_cost.items():
        raw = ratios["raw"]
        depth_levels = raw["selected"]["comb_depth_cells"] - raw["max1"]["comb_depth_cells"]
        checks = {
            "state_equal": ratios["ff_bits"] == 1,
            "total_cells": ratios["total_cells"] - 1 <= ceiling["mapped_total_cells_maximum_premium_fraction"],
            "comb_cells": ratios["comb_cells"] - 1 <= ceiling["mapped_comb_cells_maximum_premium_fraction"],
            "depth_levels": depth_levels <= ceiling["logic_depth_maximum_premium_levels"],
            "depth_fraction": ratios["comb_depth_cells"] - 1 <= ceiling["logic_depth_maximum_premium_fraction"],
            "max_data_fanout": ratios["max_fanout_data"] - 1 <= ceiling["max_fanout_maximum_premium_fraction"],
            "fanout_ge16_nets": ratios["data_nets_fanout_ge16"] - 1 <= ceiling["nets_fanout_ge16_maximum_premium_fraction"],
            "conservative_total_cells": (
                ratios["conservative_enable"]["effective_total_cell_ratio"] - 1
                <= conservative_ceiling["effective_total_cells_maximum_premium_fraction"]
            ),
            "conservative_comb_cells": (
                ratios["conservative_enable"]["effective_comb_cell_ratio"] - 1
                <= conservative_ceiling["effective_comb_cells_maximum_premium_fraction"]
            ),
        }
        predeclared_checks[f"n{sources}"] = {
            "depth_premium_levels": depth_levels,
            "checks": checks,
            "pass": all(checks.values()),
        }

    suites = {
        name: suite_gate(name, audit["suites"][name]["aggregate"], same_flow_cost)
        for name in ("full50", "capacity22")
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
        "a4_predeclared_same_flow_local_cost_gate": all(
            row["pass"] for row in predeclared_checks.values()
        ),
        "common_qualification_complete": local["status"]["common_qualification"] == "PASS",
        "physical_ppa_complete": local["status"]["physical_ppa_qualification"] == "PASS",
    }
    utility_pass = all(hard_gates[key] for key in (
        "exact_functional_equivalence",
        "capacity_direction_detected_both_suites",
        "matched_tail_non_regression_both_suites",
        "same_flow_throughput_per_cell_break_even_both_suites",
        "a4_predeclared_same_flow_local_cost_gate",
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
        "schema_version": 2,
        "decision": decision,
        "decision_scope": (
            "unweighted default replacement gate; an explicitly priced tail tradeoff may define a new gate"
        ),
        "hard_gates": hard_gates,
        "suites": suites,
        "same_flow_cost": {f"n{sources}": row for sources, row in same_flow_cost.items()},
        "a4_predeclared_gate": {
            "gate_id": predeclared_gate["gate_id"],
            "checks": predeclared_checks,
            "decision": "NO_GO" if not all(row["pass"] for row in predeclared_checks.values())
                        else "GO_TO_COMMON_AND_PHYSICAL",
        },
        "historical_cost_excluded": {
            "commit": "d1e979e1ce15a7e96e5aa6c32ef9b96c1d32d029",
            "classification": "EXTERNAL_HISTORICAL_DIAGNOSTIC_ONLY",
            "reason": "MAX1/MAX2 result is not the selected STYLE2/MAX1 six-way same-flow comparison",
        },
        "a9_citation_audit": a9_citation_audit(a9_report, a9_summary),
        "provenance": {
            "a5_audit_sha256": A5_AUDIT_SHA256,
            "a4_commit": A4_COMMIT,
            "a4_local_summary_sha256": PINNED_SHA256[A4_LOCAL_PATH],
            "a4_functional_followup_sha256": PINNED_SHA256[A4_FOLLOWUP_PATH],
            "a4_predeclared_gate_commit": A4_GATE_COMMIT,
            "a4_predeclared_gate_sha256": PINNED_SHA256[A4_GATE_PATH],
            "a3_commit": A3_COMMIT,
            "a3_selected_max1_six_way_receipt_sha256": PINNED_SHA256[A3_COST_PATH],
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
    parser.add_argument("--receipt", type=Path,
                        help="optional byte receipt for deterministic result and producer")
    args = parser.parse_args()
    document = evaluate(args.a5_audit, args.a4_repo, args.a3_repo, args.a9_repo)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    decision_exit = {
        "ADOPT": 0,
        "HOLD_PENDING_COMMON_AND_PHYSICAL": 3,
        "REJECT_AS_DEFAULT_REPLACEMENT": 4,
    }[document["decision"]]
    if args.receipt:
        producer = Path(__file__).resolve()
        receipt = {
            "schema_version": 1,
            "artifact": str(args.output),
            "artifact_sha256": sha256_bytes(args.output.read_bytes()),
            "producer": str(producer.relative_to(project)),
            "producer_sha256": sha256_bytes(producer.read_bytes()),
            "decision": document["decision"],
            "decision_exit": decision_exit,
            "determinism_contract": "same pinned input bytes produce byte-identical sorted JSON",
            "input_receipts": document["provenance"],
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    print(f"A5_W4_FINAL_GATE_{document['decision']} output={args.output}")
    return decision_exit


if __name__ == "__main__":
    raise SystemExit(main())
