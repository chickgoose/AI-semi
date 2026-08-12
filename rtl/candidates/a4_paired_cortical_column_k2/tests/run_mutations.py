#!/usr/bin/env python3
"""Compile deliberate RTL mutations and require lockstep falsification."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import pathlib
import shutil
import subprocess


HERE = pathlib.Path(__file__).resolve().parent
CANDIDATE = HERE.parent
ROOT = CANDIDATE.parents[2]
RTL = CANDIDATE / "a4_paired_cortical_column_k2.sv"
TB = HERE / "lockstep_tb.sv"
MUTATIONS = {
    "A4_PCCK2_MUTATE_FLAT_WEIGHT": "all_rows",
    "A4_PCCK2_MUTATE_STALL_ADVANCE": "stall",
    "A4_PCCK2_MUTATE_DROP_DEBT": "hotspot",
    "A4_PCCK2_MUTATE_RESET_LIVE": "reset_live",
}


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verilator", required=True)
    parser.add_argument("--vectors", type=pathlib.Path, required=True)
    parser.add_argument("--work-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    tool = shutil.which(args.verilator)
    if tool is None:
        raise SystemExit(f"required Verilator absent: {args.verilator}")
    if args.work_dir.exists():
        raise SystemExit(f"refusing to reuse mutation work directory: {args.work_dir}")
    args.work_dir.mkdir(parents=True)

    def build_mutation(item: tuple[str, str]) -> tuple[str, str, pathlib.Path]:
        mutation, case_name = item
        obj = args.work_dir / mutation.lower()
        build = run([
            tool, "--binary", "--timing", "--assert", "-Wall",
            "-Wno-UNUSEDSIGNAL", "-Wno-UNUSEDPARAM",
            "--top-module", "a4_pcck2_lockstep_tb", f"-D{mutation}",
            "--Mdir", str(obj), "-o", "sim", str(RTL), str(TB),
        ])
        (args.work_dir / f"{mutation}.build.log").write_text(build.stdout)
        if build.returncode or "%Warning" in build.stdout:
            raise RuntimeError(f"mutation build failed/warned: {mutation}")
        return mutation, case_name, obj

    with ThreadPoolExecutor(max_workers=len(MUTATIONS)) as executor:
        built = list(executor.map(build_mutation, MUTATIONS.items()))
    for mutation, case_name, obj in built:
        simulation = run([
            str(obj / "sim"), f"+CASE={case_name}",
            f"+VECTORS={args.vectors / (case_name + '.vectors')}",
        ])
        (args.work_dir / f"{mutation}.run.log").write_text(simulation.stdout)
        if simulation.returncode == 0 or "LOCKSTEP_PASS" in simulation.stdout:
            raise SystemExit(f"mutation escaped falsifier: {mutation}")
        if not any(marker in simulation.stdout for marker in (
            "lockstep mismatch", "blocked count changed", "blocked address changed",
            "Assertion failed",
        )):
            raise SystemExit(f"mutation failed without expected detector: {mutation}")
        print(f"A4_PCCK2_MUTATION_KILLED mutation={mutation} case={case_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
