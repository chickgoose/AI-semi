#!/usr/bin/env python3
"""Positive controls plus diagnostic-specific kills for ten required mutants."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from oracle import (
    BATCHED_IWRR_ROWS,
    CONTRACTS,
    ContractViolation,
    CycleInput,
    TwoLaneBufferedLink,
    WEIGHTS,
    check_weight_schedule,
    flatten_committed,
    run_trace,
    validate_observation,
)


ROOT = Path(__file__).resolve().parent
VECTORS = ROOT / "vectors.json"


@dataclass(frozen=True)
class Mutation:
    fault: str
    case: str
    diagnostic: str


MUTATIONS = (
    Mutation("false_aggregate_1551", "batched_iwrr_full_epoch", "FALSE_AGGREGATE_1551"),
    Mutation("calendar_advance_uncommitted_lane", "calendar_atomic_hold", "CALENDAR_ADVANCE_ON_UNCOMMITTED_LANE"),
    Mutation("stale_g1", "scalar_stale_g1", "STALE_G1"),
    Mutation("duplicate_source", "scalar_duplicate", "DUPLICATE_SOURCE"),
    Mutation("wrong_rr_state_after_g0", "scalar_intermediate_rr", "WRONG_RR_STATE_AFTER_G0"),
    Mutation("future_arrival_overclaim", "scalar_future_arrival", "FUTURE_ARRIVAL_OVERCLAIM"),
    Mutation("independent_lane_stall_corruption", "link_adapter_independent_stall", "INDEPENDENT_LANE_STALL_CORRUPTION"),
    Mutation("reset_phantom", "reset_pending_bundle", "RESET_PHANTOM"),
    Mutation("sparse_fallback_debt", "batched_sparse_debt_repay", "SPARSE_FALLBACK_DEBT"),
    Mutation("bitmap_popcount_confusion", "scalar_bitmap_confusion", "BITMAP_POPCOUNT_CONFUSION"),
)


def _cycle(record: dict) -> CycleInput:
    required = {"request", "bundle_ready"}
    if not required.issubset(record):
        raise ContractViolation(f"VECTOR_CYCLE_FIELDS record={record}")
    unknown = set(record) - required - {"reset", "future_request"}
    if unknown:
        raise ContractViolation(f"VECTOR_CYCLE_UNKNOWN fields={sorted(unknown)}")
    return CycleInput(
        request=int(record["request"], 16),
        bundle_ready=bool(record["bundle_ready"]),
        reset=bool(record.get("reset", False)),
        future_request=int(record.get("future_request", "0"), 16),
    )


def load_vectors(path: Path = VECTORS) -> dict[str, tuple[str, list[CycleInput]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "cases"} or document["schema_version"] != 2:
        raise ContractViolation("VECTOR_SCHEMA")
    result: dict[str, tuple[str, list[CycleInput]]] = {}
    for record in document["cases"]:
        if set(record) != {"name", "contract", "cycles"}:
            raise ContractViolation(f"VECTOR_CASE_FIELDS record={record}")
        name, contract = record["name"], record["contract"]
        if name in result or contract not in CONTRACTS:
            raise ContractViolation(f"VECTOR_CASE_ID name={name} contract={contract}")
        result[name] = (contract, [_cycle(cycle) for cycle in record["cycles"]])
    return result


def run_positive_controls(vectors: dict[str, tuple[str, list[CycleInput]]]) -> int:
    check_weight_schedule(BATCHED_IWRR_ROWS)
    for contract, trace in vectors.values():
        for observation in run_trace(contract, trace):
            validate_observation(observation)
    return len(vectors)


def _raise_if_equal(label: str, expected: object, actual: object) -> None:
    if expected != actual:
        raise ContractViolation(f"{label} expected={expected} actual={actual}")
    raise ContractViolation(f"MUTANT_SURVIVED diagnostic={label}")


def _exercise_mutant(
    mutation: Mutation, vectors: dict[str, tuple[str, list[CycleInput]]]
) -> None:
    if mutation.fault == "independent_lane_stall_corruption":
        link = TwoLaneBufferedLink(fault=mutation.fault)
        link.accept_atomic((4, 11))
        observation = link.step((True, False), (8, 15))
        if observation.held_after != (None, 11) or observation.scheduler_policy_touched:
            raise ContractViolation(
                "INDEPENDENT_LANE_STALL_CORRUPTION "
                f"held={observation.held_after} policy_touch={observation.scheduler_policy_touched}"
            )
        raise ContractViolation("MUTANT_SURVIVED diagnostic=INDEPENDENT_LANE_STALL_CORRUPTION")

    contract, trace = vectors[mutation.case]
    expected = run_trace(contract, trace)
    actual = run_trace(contract, trace, fault=mutation.fault)
    if mutation.fault in {
        "calendar_advance_uncommitted_lane",
        "duplicate_source",
        "future_arrival_overclaim",
        "reset_phantom",
        "bitmap_popcount_confusion",
    }:
        for observation in actual:
            validate_observation(observation)
        raise ContractViolation(f"MUTANT_SURVIVED diagnostic={mutation.diagnostic}")
    if mutation.fault == "false_aggregate_1551":
        counts = tuple(
            sum(source // 4 == row for source in flatten_committed(actual))
            for row in range(4)
        )
        _raise_if_equal("FALSE_AGGREGATE_1551", WEIGHTS, counts)
    elif mutation.fault == "stale_g1":
        _raise_if_equal("STALE_G1", expected[1].addresses[1], actual[1].addresses[1])
    elif mutation.fault == "wrong_rr_state_after_g0":
        _raise_if_equal("WRONG_RR_STATE_AFTER_G0", expected[0].addresses[1], actual[0].addresses[1])
    elif mutation.fault == "sparse_fallback_debt":
        _raise_if_equal(
            "SPARSE_FALLBACK_DEBT",
            expected[0].policy_after["fallback_debt"],
            actual[0].policy_after["fallback_debt"],
        )
    else:
        raise ContractViolation(f"MUTATION_UNROUTED fault={mutation.fault}")


def run_mutations(
    vectors: dict[str, tuple[str, list[CycleInput]]],
    mutations: Sequence[Mutation] = MUTATIONS,
) -> list[dict]:
    rows = []
    for mutation in mutations:
        if mutation.case != "link_adapter_independent_stall" and mutation.case not in vectors:
            raise ContractViolation(f"MUTATION_VECTOR_MISSING case={mutation.case}")
        try:
            _exercise_mutant(mutation, vectors)
        except ContractViolation as error:
            actual = str(error).split(maxsplit=1)[0]
            if actual != mutation.diagnostic:
                raise ContractViolation(
                    "MUTANT_DIAGNOSTIC_MISMATCH "
                    f"fault={mutation.fault} expected={mutation.diagnostic} actual={actual}"
                ) from error
            rows.append(
                {**asdict(mutation), "actual_diagnostic": actual, "killed": True}
            )
            continue
        raise ContractViolation(f"MUTANT_SURVIVED fault={mutation.fault}")
    return rows


def build_report() -> dict:
    vectors = load_vectors()
    mutations = run_mutations(vectors)
    return {
        "schema_version": 2,
        "decision": "PASS",
        "contracts": list(CONTRACTS),
        "weights": list(WEIGHTS),
        "vector_sha256": hashlib.sha256(VECTORS.read_bytes()).hexdigest(),
        "positive_cases": run_positive_controls(vectors),
        "mutations": mutations,
        "killed": len(mutations),
        "sentinel": "W8_A8_K2_INDEPENDENT_MUTATION_PASS",
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{report['sentinel']} positive={report['positive_cases']} "
            f"mutants={report['killed']} vectors_sha256={report['vector_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
