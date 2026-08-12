#!/usr/bin/env python3
"""Deliberately broken traces used to prove that the oracle fails closed."""

from __future__ import annotations

from oracle import Event


def missing_req_mask_duplicate() -> list[Event]:
    return [
        Event(0, "request", 1, 0x5),
        Event(1, "native", 1, 0x5),
        Event(2, "native", 1, 0x5),
    ]


def valid_edge_detector_drop() -> list[Event]:
    return [
        Event(1, "source", 1, 0x2, valid=True, ready=True),
        Event(1, "launch", 1, 0x2),
        Event(2, "source", 2, 0xB, valid=True, ready=True),
        Event(2, "available", 1, 0x2),
        Event(3, "retire", 1, 0x2),
    ]


def reset_first_edge_loss() -> list[Event]:
    return [
        Event(0, "reset_assert"),
        Event(1, "reset_release"),
        Event(2, "source", 1, 0x7, valid=True, ready=True),
    ]


def premature_drain() -> list[Event]:
    return [
        Event(1, "source", 1, 0x4, valid=True, ready=True),
        Event(1, "launch", 1, 0x4),
        Event(1, "drain", drain_idle=True, launch_fire=True,
              retire_valid=False),
        Event(2, "available", 1, 0x4),
        Event(3, "retire", 1, 0x4),
    ]


def swapped_address() -> list[Event]:
    return [
        Event(1, "source", 1, 0x3, valid=True, ready=True),
        Event(1, "launch", 1, 0x3),
        Event(2, "available", 1, 0x3),
        Event(3, "retire", 1, 0xC),
    ]


def plus_latency() -> list[Event]:
    return [
        Event(1, "source", 1, 0x9, valid=True, ready=True),
        Event(1, "launch", 1, 0x9),
        Event(3, "available", 1, 0x9),
        Event(4, "retire", 1, 0x9),
    ]


def stale_retrigger() -> list[Event]:
    return [
        Event(0, "request", 1, 0x6),
        Event(1, "native", 1, 0x6),
        Event(2, "request", 2, 0x6),
        # Old registered valid/address is incorrectly rebound to the new credit.
        Event(2, "native", 1, 0x6),
    ]


ALL = {
    "missing_req_mask_duplicate": (missing_req_mask_duplicate,
                                   "NATIVE_DUPLICATE_NO_REQUEST"),
    "valid_edge_detector_drop": (valid_edge_detector_drop,
                                 "VALID_EDGE_DETECTOR_DROP"),
    "reset_first_edge_loss": (reset_first_edge_loss, "RESET_FIRST_EDGE_LOSS"),
    "premature_drain": (premature_drain, "PREMATURE_DRAIN"),
    "swapped_address": (swapped_address, "RETIRED_ADDRESS_SWAP"),
    "plus_latency": (plus_latency, "AVAILABILITY_LATENCY"),
    "stale_retrigger": (stale_retrigger, "STALE_RETRIGGER_CAUSALITY"),
}
