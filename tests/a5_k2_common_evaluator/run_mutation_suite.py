#!/usr/bin/env python3
"""Prove that the K2 evaluator kills the predeclared false-pass mutations."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
from typing import Any, Callable

from evaluate_k2 import ContractError, aggregate, digest_bytes, evaluate_run, validate_evidence
from generate_vectors import build_bundle
from k2_oracle import PolicyState, fold_prefix
from synthetic_reference import build_reference_evidence, materialize_owner_fixture
import json


ROOT = Path(__file__).resolve().parent
OWNER_FIXTURES = ROOT / "fixtures" / "owners"


def run_by_name(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(run for run in document["runs"] if run["name"] == name)


def observation(run: dict[str, Any], cycle: int) -> dict[str, Any]:
    return run["cycles"][cycle]


def first_two_accept_cycle(run: dict[str, Any]) -> int:
    return next(item["cycle"] for item in run["cycles"] if len(item["accepts"]) == 2)


def codes(bundle: dict[str, Any], evidence: dict[str, Any], name: str,
          thresholds: dict[str, Any]) -> set[str]:
    vector = next(run for run in bundle["runs"] if run["name"] == name)
    result = evaluate_run(vector, run_by_name(evidence, name), thresholds)
    return {item["code"] for item in result["hard_failures"]}


def future_witness() -> None:
    state = PolicyState()
    first, state_after = fold_prefix({0, 8}, state, 2)
    scalar_first, scalar_state = fold_prefix({0, 8}, state, 1)
    scalar_second, _ = fold_prefix({4, 8}, scalar_state, 1)
    k2_next, _ = fold_prefix({4, 8}, state_after, 1)
    k2_stream = first + k2_next
    scalar_stream = scalar_first + scalar_second + [8]
    if k2_stream == scalar_stream or k2_stream[:2] != [0, 8] or scalar_stream[:3] != [0, 4, 8]:
        raise AssertionError(f"future-arrival witness missing: K2={k2_stream} scalar={scalar_stream}")


def main() -> int:
    bundle = build_bundle()
    thresholds = json.loads((ROOT / "thresholds.json").read_text(encoding="utf-8"))
    good = build_reference_evidence(bundle)
    good_results = [evaluate_run(vector, run_by_name(good, vector["name"]), thresholds)
                    for vector in bundle["runs"]]
    reference = aggregate(good["candidate"], good_results)
    if reference["status"] != "PASS":
        raise AssertionError(f"test-only reference failed: {reference['hard_failures']}")

    mutations: list[tuple[str, str, str, Callable[[dict[str, Any]], None]]] = []

    def false_weight(doc: dict[str, Any]) -> None:
        run = run_by_name(doc, "persistent_weight_120")
        changed = 0
        for row in run["cycles"]:
            for accept in row["accepts"]:
                if accept["source"] // 4 == 1 and changed < 5:
                    accept["source"] = 12 + (changed % 4)
                    changed += 1
    mutations.append(("false_weight", "persistent_weight_120", "false_1_5_5_1", false_weight))

    def stale_second(doc: dict[str, Any]) -> None:
        run = run_by_name(doc, "stale_second_revalidation")
        cycle = 5
        row = observation(run, cycle)
        row["accepts"][1] = {"slot": 1, "source": 8,
                             "event_id": "stale_second_revalidation:c2:s8"}
    mutations.append(("stale_second", "stale_second_revalidation", "stale_or_wrong_event", stale_second))

    def duplicate(doc: dict[str, Any]) -> None:
        run = run_by_name(doc, "same_row_distinct_pair")
        cycle = first_two_accept_cycle(run)
        row = observation(run, cycle)
        row["accepts"][1] = dict(row["accepts"][0])
        row["accepts"][1]["slot"] = 1
    mutations.append(("same_source_duplicate", "same_row_distinct_pair", "same_source_duplicate", duplicate))

    def corrupt_stall(doc: dict[str, Any]) -> None:
        run = run_by_name(doc, "ordered_lane_stall")
        for cycle in range(1, len(run["cycles"])):
            previous = run["cycles"][cycle - 1]
            if previous["outputs"][0]["valid"]:
                current = run["cycles"][cycle]
                if current["outputs"][0]["valid"]:
                    current["outputs"][0]["event_id"] += ":corrupt"
                    return
        raise AssertionError("stall mutation site absent")
    mutations.append(("lane_stall_corruption", "ordered_lane_stall", "lane_stall_corruption", corrupt_stall))

    def lane_bypass(doc: dict[str, Any]) -> None:
        run = run_by_name(doc, "ordered_lane_stall")
        for row in run["cycles"]:
            if row["cycle"] in (3, 4, 5) and row["outputs"][0]["valid"]:
                row["outputs"][1] = {
                    "lane": 1, "valid": True, "source": 1,
                    "event_id": "ordered_lane_stall:c2:s1",
                }
                return
        raise AssertionError("lane bypass hazard absent in reference")
    mutations.append(("younger_lane_bypass", "ordered_lane_stall", "younger_lane_bypass", lane_bypass))

    def reset_phantom(doc: dict[str, Any]) -> None:
        run = run_by_name(doc, "reset_abort_no_phantom")
        row = observation(run, 6)
        row["outputs"][0] = {"lane": 0, "valid": True, "source": 0,
                             "event_id": "reset_abort_no_phantom:c2:s0"}
    mutations.append(("reset_phantom", "reset_abort_no_phantom", "reset_phantom", reset_phantom))

    killed = 0
    for mutation_name, run_name, expected_code, mutate in mutations:
        candidate = deepcopy(good)
        mutate(candidate)
        observed_codes = codes(bundle, candidate, run_name, thresholds)
        if expected_code not in observed_codes:
            raise AssertionError(
                f"mutation {mutation_name} survived: expected {expected_code}, got {sorted(observed_codes)}")
        killed += 1

    overclaim = deepcopy(good["candidate"])
    overclaim["claims"]["full_future_trace_equivalence"] = True
    result = aggregate(overclaim, good_results)
    if "future_trace_overclaim" not in {item["code"] for item in result["hard_failures"]}:
        raise AssertionError("future-trace overclaim mutation survived")
    killed += 1
    future_witness()

    # Provenance mutations operate on actual regular fixture files and separate
    # runner artifacts; these are not dict-only semantic mutations.
    with tempfile.TemporaryDirectory() as temporary:
        fixture_root = Path(temporary)

        unattached_path, unattached = materialize_owner_fixture(
            bundle, fixture_root / "unattached", OWNER_FIXTURES / "owner-alpha",
            "malicious-unattached")
        del unattached["candidate"]["source"]
        unattached["candidate"]["source_sha256"] = "0" * 64
        unattached_path.write_text(json.dumps(unattached), encoding="utf-8")
        try:
            validate_evidence(unattached, bundle, unattached_path)
        except ContractError as error:
            if "incomplete candidate identity" not in str(error):
                raise AssertionError(f"unattached_hash wrong diagnostic: {error}") from error
        else:
            raise AssertionError("unattached_hash mutation survived")
        killed += 1

        fabricated_path, fabricated = materialize_owner_fixture(
            bundle, fixture_root / "fabricated", OWNER_FIXTURES / "owner-beta",
            "malicious-fabricated")
        fake_artifact = fixture_root / "fabricated-output.json"
        fake_artifact.write_text(json.dumps({"cycles": []}), encoding="utf-8")
        fabricated["runs"][0]["artifact"] = {
            "path": str(fake_artifact), "digest_kind": "sha256",
            "digest": digest_bytes(fake_artifact.read_bytes(), "sha256"),
        }
        fabricated_path.write_text(json.dumps(fabricated), encoding="utf-8")
        try:
            validate_evidence(fabricated, bundle, fabricated_path)
        except ContractError as error:
            if "malformed envelope" not in str(error):
                raise AssertionError(f"fabricated_output wrong diagnostic: {error}") from error
        else:
            raise AssertionError("fabricated_output mutation survived")
        killed += 1

        rebound_path, rebound = materialize_owner_fixture(
            bundle, fixture_root / "rebound", OWNER_FIXTURES / "owner-gamma",
            "malicious-rebound")
        _, donor = materialize_owner_fixture(
            bundle, fixture_root / "donor", OWNER_FIXTURES / "owner-alpha", "donor")
        rebound["candidate"]["binding"] = donor["candidate"]["binding"]
        rebound_path.write_text(json.dumps(rebound), encoding="utf-8")
        try:
            validate_evidence(rebound, bundle, rebound_path)
        except ContractError as error:
            if "candidate identity rebound" not in str(error):
                raise AssertionError(f"rebound wrong diagnostic: {error}") from error
        else:
            raise AssertionError("rebound mutation survived")
        killed += 1

    print(f"A5_K2_MUTATION_SUITE_PASS killed={killed} future_witness=1 reference=test-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
