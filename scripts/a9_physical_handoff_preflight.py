#!/usr/bin/env python3
"""Fail-closed verifier for the optional A9 physical handoff.

No EDA tool or workload is launched here.  The script proves that the selected
profile is committed and clean, then verifies externally produced evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = {"NUM_SOURCES": 64, "RETIRE_LANES": 8,
              "ADDR_WIDTH": 16, "SOURCE_WIDTH": 6}
PORT_BITS = {"functional_total": 1344, "functional_inputs": 1096,
             "functional_outputs": 248, "retire_lanes": 8}
BLOCKED_RELEASE_GATE = {
    "disposition": "NOT_ELIGIBLE",
    "blocked_stages": ["XCELIUM", "GENUS", "INNOVUS"],
    "open_findings": ["B2", "B5"],
    "current_a9_xcelium_eligibility_pass_sufficient": False,
    "release_condition":
        "CANONICAL_TRACE_REPLAY_THROUGH_EXACT_REGISTERED_PHYSICAL_BOUNDARY_"
        "PLUS_HARDENED_EVIDENCE_BINDING_PLUS_INDEPENDENT_REREVIEW",
}
PROFILES = {
    "a9_static_n64_timing_diagnostic": {
        "top": "a9_static_n64_timing_top",
        "top_file": "rtl/candidates/a9_distributed_token_fabric/physical/"
                    "a9_static_n64_timing_top.sv",
        "synth_filelist": "rtl/candidates/a9_distributed_token_fabric/physical/"
                          "static_n64_timing.f",
        "common_filelist": "tests/a9/physical/xcelium_common_n16_static.f",
        "common_define": [],
        "implementation": "distributed",
        "capability": "N64_TIMING_DIAGNOSTIC_ONLY",
    },
    "a9_h2_n64_asymmetric_stall_conditional": {
        "top": "a9_h2_n64_asymmetric_stall_top",
        "top_file": "rtl/candidates/a9_distributed_token_fabric/physical/"
                    "a9_h2_n64_asymmetric_stall_top.sv",
        "synth_filelist": "rtl/candidates/a9_distributed_token_fabric/physical/"
                          "h2_n64_asymmetric_stall.f",
        "common_filelist": "tests/a9/physical/xcelium_common_n16_h2.f",
        "common_define": ["A9_NEIGHBOR_HANDOFF"],
        "implementation": "diffusive",
        "capability": "PERSISTENT_ASYMMETRIC_STALL_WITH_IDLE_PARTNER_ONLY",
    },
}


class Blocked(RuntimeError):
    pass


def run_git(*args: str, binary: bool = False) -> bytes | str:
    try:
        value = subprocess.run(["git", *args], cwd=ROOT, check=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Blocked(f"git {' '.join(args)} failed") from exc
    return value if binary else value.decode("utf-8", errors="strict")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    try:
        return sha_bytes(path.read_bytes())
    except OSError as exc:
        raise Blocked(f"cannot read {path}: {exc}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Blocked(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Blocked(f"JSON object required: {path}")
    return value


def need(value: Any, expected: Any, label: str) -> None:
    if value != expected:
        raise Blocked(f"{label}: expected {expected!r}, got {value!r}")


def keys(value: dict[str, Any], required: list[str], label: str) -> None:
    missing = [key for key in required if key not in value]
    if missing:
        raise Blocked(f"{label}: missing {', '.join(missing)}")


def repo_path(text: str) -> Path:
    path = (ROOT / text).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise Blocked(f"repository path escapes root: {text}") from exc
    return path


def ordered_filelist(path_text: str) -> list[str]:
    path = repo_path(path_text)
    try:
        return [line.strip() for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    except OSError as exc:
        raise Blocked(f"cannot read filelist {path}: {exc}") from exc


def require_clean_tracked(manifest_path: Path) -> None:
    status = run_git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise Blocked("worktree contains dirty/untracked paths; evidence disabled")
    relative = manifest_path.relative_to(ROOT).as_posix()
    tracked = run_git("ls-files", "--error-unmatch", "--", relative).strip()
    need(tracked, relative, "manifest tracking")
    head_blob = run_git("show", f"HEAD:{relative}", binary=True)
    need(sha_bytes(head_blob), digest(manifest_path), "manifest must match HEAD")


def verify_lock(entry: dict[str, Any], commit: str) -> None:
    keys(entry, ["path", "sha256"], "file lock")
    path_text = entry["path"]
    path = repo_path(path_text)
    if not path.is_file() or path.is_symlink():
        raise Blocked(f"locked regular file missing: {path_text}")
    need(digest(path), entry["sha256"], f"working SHA256 {path_text}")
    committed = run_git("show", f"{commit}:{path_text}", binary=True)
    need(sha_bytes(committed), entry["sha256"],
         f"committed SHA256 {commit}:{path_text}")


def verify_package(path: Path, manifest: dict[str, Any]) -> str:
    keys(manifest, ["schema", "candidate", "release_gate", "package_commit", "rtl",
                    "contract", "synthesis", "xcelium", "locked_files"],
         "manifest")
    need(manifest["schema"], "a9-optional-physical-handoff-v2", "schema")
    candidate = manifest["candidate"]
    keys(candidate, ["key", "status", "capability"], "candidate")
    if candidate["key"] not in PROFILES:
        raise Blocked(f"unknown candidate key: {candidate['key']}")
    profile = PROFILES[candidate["key"]]
    need(candidate["status"], "DIAGNOSTIC_ONLY_NOT_N16_SHORTLIST", "status")
    need(candidate["capability"], profile["capability"], "capability")
    need(manifest["release_gate"], BLOCKED_RELEASE_GATE, "release gate")
    need(manifest["rtl"]["top"], profile["top"], "top")
    need(manifest["rtl"]["top_file"], profile["top_file"], "top file")
    need(manifest["contract"]["parameters"], PARAMETERS, "parameters")
    need(manifest["contract"]["port_bits"], PORT_BITS, "port bits")
    need(manifest["contract"]["boundary"],
         "PHASE4_REGISTERED_INGRESS_READY_EGRESS_V1", "register boundary")
    need(manifest["contract"]["normalization"], "WIRE_ONLY_PACK_UNPACK",
         "normalization")
    need(manifest["contract"]["reset"],
         {"port": "rst_ni", "active": "low", "assertion": "asynchronous",
          "deassertion_contract": "synchronous_to_clk_i"}, "reset")
    need(manifest["contract"]["clock"],
         {"port": "clk_i", "edge": "rising", "period_ns": 5.0,
          "uncertainty_ns": 0.1}, "clock")
    need(manifest["contract"]["io"],
         {"input_delay_ns": 0.25, "output_delay_ns": 0.25,
          "output_load_pf": 0.01}, "IO/load")

    synth = manifest["synthesis"]
    need(synth["filelist"], profile["synth_filelist"], "synthesis filelist")
    actual_synth = ordered_filelist(synth["filelist"])
    need(actual_synth, synth["ordered_sources"], "synthesis source order")
    need(actual_synth[-1], profile["top_file"], "synthesis top position")
    expected_defines = (["A9_YOSYS", "A9_PHASE4_DIFFUSIVE"]
                        if profile["implementation"] == "diffusive"
                        else ["A9_YOSYS"])
    need(synth["defines"], expected_defines, "synthesis defines")

    common = manifest["xcelium"]["common_n16_gate"]
    need(common["filelist"], profile["common_filelist"], "common filelist")
    actual_common = ordered_filelist(common["filelist"])
    need(actual_common, common["ordered_sources"], "common source order")
    need(common["defines"], profile["common_define"], "common defines")
    need(common["implementation"], profile["implementation"],
         "common implementation")
    need(common["top"], "aer_clean_tb", "common top")
    need(common["parameters"],
         {"NUM_SOURCES": 16, "RETIRE_LANES": 4, "ADDR_WIDTH": 16},
         "common parameters")
    need(common["trace_manifest"],
         "benchmarks/clean_slate_aer/manifest.neutrality-n16.json",
         "common trace manifest")
    trace_manifest = load_json(repo_path(common["trace_manifest"]))
    need(len(trace_manifest.get("runs", [])), 46, "frozen common run count")
    need(common["expected_runs"], [item["name"] for item in trace_manifest["runs"]],
         "frozen common run names")

    package_commit = manifest["package_commit"]
    run_git("cat-file", "-e", f"{package_commit}^{{commit}}")
    locks = manifest["locked_files"]
    lock_paths = [entry["path"] for entry in locks]
    if len(lock_paths) != len(set(lock_paths)):
        raise Blocked("duplicate locked path")
    required = set(actual_synth + actual_common + [
        synth["filelist"], common["filelist"], common["trace_manifest"],
        "benchmarks/clean_slate_aer/generate_trace.py",
        "benchmarks/clean_slate_aer/prepare_sv_trace.py",
        "scripts/run_a9_benchmark.sh",
        "scripts/a9_physical_handoff_preflight.py",
        "docs/research/a9_optional_physical_handoff.md",
    ])
    need(set(lock_paths), required, "complete locked-file closure")
    for entry in locks:
        verify_lock(entry, package_commit)
    require_clean_tracked(path)
    return digest(path)


def external_file(base: Path, entry: dict[str, Any]) -> Path:
    keys(entry, ["path", "sha256"], "evidence file")
    path = (base / entry["path"]).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise Blocked(f"evidence path escapes directory: {entry['path']}") from exc
    if not path.is_file() or path.is_symlink():
        raise Blocked(f"evidence regular file missing: {path}")
    need(digest(path), entry["sha256"], f"evidence SHA256 {path}")
    return path


def evidence_dir(path: Path | None) -> Path:
    if path is None:
        raise Blocked("--evidence-dir is required; no run is authorized")
    directory = path.resolve()
    try:
        directory.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Blocked("evidence directory must be outside the repository")
    return directory


def verify_xcelium(directory: Path, manifest: dict[str, Any], mhash: str) -> None:
    record = load_json(directory / "xcelium_eligibility.json")
    keys(record, ["package_commit", "manifest_sha256", "head_approved",
                  "tool", "common_command", "profile_elaboration_command",
                  "common_command_log", "common_logs",
                  "profile_elaboration_log"], "Xcelium record")
    need(record["package_commit"], manifest["package_commit"], "Xcelium package")
    need(record["manifest_sha256"], mhash, "Xcelium manifest")
    need(record["head_approved"], True, "Xcelium head approval")
    need(record["tool"].get("name"), "xcelium", "Xcelium tool")
    if not record["tool"].get("version"):
        raise Blocked("Xcelium version missing")
    common = manifest["xcelium"]["common_n16_gate"]
    need(record["common_command"], common["command"], "common command")
    profile = manifest["xcelium"]["profile_elaboration"]
    need(record["profile_elaboration_command"], profile["command"],
         "profile elaboration command")
    command_log = external_file(directory, record["common_command_log"])
    command_text = command_log.read_text(encoding="utf-8", errors="replace")
    if common["command"] not in command_text:
        raise Blocked("common command is absent from locked command log")
    logs = record["common_logs"]
    need([item["run"] for item in logs], common["expected_runs"],
         "common evidence run order")
    forbidden = re.compile(r"AER_CLEAN_TEST_FAIL|\$fatal|errors=[1-9][0-9]*|\*E,")
    for item in logs:
        keys(item, ["run", "path", "sha256", "metrics", "event_metrics",
                    "prepared_trace", "run_manifest"], "common run evidence")
        path = external_file(directory, item)
        text = path.read_text(encoding="utf-8", errors="replace")
        marker = f"AER_CLEAN_TEST_PASS {item['run']}"
        if marker not in text or forbidden.search(text):
            raise Blocked(f"common log lacks clean PASS: {item['run']}")
        metrics = external_file(directory, item["metrics"])
        external_file(directory, item["event_metrics"])
        external_file(directory, item["prepared_trace"])
        external_file(directory, item["run_manifest"])
        try:
            rows = list(csv.DictReader(metrics.open(newline="")))
        except OSError as exc:
            raise Blocked(f"cannot parse metrics for {item['run']}: {exc}") from exc
        if len(rows) != 1 or rows[0].get("test") != item["run"] or \
                rows[0].get("errors") != "0":
            raise Blocked(f"metrics scoreboard is not clean: {item['run']}")
    elab_log = external_file(directory, record["profile_elaboration_log"])
    text = elab_log.read_text(encoding="utf-8", errors="replace")
    marker = f"A9_PROFILE_ELAB_PASS {manifest['rtl']['top']}"
    if profile["command"] not in text or marker not in text or \
            re.search(r"\*E,|xrun: \*E|FAILED", text):
        raise Blocked("profile elaboration log lacks clean PASS")


def verify_site(directory: Path, manifest: dict[str, Any], mhash: str,
                tool_name: str) -> None:
    freeze = load_json(directory / "site_freeze.json")
    keys(freeze, ["package_commit", "manifest_sha256", "approved_by_head",
                  "constraint_contract", "genus", "innovus"], "site freeze")
    need(freeze["package_commit"], manifest["package_commit"], "site package")
    need(freeze["manifest_sha256"], mhash, "site manifest")
    need(freeze["approved_by_head"], True, "physical head approval")
    need(freeze["constraint_contract"],
         {"clock": manifest["contract"]["clock"],
          "reset": manifest["contract"]["reset"],
          "io": manifest["contract"]["io"]}, "site constraint contract")
    tool = freeze[tool_name]
    keys(tool, ["version", "run_tcl", "constraints", "libraries",
                "pvt_corner"], tool_name)
    if not tool["version"] or not tool["libraries"]:
        raise Blocked(f"{tool_name} version/libraries missing")
    external_file(directory, tool["run_tcl"])
    external_file(directory, tool["constraints"])
    for entry in tool["libraries"]:
        external_file(directory, entry)
    if not tool["pvt_corner"]:
        raise Blocked(f"{tool_name} PVT corner missing")
    if tool_name == "innovus":
        keys(tool, ["rc_corner", "floorplan", "target_utilization",
                    "aspect_ratio", "pin_placement", "power_grid", "cts",
                    "routing", "extraction"], "innovus physical freeze")
        if any(tool[field] in (None, "", {}) for field in
               ("rc_corner", "floorplan", "target_utilization", "aspect_ratio",
                "pin_placement", "power_grid", "cts", "routing", "extraction")):
            raise Blocked("Innovus physical/RC settings are incomplete")


def verify_genus(directory: Path, manifest: dict[str, Any], mhash: str) -> None:
    verify_xcelium(directory, manifest, mhash)
    verify_site(directory, manifest, mhash, "genus")


def verify_innovus(directory: Path, manifest: dict[str, Any], mhash: str) -> None:
    verify_genus(directory, manifest, mhash)
    verify_site(directory, manifest, mhash, "innovus")
    record = load_json(directory / "genus_preflight.json")
    keys(record, ["package_commit", "manifest_sha256", "top", "tool",
                  "exit_code", "check_design", "unresolved_references",
                  "unconstrained_endpoints", "latch_count", "netlist", "sdc",
                  "log"], "Genus record")
    need(record["package_commit"], manifest["package_commit"], "Genus package")
    need(record["manifest_sha256"], mhash, "Genus manifest")
    need(record["top"], manifest["rtl"]["top"], "Genus top")
    need(record["tool"].get("name"), "genus", "Genus tool")
    need(record["exit_code"], 0, "Genus exit")
    need(record["check_design"], "PASS", "Genus check_design")
    need(record["unresolved_references"], 0, "unresolved references")
    need(record["unconstrained_endpoints"], 0, "unconstrained endpoints")
    need(record["latch_count"], 0, "inferred latches")
    log = external_file(directory, record["log"])
    for field in ("netlist", "sdc"):
        external_file(directory, record[field])
    text = log.read_text(encoding="utf-8", errors="replace")
    if "A9_GENUS_PREFLIGHT_PASS" not in text or re.search(r"\*E,|FAILED", text):
        raise Blocked("Genus log lacks clean PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--stage", choices=("package", "xcelium", "genus",
                                             "innovus"), default="package")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    try:
        manifest_path = args.manifest.resolve()
        manifest = load_json(manifest_path)
        mhash = verify_package(manifest_path, manifest)
        if args.stage == "package":
            print(f"A9_PACKAGE_LOCK_PASS {manifest['candidate']['key']}")
            return 0
        raise Blocked(
            "FINAL_HEAD_NOT_ELIGIBLE: B2/B5 remain open; canonical trace must "
            "be replayed through the exact registered physical boundary, "
            "evidence binding hardened, and independent re-review completed"
        )
        # Deliberately unreachable while release_gate is NOT_ELIGIBLE.  Keep
        # the evidence validators as preserved history for a future reviewed
        # manifest/schema revision; do not weaken this gate in place.
        directory = evidence_dir(args.evidence_dir)
        if args.stage == "xcelium":
            verify_xcelium(directory, manifest, mhash)
            print("A9_XCELIUM_ELIGIBILITY_PASS")
        elif args.stage == "genus":
            verify_genus(directory, manifest, mhash)
            print("A9_GENUS_ELIGIBILITY_PASS")
        else:
            verify_innovus(directory, manifest, mhash)
            print("A9_INNOVUS_ELIGIBILITY_PASS")
        return 0
    except (Blocked, KeyError, TypeError) as exc:
        print(f"A9_PREFLIGHT_BLOCKED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
