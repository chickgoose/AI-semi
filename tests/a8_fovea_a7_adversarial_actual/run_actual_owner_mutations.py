#!/usr/bin/env python3
"""Run fail-closed mutations through the exact A7 W6 qualification."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


DEFAULT_OWNER = Path("/home/chickgoose/projects/a7")
DEFAULT_FIXTURE = Path(
    "/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures"
)
DEFAULT_COMMIT = "d3c52f0"
PASS_SENTINEL = "A7_W6_EXACT_CANONICAL_QUALIFICATION_PASS"
EXPECTED_BLOBS = {
    "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv":
        "7064bdc7fcc5bbb4a7ab59c4a90a490bce9052b1",
    "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv":
        "3379f887288a251e7e360a85cae9404dc3696a32",
    "scripts/run_a7_weighted_fovea_ddr_qualification.sh":
        "6fc9dc74ca801d1bc943d7dd36da6b8cb16ea9cc",
}


class AuditFailure(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
        timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout, check=False,
    )


def git(owner: Path, *args: str) -> str:
    result = run(["git", *args], cwd=owner)
    if result.returncode:
        raise AuditFailure(f"git {' '.join(args)} failed:\n{result.stdout}")
    return result.stdout.strip()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise AuditFailure(f"mutation anchor count != 1 in {path}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


AUDIT_MONITOR = r'''
  // A8 audit-only observation state. This lives only in the temporary clone.
  integer a8_cycle;
  integer a8_admit_cycle [0:2047];
  integer a8_admit_count, a8_available_count, a8_sink_count;

  // A real always_ff sink sees the producer values from before this edge.
  always @(posedge ref_clk_i) begin
    a8_cycle = a8_cycle + 1;
    if (rst_n && |source_ready) begin
      a8_admit_cycle[a8_admit_count] = a8_cycle;
      a8_admit_count = a8_admit_count + 1;
    end
    if (rst_n && retire_valid_o) begin
      if (a8_sink_count >= a8_admit_count ||
          a8_cycle - a8_admit_cycle[a8_sink_count] != 2)
        $fatal(1, "A8_ACTUAL_SINK_LATENCY_FAIL cycle=%0d index=%0d",
               a8_cycle, a8_sink_count);
      a8_sink_count = a8_sink_count + 1;
    end
    #1ps;
    if (rst_n && retire_valid_o) begin
      if (a8_available_count >= a8_admit_count ||
          a8_cycle - a8_admit_cycle[a8_available_count] != 1)
        $fatal(1, "A8_ACTUAL_AVAILABILITY_LATENCY_FAIL cycle=%0d index=%0d",
               a8_cycle, a8_available_count);
      a8_available_count = a8_available_count + 1;
    end
    if (rst_n && drain_idle_o &&
        ((|source_valid) || (|source_ready) || retire_valid_o ||
         (|dut.fovea_req) || dut.fovea_valid ||
         !dut.endpoint_drain_idle || dut.protocol_fault_o))
      $fatal(1, "A8_ACTUAL_PREMATURE_DRAIN_FAIL cycle=%0d", a8_cycle);
  end
'''


def install_audit_monitor(repo: Path) -> None:
    tb = repo / "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv"
    anchor = "  initial begin\n    rst_n = 1'b0;"
    replace_once(tb, anchor, AUDIT_MONITOR + "\n" + anchor)
    reset_anchor = "    errors = 0;\n    full_contention_mode = 1'b0;"
    reset_new = (
        "    errors = 0;\n"
        "    a8_cycle = 0; a8_admit_count = 0;\n"
        "    a8_available_count = 0; a8_sink_count = 0;\n"
        "    full_contention_mode = 1'b0;"
    )
    replace_once(tb, reset_anchor, reset_new)


def mutate(repo: Path, name: str) -> None:
    wrapper = repo / "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv"
    observer = repo / "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv"
    if name == "premature_drain":
        old = (
            "  assign drain_idle_o = rst_n & endpoint_ready & endpoint_drain_idle &\n"
            "                        ~(|source_valid) & ~(|fovea_req) & ~fovea_valid &\n"
            "                        ~(|source_ready) & ~retire_valid_o &\n"
            "                        ~protocol_fault_o;"
        )
        # Preserve every token checked by the owner's static contract while
        # making live source traffic incorrectly report idle.
        new = old + "\n  assign drain_idle_o = rst_n & endpoint_ready;"
        # Two continuous assignments would be a compile error, not the intended
        # behavioral mutant. Replace the original LHS with a dead audit wire.
        new = new.replace("assign drain_idle_o = rst_n & endpoint_ready &",                     "wire a8_original_drain = rst_n & endpoint_ready &", 1)
        replace_once(wrapper, old, new)
    elif name == "plus_latency":
        old_decl = "  output logic       seen_toggle_o\n);"
        new_decl = old_decl + "\n  logic a8_delay_valid_q;\n  logic [3:0] a8_delay_addr_q;"
        replace_once(observer, old_decl, new_decl)
        old_reset = (
            "      retire_addr_o <= '0;\n"
            "      retire_valid_o <= 1'b0;"
        )
        new_reset = old_reset + "\n      a8_delay_valid_q <= 1'b0;\n      a8_delay_addr_q <= '0;"
        replace_once(observer, old_reset, new_reset)
        old_body = (
            "      retire_valid_o <= raw_toggle_i ^ seen_toggle_o;\n"
            "      seen_toggle_o <= raw_toggle_i;\n"
            "      if (raw_toggle_i ^ seen_toggle_o)\n"
            "        retire_addr_o <= raw_addr_i;"
        )
        new_body = (
            "      retire_valid_o <= a8_delay_valid_q;\n"
            "      a8_delay_valid_q <= raw_toggle_i ^ seen_toggle_o;\n"
            "      seen_toggle_o <= raw_toggle_i;\n"
            "      if (raw_toggle_i ^ seen_toggle_o)\n"
            "        a8_delay_addr_q <= raw_addr_i;\n"
            "      if (a8_delay_valid_q)\n"
            "        retire_addr_o <= a8_delay_addr_q;"
        )
        replace_once(observer, old_body, new_body)
    elif name == "stale_retrigger":
        old = "? (source_valid & ~current_result_mask) : '0;"
        # The required static expression remains present, but the OR restores
        # the just-returned address and permits stale immediate retrigger.
        new = "? ((source_valid & ~current_result_mask) | current_result_mask) : '0;"
        replace_once(wrapper, old, new)
    else:
        raise AuditFailure(f"unknown mutant: {name}")


def materialize(owner: Path, commit: str, destination: Path) -> None:
    result = run(
        ["git", "clone", "--quiet", "--shared", "--no-checkout",
         str(owner), str(destination)], cwd=destination.parent,
    )
    if result.returncode:
        raise AuditFailure(f"temporary clone failed:\n{result.stdout}")
    result = run(["git", "checkout", "--quiet", "--detach", commit], cwd=destination)
    if result.returncode:
        raise AuditFailure(f"temporary checkout failed:\n{result.stdout}")


def qualify(repo: Path, fixture: Path, output: Path, base_commit: str) -> tuple[int, str]:
    env = os.environ.copy()
    env["A7_W6_CANONICAL_DIR"] = str(fixture)
    env["A7_W6_QUAL_OUT"] = str(output)
    # The owner runner exposes this input because a cherry-pick must compare
    # protected paths with its own first parent, not the source branch parent.
    env["A7_W6_BASE_COMMIT"] = base_commit
    result = run(
        ["bash", "scripts/run_a7_weighted_fovea_ddr_qualification.sh"],
        cwd=repo, env=env,
    )
    return result.returncode, result.stdout


def validate_outcome(name: str, rc: int, sentinel: bool) -> None:
    """Require a real baseline PASS and reject either form of mutant escape."""
    if name == "baseline":
        if rc != 0 or not sentinel:
            raise AuditFailure(
                f"baseline exact qualification did not PASS: rc={rc} sentinel={sentinel}"
            )
    elif rc == 0 or sentinel:
        raise AuditFailure(
            f"mutant escaped exact qualification: {name} rc={rc} sentinel={sentinel}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", type=Path,
                        default=Path(os.environ.get("A8_W6_OWNER_REPO", DEFAULT_OWNER)))
    parser.add_argument("--fixture", type=Path,
                        default=Path(os.environ.get("A8_W6_CANONICAL_DIR", DEFAULT_FIXTURE)))
    parser.add_argument("--commit",
                        default=os.environ.get("A8_W6_OWNER_COMMIT", DEFAULT_COMMIT))
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    owner = args.owner.resolve()
    fixture = args.fixture.resolve()
    commit = git(owner, "rev-parse", f"{args.commit}^{{commit}}")
    base_commit = git(owner, "rev-parse", f"{commit}^")
    for path, expected in EXPECTED_BLOBS.items():
        actual = git(owner, "rev-parse", f"{commit}:{path}")
        if actual != expected:
            raise AuditFailure(
                f"owner blob mismatch at {path}: got {actual}, expected {expected}"
            )

    temp_root = Path(tempfile.mkdtemp(prefix="a8-w6-actual-mutants.", dir="/tmp"))
    records: list[str] = []
    try:
        for name in ("baseline", "premature_drain", "plus_latency", "stale_retrigger"):
            repo = temp_root / f"repo-{name}"
            materialize(owner, commit, repo)
            install_audit_monitor(repo)
            if name != "baseline":
                mutate(repo, name)
            output = temp_root / f"out-{name}"
            rc, log = qualify(repo, fixture, output, base_commit)
            log_path = temp_root / f"{name}.log"
            log_path.write_text(log, encoding="utf-8")
            sentinel = PASS_SENTINEL in log
            digest = hashlib.sha256(log.encode("utf-8")).hexdigest()
            records.append(f"{name} rc={rc} pass_sentinel={int(sentinel)} log_sha256={digest}")
            validate_outcome(name, rc, sentinel)
        for record in records:
            print(record)
        print(f"A8_W6_ACTUAL_OWNER_MUTATION_PASS commit={commit} mutants=3")
        if args.keep_temp:
            print(f"A8_W6_ACTUAL_OWNER_TEMP={temp_root}")
            temp_root = Path()
        return 0
    finally:
        if temp_root and temp_root.exists():
            shutil.rmtree(temp_root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditFailure, subprocess.TimeoutExpired) as exc:
        print(f"A8_W6_ACTUAL_OWNER_MUTATION_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
