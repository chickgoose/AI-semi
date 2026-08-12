#!/usr/bin/env python3
"""Fail-closed W7 capability/timing/reset contract validator."""

import argparse
import json
from pathlib import Path


def reject(kind: str, detail: str) -> None:
    raise SystemExit(f"A7_W7_CONTRACT_{kind}_CAUGHT: {detail}")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=root / "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_w7.manifest.json")
    parser.add_argument("--sdc", type=Path, default=root / "constraints/a7_weighted_fovea_ddr_w7.sdc")
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    sdc = args.sdc.read_text(encoding="utf-8")

    if data.get("address_semantics") != "address_only_n16" or data.get("source_count") != 16:
        reject("ADDRESS", "candidate must remain address-only N16")
    caps = data.get("capabilities", {})
    if caps.get("sink_always_ready") is not True or caps.get("output_backpressure") is not False:
        reject("BACKPRESSURE", "mandatory sink is always-ready; optional backpressure must be false")
    if caps.get("unrelated_clock_cdc") is not False:
        reject("CDC", "R1 candidate must not claim unrelated-clock CDC")
    storage = data.get("storage", {})
    if storage.get("wrapper_queue_depth") != 0 or storage.get("output_queue_depth") != 0:
        reject("QUEUE", "no uncharged wrapper/output queue is allowed")
    reset = data.get("reset_contract", {})
    if (reset.get("assertion") != "only_after_drain_idle_and_burst_clock_low" or
            reset.get("mid_traffic_flush_supported") is not False or
            reset.get("accepted_event_abort_allowed") is not False):
        reject("RESET", "reset is drain-only and cannot abort accepted traffic")
    clock = data.get("clock_contract", {})
    if (clock.get("relationship") != "same_source_phase_related_r1" or
            clock.get("reference_period_ns") != 16.0 or
            clock.get("sample_phase_from_reference_rise_ns") != 4.0 or
            clock.get("sample_high_ns") != 8.0 or clock.get("sample_low_ns") != 8.0):
        reject("PHASE", "frozen R1 clock relation is 16ns, +4ns phase, 8/8 duty")
    required_sdc = (
        "-waveform {0.000 8.000}", "-waveform {4.000 12.000}",
        "create_generated_clock -name a7_burst_clk", "set_min_pulse_width -high",
        "set_min_pulse_width -low", "source_valid[*]", "retire_valid_o",
    )
    if any(token not in sdc for token in required_sdc) or "set_false_path -from [get_ports rst_n]" in sdc:
        reject("TIMING", "SDC lost phase/pulse/I/O constraints or false-pathed reset")
    if data.get("digital_status") != "submission_ready" or data.get("physical_status") != "hold":
        reject("STATUS", "digital is submission-ready while physical remains HOLD")
    print("A7_W7_SUBMISSION_CONTRACT_PASS scope=always_ready_phase_related_no_queue physical=HOLD")


if __name__ == "__main__":
    main()
