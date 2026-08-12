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
DEFAULT_COMMIT = "e9f27e6"
PASS_SENTINEL = "A7_W6_SHA_PINNED_DIRECTED_RTL_PASS"
ALLOWED_COMMITS = {
    "e9f27e6aed302491011a5deb803a7b42a0c712b3",
    "0f49816b48a4cba027d40733a09edb590bfc7a86",
}
EXPECTED_BLOBS = {
    "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv":
        "7064bdc7fcc5bbb4a7ab59c4a90a490bce9052b1",
    "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv":
        "ecde2d6e5d6ee589b808c6413f45f7155eb6adb7",
    "scripts/run_a7_weighted_fovea_ddr_qualification.sh":
        "fa6f6412863affdfac33916e926b9047d5389e15",
    "scripts/run_a7_weighted_fovea_ddr_fault.sh":
        "1193b63da55c94b653cca57d7eda3cad930e16a0",
    "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_fault_tb.sv":
        "27d7b527ffca84efa8be670ac943702bb72fb465",
    "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_stale_no_live_fixture.sv":
        "94b7c676b669467c24f7f94f6dcc5839ed5fea29",
    "tests/a7_weighted_fovea_ddr/contract_check.py":
        "2d7909ad80a4dbb44aaaba1f5affedf6e744e07f",
}
MUTANT_DIAGNOSTICS = {
    "premature_drain": "A8_ACTUAL_PREMATURE_DRAIN_FAIL",
    "plus_latency": "A8_ACTUAL_AVAILABILITY_LATENCY_FAIL",
    "stale_no_live": "A8_ACTUAL_STALE_NO_LIVE_FAIL",
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
    reset_anchor = "    errors = 0;\n    ref_cycle = 0;\n    full_contention_mode = 1'b0;"
    reset_new = (
        "    errors = 0;\n"
        "    ref_cycle = 0;\n"
        "    a8_cycle = 0; a8_admit_count = 0;\n"
        "    a8_available_count = 0; a8_sink_count = 0;\n"
        "    full_contention_mode = 1'b0;"
    )
    replace_once(tb, reset_anchor, reset_new)
    drain_anchor = "      if ((|source_valid) && drain_idle_o) begin"
    drain_guard = (
        "      if ((|source_valid) && drain_idle_o)\n"
        "        $fatal(1, \"A8_ACTUAL_PREMATURE_DRAIN_FAIL cycle=%0d\", ref_cycle);\n"
        + drain_anchor
    )
    replace_once(tb, drain_anchor, drain_guard)
    available_anchor = (
        "        end else if (ref_cycle != accept_cycle[available] + 1) begin"
    )
    available_guard = (
        "        end else if (ref_cycle != accept_cycle[available] + 1) begin\n"
        "          $fatal(1, \"A8_ACTUAL_AVAILABILITY_LATENCY_FAIL cycle=%0d index=%0d\",\n"
        "                 ref_cycle, available);"
    )
    replace_once(tb, available_anchor, available_guard)
    sink_anchor = (
        "        end else if (ref_cycle != accept_cycle[retired] + 2) begin"
    )
    sink_guard = (
        "        end else if (ref_cycle != accept_cycle[retired] + 2) begin\n"
        "          $fatal(1, \"A8_ACTUAL_SINK_LATENCY_FAIL cycle=%0d index=%0d\",\n"
        "                 ref_cycle, retired);"
    )
    replace_once(tb, sink_anchor, sink_guard)


def install_stale_no_live_monitor(repo: Path) -> None:
    tb = repo / "tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_fault_tb.sv"
    anchor = "  initial begin\n    rst_n = 1'b0;"
    monitor = r'''
  // Independent A8 monitor: raw native valid needs a currently live address.
  always @(posedge ref_clk_i) begin
    #1ps;
    if (rst_n && dut.fovea_valid &&
        !source_valid[dut.fovea_addr])
      $fatal(1, "A8_ACTUAL_STALE_NO_LIVE_FAIL addr=%h", dut.fovea_addr);
  end
'''
    replace_once(tb, anchor, monitor + "\n" + anchor)


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
    elif name == "stale_no_live":
        old = "assign endpoint_valid = rst_n & fovea_valid;"
        # Hide the stale/no-live raw result from the endpoint.  The independent
        # monitor must still see and kill the native causality violation.
        new = (
            "assign endpoint_valid = rst_n & fovea_valid & "
            "source_valid[fovea_addr];"
        )
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


def validate_outcome(name: str, rc: int, sentinel: bool,
                     diagnostic_found: bool = True) -> None:
    """Require a real baseline PASS and reject either form of mutant escape."""
    if name == "baseline":
        if rc != 0 or not sentinel:
            raise AuditFailure(
                f"baseline exact qualification did not PASS: rc={rc} sentinel={sentinel}"
            )
    elif rc == 0 or sentinel or not diagnostic_found:
        raise AuditFailure(
            "mutant escaped independent qualification: "
            f"{name} rc={rc} sentinel={sentinel} diagnostic={diagnostic_found}"
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
    if commit not in ALLOWED_COMMITS:
        raise AuditFailure(
            f"owner commit is not pinned to e9f27e6/0f49816: {commit}"
        )
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
        for name in ("baseline", "premature_drain", "plus_latency", "stale_no_live"):
            repo = temp_root / f"repo-{name}"
            materialize(owner, commit, repo)
            install_audit_monitor(repo)
            if name != "baseline":
                mutate(repo, name)
            if name == "stale_no_live":
                install_stale_no_live_monitor(repo)
            output = temp_root / f"out-{name}"
            rc, log = qualify(repo, fixture, output, base_commit)
            log_path = temp_root / f"{name}.log"
            log_path.write_text(log, encoding="utf-8")
            sentinel = PASS_SENTINEL in log
            diagnostic = name == "baseline" or MUTANT_DIAGNOSTICS[name] in log
            digest = hashlib.sha256(log.encode("utf-8")).hexdigest()
            records.append(
                f"{name} rc={rc} pass_sentinel={int(sentinel)} "
                f"independent_diagnostic={int(diagnostic)} log_sha256={digest}"
            )
            validate_outcome(name, rc, sentinel, diagnostic)
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
