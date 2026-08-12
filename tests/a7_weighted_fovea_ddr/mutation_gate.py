#!/usr/bin/env python3
"""Compile and execute five W6 directed false-PASS mutants fail-closed."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
R1 = ROOT / "rtl/candidates/a7_r1_candidate_endpoint"
TOP = ROOT / "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv"
TB = ROOT / "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv"
QUALIFIER = R1 / "a7_r1_launch_qualifier.sv"


def replace_once(text: str, old: str, new: str, name: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{name}: mutation anchor count={count}, expected=1")
    return text.replace(old, new, 1)


def run(command: list[str], log: Path) -> int:
    with log.open("wb") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream,
                              stderr=subprocess.STDOUT, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verilator", required=True)
    parser.add_argument("--canonical-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    canonical = {
        "arbiter2": args.canonical_dir / "arbiter2.v",
        "arbiter4": args.canonical_dir / "arbiter4_tree.v",
        "fovea": args.canonical_dir / "aer_tx16_trad_rowcol_fovea.v",
    }
    for path in canonical.values():
        if not path.is_file():
            raise RuntimeError(f"missing canonical input: {path}")

    top_text = TOP.read_text(encoding="utf-8")
    qualifier_text = QUALIFIER.read_text(encoding="utf-8")
    fovea_text = canonical["fovea"].read_text(encoding="utf-8")

    late_qualifier = replace_once(
        qualifier_text,
        "  logic reset_release_armed_q;\n",
        "  logic reset_release_armed_q;\n  logic mutant_delay_q;\n",
        "late_arm_state_decl",
    )
    late_qualifier = replace_once(
        late_qualifier,
        "    if (!rst_n)\n"
        "      reset_release_armed_q <= 1'b0;\n"
        "    else\n"
        "      reset_release_armed_q <= 1'b1;",
        "    if (!rst_n) begin\n"
        "      reset_release_armed_q <= 1'b0;\n"
        "      mutant_delay_q <= 1'b0;\n"
        "    end else begin\n"
        "      reset_release_armed_q <= mutant_delay_q;\n"
        "      mutant_delay_q <= 1'b1;\n"
        "    end",
        "late_arm_logic",
    )

    second_grant_top = replace_once(
        top_text,
        "  logic        endpoint_drain_idle;\n",
        "  logic        endpoint_drain_idle;\n"
        "  logic        mutant_seen_addr6_q;\n",
        "second_grant_state_decl",
    )
    second_grant_top = replace_once(
        second_grant_top,
        "  // endpoint_ready is the existing R1 safe-release qualifier.",
        "  always_ff @(posedge ref_clk_i or negedge rst_n) begin\n"
        "    if (!rst_n) mutant_seen_addr6_q <= 1'b0;\n"
        "    else if (source_ready[6]) mutant_seen_addr6_q <= 1'b1;\n"
        "  end\n\n"
        "  // endpoint_ready is the existing R1 safe-release qualifier.",
        "second_grant_state_logic",
    )
    second_grant_top = replace_once(
        second_grant_top,
        "(source_valid & ~current_result_mask) : '0;",
        "(source_valid & ~current_result_mask &\n"
        "                      ~(mutant_seen_addr6_q ? 16'h0040 : 16'h0000)) : '0;",
        "second_grant_mask",
    )

    mutants = [
        (
            "bubble",
            "fovea",
            replace_once(fovea_text, "valid <= |row_gnt;",
                         "valid <= |row_gnt & round[0];", "bubble"),
            "A7_W6_FULL_CONTENTION_BUBBLE_CAUGHT",
        ),
        (
            "early_arm",
            "qualifier",
            replace_once(qualifier_text,
                         "assign event_ready_o = rst_n & reset_release_armed_q;",
                         "assign event_ready_o = rst_n;", "early_arm"),
            "A7_W6_RESET_R0_PRE_BOUNDARY_CAUGHT",
        ),
        (
            "late_arm",
            "qualifier",
            late_qualifier,
            "A7_W6_RESET_R0_POST_ARM_CAUGHT",
        ),
        (
            "endpoint_drain_term_removed",
            "top",
            replace_once(top_text,
                         "rst_n & endpoint_ready & endpoint_drain_idle &",
                         "rst_n & endpoint_ready & 1'b1 &", "drain_term"),
            "A7_W6_ENDPOINT_DRAIN_TERM_REMOVAL_CAUGHT",
        ),
        (
            "second_grant_suppressed",
            "top",
            second_grant_top,
            "A7_W6_SECOND_GRANT_SUPPRESSION_CAUGHT attempt=1",
        ),
    ]

    fixed_r1 = [
        R1 / "a7_r1_icg_boundary.sv",
        R1 / "a7_r1_ddr_tx.sv",
        R1 / "a7_r1_ddr_rx.sv",
        R1 / "a7_r1_retire_observer.sv",
        R1 / "a7_r1_candidate_endpoint.sv",
    ]
    diagnostic_pattern = re.compile(
        rb"(^|\s)(%Warning|%Error|Warning:|ERROR:|FATAL:|FAILED:)", re.MULTILINE
    )

    for name, target, mutated_text, expected in mutants:
        mutant_dir = args.output / name
        mutant_dir.mkdir()
        filenames = {
            "fovea": "aer_tx16_trad_rowcol_fovea.v",
            "qualifier": "a7_r1_launch_qualifier.sv",
            "top": "a7_weighted_fovea_ddr.sv",
        }
        mutated = mutant_dir / filenames[target]
        mutated.write_text(mutated_text, encoding="utf-8")

        qualifier = mutated if target == "qualifier" else QUALIFIER
        top = mutated if target == "top" else TOP
        fovea = mutated if target == "fovea" else canonical["fovea"]
        obj = mutant_dir / "obj"
        command = [
            args.verilator,
            "--binary", "--timing", "-Wall", "-Wno-fatal", "-Wno-BLKSEQ",
            "-Wno-SYNCASYNCNET", "-Wno-UNUSEDSIGNAL", "-Wno-UNOPTFLAT",
            "--top-module", "a7_weighted_fovea_ddr_tb",
            "--Mdir", str(obj), "-o", "mutant_sim",
            "-DA7_WEIGHTED_FOVEA_MODULE=aer_tx16_trad_rowcol_fovea",
            str(qualifier), *(str(path) for path in fixed_r1), str(top),
            str(canonical["arbiter2"]), str(canonical["arbiter4"]), str(fovea),
            str(TB),
        ]
        build_log = mutant_dir / "build.log"
        build_rc = run(command, build_log)
        build_bytes = build_log.read_bytes()
        if build_rc != 0 or diagnostic_pattern.search(build_bytes):
            print(f"A7_W6_MUTANT_GATE_FAIL name={name} compile_exit={build_rc}",
                  file=sys.stderr)
            return 1

        run_log = mutant_dir / "run.log"
        run_rc = run([str(obj / "mutant_sim")], run_log)
        run_text = run_log.read_text(encoding="utf-8", errors="replace")
        caught = re.findall(r"A7_W6_[A-Z0-9_]+_CAUGHT(?: attempt=\d+)?",
                            run_text)
        if run_rc == 0 or caught != [expected]:
            print(f"A7_W6_MUTANT_GATE_FAIL name={name} runtime_exit={run_rc} "
                  f"caught={caught!r} expected={expected!r}", file=sys.stderr)
            return 1
        print(f"A7_W6_MUTANT_EXPECTED_FAIL_PASS name={name} "
              f"compile_exit=0 runtime_exit={run_rc} diagnostic={expected}")

    print("A7_W6_FIVE_MUTANT_GATE_PASS count=5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
