#!/usr/bin/env python3
"""Cross-audit the exact A7/A4 W7 follow-up commits in disposable overlays."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


A7 = Path("/home/chickgoose/projects/a7")
A4 = Path("/home/chickgoose/projects/a4")
A7_COMMIT = "02336900b364992223495f07653c54713ff65e67"
A4_COMMIT = "63c4f2a600fe507c13ef0a5112be8638b7012ac1"
A7_BLOBS = {
    "scripts/run_a7_weighted_fovea_ddr_w7_submission.sh":
        "2df4283e95dcb8c61ba20ad781aa28134c79ea7b",
    "tests/a7_weighted_fovea_ddr/submission_contract_check.py":
        "21efd061f2357c7e8c1d8540f8de57417607ddd1",
    "tests/a7_weighted_fovea_ddr/contract_mutation_gate.py":
        "341c3772809478c2dfb8182d6d89c6d825d4609f",
}
A4_BLOBS = {
    "tests/a4_w7_fovea_cluster2_compare/run_w7_compare.py":
        "09f42a8c78921cd669ce09a2e8ba5b1b283b411a",
    "tests/a4_w7_fovea_cluster2_compare/test_w7_compare.py":
        "cea53ee64df24043955834a23d83129f377fe0ea",
}


class CrossAuditError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
        timeout: int = 420) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, check=False)


def git(repo: Path, *args: str) -> str:
    result = run(["git", "-C", str(repo), *args], cwd=repo)
    if result.returncode:
        raise CrossAuditError(result.stdout)
    return result.stdout.strip()


def verify_commit(repo: Path, commit: str, blobs: dict[str, str]) -> None:
    if git(repo, "rev-parse", f"{commit}^{{commit}}") != commit:
        raise CrossAuditError(f"commit resolution mismatch: {commit}")
    for path, expected in blobs.items():
        actual = git(repo, "rev-parse", f"{commit}:{path}")
        if actual != expected:
            raise CrossAuditError(f"blob mismatch {path}: {actual}")


def materialize(source: Path, commit: str, destination: Path) -> None:
    cloned = run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                  str(source), str(destination)], cwd=destination.parent)
    if cloned.returncode:
        raise CrossAuditError("temporary clone failed: " + cloned.stdout)
    checked = run(["git", "checkout", "--quiet", "--detach", commit],
                  cwd=destination)
    if checked.returncode:
        raise CrossAuditError("temporary checkout failed: " + checked.stdout)


def import_a4_runner(repo: Path):
    path = repo / "tests/a4_w7_fovea_cluster2_compare/run_w7_compare.py"
    spec = importlib.util.spec_from_file_location("a8_w7_a4_overlay", path)
    if spec is None or spec.loader is None:
        raise CrossAuditError("cannot import A4 W7 runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_address_rebound_fixture(root: Path, module):
    root.mkdir(parents=True)
    summary = root / "trace.csv"
    events = root / "trace.events.csv"
    candidate = module.Candidate(
        "fovea", "ganghee-native-coordinate-source-projection", "fake_fovea",
        root / "fake.v", None, "AER_CLEAN_GANGHEE_NATIVE",
        "AER_GANGHEE_NATIVE_MODULE", root / "binding.sv", 1,
    )
    metadata = {
        "run": {"name": "address_rebound", "seed": 1, "load": "0.1",
                "stim_cycles": 10},
        "event_count": 1,
    }
    summary_fields = sorted(module.SUMMARY_REQUIRED)
    summary_row = dict.fromkeys(summary_fields, "0")
    summary_row.update({
        "candidate": candidate.report_candidate, "test": "address_rebound",
        "seed": "1", "load_pct": "10", "stim_cycles": "10",
        "generated": "1", "accepted": "1", "delivered": "1", "errors": "0",
        "source_overrun": "0", "measurement_delivered": "1",
        "measurement_cycles": "10", "throughput": "0.100000",
    })
    with summary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields)
        writer.writeheader(); writer.writerow(summary_row)
    event_fields = sorted(module.EVENT_REQUIRED)
    event_row = dict.fromkeys(event_fields, "")
    event_row.update({
        "candidate": candidate.report_candidate, "test": "address_rebound",
        "seed": "1", "load_pct": "10", "tb_only_event_id": "0",
        # The corresponding input occurrence is source 3. The copied result is
        # maliciously rebound to another in-range address.
        "logical_source": "9", "source_count": "16", "occurrence_cycle": "2",
        "accept_cycle": "3", "delivery_cycle": "4",
        "observation_end_cycle": "9", "event_state": "delivered",
    })
    with events.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=event_fields)
        writer.writeheader(); writer.writerow(event_row)
    return metadata, candidate, summary, events


def address_rebound_escapes(root: Path, module) -> bool:
    metadata, candidate, summary, events = write_address_rebound_fixture(root, module)
    try:
        module.validate_outputs(metadata, candidate, summary, events)
    except module.W7Error:
        return False
    return True


def no_output_xrun_is_rejected(root: Path, module, key: str, lanes: int) -> bool:
    fake = root / f"xrun-{key}"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    rtl, binding = root / f"{key}.sv", root / f"{key}-binding.sv"
    rtl.write_text(f"module fake_{key}; endmodule\n", encoding="utf-8")
    binding.write_text("module fake_binding; endmodule\n", encoding="utf-8")
    candidate = module.Candidate(
        key, f"audit-{key}", f"fake_{key}", rtl, None, "AUDIT_DEFINE",
        "AUDIT_MODULE", binding, lanes,
    )
    output = root / f"out-{key}"
    try:
        module.compile_candidate(fake, Path("/home/chickgoose/projects/a1"),
                                 output, candidate)
    except module.W7Error as exc:
        return "did not create elaborate.log" in str(exc)
    return False


def run_a7_owner(root: Path, repo: Path) -> tuple[bool, int]:
    output = root / "a7-owner"
    env = os.environ.copy()
    env["A7_W7_OUT"] = str(output)
    result = run(["bash", "scripts/run_a7_weighted_fovea_ddr_w7_submission.sh"],
                 cwd=repo, env=env)
    markers = (
        "A7_W7_N16_BITMAP_EXHAUSTIVE_PASS bitmaps=65536 nonempty=65535 accepted=65535 retired=65535",
        "A7_W6_FIVE_MUTANT_GATE_PASS count=5",
        "A7_W6_STALE_NO_LIVE_EXPECTED_FAIL_PASS",
        "A7_W7_DIGITAL_SUBMISSION_PASS",
        "A7_W7_SUBMISSION_RUN_PASS scope=digital-always-ready physical_status=HOLD",
    )
    return result.returncode == 0 and all(marker in result.stdout for marker in markers), result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-owner", action="store_true",
                        help="run overlay attacks only; not a full qualification")
    parser.add_argument("--require-go", action="store_true")
    args = parser.parse_args(argv)
    verify_commit(A7, A7_COMMIT, A7_BLOBS)
    verify_commit(A4, A4_COMMIT, A4_BLOBS)
    root = Path(tempfile.mkdtemp(prefix="w7-a8-followup.", dir="/tmp"))
    try:
        a7_snapshot, a4_snapshot = root / "a7-0233690", root / "a4-63c4f2a"
        materialize(A7, A7_COMMIT, a7_snapshot)
        materialize(A4, A4_COMMIT, a4_snapshot)
        module = import_a4_runner(a4_snapshot)
        owner_ok, owner_rc = ((True, -1) if args.skip_owner else
                              run_a7_owner(root, a7_snapshot))
        rebound_escape = address_rebound_escapes(root / "rebound", module)
        fovea_closed = no_output_xrun_is_rejected(root, module, "fovea", 1)
        cluster2_closed = no_output_xrun_is_rejected(root, module, "cluster2", 8)
        blockers = []
        if not owner_ok:
            blockers.append("a7_w7_owner_execution_failed")
        if rebound_escape:
            blockers.append("a4_result_logical_source_not_bound_to_input_occurrence")
        if not fovea_closed:
            blockers.append("a4_fovea_no_output_rc0_escape")
        if not cluster2_closed:
            blockers.append("a4_cluster2_no_output_rc0_escape")
        report = {
            "schema": "w7_a8_followup_cross_audit_v1",
            "a7_commit": A7_COMMIT, "a4_commit": A4_COMMIT,
            "status": "HOLD" if blockers else "GO",
            "a7_owner": {"executed": not args.skip_owner, "pass": owner_ok,
                         "return_code": owner_rc},
            "prior_blockers": {
                "logical_source_rebound": "OPEN" if rebound_escape else "CLOSED",
                "fovea_no_output_rc0": "CLOSED" if fovea_closed else "OPEN",
                "cluster2_no_output_rc0": "CLOSED" if cluster2_closed else "OPEN",
            },
            "blockers": blockers,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        print(f"W7_A8_FOLLOWUP_{report['status']} blockers={len(blockers)}")
        return 3 if args.require_go and blockers else 0
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
