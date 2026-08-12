#!/usr/bin/env python3
"""Compile and kill directed synthesizable-RTL mutants with lockstep vectors."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl/a2_batched_iwrr_k2.sv"
TB = ROOT / "tb/a2_batched_iwrr_k2_lockstep_tb.sv"

MUTANTS = {
    "wrong_weight_token": ("3'd0: phase_rows = {2'd2, 2'd1};",
                           "3'd0: phase_rows = {2'd2, 2'd0};"),
    "fixed_priority": ("row_ptr_q[first_row]);", "2'd0);"),
    "advance_on_stall": ("if ((!any_valid) || grant_ready) begin",
                         "if ((!any_valid) || 1'b1) begin"),
    "drop_second_lane": ("if (second_pick[2]) begin\n      if",
                         "if (1'b0) begin\n      if"),
    "wrong_reset_phase": ("phase_q <= 3'd0;", "phase_q <= 3'd1;"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verilator", required=True, type=Path)
    parser.add_argument("--vectors", required=True, type=Path)
    args = parser.parse_args()
    if not args.verilator.is_file() or not os.access(args.verilator, os.X_OK):
        raise SystemExit("A2_K2_RTL_MUTATION_FAIL Verilator unavailable")
    source = RTL.read_text(encoding="utf-8")
    killed = []
    with tempfile.TemporaryDirectory(prefix="a2-k2-rtl-mutants-") as temporary:
        temp = Path(temporary)
        for name, (old, new) in MUTANTS.items():
            if source.count(old) != 1:
                raise RuntimeError(f"mutation anchor is not unique: {name}")
            mutant = temp / f"{name}.sv"
            mutant.write_text(source.replace(old, new), encoding="utf-8")
            obj = temp / f"obj-{name}"
            compile_command = [
                str(args.verilator), "--binary", "--timing", "--assert", "-Wall",
                "-Wno-WIDTHEXPAND", "-Wno-UNUSEDSIGNAL", "-Wno-DECLFILENAME",
                "--Mdir", str(obj),
                "-o", "sim", str(mutant), str(TB),
                "--top-module", "a2_batched_iwrr_k2_lockstep_tb",
            ]
            compiled = subprocess.run(compile_command, text=True, capture_output=True)
            if compiled.returncode:
                raise RuntimeError(f"mutant did not compile {name}: {compiled.stderr[-1000:]}")
            run = subprocess.run([str(obj / "sim"), f"+VECTORS={args.vectors}"],
                                 text=True, capture_output=True)
            if run.returncode == 0 or "A2_K2_LOCKSTEP_FAIL" not in run.stdout:
                raise RuntimeError(f"RTL mutant survived {name}")
            killed.append(name)
    print(f"A2_K2_RTL_MUTATION_PASS killed={len(killed)} names={','.join(killed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
