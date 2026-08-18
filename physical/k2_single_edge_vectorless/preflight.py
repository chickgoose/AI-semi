#!/usr/bin/env python3
"""Fail-closed A2/A3 mapped Genus default-vectorless diagnostic gate.

The current I/O constraints are unconfirmed placeholders and there is no
controlled producer.  Every supported result is therefore HOLD; structural
artifact checks never imply producer authentication, equivalence, or signoff.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_PATH = HERE / "contract.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
TCL_SAFE_PATH_RE = re.compile(r"/[A-Za-z0-9_./:+-]+")
HOLD_STATUS = "HOLD_PLACEHOLDER_IO_AND_NO_CONTROLLED_PRODUCER"
EXPECTED_INPUTS = ["clk_i", "rst_i", "link_enable_i", "source_pending_i[15:0]"]
EXPECTED_OUTPUTS = [
    "source_accept_o[15:0]", "accept_count_o[1:0]", "accept_addr0_o[3:0]",
    "accept_addr1_o[3:0]", "link_valid_o", "link_addr0_o[3:0]",
    "link_addr1_o[3:0]", "retire_valid_o[1:0]", "retire_addr0_o[3:0]",
    "retire_addr1_o[3:0]", "protocol_error_o", "drain_idle_o",
]
FORBIDDEN_ACTIVITY_TOKENS = {
    "read_vcd", "read_saif", "read_tcf", "read_activity",
    "read_activity_file", "set_switching_activity",
    "set_default_switching_activity", "set_power_activity", "set_activity",
    "lp_toggle_rate", "lp_static_probability", "toggle_rate",
    "static_probability", ".vcd", ".saif", ".tcf",
}
DIAGNOSTIC_RECEIPT_KEYS = {
    "schema", "status", "evidence_class", "candidate", "architecture",
    "interface", "boundary", "lineage", "top", "capture", "operating_point",
    "activity_policy", "tool", "execution", "artifacts",
    "complete_artifact_ledger_sha256",
}
EXECUTION_KEYS = {
    "argv", "cwd", "exit_code", "semantic_environment",
    "semantic_environment_sha256",
}


class EvidenceError(ValueError):
    """The requested diagnostic input is incomplete or inconsistent."""


def canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise EvidenceError(f"value is not canonical JSON: {error}") from error


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identity(row: os.stat_result) -> tuple[int, ...]:
    return (row.st_dev, row.st_ino, row.st_mode, row.st_nlink, row.st_size,
            row.st_mtime_ns, row.st_ctime_ns)


def stable_read(path: Path, label: str, *, single_link: bool = True) -> bytes:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise EvidenceError(f"{label} is not a regular non-symlink file")
        if single_link and before.st_nlink != 1:
            raise EvidenceError(f"{label} must be a single-link file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after_read = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = path.lstat()
    except OSError as error:
        raise EvidenceError(f"cannot read {label}: {path}: {error}") from error
    if not (_identity(before) == _identity(opened) ==
            _identity(after_read) == _identity(after)):
        raise EvidenceError(f"{label} changed while being read")
    return b"".join(chunks)


def _directory_identity(path: Path, label: str) -> tuple[int, ...]:
    try:
        row = path.lstat()
    except OSError as error:
        raise EvidenceError(f"cannot inspect {label}: {path}: {error}") from error
    if stat.S_ISLNK(row.st_mode) or not stat.S_ISDIR(row.st_mode):
        raise EvidenceError(f"{label} is not a non-symlink directory")
    return _identity(row)


def _require_directory_identity(path: Path, expected: tuple[int, ...],
                                label: str) -> None:
    if _directory_identity(path, label) != expected:
        raise EvidenceError(f"{label} changed during validation")


def _tcl_safe_absolute_path(path: Path, label: str) -> str:
    text = str(path)
    if (not path.is_absolute() or Path(os.path.normpath(text)) != path or
            TCL_SAFE_PATH_RE.fullmatch(text) is None):
        raise EvidenceError(
            f"{label} must be an absolute Tcl-list-safe path without whitespace or metacharacters")
    return text


def parse_json(payload: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise EvidenceError(f"non-finite JSON number in {label}: {value}")

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicates,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"invalid JSON in {label}: {error}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def read_json(path: Path, label: str, *, single_link: bool = True
              ) -> tuple[dict[str, Any], bytes]:
    payload = stable_read(path, label, single_link=single_link)
    return parse_json(payload, label), payload


def exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise EvidenceError(
            f"{label} keys mismatch; missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}")
    return value


def string(value: Any, label: str,
           pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise EvidenceError(f"{label} has invalid syntax")
    return value


def digest(value: Any, label: str) -> str:
    return string(value, label, SHA256_RE)


def integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvidenceError(f"{label} must be an integer >= {minimum}")
    return value


def write_exclusive(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
             getattr(os, "O_NOFOLLOW", 0))
    payload = canonical(document)
    try:
        descriptor = os.open(path, flags, 0o644)
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise EvidenceError(f"cannot create immutable output {path}: {error}") from error


def _repo_file(root: Path, relative: Any, label: str) -> Path:
    text = string(relative, label)
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != text:
        raise EvidenceError(f"{label} must be a normalized repository-relative path")
    resolved_root = root.resolve(strict=True)
    resolved = (resolved_root / candidate).resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise EvidenceError(f"{label} escapes repository") from error
    return resolved


def _forbid_lineage_text(value: str, label: str) -> None:
    lowered = value.lower()
    if any(token in lowered for token in
           ("p6", "synthetic", "inherited", ".vcd", ".saif", ".tcf")):
        raise EvidenceError(
            f"{label} contains forbidden P6/synthetic/inherited/activity lineage")


def _committed_payload(root: Path, commit: str, relative: str, label: str) -> bytes:
    """Read a source identity from an explicitly pinned hardened commit.

    The integration branch may not yet contain that commit when this diagnostic
    flow is reviewed, so the immutable Git object is the sole source authority.
    Server attempts must snapshot these bytes rather than compile a checkout.
    """
    try:
        completed = subprocess.run(
            ["git", "show", f"{commit}:{relative}"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as error:
        raise EvidenceError(f"cannot inspect pinned commit for {label}: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise EvidenceError(f"missing pinned commit object for {label}: {detail}")
    return completed.stdout


def _rtl_ansi_ports(payload: bytes, top: str) -> tuple[list[str], list[str]]:
    """Extract the deliberately simple ANSI port declarations of a pinned top."""
    text = payload.decode("utf-8", errors="strict")
    matches = re.findall(
        rf"(?ms)^\s*module\s+{re.escape(top)}\s*\((.*?)\)\s*;", text)
    if len(matches) != 1:
        raise EvidenceError(f"{top} must have one exact module declaration")
    ports: dict[str, list[str]] = {"input": [], "output": []}
    declarations = re.findall(
        r"(?m)^\s*(input|output)\s+(?:logic|wire|reg)\s*"
        r"(\[[0-9]+:[0-9]+\])?\s*([A-Za-z_][A-Za-z0-9_$]*)\s*,?\s*$",
        matches[0])
    for direction, width, name in declarations:
        ports[direction].append(name + width)
    return ports["input"], ports["output"]


def reject_activity(payload: bytes, label: str,
                    forbidden_tokens: list[str]) -> None:
    try:
        lowered = payload.decode("utf-8", errors="strict").lower()
    except UnicodeError as error:
        raise EvidenceError(f"{label} is not UTF-8") from error
    for token in forbidden_tokens:
        if token.lower() in lowered:
            raise EvidenceError(f"{label} contains forbidden activity token {token}")


def reject_cadence_diagnostics(text: str, label: str, *,
                               require_terminal_summary: bool = False) -> None:
    """Reject contradictory Cadence error/fatal diagnostics.

    Cadence outputs use several forms (``Error=3``, ``3 error(s)``, and
    ``**ERROR (CODE):``).  A zero summary must never mask another nonzero or
    textual error/fatal diagnostic elsewhere in the same artifact.
    """
    summaries = re.findall(
        r"(?i)\bInfo=(\d+),\s*Warn=(\d+),\s*Error=(\d+),\s*Fatal=(\d+)\b",
        text)
    if require_terminal_summary:
        if len(summaries) != 1 or summaries[0][2:] != ("0", "0"):
            raise EvidenceError(
                f"{label} requires exactly one zero-error/zero-fatal summary")
    elif any(int(error_count) or int(fatal_count)
             for _, _, error_count, fatal_count in summaries):
        raise EvidenceError(f"{label} contains a nonzero Cadence summary")

    count_patterns = (
        r"(?i)\b(?:errors?|fatals?)(?:\(s\))?\s*[:=]\s*(\d+)\b",
        r"(?i)\b(\d+)\s+(?:errors?|fatals?)(?:\(s\))?\b",
    )
    for pattern in count_patterns:
        if any(int(value) != 0 for value in re.findall(pattern, text)):
            raise EvidenceError(f"{label} contains a nonzero error/fatal count")

    for line in text.splitlines():
        if re.match(r"(?i)^\s*(?:\*\*\s*)?(?:errors?|fatals?)\b", line):
            if re.fullmatch(
                    r"(?i)\s*(?:errors?|fatals?)\s*[:=]\s*0\s*", line) is None:
                raise EvidenceError(f"{label} contains an error/fatal diagnostic")


def load_contract(root: Path = ROOT) -> tuple[dict[str, Any], bytes]:
    expected = root.resolve(strict=True) / "physical/k2_single_edge_vectorless/contract.json"
    if expected != CONTRACT_PATH.resolve(strict=True):
        raise EvidenceError("entrypoint and repository root do not match")
    return read_json(expected, "single-edge vectorless contract")


def validate_contract(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    exact_keys(contract, {
        "schema", "status", "evidence_class", "decision_policy",
        "policy_binding", "source_pins", "constraint_authority", "candidates", "operating_point",
        "activity_policy", "tool", "execution_policy", "artifact_policy",
        "diagnostic_provenance", "templates",
    }, "contract")
    if (contract["schema"] != "k2_single_edge_vectorless_contract_v2" or
            contract["status"] != "DIAGNOSTIC_ONLY_PLACEHOLDER_IO_NO_CONTROLLED_PRODUCER" or
            contract["evidence_class"] !=
            "GENUS_MAPPED_A2_A3_SINGLE_EDGE_DEFAULT_VECTORLESS_DIAGNOSTIC"):
        raise EvidenceError("contract identity/status mismatch")

    decision = exact_keys(contract["decision_policy"], {
        "candidate_order", "exact_cohort_required", "release_interface",
        "transfer_mode", "synthetic_allowed", "inherited_allowed", "p6_allowed",
        "borrowed_dependency_ids_allowed", "candidate_go_possible",
        "comparison_ready_possible",
    }, "decision_policy")
    if decision != {
        "candidate_order": ["a2_single_edge", "a3_single_edge"],
        "exact_cohort_required": True,
        "release_interface": "PARALLEL_FALLBACK",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "synthetic_allowed": False,
        "inherited_allowed": False,
        "p6_allowed": False,
        "borrowed_dependency_ids_allowed": [],
        "candidate_go_possible": False,
        "comparison_ready_possible": False,
    }:
        raise EvidenceError("single-edge exact-cohort decision policy changed")

    policy = exact_keys(contract["policy_binding"], {
        "commit", "path", "sha256", "required_disallowed_dependencies",
    }, "policy_binding")
    _repo_file(root, policy["path"], "policy_binding.path")
    if policy["commit"] != "95ffa7ec31639542c585ed678961265c31d67be5":
        raise EvidenceError("REDRED policy commit mismatch")
    policy_payload = _committed_payload(
        root, policy["commit"], policy["path"], "REDRED system policy")
    if sha256(policy_payload) != digest(policy["sha256"], "policy_binding.sha256"):
        raise EvidenceError("REDRED policy SHA mismatch")
    policy_doc = parse_json(policy_payload, "REDRED system policy")
    fallback = policy_doc.get("interfaces", {}).get("PARALLEL_FALLBACK", {})
    required_disallowed = [
        "INHERITED_6P5_STANDARD_CELL_REFERENCE",
        "P6_VECTORLESS_POWER",
        "P6_PAD_PACKAGE_CHANNEL",
    ]
    io_authority = policy_doc.get("external_data_and_coordinate_policy", {}).get(
        "pdk_endpoint_io_rules", {})
    if (policy_doc.get("goal_policy", {}).get("selected_release_interface") !=
            "PARALLEL_FALLBACK" or
            policy_doc.get("goal_policy", {}).get("selected_release_interface_status") !=
            "IMPLEMENTED_RELEASE_HELD" or
            policy_doc.get("canonical_digital_dependency", {}).get("status") !=
            "PASS_SCOPED_NATIVE_CAMPAIGN" or
            fallback.get("competition_release_status") !=
            "HOLD_INCOMPLETE_MAPPED_PHYSICAL_POWER_AND_SELECTION" or
            fallback.get("transfer_mode") != "SINGLE_EDGE_PARALLEL" or
            fallback.get("may_borrow_p6_physical_evidence") is not False or
            fallback.get("disallowed_borrowed_dependencies") != required_disallowed or
            policy["required_disallowed_dependencies"] != required_disallowed or
            io_authority.get("status") != "HOLD" or
            io_authority.get("inherited_6p5_values_are_final_competition_rules") is not False):
        raise EvidenceError("REDRED HOLD/I/O-authority/P6 policy mismatch")

    authority = exact_keys(contract["constraint_authority"], {
        "status", "evidence_class", "external_authority_available",
        "values_are_release_claims", "candidate_go_eligible",
        "comparison_ready_eligible", "maximum_status",
    }, "constraint_authority")
    if authority != {
        "status": "UNCONFIRMED_TEAM_PLACEHOLDER",
        "evidence_class": "TEAM_PLACEHOLDER_SCREENING_ONLY",
        "external_authority_available": False,
        "values_are_release_claims": False,
        "candidate_go_eligible": False,
        "comparison_ready_eligible": False,
        "maximum_status": HOLD_STATUS,
    }:
        raise EvidenceError("placeholder I/O authority fail-closure changed")

    source_identity = exact_keys(contract["source_pins"], {"path", "sha256"},
                                 "source_pins")
    source_path = _repo_file(root, source_identity["path"], "source_pins.path")
    source_doc, source_payload = read_json(source_path, "source pins")
    if sha256(source_payload) != digest(source_identity["sha256"],
                                        "source_pins.sha256"):
        raise EvidenceError("source pin manifest SHA mismatch")
    exact_keys(source_doc, {"schema", "equivalent_hardened_commits", "candidates"},
               "source pins")
    hardened_commits = source_doc["equivalent_hardened_commits"]
    if (source_doc["schema"] != "k2_single_edge_vectorless_source_pins_v2" or
            hardened_commits != [
                "a0a4eb38632245db8ff5937ea5b6c6e3f3839246",
                "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b",
            ]):
        raise EvidenceError("source pin schema mismatch")

    candidates = contract["candidates"]
    if not isinstance(candidates, dict) or list(candidates) != decision["candidate_order"]:
        raise EvidenceError("candidate roster/order mismatch")
    if source_doc["candidates"].keys() != candidates.keys():
        raise EvidenceError("source pin candidate set mismatch")
    expected_tops = {
        "a2_single_edge": "a2_batched_iwrr_single_edge_top",
        "a3_single_edge": "a3_exact_scalar_prefix_k2_single_edge_top",
    }
    expected_arch = {"a2_single_edge": "A2", "a3_single_edge": "A3"}
    for candidate in decision["candidate_order"]:
        row = exact_keys(candidates[candidate], {
            "architecture", "top", "boundary", "inputs", "outputs",
        }, f"candidates.{candidate}")
        if (row["architecture"] != expected_arch[candidate] or
                row["top"] != expected_tops[candidate] or
                row["boundary"] !=
                "SOURCE_PENDING_ACCEPT_THROUGH_SINGLE_EDGE_LINK_RETIRE" or
                row["inputs"] != EXPECTED_INPUTS or
                row["outputs"] != EXPECTED_OUTPUTS):
            raise EvidenceError(f"{candidate} complete-boundary contract mismatch")
        _forbid_lineage_text(row["top"], f"{candidate} top")
        pins = exact_keys(source_doc["candidates"][candidate],
                          {"architecture", "top", "filelists", "sources"},
                          f"source pins {candidate}")
        if pins["architecture"] != row["architecture"] or pins["top"] != row["top"]:
            raise EvidenceError(f"{candidate} source/top pin mismatch")
        if (not isinstance(pins["filelists"], list) or len(pins["filelists"]) != 2 or
                not isinstance(pins["sources"], list) or len(pins["sources"]) != 6):
            raise EvidenceError(f"{candidate} source closure is incomplete")
        seen: set[str] = set()
        top_payload: bytes | None = None
        committed_by_path: dict[str, bytes] = {}
        for kind, identities in (("filelist", pins["filelists"]),
                                 ("source", pins["sources"])):
            for index, source in enumerate(identities):
                exact_keys(source, {"path", "sha256"},
                           f"{kind} pins {candidate}[{index}]")
                source_rel = string(
                    source["path"], f"{kind} {candidate}[{index}].path")
                _forbid_lineage_text(
                    source_rel, f"{kind} {candidate}[{index}].path")
                if source_rel in seen:
                    raise EvidenceError(f"duplicate source/filelist in {candidate}")
                seen.add(source_rel)
                committed = [
                    _committed_payload(root, commit, source_rel,
                                       f"{kind} {source_rel} at {commit}")
                    for commit in hardened_commits
                ]
                if committed[0] != committed[1]:
                    raise EvidenceError(
                        f"hardened commits disagree for {kind}: {source_rel}")
                if sha256(committed[0]) != digest(source["sha256"], "source SHA"):
                    raise EvidenceError(f"committed {kind} SHA mismatch: {source_rel}")
                committed_by_path[source_rel] = committed[0]
                if source_rel.endswith(f"/{row['top']}.sv"):
                    top_payload = committed[0]
        if top_payload is None or _rtl_ansi_ports(top_payload, row["top"]) != (
                EXPECTED_INPUTS, EXPECTED_OUTPUTS):
            raise EvidenceError(f"{candidate} committed top port set mismatch")
        candidate_filelist = pins["filelists"][0]["path"]
        generic_filelist = pins["filelists"][1]["path"]
        candidate_lines = [line.strip() for line in
                           committed_by_path[candidate_filelist].decode().splitlines()
                           if line.strip()]
        generic_lines = [line.strip() for line in
                         committed_by_path[generic_filelist].decode().splitlines()
                         if line.strip()]
        if candidate_lines.count(f"-f {generic_filelist}") != 1:
            raise EvidenceError(f"{candidate} candidate filelist nesting mismatch")
        expanded = []
        for line in candidate_lines:
            expanded.extend(generic_lines if line == f"-f {generic_filelist}" else [line])
        if expanded != [identity["path"] for identity in pins["sources"]]:
            raise EvidenceError(f"{candidate} committed filelist/source expansion mismatch")

    expected_operating = {
        "corner": {"pdk": "GPDK045/gsclib045", "process": 1.0,
                   "voltage_v": 0.9, "temperature_c": 125.0,
                   "power_liberty_role": "setup_slow"},
        "clock": {"port": "clk_i", "name": "single_edge_clk", "period_ns": 6.5,
                  "waveform_ns": [0.0, 3.25], "uncertainty_ns": 0.25,
                  "min_pulse_high_ns": 0.5, "min_pulse_low_ns": 0.5},
        "io": {"input_delay_min_ns": 0.1, "input_delay_max_ns": 0.5,
               "output_delay_min_ns": 0.1, "output_delay_max_ns": 0.5,
               "input_transition_ns": 0.05},
        "load": {"all_outputs_pf": 0.01},
        "libraries": {
            "setup": {"relative_path": "timing/slow_vdd1v0_basicCells.lib",
                      "sha256": "dec616b7b53aa5166eac9660ba83561a4057ee3b7e62f59f3d4bebad495ffe10"},
            "hold": {"relative_path": "timing/fast_vdd1v0_basicCells.lib",
                     "sha256": "e63762d156fd929cde2f58b0a5883020d6f16f0a41d3736577d0af6b94191560"},
        },
    }
    if contract["operating_point"] != expected_operating:
        raise EvidenceError("corner/clock/I/O/load/Liberty operating point changed")

    activity = exact_keys(contract["activity_policy"], {
        "mode", "activity_annotated", "primary_input_activity",
        "sequential_element_activity", "required_activity_file_header",
        "required_user_defined_activity_header", "waveform_formats_allowed",
        "per_object_activity_allowed", "forbidden_tokens",
    }, "activity_policy")
    if (activity["mode"] != "GENUS_DEFAULT_VECTORLESS" or
            activity["activity_annotated"] is not False or
            activity["primary_input_activity"] != 0.2 or
            activity["sequential_element_activity"] != 0.2 or
            activity["required_activity_file_header"] != "N.A." or
            activity["required_user_defined_activity_header"] != "N.A." or
            activity["waveform_formats_allowed"] != [] or
            activity["per_object_activity_allowed"] is not False):
        raise EvidenceError("default-vectorless activity policy changed")
    if set(activity["forbidden_tokens"]) != FORBIDDEN_ACTIVITY_TOKENS:
        raise EvidenceError("forbidden activity token policy changed")

    tool = exact_keys(contract["tool"], {
        "name", "version", "requested_path", "resolved_path", "sha256",
    }, "tool")
    if tool != {
        "name": "Cadence Genus", "version": "23.14-s090_1",
        "requested_path": "/tools/cadence/DDI231/GENUS231/bin/genus",
        "resolved_path": "/tools/cadence/DDI231/GENUS231/bin/.cdnWrapperIndep",
        "sha256": "41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa",
    }:
        raise EvidenceError("exact Genus identity changed")

    execution = exact_keys(contract["execution_policy"], {
        "argv_suffix", "semantic_environment_keys", "required_exit_code",
        "require_absolute_cwd_and_driver", "require_in_place_server_root",
    }, "execution_policy")
    expected_env = [
        "K2_SE_TOP", "K2_SE_SOURCES_SV", "K2_SE_LIBRARY", "K2_SE_SDC",
        "K2_SE_OUTPUT", "K2_SE_ACTIVITY_MODE", "LC_ALL",
    ]
    if (execution["argv_suffix"] != ["-batch", "-files"] or
            execution["semantic_environment_keys"] != expected_env or
            execution["required_exit_code"] != 0 or
            execution["require_absolute_cwd_and_driver"] is not True or
            execution["require_in_place_server_root"] is not True):
        raise EvidenceError("execution binding policy changed")

    artifact_policy = exact_keys(contract["artifact_policy"], {
        "fixed_roles", "source_role_prefix", "filelist_role_prefix",
        "all_files_regular_single_link", "complete_ledger_required",
    }, "artifact_policy")
    if (len(artifact_policy["fixed_roles"]) != 19 or
            len(set(artifact_policy["fixed_roles"])) != 19 or
            artifact_policy["source_role_prefix"] != "source_" or
            artifact_policy["filelist_role_prefix"] != "filelist_" or
            artifact_policy["all_files_regular_single_link"] is not True or
            artifact_policy["complete_ledger_required"] is not True):
        raise EvidenceError("complete artifact policy changed")

    provenance = exact_keys(contract["diagnostic_provenance"], {
        "accepted_receipt_schema", "accepted_origin", "controlled_runner_available",
        "live_host_binding_available", "freshness_or_replay_protection_available",
        "keyring_or_hmac_accepted", "structural_validation_implies_equivalence",
        "structural_validation_implies_signoff", "maximum_status",
    }, "diagnostic_provenance")
    if provenance != {
        "accepted_receipt_schema": "k2_single_edge_vectorless_diagnostic_receipt_v2",
        "accepted_origin": "UNCONTROLLED_EXTERNAL_CAPTURE",
        "controlled_runner_available": False,
        "live_host_binding_available": False,
        "freshness_or_replay_protection_available": False,
        "keyring_or_hmac_accepted": False,
        "structural_validation_implies_equivalence": False,
        "structural_validation_implies_signoff": False,
        "maximum_status": HOLD_STATUS,
    }:
        raise EvidenceError("diagnostic-only provenance policy changed")

    templates = exact_keys(contract["templates"],
                           {"driver", "sdc", "diagnostic_receipt"}, "templates")
    payloads: dict[str, bytes] = {}
    for name, identity in templates.items():
        exact_keys(identity, {"path", "sha256"}, f"templates.{name}")
        payload = stable_read(_repo_file(root, identity["path"], f"templates.{name}.path"),
                              f"{name} template")
        if sha256(payload) != digest(identity["sha256"], f"templates.{name}.sha256"):
            raise EvidenceError(f"{name} template SHA mismatch")
        payloads[name] = payload
    reject_activity(payloads["driver"], "Genus driver", activity["forbidden_tokens"])
    driver_text = payloads["driver"].decode("utf-8")
    for command in ("syn_generic", "syn_map", "syn_opt", "write_hdl",
                    "write_sdc", "write_sdf", "check_design"):
        if len(re.findall(rf"(?m)^\s*{command}\b", driver_text)) != 1:
            raise EvidenceError(f"Genus driver requires exactly one {command}")
    if len(re.findall(r"(?m)^\s*report_power\b", driver_text)) != 1:
        raise EvidenceError("Genus driver requires exactly one report_power")
    if ("K2_SE_ACTIVITY_MODE" not in driver_text or
            "GENUS_DEFAULT_VECTORLESS" not in driver_text or
            "K2_SINGLE_EDGE_VECTORLESS_DIAGNOSTIC_COMPLETE" not in driver_text):
        raise EvidenceError("Genus driver lacks diagnostic activity/completion guard")
    sdc_text = payloads["sdc"].decode("utf-8")
    for required in ("create_clock -name single_edge_clk -period 6.500",
                     "set_input_delay -clock single_edge_clk -min 0.100",
                     "set_input_delay -clock single_edge_clk -max 0.500",
                     "set_output_delay -clock single_edge_clk -min 0.100",
                     "set_output_delay -clock single_edge_clk -max 0.500",
                     "set_input_transition 0.050", "set_load 0.010"):
        if required not in sdc_text:
            raise EvidenceError(f"strict SDC missing exact constraint: {required}")
    if (sdc_text.count("create_clock ") != 1 or
            "UNCONFIRMED_TEAM_PLACEHOLDER" not in sdc_text or
            re.search(r"(?i)\b(?:create_generated_clock|false_path|multicycle|p6|negedge|falling)\b",
                      sdc_text)):
        raise EvidenceError("single-edge SDC contains borrowed/multi-edge exception")
    template_doc = exact_keys(
        parse_json(payloads["diagnostic_receipt"], "receipt template"),
        DIAGNOSTIC_RECEIPT_KEYS, "receipt template")
    template_execution = exact_keys(
        template_doc["execution"], EXECUTION_KEYS, "receipt template execution")
    exact_keys(template_doc["lineage"],
               {"synthetic", "inherited", "p6", "borrowed_dependency_ids"},
               "receipt template lineage")
    exact_keys(template_doc["capture"], {
        "origin", "capture_id", "claimed_host_fingerprint_sha256",
        "started_utc", "finished_utc",
    }, "receipt template capture")
    if (template_doc["schema"] != provenance["accepted_receipt_schema"] or
            template_doc["status"] != "HOLD_TEMPLATE_NOT_DIAGNOSTIC_ARTIFACTS" or
            template_doc["evidence_class"] != contract["evidence_class"] or
            template_doc["interface"] != "SINGLE_EDGE_PARALLEL" or
            template_doc["lineage"] != {
                "synthetic": False, "inherited": False, "p6": False,
                "borrowed_dependency_ids": [],
            } or
            template_doc["capture"] != {
                "origin": provenance["accepted_origin"], "capture_id": None,
                "claimed_host_fingerprint_sha256": None,
                "started_utc": None, "finished_utc": None,
            } or
            any(template_doc[field] is not None for field in (
                "candidate", "architecture", "boundary", "top", "operating_point",
                "activity_policy", "tool", "artifacts",
                "complete_artifact_ledger_sha256")) or
            any(value is not None for value in template_execution.values())):
        raise EvidenceError("receipt template must remain diagnostic-only HOLD")
    return source_doc


def preflight(root: Path, output: Path) -> Path:
    contract, contract_payload = load_contract(root)
    source_doc = validate_contract(root, contract)
    result = {
        "schema": "k2_single_edge_vectorless_preflight_v2",
        "status": HOLD_STATUS,
        "comparison_ready": False,
        "candidate_go": False,
        "reason": "I/O/load constraints are unconfirmed placeholders and no controlled producer, live-host binding, or freshness authority exists",
        "evidence_class": contract["evidence_class"],
        "candidate_order": contract["decision_policy"]["candidate_order"],
        "release_interface": "PARALLEL_FALLBACK",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "power_method": "GENUS_MAPPED_DEFAULT_VECTORLESS",
        "activity_annotated": False,
        "constraint_authority": contract["constraint_authority"]["status"],
        "controlled_producer_available": False,
        "contract_sha256": sha256(contract_payload),
        "source_pins_sha256": contract["source_pins"]["sha256"],
        "source_tops": {key: row["top"]
                        for key, row in source_doc["candidates"].items()},
        "template_sha256": {key: value["sha256"]
                            for key, value in contract["templates"].items()},
    }
    write_exclusive(output, result)
    return output


def _contained_directory(root: Path, relative_value: Any, label: str) -> Path:
    text = string(relative_value, label)
    relative = Path(text)
    if (relative.is_absolute() or ".." in relative.parts or
            relative.as_posix() != text or not relative.parts):
        raise EvidenceError(f"{label} must be a normalized contained path")
    _forbid_lineage_text(text, label)
    current = root.resolve(strict=True)
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as error:
            raise EvidenceError(f"cannot inspect {label}: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise EvidenceError(f"{label} contains a symlink")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise EvidenceError(f"{label} escapes evidence root") from error
    if not resolved.is_dir():
        raise EvidenceError(f"{label} is not a directory")
    return resolved


def read_beneath(root: Path, relative_value: Any, label: str) -> bytes:
    text = string(relative_value, f"{label}.path")
    relative = Path(text)
    if (relative.is_absolute() or ".." in relative.parts or
            relative.as_posix() != text or not relative.parts or "\x00" in text):
        raise EvidenceError(f"{label}.path must be normalized and contained")
    _forbid_lineage_text(text, f"{label}.path")
    directory_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                       getattr(os, "O_NOFOLLOW", 0))
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        descriptor = os.open(root, directory_flags)
        descriptors.append(descriptor)
        for part in relative.parts[:-1]:
            descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            descriptors.append(descriptor)
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=descriptor)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise EvidenceError(f"{label} must be a regular single-link file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise EvidenceError(f"cannot read contained {label}: {error}") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if _identity(before) != _identity(after):
        raise EvidenceError(f"{label} changed while being read")
    return b"".join(chunks)


def parse_power_report(payload: bytes, top: str,
                       activity: dict[str, Any]) -> dict[str, float]:
    reject_activity(payload, "power report", activity["forbidden_tokens"])
    text = payload.decode("utf-8")
    reject_cadence_diagnostics(text, "power report")

    def one(pattern: str, label: str) -> str:
        values = re.findall(pattern, text, re.MULTILINE)
        if len(values) != 1:
            raise EvidenceError(f"power report requires exactly one {label}")
        return values[0].strip()

    generated_lines = re.findall(r"(?m)^Generated by:\s*(.*?)\s*$", text)
    if generated_lines != ["Genus(TM) Synthesis Solution"]:
        raise EvidenceError("power report tool identifier mismatch")
    instance = one(r"^Instance:\s+/(\S+)\s*$", "exact Instance identifier")
    if instance != top:
        raise EvidenceError("power report top mismatch")
    if one(r"^\s*\*\s*Activity File\s*:\s*(.*?)\s*$", "Activity File header") != "N.A.":
        raise EvidenceError("power report Activity File must be exactly N.A.")
    if one(r"^\s*\*\s*User-Defined Activity\s*:\s*(.*?)\s*$",
           "User-Defined Activity header") != "N.A.":
        raise EvidenceError("power report User-Defined Activity must be exactly N.A.")
    sequential = one(r"^\s*\*\s*Sequential Element Activity\s*:\s*(\S+)\s*$",
                     "Sequential Element Activity header")
    primary = one(r"^\s*\*\s*Primary Input Activity\s*:\s*(\S+)\s*$",
                  "Primary Input Activity header")
    if sequential != "0.200000" or primary != "0.200000":
        raise EvidenceError("power report does not show native Genus 0.2 defaults")
    unit_lines = re.findall(r"(?m)^\s*Power Unit:\s*(\S+)\s*$", text)
    if unit_lines != ["W"]:
        raise EvidenceError("power report requires exactly one noncontradictory W unit")
    headers = re.findall(
        r"(?m)^\s*(Category\s+Leakage\s+Internal\s+Switching\s+Total)\s*$", text)
    if headers != ["Category         Leakage     Internal    Switching        Total"]:
        raise EvidenceError("power report requires exact native category column order")
    rows = re.findall(
        r"(?m)^\s*Subtotal\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$", text)
    if len(rows) != 1:
        raise EvidenceError("power report must contain exactly one Subtotal row")
    try:
        leakage, internal, switching, total = map(float, rows[0])
    except ValueError as error:
        raise EvidenceError("power report subtotal is nonnumeric") from error
    numbers = (leakage, internal, switching, total)
    if any(not math.isfinite(value) or value < 0 for value in numbers) or total <= 0:
        raise EvidenceError("power report contains invalid power")
    if not math.isclose(total, leakage + internal + switching,
                        rel_tol=2e-5, abs_tol=5e-10):
        raise EvidenceError("power report components do not sum")
    return {"leakage_mw": leakage * 1000, "internal_mw": internal * 1000,
            "switching_mw": switching * 1000, "total_mw": total * 1000}


def validate_genus_diagnostic_log(payload: bytes, top: str,
                                  contract: dict[str, Any]) -> None:
    reject_activity(payload, "Genus log", contract["activity_policy"]["forbidden_tokens"])
    text = payload.decode("utf-8")
    reject_cadence_diagnostics(text, "Genus log", require_terminal_summary=True)
    if (text.count(f"Version: {contract['tool']['version']}") != 1 or
            text.count(f"K2_SINGLE_EDGE_VECTORLESS_DIAGNOSTIC_COMPLETE top={top}") != 1 or
            text.count("Normal exit.") != 1):
        raise EvidenceError(
            "Genus log lacks version/diagnostic-completion/zero-error/normal-exit context")


def validate_structural_netlist(payload: bytes, top: str) -> dict[str, int]:
    text = payload.decode("utf-8", errors="strict")
    modules = re.findall(r"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", text)
    if modules != [top] or len(re.findall(r"(?m)^\s*endmodule\b", text)) != 1:
        raise EvidenceError("mapped netlist requires exactly the expected top module")
    if re.search(r"(?i)\bp6\b", text):
        raise EvidenceError("mapped netlist contains P6 lineage")
    if re.search(r"(?mi)^\s*(?:always|initial)\b", text):
        raise EvidenceError("mapped netlist is behavioral, not mapped")

    declared: dict[str, set[str]] = {"input": set(), "output": set()}
    for direction, width, names in re.findall(
            r"(?ms)\b(input|output)\b\s+(?:(?:wire|reg|logic)\s+)?"
            r"(\[[^\]]+\])?\s*([^;]+);", text):
        normalized_width = re.sub(r"\s+", "", width)
        for raw_name in names.split(","):
            name = raw_name.strip().split("=")[0].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
                declared[direction].add(name + normalized_width)
    if declared["input"] != set(EXPECTED_INPUTS) or \
            declared["output"] != set(EXPECTED_OUTPUTS):
        raise EvidenceError("mapped netlist complete-boundary port set mismatch")

    keywords = {"module", "input", "output", "inout", "wire", "reg", "logic",
                "assign", "always", "initial", "if", "for", "case", "function"}
    instances = [match.group(1) for match in re.finditer(
        r"(?m)^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+"
        r"(?:#\s*\([^;]*?\)\s*)?[A-Za-z_\\][^\s(]*\s*\(", text)
        if match.group(1).lower() not in keywords]
    if not instances:
        raise EvidenceError("mapped netlist has no structural cell instances")

    # This is deliberately not an equivalence proof, but a diagnostic netlist
    # must at least drive every externally visible output.  Count continuous
    # assignment LHSs and conventional standard-cell output pins.  Bit-select
    # drivers may repeat a bus base, while an exact signal may have one driver.
    driven_signals = [re.sub(r"\s+", "", match) for match in re.findall(
        r"(?m)^\s*assign\s+([A-Za-z_][A-Za-z0-9_$]*(?:\s*\[[^\]]+\])?)\s*=",
        text)]
    driven_signals.extend(re.sub(r"\s+", "", match) for match in re.findall(
        r"\.(?:Q|QN|Y|Z|ZN|ECK|IQ|SO|CO)\s*\(\s*"
        r"([A-Za-z_][A-Za-z0-9_$]*(?:\s*\[[^\]]+\])?)\s*\)", text))
    if len(driven_signals) != len(set(driven_signals)):
        raise EvidenceError("mapped netlist contains a multiply driven output signal")
    output_bases = [value.split("[")[0] for value in EXPECTED_OUTPUTS]
    missing_drivers = [base for base in output_bases if not any(
        signal == base or signal.startswith(base + "[") for signal in driven_signals)]
    if missing_drivers:
        raise EvidenceError(
            "mapped netlist has undriven top outputs: " + ",".join(missing_drivers))
    return {
        "structural_instance_count": len(instances),
        "driven_output_count": len(output_bases),
    }


def validate_mapped_sdc(payload: bytes,
                        forbidden_tokens: list[str] | None = None) -> None:
    reject_activity(payload, "mapped SDC",
                    forbidden_tokens or sorted(FORBIDDEN_ACTIVITY_TOKENS))
    text = payload.decode("utf-8", errors="strict")
    if re.search(
            r"(?i)\b(?:create_generated_clock|set_false_path|set_multicycle_path|"
            r"negedge|falling|p6)\b", text):
        raise EvidenceError("mapped SDC contains a timing exception or multi-edge lineage")
    expected_lines = [
        ("create_clock -name single_edge_clk -period 6.500 "
         "-waveform {0.000 3.250} [get_ports clk_i]"),
        "set_clock_uncertainty 0.250 [get_clocks single_edge_clk]",
        "set nonclock_inputs [remove_from_collection [all_inputs] [get_ports clk_i]]",
        "set_input_delay -clock single_edge_clk -min 0.100 $nonclock_inputs",
        "set_input_delay -clock single_edge_clk -max 0.500 $nonclock_inputs",
        "set_input_transition 0.050 $nonclock_inputs",
        "set_output_delay -clock single_edge_clk -min 0.100 [all_outputs]",
        "set_output_delay -clock single_edge_clk -max 0.500 [all_outputs]",
        "set_load 0.010 [all_outputs]",
        "set_min_pulse_width -high 0.500 [get_clocks single_edge_clk]",
        "set_min_pulse_width -low 0.500 [get_clocks single_edge_clk]",
    ]
    active_lines = [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    if active_lines != expected_lines:
        raise EvidenceError(
            "mapped SDC command cardinality, order, value, or exact selector mismatch")


def _native_report(payload: bytes, top: str, label: str) -> str:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label} is not UTF-8") from error
    if len(payload) < 80:
        raise EvidenceError(f"{label} lacks native report context")
    generated = re.findall(
        r"(?m)^\s*Generated by:\s*(Genus\(TM\) Synthesis Solution(?:\s+[^\n]+)?)\s*$",
        text)
    if len(generated) != 1:
        raise EvidenceError(f"{label} requires exactly one Genus generator header")
    contexts = re.findall(r"(?m)^\s*(?:Module|Design):\s*/?(\S+)\s*$", text)
    if contexts != [top]:
        raise EvidenceError(f"{label} requires exactly one expected design context")
    reject_cadence_diagnostics(text, label)
    return text


def _finite_report_number(value: str, label: str, *, nonnegative: bool = False) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise EvidenceError(f"{label} is nonnumeric") from error
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise EvidenceError(f"{label} must be finite" + (" and nonnegative" if nonnegative else ""))
    return number


def validate_area_report(payload: bytes, top: str) -> dict[str, float | int]:
    text = _native_report(payload, top, "area report")
    headers = re.findall(
        r"(?m)^\s*Instance\s+Module\s+Cell-Count\s+Cell-Area\s+Net-Area\s+Total-Area\s*$",
        text)
    if len(headers) != 1:
        raise EvidenceError("area report requires one native area header")
    rows = re.findall(
        rf"(?m)^\s*{re.escape(top)}\s+NA\s+(\d+)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$", text)
    if len(rows) != 1:
        raise EvidenceError("area report requires one exact top subtotal row")
    cell_count = integer(int(rows[0][0]), "area report cell count", minimum=1)
    cell_area, net_area, total_area = (
        _finite_report_number(value, "area report area", nonnegative=True)
        for value in rows[0][1:]
    )
    if total_area <= 0 or not math.isclose(
            total_area, cell_area + net_area, rel_tol=2e-5, abs_tol=5e-6):
        raise EvidenceError("area report total does not match cell plus net area")
    return {"cell_count": cell_count, "total_area": total_area}


def validate_timing_report(payload: bytes, top: str) -> dict[str, float | int]:
    text = _native_report(payload, top, "timing report")
    headers = list(re.finditer(
        r"(?m)^Path\s+(\d+):\s+(MET|VIOLATED)\s+"
        r"\(([-+]?(?:\d+(?:\.\d*)?|\.\d+|NaN|Inf|-Inf))\s+ps\)\s+"
        r"(Setup|Hold) Check\s*$", text))
    if not headers or [int(row.group(1)) for row in headers] != list(
            range(1, len(headers) + 1)):
        raise EvidenceError("timing report requires sequential native path rows")
    slacks: list[float] = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        segment = text[header.start():end]
        if len(re.findall(r"(?m)^\s*Beginpoint:\s+\S+\s*$", segment)) != 1 or \
                len(re.findall(r"(?m)^\s*Endpoint:\s+\S+\s*$", segment)) != 1:
            raise EvidenceError("timing report path lacks beginpoint/endpoint context")
        detail = re.findall(
            r"(?m)^\s*Slack:=\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+|NaN|Inf|-Inf))\s*$",
            segment)
        if len(detail) != 1:
            raise EvidenceError("timing report path lacks one native Slack row")
        header_slack = _finite_report_number(header.group(3), "timing header slack")
        detail_slack = _finite_report_number(detail[0], "timing detail slack")
        if not math.isclose(header_slack, detail_slack, abs_tol=1e-9):
            raise EvidenceError("timing report header/detail slack mismatch")
        slacks.append(detail_slack)
    return {"path_count": len(slacks), "minimum_slack_ps": min(slacks)}


def validate_qor_report(payload: bytes, top: str) -> dict[str, float | int]:
    text = _native_report(payload, top, "QoR report")
    if len(re.findall(r"(?m)^\s*Quality of Results Summary\s*$", text)) != 1:
        raise EvidenceError("QoR report requires one native summary header")
    values: dict[str, float] = {}
    for label in ("WNS (ps)", "TNS (ps)", "Violating Paths", "Unconstrained Paths"):
        rows = re.findall(
            rf"(?m)^\s*{re.escape(label)}:\s*"
            r"([-+]?(?:\d+(?:\.\d*)?|\.\d+|NaN|Inf|-Inf))\s*$", text)
        if len(rows) != 1:
            raise EvidenceError(f"QoR report requires exactly one {label}")
        values[label] = _finite_report_number(rows[0], f"QoR {label}")
    for label in ("Violating Paths", "Unconstrained Paths"):
        if values[label] < 0 or not values[label].is_integer():
            raise EvidenceError(f"QoR {label} must be a nonnegative integer")
    return {
        "wns_ps": values["WNS (ps)"], "tns_ps": values["TNS (ps)"],
        "violating_paths": int(values["Violating Paths"]),
        "unconstrained_paths": int(values["Unconstrained Paths"]),
    }


def validate_timing_intent_report(payload: bytes, top: str) -> dict[str, int]:
    text = _native_report(payload, top, "timing-intent report")
    if len(re.findall(r"(?mi)^\s*Check Timing Intent Summary\s*$", text)) != 1:
        raise EvidenceError("timing-intent report requires one native summary header")
    result: dict[str, int] = {}
    for label, key in (("Unconstrained Endpoints", "unconstrained_endpoints"),
                       ("Unclocked Registers", "unclocked_registers"),
                       ("Multiple Clock Pins", "multiple_clock_pins")):
        rows = re.findall(rf"(?mi)^\s*{re.escape(label)}:\s*(\d+)\s*$", text)
        if len(rows) != 1:
            raise EvidenceError(f"timing-intent report requires exactly one {label}")
        result[key] = integer(int(rows[0]), f"timing-intent {label}")
    return result


def validate_clock_report(payload: bytes, top: str) -> dict[str, float | int]:
    text = _native_report(payload, top, "clock report")
    headers = re.findall(
        r"(?m)^\s*Clock\s+Period\(ns\)\s+Rise\(ns\)\s+Fall\(ns\)\s+Uncertainty\(ns\)\s*$",
        text)
    if len(headers) != 1:
        raise EvidenceError("clock report requires one native uncertainty header")
    rows = re.findall(
        r"(?m)^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$",
        text)
    if len(rows) != 1 or rows[0][0] != "single_edge_clk":
        raise EvidenceError("clock report requires exactly one single_edge_clk row")
    period, rise, fall, uncertainty = (
        _finite_report_number(value, "clock report value", nonnegative=True)
        for value in rows[0][1:]
    )
    if (period, rise, fall, uncertainty) != (6.5, 0.0, 3.25, 0.25):
        raise EvidenceError("clock report values differ from placeholder clock")
    return {"clock_count": 1, "period_ns": period, "uncertainty_ns": uncertainty}


def validate_check_design_diagnostic(payload: bytes, top: str) -> dict[str, int]:
    text = _native_report(payload, top, "check-design report")
    if len(re.findall(r"(?mi)^\s*Check Design -all Summary\s*$", text)) != 1:
        raise EvidenceError("check-design report requires one native -all summary")
    result: dict[str, int] = {}
    for label, key in (("Unresolved References", "unresolved_references"),
                       ("Black Boxes", "black_boxes"), ("Errors", "errors")):
        rows = re.findall(rf"(?mi)^\s*{re.escape(label)}:\s*(\d+)\s*$", text)
        if rows != ["0"]:
            raise EvidenceError(f"check-design report requires exactly one zero {label}")
        result[key] = 0
    return result


def validate_mapped_sdf(payload: bytes, top: str) -> dict[str, int]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceError("mapped SDF is not UTF-8") from error
    without_strings = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    depth = 0
    for character in without_strings:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise EvidenceError("mapped SDF parentheses are unbalanced")
    if depth != 0 or len(re.findall(r"(?i)\(\s*DELAYFILE\b", text)) != 1:
        raise EvidenceError("mapped SDF requires one balanced DELAYFILE")
    exact_rows = {
        "SDFVERSION": r'"(?:3\.0|4\.0)"',
        "DESIGN": rf'"{re.escape(top)}"',
        "PROGRAM": r'"[^"\n]*Genus[^"\n]*"',
        "TIMESCALE": r'(?:1|10|100)(?:fs|ps|ns|us)',
    }
    for label, value_pattern in exact_rows.items():
        if len(re.findall(rf"(?i)\(\s*{label}\s+{value_pattern}\s*\)", text)) != 1:
            raise EvidenceError(f"mapped SDF requires one exact {label} row")
    cell_count = len(re.findall(r"(?i)\(\s*CELL\b", text))
    if (cell_count < 1 or
            len(re.findall(
                r"(?i)\(\s*CELLTYPE\s+\"[^\"\n]+\"\s*\)", text)) != cell_count or
            len(re.findall(r"(?i)\(\s*INSTANCE(?:\s+[^()\s]+)?\s*\)", text)) != cell_count or
            len(re.findall(r"(?i)\(\s*DELAY\b", text)) < cell_count):
        raise EvidenceError("mapped SDF CELL/CELLTYPE/INSTANCE/DELAY structure is incomplete")
    return {"cell_count": cell_count}


def validate_inventory_consistency(netlist_diagnostic: dict[str, int],
                                   sdf_diagnostic: dict[str, int],
                                   area_diagnostic: dict[str, float | int]) -> int:
    counts = {
        "mapped netlist": integer(netlist_diagnostic.get("structural_instance_count"),
                                  "mapped netlist instance count", minimum=1),
        "mapped SDF": integer(sdf_diagnostic.get("cell_count"),
                              "mapped SDF cell count", minimum=1),
        "area report": integer(area_diagnostic.get("cell_count"),
                               "area report cell count", minimum=1),
    }
    if len(set(counts.values())) != 1:
        detail = ", ".join(f"{label}={value}" for label, value in counts.items())
        raise EvidenceError(f"mapped inventory counts disagree: {detail}")
    return next(iter(counts.values()))


def _expected_genus_output_paths(top: str) -> dict[str, str]:
    return {
        "report_check_design": f"work/{top}_check_design.rpt",
        "report_area": f"work/{top}_area.rpt",
        "report_timing": f"work/{top}_gtiming.rpt",
        "report_power": f"work/{top}_gpower.rpt",
        "report_qor": f"work/{top}_qor.rpt",
        "report_timing_intent": f"work/{top}_timing_intent.rpt",
        "report_clocks": f"work/{top}_clocks.rpt",
        "mapped_netlist": f"work/{top}_netlist.v",
        "mapped_sdc": f"work/{top}_mapped.sdc",
        "mapped_sdf": f"work/{top}.sdf",
    }


def _artifact_rows(attempt: Path, receipt: dict[str, Any], candidate: str,
                   source_pins: dict[str, Any], contract: dict[str, Any]
                   ) -> tuple[dict[str, bytes], dict[str, Any]]:
    attempt_identity = _directory_identity(attempt, f"{candidate} attempt root")
    artifacts = receipt["artifacts"]
    if not isinstance(artifacts, dict):
        raise EvidenceError(f"{candidate} artifacts must be an object")
    fixed = contract["artifact_policy"]["fixed_roles"]
    source_count = len(source_pins["candidates"][candidate]["sources"])
    filelist_count = len(source_pins["candidates"][candidate]["filelists"])
    source_roles = [f"source_{index:02d}" for index in range(source_count)]
    filelist_roles = [f"filelist_{index:02d}" for index in range(filelist_count)]
    expected_roles = set(fixed + source_roles + filelist_roles)
    if set(artifacts) != expected_roles:
        raise EvidenceError(f"{candidate} complete artifact role set mismatch")
    if receipt["complete_artifact_ledger_sha256"] != sha256(canonical(artifacts)):
        raise EvidenceError(f"{candidate} complete artifact ledger SHA mismatch")
    payloads: dict[str, bytes] = {}
    paths: set[str] = set()
    top = contract.get("candidates", {}).get(candidate, {}).get("top")
    expected_output_paths = _expected_genus_output_paths(top) if top else {}
    for role in sorted(artifacts):
        _require_directory_identity(attempt, attempt_identity,
                                    f"{candidate} attempt root")
        row = exact_keys(artifacts[role], {"path", "sha256", "size_bytes"},
                         f"{candidate}.artifacts.{role}")
        path = string(row["path"], f"{candidate}.{role}.path")
        if role in expected_output_paths and path != expected_output_paths[role]:
            raise EvidenceError(
                f"{candidate} {role} path is not the exact Genus output path")
        if path in paths:
            raise EvidenceError(f"{candidate} artifact path reused: {path}")
        paths.add(path)
        payload = read_beneath(attempt, path, f"{candidate} {role}")
        if (sha256(payload) != digest(row["sha256"], f"{candidate}.{role}.sha256") or
                len(payload) != integer(row["size_bytes"], f"{candidate}.{role}.size")):
            raise EvidenceError(f"{candidate} {role} bytes/hash/size mismatch")
        if not payload:
            raise EvidenceError(f"{candidate} {role} is empty")
        payloads[role] = payload
    _require_directory_identity(attempt, attempt_identity,
                                f"{candidate} attempt root")
    return payloads, artifacts


def _utc(value: Any, label: str) -> datetime:
    text = string(value, label, UTC_RE)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise EvidenceError(f"{label} is invalid") from error
    if parsed.tzinfo != timezone.utc:
        raise EvidenceError(f"{label} must be UTC")
    return parsed


def validate_diagnostic_attempt(attempt: Path, candidate: str, expected_top: str,
                                source_pins: dict[str, Any], contract: dict[str, Any]
                                ) -> dict[str, Any]:
    attempt_identity = _directory_identity(attempt, f"{candidate} attempt root")
    receipt, receipt_payload = read_json(attempt / "diagnostic-receipt.json",
                                         f"{candidate} diagnostic receipt")
    _require_directory_identity(attempt, attempt_identity,
                                f"{candidate} attempt root")
    exact_keys(receipt, DIAGNOSTIC_RECEIPT_KEYS,
               f"{candidate} diagnostic receipt")
    schema = receipt.get("schema")
    if schema != contract["diagnostic_provenance"]["accepted_receipt_schema"]:
        text = str(schema).lower()
        if any(token in text for token in ("p6", "endpoint_vectorless", "k2_w2_genus",
                                           "template", "synthetic", "inherited")):
            raise EvidenceError(f"{candidate} legacy/P6/synthetic receipt schema rejected")
        raise EvidenceError(f"{candidate} unsupported diagnostic receipt schema")
    expected = contract["candidates"][candidate]
    if (receipt["status"] != "DIAGNOSTIC_COMPLETE_UNVERIFIED" or
            receipt["evidence_class"] != contract["evidence_class"] or
            receipt["candidate"] != candidate or
            receipt["architecture"] != expected["architecture"] or
            receipt["interface"] != "SINGLE_EDGE_PARALLEL" or
            receipt["boundary"] != expected["boundary"] or
            receipt["top"] != expected_top):
        raise EvidenceError(f"{candidate} identity/top/boundary contradiction")
    _forbid_lineage_text(receipt["top"], f"{candidate} top")
    lineage = exact_keys(receipt["lineage"],
                         {"synthetic", "inherited", "p6", "borrowed_dependency_ids"},
                         f"{candidate} lineage")
    if (lineage["synthetic"] is not False or lineage["inherited"] is not False or
            lineage["p6"] is not False or lineage["borrowed_dependency_ids"] != []):
        raise EvidenceError(f"{candidate} synthetic/inherited/P6 evidence rejected")
    if receipt["operating_point"] != contract["operating_point"]:
        raise EvidenceError(f"{candidate} corner/clock/I/O/load/Liberty mismatch")
    if receipt["activity_policy"] != contract["activity_policy"]:
        raise EvidenceError(f"{candidate} activity policy mismatch")
    if receipt["tool"] != contract["tool"]:
        raise EvidenceError(f"{candidate} Genus tool/path/version/hash mismatch")
    try:
        requested_tool = Path(contract["tool"]["requested_path"])
        resolved_tool = requested_tool.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(
            f"{candidate} live Genus entrypoint is unavailable on server") from error
    if str(resolved_tool) != contract["tool"]["resolved_path"]:
        raise EvidenceError(f"{candidate} live Genus resolved path mismatch")
    tool_payload = stable_read(resolved_tool, f"{candidate} live Genus binary")
    if sha256(tool_payload) != contract["tool"]["sha256"]:
        raise EvidenceError(f"{candidate} live Genus binary SHA mismatch")

    capture = exact_keys(receipt["capture"], {
        "origin", "capture_id", "claimed_host_fingerprint_sha256",
        "started_utc", "finished_utc",
    }, f"{candidate} diagnostic capture")
    if capture["origin"] != contract["diagnostic_provenance"]["accepted_origin"]:
        raise EvidenceError(f"{candidate} capture must remain explicitly uncontrolled")
    string(capture["capture_id"], f"{candidate} capture_id", ID_RE)
    digest(capture["claimed_host_fingerprint_sha256"],
           f"{candidate} claimed host fingerprint")
    if _utc(capture["finished_utc"], "finished_utc") <= _utc(
            capture["started_utc"], "started_utc"):
        raise EvidenceError(f"{candidate} diagnostic timestamps are not increasing")

    payloads, artifacts = _artifact_rows(
        attempt, receipt, candidate, source_pins, contract)
    pins = source_pins["candidates"][candidate]
    source_manifest = parse_json(payloads["source_manifest"],
                                 f"{candidate} source manifest")
    exact_keys(source_manifest, {
        "schema", "equivalent_hardened_commits", "candidate", "top",
        "filelists", "sources",
    },
               f"{candidate} source manifest")
    if (source_manifest["schema"] != "k2_single_edge_source_snapshot_v2" or
            source_manifest["equivalent_hardened_commits"] !=
            source_pins["equivalent_hardened_commits"] or
            source_manifest["candidate"] != candidate or
            source_manifest["top"] != expected_top or
            not isinstance(source_manifest["filelists"], list) or
            len(source_manifest["filelists"]) != len(pins["filelists"]) or
            not isinstance(source_manifest["sources"], list) or
            len(source_manifest["sources"]) != len(pins["sources"])):
        raise EvidenceError(f"{candidate} source manifest identity mismatch")
    for kind in ("filelists", "sources"):
        prefix = "filelist" if kind == "filelists" else "source"
        for index, (actual, pinned) in enumerate(zip(source_manifest[kind], pins[kind])):
            exact_keys(actual, {"path", "repository_path", "sha256"},
                       f"{candidate} {prefix} manifest row {index}")
            role = f"{prefix}_{index:02d}"
            if (actual["path"] != artifacts[role]["path"] or
                    actual["repository_path"] != pinned["path"] or
                    actual["sha256"] != pinned["sha256"] or
                    sha256(payloads[role]) != pinned["sha256"]):
                raise EvidenceError(
                    f"{candidate} {prefix} snapshot {index} mismatch")

    template_driver = stable_read(ROOT / contract["templates"]["driver"]["path"],
                                  "trusted driver template")
    template_sdc = stable_read(ROOT / contract["templates"]["sdc"]["path"],
                               "trusted SDC template")
    if payloads["driver_tcl"] != template_driver:
        raise EvidenceError(f"{candidate} executed driver differs from pinned template")
    if payloads["input_sdc"] != template_sdc or payloads["materialized_sdc"] != template_sdc:
        raise EvidenceError(f"{candidate} exact input/materialized SDC mismatch")
    if sha256(payloads["setup_liberty"]) != contract["operating_point"]["libraries"]["setup"]["sha256"]:
        raise EvidenceError(f"{candidate} setup/power Liberty mismatch")
    if sha256(payloads["hold_liberty"]) != contract["operating_point"]["libraries"]["hold"]["sha256"]:
        raise EvidenceError(f"{candidate} hold Liberty mismatch")

    netlist_diagnostic = validate_structural_netlist(
        payloads["mapped_netlist"], expected_top)
    validate_mapped_sdc(payloads["mapped_sdc"],
                        contract["activity_policy"]["forbidden_tokens"])
    sdf_diagnostic = validate_mapped_sdf(payloads["mapped_sdf"], expected_top)
    validate_genus_diagnostic_log(payloads["genus_log"], expected_top, contract)
    power = parse_power_report(payloads["report_power"], expected_top,
                               contract["activity_policy"])
    for role in ("report_area", "report_timing", "report_qor", "report_timing_intent",
                 "report_clocks", "report_check_design"):
        reject_activity(payloads[role], f"{candidate} {role}",
                        contract["activity_policy"]["forbidden_tokens"])
    area_diagnostic = validate_area_report(payloads["report_area"], expected_top)
    timing_diagnostic = validate_timing_report(payloads["report_timing"], expected_top)
    qor_diagnostic = validate_qor_report(payloads["report_qor"], expected_top)
    timing_intent_diagnostic = validate_timing_intent_report(
        payloads["report_timing_intent"], expected_top)
    clock_diagnostic = validate_clock_report(payloads["report_clocks"], expected_top)
    check_design_diagnostic = validate_check_design_diagnostic(
        payloads["report_check_design"], expected_top)
    if not math.isclose(qor_diagnostic["wns_ps"], timing_diagnostic["minimum_slack_ps"],
                        abs_tol=0.51):
        raise EvidenceError(f"{candidate} timing/QoR WNS context mismatch")
    inventory_count = validate_inventory_consistency(
        netlist_diagnostic, sdf_diagnostic, area_diagnostic)
    netlist_diagnostic.update({
        "cross_checked_cell_count": inventory_count,
        "sdf_cell_count": sdf_diagnostic["cell_count"],
        "native_report_count": 7,
        "area_cell_count": area_diagnostic["cell_count"],
        "timing_path_count": timing_diagnostic["path_count"],
        "timing_intent_issue_count": sum(timing_intent_diagnostic.values()),
        "clock_count": clock_diagnostic["clock_count"],
        "check_design_error_count": sum(check_design_diagnostic.values()),
    })

    execution = exact_keys(receipt["execution"], EXECUTION_KEYS,
                           f"{candidate} execution")
    if integer(execution["exit_code"], f"{candidate} exit_code") != 0:
        raise EvidenceError(f"{candidate} Genus exit code is not zero")
    cwd = Path(string(execution["cwd"], f"{candidate} cwd"))
    if not cwd.is_absolute() or cwd != attempt:
        raise EvidenceError(f"{candidate} cwd is not the in-place server attempt")
    driver_path = attempt / artifacts["driver_tcl"]["path"]
    driver_text = _tcl_safe_absolute_path(driver_path,
                                          f"{candidate} driver path")
    expected_argv = [contract["tool"]["requested_path"], "-batch", "-files",
                     driver_text]
    if execution["argv"] != expected_argv:
        raise EvidenceError(f"{candidate} exact executed argv mismatch")
    environment = execution["semantic_environment"]
    if (not isinstance(environment, dict) or
            list(environment) != contract["execution_policy"]["semantic_environment_keys"]):
        raise EvidenceError(f"{candidate} semantic environment keys/order mismatch")
    if execution["semantic_environment_sha256"] != sha256(canonical(environment)):
        raise EvidenceError(f"{candidate} semantic environment SHA mismatch")
    source_paths = [_tcl_safe_absolute_path(
        attempt / artifacts[f"source_{index:02d}"]["path"],
        f"{candidate} source_{index:02d} path")
        for index in range(len(pins["sources"]))]
    library_path = _tcl_safe_absolute_path(
        attempt / artifacts["setup_liberty"]["path"],
        f"{candidate} setup Liberty path")
    sdc_path = _tcl_safe_absolute_path(
        attempt / artifacts["materialized_sdc"]["path"],
        f"{candidate} materialized SDC path")
    output_path = _tcl_safe_absolute_path(attempt / "work",
                                         f"{candidate} output path")
    expected_environment = {
        "K2_SE_TOP": expected_top,
        "K2_SE_SOURCES_SV": " ".join(source_paths),
        "K2_SE_LIBRARY": library_path,
        "K2_SE_SDC": sdc_path,
        "K2_SE_OUTPUT": output_path,
        "K2_SE_ACTIVITY_MODE": "GENUS_DEFAULT_VECTORLESS",
        "LC_ALL": "C",
    }
    if environment != expected_environment:
        raise EvidenceError(f"{candidate} exact semantic environment mismatch")
    reject_activity(canonical(environment), f"{candidate} semantic environment",
                    contract["activity_policy"]["forbidden_tokens"])

    command = parse_json(payloads["command_receipt"], f"{candidate} command receipt")
    expected_command = {"schema": "k2_single_edge_command_receipt_v1", **execution}
    if command != expected_command:
        raise EvidenceError(f"{candidate} diagnostic command receipt mismatch")
    environment_receipt = parse_json(payloads["environment_receipt"],
                                     f"{candidate} environment receipt")
    expected_environment_receipt = {
        "schema": "k2_single_edge_diagnostic_environment_v2",
        "capture": capture,
        "tool": contract["tool"],
        "operating_point": contract["operating_point"],
        "activity_policy": contract["activity_policy"],
    }
    if environment_receipt != expected_environment_receipt:
        raise EvidenceError(f"{candidate} diagnostic environment receipt mismatch")

    _require_directory_identity(attempt, attempt_identity,
                                f"{candidate} attempt root")

    return {
        "candidate": candidate, "top": expected_top, "receipt": receipt,
        "receipt_sha256": sha256(receipt_payload), "power": power,
        "capture_id": capture["capture_id"],
        "claimed_host_fingerprint_sha256":
            capture["claimed_host_fingerprint_sha256"],
        "artifact_ledger_sha256": receipt["complete_artifact_ledger_sha256"],
        "structural_diagnostic": netlist_diagnostic,
    }


def qualify(evidence_path: Path, output: Path, root: Path = ROOT,
            keyring_path: Path | None = None,
            expected_keyring_sha256: str | None = None) -> Path:
    if keyring_path is not None or expected_keyring_sha256 is not None:
        raise EvidenceError(
            "caller keyrings/HMAC are unsupported without a controlled producer")
    contract, contract_payload = load_contract(root)
    source_pins = validate_contract(root, contract)
    evidence, evidence_payload = read_json(evidence_path, "evidence index")
    exact_keys(evidence, {
        "schema", "evidence_class", "candidate_order", "interface",
        "contract_sha256", "rows",
    }, "evidence index")
    if evidence["schema"] != "k2_single_edge_vectorless_diagnostic_index_v2":
        raise EvidenceError("legacy/P6/synthetic evidence index schema rejected")
    order = contract["decision_policy"]["candidate_order"]
    if (evidence["evidence_class"] != contract["evidence_class"] or
            evidence["candidate_order"] != order or
            evidence["interface"] != "SINGLE_EDGE_PARALLEL" or
            evidence["contract_sha256"] != sha256(contract_payload)):
        raise EvidenceError("evidence index cohort/contract binding mismatch")
    if not isinstance(evidence["rows"], list) or len(evidence["rows"]) != 2:
        raise EvidenceError("evidence index requires exact A2/A3 two-row cohort")
    evidence_root = evidence_path.resolve(strict=True).parent
    diagnostics: list[dict[str, Any]] = []
    attempt_paths: set[Path] = set()
    for index, candidate in enumerate(order):
        row = exact_keys(evidence["rows"][index],
                         {"candidate", "top", "attempt_directory"},
                         f"evidence row {index}")
        expected_top = contract["candidates"][candidate]["top"]
        if row["candidate"] != candidate or row["top"] != expected_top:
            raise EvidenceError(f"evidence row {index} candidate/top mismatch")
        attempt = _contained_directory(evidence_root, row["attempt_directory"],
                                       f"{candidate} attempt_directory")
        if attempt in attempt_paths:
            raise EvidenceError("A2/A3 attempts must be distinct")
        attempt_paths.add(attempt)
        diagnostics.append(validate_diagnostic_attempt(
            attempt, candidate, expected_top, source_pins, contract))
    if len({row["claimed_host_fingerprint_sha256"] for row in diagnostics}) != 1:
        raise EvidenceError("A2/A3 claimed diagnostic host differs")
    if len({row["capture_id"] for row in diagnostics}) != 2:
        raise EvidenceError("A2/A3 diagnostic capture IDs must be distinct")

    result = {
        "schema": "k2_single_edge_vectorless_qualification_v2",
        "status": HOLD_STATUS,
        "comparison_ready": False,
        "candidate_go": False,
        "reason": "artifact structure is diagnostic-only; placeholder I/O authority and absence of a controlled producer/live-host/freshness binding prohibit GO",
        "controlled_producer_available": False,
        "live_host_bound": False,
        "freshness_or_replay_protected": False,
        "structural_diagnostics_complete": True,
        "constraint_authority": contract["constraint_authority"]["status"],
        "evidence_class": contract["evidence_class"],
        "candidate_order": order,
        "release_interface": "PARALLEL_FALLBACK",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "power_method": "GENUS_MAPPED_DEFAULT_VECTORLESS",
        "activity_annotated": False,
        "contract_sha256": sha256(contract_payload),
        "evidence_index_sha256": sha256(evidence_payload),
        "diagnostic_rows": [{key: row[key] for key in (
            "candidate", "top", "receipt_sha256", "artifact_ledger_sha256",
            "power", "structural_diagnostic")}
                            for row in diagnostics],
    }
    write_exclusive(output, result)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("preflight")
    check.add_argument("--repo-root", type=Path, default=ROOT)
    check.add_argument("--output", type=Path, required=True)
    gate = subparsers.add_parser("qualify")
    gate.add_argument("--repo-root", type=Path, default=ROOT)
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.repo_root.resolve(strict=True)
        if root != ROOT.resolve(strict=True):
            raise EvidenceError("entrypoint/repository root mismatch")
        if args.command == "preflight":
            path = preflight(root, args.output)
            print(f"K2_SINGLE_EDGE_VECTORLESS_HOLD receipt={path}")
        else:
            path = qualify(args.evidence, args.output, root)
            result = parse_json(stable_read(path, "qualification output"),
                                "qualification output")
            if (result["status"] != HOLD_STATUS or result["candidate_go"] is not False or
                    result["comparison_ready"] is not False):
                raise EvidenceError("internal qualification decision escaped HOLD")
            print(f"K2_SINGLE_EDGE_VECTORLESS_HOLD receipt={path}")
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"K2_SINGLE_EDGE_VECTORLESS_FAIL {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
