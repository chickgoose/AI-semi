#!/usr/bin/env python3
"""Exhaust the safe-selection contract for N=3 over short sequences.

Prediction controls are unconstrained, so this covers oracle, last-successor,
Markov, alias/cold-start behavior, and deliberately adversarial predictions.
"""

from __future__ import annotations

import itertools


N = 3
STEPS = 3


def fallback(pending: list[int | None], start: int) -> int | None:
    for offset in range(N):
        source = (start + offset) % N
        if pending[source] is not None:
            return source
    return None


def check_case(arrivals: tuple[int, ...], ready: tuple[int, ...],
               predictions: tuple[int, ...]) -> None:
    pending: list[int | None] = [None] * N
    output: int | None = None
    accepted: list[int] = []
    delivered: list[int] = []
    fallback_start = 0
    next_id = 0

    for cycle in range(STEPS + 2 * N + 2):
        arrival_mask = arrivals[cycle] if cycle < STEPS else 0
        retire_ready = bool(ready[cycle]) if cycle < STEPS else True
        prediction = predictions[cycle] if cycle < STEPS else -1
        for source in range(N):
            if arrival_mask & (1 << source):
                if pending[source] is None:
                    pending[source] = next_id
                    accepted.append(next_id)
                next_id += 1

        slot_available = output is None or retire_ready
        fallback_source = fallback(pending, fallback_start) if slot_available else None
        prediction_valid = prediction >= 0
        prediction_hit = (fallback_source is not None and prediction_valid and
                          pending[prediction] is not None)
        selected = prediction if prediction_hit else fallback_source

        assert selected is None or pending[selected] is not None
        if prediction_valid and not prediction_hit:
            assert selected == fallback_source

        old_output = output
        if old_output is not None and retire_ready:
            delivered.append(old_output)
            output = None
        if slot_available and selected is not None:
            event = pending[selected]
            pending[selected] = None
            fallback_start = (selected + 1) % N
            if old_output is None and retire_ready and prediction_hit:
                delivered.append(event)  # same-cycle safe bypass
            else:
                output = event

    assert output is None and all(item is None for item in pending)
    assert len(delivered) == len(set(delivered))
    assert sorted(delivered) == sorted(accepted)


def main() -> int:
    cases = 0
    masks = range(1 << N)
    pred_values = range(-1, N)
    for arrivals in itertools.product(masks, repeat=STEPS):
        for ready in itertools.product((0, 1), repeat=STEPS):
            for predictions in itertools.product(pred_values, repeat=STEPS):
                check_case(arrivals, ready, predictions)
                cases += 1
    print(f"A5_EXHAUSTIVE_SMALL_N_PASS n={N} steps={STEPS} cases={cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
