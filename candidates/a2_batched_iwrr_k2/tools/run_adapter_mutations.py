#!/usr/bin/env python3
"""Compile and kill the four required charged-adapter RTL mutations."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TB = ROOT / "tb/a2_batched_iwrr_k2_adapter_lockstep_tb.sv"

MUTANTS = {
    "partial_bundle": (
        "link",
        "offer_ready = (offer_count <= (2'd2 - remaining_count));",
        "offer_ready = (offer_count == 0) || ((2'd2 - remaining_count) != 0);",
    ),
    "overflow": (
        "link",
        "offer_ready = (offer_count <= (2'd2 - remaining_count));",
        "offer_ready = (offer_count <= (2'd3 - remaining_count));",
    ),
    "wrong_state_advance": (
        "normalized",
        ".bundle_ready(native_bundle_ready),",
        ".bundle_ready(native_bundle_ready || (native_count != 0)),",
    ),
    "reorder": (
        "link",
        "retire_valid[1] = (count_q == 2) && retire_ready[0] && retire_ready[1];",
        "retire_valid[1] = (count_q == 2) && retire_ready[1];",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verilator", required=True, type=Path)
    parser.add_argument("--vectors", required=True, type=Path)
    parser.add_argument("--rtl-root", type=Path, default=ROOT / "rtl")
    args = parser.parse_args()
    if not args.verilator.is_file() or not os.access(args.verilator, os.X_OK):
        raise SystemExit("A2_K2_ADAPTER_MUTATION_FAIL Verilator unavailable")

    owner = args.rtl_root / "a2_batched_iwrr_k2.sv"
    link = args.rtl_root / "a2_k2_ordered_link_adapter.sv"
    normalized = args.rtl_root / "a2_batched_iwrr_k2_normalized.sv"
    sources = {
        "link": link.read_text(encoding="utf-8"),
        "normalized": normalized.read_text(encoding="utf-8"),
    }
    killed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="a2-k2-adapter-mutants-") as temporary:
        temporary_root = Path(temporary)
        for name, (target, old, new) in MUTANTS.items():
            source = sources[target]
            if source.count(old) != 1:
                raise RuntimeError(f"mutation anchor is not unique: {name}")
            mutant = temporary_root / f"{name}.sv"
            mutant.write_text(source.replace(old, new), encoding="utf-8")
            compile_link = mutant if target == "link" else link
            compile_normalized = mutant if target == "normalized" else normalized
            object_root = temporary_root / f"obj-{name}"
            compile_command = [
                str(args.verilator), "--binary", "--timing", "--assert", "-Wall",
                "-Wno-WIDTHEXPAND", "-Wno-UNUSEDSIGNAL", "-Wno-TIMESCALEMOD",
                "-Wno-UNOPTFLAT", "-Wno-DECLFILENAME",
                "--Mdir", str(object_root), "-o", "sim",
                str(owner), str(compile_link), str(compile_normalized), str(TB),
                "--top-module", "a2_batched_iwrr_k2_adapter_lockstep_tb",
            ]
            compiled = subprocess.run(
                compile_command, text=True, capture_output=True, check=False,
            )
            if compiled.returncode:
                raise RuntimeError(
                    f"mutant did not compile {name}: {compiled.stderr[-1600:]}"
                )
            run = subprocess.run(
                [str(object_root / "sim"), f"+VECTORS={args.vectors}"],
                text=True, capture_output=True, check=False,
            )
            diagnostic = run.stdout + run.stderr
            if run.returncode == 0 or not any(
                marker in diagnostic
                for marker in (
                    "A2_K2_ADAPTER_LOCKSTEP_FAIL",
                    "A2_K2_LINK illegal count",
                    "A2_K2_LINK non-fitting offer accepted",
                )
            ):
                raise RuntimeError(
                    f"RTL mutant survived without a property diagnostic {name}: "
                    f"{diagnostic[-1200:]}"
                )
            killed.append(name)

    print(
        "A2_K2_ADAPTER_MUTATION_PASS "
        f"killed={len(killed)} names={','.join(killed)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
