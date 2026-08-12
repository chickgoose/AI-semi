#!/usr/bin/env python3
"""Small directed traces that expose each normalized-wrapper mutation."""

from __future__ import annotations

from typing import Any

from oracle import SCHEMA


MUTATIONS: dict[str, str] = {
    "partial_count2_accept": "PARTIAL_COUNT2_ACCEPT",
    "lane_swap": "GLOBAL_ORDER_MISMATCH",
    "younger_bypass": "YOUNGER_BYPASS",
    "overflow_drop": "OVERFLOW_DROP",
    "duplicate": "DUPLICATE_RETIRE",
    "wrong_source_ready": "WRONG_SOURCE_READY",
    "unstable_stall": "UNSTABLE_STALL",
    "early_drain": "EARLY_DRAIN",
    "stale_reset": "STALE_RESET",
    "latency_shift": "LATENCY_SHIFT",
}


def event(label: str, source: int, payload: int) -> dict[str, Any]:
    return {"source": source, "event_id": label, "payload": payload}


def cycle(
    index: int,
    *,
    offer: list[dict[str, Any]] | None = None,
    live: list[dict[str, Any]] | None = None,
    ready: tuple[bool, bool] = (True, True),
    reset_n: bool = True,
) -> dict[str, Any]:
    return {
        "cycle": index,
        "reset_n": reset_n,
        "retire_ready": list(ready),
        "offer": offer or [],
        "source_live": live or [],
    }


def document(name: str, cycles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "name": name,
        "source_count": 16,
        "retire_lanes": 2,
        "cycles": cycles,
    }


def build_vectors() -> dict[str, dict[str, Any]]:
    a = event("a:s0", 0, 0xA000)
    b = event("b:s5", 5, 0xB005)
    c = event("c:s10", 10, 0xC00A)
    d = event("d:s15", 15, 0xD00F)

    pair = lambda name: document(name, [  # noqa: E731 - keeps the cases compact
        cycle(0, reset_n=False),
        cycle(1, offer=[a, b], live=[a, b]),
        cycle(2),
        cycle(3),
    ])

    return {
        "partial_count2_accept": pair("partial_count2_accept"),
        "lane_swap": pair("lane_swap"),
        "wrong_source_ready": pair("wrong_source_ready"),
        "younger_bypass": document("younger_bypass", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a, b], live=[a, b]),
            cycle(2, ready=(False, True)),
            cycle(3, ready=(True, True)),
            cycle(4),
        ]),
        "overflow_drop": document("overflow_drop", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a], live=[a]),
            cycle(2, offer=[b, c], live=[b, c], ready=(False, False)),
            cycle(3, offer=[b, c], live=[b, c], ready=(True, False)),
            cycle(4),
            cycle(5),
        ]),
        "duplicate": document("duplicate", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a], live=[a]),
            cycle(2),
            cycle(3),
        ]),
        "unstable_stall": document("unstable_stall", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a], live=[a]),
            cycle(2, ready=(False, True)),
            cycle(3, ready=(False, True)),
            cycle(4),
            cycle(5),
        ]),
        "early_drain": document("early_drain", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a], live=[a]),
            cycle(2, ready=(False, False)),
            cycle(3),
            cycle(4),
        ]),
        "stale_reset": document("stale_reset", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a], live=[a], ready=(False, False)),
            cycle(2, reset_n=False, ready=(False, False)),
            cycle(3, offer=[d], live=[d], ready=(False, False)),
            cycle(4),
            cycle(5),
        ]),
        "latency_shift": document("latency_shift", [
            cycle(0, reset_n=False),
            cycle(1, offer=[a], live=[a]),
            cycle(2),
            cycle(3),
            cycle(4),
        ]),
    }


def vector_for(mutation: str) -> dict[str, Any]:
    if mutation not in MUTATIONS:
        raise KeyError(f"unknown mutation: {mutation}")
    return build_vectors()[mutation]
