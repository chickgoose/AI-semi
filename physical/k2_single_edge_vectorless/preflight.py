#!/usr/bin/env python3
"""Fail-closed A2/A3 mapped Genus default-vectorless evidence gate.

The committed templates are configuration and can only produce HOLD.  GO needs
two complete in-place server attempts plus an HMAC keyring kept outside the
public evidence tree and pinned by an out-of-band SHA-256 value.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
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


class EvidenceError(ValueError):
    """The requested proof is incomplete, inconsistent, or unauthenticated."""


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
    """Read a source identity from the explicitly pinned producer commit.

    The integration branch may not yet contain that commit when this evidence
    flow is reviewed, so the immutable Git object is the authority.  If the
    path is already materialized, its bytes must match the same object.
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
    current = root / relative
    if current.exists():
        current_payload = stable_read(current, f"materialized {label}", single_link=False)
        if current_payload != completed.stdout:
            raise EvidenceError(f"materialized {label} differs from producer commit")
    return completed.stdout


def reject_activity(payload: bytes, label: str,
                    forbidden_tokens: list[str]) -> None:
    try:
        lowered = payload.decode("utf-8", errors="strict").lower()
    except UnicodeError as error:
        raise EvidenceError(f"{label} is not UTF-8") from error
    for token in forbidden_tokens:
        if token.lower() in lowered:
            raise EvidenceError(f"{label} contains forbidden activity token {token}")


def load_contract(root: Path = ROOT) -> tuple[dict[str, Any], bytes]:
    expected = root.resolve(strict=True) / "physical/k2_single_edge_vectorless/contract.json"
    if expected != CONTRACT_PATH.resolve(strict=True):
        raise EvidenceError("entrypoint and repository root do not match")
    return read_json(expected, "single-edge vectorless contract")


