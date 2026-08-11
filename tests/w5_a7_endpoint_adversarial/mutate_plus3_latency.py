#!/usr/bin/env python3
"""Insert one ref-cycle into the exact-bound owner retire observer."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ORIGINAL_SHA256 = "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("observer", type=Path)
    args = parser.parse_args()
    source = args.observer.read_bytes()
    if hashlib.sha256(source).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("latency mutant input is not the exact 42377ca observer")
    text = source.decode("utf-8")
    text = text.replace(
        "  always_ff @(posedge ref_clk_i or negedge rst_n) begin\n",
        "  logic [3:0] pending_addr_q;\n"
        "  logic       pending_valid_q;\n\n"
        "  always_ff @(posedge ref_clk_i or negedge rst_n) begin\n",
        1,
    )
    original = """    if (!rst_n) begin
      seen_toggle_o <= 1'b0;
      retire_addr_o <= '0;
      retire_valid_o <= 1'b0;
    end else begin
      retire_valid_o <= raw_toggle_i ^ seen_toggle_o;
      seen_toggle_o <= raw_toggle_i;
      if (raw_toggle_i ^ seen_toggle_o)
        retire_addr_o <= raw_addr_i;
    end
"""
    replacement = """    if (!rst_n) begin
      seen_toggle_o <= 1'b0;
      pending_addr_q <= '0;
      pending_valid_q <= 1'b0;
      retire_addr_o <= '0;
      retire_valid_o <= 1'b0;
    end else begin
      pending_valid_q <= raw_toggle_i ^ seen_toggle_o;
      retire_valid_o <= pending_valid_q;
      seen_toggle_o <= raw_toggle_i;
      if (raw_toggle_i ^ seen_toggle_o)
        pending_addr_q <= raw_addr_i;
      if (pending_valid_q)
        retire_addr_o <= pending_addr_q;
    end
"""
    if text.count(original) != 1:
        raise ValueError("exact observer body was not found once")
    args.observer.write_text(text.replace(original, replacement), encoding="utf-8")
    print("W5_A8_PLUS3_LATENCY_MUTANT_MATERIALIZED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
