#!/usr/bin/env python3
"""Fail-closed static checks for the W6 composition boundary."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOP = ROOT / "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv"


def fail(message: str) -> None:
    print(f"A7_W6_CONTRACT_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


text = TOP.read_text(encoding="utf-8")

required = {
    "exact source_valid port": "input  logic [15:0] source_valid",
    "exact source_ready port": "output logic [15:0] source_ready",
    "external macro selection": "`A7_WEIGHTED_FOVEA_MODULE canonical_fovea",
    "existing R1 endpoint": "a7_r1_candidate_endpoint endpoint",
    "stateless current-result request mask":
        "source_valid & ~current_result_mask",
    "accepted-current-result ready":
        "current_result_mask & source_valid",
    "source drain term": "~(|source_valid)",
    "request drain term": "~(|fovea_req)",
    "fovea-result drain term": "~fovea_valid",
    "endpoint idle drain term": "endpoint_drain_idle",
    "final retire drain term": "~retire_valid_o",
}
for description, token in required.items():
    if token not in text:
        fail(f"missing {description}: {token}")

for forbidden in ("source_event", "FIFO_DEPTH", "fifo_", "queue_", "payload"):
    if forbidden in text:
        fail(f"forbidden reconstruction/buffering token present: {forbidden}")

if "always_ff" in text or "always_latch" in text:
    fail("composition wrapper must have zero functional sequential state")

if "reset_release_armed" in text:
    fail("wrapper must reuse endpoint_ready instead of charging another arm bit")

print("A7_W6_COMPOSITION_CONTRACT_PASS")