def validate_contract(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    exact_keys(contract, {
        "schema", "status", "evidence_class", "decision_policy",
        "policy_binding", "source_pins", "candidates", "operating_point",
        "activity_policy", "tool", "execution_policy", "artifact_policy",
        "producer_authority", "templates",
    }, "contract")
    if (contract["schema"] != "k2_single_edge_vectorless_contract_v1" or
            contract["status"] != "READY_FOR_PRODUCER_BOUND_SERVER_EXECUTION" or
            contract["evidence_class"] !=
            "GENUS_MAPPED_A2_A3_SINGLE_EDGE_DEFAULT_VECTORLESS"):
        raise EvidenceError("contract identity/status mismatch")

    decision = exact_keys(contract["decision_policy"], {
        "candidate_order", "exact_cohort_required", "release_interface",
        "transfer_mode", "synthetic_allowed", "inherited_allowed", "p6_allowed",
        "borrowed_dependency_ids_allowed",
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
    }:
        raise EvidenceError("single-edge exact-cohort decision policy changed")

    policy = exact_keys(contract["policy_binding"], {
        "path", "sha256", "required_disallowed_dependencies",
    }, "policy_binding")
    policy_path = _repo_file(root, policy["path"], "policy_binding.path")
    policy_payload = stable_read(policy_path, "REDRED system policy", single_link=False)
    if sha256(policy_payload) != digest(policy["sha256"], "policy_binding.sha256"):
        raise EvidenceError("REDRED policy SHA mismatch")
    policy_doc = parse_json(policy_payload, "REDRED system policy")
    fallback = policy_doc.get("interfaces", {}).get("PARALLEL_FALLBACK", {})
    required_disallowed = [
        "INHERITED_6P5_STANDARD_CELL_REFERENCE",
        "P6_VECTORLESS_POWER",
        "P6_PAD_PACKAGE_CHANNEL",
    ]
    if (fallback.get("transfer_mode") != "SINGLE_EDGE_PARALLEL" or
            fallback.get("may_borrow_p6_physical_evidence") is not False or
            fallback.get("disallowed_borrowed_dependencies") != required_disallowed or
            policy["required_disallowed_dependencies"] != required_disallowed):
        raise EvidenceError("REDRED single-edge/P6 anti-borrow policy mismatch")

    source_identity = exact_keys(contract["source_pins"], {"path", "sha256"},
                                 "source_pins")
    source_path = _repo_file(root, source_identity["path"], "source_pins.path")
    source_doc, source_payload = read_json(source_path, "source pins")
    if sha256(source_payload) != digest(source_identity["sha256"],
                                        "source_pins.sha256"):
        raise EvidenceError("source pin manifest SHA mismatch")
    exact_keys(source_doc, {"schema", "producer_commit", "candidates"}, "source pins")
    producer_commit = source_doc["producer_commit"]
    if (source_doc["schema"] != "k2_single_edge_vectorless_source_pins_v1" or
            producer_commit != "4ce4836fab1309d3468db8e660d2da9af371f784"):
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
                not isinstance(row["inputs"], list) or not row["inputs"] or
                not isinstance(row["outputs"], list) or not row["outputs"]):
            raise EvidenceError(f"{candidate} complete-boundary contract mismatch")
        _forbid_lineage_text(row["top"], f"{candidate} top")
        pins = exact_keys(source_doc["candidates"][candidate],
                          {"architecture", "top", "filelists", "sources"},
                          f"source pins {candidate}")
        if pins["architecture"] != row["architecture"] or pins["top"] != row["top"]:
            raise EvidenceError(f"{candidate} source/top pin mismatch")
        if (not isinstance(pins["filelists"], list) or len(pins["filelists"]) != 2 or
                not isinstance(pins["sources"], list) or len(pins["sources"]) != 5):
            raise EvidenceError(f"{candidate} source closure is incomplete")
        seen: set[str] = set()
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
                source_bytes = _committed_payload(
                    root, producer_commit, source_rel, f"{kind} {source_rel}")
                if sha256(source_bytes) != digest(source["sha256"], "source SHA"):
                    raise EvidenceError(f"committed {kind} SHA mismatch: {source_rel}")

    expected_operating = {
        "corner": {"pdk": "GPDK045/gsclib045", "process": 1.0,
                   "voltage_v": 0.9, "temperature_c": 125.0,
                   "power_liberty_role": "setup_slow"},
        "clock": {"port": "clk_i", "name": "single_edge_clk", "period_ns": 6.5,
                  "waveform_ns": [0.0, 3.25], "uncertainty_ns": 0.25,
                  "min_pulse_high_ns": 0.5, "min_pulse_low_ns": 0.5},
        "io": {"input_delay_min_ns": 0.1, "input_delay_max_ns": 0.5,
               "output_delay_min_ns": 0.1, "output_delay_max_ns": 0.5,
               "input_transition_ns": 0.05, "drive_cell": "BUFX2"},
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
    required_forbidden = {
        "read_vcd", "read_saif", "read_tcf", "read_activity",
        "read_activity_file",
        "set_switching_activity", "set_default_switching_activity",
        "set_power_activity", "set_activity", "lp_toggle_rate",
        "lp_static_probability", "toggle_rate", "static_probability",
        ".vcd", ".saif", ".tcf",
    }
    if set(activity["forbidden_tokens"]) != required_forbidden:
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
    if (len(artifact_policy["fixed_roles"]) != 18 or
            len(set(artifact_policy["fixed_roles"])) != 18 or
            artifact_policy["source_role_prefix"] != "source_" or
            artifact_policy["filelist_role_prefix"] != "filelist_" or
            artifact_policy["all_files_regular_single_link"] is not True or
            artifact_policy["complete_ledger_required"] is not True):
        raise EvidenceError("complete artifact policy changed")

    authority = exact_keys(contract["producer_authority"], {
        "accepted_receipt_schema", "accepted_origin", "attestation_schema",
        "algorithm", "keyring_schema", "trust_anchor_location",
        "out_of_band_keyring_sha256_required", "unauthenticated_result",
    }, "producer_authority")
    if authority != {
        "accepted_receipt_schema": "k2_single_edge_vectorless_producer_receipt_v1",
        "accepted_origin": "DIRECT_GENUS_SERVER_RUN",
        "attestation_schema": "k2_single_edge_vectorless_attestation_v1",
        "algorithm": "hmac-sha256",
        "keyring_schema": "k2_single_edge_vectorless_keyring_v1",
        "trust_anchor_location": "OUTSIDE_PUBLIC_EVIDENCE_ROOT",
        "out_of_band_keyring_sha256_required": True,
        "unauthenticated_result": "HOLD_UNAUTHENTICATED_PRODUCER_ARTIFACTS",
    }:
        raise EvidenceError("producer authority policy changed")

    templates = exact_keys(contract["templates"],
                           {"driver", "sdc", "producer_receipt"}, "templates")
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
                    "write_sdc", "write_sdf"):
        if len(re.findall(rf"(?m)^\s*{command}\b", driver_text)) != 1:
            raise EvidenceError(f"Genus driver requires exactly one {command}")
    if len(re.findall(r"(?m)^\s*report_power\b", driver_text)) != 1:
        raise EvidenceError("Genus driver requires exactly one report_power")
    sdc_text = payloads["sdc"].decode("utf-8")
    for required in ("create_clock -name single_edge_clk -period 6.500",
                     "set_input_delay -clock single_edge_clk -min 0.100",
                     "set_input_delay -clock single_edge_clk -max 0.500",
                     "set_output_delay -clock single_edge_clk -min 0.100",
                     "set_output_delay -clock single_edge_clk -max 0.500",
                     "set_input_transition 0.050", "set_load 0.010"):
        if required not in sdc_text:
            raise EvidenceError(f"strict SDC missing exact constraint: {required}")
    if re.search(r"(?i)\b(?:false_path|multicycle|p6|negedge|falling)\b", sdc_text):
        raise EvidenceError("single-edge SDC contains borrowed/multi-edge exception")
    template_doc = parse_json(payloads["producer_receipt"], "receipt template")
    if (template_doc.get("schema") !=
            "k2_single_edge_vectorless_producer_receipt_template_v1" or
            template_doc.get("status") != "HOLD_TEMPLATE_NOT_EXECUTION_EVIDENCE" or
            template_doc.get("candidate_go") is not False or
            any(template_doc.get(field) is not None for field in (
                "mapped_netlist_sha256", "materialized_sdc_sha256",
                "genus_log_sha256", "exact_executed_argv", "genus_exit_code",
                "complete_artifact_ledger_sha256", "producer_attestation"))):
        raise EvidenceError("receipt template must remain non-evidence HOLD")
    return source_doc


def preflight(root: Path, output: Path) -> Path:
    contract, contract_payload = load_contract(root)
    source_doc = validate_contract(root, contract)
    result = {
        "schema": "k2_single_edge_vectorless_preflight_v1",
        "status": "HOLD_NO_PRODUCER_BOUND_SERVER_ARTIFACTS",
        "comparison_ready": False,
        "candidate_go": False,
        "reason": "templates and local pins are valid; Genus was not invoked and no authenticated server artifacts were supplied",
        "evidence_class": contract["evidence_class"],
        "candidate_order": contract["decision_policy"]["candidate_order"],
        "release_interface": "PARALLEL_FALLBACK",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "power_method": "GENUS_MAPPED_DEFAULT_VECTORLESS",
        "activity_annotated": False,
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

    def one(pattern: str, label: str) -> str:
        values = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        if len(values) != 1:
            raise EvidenceError(f"power report requires exactly one {label}")
        return values[0].strip()

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
    if "Generated by:           Genus(TM) Synthesis Solution" not in text:
        raise EvidenceError("power report lacks native Genus header")
    if f"Instance: /{top}" not in text and f"* Design: {top}" not in text:
        raise EvidenceError("power report top mismatch")
    if re.search(r"(?mi)^\s*Power Unit:\s*W\s*$", text) is None:
        raise EvidenceError("power report unit must be W")
    rows = re.findall(
        r"(?mi)^\s*Subtotal\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
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


def verify_genus_log(payload: bytes, top: str, contract: dict[str, Any]) -> None:
    reject_activity(payload, "Genus log", contract["activity_policy"]["forbidden_tokens"])
    text = payload.decode("utf-8")
    if (f"Version: {contract['tool']['version']}" not in text or
            f"K2_SINGLE_EDGE_VECTORLESS_PRODUCER_PASS top={top}" not in text or
            "Normal exit." not in text or
            re.search(r"Info=\d+, Warn=\d+, Error=0, Fatal=0", text) is None):
        raise EvidenceError("Genus log lacks version/PASS/zero-error/normal-exit evidence")
    if re.search(r"(?mi)^\s*(?:\*\*)?(?:Error|Fatal)\s*[:\[]", text):
        raise EvidenceError("Genus log contains an error/fatal diagnostic")


def _artifact_rows(attempt: Path, receipt: dict[str, Any], candidate: str,
                   source_pins: dict[str, Any], contract: dict[str, Any]
                   ) -> tuple[dict[str, bytes], dict[str, Any]]:
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
    for role in sorted(artifacts):
        row = exact_keys(artifacts[role], {"path", "sha256", "size_bytes"},
                         f"{candidate}.artifacts.{role}")
        path = string(row["path"], f"{candidate}.{role}.path")
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


def verify_attempt(attempt: Path, candidate: str, expected_top: str,
                   source_pins: dict[str, Any], contract: dict[str, Any]
                   ) -> dict[str, Any]:
    receipt, receipt_payload = read_json(attempt / "producer-receipt.json",
                                         f"{candidate} producer receipt")
    exact_keys(receipt, {
        "schema", "status", "evidence_class", "candidate", "architecture",
        "interface", "boundary", "lineage", "top", "producer",
        "operating_point", "activity_policy", "tool", "execution", "artifacts",
        "complete_artifact_ledger_sha256", "attestation",
    }, f"{candidate} producer receipt")
    schema = receipt.get("schema")
    if schema != contract["producer_authority"]["accepted_receipt_schema"]:
        text = str(schema).lower()
        if any(token in text for token in ("p6", "endpoint_vectorless", "k2_w2_genus",
                                           "template", "synthetic", "inherited")):
            raise EvidenceError(f"{candidate} legacy/P6/synthetic receipt schema rejected")
        raise EvidenceError(f"{candidate} unsupported producer receipt schema")
    expected = contract["candidates"][candidate]
    if (receipt["status"] != "PRODUCER_COMPLETE" or
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

    producer = exact_keys(receipt["producer"], {
        "origin", "authority_id", "run_id", "host_fingerprint_sha256",
        "started_utc", "finished_utc",
    }, f"{candidate} producer")
    if producer["origin"] != contract["producer_authority"]["accepted_origin"]:
        raise EvidenceError(f"{candidate} is not a direct server producer")
    string(producer["authority_id"], f"{candidate} authority_id", ID_RE)
    string(producer["run_id"], f"{candidate} run_id", ID_RE)
    digest(producer["host_fingerprint_sha256"], f"{candidate} host fingerprint")
    if _utc(producer["finished_utc"], "finished_utc") <= _utc(
            producer["started_utc"], "started_utc"):
        raise EvidenceError(f"{candidate} producer timestamps are not increasing")

    payloads, artifacts = _artifact_rows(
        attempt, receipt, candidate, source_pins, contract)
    pins = source_pins["candidates"][candidate]
    source_manifest = parse_json(payloads["source_manifest"],
                                 f"{candidate} source manifest")
    exact_keys(source_manifest, {
        "schema", "producer_commit", "candidate", "top", "filelists", "sources",
    },
               f"{candidate} source manifest")
    if (source_manifest["schema"] != "k2_single_edge_source_snapshot_v1" or
            source_manifest["producer_commit"] != source_pins["producer_commit"] or
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

    netlist_text = payloads["mapped_netlist"].decode("utf-8", errors="strict")
    if re.search(rf"(?m)^\s*module\s+{re.escape(expected_top)}\b", netlist_text) is None:
        raise EvidenceError(f"{candidate} mapped netlist lacks exact top")
    if re.search(r"(?i)\bp6\b", netlist_text):
        raise EvidenceError(f"{candidate} mapped netlist contains P6 lineage")
    if re.search(r"(?mi)^\s*(?:always|initial)\b", netlist_text):
        raise EvidenceError(f"{candidate} netlist is behavioral, not mapped")
    mapped_sdc_text = payloads["mapped_sdc"].decode("utf-8", errors="strict")
    if ("single_edge_clk" not in mapped_sdc_text or "6.500" not in mapped_sdc_text or
            re.search(r"(?i)\b(?:p6|negedge|falling)\b", mapped_sdc_text)):
        raise EvidenceError(f"{candidate} mapped SDC lost single-edge clock binding")
    if expected_top not in payloads["mapped_sdf"].decode("utf-8", errors="strict"):
        raise EvidenceError(f"{candidate} mapped SDF top mismatch")
    verify_genus_log(payloads["genus_log"], expected_top, contract)
    power = parse_power_report(payloads["report_power"], expected_top,
                               contract["activity_policy"])
    for role in ("report_area", "report_timing", "report_qor",
                 "report_timing_intent", "report_clocks"):
        reject_activity(payloads[role], f"{candidate} {role}",
                        contract["activity_policy"]["forbidden_tokens"])

    execution = exact_keys(receipt["execution"], {
        "argv", "cwd", "exit_code", "semantic_environment",
        "semantic_environment_sha256",
    }, f"{candidate} execution")
    if integer(execution["exit_code"], f"{candidate} exit_code") != 0:
        raise EvidenceError(f"{candidate} Genus exit code is not zero")
    cwd = Path(string(execution["cwd"], f"{candidate} cwd"))
    if not cwd.is_absolute() or cwd != attempt:
        raise EvidenceError(f"{candidate} cwd is not the in-place server attempt")
    driver_path = attempt / artifacts["driver_tcl"]["path"]
    expected_argv = [contract["tool"]["requested_path"], "-batch", "-files",
                     str(driver_path)]
    if execution["argv"] != expected_argv:
        raise EvidenceError(f"{candidate} exact executed argv mismatch")
    environment = execution["semantic_environment"]
    if (not isinstance(environment, dict) or
            list(environment) != contract["execution_policy"]["semantic_environment_keys"]):
        raise EvidenceError(f"{candidate} semantic environment keys/order mismatch")
    if execution["semantic_environment_sha256"] != sha256(canonical(environment)):
        raise EvidenceError(f"{candidate} semantic environment SHA mismatch")
    source_paths = [str(attempt / artifacts[f"source_{index:02d}"]["path"])
                    for index in range(len(pins["sources"]))]
    expected_environment = {
        "K2_SE_TOP": expected_top,
        "K2_SE_SOURCES_SV": " ".join(source_paths),
        "K2_SE_LIBRARY": str(attempt / artifacts["setup_liberty"]["path"]),
        "K2_SE_SDC": str(attempt / artifacts["materialized_sdc"]["path"]),
        "K2_SE_OUTPUT": str(attempt / "work"),
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
        raise EvidenceError(f"{candidate} producer command receipt mismatch")
    environment_receipt = parse_json(payloads["environment_receipt"],
                                     f"{candidate} environment receipt")
    expected_environment_receipt = {
        "schema": "k2_single_edge_server_environment_v1",
        "producer": producer,
        "tool": contract["tool"],
        "operating_point": contract["operating_point"],
        "activity_policy": contract["activity_policy"],
    }
    if environment_receipt != expected_environment_receipt:
        raise EvidenceError(f"{candidate} producer environment receipt mismatch")

    attestation = exact_keys(receipt["attestation"], {
        "schema", "key_id", "algorithm", "payload_sha256", "mac_sha256",
    }, f"{candidate} attestation")
    unsigned = dict(receipt)
    del unsigned["attestation"]
    if (attestation["schema"] != contract["producer_authority"]["attestation_schema"] or
            attestation["algorithm"] != "hmac-sha256" or
            attestation["key_id"] != producer["authority_id"] or
            attestation["payload_sha256"] != sha256(canonical(unsigned))):
        raise EvidenceError(f"{candidate} producer attestation binding mismatch")
    digest(attestation["mac_sha256"], f"{candidate} attestation MAC")
    return {
        "candidate": candidate, "top": expected_top, "receipt": receipt,
        "receipt_sha256": sha256(receipt_payload), "power": power,
        "authority_id": producer["authority_id"],
        "host_fingerprint_sha256": producer["host_fingerprint_sha256"],
        "run_id": producer["run_id"],
        "artifact_ledger_sha256": receipt["complete_artifact_ledger_sha256"],
    }


def _load_keyring(keyring_path: Path, expected_sha: str, evidence_root: Path,
                  contract: dict[str, Any]) -> tuple[dict[str, bytes], str]:
    expected_sha = digest(expected_sha, "out-of-band keyring SHA256")
    keyring_resolved = keyring_path.resolve(strict=True)
    root = evidence_root.resolve(strict=True)
    if keyring_resolved == root or root in keyring_resolved.parents:
        raise EvidenceError("producer keyring must remain outside evidence root")
    info = keyring_resolved.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise EvidenceError("producer keyring must be owner-owned mode 0600")
    keyring, payload = read_json(keyring_resolved, "producer keyring")
    if sha256(payload) != expected_sha:
        raise EvidenceError("producer keyring does not match out-of-band SHA256")
    exact_keys(keyring, {"schema", "keys"}, "producer keyring")
    if (keyring["schema"] != contract["producer_authority"]["keyring_schema"] or
            not isinstance(keyring["keys"], dict) or not keyring["keys"]):
        raise EvidenceError("producer keyring schema/keys mismatch")
    keys: dict[str, bytes] = {}
    for key_id, row in keyring["keys"].items():
        string(key_id, "keyring key_id", ID_RE)
        exact_keys(row, {"algorithm", "secret_hex", "producer_origin"},
                   f"keyring.{key_id}")
        if (row["algorithm"] != "hmac-sha256" or
                row["producer_origin"] != "DIRECT_GENUS_SERVER_RUN"):
            raise EvidenceError(f"keyring.{key_id} authority mismatch")
        secret_hex = string(row["secret_hex"], f"keyring.{key_id}.secret_hex")
        try:
            secret = bytes.fromhex(secret_hex)
        except ValueError as error:
            raise EvidenceError(f"keyring.{key_id} secret is not hex") from error
        if len(secret) < 32:
            raise EvidenceError(f"keyring.{key_id} secret must contain >=256 bits")
        keys[key_id] = secret
    return keys, expected_sha


def _authenticate(row: dict[str, Any], keys: dict[str, bytes]) -> None:
    receipt = row["receipt"]
    attestation = receipt["attestation"]
    key_id = attestation["key_id"]
    if key_id not in keys:
        raise EvidenceError(f"producer authority key not trusted: {key_id}")
    unsigned = dict(receipt)
    del unsigned["attestation"]
    actual = hmac.new(keys[key_id], canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, attestation["mac_sha256"]):
        raise EvidenceError(f"producer attestation MAC mismatch: {row['candidate']}")


def qualify(evidence_path: Path, output: Path, root: Path = ROOT,
            keyring_path: Path | None = None,
            expected_keyring_sha256: str | None = None) -> Path:
    contract, contract_payload = load_contract(root)
    source_pins = validate_contract(root, contract)
    evidence, evidence_payload = read_json(evidence_path, "evidence index")
    exact_keys(evidence, {
        "schema", "evidence_class", "candidate_order", "interface",
        "contract_sha256", "rows",
    }, "evidence index")
    if evidence["schema"] != "k2_single_edge_vectorless_evidence_v1":
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
    verified: list[dict[str, Any]] = []
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
        verified.append(verify_attempt(attempt, candidate, expected_top,
                                       source_pins, contract))
    if len({row["authority_id"] for row in verified}) != 1:
        raise EvidenceError("A2/A3 producer authority differs")
    if len({row["host_fingerprint_sha256"] for row in verified}) != 1:
        raise EvidenceError("A2/A3 server host binding differs")
    if len({row["run_id"] for row in verified}) != 2:
        raise EvidenceError("A2/A3 producer run IDs must be distinct")

    if (keyring_path is None) != (expected_keyring_sha256 is None):
        raise EvidenceError("keyring path and out-of-band SHA256 are both required")
    common = {
        "evidence_class": contract["evidence_class"],
        "candidate_order": order,
        "release_interface": "PARALLEL_FALLBACK",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "power_method": "GENUS_MAPPED_DEFAULT_VECTORLESS",
        "activity_annotated": False,
        "contract_sha256": sha256(contract_payload),
        "evidence_index_sha256": sha256(evidence_payload),
        "verified_rows": [{key: row[key] for key in (
            "candidate", "top", "receipt_sha256", "artifact_ledger_sha256", "power")}
                          for row in verified],
    }
    if keyring_path is None:
        result = {
            "schema": "k2_single_edge_vectorless_qualification_v1",
            "status": contract["producer_authority"]["unauthenticated_result"],
            "comparison_ready": False,
            "candidate_go": False,
            "reason": "artifact consistency passed, but no out-of-band producer trust anchor was supplied",
            "producer_authenticated": False,
            "trusted_keyring_sha256": None,
            **common,
        }
    else:
        keys, keyring_sha = _load_keyring(keyring_path, expected_keyring_sha256,
                                          evidence_root, contract)
        for row in verified:
            _authenticate(row, keys)
        result = {
            "schema": "k2_single_edge_vectorless_qualification_v1",
            "status": "GO_PRODUCER_BOUND_A2_A3_SINGLE_EDGE_VECTORLESS",
            "comparison_ready": True,
            "candidate_go": True,
            "reason": "both exact single-edge endpoints and every canonical artifact are producer-authenticated",
            "producer_authenticated": True,
            "trusted_keyring_sha256": keyring_sha,
            **common,
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
    gate.add_argument("--keyring", type=Path)
    gate.add_argument("--keyring-sha256")
    args = parser.parse_args(argv)
    try:
        root = args.repo_root.resolve(strict=True)
        if root != ROOT.resolve(strict=True):
            raise EvidenceError("entrypoint/repository root mismatch")
        if args.command == "preflight":
            path = preflight(root, args.output)
            print(f"K2_SINGLE_EDGE_VECTORLESS_HOLD receipt={path}")
        else:
            path = qualify(args.evidence, args.output, root, args.keyring,
                           args.keyring_sha256)
            result = parse_json(stable_read(path, "qualification output"),
                                "qualification output")
            marker = "GO" if result["candidate_go"] else "HOLD"
            print(f"K2_SINGLE_EDGE_VECTORLESS_{marker} receipt={path}")
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"K2_SINGLE_EDGE_VECTORLESS_FAIL {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
