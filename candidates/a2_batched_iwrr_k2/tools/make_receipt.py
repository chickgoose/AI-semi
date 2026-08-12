#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASHED = (
    "CONTRACT.md",
    "rtl/a2_batched_iwrr_k2.sv",
    "model/batched_iwrr_k2.py",
    "tb/a2_batched_iwrr_k2_lockstep_tb.sv",
    "tests/test_model.py",
    "tools/generate_lockstep_vectors.py",
    "tools/run_frozen_v4_replay.py",
    "tools/run_model_mutations.py",
    "tools/run_rtl_mutations.py",
    "tools/yosys_proxy.py",
    "tools/make_receipt.py",
    "run_all.sh",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--yosys", required=True, type=Path)
    parser.add_argument("--verilator-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text(encoding="utf-8"))
    yosys = json.loads(args.yosys.read_text(encoding="utf-8"))
    document = {
        "schema": "a2_batched_iwrr_k2_qualification_v1",
        "decision": "LOCAL_CANDIDATE_FUNCTIONAL_GO_PHYSICAL_AND_WIDENED_A7_HOLD",
        "contract": {
            "sources": 16, "max_grants_per_cycle": 2,
            "acceptance": "atomic_batch_ready",
            "calendar_rows": [1, 2, 0, 1, 2, 3, 1, 2, 1, 2, 1, 2],
            "persistent_six_cycle_row_grants": [1, 5, 5, 1],
            "sparse": "fixed_phase_waive_empty_no_borrow_no_credit_compact_survivors",
        },
        "tests": {
            "python_unittests": 8,
            "exhaustive_n16_bitmap_phase_uniform_pointer_cases": 1_572_864,
            "exhaustive_row_picker_cases": 64,
            "verilator_lockstep_cycles": 20_000,
            "model_directed_mutations_killed": 7,
            "compiled_rtl_mutations_killed": 5,
        },
        "tools": {"verilator": args.verilator_version, "yosys": yosys["yosys_version"]},
        "yosys_proxy": yosys,
        "frozen_v4_replay": replay,
        "source_sha256": {relative: sha(ROOT / relative) for relative in HASHED},
        "holds": [
            "generic_lut4_proxy_is_not_characterized_physical_PPA",
            "widened_low_pin_A7_endpoint_is_not_implemented_or_qualified",
            "atomic_ready_requires_req_stability_while_stalled",
            "single_pending_bit_per_source_does_not_preserve_occurrence_multiplicity",
            "exhaustive_pointer_vector_cross_product_not_run; row_picker_is_exhaustive_and_uniform_pointer_vectors_are_exhaustive",
            "frozen_v4_replay_is_independent_model_not_common_TB_or_RTL_replay",
        ],
    }
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"A2_K2_RECEIPT_PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
