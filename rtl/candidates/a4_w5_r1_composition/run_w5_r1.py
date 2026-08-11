#!/usr/bin/env python3
"""Reproduce the A4 W5 R1 composition test from pinned A7 git objects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


A7_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
A7_OBJECTS = {
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv": {
        "blob": "e9c29a63f05be1b44e8d651cd7de0fb0ef0d70ae",
        "sha256": "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    },
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv": {
        "blob": "01e3d6b05072df7ac6b06e30f4fcdba03ace43e4",
        "sha256": "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    },
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv": {
        "blob": "544f54353a2bad0fc448765766807d39f59d6514",
        "sha256": "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    },
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv": {
        "blob": "51306d854c8ce9bebc89e3126b71982dda123f30",
        "sha256": "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    },
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv": {
        "blob": "77106c061512c03af599939a0fa71a739408f8b1",
        "sha256": "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    },
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv": {
        "blob": "1de1363dee70a722dcd994b517eb6bb73ba452c6",
        "sha256": "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    },
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv": {
        "blob": "03d30c5fefb77360d4f0288147d8aac809fa9616",
        "sha256": "151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748",
    },
}
PASS_RE = re.compile(
    r"A4_W5_R1_COMPOSITION_PASS accepted=51 retired=50 aborted=1 "
    r"continuous=32 initial_gapped=12 all_gapped=17 held=1 post_reset=4 "
    r"endpoint_valid_ns=16 sink_sample_ns=32"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def find_verilator(explicit: str | None) -> Path:
    choices = [explicit, os.environ.get("AER_VERILATOR"), os.environ.get("VERILATOR")]
    for choice in choices:
        if choice:
            path = Path(choice).expanduser().resolve()
            if path.is_file() and os.access(path, os.X_OK):
                return path
            raise RuntimeError(f"configured Verilator is not executable: {path}")
    discovered = shutil.which("verilator")
    if discovered:
        return Path(discovered).resolve()
    fallback = Path("/tmp/a7-sim-bin/verilator")
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return fallback.resolve()
    raise RuntimeError("Verilator unavailable (checked AER_VERILATOR, VERILATOR, PATH, /tmp/a7-sim-bin)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a7-repo", type=Path, default=Path("/home/chickgoose/projects/a7"))
    parser.add_argument("--verilator")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    rtl = root / "rtl/candidates/a4_w5_r1_composition/a4_w5_r1_composition.sv"
    tb = root / "rtl/candidates/a4_w5_r1_composition/tests/a4_w5_r1_composition_tb.sv"
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    repo = args.a7_repo.resolve()
    commit_type = git_bytes(repo, "cat-file", "-t", A7_COMMIT).decode().strip()
    if commit_type != "commit":
        raise RuntimeError("pinned A7 object is not a commit")
    head = git_bytes(repo, "rev-parse", "HEAD").decode().strip()
    porcelain = git_bytes(repo, "status", "--porcelain").decode().splitlines()
    verilator = find_verilator(args.verilator)
    version = run([str(verilator), "--version"])
    if version.returncode != 0:
        raise RuntimeError("Verilator --version failed")

    owned_work = args.work_dir is None
    work = Path(tempfile.mkdtemp(prefix="a4-w5-r1-")) if owned_work else args.work_dir.resolve()
    if not owned_work:
        if work.exists() or work.is_symlink():
            raise RuntimeError(f"work directory must not exist: {work}")
        work.mkdir(parents=True)

    try:
        pinned_files: list[Path] = []
        object_report: dict[str, dict[str, str]] = {}
        for index, (source_path, expected) in enumerate(A7_OBJECTS.items()):
            tree_line = git_bytes(repo, "ls-tree", A7_COMMIT, source_path).decode().strip()
            fields = tree_line.split()
            if len(fields) < 3 or fields[1] != "blob" or fields[2] != expected["blob"]:
                raise RuntimeError(f"A7 tree/blob mismatch for {source_path}: {tree_line}")
            data = git_bytes(repo, "cat-file", "blob", expected["blob"])
            actual_sha = sha256(data)
            if actual_sha != expected["sha256"]:
                raise RuntimeError(f"A7 content hash mismatch for {source_path}")
            materialized = work / f"pinned_{index}_{Path(source_path).name}"
            materialized.write_bytes(data)
            pinned_files.append(materialized)
            object_report[source_path] = {**expected, "materialized_sha256": actual_sha}

        obj_dir = work / "obj"
        command = [
            str(verilator), "--binary", "--timing", "--assert", "-Wall", "-Wno-fatal",
            "-Wno-DECLFILENAME", "--Mdir", str(obj_dir), "--top-module",
            "a4_w5_r1_composition_tb", *map(str, pinned_files), str(rtl), str(tb),
        ]
        compiled = run(command, cwd=root)
        compile_text = compiled.stdout + compiled.stderr
        if compiled.returncode != 0:
            raise RuntimeError(f"Verilator compile failed\n{compile_text}")
        executable = obj_dir / "Va4_w5_r1_composition_tb"
        simulated = run([str(executable)], cwd=root)
        simulation_text = simulated.stdout + simulated.stderr
        if simulated.returncode != 0 or not PASS_RE.search(simulation_text):
            raise RuntimeError(f"simulation failed or PASS marker absent\n{simulation_text}")

        report = {
            "schema": "a4_w5_r1_composition_v1",
            "status": "LOCAL_R1_COMPOSITION_PASS",
            "architecture_contract": {
                "clocking": "strict_phase_related_synchronous_R1",
                "admission": "one_frame_per_ref_posedge_valid_and_ready",
                "qualifier_state_bits_per_endpoint": 1,
                "consumer_boundary": "registered_retire_valid_sampled_pre_NBA_by_sink_on_following_ref_rise",
                "parallel_boundary": "same_registered_availability_and_pre_NBA_sink_boundary",
                "cdc_claim": "none",
                "sink": "always_ready",
                "free_queue": False,
            },
            "counts": {
                "accepted": 51, "retired": 50, "reset_aborted": 1,
                "continuous_valid_changing_address": 32,
                "initial_gapped": 12, "all_gapped": 17,
                "stalled_held_valid": 1, "reset_after_drain": 4,
            },
            "timing_ns": {
                "ddr_native_commit_from_accept": 12,
                "endpoint_registered_valid_from_accept": 16,
                "sequential_sink_sample_from_accept": 32,
            },
            "a7": {
                "repo": str(repo), "pinned_commit": A7_COMMIT,
                "current_head": head, "working_tree_clean": not porcelain,
                "working_tree_entries": porcelain, "objects": object_report,
            },
            "a4_sources": {
                str(rtl.relative_to(root)): sha256(rtl.read_bytes()),
                str(tb.relative_to(root)): sha256(tb.read_bytes()),
            },
            "tool": {"path": str(verilator), "version": version.stdout.strip()},
            "evidence": {
                "command": command, "compile_log_sha256": sha256(compile_text.encode()),
                "simulation_log_sha256": sha256(simulation_text.encode()),
                "pass_marker": PASS_RE.search(simulation_text).group(0),
            },
            "unsupported": [
                "unrelated_clock_CDC", "sink_backpressure", "R_greater_than_1",
                "level_request_one_shot_protocol", "mid_frame_delivery_across_reset",
            ],
        }
        temporary = output.with_name(output.name + ".tmp")
        if temporary.exists() or temporary.is_symlink():
            raise RuntimeError(f"refusing stale temporary result: {temporary}")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, output)
        print(PASS_RE.search(simulation_text).group(0))
        print(f"RESULT {output}")
        return 0
    finally:
        if owned_work:
            shutil.rmtree(work)


if __name__ == "__main__":
    raise SystemExit(main())
