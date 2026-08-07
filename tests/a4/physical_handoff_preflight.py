#!/usr/bin/env python3
"""Read-only preflight for the immutable A4 physical handoff package.

This script never launches Xcelium, Genus, or Innovus and never edits a common
flow. It validates candidate identity and refuses unsupported stage ordering.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise SystemExit(f"A4_PREFLIGHT_FAIL {message}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file():
        fail(f"missing_file={path}")
    actual = sha256(path)
    if actual != expected:
        fail(f"hash_mismatch path={path} expected={expected} actual={actual}")


def git(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=project, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def verify_source_identity(project: Path, manifest: dict, require_clean: bool) -> None:
    source_commit = manifest["source_identity"]["commit_sha"]
    if git(project, "cat-file", "-e", f"{source_commit}^{{commit}}").returncode:
        fail(f"missing_source_commit={source_commit}")
    if git(project, "merge-base", "--is-ancestor", source_commit, "HEAD").returncode:
        fail(f"head_is_not_descendant_of_source_commit={source_commit}")
    if require_clean and git(project, "status", "--porcelain").stdout.strip():
        fail("worktree_not_clean")

    protected = []
    for profile in manifest["profiles"].values():
        protected.extend(item["path"] for item in profile["rtl_files"])
    for relative in sorted(set(protected)):
        if git(project, "cat-file", "-e", f"{source_commit}:{relative}").returncode == 0:
            if git(project, "diff", "--quiet", source_commit, "--", relative).returncode:
                fail(f"rtl_changed_after_source_commit={relative}")


def verify_filelist(project: Path, profile: dict) -> None:
    filelist = project / profile["synthesis_filelist"]
    require_hash(filelist, profile["synthesis_filelist_sha256"])
    entries = [line.strip() for line in filelist.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    expected = [item["path"] for item in profile["rtl_files"]]
    if entries != expected:
        fail(f"filelist_order profile={profile['candidate_key']} entries={entries}")
    for item in profile["rtl_files"]:
        require_hash(project / item["path"], item["sha256"])
    digest_lines = "".join(
        f"{item['sha256']}  {item['path']}\n" for item in profile["rtl_files"]
    ) + f"{profile['synthesis_filelist_sha256']}  {profile['synthesis_filelist']}\n"
    if hashlib.sha256(digest_lines.encode()).hexdigest() != profile["source_set_sha256"]:
        fail(f"source_set_sha256_mismatch profile={profile['candidate_key']}")


def verify_top(project: Path, profile: dict) -> None:
    sources = "\n".join((project / item["path"]).read_text(encoding="utf-8")
                        for item in profile["rtl_files"])
    top = profile["synthesis_top"]
    match = re.search(rf"\bmodule\s+{re.escape(top)}\s*#\s*\((.*?)\)\s*\(",
                      sources, flags=re.DOTALL)
    if not match:
        fail(f"top_not_found={top}")
    parameter_order = re.findall(r"\bparameter\s+(?:bit|int|integer|logic)?\s*([A-Za-z_]\w*)\s*=",
                                 match.group(1))
    expected = list(profile["parameters"])
    if parameter_order[:len(expected)] != expected:
        fail(f"parameter_order top={top} expected={expected} actual={parameter_order}")


def verify_package(project: Path, manifest: dict, require_clean: bool) -> None:
    verify_source_identity(project, manifest, require_clean)
    require_hash(project / manifest["common_contract"]["path"],
                 manifest["common_contract"]["sha256"])
    require_hash(project / manifest["common_constraints"]["sdc"],
                 manifest["common_constraints"]["sdc_sha256"])
    for profile in manifest["profiles"].values():
        verify_filelist(project, profile)
        verify_top(project, profile)
        capability = profile.get("capability_profile", {})
        if "path" in capability:
            require_hash(project / capability["path"], capability["sha256"])
        xcelium = profile.get("xcelium", {})
        for key in ("tb_filelist", "candidate_filelist", "properties", "trace_manifest"):
            if key in xcelium:
                require_hash(project / xcelium[key], xcelium[f"{key}_sha256"])
    for evidence in manifest["measured_local_evidence"]:
        require_hash(project / evidence["path"], evidence["sha256"])


def explicit_equal(name: str, actual: object, expected: object) -> None:
    if actual is None:
        fail(f"missing_explicit_argument={name}")
    if actual != expected:
        fail(f"wrong_{name} expected={expected} actual={actual}")


def require_sha_argument(name: str, value: str | None) -> None:
    if value is None or not SHA_RE.fullmatch(value):
        fail(f"missing_or_invalid_{name}")


def verify_stage_record(path_text: str | None, expected_sha: str | None, stage: str,
                        profile_name: str, profile: dict, manifest: dict) -> dict:
    if not path_text:
        fail(f"{stage}_pass_record_is_required")
    path = Path(path_text)
    if not path.is_absolute() or not path.is_file():
        fail(f"{stage}_pass_record_must_be_existing_absolute_path")
    require_sha_argument(f"{stage}_pass_record_sha256", expected_sha)
    if sha256(path) != expected_sha:
        fail(f"{stage}_pass_record_sha256_mismatch")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"{stage}_pass_record_invalid_json={error}")
    expected = {
        "schema": manifest["head_stage_record_schema"]["schema"],
        "stage": stage,
        "profile": profile_name,
        "candidate_key": profile["candidate_key"],
        "source_commit": manifest["source_identity"]["commit_sha"],
        "top": profile["synthesis_top"],
        "filelist_sha256": profile["synthesis_filelist_sha256"],
        "status": "PASS",
    }
    for key, value in expected.items():
        if record.get(key) != value:
            fail(f"{stage}_pass_record_wrong_{key} expected={value} actual={record.get(key)}")
    if stage == "xcelium":
        assertions = record.get("assertions_passed")
        if assertions != profile["expected_assertions"]:
            fail("xcelium_pass_record_assertion_set_mismatch")
    if stage == "genus":
        if record.get("check_design_unresolved") != 0 or record.get("empty_modules") != 0:
            fail("genus_pass_record_design_check_failed")
        if not isinstance(record.get("period_ns"), (int, float)) or record["period_ns"] <= 0:
            fail("genus_pass_record_invalid_period")
        require_sha_argument("genus_record_tool_config_sha256", record.get("tool_config_sha256"))
    return record


def verify_physical_inputs(args: argparse.Namespace, project: Path, common: dict) -> None:
    explicit_equal("sdc", args.sdc, common["sdc"])
    explicit_equal("clock_port", args.clock_port, common["clock_port"])
    explicit_equal("reset_port", args.reset_port, common["reset_port"])
    explicit_equal("input_delay_ns", args.input_delay_ns, common["input_delay_ns"])
    explicit_equal("output_delay_ns", args.output_delay_ns, common["output_delay_ns"])
    explicit_equal("clock_uncertainty_ns", args.clock_uncertainty_ns,
                   common["clock_uncertainty_ns"])
    explicit_equal("output_load_pf", args.output_load_pf, common["output_load_pf"])
    if args.period_ns is None or args.period_ns <= 0:
        fail("period_ns_must_be_explicit_and_positive")
    if not args.corner:
        fail("corner_must_be_explicit")
    if not args.library_file:
        fail("library_file_must_be_explicit")
    library = Path(args.library_file)
    if not library.is_absolute() or not library.is_file():
        fail("library_file_must_be_existing_absolute_path")
    if library.name != common["library_basename"]:
        fail(f"wrong_library_basename={library.name}")
    require_sha_argument("library_sha256", args.library_sha256)
    if sha256(library) != args.library_sha256:
        fail("library_sha256_mismatch")
    if not args.tool_config:
        fail("tool_config_must_be_explicit")
    tool_config = Path(args.tool_config)
    if not tool_config.is_absolute() or not tool_config.is_file():
        fail("tool_config_must_be_existing_absolute_path")
    require_sha_argument("tool_config_sha256", args.tool_config_sha256)
    if sha256(tool_config) != args.tool_config_sha256:
        fail("tool_config_sha256_mismatch")
    require_hash(project / args.sdc, common["sdc_sha256"])


def verify_stage(args: argparse.Namespace, project: Path, manifest: dict) -> dict:
    if not args.stage:
        fail("stage_is_required_without_check_package_only")
    profile = manifest["profiles"][args.profile]
    expected_width = profile["parameters"].get("ADDR_WIDTH",
                                               profile["parameters"].get("EVENT_WIDTH"))
    explicit_equal("top", args.top, profile["synthesis_top"])
    explicit_equal("filelist", args.filelist, profile["synthesis_filelist"])
    explicit_equal("num_sources", args.num_sources, profile["parameters"]["NUM_SOURCES"])
    explicit_equal("addr_width", args.addr_width, expected_width)

    if args.profile == "n64":
        fail("n64_blocked_pending_new_immutable_common_tb_qualification")

    if args.stage == "xcelium":
        xcelium = profile["xcelium"]
        explicit_equal("tb_top", args.tb_top, xcelium["top"])
        explicit_equal("tb_filelist", args.tb_filelist, xcelium["tb_filelist"])
        explicit_equal("candidate_filelist", args.candidate_filelist,
                       xcelium["candidate_filelist"])
        explicit_equal("properties", args.properties, xcelium["properties"])
        explicit_equal("trace_manifest", args.trace_manifest, xcelium["trace_manifest"])
        return profile

    if not args.override_local_decision:
        fail("n16_is_hold_flat_use_override_local_decision_after_head_review")
    verify_stage_record(args.xcelium_pass_record, args.xcelium_pass_record_sha256,
                        "xcelium", args.profile, profile, manifest)
    explicit_equal("defines", args.defines, ",".join(profile["defines"]))
    verify_physical_inputs(args, project, manifest["common_constraints"])
    if args.stage == "genus":
        explicit_equal("synthesis_mode", args.synthesis_mode, "genus_screening")
        return profile

    verify_stage_record(args.genus_pass_record, args.genus_pass_record_sha256,
                        "genus", args.profile, profile, manifest)
    if args.synthesis_mode not in ("fixed_netlist", "per_target_resynthesis"):
        fail("innovus_requires_explicit_synthesis_mode")
    if not args.rc_tech_file:
        fail("innovus_requires_absolute_rc_tech_file")
    rc_file = Path(args.rc_tech_file)
    if not rc_file.is_absolute() or not rc_file.is_file():
        fail("rc_tech_file_must_be_existing_absolute_path")
    require_sha_argument("rc_tech_sha256", args.rc_tech_sha256)
    if sha256(rc_file) != args.rc_tech_sha256:
        fail("rc_tech_sha256_mismatch")
    return profile


def main() -> int:
    default_manifest = Path(__file__).resolve().parents[2] / (
        "rtl/candidates/a4_quadtree_fabric/handoff/a4_physical_handoff.json")
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=default_manifest)
    parser.add_argument("--profile", choices=("n16", "n64"), required=True)
    parser.add_argument("--check-package-only", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--stage", choices=("xcelium", "genus", "innovus"))
    parser.add_argument("--top")
    parser.add_argument("--filelist")
    parser.add_argument("--num-sources", type=int)
    parser.add_argument("--addr-width", type=int)
    parser.add_argument("--tb-top")
    parser.add_argument("--tb-filelist")
    parser.add_argument("--candidate-filelist")
    parser.add_argument("--properties")
    parser.add_argument("--trace-manifest")
    parser.add_argument("--clock-port")
    parser.add_argument("--reset-port")
    parser.add_argument("--sdc")
    parser.add_argument("--defines")
    parser.add_argument("--period-ns", type=float)
    parser.add_argument("--input-delay-ns", type=float)
    parser.add_argument("--output-delay-ns", type=float)
    parser.add_argument("--clock-uncertainty-ns", type=float)
    parser.add_argument("--output-load-pf", type=float)
    parser.add_argument("--corner")
    parser.add_argument("--library-file")
    parser.add_argument("--library-sha256")
    parser.add_argument("--tool-config")
    parser.add_argument("--tool-config-sha256")
    parser.add_argument("--rc-tech-file")
    parser.add_argument("--rc-tech-sha256")
    parser.add_argument("--synthesis-mode",
                        choices=("genus_screening", "fixed_netlist", "per_target_resynthesis"))
    parser.add_argument("--xcelium-pass-record")
    parser.add_argument("--xcelium-pass-record-sha256")
    parser.add_argument("--genus-pass-record")
    parser.add_argument("--genus-pass-record-sha256")
    parser.add_argument("--override-local-decision", action="store_true")
    parser.add_argument("--emit-env", action="store_true")
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[2]
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    verify_package(project, manifest, args.require_clean)
    if args.check_package_only:
        print(f"A4_HANDOFF_PACKAGE_PASS profile={args.profile} "
              f"source_commit={manifest['source_identity']['commit_sha']}")
        return 0
    profile = verify_stage(args, project, manifest)
    if args.emit_env:
        print(f"AER_TOP={profile['synthesis_top']}")
        print(f"AER_RTL_FILELIST={profile['synthesis_filelist']}")
        print(f"AER_NUM_SOURCES={profile['parameters']['NUM_SOURCES']}")
        width = profile["parameters"].get("ADDR_WIDTH", profile["parameters"].get("EVENT_WIDTH"))
        print(f"AER_ADDR_WIDTH={width}")
        print(f"AER_DEFINES={','.join(profile['defines'])}")
        if args.period_ns is not None:
            print(f"AER_CLOCK_PERIOD_NS={args.period_ns}")
            print(f"AER_CLOCK_PORT={args.clock_port}")
            print(f"AER_RESET_PORT={args.reset_port}")
            print(f"AER_SDC={args.sdc}")
    print(f"A4_PHYSICAL_PREFLIGHT_PASS profile={args.profile} stage={args.stage} "
          f"top={profile['synthesis_top']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyError as error:
        fail(f"manifest_missing_key={error}")
