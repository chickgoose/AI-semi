#!/usr/bin/env python3
"""Fail-closed, candidate-neutral Genus runner for the frozen K2 cohort."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = HERE / "designs.json"
DIAGNOSTIC_REGISTRY = HERE / "diagnostic_designs.json"
DRIVER_TCL = HERE / "genus_driver.tcl"
GOLDEN_REFERENCE = HERE / "golden_reference.json"
RAW_GOLDEN_REFERENCE = HERE / "raw_golden_reference.json"
FUNCTIONAL_LOSS_REFERENCE = HERE / "functional_loss_reference.json"
MAPPED_FUNCTIONAL_TB = HERE / "mapped_functional_tb.sv"
MAPPED_FUNCTIONAL_HOOK = HERE / "run_mapped_functional_xcelium.py"
SERVER_ENV_CONTRACT = ROOT / "physical/k2_w2_server_env/contract.json"
SERVER_ENV_PREFLIGHT = ROOT / "physical/k2_w2_server_env/preflight.py"
BOUNDARY_REGISTRY = HERE.parent / "k2_w2_boundaries.json"
FAIR_TOP_REGISTRY = HERE.parent / "k2_w2_tops" / "designs.json"
SAFE_ATTEMPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
CELL_RE = re.compile(r"\bcell\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)")
MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.MULTILINE)
BLACKBOX_RE = re.compile(
    r"(?:\(\*[^*]*\bblackbox\b[^*]*\*\)|\bblackbox\b)", re.IGNORECASE)
INSTANCE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+"
    r"(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
SCAN_RE = re.compile(r"(?:^|_)(?:SDFF|SCAN)", re.IGNORECASE)
KEYWORDS = {"module", "if", "for", "case", "assign", "always", "function", "task"}


class FlowError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise FlowError(f"input is not a regular single-link file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            raise FlowError(f"input changed while read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def write_exclusive(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_stable(source: Path, destination: Path, mode: int = 0o444) -> str:
    payload = stable_read(source)
    write_exclusive(destination, payload, mode)
    return sha256_bytes(payload)


def verify_server_environment_receipt(
        receipt_path: Path, genus: Path, setup_liberty: Path,
        hold_liberty: Path, macro_lef: Path, shared_qrc: Path
        ) -> tuple[bytes, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "k2_w2_server_env_preflight", SERVER_ENV_PREFLIGHT)
    if spec is None or spec.loader is None:
        raise FlowError("server-environment verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        contract_payload = stable_read(SERVER_ENV_CONTRACT)
        contract = json.loads(contract_payload)
        module.validate_contract(contract)
        payload = stable_read(receipt_path.resolve(strict=True))
        document = json.loads(payload)
        contract_sha = sha256_bytes(contract_payload)
        module.verify_go_document(document, contract_sha)
    except (OSError, ValueError, json.JSONDecodeError,
            module.PreflightError) as error:
        raise FlowError(f"server environment receipt is not GO: {error}") from error
    gates = document["gates"]
    tool = gates["tool_executables"]["evidence"]["genus"]
    xrun = gates["tool_executables"]["evidence"]["xrun"]
    expected_paths = {
        "setup_liberty": setup_liberty.resolve(strict=True),
        "hold_liberty": hold_liberty.resolve(strict=True),
        "macro_lef": macro_lef.resolve(strict=True),
        "setup_qrc": shared_qrc.resolve(strict=True),
    }
    identities = gates["technology_files"]["evidence"]
    supplied_tool = tool_identity(genus)
    if (tool.get("path") != supplied_tool["resolved_path"] or
            tool.get("sha256") != supplied_tool["sha256"] or
            tool.get("parsed_version") not in supplied_tool["version_output"]):
        raise FlowError("Genus executable is not the proven server executable")
    for role, path in expected_paths.items():
        row = identities.get(role, {})
        if (row.get("path") != str(path) or
                row.get("sha256") != sha256_bytes(stable_read(path))):
            raise FlowError(f"{role} is not the proven server technology input")
    hold_qrc = identities.get("hold_qrc", {})
    if (hold_qrc.get("path") != str(expected_paths["setup_qrc"]) or
            hold_qrc.get("sha256") != identities["setup_qrc"]["sha256"]):
        raise FlowError("server receipt does not prove one shared setup/hold QRC")
    return payload, {
        "path": str(receipt_path.resolve(strict=True)),
        "sha256": sha256_bytes(payload),
        "contract_sha256": contract_sha,
        "environment_binding_sha256": document["environment_binding_sha256"],
        "xrun": {
            "resolved_path": xrun["path"],
            "sha256": xrun["sha256"],
            "parsed_version": xrun["parsed_version"],
        },
    }


def git(root: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=not binary, check=False,
    )
    if result.returncode:
        error = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise FlowError(f"git {' '.join(args)} failed: {error.strip()}")
    return result.stdout


def validate_final_registry_document(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema") != "k2_w2_genus_final_tech_staged_registry_v3":
        raise FlowError("design registry schema mismatch")
    goal_order = ["fovea_a7", "a2_p6", "a3_p6"]
    if (document.get("goal_order") != goal_order or
            set(document.get("design_expectations", {})) != set(goal_order)):
        raise FlowError("final registry must contain three staged compositions")
    if document.get("common_constraints", {}).get("clock_gating_insertion") is not True:
        raise FlowError("design registry must use the golden clock-gating assumption")
    if document.get("ranking_policy") != (
            "ONLY_THREE_TECH_STAGED_COMPLETE_COMPOSITIONS_COMPARABLE"):
        raise FlowError("final staged-composition ranking policy mismatch")
    pointer = document.get("staged_manifest", {})
    expected_pointer = {
        "required_schema": "k2_w2_tech_staged_compositions_v1",
        "required_status": "READY_FOR_GENUS_AND_INNOVUS",
        "path": "rtl/technology/physical_staging/physical_staging_manifest.json",
        "sha256": "923c898e883f535547aa6eee309ecc7270e9c431e872667561c1902afc55279b",
        "source_commit": "07f2413f07357fa1ef34c48fc74c32d238873c30",
        "publication_commit": "7f149e043a740c032e2cd22b3ed1d6876b6670ce",
    }
    if document.get("integration_state") != "ready" or pointer != expected_pointer:
        raise FlowError("canonical staged-manifest registry pointer mismatch")
    if document.get("repository_commit") != pointer["source_commit"]:
        raise FlowError("registry source commit differs from staged-manifest pointer")
    authorities = document.get("required_technology_authorities", {})
    if (set(authorities) != {"raw_golden", "buffered_golden", "live_gsclib045", "cells"} or
            authorities.get("raw_golden", {}).get("sha256") !=
            "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3" or
            authorities.get("buffered_golden", {}).get("sha256") !=
            "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f" or
            authorities.get("live_gsclib045", {}).get(
                "dffnsrx1_cell_and_interface_verified") is not True or
            authorities.get("live_gsclib045", {}).get(
                "liberty_timing_arcs_claimed_by_manifest") is not False or
            set(authorities.get("cells", {})) != {
                "TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1"}):
        raise FlowError("canonical staged technology authority mismatch")
    return document


def load_registry_document() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(REGISTRY))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid design registry: {error}") from error
    return validate_final_registry_document(document)


def relative_repo_path(root: Path, value: Any, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise FlowError(f"{label} must be a nonempty repository-relative path")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise FlowError(f"{label} escapes repository root") from error
    return value, candidate


def parse_ansi_ports(payload: bytes, top: str) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="strict")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    match = re.search(
        rf"\bmodule\s+{re.escape(top)}\s*\((.*?)\)\s*;", text, re.DOTALL)
    if match is None:
        raise FlowError(f"staged top lacks canonical ANSI module boundary: {top}")
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^(input|output)\s+(?:(?:wire|logic|reg)\s+)?"
        r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?"
        r"([A-Za-z_][A-Za-z0-9_$]*)$")
    for declaration in match.group(1).split(","):
        normalized = " ".join(declaration.split())
        port = pattern.fullmatch(normalized)
        if port is None:
            raise FlowError(f"unsupported/ambiguous staged top port: {normalized}")
        direction, msb, lsb, name = port.groups()
        width = 1 if msb is None else abs(int(msb) - int(lsb)) + 1
        rows.append({"direction": direction, "name": name, "width": width})
    if len({row["name"] for row in rows}) != len(rows):
        raise FlowError(f"duplicate staged top port: {top}")
    return rows


def parse_staged_filelist(payload: bytes, label: str) -> tuple[list[str], list[str], list[str]]:
    try:
        lines = [line.strip() for line in payload.decode("utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
    except UnicodeDecodeError as error:
        raise FlowError(f"{label} filelist is not UTF-8") from error
    sources: list[str] = []
    defines: list[str] = []
    include_dirs: list[str] = []
    for line in lines:
        if line.startswith("+define+"):
            define = line.removeprefix("+define+")
            if not define or "+" in define:
                raise FlowError(f"{label} filelist has an ambiguous define")
            defines.append(define)
        elif line.startswith("+incdir+"):
            include_dir = line.removeprefix("+incdir+")
            if not include_dir or "+" in include_dir:
                raise FlowError(f"{label} filelist has an ambiguous include directory")
            include_dirs.append(include_dir)
        elif line.startswith("+") or line.startswith("-"):
            raise FlowError(f"{label} filelist has an unsupported directive")
        else:
            sources.append(line)
    if (not sources or len(sources) != len(set(sources)) or
            defines != ["W2_P6_TECH_GSCLIB045"] or
            include_dirs != ["rtl/technology/p6"]):
        raise FlowError(f"{label} gsclib045 filelist contract mismatch")
    return sources, defines, include_dirs


def staged_port_rows(manifest: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in manifest.get("common_ports", []):
        if not isinstance(value, dict):
            raise FlowError("shared tech-staged common port is not an object")
        row = {name: value.get(name) for name in ("direction", "name")}
        if "width" in value:
            row["width"] = value["width"]
        elif set(value.get("width_by_design", {})) == {"fovea_a7", "a2_p6", "a3_p6"}:
            row["width"] = value["width_by_design"][key]
        else:
            raise FlowError("shared tech-staged common port width is incomplete")
        if (row["direction"] not in {"input", "output"} or
                not isinstance(row["name"], str) or not row["name"] or
                not isinstance(row["width"], int) or isinstance(row["width"], bool) or
                row["width"] <= 0):
            raise FlowError("shared tech-staged common port is invalid")
        rows.append(row)
    return rows


def validate_staged_manifest(root: Path, registry: dict[str, Any],
                             manifest: dict[str, Any]) -> dict[str, Any]:
    expected_order = registry["goal_order"]
    pointer = registry["staged_manifest"]
    if set(manifest) != {
            "schema", "status", "repository_commit", "goal_order", "common_ports",
            "technology_authorities", "constraint_templates", "designs",
            "source_hashes", "test_policy", "consumer_contract"}:
        raise FlowError("shared tech-staged manifest fields are not canonical")
    if (manifest.get("schema") != pointer["required_schema"] or
            manifest.get("status") != pointer["required_status"] or
            manifest.get("goal_order") != expected_order or
            list(manifest.get("designs", {})) != expected_order):
        raise FlowError("shared tech-staged manifest schema/status/top order mismatch")
    commit = manifest.get("repository_commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise FlowError("tech-staged manifest repository commit is missing")
    if (commit != registry["staged_manifest"]["source_commit"] or
            manifest.get("technology_authorities") != registry.get(
                "required_technology_authorities")):
        raise FlowError("tech-staged manifest technology authority mismatch")
    expected_templates = {
        "ref_period_ns": 5.0, "sample_period_ns": 5.0,
        "sample_waveform_ns": [1.25, 3.75], "clock_uncertainty_ns": 0.25,
        "input_delay_ns": 0.5, "output_delay_ns": 0.5, "output_load_pf": 0.01,
        "generated_link_clock_required": True, "both_link_edges_required": True,
        "ref_and_sample_are_phase_related": True,
    }
    if manifest.get("constraint_templates") != expected_templates:
        raise FlowError("tech-staged manifest constraint-template mismatch")
    if manifest.get("test_policy") != {
            "acceptance_sample": "posedge_ref_active_region_pre_NBA",
            "pending_hold": "through_charged_posedge",
            "protocol_error_must_equal_zero": True,
            "epoch_accepted_equals_retired": True,
            "cell_models_test_only": True,
            }:
        raise FlowError("tech-staged manifest test-policy mismatch")
    forbidden_tops = set(registry["forbidden_final_tops"])
    forbidden_paths = set(registry["forbidden_final_source_paths"])
    source_hashes = manifest.get("source_hashes")
    if (not isinstance(source_hashes, dict) or not source_hashes or
            any(not isinstance(path, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for path, digest in source_hashes.items())):
        raise FlowError("tech-staged manifest source-hash inventory is invalid")
    for source_name, expected_digest in source_hashes.items():
        name, path = relative_repo_path(root, source_name, "staged source-hash entry")
        if name in forbidden_paths or sha256_bytes(stable_read(path)) != expected_digest:
            raise FlowError(f"staged source-hash inventory mismatch: {name}")
    expected_consumer = {
        "consumers": ["genus", "innovus"],
        "manifest_path": registry["staged_manifest"]["path"],
        "required_schema": pointer["required_schema"],
        "required_status": pointer["required_status"],
        "require_repository_commit": True,
        "require_literal_common_port_signature": True,
        "require_endpoint_path_and_leaf_provenance": True,
        "forbidden_port_aliases": [
            "load_i", "pending_i", "source_ready_o", "protocol_fault_o",
            "link_enable", "link_enable_i", "burst_clk_o", "burst_data_o",
            "p6_clk_o", "p6_data_o",
        ],
    }
    if manifest.get("consumer_contract") != expected_consumer:
        raise FlowError("tech-staged consumer contract mismatch")
    designs: dict[str, Any] = {}
    for key in expected_order:
        row = manifest["designs"][key]
        expectation = registry["design_expectations"][key]
        top = row.get("top")
        filelist_stem = "fovea" if key == "fovea_a7" else key.removesuffix("_p6")
        expected_filelists = {
            "generic": (
                f"rtl/technology/physical_staging/filelists/{filelist_stem}_generic.f"),
            "gsclib045": (
                f"rtl/technology/physical_staging/filelists/{filelist_stem}_gsclib045.f"),
        }
        if (not isinstance(top, str) or not top or top in forbidden_tops or
                top != expectation["staged_top"] or
                set(row) != {
                    "top", "filelists", "port_signature", "endpoint_root",
                    "endpoint_leaf_contract", "whole_top_observed_totals"} or
                row.get("filelists") != expected_filelists or
                row.get("endpoint_root") != {
                    "attribute": "w2_endpoint_root=" + ("r1" if key == "fovea_a7" else "p6"),
                    "stable_prefix": "w2_endpoint_link__" +
                    ("r1" if key == "fovea_a7" else "p6"),
                }):
            raise FlowError(f"forbidden or wrong technology-staged top: {key}")
        leaf = row.get("endpoint_leaf_contract", {})
        if (leaf.get("leaf_counts") != expectation["endpoint_expected_inventory"] or
                leaf.get("path_segment") != row["endpoint_root"]["stable_prefix"] or
                leaf.get("preserved_name_prefixes") != expectation[
                    "endpoint_preserved_name_prefixes"]):
            raise FlowError(f"staged exact endpoint inventory mismatch: {key}")
        if row.get("whole_top_observed_totals") != {
                "status": "PENDING_DEDICATED_GENUS_RUN", "records": []}:
            raise FlowError(f"staged whole-top inventory status mismatch: {key}")
        filelist_name, filelist_path = relative_repo_path(
            root, row.get("filelists", {}).get("gsclib045"), f"{key} filelist")
        filelist_payload = stable_read(filelist_path)
        source_names, filelist_defines, include_dirs = parse_staged_filelist(
            filelist_payload, key)
        top_source = "rtl/technology/physical_staging/" + top + ".sv"
        if top_source not in source_names or top_source in forbidden_paths:
            raise FlowError(f"staged top source missing or generic wrapper substituted: {key}")
        sources = []
        source_payloads: dict[str, bytes] = {}
        for source_name in source_names:
            name, path = relative_repo_path(root, source_name, f"{key} source")
            source_payload = stable_read(path)
            source_payloads[name] = source_payload
            digest = sha256_bytes(source_payload)
            if (name in forbidden_paths or
                    (name in source_hashes and digest != source_hashes[name])):
                raise FlowError(f"staged source SHA/path mismatch: {name}")
            sources.append({"path": name, "sha256": digest})
        include_files: list[dict[str, str]] = []
        for source_name, source_payload in source_payloads.items():
            try:
                includes = re.findall(
                    r'^\s*`include\s+"([A-Za-z0-9_./-]+)"',
                    source_payload.decode("utf-8"), re.MULTILINE)
            except UnicodeDecodeError as error:
                raise FlowError(f"staged source is not UTF-8: {source_name}") from error
            for include in includes:
                matches = []
                for include_dir in include_dirs:
                    candidate_name, candidate = relative_repo_path(
                        root, f"{include_dir}/{include}", f"{key} include")
                    if candidate.is_file():
                        matches.append((candidate_name, candidate))
                if len(matches) != 1:
                    raise FlowError(f"staged include is missing or ambiguous: {include}")
                include_name, include_path = matches[0]
                identity = {"path": include_name,
                            "sha256": sha256_bytes(stable_read(include_path))}
                if identity not in include_files:
                    include_files.append(identity)
        top_payload = stable_read(root / top_source)
        ports = parse_ansi_ports(top_payload, top)
        expected_ports = staged_port_rows(manifest, key)
        registry_ports = (registry["required_common_inputs"] +
                          registry["required_common_outputs"][:1] +
                          expectation["link_outputs"] +
                          registry["required_common_outputs"][1:])
        by_name = lambda values: sorted(values, key=lambda value: value["name"])
        port_signature = [port["name"] if port["width"] == 1 else
                          f"{port['name']}[{port['width'] - 1}:0]"
                          for port in expected_ports]
        if (expected_ports != registry_ports or
                by_name(ports) != by_name(expected_ports) or
                row.get("port_signature") != port_signature):
            raise FlowError(f"staged top boundary mismatch: {key}")
        if sum(port["width"] for port in expectation["link_outputs"]) != \
                expectation["link_bits"]:
            raise FlowError(f"staged link-width contract mismatch: {key}")
        link_names = [port["name"] for port in expectation["link_outputs"]]
        designs[key] = {
            **row,
            "top": top,
            "top_source": top_source,
            "filelist": filelist_name,
            "filelist_sha256": sha256_bytes(filelist_payload),
            "sources": sources,
            "defines": ["SYNTHESIS", *filelist_defines],
            "include_dirs": include_dirs,
            "include_files": include_files,
            "parameters": {},
            "mapped_rx_contract": expectation["mapped_rx_contract"],
            "mapped_posedge_contract": expectation["mapped_posedge_contract"],
            "endpoint_expected_inventory": expectation["endpoint_expected_inventory"],
            "endpoint_link_roots": expectation["endpoint_link_roots"],
            "endpoint_preserved_name_prefixes": expectation[
                "endpoint_preserved_name_prefixes"],
            "no_other_negedge_state_proven": expectation[
                "no_other_negedge_state_proven"],
            "strict_sdc": expectation["strict_sdc"],
            "boundary_cohort": "tech_staged_complete_compositions",
            "source_origin": "tech_staged_repository_exact",
            "clocks": [
                {"name": "ref_clk", "port": "ref_clk_i", "waveform_ns": [0.0, 2.5]},
                {"name": "sample_clk", "port": "sample_clk_i",
                 "waveform_ns": [1.25, 3.75]},
            ],
            "generated_clock": {
                "name": "staged_link_clk", "source_port": "sample_clk_i",
                "target_port": link_names[0], "divide_by": 1,
            },
            "reset": {"port": "rst_n", "active": "low",
                      "asynchronous_assertion": True,
                      "release_contract": "phase_related_drained"},
            "data_inputs": ["source_pending_i"],
            "outputs": [port["name"] for port in
                        registry["required_common_outputs"] + expectation["link_outputs"]],
        }
    return designs


def resolve_staged_registry(root: Path, document: dict[str, Any]) -> dict[str, Any]:
    pointer = document.get("staged_manifest", {})
    if document.get("integration_state") != "ready" or any(
            pointer.get(field) is None for field in (
                "path", "sha256", "source_commit", "publication_commit")):
        raise FlowError(
            "final tech-staged composition manifest is missing; generic/native substitution forbidden")
    timing_identities: dict[str, Any] = {}
    for key, expectation in document["design_expectations"].items():
        timing = expectation["strict_sdc"]
        name, path = relative_repo_path(root, timing["path"], f"{key} strict SDC")
        payload = stable_read(path)
        if sha256_bytes(payload) != timing["sha256"]:
            raise FlowError(f"{key} strict SDC SHA mismatch")
        timing_identities[key] = dict(timing)
    mmmc = document.get("mmmc_template", {})
    mmmc_name, mmmc_path = relative_repo_path(
        root, mmmc.get("path"), "shared-QRC MMMC template")
    mmmc_payload = stable_read(mmmc_path)
    if (sha256_bytes(mmmc_payload) != mmmc.get("sha256") or
            mmmc.get("qrc_policy") != "shared_single_gpdk045_typical_rc_disclosed"):
        raise FlowError("shared-QRC MMMC template/policy mismatch")
    manifest_name, manifest_path = relative_repo_path(
        root, pointer["path"], "staged manifest")
    payload = stable_read(manifest_path)
    if sha256_bytes(payload) != pointer["sha256"]:
        raise FlowError("tech-staged manifest SHA mismatch")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid tech-staged manifest: {error}") from error
    if manifest.get("repository_commit") != pointer["source_commit"]:
        raise FlowError("tech-staged manifest commit pointer mismatch")
    runtime = dict(document)
    runtime["repository_commit"] = pointer["source_commit"]
    runtime["designs"] = validate_staged_manifest(root, document, manifest)
    runtime["staged_manifest_identity"] = {
        "path": manifest_name, "sha256": pointer["sha256"],
        "source_commit": pointer["source_commit"],
        "publication_commit": pointer["publication_commit"],
    }
    runtime["technology_authority_identities"] = dict(
        document["required_technology_authorities"])
    runtime["timing_template_identities"] = timing_identities
    runtime["mmmc_template_identity"] = {**mmmc, "path": mmmc_name}
    return runtime


def load_registry(root: Path = ROOT) -> dict[str, Any]:
    return resolve_staged_registry(root, load_registry_document())


def load_golden_reference() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(GOLDEN_REFERENCE))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid golden reference manifest: {error}") from error
    if document.get("schema") != "k2_w2_ganghee_genus_golden_v1":
        raise FlowError("golden reference manifest schema mismatch")
    if document.get("archive_sha256") != (
            "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f"):
        raise FlowError("golden archive SHA is not the authoritative value")
    if document.get("genus_version") != "23.14-s090_1":
        raise FlowError("golden Genus version mismatch")
    if document.get("clock_gating_insertion") is not True:
        raise FlowError("golden clock-gating assumption mismatch")
    if document.get("cohort") != "buffered_ready_valid_reference":
        raise FlowError("buffered golden cohort mismatch")
    if document.get("library_path") != (
            "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/"
            "slow_vdd1v0_basicCells.lib"):
        raise FlowError("buffered golden exact library setting mismatch")
    anchors = document.get("anchors")
    if not isinstance(anchors, dict) or len(anchors) != 25:
        raise FlowError("golden anchor set must contain exactly 25 members")
    return document


def load_raw_golden_reference() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(RAW_GOLDEN_REFERENCE))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid raw golden reference manifest: {error}") from error
    if document.get("schema") != "k2_w2_ganghee_raw_genus_golden_v1":
        raise FlowError("raw golden reference manifest schema mismatch")
    if document.get("archive_sha256") != (
            "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3"):
        raise FlowError("raw golden archive SHA is not the authoritative value")
    if (document.get("cohort") != "raw_native_core_reference" or
            document.get("genus_version") != "23.14-s090_1" or
            document.get("clock_gating_insertion") is not True or
            document.get("library_basename") != "slow_vdd1v0_basicCells.lib"):
        raise FlowError("raw golden tool/library/cohort settings mismatch")
    if set(document.get("runs", {})) != {"fovea_raw", "cluster2_raw"}:
        raise FlowError("raw golden run set mismatch")
    anchors = document.get("anchors")
    if not isinstance(anchors, dict) or len(anchors) != 22:
        raise FlowError("raw golden anchor set must contain exactly 22 members")
    return document


def load_functional_loss_reference() -> dict[str, Any]:
    try:
        document = json.loads(stable_read(FUNCTIONAL_LOSS_REFERENCE))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid functional loss reference manifest: {error}") from error
    if document.get("schema") != "k2_w2_functional_loss_reference_v1":
        raise FlowError("functional loss reference schema mismatch")
    if (document.get("archive_sha256") !=
            "22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39" or
            document.get("qualification") != "NON_OFFICIAL_WORKSPACE_DIFF" or
            document.get("claim_scope") !=
            "FULL50_GENERATED_ACCEPTED_DELIVERED_OVERRUN_ONLY_NOT_PPA"):
        raise FlowError("functional loss authority/scope mismatch")
    if document.get("excluded_artifacts") != ["eval-driver-final.log"]:
        raise FlowError("stale outer-driver exclusion mismatch")
    if set(document.get("candidates", {})) != {"fovea", "cluster2"}:
        raise FlowError("functional loss candidate set mismatch")
    if len(document.get("anchors", {})) != 10:
        raise FlowError("functional loss anchor set must contain exactly 10 members")
    return document


def verify_flow_tree(root: Path, registry: dict[str, Any]) -> dict[str, str]:
    required = [
        "physical/k2_w2_genus/designs.json",
        "physical/k2_w2_genus/diagnostic_designs.json",
        "physical/k2_w2_genus/component_diagnostics.json",
        "physical/k2_w2_genus/golden_reference.json",
        "physical/k2_w2_genus/raw_golden_reference.json",
        "physical/k2_w2_genus/functional_loss_reference.json",
        "physical/k2_w2_genus/genus_driver.tcl",
        "physical/k2_w2_genus/run_genus.py",
        "physical/k2_w2_genus/run_goal_cohort.py",
        "physical/k2_w2_genus/run_mapped_functional_xcelium.py",
        "physical/k2_w2_genus/mapped_functional_tb.sv",
        "physical/k2_w2_boundaries.json",
        "physical/k2_w2_tops/designs.json",
    ]
    required.append(registry["staged_manifest_identity"]["path"])
    required.extend(row["path"] for row in
                    registry["timing_template_identities"].values())
    required.append(registry["mmmc_template_identity"]["path"])
    for design in registry["designs"].values():
        required.append(design["filelist"])
        required.extend(source["path"] for source in design["sources"])
        required.extend(include["path"] for include in design["include_files"])
    required = list(dict.fromkeys(required))
    for relative in required:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if tracked.returncode:
            raise FlowError(f"flow input is not tracked: {relative}")
    clean = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *required], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if clean.returncode:
        raise FlowError("flow registry/driver/runner/filelist differs from HEAD")
    return {relative: sha256_bytes(stable_read(root / relative)) for relative in required}


def verify_goal_registry_bindings(registry: dict[str, Any]) -> None:
    try:
        diagnostics = json.loads(stable_read(DIAGNOSTIC_REGISTRY))
        components = json.loads(stable_read(HERE / "component_diagnostics.json"))
        fair = json.loads(stable_read(FAIR_TOP_REGISTRY))
        boundaries = json.loads(stable_read(BOUNDARY_REGISTRY))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid goal boundary registry: {error}") from error
    if (diagnostics.get("schema") != "k2_w2_genus_diagnostic_registry_v1" or
            diagnostics.get("ranking_eligible") is not False or
            diagnostics.get("final_server_execution_eligible") is not False):
        raise FlowError("native/generic diagnostic registry eligibility mismatch")
    if (components.get("schema") != "k2_w2_genus_component_diagnostics_v1" or
            components.get("ranking_eligible") is not False or
            components.get("final_server_execution_eligible") is not False):
        raise FlowError("component/native diagnostic registry eligibility mismatch")
    if (fair.get("schema") != "k2-w2-fair-physical-tops-v1" or
            fair.get("ranking_eligible") is not False or
            fair.get("final_server_execution_eligible") is not False or
            set(fair.get("designs", {})) != {"fovea_a7", "a2_p6", "a3_p6"}):
        raise FlowError("owner generic diagnostic registry mismatch")
    final_tops = {row["top"] for row in registry["designs"].values()}
    generic_tops = {row["top"] for row in fair["designs"].values()}
    if final_tops & generic_tops:
        raise FlowError("owner generic wrapper substituted into final staged registry")
    cohorts = {row.get("id"): row for row in boundaries.get("cohorts", [])}
    if boundaries.get("schema") != "k2-w2-physical-boundary-cohorts-v1":
        raise FlowError("diagnostic boundary registry schema mismatch")
    owner_generic = cohorts.get("owner_generic_endpoint_diagnostic", {})
    if owner_generic.get("final_server_execution_eligible") is not False:
        raise FlowError("owner generic boundary is not diagnostic-only")


def require_ordered_tokens(text: str, tokens: list[str], label: str) -> None:
    cursor = 0
    for token in tokens:
        position = text.find(token, cursor)
        if position < 0:
            raise FlowError(f"{label} omits or reorders golden command: {token}")
        cursor = position + len(token)


def verify_driver_contract(golden: dict[str, Any]) -> None:
    text = stable_read(DRIVER_TCL).decode("utf-8", errors="strict")
    commands = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))
    require_ordered_tokens(commands, golden["command_order"], "candidate-neutral driver")
    if "set_db lp_insert_clock_gating true" not in commands:
        raise FlowError("candidate-neutral driver differs from golden clock-gating mode")
    required_classes = (
        "report_timing -check_type setup", "report_timing -check_type hold",
        "report_timing -check_type recovery", "report_timing -check_type removal",
        "report_timing -check_type clock_gating_setup",
        "report_timing -check_type clock_gating_hold",
        "report_timing -check_type pulse_width", "report_qor", "check_timing -verbose",
    )
    missing = [token for token in required_classes if token not in commands]
    if missing:
        raise FlowError(f"Genus driver omits strict timing report class: {','.join(missing)}")


def read_golden_members(archive: Path, golden: dict[str, Any]) -> dict[str, bytes]:
    expected = golden["anchors"]
    payloads: dict[str, bytes] = {}
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members: dict[str, list[tarfile.TarInfo]] = {}
            for member in bundle.getmembers():
                members.setdefault(member.name, []).append(member)
            for name, identity in expected.items():
                matches = members.get(name, [])
                if len(matches) != 1 or not matches[0].isfile():
                    raise FlowError(f"golden archive member missing/duplicate/non-file: {name}")
                extracted = bundle.extractfile(matches[0])
                if extracted is None:
                    raise FlowError(f"cannot read golden archive member: {name}")
                payload = extracted.read()
                if len(payload) != identity[0] or sha256_bytes(payload) != identity[1]:
                    raise FlowError(f"golden archive member byte mismatch: {name}")
                payloads[name] = payload
    except (tarfile.TarError, OSError) as error:
        raise FlowError(f"invalid golden archive: {error}") from error
    return payloads


def verify_golden_archive(source: Path, snapshot: Path,
                          golden: dict[str, Any]) -> dict[str, Any]:
    resolved = source.resolve(strict=True)
    if resolved.name != golden["archive_filename"]:
        raise FlowError("golden archive filename mismatch; local-source substitution rejected")
    archive_hash = copy_stable(resolved, snapshot)
    if archive_hash != golden["archive_sha256"]:
        raise FlowError("golden archive SHA mismatch; local-source substitution rejected")
    payloads = read_golden_members(snapshot, golden)
    command_order = golden["command_order"]
    for family in ("fovea", "cluster2"):
        prefix = f"synth/pnr/resynth_{family}_buffered"
        tcl = payloads[f"{prefix}/genus_1.0.tcl"].decode("utf-8")
        cmd = payloads[f"{prefix}/genus_1.0.cmd"].decode("utf-8")
        log = payloads[f"{prefix}/genus_1.0.log"].decode("utf-8")
        require_ordered_tokens(tcl, command_order, f"golden {family} Tcl")
        if "set_db lp_insert_clock_gating true" not in tcl:
            raise FlowError(f"golden {family} Tcl clock-gating contract mismatch")
        if f"source {prefix}/genus_1.0.tcl" not in cmd:
            raise FlowError(f"golden {family} command transcript mismatch")
        if (f"Version: {golden['genus_version']}" not in log or
                "Error=0, Fatal=0" not in log or "Normal exit." not in log):
            raise FlowError(f"golden {family} log format/status mismatch")
        stem = f"aer_{family}_buffered_1.0"
        verify_report_payloads(
            f"aer_{family}_buffered",
            payloads[f"{prefix}/{stem}_area.rpt"],
            payloads[f"{prefix}/{stem}_gtiming.rpt"],
            payloads[f"{prefix}/{stem}_gpower.rpt"],
            label=f"golden {family}",
        )
    if sha256_bytes(stable_read(resolved)) != archive_hash:
        raise FlowError("golden archive changed during qualification")
    return {
        "cohort": golden["cohort"],
        "archive_filename": golden["archive_filename"],
        "archive_sha256": archive_hash,
        "manifest_sha256": sha256_bytes(stable_read(GOLDEN_REFERENCE)),
        "anchor_count": len(payloads),
        "anchor_sha256": {
            name: sha256_bytes(payload) for name, payload in sorted(payloads.items())
        },
        "genus_version": golden["genus_version"],
        "clock_gating_insertion": True,
        "report_format": "GANGHEE_GENUS_23P14_AREA_GTIMING_GPOWER",
    }


def verify_raw_golden_archive(source: Path, snapshot: Path,
                              golden: dict[str, Any]) -> dict[str, Any]:
    resolved = source.resolve(strict=True)
    if resolved.name != golden["archive_filename"]:
        raise FlowError("raw golden filename mismatch; local-source substitution rejected")
    archive_hash = copy_stable(resolved, snapshot)
    if archive_hash != golden["archive_sha256"]:
        raise FlowError("raw golden archive SHA mismatch; report-only/local substitution rejected")
    payloads = read_golden_members(snapshot, golden)
    for run_name, run in golden["runs"].items():
        prefix = run["prefix"]
        period = run["period"]
        top = run["top"]
        stem = f"{top}_{period}"
        tcl_name = f"{prefix}/genus_{period}.tcl"
        cmd_name = f"{prefix}/genus_{period}.cmd"
        log_name = f"{prefix}/genus_{period}.log"
        tcl = payloads[tcl_name].decode("utf-8")
        cmd = payloads[cmd_name].decode("utf-8")
        log = payloads[log_name].decode("utf-8")
        require_ordered_tokens(tcl, golden["command_order"], f"raw {run_name} Tcl")
        if (f"set LIB_FILE {golden['library_path']}" not in tcl or
                "set_db lp_insert_clock_gating true" not in tcl or
                run["read_hdl"] not in tcl):
            raise FlowError(f"raw {run_name} exact library/source settings mismatch")
        if f"source {prefix}/genus_{period}.tcl" not in cmd:
            raise FlowError(f"raw {run_name} command transcript mismatch")
        if (f"Version: {golden['genus_version']}" not in log or
                "Error=0, Fatal=0" not in log or "Normal exit." not in log):
            raise FlowError(f"raw {run_name} log format/status mismatch")
        area = payloads[f"{prefix}/{stem}_area.rpt"]
        timing = payloads[f"{prefix}/{stem}_gtiming.rpt"]
        power = payloads[f"{prefix}/{stem}_gpower.rpt"]
        verify_report_payloads(top, area, timing, power, label=f"raw {run_name}")
        netlist = payloads[f"{prefix}/{stem}_netlist.v"]
        modules = set(MODULE_RE.findall(netlist.decode("utf-8", errors="strict")))
        if top not in modules:
            raise FlowError(f"raw {run_name} netlist does not define its exact top")
        if not payloads[f"{prefix}/{stem}_out.sdc"]:
            raise FlowError(f"raw {run_name} mapped SDC is empty")
    if sha256_bytes(stable_read(resolved)) != archive_hash:
        raise FlowError("raw golden archive changed during qualification")
    return {
        "cohort": golden["cohort"],
        "archive_filename": golden["archive_filename"],
        "archive_sha256": archive_hash,
        "manifest_sha256": sha256_bytes(stable_read(RAW_GOLDEN_REFERENCE)),
        "anchor_count": len(payloads),
        "anchor_sha256": {
            name: sha256_bytes(payload) for name, payload in sorted(payloads.items())
        },
        "genus_version": golden["genus_version"],
        "library_path": golden["library_path"],
        "clock_gating_insertion": True,
        "report_format": "GANGHEE_RAW_GENUS_23P14_AREA_GTIMING_GPOWER",
        "artifact_completeness": "TCL_LOG_REPORT_NETLIST_SDC_SOURCE_COMPLETE",
    }


def verify_reference_cohort_separation(raw: dict[str, Any],
                                       buffered: dict[str, Any]) -> None:
    if raw["cohort"] == buffered["cohort"]:
        raise FlowError("raw and buffered reference cohorts collapsed")
    shared = (
        "rtl/ganghee_cluster2/arbiter2.v",
        "rtl/ganghee_cluster2/arbiter4_tree.v",
        "rtl/ganghee_cluster2/aer_tx16_trad_rowcol_fovea.v",
        "rtl/ganghee_cluster2/aer_tx16_trad_rowcol_fovea_cluster2.v",
    )
    for path in shared:
        if raw["anchor_sha256"].get(path) != buffered["anchor_sha256"].get(path):
            raise FlowError(f"raw/buffered shared native source mismatch: {path}")


def verify_functional_loss_archive(source: Path, snapshot: Path,
                                   reference: dict[str, Any]) -> dict[str, Any]:
    resolved = source.resolve(strict=True)
    if resolved.name != reference["archive_filename"]:
        raise FlowError("functional loss archive filename mismatch")
    archive_hash = copy_stable(resolved, snapshot)
    if archive_hash != reference["archive_sha256"]:
        raise FlowError("functional loss archive SHA mismatch")
    anchors = read_golden_members(snapshot, reference)
    provenance = anchors["provenance.txt"].decode("utf-8", errors="strict")
    required_provenance = (
        f"snapshot_head={reference['snapshot_head']}",
        "binding_reset_quiet_arming_patch=workspace-diff",
        f"snapshot_archive_sha256={reference['snapshot_archive_sha256']}",
        f"attempt={reference['ledger_prefix'].rstrip('/')}",
        f"TOOL:\t{reference['simulator']}",
    )
    if any(line not in provenance.splitlines() for line in required_provenance):
        raise FlowError("functional loss provenance mismatch")

    try:
        with tarfile.open(snapshot, mode="r:gz") as bundle:
            regular: dict[str, tarfile.TarInfo] = {}
            duplicates: set[str] = set()
            for member in bundle.getmembers():
                if member.name in regular:
                    duplicates.add(member.name)
                elif member.isfile():
                    regular[member.name] = member
            if duplicates:
                raise FlowError("functional loss archive contains duplicate regular members")
            if "eval-driver-final.log" in regular:
                raise FlowError("stale outer eval-driver-final.log must not be bound")
            ledger = anchors["result-artifacts.sha256"].decode("utf-8").splitlines()
            if len(ledger) != reference["ledger_entries"]:
                raise FlowError("functional loss ledger cardinality mismatch")
            seen: set[str] = set()
            for line in ledger:
                match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
                if not match or not match.group(2).startswith(reference["ledger_prefix"]):
                    raise FlowError("functional loss ledger prefix/schema mismatch")
                relative = match.group(2)[len(reference["ledger_prefix"]):]
                if (not relative.startswith("results/") or relative in seen or
                        relative not in regular):
                    raise FlowError("functional loss ledger missing/duplicate/unattached member")
                extracted = bundle.extractfile(regular[relative])
                if extracted is None or sha256_bytes(extracted.read()) != match.group(1):
                    raise FlowError(f"functional loss ledger SHA mismatch: {relative}")
                seen.add(relative)
    except (tarfile.TarError, OSError) as error:
        raise FlowError(f"invalid functional loss archive: {error}") from error

    metric_pattern = re.compile(
        r"AER_CLEAN_METRICS .*?generated=(\d+) overrun=(\d+) "
        r"accepted=(\d+) delivered=(\d+)")
    measured: dict[str, Any] = {}
    for candidate, expected in reference["candidates"].items():
        log = anchors[f"{candidate}-run.log"].decode("utf-8", errors="strict")
        rows = [tuple(map(int, match.groups())) for match in metric_pattern.finditer(log)]
        if len(rows) != expected["run_passes"] + 1:
            raise FlowError(f"functional {candidate} metric cardinality mismatch")
        full50 = tuple(map(sum, zip(*rows[:expected["run_passes"]])))
        actual = {
            "generated": full50[0], "overrun": full50[1],
            "accepted": full50[2], "delivered": full50[3],
        }
        if actual != expected["full50"]:
            raise FlowError(f"functional {candidate} full50 loss totals mismatch")
        reset = rows[-1]
        if reset != (
                expected["reset_generated"], 0, expected["reset_accepted"],
                expected["reset_delivered"]):
            raise FlowError(f"functional {candidate} reset accounting mismatch")
        if (log.count(f"RUN_PASS candidate={candidate} ") != expected["run_passes"] or
                f"CANDIDATE_COMPLETE key={candidate} pairwise_status=0" not in log or
                "AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16" not in log):
            raise FlowError(f"functional {candidate} run/reset/pairwise status mismatch")
        status = anchors[f"results/{candidate}/pairwise-cross-map.status"]
        if status != f"{expected['pairwise_status']}\n".encode("ascii"):
            raise FlowError(f"functional {candidate} pairwise artifact mismatch")
        aggregate = anchors[f"results/{candidate}/full50-nonmixed48.aggregate.json"]
        try:
            json.loads(aggregate)
        except json.JSONDecodeError as error:
            raise FlowError(f"functional {candidate} aggregate is invalid") from error
        measured[candidate] = actual
    if sha256_bytes(stable_read(resolved)) != archive_hash:
        raise FlowError("functional loss archive changed during qualification")
    return {
        "cohort": reference["cohort"],
        "qualification": reference["qualification"],
        "claim_scope": reference["claim_scope"],
        "archive_sha256": archive_hash,
        "manifest_sha256": sha256_bytes(stable_read(FUNCTIONAL_LOSS_REFERENCE)),
        "ledger": "PASS_338_OF_338_EXACT_PREFIX",
        "outer_driver_log": "EXCLUDED_STALE",
        "candidate_logs": "PASS_50_OF_50_EACH_RESET_AND_PAIRWISE",
        "full50_loss_totals": measured,
        "ppa_use": "FORBIDDEN",
    }


def verify_source_commit(root: Path, registry: dict[str, Any]) -> str:
    source_commit = registry["repository_commit"]
    staged = registry["staged_manifest_identity"]
    publication_commit = staged["publication_commit"]
    head = str(git(root, "rev-parse", "HEAD")).strip()
    for label, commit in (("source", source_commit),
                          ("publication", publication_commit)):
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, head], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if ancestor.returncode:
            raise FlowError(f"{label} commit {commit} is not an ancestor of HEAD {head}")
    manifest_path = staged["path"]
    object_type = str(git(
        root, "cat-file", "-t", f"{publication_commit}:{manifest_path}")).strip()
    if object_type != "blob":
        raise FlowError("published staged manifest object is not a blob")
    committed_manifest = git(
        root, "show", f"{publication_commit}:{manifest_path}", binary=True)
    assert isinstance(committed_manifest, bytes)
    if (committed_manifest != stable_read(root / manifest_path) or
            sha256_bytes(committed_manifest) != staged["sha256"]):
        raise FlowError("published staged manifest commit/blob mismatch")
    return head


def verify_design(root: Path, registry: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in registry["designs"]:
        raise FlowError(f"unknown design: {key}")
    design = registry["designs"][key]
    filelist_path = root / design["filelist"]
    filelist_payload = stable_read(filelist_path)
    if sha256_bytes(filelist_payload) != design["filelist_sha256"]:
        raise FlowError(f"filelist SHA mismatch: {design['filelist']}")
    names, filelist_defines, include_dirs = parse_staged_filelist(
        filelist_payload, key)
    expected_names = [row["path"] for row in design["sources"]]
    if (names != expected_names or
            design.get("defines") != ["SYNTHESIS", *filelist_defines] or
            design.get("include_dirs") != include_dirs):
        raise FlowError(f"filelist/source order mismatch: {key}")
    committed_filelist = git(root, "show", f"HEAD:{design['filelist']}", binary=True)
    assert isinstance(committed_filelist, bytes)
    if committed_filelist != filelist_payload:
        raise FlowError(f"filelist differs from HEAD: {design['filelist']}")
    if (design.get("source_origin") != "tech_staged_repository_exact" or
            design.get("boundary_cohort") != "tech_staged_complete_compositions"):
        raise FlowError(f"non-staged design is forbidden in final execution: {key}")
    source_commit = registry["repository_commit"]
    committed_filelist_at_source = git(
        root, "show", f"{source_commit}:{design['filelist']}", binary=True)
    assert isinstance(committed_filelist_at_source, bytes)
    if committed_filelist_at_source != filelist_payload:
        raise FlowError(f"staged filelist differs from source commit: {key}")
    for row in design["sources"]:
        relative = row["path"]
        working = stable_read(root / relative)
        committed_head = git(root, "show", f"HEAD:{relative}", binary=True)
        committed_source = git(root, "show", f"{source_commit}:{relative}", binary=True)
        assert isinstance(committed_head, bytes) and isinstance(committed_source, bytes)
        if (working != committed_head or working != committed_source or
                sha256_bytes(working) != row["sha256"]):
            raise FlowError(f"staged source byte mismatch: {relative}")
    for row in design["include_files"]:
        relative = row["path"]
        working = stable_read(root / relative)
        committed_head = git(root, "show", f"HEAD:{relative}", binary=True)
        committed_source = git(root, "show", f"{source_commit}:{relative}", binary=True)
        assert isinstance(committed_head, bytes) and isinstance(committed_source, bytes)
        if (working != committed_head or working != committed_source or
                sha256_bytes(working) != row["sha256"]):
            raise FlowError(f"staged include byte mismatch: {relative}")
    if design.get("parameters") != {}:
        raise FlowError(f"unimplemented nonempty parameter map: {key}")
    return design


def materialize_sdc(root: Path, design: dict[str, Any]) -> bytes:
    timing = design["strict_sdc"]
    _, path = relative_repo_path(root, timing["path"], "candidate strict SDC")
    payload = stable_read(path)
    if sha256_bytes(payload) != timing["sha256"]:
        raise FlowError("candidate strict SDC SHA mismatch")
    required = (
        "create_generated_clock", "-clock_fall -add_delay", "set_input_delay -min",
        "set_input_delay -max", "set_output_delay -min", "set_output_delay -max",
        "set_clock_gating_check", "set_min_pulse_width -high",
        "set_min_pulse_width -low", "recovery_falling", "removal_falling",
        "set_driving_cell", "set_input_transition", "set_load", "all_registers -clock",
    )
    text = payload.decode("utf-8", errors="strict")
    missing = [token for token in required if token not in text]
    if missing or "set_false_path" in text or "set_multicycle_path" in text:
        raise FlowError(f"strict SDC timing class missing/forbidden: {','.join(missing)}")
    return payload


def tool_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    payload = stable_read(resolved)
    if not os.access(resolved, os.X_OK):
        raise FlowError(f"tool is not executable: {resolved}")
    version = subprocess.run(
        [str(resolved), "-version"], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, check=False,
    )
    if version.returncode:
        raise FlowError(f"tool version probe failed: {resolved}")
    return {
        "requested_path": str(path),
        "resolved_path": str(resolved),
        "sha256": sha256_bytes(payload),
        "version_output": version.stdout.strip(),
    }


def dffnsrx1_preflight(liberty: Path, lef: Path, label: str) -> dict[str, Any]:
    liberty_payload = stable_read(liberty)
    lef_payload = stable_read(lef)
    text = liberty_payload.decode("utf-8", errors="strict")
    start = re.search(r"\bcell\s*\(\s*DFFNSRX1\s*\)\s*\{", text)
    if start is None:
        raise FlowError(f"{label} Liberty lacks DFFNSRX1")
    following = re.search(r"\n\s*cell\s*\(", text[start.end():])
    cell = text[start.start():start.end() + following.start()] if following else text[start.start():]
    required_tokens = (
        'clocked_on : "(!CKN)"', 'clear : "(!RN)"', 'preset : "(!SN)"',
        "pin (CKN)", "pin (D)", "pin (SN)", "pin (RN)", "pin (Q)", "pin (QN)",
        "setup_falling", "hold_falling", "recovery_falling", "removal_falling",
    )
    missing = [token for token in required_tokens if token not in cell]
    if missing:
        raise FlowError(f"{label} DFFNSRX1 Liberty contract missing: {','.join(missing)}")
    for timing_type in ("recovery_falling", "removal_falling"):
        arc = re.search(
            rf"timing_type\s*:\s*{timing_type}\s*;.*?values\s*\(\s*\"([^\"]+)\"",
            cell, re.DOTALL)
        if arc is None:
            raise FlowError(f"{label} DFFNSRX1 lacks numeric {timing_type} arc")
        try:
            values = [float(value) for value in re.split(r"[ ,]+", arc.group(1).strip())]
        except ValueError as error:
            raise FlowError(f"{label} DFFNSRX1 invalid {timing_type} values") from error
        if not values or any(not math.isfinite(value) for value in values) or \
                max(abs(value) for value in values) <= 0.0:
            raise FlowError(f"{label} DFFNSRX1 zero/NaN {timing_type} arc")
    lef_text = lef_payload.decode("utf-8", errors="strict")
    macro = re.search(r"(?ms)^MACRO DFFNSRX1\s+(.*?)^END DFFNSRX1\s*$", lef_text)
    if macro is None or "SITE CoreSite" not in macro.group(1):
        raise FlowError("LEF lacks DFFNSRX1 CoreSite macro")
    pins = set(re.findall(r"(?m)^\s*PIN\s+(\S+)\s*$", macro.group(1)))
    required_pins = {"Q", "QN", "CKN", "D", "SN", "RN", "VDD", "VSS"}
    if pins != required_pins:
        raise FlowError("LEF DFFNSRX1 pin set mismatch")
    return {
        "cell": "DFFNSRX1", "liberty_sha256": sha256_bytes(liberty_payload),
        "lef_sha256": sha256_bytes(lef_payload), "site": "CoreSite",
        "pins": sorted(pins),
        "clocked_on": "(!CKN)", "clear": "(!RN)", "preset": "(!SN)",
        "timing_types": ["setup_falling", "hold_falling",
                         "recovery_falling", "removal_falling"],
        "recovery_removal_nonzero": True,
    }


def verify_mapped_rx_contract(text: str, contract: dict[str, Any]) -> None:
    cell = contract["cell"]
    instances = re.findall(
        rf"\b{re.escape(cell)}\s+(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;",
        text, re.DOTALL)
    if len(instances) != contract["exact_instances"]:
        raise FlowError(
            f"mapped {cell} count mismatch: {len(instances)} != {contract['exact_instances']}")
    for body in instances:
        connections = dict(re.findall(
            r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^()]+?)\s*\)", body))
        expected = {
            contract["clock_pin"]: contract["clock_net"],
            contract["reset_pin"]: contract["reset_net"],
            contract["preset_pin"]: contract["preset_tie"],
        }
        for pin, net in expected.items():
            if "".join(connections.get(pin, "").split()) != "".join(net.split()):
                raise FlowError(f"mapped {cell} {pin} binding mismatch")
        if "D" not in connections or not ({"Q", "QN"} & set(connections)):
            raise FlowError(f"mapped {cell} omits D/Q connectivity")


def mapped_named_instances(text: str, cell: str) -> list[tuple[str, dict[str, str]]]:
    rows = re.findall(
        rf"\b{re.escape(cell)}\s+(\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;",
        text, re.DOTALL)
    return [(name.lstrip("\\"), dict(re.findall(
        r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^()]+?)\s*\)", body)))
            for name, body in rows]


def mapped_instances(text: str) -> list[tuple[str, str, dict[str, str]]]:
    rows = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_$]*)\s+"
        r"(\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;",
        text, re.DOTALL)
    return [(kind, name.lstrip("\\"), dict(re.findall(
        r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^()]*?)\s*\)", body)))
            for kind, name, body in rows if kind not in KEYWORDS]


def mapped_module_bodies(text: str) -> dict[str, str]:
    rows = re.findall(
        r"(?ms)(?:^|\n)\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b.*?;"
        r"(.*?)\bendmodule\b", text)
    modules = {name: body for name, body in rows}
    if len(modules) != len(rows):
        raise FlowError("mapped netlist contains duplicate module definitions")
    return modules


def hierarchy_inventory(root: str, modules: dict[str, str],
                        library_cells: set[str],
                        active: tuple[str, ...] = ()) -> tuple[dict[str, int], dict[str, int]]:
    if root in active:
        raise FlowError(f"recursive mapped hierarchy at {root}")
    if root not in modules:
        raise FlowError(f"mapped hierarchy root missing: {root}")
    cells: dict[str, int] = {}
    child_modules: dict[str, int] = {}
    for kind in INSTANCE_RE.findall(modules[root]):
        if kind in KEYWORDS:
            continue
        if kind in library_cells:
            cells[kind] = cells.get(kind, 0) + 1
        elif kind in modules:
            child_cells, descendants = hierarchy_inventory(
                kind, modules, library_cells, active + (root,))
            for cell, count in child_cells.items():
                cells[cell] = cells.get(cell, 0) + count
            child_modules[kind] = child_modules.get(kind, 0) + 1
            for module, count in descendants.items():
                child_modules[module] = child_modules.get(module, 0) + count
        else:
            raise FlowError(f"unresolved/blackbox mapped cell type: {kind}")
    return cells, child_modules


def reachable_module_text(root: str, modules: dict[str, str],
                          library_cells: set[str]) -> str:
    visited: set[str] = set()
    pending = [root]
    bodies = []
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        if module not in modules:
            raise FlowError(f"mapped endpoint hierarchy root missing: {module}")
        visited.add(module)
        body = modules[module]
        bodies.append(body)
        for kind in INSTANCE_RE.findall(body):
            if kind in modules and kind not in visited:
                pending.append(kind)
            elif kind not in library_cells and kind not in KEYWORDS:
                raise FlowError(f"unresolved endpoint hierarchy type: {kind}")
    return "\n".join(bodies)


def endpoint_instance_records(root: str, modules: dict[str, str],
                              library_cells: set[str], prefixes: dict[str, str],
                              path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if root not in modules:
        raise FlowError(f"mapped endpoint hierarchy root missing: {root}")
    if root in path:
        raise FlowError(f"recursive mapped endpoint hierarchy at {root}")
    records: list[dict[str, Any]] = []
    for kind, name, pins in mapped_instances(modules[root]):
        hierarchy = ".".join((*path, root, name))
        if kind in library_cells:
            if kind in prefixes:
                if prefixes[kind] not in name:
                    raise FlowError(
                        f"mapped endpoint {kind} lost preserved leaf name: {hierarchy}")
                records.append({
                    "hierarchy": hierarchy,
                    "mapped_instance": name,
                    "cell_type": kind,
                    "pin_bindings": dict(sorted(pins.items())),
                    "provenance_root": root if not path else path[0],
                })
        elif kind in modules:
            records.extend(endpoint_instance_records(
                kind, modules, library_cells, prefixes, (*path, root, name)))
        else:
            raise FlowError(f"unresolved endpoint hierarchy type: {kind}")
    return records


def verify_endpoint_inventory(modules: dict[str, str], library_cells: set[str],
                              roots: list[str], expected: dict[str, int],
                              rx_contract: dict[str, Any],
                              posedge_contract: dict[str, Any],
                              prefixes: dict[str, str]) -> tuple[dict[str, int],
                                                                 list[dict[str, Any]]]:
    required = {"TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1"}
    if set(expected) != required or any(
            not isinstance(count, int) or count <= 0 for count in expected.values()):
        raise FlowError("exact endpoint mapped inventory contract is malformed")
    if (set(prefixes) != required or
            any(not isinstance(value, str) or not value for value in prefixes.values()) or
            len(set(prefixes.values())) != len(prefixes)):
        raise FlowError("endpoint preserved-name contract is malformed")
    if (not isinstance(roots, list) or len(roots) != 2 or
            len(set(roots)) != len(roots)):
        raise FlowError("endpoint link hierarchy root contract is malformed")
    observed = {cell: 0 for cell in required}
    endpoint_text = []
    records: list[dict[str, Any]] = []
    for root in roots:
        inventory, _ = hierarchy_inventory(root, modules, library_cells)
        for cell in required:
            observed[cell] += inventory.get(cell, 0)
        endpoint_text.append(reachable_module_text(root, modules, library_cells))
        records.extend(endpoint_instance_records(
            root, modules, library_cells, prefixes))
    if observed != expected:
        raise FlowError(f"exact endpoint mapped inventory mismatch: {observed} != {expected}")
    endpoint_payload = "\n".join(endpoint_text)
    rx_rows = mapped_named_instances(endpoint_payload, rx_contract["cell"])
    rx_text = "\n".join(
        f"{rx_contract['cell']} endpoint_{index} (" + ",".join(
            f".{pin}({net})" for pin, net in ports.items()) + ");"
        for index, (_, ports) in enumerate(rx_rows))
    verify_mapped_rx_contract(rx_text, rx_contract)
    positive = [ports for _, ports in mapped_named_instances(
        endpoint_payload, posedge_contract["cell"])]
    if len(positive) != posedge_contract["exact_instances"]:
        raise FlowError("mapped positive-edge endpoint count mismatch")
    for ports in positive:
        bindings = {
            posedge_contract["clock_pin"]: posedge_contract["clock_net"],
            posedge_contract["reset_pin"]: posedge_contract["reset_net"],
        }
        for pin, net in bindings.items():
            if "".join(ports.get(pin, "").split()) != "".join(net.split()):
                raise FlowError(f"mapped {posedge_contract['cell']} {pin} binding mismatch")
        if "D" not in ports or "Q" not in ports:
            raise FlowError(f"mapped {posedge_contract['cell']} omits D/Q connectivity")
    icg = [ports for _, ports in mapped_named_instances(
        endpoint_payload, "TLATNTSCAX2")]
    if any(set(row) != {"E", "SE", "CK", "ECK"} or
           "".join(row["SE"].split()) not in {"0", "1'b0", "1'h0", "1'd0"} or
           "".join(row["CK"].split()) != "clock_i" or
           "".join(row["E"].split()) not in {
               "enable_i&rst_n", "rst_n&enable_i"} or
           "".join(row["ECK"].split()) != "clock_o"
           for row in icg):
        raise FlowError("mapped TLATNTSCAX2 exact pin binding mismatch")
    if any({pin: "".join(row.get(pin, "").split()) for pin in
            ("A", "B", "S0", "Y")} != {
                "A": "data0_i", "B": "data1_i",
                "S0": "select_i", "Y": "data_o"}
           for _, row in mapped_named_instances(endpoint_payload, "MX2X1")):
        raise FlowError("mapped MX2X1 exact pin binding mismatch")
    record_counts = {cell: 0 for cell in required}
    for row in records:
        record_counts[row["cell_type"]] += 1
    if record_counts != observed:
        raise FlowError("endpoint connectivity records do not match hierarchy inventory")
    records.sort(key=lambda row: (row["hierarchy"], row["cell_type"]))
    return dict(sorted(observed.items())), records


def mapped_inventory(mapped: Path, library: Path, expected_top: str,
                     rx_contract: dict[str, Any],
                     posedge_contract: dict[str, Any],
                     expected_endpoint: dict[str, int],
                     endpoint_roots: list[str],
                     preserved_prefixes: dict[str, str]) -> dict[str, Any]:
    mapped_payload = stable_read(mapped)
    library_payload = stable_read(library)
    text = mapped_payload.decode("utf-8", errors="strict")
    library_text = library_payload.decode("utf-8", errors="strict")
    modules = mapped_module_bodies(text)
    if BLACKBOX_RE.search(text):
        raise FlowError("explicit blackbox marker in mapped netlist")
    if expected_top not in modules:
        raise FlowError(f"mapped netlist does not define expected top {expected_top}")
    library_cells = set(CELL_RE.findall(library_text))
    if not library_cells:
        raise FlowError("library contains no parseable cell declarations")
    inventory, hierarchy = hierarchy_inventory(expected_top, modules, library_cells)
    scan = sorted(cell for cell in inventory if SCAN_RE.search(cell))
    if scan:
        raise FlowError(f"scan cells are forbidden: {','.join(scan)}")
    if not inventory:
        raise FlowError("mapped netlist has zero library-cell instances")
    endpoint_inventory, endpoint_instances = verify_endpoint_inventory(
        modules, library_cells, endpoint_roots, expected_endpoint, rx_contract,
        posedge_contract, preserved_prefixes)
    return {
        "mapped_netlist_sha256": sha256_bytes(mapped_payload),
        "library_cell_types_available": len(library_cells),
        "mapped_cell_count": sum(inventory.values()),
        "mapped_cell_types": dict(sorted(inventory.items())),
        "mapped_module_instance_types": dict(sorted(hierarchy.items())),
        "endpoint_cell_types": endpoint_inventory,
        "endpoint_link_roots": endpoint_roots,
        "endpoint_instances": endpoint_instances,
        "endpoint_preserved_name_prefixes": preserved_prefixes,
        "dffnsrx1_global_exclusivity_proven":
            inventory.get("DFFNSRX1", 0) == endpoint_inventory["DFFNSRX1"],
        "scan_cell_types": [],
        "unresolved_or_blackbox_cell_types": [],
        "required_rx_contract": rx_contract,
    }


def verify_report_payloads(top: str, area_payload: bytes, timing_payload: bytes,
                           power_payload: bytes, label: str,
                           require_timing_pass: bool = False) -> None:
    try:
        area = area_payload.decode("utf-8", errors="strict")
        timing = timing_payload.decode("utf-8", errors="strict")
        power = power_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise FlowError(f"{label} report is not UTF-8 text") from error
    common = f"Generated by:           Genus(TM) Synthesis Solution"
    if (common not in area or f"Module:                 {top}" not in area or
            "Cell-Count  Cell-Area" not in area or
            not re.search(rf"(?m)^\s*{re.escape(top)}\s+NA\s+\d+\s+\d", area)):
        raise FlowError(f"{label} area report format mismatch")
    if common not in timing or f"Module:                 {top}" not in timing:
        raise FlowError(f"{label} timing report format mismatch")
    parse_timing_rows(timing, f"{label} timing", require_pass=require_timing_pass)
    if (f"Instance: /{top}" not in power or "Power Unit: W" not in power or
            "Category" not in power or
            not re.search(r"(?m)^\s*Subtotal\s+\S+\s+\S+\s+\S+\s+\S+", power)):
        raise FlowError(f"{label} power report format mismatch")


def parse_timing_rows(text: str, label: str,
                      expected_check: str | None = None,
                      require_pass: bool = True) -> dict[str, Any]:
    headers = list(re.finditer(
        r"(?m)^Path\s+(\d+):\s+(MET|VIOLATED)\s+"
        r"\(([-+]?(?:\d+(?:\.\d*)?|\.\d+)|NaN|Inf|-Inf)\s+ps\)\s+([^\n]+?)\s+Check",
        text))
    if not headers:
        raise FlowError(f"{label} has no complete timing path row")
    slacks: list[float] = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        segment = text[header.start():end]
        if require_pass and header.group(2) != "MET":
            raise FlowError(f"{label} contains VIOLATED path {header.group(1)}")
        if expected_check is not None and expected_check.lower() not in header.group(4).lower():
            raise FlowError(f"{label} timing class mismatch")
        slack_row = re.search(
            r"(?m)^\s*Slack:=\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)|NaN|Inf|-Inf)\s*$",
            segment)
        if slack_row is None:
            raise FlowError(f"{label} truncated path {header.group(1)}")
        try:
            header_slack = float(header.group(3))
            detail_slack = float(slack_row.group(1))
        except ValueError as error:
            raise FlowError(f"{label} invalid slack") from error
        if (not math.isfinite(header_slack) or not math.isfinite(detail_slack) or
                (require_pass and (header_slack < 0.0 or detail_slack < 0.0)) or
                abs(header_slack - detail_slack) > 1e-9):
            raise FlowError(f"{label} negative/NaN/inconsistent slack")
        slacks.append(detail_slack)
    return {"path_count": len(slacks), "minimum_slack_ps": min(slacks)}


def parse_qor_and_coverage(qor: str, coverage: str,
                           minimum_slack_ps: float) -> dict[str, Any]:
    def number(label: str) -> float:
        match = re.fullmatch(
            rf"(?ms).*^{re.escape(label)}:\s*"
            r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)|NaN|Inf|-Inf)\s*$.*", qor)
        if match is None:
            raise FlowError(f"QoR omits {label}")
        value = float(match.group(1))
        if not math.isfinite(value):
            raise FlowError(f"QoR {label} is NaN/Inf")
        return value
    wns = number("WNS (ps)")
    tns = number("TNS (ps)")
    unconstrained = number("Unconstrained Paths")
    if wns < 0.0 or abs(tns) > 1e-12 or unconstrained != 0.0 or \
            abs(wns - minimum_slack_ps) > 1e-9:
        raise FlowError("QoR WNS/TNS/unconstrained mismatch or violation")
    coverage_names = (
        "no_clock", "constant_clock", "no_input_delay", "no_output_delay",
        "no_drive", "no_load", "unconstrained",
    )
    values: dict[str, int] = {}
    for name in coverage_names:
        matches = re.findall(rf"(?m)^{name}:\s*(\d+)\s*$", coverage)
        if len(matches) != 1:
            raise FlowError(f"check_timing missing/duplicate {name}")
        values[name] = int(matches[0])
    if any(values.values()):
        raise FlowError("check_timing reports unconstrained/no-drive/no-load coverage hole")
    return {"wns_ps": wns, "tns_ps": tns,
            "unconstrained_paths": int(unconstrained), "coverage": values}


def verify_reports(output: Path, top: str, log_payload: bytes,
                   expected_version: str) -> dict[str, str]:
    log = log_payload.decode("utf-8", errors="replace")
    if f"W2_GENUS_PASS top={top}" not in log:
        raise FlowError("Genus PASS sentinel missing from log")
    if (f"Version: {expected_version}" not in log or "Normal exit." not in log or
            not re.search(r"Info=\d+, Warn=\d+, Error=0, Fatal=0", log)):
        raise FlowError("Genus log lacks golden version/zero-error/normal-exit evidence")
    if re.search(r"(?mi)^\s*(?:Error|Fatal)\s*[:\[]", log):
        raise FlowError("Genus log contains an error/fatal diagnostic")
    names = {
        "area": f"{top}_area.rpt",
        "gtiming": f"{top}_gtiming.rpt",
        "gpower": f"{top}_gpower.rpt",
        "setup": f"{top}_setup.rpt",
        "hold": f"{top}_hold.rpt",
        "recovery": f"{top}_recovery.rpt",
        "removal": f"{top}_removal.rpt",
        "gating_setup": f"{top}_gating_setup.rpt",
        "gating_hold": f"{top}_gating_hold.rpt",
        "pulse_width": f"{top}_pulse_width.rpt",
        "qor": f"{top}_qor.rpt",
        "check_timing": f"{top}_check_timing.rpt",
    }
    payloads = {kind: stable_read(output / name) for kind, name in names.items()}
    if any(not payload for payload in payloads.values()):
        raise FlowError("empty Genus report")
    verify_report_payloads(
        top, payloads["area"], payloads["gtiming"], payloads["gpower"],
        label="candidate", require_timing_pass=True,
    )
    classes = {
        "setup": "Setup", "hold": "Hold", "recovery": "Recovery",
        "removal": "Removal", "gating_setup": "Clock Gating Setup",
        "gating_hold": "Clock Gating Hold", "pulse_width": "Pulse Width",
    }
    minimum = math.inf
    for kind, expected in classes.items():
        text = payloads[kind].decode("utf-8", errors="strict")
        if ("Generated by:           Genus(TM) Synthesis Solution" not in text or
                f"Module:                 {top}" not in text):
            raise FlowError(f"candidate {kind} report header mismatch")
        parsed = parse_timing_rows(text, f"candidate {kind}", expected)
        minimum = min(minimum, parsed["minimum_slack_ps"])
    parse_qor_and_coverage(
        payloads["qor"].decode("utf-8", errors="strict"),
        payloads["check_timing"].decode("utf-8", errors="strict"), minimum)
    return {names[kind]: sha256_bytes(payload) for kind, payload in payloads.items()}


def run_mapped_functional_gate(
        hook: Path | None, attempt: Path, design_key: str, design: dict[str, Any],
        mapped: Path, sdf: Path, models: list[Path],
        source_snapshots: list[dict[str, Any]], xrun_identity: dict[str, str]
        ) -> tuple[dict[str, Any], str]:
    if hook is None:
        raise FlowError("mapped functional gate hook is required")
    if not models:
        raise FlowError("mapped functional gate requires vendor functional models")
    snapshot = attempt / "bundle" / "mapped_functional_hook"
    hook_hash = copy_stable(hook.resolve(strict=True), snapshot, 0o555)
    tb_snapshot = attempt / "bundle" / "mapped_functional_tb.sv"
    tb_hash = copy_stable(MAPPED_FUNCTIONAL_TB, tb_snapshot)
    xrun = Path(xrun_identity["resolved_path"]).resolve(strict=True)
    xrun_before = tool_identity(xrun)
    if (xrun_before["sha256"] != xrun_identity["sha256"] or
            xrun_identity["parsed_version"] not in xrun_before["version_output"]):
        raise FlowError("mapped functional simulator is not the proven Xcelium")
    model_snapshots: list[Path] = []
    model_hashes: dict[str, str] = {}
    source_model_hashes: dict[Path, str] = {}
    for index, model in enumerate(models):
        resolved = model.resolve(strict=True)
        source_hash = sha256_bytes(stable_read(resolved))
        destination = attempt / "bundle" / "functional_models" / (
            f"{index:02d}_{resolved.name}")
        if copy_stable(resolved, destination) != source_hash:
            raise FlowError("vendor functional model snapshot SHA mismatch")
        model_snapshots.append(destination)
        if destination.name in model_hashes:
            raise FlowError("duplicate vendor functional model basename")
        model_hashes[destination.name] = source_hash
        source_model_hashes[resolved] = source_hash
    rtl_filelist = attempt / "bundle" / "mapped_functional_rtl.f"
    rtl_rows = [str(attempt / "bundle" / "sources" / row["path"])
                for row in source_snapshots]
    write_exclusive(rtl_filelist, ("\n".join(rtl_rows) + "\n").encode(), 0o444)
    hook_json = attempt / "work" / "mapped-functional-hook.json"
    gate_json = attempt / "mapped-functional-gate.json"
    gate_log = attempt / "logs" / "mapped-functional-gate.log"
    command = [
        str(snapshot), "--design", design_key, "--top", design["top"],
        "--rtl-filelist", str(rtl_filelist), "--netlist", str(mapped),
        "--sdf", str(sdf), "--scenarios",
        ",".join(design["required_mapped_functional_tests"]),
        "--xrun", str(xrun), "--testbench", str(tb_snapshot),
        "--output", str(hook_json), "--log", str(gate_log),
    ]
    for define in design["defines"]:
        command.extend(["--define", define])
    for model in model_snapshots:
        command.extend(["--model", str(model)])
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    write_exclusive(
        attempt / "logs" / "mapped-functional-hook.stdout.log",
        result.stdout.encode())
    if result.returncode or "W2_MAPPED_FUNCTIONAL_PASS" not in result.stdout:
        raise FlowError("mapped functional hook failed or omitted PASS sentinel")
    try:
        document = json.loads(stable_read(hook_json))
    except json.JSONDecodeError as error:
        raise FlowError(f"invalid mapped functional gate JSON: {error}") from error
    mapped_hash = sha256_bytes(stable_read(mapped))
    sdf_hash = sha256_bytes(stable_read(sdf))
    checks = {
        "accepted": "EXACT", "retired": "EXACT", "global_order": "EXACT",
        "conservation": "EXACT", "protocol_error": "ZERO",
        "reset_and_drain": "PASS",
    }
    if (document.get("schema") != "k2_w2_mapped_functional_gate_v1" or
            document.get("status") != "PASS" or
            document.get("design") != design_key or
            document.get("top") != design["top"] or
            document.get("mapped_netlist_sha256") != mapped_hash or
            document.get("method") not in {"xcelium_vendor_models", "formal_lec"} or
            document.get("scenarios") != design[
                "required_mapped_functional_tests"] or
            document.get("checks") != checks or
            document.get("model_sha256") != model_hashes or
            document.get("sdf_status") != "ANNOTATED" or
            document.get("sdf_sha256") != sdf_hash or
            document.get("log_sha256") != sha256_bytes(stable_read(gate_log))):
        raise FlowError("mapped staged-vs-netlist functional gate mismatch")
    if sha256_bytes(stable_read(hook.resolve(strict=True))) != hook_hash:
        raise FlowError("mapped functional hook changed during execution")
    if (sha256_bytes(stable_read(MAPPED_FUNCTIONAL_TB)) != tb_hash or
            tool_identity(xrun) != xrun_before):
        raise FlowError("mapped functional TB or Xcelium changed during execution")
    for source, expected in source_model_hashes.items():
        if sha256_bytes(stable_read(source)) != expected:
            raise FlowError("vendor functional model changed during execution")
    document["hook_sha256"] = hook_hash
    document["testbench_sha256"] = tb_hash
    document["simulator"] = xrun_before
    document["rtl_filelist_sha256"] = sha256_bytes(stable_read(rtl_filelist))
    # Re-publish the normalized producer-owned form so downstream bytes bind
    # exactly what was validated rather than an untrusted hook serialization.
    write_exclusive(gate_json, canonical(document))
    return document, sha256_bytes(stable_read(gate_json))


def run_flow(root: Path, design_key: str, genus: Path, library: Path,
             hold_library: Path, cell_lef: Path, shared_qrc: Path,
             output_root: Path, attempt_name: str,
             functional_hook: Path | None, functional_models: list[Path],
             golden_archive: Path,
             raw_golden_archive: Path,
             functional_loss_archive: Path,
             server_environment_receipt: Path) -> Path:
    root = root.resolve(strict=True)
    if root != ROOT.resolve(strict=True):
        raise FlowError("runner entrypoint and repository root identity mismatch")
    if not SAFE_ATTEMPT.fullmatch(attempt_name):
        raise FlowError("invalid attempt name")
    registry = load_registry(root)
    golden = load_golden_reference()
    raw_golden = load_raw_golden_reference()
    functional_loss = load_functional_loss_reference()
    if (raw_golden["genus_version"] != golden["genus_version"] or
            raw_golden["library_path"] != golden["library_path"] or
            raw_golden["clock_gating_insertion"] !=
            golden["clock_gating_insertion"]):
        raise FlowError("raw and buffered golden tool/library settings differ")
    verify_driver_contract(golden)
    verify_goal_registry_bindings(registry)
    flow_files = verify_flow_tree(root, registry)
    head = verify_source_commit(root, registry)
    design = verify_design(root, registry, design_key)
    if (functional_hook is None or functional_hook.resolve(strict=True) !=
            MAPPED_FUNCTIONAL_HOOK.resolve(strict=True)):
        raise FlowError("mapped functional hook is not the immutable production runner")
    environment_payload, proven_environment = verify_server_environment_receipt(
        server_environment_receipt, genus, library, hold_library, cell_lef,
        shared_qrc)
    attempt = output_root.resolve() / attempt_name
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "bundle" / "sources").mkdir(parents=True)
    (attempt / "work").mkdir(parents=True)
    (attempt / "logs").mkdir(parents=True)

    golden_identity = verify_golden_archive(
        golden_archive, attempt / "bundle" / golden["archive_filename"], golden)
    raw_golden_identity = verify_raw_golden_archive(
        raw_golden_archive,
        attempt / "bundle" / raw_golden["archive_filename"], raw_golden)
    verify_reference_cohort_separation(raw_golden_identity, golden_identity)
    functional_loss_identity = verify_functional_loss_archive(
        functional_loss_archive,
        attempt / "bundle" / functional_loss["archive_filename"], functional_loss)
    server_environment_snapshot = attempt / "bundle" / "server-environment.json"
    write_exclusive(server_environment_snapshot, environment_payload, 0o444)
    environment_identity = {
        "path": "bundle/server-environment.json",
        "sha256": sha256_bytes(environment_payload),
        "xrun": proven_environment["xrun"],
    }
    tool_before = tool_identity(genus)
    if golden["genus_version"] not in tool_before["version_output"]:
        raise FlowError("Genus version does not match authoritative golden archive")
    if library.resolve(strict=True).name != golden["library_basename"]:
        raise FlowError("Liberty basename does not match authoritative golden Tcl")
    if (hold_library.resolve(strict=True).name != "fast_vdd1v0_basicCells.lib" or
            cell_lef.resolve(strict=True).name != "gsclib045_macro.lef" or
            shared_qrc.resolve(strict=True).name != "gpdk045.tch"):
        raise FlowError("hold Liberty/LEF/shared-QRC basename mismatch")
    library_source_hash = sha256_bytes(stable_read(library.resolve(strict=True)))
    hold_library_hash = sha256_bytes(stable_read(hold_library.resolve(strict=True)))
    cell_lef_hash = sha256_bytes(stable_read(cell_lef.resolve(strict=True)))
    shared_qrc_hash = sha256_bytes(stable_read(shared_qrc.resolve(strict=True)))
    dff_setup = dffnsrx1_preflight(
        library.resolve(strict=True), cell_lef.resolve(strict=True), "setup")
    dff_hold = dffnsrx1_preflight(
        hold_library.resolve(strict=True), cell_lef.resolve(strict=True), "hold")
    library_snapshot = attempt / "bundle" / "library.lib"
    if copy_stable(library.resolve(strict=True), library_snapshot) != library_source_hash:
        raise FlowError("library snapshot SHA mismatch")
    hold_library_snapshot = attempt / "bundle" / "hold_library.lib"
    cell_lef_snapshot = attempt / "bundle" / "cells.lef"
    shared_qrc_snapshot = attempt / "bundle" / "shared_typical_qrc.tch"
    if (copy_stable(hold_library.resolve(strict=True), hold_library_snapshot) !=
            hold_library_hash or
            copy_stable(cell_lef.resolve(strict=True), cell_lef_snapshot) !=
            cell_lef_hash or
            copy_stable(shared_qrc.resolve(strict=True), shared_qrc_snapshot) !=
            shared_qrc_hash):
        raise FlowError("hold library/LEF/QRC snapshot SHA mismatch")
    source_snapshots = []
    include_snapshots = []
    source_paths_v = []
    source_paths_sv = []
    for row in design["sources"]:
        destination = attempt / "bundle" / "sources" / row["path"]
        copied = copy_stable(root / row["path"], destination)
        if copied != row["sha256"]:
            raise FlowError(f"snapshotted source SHA mismatch: {row['path']}")
        source_snapshots.append({
            "path": row["path"], "sha256": copied,
            "origin": design["source_origin"],
        })
        if destination.suffix == ".v":
            source_paths_v.append(str(destination))
        elif destination.suffix == ".sv":
            source_paths_sv.append(str(destination))
        else:
            raise FlowError(f"unsupported HDL source suffix: {row['path']}")
    for row in design["include_files"]:
        destination = attempt / "bundle" / "sources" / row["path"]
        copied = copy_stable(root / row["path"], destination)
        if copied != row["sha256"]:
            raise FlowError(f"snapshotted include SHA mismatch: {row['path']}")
        include_snapshots.append({
            "path": row["path"], "sha256": copied,
            "origin": design["source_origin"],
        })
    sdc = materialize_sdc(root, design)
    sdc_path = attempt / "bundle" / "constraints.sdc"
    write_exclusive(sdc_path, sdc, 0o444)
    tcl_snapshot = attempt / "bundle" / "genus_driver.tcl"
    tcl_hash = copy_stable(DRIVER_TCL, tcl_snapshot)
    registry_hash = sha256_bytes(stable_read(REGISTRY))

    attempt_document = {
        "schema": "k2_w2_genus_exact_three_endpoint_attempt_v3",
        "attempt": attempt_name,
        "design": design_key,
        "top": design["top"],
        "boundary_cohort": design["boundary_cohort"],
        "source_origin": design["source_origin"],
        "ranking_policy": registry["ranking_policy"],
        "flow_git_head": head,
        "source_commit": registry["repository_commit"],
        "registry_sha256": registry_hash,
        "staged_manifest": registry["staged_manifest_identity"],
        "technology_authorities": registry["technology_authority_identities"],
        "proven_environment": environment_identity,
        "flow_files_sha256": flow_files,
        "evidence_cohorts": {
            "raw_reference": raw_golden_identity,
            "buffered_reference": golden_identity,
            "goal_execution": {
                "cohort": design["boundary_cohort"],
                "design": design_key,
                "top": design["top"],
                "source_origin": design["source_origin"],
                "source_commit": registry["repository_commit"],
                "ranking_policy": registry["ranking_policy"],
            },
            "functional_loss_reference": functional_loss_identity,
        },
        "filelist_path": design["filelist"],
        "filelist_sha256": design["filelist_sha256"],
        "sources": source_snapshots,
        "include_files": include_snapshots,
        "include_dirs": design["include_dirs"],
        "defines": design["defines"],
        "parameters": design["parameters"],
        "constraints_sha256": sha256_bytes(sdc),
        "library_source_sha256": library_source_hash,
        "library_snapshot_sha256": library_source_hash,
        "hold_library_sha256": hold_library_hash,
        "cell_lef_sha256": cell_lef_hash,
        "shared_typical_qrc_sha256": shared_qrc_hash,
        "dffnsrx1_setup_preflight": dff_setup,
        "dffnsrx1_hold_preflight": dff_hold,
        "strict_sdc": design["strict_sdc"],
        "mmmc_template": registry["mmmc_template_identity"],
        "genus": tool_before,
        "driver_tcl_sha256": tcl_hash,
        "genus_command": [tool_before["resolved_path"], "-batch", "-files",
                          "bundle/genus_driver.tcl"],
        "clock_gating_insertion": True,
        "scan_mapping": False,
    }
    write_exclusive(attempt / "attempt.json", canonical(attempt_document))

    environment = os.environ.copy()
    environment.update({
        "W2_TOP": design["top"],
        "W2_SOURCES_V": " ".join("{" + path + "}" for path in source_paths_v),
        "W2_SOURCES_SV": " ".join("{" + path + "}" for path in source_paths_sv),
        "W2_DEFINES": " ".join(design["defines"]),
        "W2_INCDIRS": " ".join(
            "{" + str(attempt / "bundle" / "sources" / path) + "}"
            for path in design["include_dirs"]),
        "W2_LIBRARY": str(library_snapshot),
        "W2_SDC": str(sdc_path),
        "W2_OUTPUT": str(attempt / "work"),
        "W2_RX_CELL": design["mapped_rx_contract"]["cell"],
        "W2_RX_EXACT_INSTANCES": str(design["mapped_rx_contract"]["exact_instances"]),
        "W2_RX_CLOCK_NET": design["mapped_rx_contract"]["clock_net"],
        "W2_POS_CELL": design["mapped_posedge_contract"]["cell"],
        "W2_POS_EXACT_INSTANCES": str(
            design["mapped_posedge_contract"]["exact_instances"]),
        "W2_POS_CLOCK_NET": design["mapped_posedge_contract"]["clock_net"],
        "W2_ENDPOINT_INVENTORY": ",".join(
            f"{cell}={count}" for cell, count in sorted(
                design["endpoint_expected_inventory"].items())),
        "W2_ENDPOINT_ROOTS": ",".join(design["endpoint_link_roots"]),
    })
    environment.update(registry["strict_timing_environment"])
    run = subprocess.run(
        [tool_before["resolved_path"], "-batch", "-files", str(tcl_snapshot)],
        cwd=attempt, env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    write_exclusive(attempt / "logs" / "genus.log", run.stdout)
    if run.returncode:
        raise FlowError(f"Genus exited nonzero: {run.returncode}")
    tool_after = tool_identity(genus)
    if tool_after != tool_before:
        raise FlowError("Genus executable/version changed during execution")
    if sha256_bytes(stable_read(library.resolve(strict=True))) != library_source_hash:
        raise FlowError("source library changed during execution")
    if (sha256_bytes(stable_read(hold_library.resolve(strict=True))) != hold_library_hash or
            sha256_bytes(stable_read(cell_lef.resolve(strict=True))) != cell_lef_hash or
            sha256_bytes(stable_read(shared_qrc.resolve(strict=True))) != shared_qrc_hash):
        raise FlowError("hold library/LEF/QRC changed during execution")
    report_hashes = verify_reports(
        attempt / "work", design["top"], run.stdout, golden["genus_version"])
    sdf_path = attempt / "work" / f"{design['top']}.sdf"
    sdf_payload = stable_read(sdf_path)
    try:
        sdf_text = sdf_payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise FlowError("mapped SDF is not UTF-8 text") from error
    if (not sdf_payload or "(DELAYFILE" not in sdf_text or
            design["top"] not in sdf_text):
        raise FlowError("mapped SDF is empty, truncated, or bound to the wrong top")
    sdf_hash = sha256_bytes(sdf_payload)
    inventory = mapped_inventory(
        attempt / "work" / f"{design['top']}_netlist.v",
        library_snapshot, design["top"], design["mapped_rx_contract"],
        design["mapped_posedge_contract"],
        design["endpoint_expected_inventory"],
        design["endpoint_link_roots"],
        design["endpoint_preserved_name_prefixes"])
    endpoint_instances = inventory.pop("endpoint_instances")
    no_other_negedge = inventory["dffnsrx1_global_exclusivity_proven"]
    if no_other_negedge is not design["no_other_negedge_state_proven"]:
        raise FlowError("whole-top DFFNS inventory contradicts staged endpoint contract")
    endpoint_map = {
        "schema": "k2_w2_endpoint_connectivity_map_v1",
        "design": design_key,
        "top": design["top"],
        "mapped_netlist_sha256": inventory["mapped_netlist_sha256"],
        "endpoint_link_roots": design["endpoint_link_roots"],
        "preserved_name_prefixes": design["endpoint_preserved_name_prefixes"],
        "leaf_counts": design["endpoint_expected_inventory"],
        "no_other_negedge_state_proven": no_other_negedge,
        "instances": endpoint_instances,
    }
    endpoint_map_path = attempt / "endpoint-connectivity-map.json"
    write_exclusive(endpoint_map_path, canonical(endpoint_map))
    endpoint_map_hash = sha256_bytes(stable_read(endpoint_map_path))
    mapped_sdc_hash = sha256_bytes(stable_read(
        attempt / "work" / f"{design['top']}_out.sdc"))
    functional_gate, functional_gate_hash = run_mapped_functional_gate(
        functional_hook, attempt, design_key, design,
        attempt / "work" / f"{design['top']}_netlist.v", sdf_path,
        functional_models, source_snapshots, proven_environment["xrun"],
    )
    handoff = {
        "schema": "k2_w2_innovus_strict_sdc_handoff_v1",
        "design": design_key,
        "top": design["top"],
        "mapped_netlist_sha256": inventory["mapped_netlist_sha256"],
        "mapped_sdf_sha256": sdf_hash,
        "mapped_sdc_sha256": mapped_sdc_hash,
        "strict_input_sdc_path": design["strict_sdc"]["path"],
        "strict_input_sdc_sha256": sha256_bytes(sdc),
        "mmmc_template": registry["mmmc_template_identity"],
        "setup_liberty_sha256": library_source_hash,
        "hold_liberty_sha256": hold_library_hash,
        "cell_lef_sha256": cell_lef_hash,
        "shared_setup_hold_qrc_sha256": shared_qrc_hash,
        "shared_qrc_limitation": "ONE_TYPICAL_GPDK045_TCH_FOR_SETUP_AND_HOLD",
        "innovus_consumption_status": "PENDING_REQUIRES_EXACT_HASH_RECEIPT",
    }
    handoff_path = attempt / "innovus-handoff.json"
    write_exclusive(handoff_path, canonical(handoff))
    receipt = {
        "schema": "k2_w2_genus_exact_three_endpoint_receipt_v3",
        "status": "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD",
        "design": design_key,
        "top": design["top"],
        "boundary_cohort": design["boundary_cohort"],
        "source_origin": design["source_origin"],
        "ranking_policy": registry["ranking_policy"],
        "attempt_sha256": sha256_bytes(stable_read(attempt / "attempt.json")),
        "staged_manifest": registry["staged_manifest_identity"],
        "technology_authorities": registry["technology_authority_identities"],
        "evidence_cohorts": {
            "raw_reference": raw_golden_identity,
            "buffered_reference": golden_identity,
            "goal_execution": {
                "cohort": design["boundary_cohort"],
                "design": design_key,
                "top": design["top"],
                "source_origin": design["source_origin"],
                "source_commit": registry["repository_commit"],
                "ranking_policy": registry["ranking_policy"],
            },
            "functional_loss_reference": functional_loss_identity,
        },
        "mapped_inventory": inventory,
        "endpoint_leaf_inventory": {
            "connectivity_map_sha256": endpoint_map_hash,
            "preserved_name_prefixes": design[
                "endpoint_preserved_name_prefixes"],
            "leaf_counts": design["endpoint_expected_inventory"],
            "no_other_negedge_state_proven": no_other_negedge,
        },
        "mapped_sdf_sha256": sdf_hash,
        "mapped_sdc_sha256": mapped_sdc_hash,
        "report_sha256": report_hashes,
        "mapped_functional_gate_sha256": functional_gate_hash,
        "innovus_handoff_sha256": sha256_bytes(stable_read(handoff_path)),
        "strict_sdc_sha256": sha256_bytes(sdc),
        "dffnsrx1_preflight": {"setup": dff_setup, "hold": dff_hold},
        "checks": {
            "source_and_filelist_hashes": "PASS",
            "authoritative_ganghee_archive": "PASS_EXACT_SHA_AND_ANCHORS",
            "authoritative_raw_ganghee_archive": "PASS_EXACT_SHA_AND_ANCHORS",
            "goal_registry": "PASS_EXACT_THREE_TECH_STAGED_COMPOSITIONS",
            "generic_or_native_top_substitution": "FORBIDDEN",
            "non_link_output_boundary": "PASS_IDENTICAL_EXACT",
            "r1_vs_p6_link_width": "PASS_INHERENT_3_BITS_VS_6_BITS",
            "diagnostic_registries_used_for_final_ranking": "FORBIDDEN",
            "functional_loss_reference": "PASS_NON_OFFICIAL_WORKSPACE_DIFF_LOSS_ONLY",
            "functional_loss_used_for_ppa": "FORBIDDEN",
            "exclusive_attempt_namespace": "PASS",
            "tool_and_library_pre_post_stability": "PASS",
            "unresolved_and_blackbox": "PASS_ZERO",
            "scan_cells": "PASS_ZERO",
            "mapped_netlist_export": "PASS",
            "strict_multiclock_sdc": "PASS_HASH_BOUND_RISE_FALL_MIN_MAX_RESET_GATING_PULSE_IO",
            "dffnsrx1_rx_mapping": "PASS_EXACT_COUNT_PINS_AND_NONZERO_RECOVERY_REMOVAL",
            "innovus_exact_sdc_consumption": "PENDING_REQUIRES_DOWNSTREAM_RECEIPT",
            "power_activity_gate": "HOLD_VECTORLESS_IS_NOT_ACTIVITY_QUALIFIED",
            "mapped_functional_gate": "PASS_STAGED_VS_MAPPED_SDF_VENDOR_MODELS",
            "report_only_publication": "REJECTED_REQUIRES_SOURCE_TOOL_NETLIST_SDC_CONNECTIVITY_FUNCTIONAL_GATE",
        },
        "claim_boundary": "GENUS_MAPPED_TIMING_SCREENING_ONLY_POWER_AND_PHYSICAL_PPA_HOLD",
    }
    receipt_path = attempt / "receipt.json"
    write_exclusive(receipt_path, canonical(receipt))
    return receipt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--design", required=True)
    parser.add_argument("--genus", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--hold-library", type=Path, required=True)
    parser.add_argument("--cell-lef", type=Path, required=True)
    parser.add_argument("--shared-qrc", type=Path, required=True)
    parser.add_argument("--golden-archive", type=Path, required=True)
    parser.add_argument("--raw-golden-archive", type=Path, required=True)
    parser.add_argument("--functional-loss-archive", type=Path, required=True)
    parser.add_argument("--server-environment-receipt", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--mapped-functional-hook", type=Path, required=True)
    parser.add_argument("--functional-model", type=Path, action="append",
                        required=True)
    args = parser.parse_args(argv)
    try:
        receipt = run_flow(
            args.repo_root, args.design, args.genus, args.library,
            args.hold_library, args.cell_lef, args.shared_qrc,
            args.output_root, args.attempt, args.mapped_functional_hook,
            args.functional_model,
            args.golden_archive, args.raw_golden_archive,
            args.functional_loss_archive,
            args.server_environment_receipt,
        )
    except (FlowError, OSError, subprocess.SubprocessError) as error:
        print(f"K2_W2_GENUS_FAIL {error}", file=sys.stderr)
        return 2
    print(f"K2_W2_GENUS_PASS receipt={receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
