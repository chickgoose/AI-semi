#!/usr/bin/env python3
"""Fail-closed staging and evidence gate for REDRED single-edge endpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT = HERE / "contract.json"
VECTORLESS_CONTRACT = ROOT / "physical/k2_single_edge_vectorless/contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MODULE = re.compile(r"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b")
BAD_LOG = re.compile(
    r"(?mi)^\s*(?:ERROR|FATAL)\s*[:\[]|\*\*(?:ERROR|FATAL):|"
    r"SEG(?:MENTATION)?\s+FAULT|INTERRUPT|K2_SINGLE_EDGE_(?:GENUS|INNOVUS)_FATAL"
)
NONZERO_DIAGNOSTIC = re.compile(
    r"(?i)\b(?:errors?|fatals?)(?:\s+count)?\s*[:=]\s*[1-9][0-9]*\b|"
    r"\b[1-9][0-9]*\s+(?:errors?|fatals?)(?:\(s\))?(?![A-Za-z0-9_])"
)
RTL_SOURCE_COMMIT = "eb298fe1416a4312269a6f9232e1445f8958dda2"
RTL_INTEGRATION_COMMIT = "eb298fe1416a4312269a6f9232e1445f8958dda2"
RTL_ROWS = {
    "a2": {
        "top": "a2_batched_iwrr_single_edge_top",
        "filelist_path": "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge.f",
        "filelist_sha256": "55d6c15e33147a3362dedfddccb0ff022e47401eeb0d8388a8dd30e5d9ca1e76",
        "entries": [
            "rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv",
            "-f rtl/technology/single_edge/filelists/generic.f",
            "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv",
        ],
        "sources": [
            ("rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv", "f8d07af94db7fc03eb21583e29180708991d827dbed5602b305b38ab8ae6f66e"),
            ("rtl/technology/single_edge/w2_single_edge_error_latch.sv", "02729b04c8326bd898a465a5343eb34b40a7c60c3667f6d0bb16eb3fcdb83260"),
            ("rtl/technology/single_edge/w2_single_edge_pair_tx.sv", "e00ac30015e826cef7d017b0a72066e405bce3e84a4ee454e99fb34c68e2642c"),
            ("rtl/technology/single_edge/w2_single_edge_pair_rx.sv", "c6ebefc560e158d4ffa4d1ac340c1c1b65d8caafbe2c1a8957fadbea3b7e59a5"),
            ("rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv", "8fb80462a84929813965b9740628ae396ce6a8ebbf5f26a96e67d7ee926a8127"),
            ("rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv", "88f70140358cbfb678b71b2341e5102e982b3b8b8ce4f04d32d0504b2a44f39a"),
        ],
        "modules": [
            "a2_batched_iwrr_k2", "w2_single_edge_error_latch",
            "w2_single_edge_pair_tx", "w2_single_edge_pair_rx",
            "w2_single_edge_exact_pair_endpoint",
            "a2_batched_iwrr_single_edge_top",
        ],
    },
    "a3": {
        "top": "a3_exact_scalar_prefix_k2_single_edge_top",
        "filelist_path": "rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge.f",
        "filelist_sha256": "1fcf350a51ae32008ba207b5e1406d71e4a3083ffc52193e379f65eb1b623fee",
        "entries": [
            "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv",
            "-f rtl/technology/single_edge/filelists/generic.f",
            "rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge_top.sv",
        ],
        "sources": [
            ("rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv", "a1898ff0d142584507b7a78571724e8a7fc9a02d64ad1dbf519dde6942cfef22"),
            ("rtl/technology/single_edge/w2_single_edge_error_latch.sv", "02729b04c8326bd898a465a5343eb34b40a7c60c3667f6d0bb16eb3fcdb83260"),
            ("rtl/technology/single_edge/w2_single_edge_pair_tx.sv", "e00ac30015e826cef7d017b0a72066e405bce3e84a4ee454e99fb34c68e2642c"),
            ("rtl/technology/single_edge/w2_single_edge_pair_rx.sv", "c6ebefc560e158d4ffa4d1ac340c1c1b65d8caafbe2c1a8957fadbea3b7e59a5"),
            ("rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv", "8fb80462a84929813965b9740628ae396ce6a8ebbf5f26a96e67d7ee926a8127"),
            ("rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge_top.sv", "61daf3a31f29106d3f6383936d92131a31401fd86d71e0bee5ee53a3ab5b485d"),
        ],
        "modules": [
            "a3_exact_scalar_prefix_k2", "w2_single_edge_error_latch",
            "w2_single_edge_pair_tx", "w2_single_edge_pair_rx",
            "w2_single_edge_exact_pair_endpoint",
            "a3_exact_scalar_prefix_k2_single_edge_top",
        ],
    },
}
GENERIC_FILELIST = {
    "path": "rtl/technology/single_edge/filelists/generic.f",
    "sha256": "8445fd6785966a09d6c8dc9b1cdef14787de7494bd0c7824fd524a08df176c2e",
    "entries": [
        "rtl/technology/single_edge/w2_single_edge_error_latch.sv",
        "rtl/technology/single_edge/w2_single_edge_pair_tx.sv",
        "rtl/technology/single_edge/w2_single_edge_pair_rx.sv",
        "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv",
    ],
}
ENV_ALLOWLIST_KEYS = ("CDS_AUTO_64BIT", "CDS_LIC_FILE", "LANG", "LC_ALL",
                      "LD_LIBRARY_PATH", "LM_LICENSE_FILE", "LOGNAME", "PATH",
                      "SHELL", "USER")
TEMPLATE_IDENTITIES = {
    "genus": ("physical/k2_single_edge_endpoint/genus_single_edge.tcl",
              "c0bd75a10116bf3f036ec813aa4e48f02a20b10d229672a23eaa911e4dea2033"),
    "innovus_mmmc": ("physical/k2_single_edge_endpoint/innovus_mmmc_single_edge.tcl",
                     "425fed71eeb06b39ed2f598eca8f9b938d67e9c140cc6930e2f65e6b087d92e9"),
    "innovus": ("physical/k2_single_edge_endpoint/innovus_single_edge.tcl",
                "f89071b94e9e11d33fdacca13f96b1bb27e1ea5a6393d8fe987ae99f4a138b56")}


class FlowError(RuntimeError):
    pass


class MissingEvidence(FlowError):
    pass


def has_failure_diagnostic(text: str) -> bool:
    """Recognize native and prose failure spellings without rejecting zero summaries."""
    if BAD_LOG.search(text) or NONZERO_DIAGNOSTIC.search(text):
        return True
    without_zero_counts = re.sub(
        r"(?i)\b(?:errors?|fatals?)(?:\s+count)?\s*[:=]\s*0\b|"
        r"\b0\s+(?:errors?|fatals?)(?:\(s\))?(?![A-Za-z0-9_])", "", text)
    return re.search(r"(?i)\b(?:errors?|fatals?)\b", without_zero_counts) is not None


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path, *, allow_tool_symlink: bool = False) -> bytes:
    try:
        before_lstat = path.lstat()
    except FileNotFoundError as error:
        raise MissingEvidence(f"missing regular artifact: {path}") from error
    lexical_path = path
    if stat.S_ISLNK(before_lstat.st_mode):
        if not allow_tool_symlink:
            raise FlowError(f"symlink is forbidden: {path}")
        path = path.resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not allow_tool_symlink and (before_lstat.st_dev, before_lstat.st_ino,
                stat.S_IFMT(before_lstat.st_mode)) != (before.st_dev, before.st_ino,
                stat.S_IFMT(before.st_mode)):
            raise FlowError(f"file identity changed before open: {lexical_path}")
        if not stat.S_ISREG(before.st_mode):
            raise FlowError(f"not a regular file: {path}")
        if not allow_tool_symlink and before.st_nlink != 1:
            raise FlowError(f"artifact must have exactly one hard link: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size,
                                row.st_mtime_ns, row.st_ctime_ns)
        if identity(before) != identity(after):
            raise FlowError(f"file changed while read: {path}")
        final_lstat = lexical_path.lstat()
        if not allow_tool_symlink and (final_lstat.st_dev, final_lstat.st_ino,
                stat.S_IFMT(final_lstat.st_mode), final_lstat.st_nlink) != (
                before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode), before.st_nlink):
            raise FlowError(f"file identity changed during read: {lexical_path}")
        payload = b"".join(chunks)
        if not payload:
            raise FlowError(f"empty file is forbidden: {path}")
        return payload
    finally:
        os.close(descriptor)


def load_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    payload = stable_read(path)
    try:
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise FlowError(f"duplicate key in {label}: {key}")
                result[key] = value
            return result
        document = json.loads(payload, object_pairs_hook=unique,
                              parse_constant=lambda value: (_ for _ in ()).throw(
                                  FlowError(f"non-finite JSON value in {label}: {value}")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FlowError(f"invalid {label} JSON: {error}") from error
    if not isinstance(document, dict):
        raise FlowError(f"{label} must be a JSON object")
    return payload, document


def seal(document: dict[str, Any]) -> dict[str, Any]:
    if "document_sha256" in document:
        raise FlowError("cannot seal a document twice")
    result = dict(document)
    result["document_sha256"] = sha256(canonical(document))
    return result


def verify_seal(document: dict[str, Any], label: str) -> None:
    recorded = document.get("document_sha256")
    unsigned = dict(document)
    unsigned.pop("document_sha256", None)
    if not isinstance(recorded, str) or not SHA256.fullmatch(recorded) or \
            sha256(canonical(unsigned)) != recorded:
        raise FlowError(f"{label} self-hash mismatch")


def reject_symlink_ancestors(path: Path, *, include_final: bool = False) -> None:
    absolute = path.absolute()
    parts = absolute.parts if include_final else absolute.parts[:-1]
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        if current.exists() and current.is_symlink():
            raise FlowError(f"path contains symlink ancestor: {current}")


def write_exclusive(path: Path, document: dict[str, Any]) -> None:
    reject_symlink_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o444)
    payload = canonical(document)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def repo_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/"):
        raise FlowError("repository path must be nonempty and relative")
    path = ROOT / raw
    current = ROOT
    for part in Path(raw).parts:
        if part in {"", ".", ".."}:
            raise FlowError(f"repository path is not normalized: {raw}")
        current = current / part
        if current.exists() and current.is_symlink():
            raise FlowError(f"repository path contains symlink: {raw}")
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as error:
        raise FlowError(f"repository path escapes root: {raw}") from error
    return path


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise FlowError(f"{label} fields differ: {sorted(set(value) ^ expected)}")


def contains_forbidden(text: str, tokens: Iterable[str]) -> bool:
    """Match tokens as path/identifier components, not the 'ddr' in 'addr'."""
    return any(re.search(
        rf"(?i)(?:^|[^A-Za-z0-9]){re.escape(token)}(?:$|[^A-Za-z0-9])", text)
        for token in tokens)


def optional_exact_file(raw: str, digest: str,
                        entries: list[str] | None = None) -> bool:
    path = repo_path(raw)
    if not path.exists():
        return False
    payload = stable_read(path)
    if sha256(payload) != digest:
        raise FlowError(f"committed RTL byte hash mismatch: {raw}")
    if entries is not None and payload.decode("utf-8").splitlines() != entries:
        raise FlowError(f"committed filelist content mismatch: {raw}")
    return True


def validate_contract() -> tuple[bytes, dict[str, Any]]:
    payload, contract = load_json(CONTRACT, "contract")
    if contract.get("schema") != "k2_single_edge_endpoint_physical_contract_v1" or \
            contract.get("status") != "STATIC_READY_CANDIDATE_PHYSICAL_HOLD":
        raise FlowError("contract schema/status mismatch")
    if contract.get("candidate_order") != ["a2", "a3"] or \
            set(contract.get("candidates", {})) != {"a2", "a3"}:
        raise FlowError("candidate set/order must be exactly A2 then A3")
    boundary = contract.get("boundary", {})
    if boundary.get("kind") != \
            "COMPLETE_SOURCE_ADMISSION_THROUGH_SYNCHRONOUS_RETIREMENT" or \
            boundary.get("clock_port") != "clk_i" or \
            boundary.get("clocking") != \
            "ONE_PRIMARY_POSEDGE_CLOCK_NO_GENERATED_OR_NEGEDGE_CLOCKS":
        raise FlowError("complete single-clock boundary contract mismatch")
    expected_inputs = [
        {"name": "clk_i", "width": 1}, {"name": "rst_i", "width": 1},
        {"name": "link_enable_i", "width": 1},
        {"name": "source_pending_i", "width": 16},
    ]
    expected_outputs = [
        {"name": "source_accept_o", "width": 16},
        {"name": "accept_count_o", "width": 2},
        {"name": "accept_addr0_o", "width": 4},
        {"name": "accept_addr1_o", "width": 4},
        {"name": "link_valid_o", "width": 1},
        {"name": "link_addr0_o", "width": 4},
        {"name": "link_addr1_o", "width": 4},
        {"name": "retire_valid_o", "width": 2},
        {"name": "retire_addr0_o", "width": 4},
        {"name": "retire_addr1_o", "width": 4},
        {"name": "protocol_error_o", "width": 1},
        {"name": "drain_idle_o", "width": 1},
    ]
    if (boundary.get("reset_port") != "rst_i" or
            boundary.get("reset_semantics") != "SYNCHRONOUS_ACTIVE_HIGH" or
            boundary.get("normalized_ports") != {
                "inputs": expected_inputs, "outputs": expected_outputs}):
        raise FlowError("normalized complete-boundary port/reset contract mismatch")
    if contract.get("rtl_authority") != {
            "source_commit": RTL_SOURCE_COMMIT,
            "integration_commit": RTL_INTEGRATION_COMMIT,
            "identical_rtl_tree": "21afcca7052889d953a4801531f9f9c31b3c3be5",
            "subject": "rtl: shorten A2 two-grant timing cone",
            "policy": "EXACT_COMMITTED_FILELIST_AND_EXPANDED_SOURCE_BYTES"}:
        raise FlowError("single-edge RTL authority commit mismatch")
    forbidden = [token.lower() for token in
                 contract.get("source_policy", {}).get(
                     "forbidden_case_insensitive_tokens", [])]
    if set(forbidden) != {"p6", "a7_p6", "multiclock", "ddr"}:
        raise FlowError("source exclusion token contract mismatch")
    if contract["source_policy"].get(
            "candidate_and_nested_filelists_must_equal_declared_entries_in_order") is not True:
        raise FlowError("exact filelist-order policy was weakened")
    if contract["source_policy"].get("synthesis_defines") != ["SYNTHESIS"]:
        raise FlowError("exact synthesis define policy was weakened")
    for name in contract["candidate_order"]:
        row = contract["candidates"][name]
        expected = RTL_ROWS[name]
        filelist = row.get("filelist", {})
        expected_sources = [{"path": path, "sha256": digest}
                            for path, digest in expected["sources"]]
        if (row.get("top") != expected["top"] or
                filelist != {"path": expected["filelist_path"],
                             "sha256": expected["filelist_sha256"],
                             "entries": expected["entries"]} or
                row.get("expanded_sources") != expected_sources or
                row.get("module_inventory") != expected["modules"]):
            raise FlowError(f"{name} exact top/filelist mismatch")
        if len(expected_sources) != 6 or len({item["path"] for item in expected_sources}) != 6:
            raise FlowError(f"{name} expanded source set is not exact")
        joined = "\n".join(filelist["entries"])
        if contains_forbidden(joined, forbidden):
            raise FlowError(f"{name} filelist borrows forbidden multi-edge material")
        for identity in expected_sources:
            source = identity["path"]
            if source.startswith("/") or ".." in Path(source).parts or any(
                    source.startswith(root) for root in
                    contract["source_policy"]["forbidden_source_roots"]):
                raise FlowError(f"unsafe or forbidden source path: {source}")
        optional_exact_file(filelist["path"], filelist["sha256"], filelist["entries"])
    generic = contract["source_policy"].get("nested_generic_filelist")
    if generic != GENERIC_FILELIST:
        raise FlowError("nested single-edge generic filelist authority mismatch")
    optional_exact_file(generic["path"], generic["sha256"], generic["entries"])
    constraints = contract.get("constraints", {})
    exact_values = {
        "SE_CONSTRAINT_CLASS": "TEAM_PLACEHOLDER_SCREENING_ONLY",
        "SE_PERIOD_NS": "6.5", "SE_CLOCK_UNCERTAINTY_NS": "0.25",
        "SE_INPUT_DELAY_MIN_NS": "0.10", "SE_INPUT_DELAY_MAX_NS": "0.50",
        "SE_OUTPUT_DELAY_MIN_NS": "0.10", "SE_OUTPUT_DELAY_MAX_NS": "0.50",
        "SE_INPUT_TRANSITION_NS": "0.05", "SE_OUTPUT_LOAD_PF": "0.01",
        "SE_MIN_PULSE_HIGH_NS": "0.50", "SE_MIN_PULSE_LOW_NS": "0.50"}
    if constraints.get("authority_status") != "UNCONFIRMED_TEAM_PLACEHOLDER" or \
            constraints.get("evidence_class") != "TEAM_PLACEHOLDER_SCREENING_ONLY" or \
            constraints.get("candidate_go_eligible") is not False or \
            constraints.get("values") != exact_values or \
            constraints.get("sdc_sha256") != \
            "fe7a4456dfe18148ac1386d1ce63a6c93c0b50c85e02fd25020eae5ea06c2ae4" or \
            constraints.get("prohibited_claims") != [
                "organizer_required_operating_point", "pad_or_package_loading",
                "silicon_signoff", "fmax", "interface_legality"] or \
            constraints.get("promotion_rule") != \
            "A NEW REVIEWED CONTRACT WITH EXTERNAL CONSTRAINT AUTHORITY IS REQUIRED; A RECEIPT OR COMMAND-LINE OVERRIDE CANNOT PROMOTE THESE VALUES":
        raise FlowError("placeholder constraint classification weakened")
    qualification = contract.get("qualification", {})
    if qualification.get("maximum_decision_under_this_contract") != \
            "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE" or \
            qualification.get("candidate_physical_go_possible") is not False or \
            qualification.get("exact_native_report_context_required") is not True or \
            qualification.get("exact_mapped_sdc_command_set_required") is not True or \
            qualification.get("exact_netlist_boundary_required") is not True or \
            qualification.get("diagnostic_only_vocabulary_required") is not True or \
            qualification.get("bounded_eco_receipts_required") is not True or \
            qualification.get("bounded_eco_pre_setup_hold_handoff_allowed") is not True or \
            qualification.get("bounded_eco_setup_recovery_and_final_hold_closed_required") is not True or \
            qualification.get("final_setup_hold_zero_and_receipt_match_required") is not True or \
            qualification.get("final_timing_must_match_post_eco_state") is not True or \
            qualification.get("cohort_same_environment_snapshot_hash_required") is not True or \
            qualification.get("cohort_freshness_authority_available") is not False:
        raise FlowError("contract no longer fails closed on candidate GO")
    if contract.get("execution_policy") != {
            "explicit_authorization": {
                "genus": "I_UNDERSTAND_THIS_LAUNCHES_REAL_GENUS",
                "innovus": "I_UNDERSTAND_THIS_LAUNCHES_REAL_INNOVUS",
            },
            "exclusive_no_overwrite_stage_directories": True,
            "innovus_requires_successful_bound_genus_receipt": True,
            "execution_receipt_schema": "k2_single_edge_execution_receipt_v3",
            "exit_zero_artifact_evidence_classification":
                "BOUND_COMPLETE_EXIT_ZERO_STAGE_MANIFEST",
            "nonzero_artifact_evidence_classification":
                "UNBOUND_NONZERO_EXIT_FILES_NOT_LEDGER_ELIGIBLE",
            "local_tests_must_not_invoke_real_tools": True,
            }:
        raise FlowError("real-tool execution policy mismatch")
    expected_eco = {
        "schema": "k2_single_edge_eco_iteration_receipt_v1",
        "monotonic_epsilon_ns": 0.000001,
        "final_hold_target_slack_ns": 0.005,
        "phases": [
            {"role": "eco_hold_pre_setup",
             "path": "innovus/reports/eco_hold_pre_setup.machine",
             "phase": "pre_setup_hold", "view": "se_hold_view", "check": "hold",
             "optimizer": "postRoute_hold", "allow_setup_tns_degrade": "true",
             "max_iterations": 3,
             "allowed_statuses": ["CLOSED", "STALLED", "EXHAUSTED"]},
            {"role": "eco_setup_recovery",
             "path": "innovus/reports/eco_setup_recovery.machine",
             "phase": "setup_recovery", "view": "se_setup_view", "check": "setup",
             "optimizer": "postRoute", "allow_setup_tns_degrade": "NA",
             "max_iterations": 6, "allowed_statuses": ["CLOSED"]},
            {"role": "eco_hold_final",
             "path": "innovus/reports/eco_hold_final.machine",
             "phase": "final_hold_reclosure", "view": "se_hold_view", "check": "hold",
             "optimizer": "postRoute_hold", "allow_setup_tns_degrade": "true",
             "max_iterations": 3, "allowed_statuses": ["CLOSED"]},
        ],
        "pre_setup_hold_handoff_statuses": ["CLOSED", "STALLED", "EXHAUSTED"],
        "required_closed_phases": ["eco_setup_recovery", "eco_hold_final"],
        "final_timing_receipt": {
            "role": "final_timing_receipt",
            "path": "innovus/reports/final_timing_receipt.machine",
            "schema": "k2_single_edge_final_timing_receipt_v1",
        },
        "final_setup_and_hold_remeasured_after_last_eco": True,
    }
    if contract.get("bounded_eco") != expected_eco:
        raise FlowError("bounded 3/6/3 ECO contract mismatch")
    sdc_payload = stable_read(repo_path(constraints["sdc"]))
    if sha256(sdc_payload) != constraints["sdc_sha256"]:
        raise FlowError("placeholder SDC byte identity mismatch")
    sdc = sdc_payload.decode("utf-8")
    required_sdc = (
        "TEAM_PLACEHOLDER_SCREENING_ONLY", "create_clock -name se_primary_clk",
        "get_ports clk_i", "set_input_delay", "set_output_delay", "set_load",
        "set_input_transition", "set_min_pulse_width -high",
        "set_min_pulse_width -low", "get_clocks *",
    )
    forbidden_sdc = r"(?mi)^\s*set_(?:false_path|multicycle_path|case_analysis|disable_timing|clock_groups|max_delay|min_delay)\b"
    if any(item not in sdc for item in required_sdc) or \
            sdc.count("create_clock ") != 1 or "create_generated_clock" in sdc or \
            re.search(r"(?i)(negedge|falling_edge|-waveform\s+[^\n]*-)", sdc) or \
            re.search(forbidden_sdc, sdc):
        raise FlowError("SDC is not the exact one-primary-posedge placeholder form")
    tools = contract.get("tools", {})
    for name, version in (("genus", "23.14-s090_1"), ("innovus", "23.14-s088_1")):
        row = tools.get(name, {})
        if row.get("version") != version or not SHA256.fullmatch(str(row.get("sha256", ""))):
            raise FlowError(f"{name} identity is not pinned")
    technology = contract.get("technology", {})
    if technology.get("name") != "GPDK045" or set(technology.get("files", {})) != {
            "setup_liberty", "hold_liberty", "tech_lef", "macro_lef", "shared_qrc"}:
        raise FlowError("GPDK045 technology set mismatch")
    physical = technology.get("physical", {})
    if physical.get("site") != "CoreSite" or physical.get("site_normalization") != \
            "REPLACE_BUFX2_WITH_BUFX4_AND_DONT_USE_BUFX2":
        raise FlowError("GPDK045 endpoint placement-site policy mismatch")
    templates = contract.get("flow_templates", {})
    expected_template_tokens = {
        "genus": ("read_hdl", "elaborate", "read_sdc", "syn_generic", "syn_map",
                  "syn_opt", "report_area", "report_timing", "report_qor",
                  "report_power", "report_clocks", "write_hdl", "write_sdc", "write_sdf"),
        "innovus_mmmc": ("create_library_set", "create_rc_corner", "create_analysis_view"),
        "innovus": ("init_design", "floorPlan", "place_opt_design", "checkPlace",
                    "clock_opt_design", "routeDesign", "extractRC", "optDesign -postRoute",
                    "ecoChangeCell", "setDontUse BUFX2", "sroute",
                    "eco_hold_pre_setup.machine", "eco_setup_recovery.machine",
                    "eco_hold_final.machine", "final_timing_receipt.machine",
                    "setOptMode -opt_hold_target_slack 0.005",
                    "report_area", "report_power", "reportRoute", "check_timing",
                    "checkDesign -all", "verifyConnectivity", "verify_drc",
                    "verify_process_antenna", "saveNetlist", "write_sdf", "rcOut", "saveDesign")}
    if set(templates) != set(expected_template_tokens):
        raise FlowError("flow template set differs")
    for name, tokens in expected_template_tokens.items():
        row = templates[name]
        exact_keys(row, {"path", "sha256"}, f"{name} template")
        expected_path, expected_sha = TEMPLATE_IDENTITIES[name]
        if row != {"path": expected_path, "sha256": expected_sha}:
            raise FlowError(f"{name} template identity contract mismatch")
        template_payload = stable_read(repo_path(row["path"]))
        if sha256(template_payload) != row["sha256"]:
            raise FlowError(f"{name} template byte identity mismatch")
        active = "\n".join(line for line in template_payload.decode().splitlines()
                           if not line.lstrip().startswith("#"))
        if any(token not in active for token in tokens):
            raise FlowError(f"{name} template command inventory incomplete")
        if name == "genus":
            read_hdl_rows = [re.sub(r"\s+", " ", line.strip())
                             for line in active.splitlines()
                             if re.match(r"^\s*read_hdl\b", line)]
            if read_hdl_rows != [
                    "read_hdl -sv -define SYNTHESIS -f [file normalize $::env(SE_FILELIST)]"]:
                raise FlowError("Genus read_hdl synthesis define/filelist command is not exact")
        if name == "innovus":
            ordered = (
                "eco_hold_pre_setup.machine", "eco_setup_recovery.machine",
                "eco_hold_final.machine", "set setup_failed [catch",
                "set hold_failed [catch", "final_timing_receipt.machine",
                "if {[llength $diagnostic_failures] != 0}",
                "if {[llength $eco_phase_failures] != 0}",
                "K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE",
            )
            positions = []
            cursor = -1
            for token in ordered:
                cursor = active.find(token, cursor + 1)
                if cursor < 0:
                    break
                positions.append(cursor)
            if len(positions) != len(ordered):
                raise FlowError("Innovus 3/6/3 ECO/final timing gate order differs")
    artifact_ledger = contract.get("artifact_ledger", {})
    roles = artifact_ledger.get("required_roles", [])
    mandatory = {"genus_execution_receipt", "innovus_execution_receipt",
                 "eco_hold_pre_setup", "eco_setup_recovery", "eco_hold_final",
                 "final_timing_receipt",
                 "setup_timing", "hold_timing", "postroute_area", "drc", "antenna",
                 "connectivity", "pg_connectivity", "check_timing",
                 "check_design_pre_place", "check_place"}
    if len(roles) != len(set(roles)) or not mandatory.issubset(roles):
        raise FlowError("physical artifact ledger is incomplete")
    if artifact_ledger.get("timing_closure_roles") != [
            "eco_hold_pre_setup", "eco_setup_recovery", "eco_hold_final",
            "final_timing_receipt",
            "setup_timing_machine", "hold_timing_machine"]:
        raise FlowError("bounded ECO/final timing closure role set differs")
    return payload, contract


def package_inventory(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paths = [CONTRACT.relative_to(ROOT)]
    paths.append(Path(contract["constraints"]["sdc"]))
    paths.extend(Path(row["path"]) for row in contract["flow_templates"].values())
    result: dict[str, dict[str, Any]] = {}
    for relative in paths:
        payload = stable_read(ROOT / relative)
        result[str(relative)] = {"sha256": sha256(payload), "size_bytes": len(payload)}
    return result


def static_preflight(output: Path | None) -> dict[str, Any]:
    contract_payload, contract = validate_contract()
    present = {}
    for name, row in contract["candidates"].items():
        identities = [row["filelist"], contract["source_policy"]["nested_generic_filelist"],
                      *row["expanded_sources"]]
        present[name] = all(repo_path(item["path"]).is_file() and
                            not repo_path(item["path"]).is_symlink()
                            for item in identities)
    document = seal({
        "schema": "k2_single_edge_static_preflight_v1",
        "status": "PASS_STATIC_PACKAGE",
        "contract_sha256": sha256(contract_payload),
        "package_files": package_inventory(contract),
        "candidate_sources_present": present,
        "live_tools_or_pdk_examined": False,
        "artifact_bundle_examined": False,
        "candidate_physical_go_allowed": False,
        "maximum_decision": "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE",
    })
    if output is not None:
        write_exclusive(output, document)
    return document


def local_compatibility_preflight(output: Path | None) -> dict[str, Any]:
    """Prove the pinned RTL boundary is compatible with the physical package.

    This intentionally stops before live Cadence/PDK inspection.  It is the
    development-time gate that prevents a hash-clean RTL top from reaching the
    server with a different ordered port ABI than the mapped-netlist verifier.
    """
    contract_payload, contract = validate_contract()
    vectorless = validate_vectorless_compatibility(contract)
    candidates: dict[str, Any] = {}
    for design in contract["candidate_order"]:
        row = contract["candidates"][design]
        sources = source_identity(contract, design)
        candidates[design] = {
            "top": row["top"],
            "source_count": len(sources),
            "source_inventory_sha256": sha256(canonical(sources)),
            "module_inventory": list(row["module_inventory"]),
            "synthesis_defines": list(contract["source_policy"]["synthesis_defines"]),
            "exact_filelist_and_source_bytes": True,
            "exact_module_inventory": True,
            "exact_ordered_top_boundary": True,
            "single_edge_source_screening": True,
        }
    document = seal({
        "schema": "k2_single_edge_local_compatibility_preflight_v1",
        "status": "PASS_LOCAL_RTL_PHYSICAL_COMPATIBILITY",
        "contract_sha256": sha256(contract_payload),
        "candidate_order": list(contract["candidate_order"]),
        "vectorless_compatibility": vectorless,
        "candidates": candidates,
        "live_tools_or_pdk_examined": False,
        "server_genus_smoke_executed": False,
        "artifact_bundle_examined": False,
        "candidate_physical_go_allowed": False,
        "maximum_decision": "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE",
    })
    if output is not None:
        write_exclusive(output, document)
    return document


def validate_vectorless_compatibility(endpoint_contract: dict[str, Any]) -> dict[str, Any]:
    """Bind the diagnostic power flow to the exact endpoint compile ABI."""
    payload, contract = load_json(VECTORLESS_CONTRACT, "single-edge vectorless contract")
    if (contract.get("schema") != "k2_single_edge_vectorless_contract_v2" or
            contract.get("status") !=
            "DIAGNOSTIC_ONLY_PLACEHOLDER_IO_NO_CONTROLLED_PRODUCER"):
        raise FlowError("vectorless contract identity/status mismatch")
    decision = contract.get("decision_policy", {})
    if decision.get("candidate_order") != ["a2_single_edge", "a3_single_edge"]:
        raise FlowError("vectorless candidate order differs from endpoint order")

    def signature(rows: list[dict[str, Any]]) -> list[str]:
        return [row["name"] + (f"[{row['width'] - 1}:0]" if row["width"] > 1 else "")
                for row in rows]

    inputs = signature(endpoint_contract["boundary"]["normalized_ports"]["inputs"])
    outputs = signature(endpoint_contract["boundary"]["normalized_ports"]["outputs"])
    vectorless_rows = contract.get("candidates", {})
    for endpoint_name, vectorless_name in zip(
            endpoint_contract["candidate_order"], decision["candidate_order"]):
        endpoint_row = endpoint_contract["candidates"][endpoint_name]
        row = vectorless_rows.get(vectorless_name, {})
        if (row.get("architecture") != endpoint_name.upper() or
                row.get("top") != endpoint_row["top"] or
                row.get("inputs") != inputs or row.get("outputs") != outputs):
            raise FlowError(f"{vectorless_name} vectorless compile boundary differs")
    execution = contract.get("execution_policy", {})
    if execution.get("synthesis_defines") != \
            endpoint_contract["source_policy"]["synthesis_defines"]:
        raise FlowError("vectorless synthesis defines differ from endpoint flow")
    driver = contract.get("templates", {}).get("driver", {})
    driver_path = repo_path(driver.get("path"))
    driver_payload = stable_read(driver_path)
    if sha256(driver_payload) != driver.get("sha256"):
        raise FlowError("vectorless driver byte identity mismatch")
    driver_text = driver_payload.decode("utf-8")
    exact_read = "read_hdl -sv -define SYNTHESIS {*}$::env(K2_SE_SOURCES_SV)"
    if [line.strip() for line in driver_text.splitlines()].count(exact_read) != 1:
        raise FlowError("vectorless driver lacks one exact SYNTHESIS-defined RTL read")
    return {
        "contract_sha256": sha256(payload),
        "driver_sha256": sha256(driver_payload),
        "exact_candidate_order": True,
        "exact_ordered_top_boundary": True,
        "exact_synthesis_defines": list(execution["synthesis_defines"]),
        "diagnostic_hold_preserved": True,
    }


def source_identity(contract: dict[str, Any], design: str) -> list[dict[str, Any]]:
    row = contract["candidates"][design]
    if not optional_exact_file(row["filelist"]["path"], row["filelist"]["sha256"],
                               row["filelist"]["entries"]):
        raise MissingEvidence(f"missing committed candidate filelist: {row['filelist']['path']}")
    if not optional_exact_file(GENERIC_FILELIST["path"], GENERIC_FILELIST["sha256"],
                               GENERIC_FILELIST["entries"]):
        raise MissingEvidence(f"missing committed generic filelist: {GENERIC_FILELIST['path']}")
    result = []
    for declared in row["expanded_sources"]:
        raw = declared["path"]
        payload = stable_read(repo_path(raw))
        text = payload.decode("utf-8")
        if sha256(payload) != declared["sha256"]:
            raise FlowError(f"source differs from RTL authority commit: {raw}")
        if contains_forbidden(text,
                contract["source_policy"]["forbidden_case_insensitive_tokens"]):
            raise FlowError(f"source contains a forbidden multi-edge token: {raw}")
        if re.search(r"(?is)@\s*\([^)]*\bnegedge\b", text):
            raise FlowError(f"source contains negedge-triggered state: {raw}")
        result.append({"path": raw, "sha256": sha256(payload), "size_bytes": len(payload)})
    modules = []
    for identity in result:
        source_payload = stable_read(repo_path(identity["path"]))
        modules.extend(MODULE.findall(synthesis_text(
            source_payload, f"{design} SYNTHESIS source {identity['path']}")))
    if modules != row["module_inventory"] or modules.count(row["top"]) != 1:
        raise FlowError(f"exact module inventory/top definition differs: {design}")
    top_payload = stable_read(repo_path(row["expanded_sources"][-1]["path"]))
    top_text = synthesis_text(top_payload, f"{design} SYNTHESIS RTL top")
    validate_top_boundary(top_text.encode("utf-8"), row["top"], contract,
                          f"{design} SYNTHESIS RTL top")
    if re.search(r"(?i)@(\s*negedge)|always_ff\s*@\s*\([^)]*negedge\s+clk_i", top_text):
        raise FlowError("top contains negedge clocked state")
    return result


def planned_commands(contract: dict[str, Any], design: str,
                     attempt_root: Path) -> list[dict[str, Any]]:
    attempt_root = attempt_root.resolve()
    row = contract["candidates"][design]
    values = contract["constraints"]["values"]
    genus_env = {
        **values,
        "SE_TOP": row["top"],
        "SE_PROJECT_ROOT": str(ROOT.resolve()),
        "SE_FILELIST": str(repo_path(row["filelist"]["path"])),
        "SE_SDC": str(repo_path(contract["constraints"]["sdc"])),
        "SE_SETUP_LIB": str(Path(contract["technology"]["pdk_root"]) /
                            contract["technology"]["files"]["setup_liberty"]["relative_path"]),
        "SE_GENUS_OUT": str((attempt_root / "genus").resolve()),
        "TMPDIR": str((attempt_root / "genus/tmp").resolve()),
    }
    innovus_env = {
        "SE_TOP": row["top"],
        "SE_MAPPED_NETLIST": str((attempt_root / "genus/netlist" /
                                  f"{row['top']}.mapped.v").resolve()),
        "SE_MAPPED_SDC": str((attempt_root / "genus/netlist" /
                              f"{row['top']}.mapped.sdc").resolve()),
        "SE_MMMC": str(repo_path(contract["flow_templates"]["innovus_mmmc"]["path"])),
        "SE_INNOVUS_OUT": str((attempt_root / "innovus").resolve()),
        "TMPDIR": str((attempt_root / "innovus/tmp").resolve()),
    }
    role_to_env = {
        "setup_liberty": "SE_SETUP_LIB", "hold_liberty": "SE_HOLD_LIB",
        "tech_lef": "SE_TECH_LEF", "macro_lef": "SE_MACRO_LEF",
        "shared_qrc": "SE_SHARED_QRC",
    }
    for role, env_name in role_to_env.items():
        innovus_env[env_name] = str(Path(contract["technology"]["pdk_root"]) /
                                     contract["technology"]["files"][role]["relative_path"])
    physical = contract["technology"]["physical"]
    innovus_env.update({
        "SE_SITE": physical["site"], "SE_PROCESS": physical["process_node_nm"],
        "SE_ASPECT": physical["aspect_ratio"], "SE_UTIL": physical["core_utilization"],
        "SE_MARGIN": physical["core_margin_um"], "SE_VDD": physical["vdd_net"],
        "SE_VSS": physical["vss_net"], "SE_RING_H": physical["ring"]["horizontal_layer"],
        "SE_RING_V": physical["ring"]["vertical_layer"],
        "SE_RING_WIDTH": physical["ring"]["width_um"],
        "SE_RING_SPACING": physical["ring"]["spacing_um"],
        "SE_RING_OFFSET": physical["ring"]["offset_um"],
    })
    commands = []
    for stage, env in (("genus", genus_env), ("innovus", innovus_env)):
        tail = list(contract["commands"][f"{stage}_argv_tail"])
        tail[-1] = str(repo_path(tail[-1]))
        argv = [contract["tools"][stage]["entrypoint"], *tail]
        command = {"stage": stage, "argv": argv, "environment": env,
                   "cwd": str((attempt_root / f"{stage}/work").resolve())}
        command["environment_sha256"] = sha256(canonical(env))
        command["command_sha256"] = sha256(canonical(command))
        commands.append(command)
    return commands


def make_plan(design: str, attempt_root: Path, output: Path) -> dict[str, Any]:
    contract_payload, contract = validate_contract()
    if design not in contract["candidate_order"]:
        raise FlowError("unknown candidate")
    reject_symlink_ancestors(attempt_root, include_final=True)
    attempt_root = attempt_root.resolve()
    sources = source_identity(contract, design)
    row = contract["candidates"][design]
    commands = planned_commands(contract, design, attempt_root)
    document = seal({
        "schema": "k2_single_edge_command_plan_v1", "design": design,
        "top": row["top"], "contract_sha256": sha256(contract_payload),
        "attempt_root": str(attempt_root),
        "package_files": package_inventory(contract), "sources": sources,
        "commands": commands, "candidate_physical_go_allowed": False,
    })
    write_exclusive(output, document)
    return document


def tool_identity(path: Path, expected: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise MissingEvidence(f"missing real {name} executable: {path}") from error
    payload = stable_read(resolved, allow_tool_symlink=True)
    if str(path) != expected["entrypoint"] or str(resolved) != expected["resolved_path"] or \
            sha256(payload) != expected["sha256"] or not os.access(path, os.X_OK):
        raise FlowError(f"live {name} executable identity mismatch")
    result = subprocess.run([str(path), "-version"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0 or expected["version"].encode() not in result.stdout:
        raise FlowError(f"live {name} version probe failed")
    return {"entrypoint": str(path), "resolved_path": str(resolved),
            "sha256": sha256(payload), "version": expected["version"],
            "version_output_sha256": sha256(result.stdout)}


def validate_fresh_tool_identity(observed: dict[str, Any], fresh: dict[str, Any],
                                 name: str) -> None:
    """Compare stable tool identity while retaining raw probe output as provenance.

    Cadence wrappers may print a per-invocation TMPDIR UUID in ``-version`` output.
    Its raw digest therefore proves what the capture saw, but is not a stable
    executable identity and must not be required to repeat on a fresh probe.
    """
    keys = {"entrypoint", "resolved_path", "sha256", "version",
            "version_output_sha256"}
    exact_keys(fresh, keys, f"fresh {name} identity")
    if not SHA256.fullmatch(str(observed.get("version_output_sha256", ""))) or \
            not SHA256.fullmatch(str(fresh.get("version_output_sha256", ""))):
        raise FlowError(f"{name} raw version provenance hash is malformed")
    stable = ("entrypoint", "resolved_path", "sha256", "version")
    if any(observed.get(key) != fresh.get(key) for key in stable):
        raise FlowError(f"live {name} bytes/path/parsed version no longer match snapshot")


def capture_environment(pdk_root: Path, genus: Path, innovus: Path,
                        output: Path) -> dict[str, Any]:
    contract_payload, contract = validate_contract()
    tools = {
        "genus": tool_identity(genus, contract["tools"]["genus"], "Genus"),
        "innovus": tool_identity(innovus, contract["tools"]["innovus"], "Innovus"),
    }
    technology = {}
    if str(pdk_root) != contract["technology"]["pdk_root"]:
        raise FlowError("PDK root differs from pinned server root")
    for role, expected in contract["technology"]["files"].items():
        path = pdk_root / expected["relative_path"]
        payload = stable_read(path)
        if sha256(payload) != expected["sha256"]:
            raise FlowError(f"live GPDK045 identity mismatch: {role}")
        technology[role] = {"path": str(path), "sha256": sha256(payload),
                            "size_bytes": len(payload)}
    environment = {name: os.environ.get(name, "") for name in ENV_ALLOWLIST_KEYS}
    document = seal({
        "schema": "k2_single_edge_live_environment_snapshot_v2",
        "contract_sha256": sha256(contract_payload), "tools": tools,
        "technology": technology, "environment_allowlist": environment,
        "environment_allowlist_sha256": sha256(canonical(environment)),
        "live_bytes_reverified": True, "candidate_physical_go_allowed": False,
    })
    write_exclusive(output, document)
    return document


def execution_artifact_classification(returncode: int) -> str:
    if type(returncode) is not int:
        raise FlowError("execution return code is not an integer")
    return ("BOUND_COMPLETE_EXIT_ZERO_STAGE_MANIFEST" if returncode == 0 else
            "UNBOUND_NONZERO_EXIT_FILES_NOT_LEDGER_ELIGIBLE")


def execute_stage(design: str, stage: str, plan_path: Path,
                  environment_path: Path, authorization: str) -> dict[str, Any]:
    contract_payload, contract = validate_contract()
    if design not in contract["candidate_order"] or stage not in {"genus", "innovus"}:
        raise FlowError("unknown candidate or execution stage")
    expected_authorization = contract["execution_policy"]["explicit_authorization"][stage]
    if authorization != expected_authorization:
        raise FlowError(f"explicit real-{stage} authorization mismatch")
    contract_sha = sha256(contract_payload)
    environment_payload, environment = validate_live_environment(
        environment_path, contract_sha, contract)
    plan_payload, plan = validate_plan(plan_path, design, contract_sha, contract)
    attempt_root = Path(plan["attempt_root"])
    if environment_path.resolve() != attempt_root / "LIVE_ENVIRONMENT.json":
        raise FlowError("live environment snapshot must use the fixed attempt path")
    command = next(row for row in plan["commands"] if row["stage"] == stage)
    stage_root = attempt_root / stage
    if stage_root.exists():
        raise FlowError(f"exclusive no-overwrite stage already exists: {stage_root}")
    if stage == "innovus":
        genus_receipt_path = attempt_root / "genus/EXECUTION_RECEIPT.json"
        validate_execution_receipt(genus_receipt_path, "genus", design, attempt_root,
                                   contract, plan, sha256(plan_payload),
                                   sha256(environment_payload))
    stage_root.mkdir(parents=True, mode=0o755)
    work = stage_root / "work"
    temporary = stage_root / "tmp"
    work.mkdir(mode=0o755)
    temporary.mkdir(mode=0o755)
    runtime_environment = dict(environment["environment_allowlist"])
    runtime_environment.update(command["environment"])
    runtime_environment["HOME"] = str((stage_root / "home").resolve())
    (stage_root / "home").mkdir(mode=0o700)
    if runtime_environment.get("TMPDIR") != str(temporary.resolve()):
        raise FlowError("planned TMPDIR differs from exclusive stage directory")
    runtime_environment_sha = sha256(canonical(runtime_environment))
    log_path = stage_root / "tool.log"
    with open(log_path, "xb") as log:
        result = subprocess.run(command["argv"], cwd=work, env=runtime_environment,
                                stdout=log, stderr=subprocess.STDOUT, check=False)
        log.flush()
        os.fsync(log.fileno())
    log_payload = stable_read(log_path)
    artifacts = []
    if result.returncode == 0:
        for role, raw in expected_artifact_paths(plan["top"]).items():
            if role.endswith("execution_receipt") or not raw.startswith(stage + "/"):
                continue
            artifact = stable_read(safe_artifact(attempt_root, raw))
            artifacts.append({"role": role, "path": raw, "sha256": sha256(artifact),
                              "size_bytes": len(artifact),
                              "producer_command_sha256": command["command_sha256"]})
    manifest_sha = sha256(canonical(artifacts))
    upstream = None
    mapped_inputs: list[dict[str, Any]] = []
    if stage == "innovus":
        genus_receipt_payload = stable_read(attempt_root / "genus/EXECUTION_RECEIPT.json")
        upstream = sha256(genus_receipt_payload)
        for role in ("mapped_netlist", "mapped_sdc"):
            raw = expected_artifact_paths(plan["top"])[role]
            content = stable_read(safe_artifact(attempt_root, raw))
            mapped_inputs.append({"role": role, "path": raw, "sha256": sha256(content),
                                  "size_bytes": len(content)})
    receipt = seal({
        "schema": contract["execution_policy"]["execution_receipt_schema"], "design": design,
        "top": plan["top"], "attempt_root": str(attempt_root),
        "stage": stage,
        "status": "PASS_NATIVE_EXIT_ZERO" if result.returncode == 0 else "FAIL_NONZERO_EXIT",
        "exit_code": result.returncode, "contract_sha256": contract_sha,
        "live_environment_snapshot_sha256": sha256(environment_payload),
        "command_plan_sha256": sha256(plan_payload),
        "command_sha256": command["command_sha256"],
        "planned_environment_sha256": command["environment_sha256"],
        "runtime_environment_sha256": runtime_environment_sha,
        "tool_log_sha256": sha256(log_payload), "tool_log_size_bytes": len(log_payload),
        "artifacts": artifacts, "artifact_manifest_sha256": manifest_sha,
        "artifact_evidence_classification": execution_artifact_classification(
            result.returncode),
        "upstream_genus_receipt_sha256": upstream,
        "mapped_genus_inputs": mapped_inputs,
        "producer_authentication": "UNAUTHENTICATED_LOCAL_SELF_HASH",
        "candidate_physical_go_allowed": False,
    })
    write_exclusive(stage_root / "EXECUTION_RECEIPT.json", receipt)
    if result.returncode != 0:
        raise FlowError(f"real {stage} exited nonzero ({result.returncode})")
    return receipt


def safe_artifact(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or \
            any(part in {"", ".", ".."} for part in Path(raw).parts):
        raise FlowError(f"unsafe artifact path: {raw!r}")
    if root.is_symlink() or not root.is_absolute() or str(root.resolve()) != str(root):
        raise FlowError("attempt root must be an exact canonical non-symlink path")
    path = root / raw
    current = root
    for part in Path(raw).parts[:-1]:
        current = current / part
        try:
            row = current.lstat()
        except FileNotFoundError as error:
            raise MissingEvidence(f"missing artifact parent: {current}") from error
        if stat.S_ISLNK(row.st_mode) or not stat.S_ISDIR(row.st_mode):
            raise FlowError(f"artifact parent is not a real directory: {current}")
    return path


def parse_machine(payload: bytes, view: str, check: str) -> dict[str, Any]:
    rows: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        if line.count("=") != 1:
            raise FlowError("malformed timing machine row")
        key, value = line.split("=", 1)
        if key in rows or not value:
            raise FlowError("duplicate/empty timing machine field")
        rows[key] = value
    expected = {"schema", "view", "check", "path_count", "violation_count", "wns", "tns"}
    if set(rows) != expected or rows["schema"] != "k2_single_edge_timing_summary_v1" or \
            rows["view"] != view or rows["check"] != check:
        raise FlowError("timing machine contract mismatch")
    try:
        count, violations = int(rows["path_count"]), int(rows["violation_count"])
        wns, tns = float(rows["wns"]), float(rows["tns"])
    except ValueError as error:
        raise FlowError("timing machine numeric field is invalid") from error
    if count <= 0 or violations != 0 or wns < 0 or tns != 0 or \
            not math.isfinite(wns) or not math.isfinite(tns):
        raise FlowError("setup/hold timing is not closed")
    return {"path_count": count, "wns": wns, "tns": tns}


def parse_eco_receipt(payload: bytes, phase: dict[str, Any],
                      schema: str, epsilon: float) -> dict[str, Any]:
    rows: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        if line.count("=") != 1:
            raise FlowError("malformed ECO iteration receipt row")
        key, value = line.split("=", 1)
        if key in rows or not value:
            raise FlowError("duplicate/empty ECO iteration receipt field")
        rows[key] = value
    fixed = {"schema", "phase", "view", "check", "optimizer",
             "allow_setup_tns_degrade", "max_iterations", "status",
             "observation_count"}
    try:
        maximum = int(rows.get("max_iterations", ""))
        count = int(rows.get("observation_count", ""))
    except ValueError as error:
        raise FlowError("ECO iteration receipt count is invalid") from error
    if maximum != phase["max_iterations"] or count < 1 or count > maximum + 1:
        raise FlowError(f"{phase['role']} ECO iteration receipt count mismatch")
    observations = {f"observation_{index}" for index in range(count)}
    if not fixed.issubset(rows) or set(rows) != fixed | observations or \
            rows["schema"] != schema or \
            rows["phase"] != phase["phase"] or rows["view"] != phase["view"] or \
            rows["check"] != phase["check"] or \
            rows["optimizer"] != phase["optimizer"] or \
            rows["allow_setup_tns_degrade"] != phase["allow_setup_tns_degrade"] or \
            rows["status"] not in phase["allowed_statuses"]:
        raise FlowError(f"{phase['role']} ECO iteration receipt contract mismatch")
    metrics: list[tuple[int, int, float, float]] = []
    for index in range(count):
        tokens = rows[f"observation_{index}"].split(",")
        if len(tokens) != 4:
            raise FlowError(f"{phase['role']} ECO observation shape is invalid")
        try:
            paths, violations = int(tokens[0]), int(tokens[1])
            wns, tns = float(tokens[2]), float(tokens[3])
        except ValueError as error:
            raise FlowError(f"{phase['role']} ECO observation is nonnumeric") from error
        if paths <= 0 or violations < 0 or not math.isfinite(wns) or not math.isfinite(tns):
            raise FlowError(f"{phase['role']} ECO observation is invalid")
        clean = violations == 0 and wns >= 0.0 and tns == 0.0
        violating = violations > 0 and wns < 0.0 and tns < 0.0
        if not (clean or violating):
            raise FlowError(f"{phase['role']} ECO observation phase is invalid")
        metrics.append((paths, violations, wns, tns))
    improvements = []
    for before, after in zip(metrics, metrics[1:]):
        improvements.append(
            after[1] < before[1] or
            (after[1] == before[1] and
             (after[2] > before[2] + epsilon or
              (abs(after[2] - before[2]) <= epsilon and
               after[3] > before[3] + epsilon))))
    final = metrics[-1]
    status = rows["status"]
    final_clean = final[1] == 0 and final[2] >= 0.0 and final[3] == 0.0
    if status == "CLOSED":
        if not final_clean or any(row[1] == 0 for row in metrics[:-1]) or \
                not all(improvements):
            raise FlowError(f"{phase['role']} CLOSED ECO receipt is inconsistent")
    elif status == "EXHAUSTED":
        if final_clean or count != maximum + 1 or not all(improvements):
            raise FlowError(f"{phase['role']} EXHAUSTED ECO receipt is inconsistent")
    elif status == "STALLED":
        if final_clean or count < 2 or any(row[1] == 0 for row in metrics) or \
                not all(improvements[:-1]) or improvements[-1]:
            raise FlowError(f"{phase['role']} STALLED ECO receipt is inconsistent")
    else:
        raise FlowError(f"{phase['role']} ECO status is unsupported")
    return {"path_count": final[0], "violation_count": final[1],
            "wns": final[2], "tns": final[3], "observation_count": count,
            "status": status}


def parse_final_timing_receipt(payload: bytes, schema: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        if line.count("=") != 1:
            raise FlowError("malformed final timing receipt row")
        key, value = line.split("=", 1)
        if key in rows or not value:
            raise FlowError("duplicate/empty final timing receipt field")
        rows[key] = value
    expected = {"schema", "setup_view", "setup_check", "setup_path_count",
                "setup_violation_count", "setup_wns", "setup_tns", "hold_view",
                "hold_check", "hold_path_count", "hold_violation_count", "hold_wns",
                "hold_tns", "final_hold_phase_receipt"}
    if set(rows) != expected or rows["schema"] != schema or \
            rows["setup_view"] != "se_setup_view" or rows["setup_check"] != "setup" or \
            rows["hold_view"] != "se_hold_view" or rows["hold_check"] != "hold" or \
            rows["final_hold_phase_receipt"] != "eco_hold_final.machine":
        raise FlowError("final timing receipt contract mismatch")
    result = {}
    for check in ("setup", "hold"):
        try:
            paths = int(rows[f"{check}_path_count"])
            violations = int(rows[f"{check}_violation_count"])
            wns = float(rows[f"{check}_wns"])
            tns = float(rows[f"{check}_tns"])
        except ValueError as error:
            raise FlowError("final timing receipt numeric field is invalid") from error
        if paths <= 0 or violations != 0 or wns < 0.0 or tns != 0.0 or \
                not math.isfinite(wns) or not math.isfinite(tns):
            raise FlowError(f"final {check} timing receipt is not closed")
        result[check] = {"path_count": paths, "violation_count": violations,
                         "wns": wns, "tns": tns}
    return result


def strip_comments(text: str) -> str:
    """Remove block and line comments before any evidence token is examined."""
    without_blocks = re.sub(r"(?s)/\*.*?\*/", "", text)
    active_lines = []
    for line in without_blocks.splitlines():
        cuts = []
        slash = line.find("//")
        if slash >= 0:
            cuts.append(slash)
        for position, character in enumerate(line):
            if character == "#" and (not line[:position].strip() or
                    (position > 0 and line[position - 1].isspace() and
                     (position + 1 == len(line) or line[position + 1].isspace() or
                      line[position + 1].isalpha()))):
                cuts.append(position)
                break
        if cuts:
            line = line[:min(cuts)]
        if line.lstrip().startswith(";"):
            continue
        active_lines.append(line.rstrip())
    return "\n".join(active_lines)


def active_text(payload: bytes, label: str) -> str:
    stripped = strip_comments(payload.decode("utf-8"))
    if not stripped.strip() or has_failure_diagnostic(stripped):
        raise FlowError(f"{label} is empty, comment-only, or contains failure diagnostics")
    return stripped


def synthesis_text(payload: bytes, label: str) -> str:
    """Return comment-free text active when only SYNTHESIS is defined.

    This is intentionally a small fail-closed conditional preprocessor for
    source inventory and top-boundary screening.  Genus remains the authority
    for full SystemVerilog preprocessing and elaboration.
    """
    text = strip_comments(payload.decode("utf-8"))
    frames: list[tuple[bool, bool, bool]] = []
    active = True
    result: list[str] = []
    conditional = re.compile(
        r"^\s*`(ifdef|ifndef|else|endif)(?:\s+([A-Za-z_][A-Za-z0-9_$]*))?\s*$")
    for line in text.splitlines():
        stripped = line.strip()
        match = conditional.fullmatch(line)
        if match:
            command, name = match.groups()
            if command in {"ifdef", "ifndef"}:
                if name is None:
                    raise FlowError(f"{label} has a conditional without a macro name")
                condition = name == "SYNTHESIS"
                if command == "ifndef":
                    condition = not condition
                frames.append((active, condition, False))
                active = active and condition
            elif command == "else":
                if name is not None or not frames:
                    raise FlowError(f"{label} has an unmatched preprocessor else")
                parent, condition, seen_else = frames[-1]
                if seen_else:
                    raise FlowError(f"{label} has more than one preprocessor else")
                frames[-1] = (parent, condition, True)
                active = parent and not condition
            else:
                if name is not None or not frames:
                    raise FlowError(f"{label} has an unmatched preprocessor endif")
                parent, _, _ = frames.pop()
                active = parent
            continue
        if stripped.startswith("`elsif") or stripped.startswith("`if"):
            raise FlowError(f"{label} uses an unsupported conditional directive")
        if active:
            result.append(line)
    if frames:
        raise FlowError(f"{label} has an unterminated preprocessor conditional")
    joined = "\n".join(result)
    if not joined.strip():
        raise FlowError(f"{label} has no SYNTHESIS-active source text")
    return joined


def report_texts(payload: bytes, label: str) -> tuple[str, str]:
    """Return the untouched native report and its active, non-comment text."""
    raw = payload.decode("utf-8")
    active = strip_comments(raw)
    if not active.strip() or has_failure_diagnostic(active):
        raise FlowError(f"{label} is empty, comment-only, or contains failure diagnostics")
    return raw, active


def require_innovus_header(raw: str, top: str, version: str,
                           command_pattern: str, label: str) -> None:
    generators = re.findall(
        r"(?m)^#\s*Generated by:\s*Cadence Innovus\s+(\S+)\s*$", raw)
    designs = re.findall(r"(?m)^#\s*Design:\s*(\S+)\s*$", raw)
    commands = re.findall(r"(?m)^#\s*Command:\s*(.*?)\s*$", raw)
    if generators != [version] or designs != [top] or len(commands) != 1 or \
            re.fullmatch(command_pattern, commands[0]) is None:
        raise FlowError(f"{label} lacks one exact native Innovus tool/top/command header")


def require_innovus_identity(raw: str, top: str, version: str,
                             label: str) -> None:
    generators = re.findall(
        r"(?m)^#\s*Generated by:\s*Cadence Innovus\s+(\S+)\s*$", raw)
    designs = re.findall(r"(?m)^#\s*Design:\s*(\S+)\s*$", raw)
    commands = re.findall(r"(?m)^#\s*Command:\s*(.*?)\s*$", raw)
    if generators != [version] or designs != [top] or commands:
        raise FlowError(f"{label} lacks one exact native Innovus tool/top identity")


def require_genus_header(payload: bytes, top: str, version: str,
                         label: str) -> str:
    raw, _ = report_texts(payload, label)
    generators = re.findall(
        r"(?m)^\s*Generated by:\s*Genus\(TM\) Synthesis Solution(?:\s+(\S+))?\s*$",
        raw)
    designs = re.findall(r"(?m)^\s*(?:Module|Design|Instance):\s*/?(\S+)\s*$", raw)
    if generators != [version] or designs != [top]:
        raise FlowError(f"{label} lacks one exact native Genus tool/top header")
    return raw


def report_context_line(tool: str, version: str, top: str,
                        kind: str, context: str) -> str:
    return ("K2_SINGLE_EDGE_REPORT_CONTEXT_V1 "
            f"tool={tool} version={version} top={top} kind={kind} context={context}")


def require_report_context(text: str, tool: str, version: str, top: str,
                           kind: str, context: str) -> None:
    expected = report_context_line(tool, version, top, kind, context)
    markers = [line.strip() for line in text.splitlines()
               if line.strip().startswith("K2_SINGLE_EDGE_REPORT_CONTEXT_")]
    if markers != [expected]:
        raise FlowError(f"{kind} report lacks one exact native tool/top/context binding")


def require_zero_native(payload: bytes, kind: str, top: str,
                        version: str, context: str,
                        report_path: Path | None = None) -> None:
    raw, text = report_texts(payload, f"{kind} report")
    prefix, basename = {
        "drc": ("verify_drc -report", "drc.rpt"),
        "antenna": ("verify_process_antenna -report", "antenna.rpt"),
        "connectivity": ("verifyConnectivity -type all -error 1000 -warning 1000 -report",
                         "connectivity.rpt"),
        "pg_connectivity": ("verifyConnectivity -type special -error 1000 -warning 1000 -report",
                            "pg_connectivity.rpt"),
    }[kind]
    if report_path is None:
        command = rf"{re.escape(prefix)} (?:\S*/)?{re.escape(basename)}"
    else:
        command = re.escape(f"{prefix} {report_path}")
    if kind == "antenna":
        require_innovus_identity(raw, top, version, f"{kind} report")
    else:
        require_innovus_header(raw, top, version, command, f"{kind} report")
    require_report_context(text, "Innovus", version, top, kind, context)
    phrase = {
        "drc": r"No DRC violations were found\.?",
        "antenna": r"No Violations Found\.?",
        "connectivity": r"Found no problems or warnings\.",
        "pg_connectivity": r"Found no problems or warnings\.",
    }[kind]
    phrases = re.findall(rf"(?mi)^\s*{phrase}\s*$", text)
    if len(phrases) != 1:
        raise FlowError(f"{kind} report lacks one exact native clean sentinel")
    if kind in {"connectivity", "pg_connectivity"}:
        if len(re.findall(r"(?mi)^\s*Begin Summary\s*$", text)) != 1 or \
                len(re.findall(r"(?mi)^\s*End Summary\s*$", text)) != 1:
            raise FlowError(f"{kind} report lacks one complete native summary")
    nonzero = re.search(
        r"(?i)(?:Total\s+Violations\s*:\s*[1-9][0-9]*|"
        r"[1-9][0-9]*\s+(?:Problems?|Violations?|Errors?|Warnings?|"
        r"Problem\(s\)|Violation\(s\)|Error\(s\)|Warning\(s\))|"
        r"(?:violations?|viols?|errors?|problems?|warnings?)\s*[:=]\s*[1-9][0-9]*)",
        text)
    detail = re.search(
        r"(?mi)^\s*(?:DRC\s+)?(?:SHORT|OPEN|ANTENNA|VIOLATION)\s*(?::|#)|"
        r"\b(?:dangling wire|regular wires? (?:are )?open|special wires? (?:are )?open)\b",
        text)
    zero_totals = re.findall(
        r"(?mi)^\s*(?:Total\s+(?:DRC\s+)?Violations|Total\s+problems)\s*:\s*0"
        r"(?:\s+(?:Viols?\.?|Problem\(s\)))?\s*$", text)
    if len(zero_totals) > 1 or nonzero or detail or re.search(
            r"(?i)\b(?:aborted|incomplete|failed|not\s+run|not\s+checked|skipped|unknown)\b",
            text):
        raise FlowError(f"{kind} report contains contradictory or nonzero evidence")


def require_report(payload: bytes, label: str, top: str, tokens: Iterable[str]) -> str:
    text = active_text(payload, label)
    if top not in text or any(not re.search(token, text, re.IGNORECASE) for token in tokens):
        raise FlowError(f"{label} lacks bound native report structure")
    return text


def _expanded_boundary_ports(rows: Iterable[dict[str, Any]]) -> list[str]:
    ports = []
    for row in rows:
        if row["width"] == 1:
            ports.append(row["name"])
        else:
            ports.extend(f'{row["name"]}[{index}]'
                         for index in range(row["width"] - 1, -1, -1))
    return ports


def validate_sdc(text: str, contract: dict[str, Any],
                 top: str | None = None) -> dict[str, frozenset[str]]:
    active = strip_comments(text)
    if ";" in active or "\\\n" in active:
        raise FlowError("mapped SDC contains a semicolon or continued command")
    forbidden = r"(?i)\b(?:set_false_path|set_multicycle_path|set_case_analysis|set_disable_timing|set_clock_groups|set_max_delay|set_min_delay|create_generated_clock)\b"
    if re.search(forbidden, active):
        raise FlowError("mapped SDC values/exceptions differ from exact placeholder contract")
    commands = [re.sub(r"\s+", " ", command.strip())
                for line in active.splitlines() for command in line.split(";")
                if command.strip()]
    candidate_tops = {row["top"] for row in contract["candidates"].values()}
    if len(commands) < 4 or commands[:3] != [
            "set sdc_version 2.0", "set_units -capacitance 1000fF",
            "set_units -time 1000ps"] or \
            re.fullmatch(r"current_design ([A-Za-z_][A-Za-z0-9_$]*)",
                         commands[3]) is None or \
            commands[3].split()[1] not in ({top} if top is not None else candidate_tops):
        raise FlowError("mapped SDC first four generated commands/order are not exact")
    boundary = contract["boundary"]["normalized_ports"]
    clock_port = contract["boundary"]["clock_port"]
    input_rows = [row for row in boundary["inputs"] if row["name"] != clock_port]
    output_rows = boundary["outputs"]
    expected_inputs = _expanded_boundary_ports(input_rows)
    expected_outputs = _expanded_boundary_ports(output_rows)
    expected_by_base = {
        row["name"]: _expanded_boundary_ports([row])
        for row in boundary["inputs"] + boundary["outputs"]
    }
    all_ports = set(_expanded_boundary_ports(boundary["inputs"] + output_rows))
    selector_pattern = (r"\[get_ports\s+"
                        r"(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?)\]")
    clock_pattern = r"\[get_clocks\s+se_primary_clk\]"
    number = r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
    values = contract["constraints"]["values"]
    expected_values = {
        "period": float(values["SE_PERIOD_NS"]),
        "uncertainty": float(values["SE_CLOCK_UNCERTAINTY_NS"]),
        "input_delay_min": float(values["SE_INPUT_DELAY_MIN_NS"]),
        "input_delay_max": float(values["SE_INPUT_DELAY_MAX_NS"]),
        "input_transition": float(values["SE_INPUT_TRANSITION_NS"]),
        "output_delay_min": float(values["SE_OUTPUT_DELAY_MIN_NS"]),
        "output_delay_max": float(values["SE_OUTPUT_DELAY_MAX_NS"]),
        "load": float(values["SE_OUTPUT_LOAD_PF"]),
        "pulse_high": float(values["SE_MIN_PULSE_HIGH_NS"]),
        "pulse_low": float(values["SE_MIN_PULSE_LOW_NS"]),
    }
    seen: dict[str, list[str]] = {
        key: [] for key in ("input_delay_min", "input_delay_max",
                            "input_transition", "output_delay_min",
                            "output_delay_max", "load")
    }
    scalar_seen = {key: 0 for key in
                   ("clock", "uncertainty_setup", "uncertainty_hold",
                    "pulse_high", "pulse_low")}
    auxiliary_seen: set[str] = set()

    def exact_number(token: str, expected: float) -> bool:
        try:
            value = float(token)
        except ValueError:
            return False
        return math.isfinite(value) and value == expected

    def selector_ports(selector: str) -> list[str]:
        match = re.fullmatch(r"\[get_ports\s+(?:\{([^}]+)\}|([^\s\]]+))\]",
                             selector)
        if match is None:
            raise FlowError("mapped SDC contains a malformed port selector")
        tokens = (match.group(1) or match.group(2)).split()
        expanded: list[str] = []
        for token in tokens:
            if token in expected_by_base:
                expanded.extend(expected_by_base[token])
            elif token in all_ports:
                expanded.append(token)
            else:
                raise FlowError("mapped SDC constrains an extra or unknown port")
        if not expanded or len(expanded) != len(set(expanded)):
            raise FlowError("mapped SDC port collection is empty or duplicated")
        return expanded

    def add_ports(kind: str, token: str, selector: str) -> None:
        if not exact_number(token, expected_values[kind]):
            raise FlowError("mapped SDC contains a wrong constraint value")
        ports = selector_ports(selector)
        if len(ports) != 1:
            raise FlowError("mapped SDC constraint is not expanded to one exact port bit")
        seen[kind].extend(ports)

    for command in commands:
        match = re.fullmatch(
            rf'create_clock -name "se_primary_clk" -period ({number}) '
            rf'-waveform \{{({number}) ({number})\}} ({selector_pattern})', command)
        if match:
            period, start, end, selector = match.groups()
            if not exact_number(period, expected_values["period"]) or \
                    not exact_number(start, 0.0) or \
                    not exact_number(end, expected_values["period"] / 2.0) or \
                    selector_ports(selector) != [clock_port]:
                raise FlowError("mapped SDC primary clock is not exact")
            scalar_seen["clock"] += 1
            continue
        match = re.fullmatch(
            rf"set_clock_uncertainty(?: -(setup|hold))? ({number}) ({clock_pattern})",
            command)
        if match:
            edge, token, _ = match.groups()
            if edge is None:
                raise FlowError("mapped SDC uncertainty lacks an exact setup/hold option")
            if not exact_number(token, expected_values["uncertainty"]):
                raise FlowError("mapped SDC clock uncertainty is not exact")
            for kind in ((f"uncertainty_{edge}",) if edge else
                         ("uncertainty_setup", "uncertainty_hold")):
                scalar_seen[kind] += 1
            continue
        match = re.fullmatch(
            rf"set_min_pulse_width(?: -(high|low))? ({number}) ({clock_pattern})",
            command)
        if match:
            edge, token, _ = match.groups()
            if edge is not None:
                raise FlowError("mapped SDC pulse width is not the exact combined form")
            kinds = (f"pulse_{edge}",) if edge else ("pulse_high", "pulse_low")
            if any(not exact_number(token, expected_values[kind]) for kind in kinds):
                raise FlowError("mapped SDC minimum pulse width is not exact")
            for kind in kinds:
                scalar_seen[kind] += 1
            continue
        match = re.fullmatch(
            rf"set_(input|output)_delay -clock "
            rf"(\[get_clocks\s+se_primary_clk\]) -add_delay "
            rf"-(min|max) ({number}) ({selector_pattern})", command)
        if match:
            direction, _, edge, token, selector = match.groups()
            add_ports(f"{direction}_delay_{edge}", token, selector)
            continue
        match = re.fullmatch(
            rf"set_input_transition ({number}) ({selector_pattern})", command)
        if match:
            add_ports("input_transition", *match.groups())
            continue
        match = re.fullmatch(
            rf"set_load -pin_load ({number}) ({selector_pattern})", command)
        if match:
            add_ports("load", *match.groups())
            continue
        auxiliary = None
        if command == "set sdc_version 2.0":
            auxiliary = "sdc_version"
        elif command in {"set_units -capacitance 1000fF", "set_units -time 1000ps"}:
            auxiliary = command
        elif re.fullmatch(r"current_design [A-Za-z_][A-Za-z0-9_$]*", command):
            design = command.split()[1]
            allowed_designs = {top} if top is not None else candidate_tops
            if design not in allowed_designs:
                raise FlowError("mapped SDC current_design is not an exact candidate top")
            auxiliary = "current_design"
        elif command == "set_clock_gating_check -setup 0.0":
            auxiliary = "clock_gating_check"
        elif command == 'set_wire_load_mode "enclosed"':
            auxiliary = "wire_load_mode"
        if auxiliary is None or auxiliary in auxiliary_seen:
            raise FlowError("mapped SDC contains an extra or duplicate command")
        auxiliary_seen.add(auxiliary)

    expected_sets = {
        "input_delay_min": set(expected_inputs),
        "input_delay_max": set(expected_inputs),
        "input_transition": set(expected_inputs),
        "output_delay_min": set(expected_outputs),
        "output_delay_max": set(expected_outputs),
        "load": set(expected_outputs),
    }
    required_auxiliary = {
        "sdc_version", "set_units -capacitance 1000fF",
        "set_units -time 1000ps", "current_design", "clock_gating_check",
        "wire_load_mode",
    }
    if len(commands) != 205 or auxiliary_seen != required_auxiliary or \
            any(scalar_seen[key] != 1 for key in scalar_seen) or any(
            set(seen[key]) != expected or len(seen[key]) != len(expected)
            for key, expected in expected_sets.items()):
        raise FlowError("mapped SDC per-port constraints/cardinality are not exact")
    return {key: frozenset(seen[key]) for key in seen}


def split_verilog_list(raw: str) -> list[str]:
    result, start, depth = [], 0, 0
    for index, character in enumerate(raw):
        if character in "([{":
            depth += 1
        elif character in ")]}" and depth:
            depth -= 1
        elif character == "," and depth == 0:
            result.append(raw[start:index].strip())
            start = index + 1
    result.append(raw[start:].strip())
    return [item for item in result if item]


def declared_width(raw: str) -> int:
    ranges = re.findall(r"\[[^\]]*\]", raw)
    if not ranges:
        if "[" in raw or "]" in raw:
            raise FlowError("port declaration contains an unterminated range")
        return 1
    if len(ranges) != 1:
        raise FlowError("port declaration requires at most one numeric packed range")
    match = re.fullmatch(r"\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]", ranges[0])
    if match is None:
        raise FlowError("port declaration contains a symbolic or malformed range")
    msb, lsb = int(match.group(1)), int(match.group(2))
    if lsb != 0 or msb < lsb:
        raise FlowError("port packed range must use descending [N:0] orientation")
    range_start = raw.index(ranges[0])
    prefix = re.sub(r"\b(?:input|output|inout|wire|logic|reg|tri|signed|unsigned)\b",
                    "", raw[:range_start])
    if re.search(r"[A-Za-z_][A-Za-z0-9_$]*", prefix):
        raise FlowError("port range is unpacked rather than packed")
    suffix = raw[range_start + len(ranges[0]):]
    if re.search(r"[A-Za-z_][A-Za-z0-9_$]*", suffix) is None:
        raise FlowError("port range is unpacked or lacks a following identifier")
    return msb + 1


def validate_top_boundary(payload: bytes, top: str, contract: dict[str, Any],
                          role: str) -> tuple[str, str]:
    """Validate one exact top and its ordered port ABI for RTL or netlists."""
    text = active_text(payload, role)
    modules = re.findall(rf"(?is)\bmodule\s+{re.escape(top)}\b(.*?)\bendmodule\b", text)
    if len(modules) != 1:
        raise FlowError(f"{role} does not contain exactly one exact top")
    match = re.match(r"(?is)\s*(?:#\s*\(.*?\)\s*)?\((.*?)\)\s*;(.*)\Z", modules[0])
    if not match:
        raise FlowError(f"{role} top header is not structurally parseable")
    header, body = match.groups()
    expected_rows = [("input", row) for row in
                     contract["boundary"]["normalized_ports"]["inputs"]] + \
                    [("output", row) for row in
                     contract["boundary"]["normalized_ports"]["outputs"]]
    expected_names = [row["name"] for _, row in expected_rows]
    header_names = []
    ansi_declarations: dict[str, tuple[str, int]] = {}
    inherited_direction: str | None = None
    inherited_width = 1
    for item in split_verilog_list(header):
        direction_match = re.search(r"\b(input|output|inout)\b", item)
        if direction_match:
            inherited_direction = direction_match.group(1)
            inherited_width = declared_width(item)
        identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", re.sub(r"\[[^]]*\]", "", item))
        identifiers = [name for name in identifiers if name not in
                       {"input", "output", "inout", "wire", "logic", "reg", "tri", "signed", "unsigned"}]
        if not identifiers:
            raise FlowError(f"{role} contains an unparseable top port")
        name = identifiers[-1]
        header_names.append(name)
        if inherited_direction is not None:
            ansi_declarations[name] = (inherited_direction, inherited_width)
    if header_names != expected_names or len(set(header_names)) != len(header_names):
        raise FlowError(f"{role} top port list is not the exact complete boundary")
    declarations = dict(ansi_declarations)
    for declaration in re.finditer(r"(?is)\b(input|output|inout)\b\s+([^;]+);", body):
        direction, raw = declaration.groups()
        width = declared_width(raw)
        cleaned = re.sub(r"\[[^]]*\]", "", raw)
        cleaned = re.sub(r"\b(?:wire|logic|reg|tri|signed|unsigned)\b", "", cleaned)
        for item in split_verilog_list(cleaned):
            names = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", item.split("=", 1)[0])
            if len(names) != 1 or names[0] in declarations:
                raise FlowError(f"{role} has duplicate or unparseable port declarations")
            declarations[names[0]] = (direction, width)
    expected_declarations = {row["name"]: (direction, row["width"])
                             for direction, row in expected_rows}
    if declarations != expected_declarations:
        raise FlowError(f"{role} port directions/widths are not exact")
    return text, body


def validate_netlist(payload: bytes, top: str, contract: dict[str, Any], role: str) -> None:
    text, body = validate_top_boundary(payload, top, contract, role)
    expected_rows = [("input", row) for row in
                     contract["boundary"]["normalized_ports"]["inputs"]] + \
                    [("output", row) for row in
                     contract["boundary"]["normalized_ports"]["outputs"]]
    declaration_free = re.sub(r"(?is)\b(?:input|output|inout)\b\s+[^;]+;", "", body)
    instances = re.findall(
        r"(?ms)^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;",
        declaration_free)
    connections = "\n".join(instance[2] for instance in instances)
    input_names = {row["name"] for direction, row in expected_rows if direction == "input"}
    output_names = {row["name"] for direction, row in expected_rows if direction == "output"}
    connected = set(re.findall(r"\.\w+\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)", connections))
    if not instances or not (connected & input_names) or not (connected & output_names):
        raise FlowError(f"{role} lacks useful boundary-to-cell structural connectivity")
    if contains_forbidden(text, contract["source_policy"]["forbidden_case_insensitive_tokens"]):
        raise FlowError(f"{role} contains forbidden multi-edge material")


def validate_sdf(payload: bytes, top: str, role: str) -> None:
    text = active_text(payload, role)
    if not re.search(r"\(DELAYFILE\b", text, re.I) or \
            not re.search(rf'\(DESIGN\s+"{re.escape(top)}"\)', text, re.I) or \
            not re.search(r"\(CELL\b", text, re.I) or \
            not re.search(r"\((?:IOPATH|INTERCONNECT)\b", text, re.I):
        raise FlowError(f"{role} lacks nonempty exact-design delay structure")


def validate_spef(payload: bytes, top: str) -> None:
    text = active_text(payload, "postroute SPEF")
    for token in ("*SPEF", f'*DESIGN "{top}"', "*D_NET", "*CONN", "*CAP", "*RES", "*END"):
        if token not in text:
            raise FlowError("postroute SPEF lacks nonempty exact-design RC structure")


def _genus_native_output(text: str) -> str:
    """Remove Tcl verbose-source command echoes, retaining native tool output."""
    output = []
    echo_brace_depth = 0
    for line in text.splitlines():
        command = re.match(
            r"^@file\(genus_single_edge\.tcl\)\s+[0-9]+:\s?(.*)$", line)
        if command:
            source = command.group(1)
            echo_brace_depth = max(0, source.count("{") - source.count("}"))
            continue
        if echo_brace_depth:
            echo_brace_depth = max(
                0, echo_brace_depth + line.count("{") - line.count("}"))
            continue
        output.append(line)
    return "\n".join(output)


def validate_genus_log(text: str, top: str, version: str) -> None:
    native = _genus_native_output(text)
    summaries = re.findall(
        r"(?m)\bError=([0-9]+),\s*Fatal=([0-9]+)\b", native)
    versions = re.findall(r"(?m)^Version:[^\r\n]*$", native)
    version_pattern = (
        rf"Version:\s+{re.escape(version)}"
        r"(?:,\s+built\s+[^\r\n]+)?\s*")
    marker = f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}"
    markers = re.findall(
        r"(?m)^K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE[^\r\n]*$", native)
    normal_exits = re.findall(r"(?m)^Normal exit\.\s*$", native)
    if has_failure_diagnostic(native) or summaries != [("0", "0")] or \
            len(versions) != 1 or not re.fullmatch(version_pattern, versions[0]) or \
            markers != [marker] or len(normal_exits) != 1 or \
            not re.search(r"(?m)^Normal exit\.\s*\Z", native):
        raise FlowError("Genus log lacks one exact zero-error native completion")


def validate_innovus_log(text: str, top: str, version: str) -> None:
    summaries = [(match.start(), int(match.group(1))) for match in re.finditer(
        r"(?m)^\*\*\* Message Summary: [0-9]+ warning\(s\), "
        r"([0-9]+) error\(s\)[ \t]*$", text)]
    diagnostic_text = re.sub(
        r"(?m)^Error Limit = [0-9]+; Warning Limit = [0-9]+[ \t]*$", "", text)
    version_matches = list(re.finditer(r"(?m)^Version:[^\r\n]*$", text))
    versions = [match.group(0) for match in version_matches]
    version_pattern = (
        rf"Version:\s+v{re.escape(version)}"
        r"(?:,\s+built\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
        r"(?:[1-9]|[12][0-9]|3[01])\s+[0-2][0-9]:[0-5][0-9]:[0-5][0-9]\s+"
        r"[A-Z]{2,5}\s+[0-9]{4})?\s*")
    marker = f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}"
    markers = list(re.finditer(
        r"(?m)^[ \t]*K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE[^\r\n]*$", text))
    summary_lines = re.findall(
        r"(?m)^[ \t]*\*\*\* Message Summary[^\r\n]*$", text)
    ending_pattern = (r'^--- Ending "Innovus" '
                      r'\(totcpu=[0-9]+:[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?, '
                      r'real=[0-9]+:[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?, '
                      r'mem=[0-9]+(?:\.[0-9]+)?[KMG]\) ---[ \t]*$')
    endings = list(re.finditer(ending_pattern, text, re.MULTILINE))
    order_ok = bool(summaries and markers and endings and versions) and \
        version_matches[0].end() < markers[0].start() and \
        summaries[-1][0] > markers[0].end() and endings[0].start() > summaries[-1][0]
    if has_failure_diagnostic(diagnostic_text) or not summaries or \
            any(error_count != 0 for _, error_count in summaries) or \
            len(summary_lines) != len(summaries) or \
            len(versions) != 1 or not re.fullmatch(version_pattern, versions[0]) or \
            len(markers) != 1 or markers[0].group(0) != marker or \
            len(endings) != 1 or not re.search(
                ending_pattern + r"\s*\Z", text, re.MULTILINE) or \
            not order_ok:
        raise FlowError("Innovus log lacks one exact zero-error native completion")


def validate_check_timing(
        payload: bytes, top: str, version: str,
        sdc_semantics: dict[str, frozenset[str]] | None = None) -> None:
    raw, text = report_texts(payload, "check_timing")
    require_innovus_header(raw, top, version, r"check_timing -verbose", "check_timing")
    require_report_context(text, "Innovus", version, top, "check_timing", "postroute")
    blockers = ("no_clock", "no_input_delay", "no_output_delay",
                "unconstrained", "uncons_endpoint", "no_load")
    if len(re.findall(r"TIMING CHECK SUMMARY", text)) != 1:
        raise FlowError("check_timing lacks one native summary")
    if any(re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(blocker)}(?![A-Za-z0-9_])",
                     text) for blocker in blockers):
        raise FlowError("check_timing contains a missing-constraint or unconstrained class")
    rows = re.findall(
        r"(?m)^\s*\|\s*([a-z][a-z0-9_]*)\s*\|[^|\n]*\|\s*([0-9]+)\s*\|\s*$",
        text)
    accepted_rows = ([('ideal_clock_waveform', '1')] if sdc_semantics is None else
                     [('ideal_clock_waveform', '1'), ('no_drive', '18')])
    if rows != accepted_rows:
        raise FlowError("check_timing warning inventory is not the exact clean native form")
    ideal = re.findall(
        r"(?m)^\s*\|\s*se_primary_clk\s*\|\s*se_setup_view\s*\|\s*$", text)
    detail_sections = re.findall(
        r"(?s)TIMING CHECK DETAIL(.*?)TIMING CHECK IDEAL CLOCKS", text)
    details = re.findall(
        r"(?m)^\s*\|\s*([^|]+?)\s*\|\s*No drive assertion\s*\|"
        r"\s*([^|]+?)\s*\|\s*$", detail_sections[0] if detail_sections else "")
    expected_inputs = ["rst_i", "link_enable_i"] + [
        f"source_pending_i[{index}]" for index in range(15, -1, -1)]
    sdc_ok = sdc_semantics is not None and all(
        sdc_semantics.get(kind) == frozenset(expected_inputs)
        for kind in ("input_delay_min", "input_delay_max", "input_transition"))
    detail_ok = (not details and "TIMING CHECK DETAIL" not in text
                 if sdc_semantics is None else
                 details == [(pin, "se_setup_view") for pin in expected_inputs] and
                 len(detail_sections) == 1 and sdc_ok)
    if len(re.findall(r"TIMING CHECK IDEAL CLOCKS", text)) != 1 or len(ideal) != 1:
        raise FlowError("check_timing ideal-clock inventory is incomplete or contradictory")
    if not detail_ok:
        raise FlowError("check_timing ideal-clock inventory is incomplete or contradictory")
    if re.search(r"(?i)\b(?:aborted|incomplete|failed|not\s+run|skipped|unknown)\b", text):
        raise FlowError("check_timing contains contradictory completion evidence")


def validate_innovus_timing(payload: bytes, top: str, version: str,
                            check: str, metrics: dict[str, Any]) -> None:
    label = f"{check} timing"
    raw, text = report_texts(payload, label)
    require_innovus_header(
        raw, top, version,
        rf"report_timing -view se_{check}_view -check_type {check} -max_paths 50"
        rf'(?:\s+>\s+"\$output/reports/{check}_timing\.rpt")?',
        label)
    require_report_context(text, "Innovus", version, top,
                           f"{check}_timing", "postroute")
    paths = re.findall(
        r"(?mi)^Path\s+([0-9]+):\s+(MET|VIOLATED)\s+(.+?)\s*$", text)
    all_path_rows = re.findall(r"(?mi)^\s*Path\s+(?!Groups:)[^:\s]+:.*$", text)
    class_patterns = {
        "setup": (r"(?:\([-+0-9.]+\s+ns\)\s+)?Setup Check(?: with Pin .+)?",
                  r"(?:\([-+0-9.]+\s+ns\)\s+)?Late External Delay Assertion"),
        "hold": (r"(?:\([-+0-9.]+\s+ns\)\s+)?Hold Check(?: with Pin .+)?",
                 r"(?:\([-+0-9.]+\s+ns\)\s+)?Early External Delay Assertion"),
    }[check]
    if len(paths) != len(all_path_rows) or not paths or any(
            not any(re.fullmatch(pattern, path_class)
                                for pattern in class_patterns)
                        for _, _, path_class in paths) or \
            len(paths) != min(metrics["path_count"], 50) or \
            [int(index) for index, _, _ in paths] != list(range(1, len(paths) + 1)) or \
            any(status != "MET" for _, status, _ in paths):
        raise FlowError(f"native {check} timing report lacks sequential MET paths")
    slack_tokens = re.findall(
        r"(?mi)^\s*=?\s*Slack Time\s+([-+]?[0-9]+\.[0-9]{3})\s*$",
        text)
    if len(slack_tokens) != len(paths) or \
            len(re.findall(r"(?i)\bSlack Time\b", text)) != len(slack_tokens) or \
            re.search(r"(?i)\bVIOLATED\b|\bno[-_ ]?slack\b", text):
        raise FlowError(f"native {check} timing report lacks one Slack Time per path")
    slacks = [float(value) for value in slack_tokens]
    if any(value < 0 or not math.isfinite(value) for value in slacks):
        raise FlowError(f"native {check} timing report is not closed")
    displayed_wns = min(slacks)
    displayed_token = slack_tokens[slacks.index(displayed_wns)]
    decimals = len(displayed_token.partition(".")[2]) if "." in displayed_token else 0
    display_tolerance = 0.5 * (10 ** -decimals) + 1e-12
    contradiction_counts = re.findall(
        r"(?i)(?:violations?|violating\s+paths?)[^0-9\n]{0,12}([0-9]+)|"
        r"([0-9]+)[^0-9\n]{0,8}(?:violations?|violating\s+paths?)", text)
    if any(int(left or right) != 0 for left, right in contradiction_counts) or \
            re.search(r"(?i)no\s+(?:timing\s+)?paths?", text) or \
            not math.isclose(displayed_wns, metrics["wns"], rel_tol=0.0,
                             abs_tol=display_tolerance):
        raise FlowError(f"native {check} timing contradicts its machine summary")


def validate_innovus_area(payload: bytes, top: str, version: str) -> float:
    raw, text = report_texts(payload, "postroute area")
    if re.search(r"(?m)^#\s*(?:Generated by|Design|Command):", raw):
        raise FlowError("postroute area uses a fabricated native header")
    require_report_context(text, "Innovus", version, top, "area", "postroute")
    headers = re.findall(
        r"(?m)^\s*Hinst Name\s+Module Name\s+Inst Count\s+Total Area\s*$", text)
    totals = re.findall(
        rf"(?m)^\s*{re.escape(top)}\s+([1-9][0-9]*)\s+"
        r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\s*$", text)
    if len(headers) != 1 or len(totals) != 1:
        raise FlowError("postroute area lacks one native header/exact-top row")
    count, area = int(totals[0][0]), float(totals[0][1])
    if count <= 0 or area <= 0 or not math.isfinite(area):
        raise FlowError("postroute area lacks positive native instance/area values")
    return area


def validate_innovus_power(payload: bytes, top: str, version: str) -> float:
    raw, text = report_texts(payload, "postroute_power")
    versions = re.findall(r"(?m)^\*\s*Innovus\s+(\S+)\s+\(64bit\).*$", raw)
    designs = re.findall(r"(?m)^\*\s*Design:\s*(\S+)\s*$", raw)
    design_names = re.findall(r"(?m)^# Design Name:\s*(\S+)\s*$", raw)
    stages = re.findall(r"(?m)^# Design Stage:\s*(\S+)\s*$", raw)
    using_views = re.findall(r"(?m)^Using Power View:\s*(\S+)\.\s*$", raw)
    report_views = re.findall(r"(?m)^\*\s*Power View\s*:\s*(\S+)\s*$", raw)
    all_view_lines = re.findall(r"(?mi)^.*\bPower View\b.*$", raw)
    units = re.findall(r"(?m)^\*\s*Power Units\s*=\s*(\S+)\s*$", raw)
    if versions != [version] or designs != [top] or design_names != [top] or \
            stages != ["PostRoute"] or using_views != ["se_setup_view"] or \
            report_views != ["se_setup_view"] or len(all_view_lines) != 2 or \
            units != ["1mW"] or \
            re.findall(r"(?m)^\*\s*(report_power.*?)\s*$", raw) != ["report_power"] or \
            len(re.findall(r"(?m)^Total Power\s*$", raw)) != 1:
        raise FlowError("postroute_power lacks exact native tool/top/report identity")
    rows = re.findall(
        r"(?m)^Total (Internal|Switching|Leakage) Power:\s*"
        r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)"
        r"\s+[0-9]+(?:\.[0-9]+)?%\s*$", raw)
    totals = re.findall(
        r"(?m)^Total Power:\s*"
        r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)\s*$",
        raw)
    if [kind for kind, _ in rows] != ["Internal", "Switching", "Leakage"] or \
            len(totals) != 1:
        raise FlowError("postroute_power lacks one native Total Power structure")
    components = [float(value) for _, value in rows]
    total = float(totals[0])
    if any(not math.isfinite(value) or value < 0 for value in components) or \
            not math.isfinite(total) or total <= 0 or not math.isclose(
                total, sum(components), rel_tol=5e-7, abs_tol=5e-9):
        raise FlowError("postroute_power native totals are nonpositive or inconsistent")
    return total


def validate_genus_check_design(payload: bytes, top: str) -> None:
    raw, text = report_texts(payload, "genus_check_design")
    if re.search(r"(?m)^\s*Generated by:", raw) or len(re.findall(
            r"(?m)^\s*Check Design Report \(c\)\s*$", raw)) != 1 or \
            len(re.findall(r"(?m)^\s*Done Checking the design\.\s*$", raw)) != 1 or \
            not re.search(rf"(?m)^.*design '{re.escape(top)}'.*$", raw) or \
            len(re.findall(r"(?m)^\s*Name\s+Total\s*$", raw)) != 1:
        raise FlowError("genus_check_design lacks exact native report identity/structure")
    required_zero = {
        "Unresolved References", "Empty Modules", "Unloaded Port(s)",
        "Unloaded Sequential Pin(s)", "Unloaded Combinational Pin(s)", "Assigns",
        "Undriven Port(s)", "Undriven Leaf Pin(s)",
        "Undriven hierarchical pin(s)", "Multidriven Port(s)",
        "Multidriven Leaf Pin(s)", "Multidriven hierarchical Pin(s)",
        "Multidriven unloaded net(s)", "Constant Port(s)", "Constant Leaf Pin(s)",
        "Preserved leaf instance(s)", "Preserved hierarchical instance(s)",
        "Feedthrough Modules(s)", "Libcells with no LEF cell",
        "Physical (LEF) cells with no libcell", "Subdesigns with long module name",
        "Physical only instance(s)", "Logical only instance(s)",
    }
    special = "Constant hierarchical Pin(s)"
    sections = re.findall(
        r"(?ms)^\s*Summary\s*$.*?^\s*Name\s+Total\s*$\n"
        r"\s*-+\s*$\n(.*?)^\s*Done Checking the design\.\s*$", raw)
    if len(required_zero) != 23 or len(sections) != 1:
        raise FlowError("genus_check_design lacks one exact native Summary table")
    rows = re.findall(r"(?m)^\s*(.+?\S)\s{2,}([0-9]+)\s*$", sections[0])
    expected_names = required_zero | {special}
    if len(rows) != len(expected_names) or {name for name, _ in rows} != expected_names or \
            any(int(value) != 0 for name, value in rows if name != special):
        raise FlowError("genus_check_design contains a nonzero structural defect")


def validate_genus_power(payload: bytes, top: str) -> float:
    raw, _ = report_texts(payload, "genus_power")
    if re.search(r"(?m)^\s*Generated by:", raw) or re.findall(
            r"(?m)^Instance:\s*/(\S+)\s*$", raw) != [top] or \
            re.findall(r"(?m)^Power Unit:\s*(\S+)\s*$", raw) != ["W"] or \
            re.findall(r"(?m)^PDB Frames:\s*(\S+)\s*$", raw) != ["/stim#0/frame#0"] or \
            len(re.findall(
                r"(?m)^\s*Category\s+Leakage\s+Internal\s+Switching\s+Total\s+Row%\s*$",
                raw)) != 1:
        raise FlowError("genus_power lacks exact native report identity/structure")
    rows = re.findall(
        r"(?m)^\s*Subtotal\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+100\.00%\s*$", raw)
    if len(rows) != 1:
        raise FlowError("genus_power lacks one native Subtotal row")
    leakage, internal, switching, total = map(float, rows[0])
    if any(not math.isfinite(value) or value < 0
           for value in (leakage, internal, switching, total)) or total <= 0 or \
            not math.isclose(total, leakage + internal + switching,
                             rel_tol=5e-5, abs_tol=5e-10):
        raise FlowError("genus_power native subtotal is nonpositive or inconsistent")
    return total


def validate_genus_diagnostics(artifacts: dict[str, bytes], top: str,
                               version: str) -> None:
    role_tokens = {
        "genus_timing_intent": (r"lint\s+summary", r"total\s*:\s*0"),
        "genus_qor": (r"timing", r"path\s+slack"),
        "genus_clocks": (r"clock", r"se_primary_clk"),
    }
    for role, tokens in role_tokens.items():
        text = require_genus_header(artifacts[role], top, version, role)
        if any(not re.search(token, text, re.IGNORECASE) for token in tokens):
            raise FlowError(f"{role} lacks its native report structure")
    validate_genus_check_design(artifacts["genus_check_design"], top)
    validate_genus_power(artifacts["genus_power"], top)

    area = require_genus_header(artifacts["genus_area"], top, version, "genus_area")
    area_rows = re.findall(
        rf"(?m)^\s*{re.escape(top)}\s+\S+\s+([1-9][0-9]*)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)"
        r"(?:\s+\S+(?:\s+\([A-Z]\))?)?\s*$", area)
    if len(re.findall(
            r"(?m)^\s*Instance\s+Module\s+Cell-Count\s+Cell-Area\s+Net-Area\s+"
            r"Total-Area(?:\s+Wireload)?\s*$",
            area)) != 1 or len(area_rows) != 1:
        raise FlowError("genus_area lacks one native header/exact-top subtotal")
    try:
        cell_area, net_area, total_area = map(float, area_rows[0][1:])
    except ValueError as error:
        raise FlowError("genus_area contains a nonnumeric subtotal") from error
    if any(not math.isfinite(value) or value < 0
           for value in (cell_area, net_area, total_area)) or total_area <= 0 or \
            not math.isclose(total_area, cell_area + net_area,
                             rel_tol=2e-5, abs_tol=5e-6):
        raise FlowError("genus_area subtotal is nonpositive, nonfinite, or inconsistent")

    timing = require_genus_header(
        artifacts["genus_timing"], top, version, "genus_timing")
    paths = re.findall(
        r"(?m)^Path\s+([0-9]+):\s+(MET|VIOLATED)\s+(.+?)\s*$", timing)
    all_path_rows = re.findall(r"(?m)^\s*Path\s+(?!Groups:)[^:\s]+:.*$", timing)
    class_patterns = (
        r"\([-+0-9]+\s+ps\)\s+Setup Check(?: with Pin .+)?",
        r"\([-+0-9]+\s+ps\)\s+Late External Delay Assertion(?: at pin .+)?",
    )
    slacks = re.findall(r"(?m)^\s*Slack:=\s*(-?[0-9]+)\s*$", timing)
    path_slacks = [re.match(r"\((-?[0-9]+)\s+ps\)", path_class)
                   for _, _, path_class in paths]
    if len(paths) != len(all_path_rows) or not paths or any(
            not any(re.fullmatch(pattern, path_class)
                                for pattern in class_patterns)
                        for _, _, path_class in paths) or \
            len(paths) > 50 or len(paths) != len(slacks) or \
            len(re.findall(r"(?m)^\s*Slack:=", timing)) != len(slacks) or \
            [int(index) for index, _, _ in paths] != list(range(1, len(paths) + 1)) or \
            any(status != "MET" for _, status, _ in paths) or \
            any(match is None for match in path_slacks) or \
            [int(match.group(1)) for match in path_slacks if match] != \
            [int(value) for value in slacks] or \
            any(int(value) < 0 for value in slacks) or \
            int(slacks[0]) != min(map(int, slacks)) or \
            re.search(r"(?i)\bVIOLATED\b|\bno[-_ ]?slack\b", timing):
        raise FlowError("genus_timing lacks sequential native MET/slack paths")


def validate_check_design_report(payload: bytes, label: str) -> None:
    raw = payload.decode("utf-8")
    if has_failure_diagnostic(raw) or raw.count("Design check done.") != 1:
        raise FlowError(f"{label} lacks one clean native completion")
    summaries = re.findall(
        r"(?m)^\*\*\* Message Summary:\s+[0-9]+ warning\(s\),\s+([0-9]+) error\(s\)\s*$",
        raw)
    if summaries != ["0"] or re.search(
            r"(?mi)^.*unresolved\s+references?.*:\s*[1-9][0-9]*\s*$", raw) or \
            re.search(r"(?mi)^\s*(?:\*\*)?(?:ERROR|FATAL|VIOLATION)\b", raw):
        raise FlowError(f"{label} contains a native error or unresolved-reference class")


def validate_check_place_report(payload: bytes) -> None:
    raw = payload.decode("utf-8")
    if has_failure_diagnostic(raw) or len(re.findall(
            r"(?m)^Begin checking placement\b", raw)) != 1 or \
            len(re.findall(r"(?m)^Finished checkPlace\b", raw)) != 1:
        raise FlowError("check_place lacks one complete native placement check")
    unplaced = re.findall(r"(?mi)^\s*\*info:\s*Unplaced\s*=\s*([0-9]+)\s*$", raw)
    overlaps = [int(value) for value in re.findall(
        r"(?mi)^\s*Overlapping with other instance:\s*([0-9]+)\s*$", raw)]
    tech_site = [int(value) for value in re.findall(
        r"(?mi)^\s*TechSite Violation:\s*([0-9]+)\s*$", raw)]
    if unplaced != ["0"] or any(overlaps) or any(tech_site):
        raise FlowError(
            "check_place contains nonzero unplaced/overlap/tech-site evidence")


def validate_route_report(payload: bytes) -> None:
    raw = payload.decode("utf-8")
    if has_failure_diagnostic(raw) or len(re.findall(
            r"(?m)^#Number of fails = 0\s*$", raw)) != 1 or \
            len(re.findall(r"(?m)^#Total number of fails = 0\s*$", raw)) != 1 or \
            len(re.findall(r"(?m)^#Complete\s+on\s+.+$", raw)) != 1:
        raise FlowError("route report lacks native zero-failure completion")
    net_length = re.findall(
        r"(?m)^Total net length\s*=\s*"
        r"([0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?)\s*\(", raw)
    geometry = re.findall(
        r"(?m)^Total length:\s*"
        r"([0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?)um,\s*"
        r"number of vias:\s*([0-9]+)\s*$", raw)
    if len(net_length) != 1 or len(geometry) != 1 or \
            float(net_length[0]) <= 0 or float(geometry[0][0]) <= 0 or \
            int(geometry[0][1]) <= 0:
        raise FlowError("route report lacks positive native routed geometry")


def validate_live_environment(path: Path, contract_sha: str,
                              contract: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    payload, document = load_json(path, "live environment snapshot")
    verify_seal(document, "live environment snapshot")
    exact_keys(document, {"schema", "contract_sha256", "tools", "technology",
                          "environment_allowlist", "environment_allowlist_sha256",
                          "live_bytes_reverified", "candidate_physical_go_allowed",
                          "document_sha256"}, "live environment snapshot")
    if document.get("schema") != "k2_single_edge_live_environment_snapshot_v2" or \
            document.get("contract_sha256") != contract_sha or \
            document.get("live_bytes_reverified") is not True or \
            document.get("candidate_physical_go_allowed") is not False:
        raise FlowError("live environment snapshot contract mismatch")
    exact_keys(document.get("tools", {}), {"genus", "innovus"}, "tool identities")
    for name, expected in contract["tools"].items():
        observed = document.get("tools", {}).get(name, {})
        exact_keys(observed, {"entrypoint", "resolved_path", "sha256", "version",
                              "version_output_sha256"}, f"{name} identity")
        if any(observed.get(key) != expected[key] for key in
               ("entrypoint", "resolved_path", "sha256", "version")):
            raise FlowError(f"recorded {name} identity mismatch")
        fresh = tool_identity(Path(observed["entrypoint"]), expected, name.title())
        validate_fresh_tool_identity(observed, fresh, name)
    exact_keys(document.get("technology", {}), set(contract["technology"]["files"]),
               "technology identities")
    for role, expected in contract["technology"]["files"].items():
        row = document.get("technology", {}).get(role, {})
        exact_keys(row, {"path", "sha256", "size_bytes"}, f"technology {role}")
        exact_path = Path(contract["technology"]["pdk_root"]) / expected["relative_path"]
        if row.get("path") != str(exact_path):
            raise FlowError(f"technology path differs from pinned identity: {role}")
        live = stable_read(Path(row.get("path", "")))
        if row.get("sha256") != expected["sha256"] or sha256(live) != expected["sha256"] or \
                row.get("size_bytes") != len(live):
            raise FlowError(f"live technology bytes no longer match: {role}")
    env = document.get("environment_allowlist")
    if not isinstance(env, dict) or tuple(sorted(env)) != tuple(sorted(ENV_ALLOWLIST_KEYS)) or \
            any(not isinstance(value, str) for value in env.values()) or \
            sha256(canonical(env)) != \
            document.get("environment_allowlist_sha256"):
        raise FlowError("environment allowlist hash mismatch")
    return payload, document


def validate_plan(path: Path, design: str, contract_sha: str,
                  contract: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    payload, plan = load_json(path, "command plan")
    verify_seal(plan, "command plan")
    exact_keys(plan, {"schema", "design", "top", "contract_sha256", "attempt_root",
                      "package_files", "sources", "commands",
                      "candidate_physical_go_allowed", "document_sha256"},
               "command plan")
    row = contract["candidates"][design]
    if plan.get("schema") != "k2_single_edge_command_plan_v1" or \
            plan.get("design") != design or plan.get("top") != row["top"] or \
            plan.get("contract_sha256") != contract_sha or \
            plan.get("candidate_physical_go_allowed") is not False:
        raise FlowError("command plan binding mismatch")
    attempt_text = plan.get("attempt_root")
    if not isinstance(attempt_text, str) or not Path(attempt_text).is_absolute() or \
            str(Path(attempt_text).resolve()) != attempt_text:
        raise FlowError("command plan attempt root is not canonical absolute")
    if plan.get("package_files") != package_inventory(contract):
        raise FlowError("command plan package hashes are stale")
    observed_sources = source_identity(contract, design)
    if plan.get("sources") != observed_sources:
        raise FlowError("command plan source hashes are stale")
    commands = plan.get("commands")
    expected_commands = planned_commands(contract, design, Path(attempt_text))
    if commands != expected_commands:
        raise FlowError("command plan argv/environment/cwd hashes are not exact")
    return payload, plan


def expected_artifact_paths(top: str) -> dict[str, str]:
    return {
        "genus_execution_receipt": "genus/EXECUTION_RECEIPT.json",
        "genus_log": "genus/tool.log",
        "genus_timing": "genus/reports/timing.rpt",
        "genus_area": "genus/reports/area.rpt",
        "genus_check_design": "genus/reports/check_design.rpt",
        "genus_timing_intent": "genus/reports/timing_intent.rpt",
        "genus_qor": "genus/reports/qor.rpt",
        "genus_power": "genus/reports/power_vectorless_screening.rpt",
        "genus_clocks": "genus/reports/clocks.rpt",
        "mapped_netlist": f"genus/netlist/{top}.mapped.v",
        "mapped_sdc": f"genus/netlist/{top}.mapped.sdc",
        "mapped_sdf": f"genus/netlist/{top}.mapped.sdf",
        "genus_commands_complete": "genus/status/COMMANDS_COMPLETE",
        "innovus_execution_receipt": "innovus/EXECUTION_RECEIPT.json",
        "innovus_log": "innovus/tool.log",
        "eco_hold_pre_setup": "innovus/reports/eco_hold_pre_setup.machine",
        "eco_setup_recovery": "innovus/reports/eco_setup_recovery.machine",
        "eco_hold_final": "innovus/reports/eco_hold_final.machine",
        "final_timing_receipt": "innovus/reports/final_timing_receipt.machine",
        "setup_timing": "innovus/reports/setup_timing.rpt",
        "setup_timing_machine": "innovus/reports/setup_timing.machine",
        "hold_timing": "innovus/reports/hold_timing.rpt",
        "hold_timing_machine": "innovus/reports/hold_timing.machine",
        "postroute_area": "innovus/reports/area.rpt",
        "postroute_power": "innovus/reports/power_vectorless_screening.rpt",
        "check_timing": "innovus/reports/check_timing.rpt",
        "check_design_pre_place": "innovus/reports/check_design_pre_place.rpt",
        "check_place": "innovus/reports/check_place.rpt",
        "check_design_post_route": "innovus/reports/check_design_post_route.rpt",
        "route": "innovus/reports/route.rpt",
        "drc": "innovus/reports/drc.rpt",
        "antenna": "innovus/reports/antenna.rpt",
        "connectivity": "innovus/reports/connectivity.rpt",
        "pg_connectivity": "innovus/reports/pg_connectivity.rpt",
        "postroute_netlist": f"innovus/netlist/{top}.postroute.v",
        "postroute_sdf": f"innovus/netlist/{top}.postroute.sdf",
        "postroute_spef": f"innovus/netlist/{top}.postroute.spef",
        "innovus_database_manifest": "innovus/database/MANIFEST.txt",
        "innovus_commands_complete": "innovus/status/COMMANDS_COMPLETE",
    }


def _stage_for_role(role: str) -> str:
    return "genus" if role.startswith("genus_") or role.startswith("mapped_") else "innovus"


def validate_execution_receipt(path: Path, stage: str, design: str, root: Path,
                               contract: dict[str, Any], plan: dict[str, Any],
                               plan_sha: str, environment_sha: str) -> tuple[bytes, dict[str, Any]]:
    payload, receipt = load_json(path, f"{stage} execution receipt")
    verify_seal(receipt, f"{stage} execution receipt")
    exact_keys(receipt, {"schema", "design", "top", "attempt_root", "stage", "status",
                "exit_code", "contract_sha256", "live_environment_snapshot_sha256",
                "command_plan_sha256", "command_sha256", "planned_environment_sha256",
                "runtime_environment_sha256", "tool_log_sha256", "tool_log_size_bytes",
                "artifacts", "artifact_manifest_sha256", "artifact_evidence_classification",
                "upstream_genus_receipt_sha256",
                "mapped_genus_inputs", "producer_authentication",
                "candidate_physical_go_allowed", "document_sha256"},
               f"{stage} execution receipt")
    command = next(row for row in plan["commands"] if row["stage"] == stage)
    live_environment_bytes, live_environment = load_json(
        root / "LIVE_ENVIRONMENT.json", "live environment snapshot")
    allowlist = live_environment.get("environment_allowlist")
    if not isinstance(allowlist, dict) or set(allowlist) != set(ENV_ALLOWLIST_KEYS) or \
            sha256(live_environment_bytes) != environment_sha:
        raise FlowError("receipt live environment binding mismatch")
    expected_runtime = dict(allowlist)
    expected_runtime.update(command["environment"])
    expected_runtime["HOME"] = str(root / stage / "home")
    expected_runtime_sha = sha256(canonical(expected_runtime))
    top = contract["candidates"][design]["top"]
    if (receipt["schema"] != contract["execution_policy"]["execution_receipt_schema"] or
            receipt["design"] != design or receipt["top"] != top or
            receipt["attempt_root"] != str(root) or receipt["stage"] != stage or
            receipt["status"] != "PASS_NATIVE_EXIT_ZERO" or
            type(receipt["exit_code"]) is not int or receipt["exit_code"] != 0 or
            receipt["contract_sha256"] != sha256(stable_read(CONTRACT)) or
            receipt["live_environment_snapshot_sha256"] != environment_sha or
            receipt["command_plan_sha256"] != plan_sha or
            receipt["command_sha256"] != command["command_sha256"] or
            receipt["planned_environment_sha256"] != command["environment_sha256"] or
            receipt["runtime_environment_sha256"] != expected_runtime_sha or
            receipt["artifact_evidence_classification"] !=
            contract["execution_policy"]["exit_zero_artifact_evidence_classification"] or
            receipt["producer_authentication"] != "UNAUTHENTICATED_LOCAL_SELF_HASH" or
            receipt["candidate_physical_go_allowed"] is not False):
        raise FlowError(f"{stage} execution receipt binding mismatch")
    log_raw = expected_artifact_paths(top)[f"{stage}_log"]
    log = stable_read(safe_artifact(root, log_raw))
    if receipt["tool_log_sha256"] != sha256(log) or receipt["tool_log_size_bytes"] != len(log):
        raise FlowError(f"{stage} receipt log binding mismatch")
    rows = receipt["artifacts"]
    if not isinstance(rows, list) or receipt["artifact_manifest_sha256"] != sha256(canonical(rows)):
        raise FlowError(f"{stage} receipt artifact manifest hash mismatch")
    expected_paths = expected_artifact_paths(top)
    expected_roles = {role for role in expected_paths
                      if _stage_for_role(role) == stage and not role.endswith("execution_receipt")}
    role_rows = {row.get("role"): row for row in rows if isinstance(row, dict)}
    if len(role_rows) != len(rows) or set(role_rows) != expected_roles:
        raise FlowError(f"{stage} receipt artifact roles differ")
    for role in expected_roles:
        row = role_rows[role]
        exact_keys(row, {"role", "path", "sha256", "size_bytes", "producer_command_sha256"},
                   f"{stage} receipt artifact {role}")
        if row["path"] != expected_paths[role] or row["producer_command_sha256"] != command["command_sha256"]:
            raise FlowError(f"{stage} receipt stage/path binding mismatch: {role}")
        live = stable_read(safe_artifact(root, row["path"]))
        if row["sha256"] != sha256(live) or row["size_bytes"] != len(live):
            raise FlowError(f"{stage} receipt artifact bytes changed: {role}")
    if stage == "genus":
        if receipt["upstream_genus_receipt_sha256"] is not None or receipt["mapped_genus_inputs"] != []:
            raise FlowError("Genus receipt has unexpected upstream inputs")
    else:
        genus_payload = stable_read(safe_artifact(root, "genus/EXECUTION_RECEIPT.json"))
        if receipt["upstream_genus_receipt_sha256"] != sha256(genus_payload):
            raise FlowError("Innovus receipt is not bound to exact Genus receipt")
        inputs = []
        for role in ("mapped_netlist", "mapped_sdc"):
            raw = expected_paths[role]
            live = stable_read(safe_artifact(root, raw))
            inputs.append({"role": role, "path": raw, "sha256": sha256(live),
                           "size_bytes": len(live)})
        if receipt["mapped_genus_inputs"] != inputs:
            raise FlowError("Innovus receipt mapped input binding mismatch")
    return payload, receipt


def build_ledger(design: str, attempt_root: Path, plan_path: Path,
                 output: Path) -> dict[str, Any]:
    contract_payload, contract = validate_contract()
    if design not in contract["candidate_order"]:
        raise FlowError("unknown candidate")
    plan_payload, plan = validate_plan(plan_path, design, sha256(contract_payload), contract)
    attempt_root = attempt_root.resolve()
    if str(attempt_root) != plan["attempt_root"]:
        raise FlowError("caller attempt root differs from command plan")
    top = contract["candidates"][design]["top"]
    paths = expected_artifact_paths(top)
    if set(paths) != set(contract["artifact_ledger"]["required_roles"]):
        raise FlowError("internal artifact path map differs from contract roles")
    environment_path = attempt_root / "LIVE_ENVIRONMENT.json"
    environment_payload, _ = validate_live_environment(
        environment_path, sha256(contract_payload), contract)
    receipt_rows: dict[str, dict[str, Any]] = {}
    for stage in ("genus", "innovus"):
        receipt_path = attempt_root / stage / "EXECUTION_RECEIPT.json"
        receipt_payload, receipt = validate_execution_receipt(
            receipt_path, stage, design, attempt_root, contract, plan,
            sha256(plan_payload), sha256(environment_payload))
        receipt_rows[f"{stage}_execution_receipt"] = {
            "role": f"{stage}_execution_receipt", "path": paths[f"{stage}_execution_receipt"],
            "sha256": sha256(receipt_payload), "size_bytes": len(receipt_payload),
            "producer_command_sha256": next(row["command_sha256"] for row in plan["commands"]
                                               if row["stage"] == stage)}
        receipt_rows.update({row["role"]: row for row in receipt["artifacts"]})
    rows = [receipt_rows[role] for role in contract["artifact_ledger"]["required_roles"]]
    document = seal({
        "schema": contract["artifact_ledger"]["schema"], "design": design,
        "top": top, "contract_sha256": sha256(contract_payload),
        "attempt_root": str(attempt_root),
        "command_plan_sha256": sha256(plan_payload),
        "live_environment_snapshot_sha256": sha256(environment_payload),
        "artifacts": rows, "candidate_physical_go_allowed": False,
    })
    write_exclusive(output, document)
    return document


def validate_artifacts(root: Path, ledger_path: Path, design: str,
                       contract: dict[str, Any], plan: dict[str, Any]) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    if str(root) != plan["attempt_root"]:
        raise FlowError("caller attempt root differs from command plan")
    payload, ledger = load_json(ledger_path, "artifact ledger")
    verify_seal(ledger, "artifact ledger")
    exact_keys(ledger, {"schema", "design", "top", "contract_sha256", "attempt_root",
                       "command_plan_sha256", "live_environment_snapshot_sha256",
                       "artifacts", "candidate_physical_go_allowed", "document_sha256"},
               "artifact ledger")
    if ledger.get("schema") != contract["artifact_ledger"]["schema"] or \
            ledger.get("design") != design or ledger.get("top") != \
            contract["candidates"][design]["top"] or ledger.get("attempt_root") != str(root):
        raise FlowError("artifact ledger design/top mismatch")
    if ledger.get("candidate_physical_go_allowed") is not False:
        raise FlowError("artifact ledger attempts to authorize candidate GO")
    if ledger.get("contract_sha256") != sha256(stable_read(CONTRACT)) or \
            ledger.get("command_plan_sha256") != sha256(canonical(plan)):
        raise FlowError("artifact ledger contract/command binding mismatch")
    rows = ledger.get("artifacts")
    if not isinstance(rows, list):
        raise FlowError("artifact ledger rows are missing")
    role_rows = {row.get("role"): row for row in rows if isinstance(row, dict)}
    required = contract["artifact_ledger"]["required_roles"]
    if len(role_rows) != len(rows) or set(role_rows) != set(required):
        raise FlowError("artifact ledger roles are not exact")
    command_hashes = {row["stage"]: row["command_sha256"] for row in plan["commands"]}
    exact_paths = expected_artifact_paths(contract["candidates"][design]["top"])
    artifacts: dict[str, bytes] = {}
    for role in required:
        row = role_rows[role]
        exact_keys(row, {"role", "path", "sha256", "size_bytes", "producer_command_sha256"},
                   f"artifact {role}")
        if row["path"] != exact_paths[role]:
            raise FlowError(f"artifact {role} path is not exact")
        expected_stage = _stage_for_role(role)
        if not SHA256.fullmatch(str(row["sha256"])) or \
                row["producer_command_sha256"] != command_hashes[expected_stage]:
            raise FlowError(f"artifact {role} has invalid provenance")
        artifact = stable_read(safe_artifact(root, row["path"]))
        if sha256(artifact) != row["sha256"] or len(artifact) != row["size_bytes"]:
            raise FlowError(f"artifact {role} byte identity mismatch")
        artifacts[role] = artifact
    env_payload = stable_read(root / "LIVE_ENVIRONMENT.json")
    if ledger["live_environment_snapshot_sha256"] != sha256(env_payload):
        raise FlowError("artifact ledger environment binding mismatch")
    for stage in ("genus", "innovus"):
        receipt_payload, receipt = validate_execution_receipt(
            root / stage / "EXECUTION_RECEIPT.json", stage, design, root, contract, plan,
            ledger["command_plan_sha256"], sha256(env_payload))
        receipt_role = f"{stage}_execution_receipt"
        if artifacts[receipt_role] != receipt_payload:
            raise FlowError(f"ledger differs from {stage} receipt bytes")
        manifest = {row["role"]: row for row in receipt["artifacts"]}
        for role, row in manifest.items():
            ledger_row = role_rows[role]
            if ledger_row != row:
                raise FlowError(f"ledger differs from {stage} receipt manifest: {role}")
    top = contract["candidates"][design]["top"]
    genus_log = artifacts["genus_log"].decode("utf-8")
    validate_genus_log(genus_log, top, contract["tools"]["genus"]["version"])
    innovus_log = artifacts["innovus_log"].decode("utf-8")
    validate_innovus_log(innovus_log, top, contract["tools"]["innovus"]["version"])
    if artifacts["genus_commands_complete"].decode("utf-8").strip() != \
            f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}" or \
            artifacts["innovus_commands_complete"].decode("utf-8").strip() != \
            f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}":
        raise FlowError("native completion marker content mismatch")
    innovus_version = contract["tools"]["innovus"]["version"]
    setup = parse_machine(artifacts["setup_timing_machine"], "se_setup_view", "setup")
    hold = parse_machine(artifacts["hold_timing_machine"], "se_hold_view", "hold")
    eco = {}
    eco_contract = contract["bounded_eco"]
    for phase in eco_contract["phases"]:
        eco[phase["role"]] = parse_eco_receipt(
            artifacts[phase["role"]], phase, eco_contract["schema"],
            eco_contract["monotonic_epsilon_ns"])
    for role in eco_contract["required_closed_phases"]:
        if eco[role]["status"] != "CLOSED":
            raise FlowError(f"required ECO phase is not CLOSED: {role}")
    final_receipt_contract = eco_contract["final_timing_receipt"]
    final_receipt = parse_final_timing_receipt(
        artifacts[final_receipt_contract["role"]], final_receipt_contract["schema"])
    for check, summary in (("setup", setup), ("hold", hold)):
        receipt_metrics = final_receipt[check]
        if receipt_metrics["path_count"] != summary["path_count"] or \
                abs(receipt_metrics["wns"] - summary["wns"]) > 1e-9 or \
                abs(receipt_metrics["tns"] - summary["tns"]) > 1e-9:
            raise FlowError(f"final {check} receipt differs from final {check} summary")
    final_hold = eco["eco_hold_final"]
    if final_hold["path_count"] != final_receipt["hold"]["path_count"] or \
            abs(final_hold["wns"] - final_receipt["hold"]["wns"]) > 1e-9 or \
            abs(final_hold["tns"] - final_receipt["hold"]["tns"]) > 1e-9:
        raise FlowError("final hold ECO receipt differs from final timing receipt")
    for role, metrics, check in (("setup_timing", setup, "setup"),
                                 ("hold_timing", hold, "hold")):
        validate_innovus_timing(artifacts[role], top, innovus_version, check, metrics)
    paths = expected_artifact_paths(top)
    require_zero_native(artifacts["drc"], "drc", top, innovus_version, "postroute",
                        root / paths["drc"])
    require_zero_native(artifacts["antenna"], "antenna", top, innovus_version,
                        "postroute", root / paths["antenna"])
    require_zero_native(artifacts["connectivity"], "connectivity", top,
                        innovus_version, "signal_postroute", root / paths["connectivity"])
    require_zero_native(artifacts["pg_connectivity"], "pg_connectivity", top,
                        innovus_version, "pg_postroute", root / paths["pg_connectivity"])
    sdc_semantics = validate_sdc(
        active_text(artifacts["mapped_sdc"], "mapped SDC"), contract, top)
    validate_check_timing(
        artifacts["check_timing"], top, innovus_version, sdc_semantics)
    for role in ("mapped_netlist", "postroute_netlist"):
        validate_netlist(artifacts[role], top, contract, role)
    validate_sdf(artifacts["mapped_sdf"], top, "mapped SDF")
    validate_sdf(artifacts["postroute_sdf"], top, "postroute SDF")
    validate_spef(artifacts["postroute_spef"], top)
    area = validate_innovus_area(artifacts["postroute_area"], top, innovus_version)
    validate_genus_diagnostics(
        artifacts, top, contract["tools"]["genus"]["version"])
    validate_innovus_power(artifacts["postroute_power"], top, innovus_version)
    validate_check_design_report(
        artifacts["check_design_pre_place"], "check_design_pre_place")
    validate_check_place_report(artifacts["check_place"])
    validate_check_design_report(
        artifacts["check_design_post_route"], "check_design_post_route")
    validate_route_report(artifacts["route"])
    database = require_report(artifacts["innovus_database_manifest"],
                              "database manifest", top, (r"checkpoint", r"entry"))
    if "UNAUTHENTICATED_LOCAL_SELF_HASH" not in database:
        raise FlowError("database manifest does not disclose its unauthenticated status")
    return payload, ledger, {"setup": setup, "hold": hold, "bounded_eco": eco,
                             "final_timing_receipt": final_receipt, "area": area}


def hold_receipt(contract_sha: str, design: str, blockers: Iterable[str]) -> dict[str, Any]:
    return seal({
        "schema": "k2_single_edge_physical_qualification_v3", "design": design,
        "decision": "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE",
        "contract_sha256": contract_sha,
        "blockers": list(blockers), "diagnostic_artifact_checks_completed": False,
        "diagnostic_evidence_scope": "CALLER_SELF_SEALED_UNAUTHENTICATED_ONLY",
        "producer_authenticated": False, "candidate_physical_go": False,
    })


def qualify(design: str, attempt_root: Path, environment: Path, plan_path: Path,
            ledger_path: Path, output: Path) -> dict[str, Any]:
    contract_payload, contract = validate_contract()
    if design not in contract["candidate_order"]:
        raise FlowError("unknown candidate")
    contract_sha = sha256(contract_payload)
    missing = [str(path) for path in (environment, plan_path, ledger_path,
               attempt_root / "genus/EXECUTION_RECEIPT.json",
               attempt_root / "innovus/EXECUTION_RECEIPT.json") if not path.exists()]
    if missing:
        document = hold_receipt(contract_sha, design, missing)
        write_exclusive(output, document)
        return document
    env_payload, _ = validate_live_environment(environment, contract_sha, contract)
    plan_payload, plan = validate_plan(plan_path, design, contract_sha, contract)
    attempt_root = attempt_root.resolve()
    if str(attempt_root) != plan["attempt_root"] or \
            environment.resolve() != attempt_root / "LIVE_ENVIRONMENT.json":
        raise FlowError("caller attempt/environment path differs from command plan")
    ledger_payload, _, metrics = validate_artifacts(
        attempt_root.resolve(), ledger_path, design, contract, plan)
    document = seal({
        "schema": "k2_single_edge_physical_qualification_v3", "design": design,
        "decision": "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE",
        "contract_sha256": contract_sha,
        "environment_receipt_sha256": sha256(env_payload),
        "command_plan_sha256": sha256(plan_payload),
        "artifact_ledger_sha256": sha256(ledger_payload),
        "diagnostic_metrics_only": metrics, "diagnostic_artifact_checks_completed": True,
        "diagnostic_evidence_scope": "CALLER_SELF_SEALED_UNAUTHENTICATED_ONLY",
        "producer_authenticated": False,
        "constraint_evidence_class": contract["constraints"]["evidence_class"],
        "candidate_physical_go": False,
        "promotion_requires_new_reviewed_contract": True,
        "promotion_requires_out_of_band_producer_authentication": True,
    })
    write_exclusive(output, document)
    return document


def bind_cohort(a2_qualification: Path, a3_qualification: Path,
                output: Path) -> dict[str, Any]:
    """Bind two diagnostic HOLD receipts to one environment snapshot hash."""
    contract_payload, contract = validate_contract()
    contract_sha = sha256(contract_payload)
    expected_keys = {
        "schema", "design", "decision", "contract_sha256",
        "environment_receipt_sha256", "command_plan_sha256",
        "artifact_ledger_sha256", "diagnostic_metrics_only",
        "diagnostic_artifact_checks_completed", "diagnostic_evidence_scope",
        "producer_authenticated", "constraint_evidence_class",
        "candidate_physical_go", "promotion_requires_new_reviewed_contract",
        "promotion_requires_out_of_band_producer_authentication", "document_sha256",
    }
    rows = []
    environment_hashes = []
    for design, path in (("a2", a2_qualification), ("a3", a3_qualification)):
        payload, receipt = load_json(path, f"{design} qualification")
        verify_seal(receipt, f"{design} qualification")
        exact_keys(receipt, expected_keys, f"{design} qualification")
        environment_sha = receipt.get("environment_receipt_sha256")
        bound_hashes = (environment_sha, receipt.get("command_plan_sha256"),
                        receipt.get("artifact_ledger_sha256"))
        if receipt.get("schema") != "k2_single_edge_physical_qualification_v3" or \
                receipt.get("design") != design or \
                receipt.get("decision") != "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE" or \
                receipt.get("contract_sha256") != contract_sha or \
                receipt.get("diagnostic_artifact_checks_completed") is not True or \
                receipt.get("diagnostic_evidence_scope") != \
                "CALLER_SELF_SEALED_UNAUTHENTICATED_ONLY" or \
                receipt.get("producer_authenticated") is not False or \
                receipt.get("candidate_physical_go") is not False or \
                receipt.get("constraint_evidence_class") != \
                contract["constraints"]["evidence_class"] or \
                receipt.get("promotion_requires_new_reviewed_contract") is not True or \
                receipt.get("promotion_requires_out_of_band_producer_authentication") is not True or \
                not isinstance(receipt.get("diagnostic_metrics_only"), dict) or \
                any(not isinstance(value, str) or not SHA256.fullmatch(value)
                    for value in bound_hashes):
            raise FlowError(f"{design} qualification is not an exact diagnostic HOLD")
        environment_hashes.append(environment_sha)
        rows.append({"design": design, "qualification_sha256": sha256(payload),
                     "environment_receipt_sha256": environment_sha,
                     "command_plan_sha256": receipt["command_plan_sha256"],
                     "artifact_ledger_sha256": receipt["artifact_ledger_sha256"]})
    if len(set(environment_hashes)) != 1:
        raise FlowError("A2/A3 qualifications do not bind the same environment snapshot bytes")
    document = seal({
        "schema": "k2_single_edge_diagnostic_cohort_v1",
        "decision": "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE",
        "contract_sha256": contract_sha, "candidate_order": ["a2", "a3"],
        "rows": rows,
        "diagnostic_same_environment_snapshot_sha256": environment_hashes[0],
        "diagnostic_evidence_scope": "CALLER_SELF_SEALED_UNAUTHENTICATED_ONLY",
        "producer_authenticated": False, "freshness_verified": False,
        "comparison_ready": False, "candidate_physical_go": False,
        "promotion_requires_new_reviewed_contract": True,
        "promotion_requires_out_of_band_producer_authentication": True,
        "promotion_requires_controlled_runner_and_freshness_authority": True,
    })
    write_exclusive(output, document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    static = sub.add_parser("static")
    static.add_argument("--output", type=Path)
    compatibility = sub.add_parser("compatibility")
    compatibility.add_argument("--output", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("--design", choices=("a2", "a3"), required=True)
    plan.add_argument("--attempt-root", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    environment = sub.add_parser("capture-environment")
    environment.add_argument("--pdk-root", type=Path, required=True)
    environment.add_argument("--genus", type=Path, required=True)
    environment.add_argument("--innovus", type=Path, required=True)
    environment.add_argument("--output", type=Path, required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--design", choices=("a2", "a3"), required=True)
    execute.add_argument("--stage", choices=("genus", "innovus"), required=True)
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--environment", type=Path, required=True)
    execute.add_argument("--authorization", required=True)
    ledger = sub.add_parser("build-ledger")
    ledger.add_argument("--design", choices=("a2", "a3"), required=True)
    ledger.add_argument("--attempt-root", type=Path, required=True)
    ledger.add_argument("--plan", type=Path, required=True)
    ledger.add_argument("--output", type=Path, required=True)
    gate = sub.add_parser("qualify")
    gate.add_argument("--design", choices=("a2", "a3"), required=True)
    gate.add_argument("--attempt-root", type=Path, required=True)
    gate.add_argument("--environment", type=Path, required=True)
    gate.add_argument("--plan", type=Path, required=True)
    gate.add_argument("--ledger", type=Path, required=True)
    gate.add_argument("--output", type=Path, required=True)
    cohort = sub.add_parser("bind-cohort")
    cohort.add_argument("--a2-qualification", type=Path, required=True)
    cohort.add_argument("--a3-qualification", type=Path, required=True)
    cohort.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "static":
            receipt = static_preflight(args.output)
            print(f"K2_SINGLE_EDGE_STATIC_PASS sha256={receipt['document_sha256']}")
        elif args.command == "compatibility":
            receipt = local_compatibility_preflight(args.output)
            print("K2_SINGLE_EDGE_LOCAL_COMPATIBILITY_PASS "
                  f"sha256={receipt['document_sha256']}")
        elif args.command == "plan":
            receipt = make_plan(args.design, args.attempt_root, args.output)
            print(f"K2_SINGLE_EDGE_PLAN_PASS sha256={receipt['document_sha256']}")
        elif args.command == "capture-environment":
            receipt = capture_environment(args.pdk_root, args.genus, args.innovus, args.output)
            print(f"K2_SINGLE_EDGE_LIVE_ENV_SNAPSHOT_PASS sha256={receipt['document_sha256']}")
        elif args.command == "execute":
            receipt = execute_stage(args.design, args.stage, args.plan, args.environment,
                                    args.authorization)
            print(f"K2_SINGLE_EDGE_{args.stage.upper()}_EXECUTION_PASS "
                  f"sha256={receipt['document_sha256']}")
        elif args.command == "build-ledger":
            receipt = build_ledger(args.design, args.attempt_root, args.plan, args.output)
            print(f"K2_SINGLE_EDGE_LEDGER_PASS sha256={receipt['document_sha256']}")
        elif args.command == "qualify":
            receipt = qualify(args.design, args.attempt_root, args.environment,
                              args.plan, args.ledger, args.output)
            print(f"K2_SINGLE_EDGE_{receipt['decision']} sha256={receipt['document_sha256']}")
        else:
            receipt = bind_cohort(args.a2_qualification, args.a3_qualification,
                                  args.output)
            print(f"K2_SINGLE_EDGE_COHORT_{receipt['decision']} "
                  f"sha256={receipt['document_sha256']}")
        return 0
    except (FlowError, OSError, UnicodeDecodeError, ValueError) as error:
        print(f"K2_SINGLE_EDGE_FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
