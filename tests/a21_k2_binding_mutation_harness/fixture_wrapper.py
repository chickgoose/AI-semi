#!/usr/bin/env python3
"""Executable black-box stand-in for the A2/A3 normalized binding pins.

This runner is test infrastructure, not owner RTL evidence.  It implements the
shared ordered-link contract, then injects one externally observable mutation.
The mutation harness communicates with it only through JSON files/process exit.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from oracle import Event, SCHEMA, output_record, validate_stimulus
from vectors import MUTATIONS


BINDINGS = ("a2_batched_iwrr_k2_normalized", "a3_k2_common_wrapper")


def simulate(stimulus_document: dict[str, Any], binding: str) -> dict[str, Any]:
    stimulus = validate_stimulus(stimulus_document)
    queue: list[Event] = []
    rows: list[dict[str, Any]] = []
    for item in stimulus:
        cycle = item["cycle"]
        if not item["reset_n"]:
            queue.clear()
            rows.append({
                "cycle": cycle,
                "offer_ready": False,
                "source_ready": [],
                "outputs": [output_record(0, None), output_record(1, None)],
                "drain_idle": binding == BINDINGS[0],
            })
            continue

        ready0, ready1 = item["retire_ready"]
        lane0 = queue[0] if queue else None
        lane1 = queue[1] if len(queue) == 2 and ready0 and ready1 else None
        outputs = [output_record(0, lane0), output_record(1, lane1)]
        retire_count = 0
        if queue and ready0:
            retire_count = 2 if len(queue) == 2 and ready1 else 1
        remaining = queue[retire_count:]
        offer = item["offer"]
        offer_ready = len(offer) <= 2 - len(remaining)
        source_ready = sorted(event.source for event in offer) if offer and offer_ready else []
        drain_idle = not queue and not item["source_live"] and not offer
        if offer and offer_ready:
            remaining.extend(offer)
        queue = remaining
        rows.append({
            "cycle": cycle,
            "offer_ready": offer_ready,
            "source_ready": source_ready,
            "outputs": outputs,
            "drain_idle": drain_idle,
        })
    return {"schema": SCHEMA, "binding": binding, "cycles": rows}


def _first_output_cycle(rows: list[dict[str, Any]], event_id: str) -> int:
    for row in rows:
        if any(output.get("event_id") == event_id for output in row["outputs"]):
            return int(row["cycle"])
    raise AssertionError(f"fixture lacks output event {event_id}")


def inject(document: dict[str, Any], mutation: str) -> dict[str, Any]:
    result = deepcopy(document)
    rows = result["cycles"]
    if mutation == "partial_count2_accept":
        rows[1]["source_ready"] = rows[1]["source_ready"][:1]
    elif mutation == "lane_swap":
        cycle = _first_output_cycle(rows, "a:s0")
        rows[cycle]["outputs"][0], rows[cycle]["outputs"][1] = (
            rows[cycle]["outputs"][1], rows[cycle]["outputs"][0]
        )
        rows[cycle]["outputs"][0]["lane"] = 0
        rows[cycle]["outputs"][1]["lane"] = 1
    elif mutation == "younger_bypass":
        rows[2]["outputs"][1] = {
            "lane": 1,
            "valid": True,
            "source": 5,
            "event_id": "b:s5",
            "payload": 0xB005,
        }
    elif mutation == "overflow_drop":
        rows[2]["offer_ready"] = True
        rows[2]["source_ready"] = [5, 10]
    elif mutation == "duplicate":
        rows[3]["outputs"][0] = {
            "lane": 0,
            "valid": True,
            "source": 0,
            "event_id": "a:s0",
            "payload": 0xA000,
        }
        rows[3]["drain_idle"] = False
    elif mutation == "wrong_source_ready":
        rows[1]["source_ready"] = [0, 6]
    elif mutation == "unstable_stall":
        rows[3]["outputs"][0] = {
            "lane": 0,
            "valid": True,
            "source": 5,
            "event_id": "corrupt:s5",
            "payload": 0xBAD5,
        }
    elif mutation == "early_drain":
        rows[2]["drain_idle"] = True
    elif mutation == "stale_reset":
        rows[3]["outputs"][0] = {
            "lane": 0,
            "valid": True,
            "source": 0,
            "event_id": "a:s0",
            "payload": 0xA000,
        }
    elif mutation == "latency_shift":
        rows[2]["outputs"] = [output_record(0, None), output_record(1, None)]
        rows[3]["outputs"][0] = {
            "lane": 0,
            "valid": True,
            "source": 0,
            "event_id": "a:s0",
            "payload": 0xA000,
        }
        rows[3]["drain_idle"] = False
    else:
        raise ValueError(f"unknown mutation: {mutation}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", choices=BINDINGS, required=True)
    parser.add_argument("--stimulus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mutation", choices=("none", *MUTATIONS), default="none")
    args = parser.parse_args()
    stimulus = json.loads(args.stimulus.read_text(encoding="utf-8"))
    result = simulate(stimulus, args.binding)
    if args.mutation != "none":
        result = inject(result, args.mutation)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"A21_K2_FIXTURE_TRACE binding={args.binding} mutation={args.mutation} "
        f"cycles={len(result['cycles'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
