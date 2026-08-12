#!/usr/bin/env python3
"""Exhaustively compare initial K2 folds with an external A8 oracle blob."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE))
from oracle import PolicyState, scalar_prefix_k2  # noqa: E402


def load_external(path: Path):
    spec = importlib.util.spec_from_file_location("a8_external_k2_oracle", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load A8 oracle {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def packed_tree(tree) -> int:
    return (
        int(tree.low.last_grant)
        | (int(tree.high.last_grant) << 1)
        | (int(tree.top.last_grant) << 2)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a8-oracle", type=Path, required=True)
    args = parser.parse_args()
    a8 = load_external(args.a8_oracle)
    checked = 0
    for request in range(1 << 16):
        actual, actual_state = scalar_prefix_k2(request, PolicyState())
        state = a8.FoveaState()
        remaining = request
        expected = []
        for _ in range(2):
            source, state = a8.canonical_fovea_step(remaining, state)
            if source is None:
                break
            expected.append(source)
            remaining &= ~(1 << source)
        external_state = (
            int(state.round_index),
            packed_tree(state.center),
            packed_tree(state.peripheral),
            packed_tree(state.column),
        )
        candidate_state = (
            actual_state.round,
            actual_state.center,
            actual_state.peripheral,
            actual_state.column,
        )
        if tuple(expected) != actual or external_state != candidate_state:
            raise SystemExit(
                "A8_CANDIDATE_DIVERGENCE "
                f"request={request:04x} expected={expected}/{external_state} "
                f"actual={actual}/{candidate_state}"
            )
        checked += 1
    print(f"A3_K2_A8_EXHAUSTIVE_INITIAL_PASS masks={checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
