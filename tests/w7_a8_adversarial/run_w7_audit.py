#!/usr/bin/env python3
"""Execute W7 A8 false-PASS attacks against pinned A1 campaign paths."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


A1 = Path("/home/chickgoose/projects/a1")
A1_HEAD = "2a3a3be94be8f12585f484b5b1da2b372f7282d9"
W6_INTEGRATION = "61b7fb5ab298d6b25c23655c92538350fcf7041b"
PINNED_BLOBS = {
    "tests/a4_fovea_a7_common_trace/run_common_trace.py":
        "5d507b4e7a85697fdd44c8c055727d09c9093a85",
    "tests/a4_fovea_a7_common_trace/a4_fovea_a7_common_trace_tb.sv":
        "6427652eb50c76fd1edaf74fa440d1fd4b35d5e2",
    "scripts/run_ganghee_native_benchmark.sh":
        "a91f5d5548fc64bab3ec3e2db6e43be08829a69d",
    "scripts/run_ganghee_cluster2_benchmark.sh":
        "723028c9b3ac1eac8fbb6471b73664b3c48d735d",
    "tests/a7_weighted_fovea_ddr/mutation_gate.py":
        "50ee3fa814935be402e841a9060b0b1970929c48",
    "scripts/run_a7_weighted_fovea_ddr_qualification.sh":
        "b091a79b6e9511b291ac665004d604b94c78763e",
}


class AuditError(RuntimeError):
    pass


def execute(command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
            timeout: int = 360) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, check=False)


def git(*args: str) -> str:
    result = execute(["git", "-C", str(A1), *args], cwd=A1)
    if result.returncode:
        raise AuditError(result.stdout)
    return result.stdout.strip()


def check_pins() -> None:
    if git("rev-parse", "HEAD^{commit}") != A1_HEAD:
        raise AuditError("A1 HEAD changed from the W7 audit pin")
    for path, expected in PINNED_BLOBS.items():
        actual = git("rev-parse", f"HEAD:{path}")
        if actual != expected:
            raise AuditError(f"A1 blob changed: {path} got={actual}")


def load_runner():
    path = A1 / "tests/a4_fovea_a7_common_trace/run_common_trace.py"
    spec = importlib.util.spec_from_file_location("w7_pinned_common_runner", path)
    if spec is None or spec.loader is None:
        raise AuditError("cannot import pinned common trace runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mutate_csv(path: Path, mutation: str) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if mutation == "duplicate":
        rows.append(dict(rows[0]))
    elif mutation == "timing":
        rows[0]["delivery_cycle"] = str(int(rows[0]["delivery_cycle"]) + 1)
    elif mutation == "swapped_address":
        rows[0]["logical_source"] = str((int(rows[0]["logical_source"]) + 1) % 16)
    else:
        raise AuditError(f"unknown CSV mutation {mutation}")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_mutant(module, baseline: Path, mutation: str) -> bool:
    root = baseline.parent / f"mutant-{mutation}"
    shutil.copytree(baseline, root)
    run_root = root / "runs/core_simultaneous_identity"
    events = run_root / "trace.events.csv"
    summary = run_root / "trace.csv"
    log = run_root / "run.log"
    if mutation in {"duplicate", "timing", "swapped_address"}:
        mutate_csv(events, mutation)
    elif mutation == "duplicate_log":
        lines = log.read_text(encoding="utf-8").splitlines()
        marker = next(line for line in lines
                      if line.startswith("A4_FOVEA_A7_COMMON_TRACE_PASS"))
        log.write_text(log.read_text(encoding="utf-8") + marker + "\n",
                       encoding="utf-8")
    else:
        raise AuditError(f"unknown result mutation {mutation}")
    index = json.loads((root / "traces/generation-index.json").read_text())
    metadata = next(row for row in index["runs"]
                    if row["run"]["name"] == "core_simultaneous_identity")
    trace = root / "traces" / metadata["trace_file"]
    first = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    try:
        module.validate_result(metadata, events, summary, log,
                               first["occurrence_cycle"])
    except module.RunError:
        return True
    return False


def fake_xrun_escape(temp: Path, script: str, cluster2: bool) -> tuple[int, str]:
    tool_dir = temp / ("fake-cluster2-bin" if cluster2 else "fake-native-bin")
    tool_dir.mkdir()
    fake = tool_dir / "xrun"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    rtl = temp / ("cluster2.sv" if cluster2 else "fovea.sv")
    rtl.write_text("module audit_dummy; endmodule\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(tool_dir) + os.pathsep + env.get("PATH", "")
    env["AER_CLEAN_OUT"] = str(temp / "native-output")
    if cluster2:
        env["AER_GANGHEE_CLUSTER2_TOP"] = "audit_dummy"
        env["AER_GANGHEE_CLUSTER2_RTL"] = str(rtl)
    else:
        env["AER_GANGHEE_TOP"] = "audit_dummy"
        env["AER_GANGHEE_RTL"] = str(rtl)
    result = execute(["bash", str(A1 / script), "basic_single"], cwd=A1,
                     env=env)
    return result.returncode, result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-go", action="store_true")
    args = parser.parse_args(argv)
    check_pins()
    root = Path(tempfile.mkdtemp(prefix="w7-a8-adversarial.", dir="/tmp"))
    findings: list[dict[str, object]] = []
    blockers: list[str] = []
    try:
        owner = execute([
            sys.executable,
            str(Path(__file__).parents[1] /
                "a8_fovea_a7_adversarial_actual/run_actual_owner_mutations.py"),
            "--owner", str(A1), "--commit", W6_INTEGRATION,
        ], cwd=Path(__file__).parents[2])
        owner_ok = (owner.returncode == 0 and
                    "A8_W6_ACTUAL_OWNER_MUTATION_PASS" in owner.stdout)
        findings.append({"attack": "reset_retrigger_stale_timing_continuous_valid",
                         "caught": owner_ok, "rc": owner.returncode,
                         "log_sha256": hashlib.sha256(owner.stdout.encode()).hexdigest()})
        if not owner_ok:
            raise AuditError("actual owner mutation gate failed:\n" + owner.stdout[-4000:])

        baseline = root / "campaign-baseline"
        replay = execute([
            sys.executable,
            str(A1 / "tests/a4_fovea_a7_common_trace/run_common_trace.py"),
            "--suite", "smoke", "--output", str(baseline),
            "--repo", str(A1),
        ], cwd=A1)
        replay_ok = (replay.returncode == 0 and
                     "PASS status=LOCAL_RTL_TRACE_REPLAY_PASS" in replay.stdout and
                     (baseline / "receipt.json").is_file())
        findings.append({"attack": "campaign_baseline", "caught": replay_ok,
                         "rc": replay.returncode,
                         "log_sha256": hashlib.sha256(replay.stdout.encode()).hexdigest()})
        if not replay_ok:
            raise AuditError("campaign baseline failed:\n" + replay.stdout[-4000:])

        module = load_runner()
        for mutation in ("duplicate", "timing", "duplicate_log", "swapped_address"):
            caught = validate_mutant(module, baseline, mutation)
            findings.append({"attack": mutation, "caught": caught})
            if not caught:
                blockers.append(f"campaign_{mutation}_artifact_escape")

        for script, cluster2, label in (
            ("scripts/run_ganghee_native_benchmark.sh", False, "fovea_raw_runner"),
            ("scripts/run_ganghee_cluster2_benchmark.sh", True, "cluster2_raw_runner"),
        ):
            rc, log = fake_xrun_escape(root, script, cluster2)
            caught = rc != 0
            findings.append({"attack": label + "_zero_output_xrun", "caught": caught,
                             "rc": rc,
                             "completion_marker": "benchmark complete" in log})
            if not caught:
                blockers.append(label + "_accepts_rc0_without_result_or_pass_sentinel")

        report = {
            "schema": "w7_a8_adversarial_v1",
            "a1_head": A1_HEAD,
            "w6_integration": W6_INTEGRATION,
            "status": "HOLD" if blockers else "GO",
            "xcelium_available": shutil.which("xrun") is not None,
            "findings": findings,
            "blockers": blockers,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        print(f"W7_A8_ADVERSARIAL_{report['status']} blockers={len(blockers)}")
        if args.require_go and blockers:
            return 3
        return 0
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
