#!/usr/bin/env python3
"""Fail-closed static checks for the W6 composition boundary."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOP = ROOT / "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv"
TB = ROOT / "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv"
FAULT_TB = ROOT / "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_fault_tb.sv"


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

tb_text = TB.read_text(encoding="utf-8")
for token in (
    "accept_cycle[accepted] = ref_cycle",
    "accept_cycle[available] + 1",
    "accept_cycle[retired] + 2",
    "always_ff @(posedge ref_clk_i or negedge rst_n)",
    "dut.endpoint.launch_fire && drain_idle_o",
    "retire_valid_o) begin",
    "A7_W6_SAME_ADDRESS_RETRIGGER_PASS",
):
    if token not in tb_text:
        fail(f"missing directed timing/drain/retrigger evidence: {token}")

fault_text = FAULT_TB.read_text(encoding="utf-8")
for token in (
    "source_valid = '0",
    "protocol_fault_o",
    "retire_addr_o !== 4'ha",
    "A7_W6_STALE_NO_LIVE_NEGATIVE_CAUGHT",
):
    if token not in fault_text:
        fail(f"missing stale/no-live negative evidence: {token}")

print("A7_W6_COMPOSITION_CONTRACT_PASS")
