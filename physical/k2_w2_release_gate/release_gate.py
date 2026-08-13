#!/usr/bin/env python3
"""Receipt-only, fail-closed release gate for one K2 W2 ranking cohort.

This module deliberately does not open or parse EDA reports, waveforms, trace
CSVs, or metric tables.  Those are responsibilities of the upstream receipt
producers.  It authenticates the immutable receipt bytes, checks that all five
producers describe the same campaign, and emits permission to rank without
copying or manufacturing any metric.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


MANIFEST_SCHEMA = "k2_w2_release_manifest_v1"
KEYRING_SCHEMA = "k2_w2_release_keyring_v1"
OUTPUT_SCHEMA = "k2_w2_release_gate_receipt_v1"
BOUNDARY_ATTESTATION_SCHEMA = "k2_w2_boundary_attestation_v1"

ROLE_SCHEMAS = {
    "genus": "k2_w2_genus_receipt_v1",
    "innovus": "k2_w2_innovus_receipt_v1",
    "activity_power": "k2_w2_activity_power_receipt_v1",
    "functional_loss": "k2_w2_functional_loss_receipt_v1",
    "boundary": "k2_w2_boundary_receipt_v1",
}
METRIC_ROLES = tuple(role for role in ROLE_SCHEMAS if role != "boundary")
ROLES = tuple(ROLE_SCHEMAS)

SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_SHA1_RE = re.compile(r"[0-9a-f]{40}")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")


class ReleaseGateError(ValueError):
    """An input is incomplete, inconsistent, stale, or unauthenticated."""


def canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ReleaseGateError(f"value is not canonical JSON data: {exc}") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def stable_read(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    """Read one regular, non-linked file and detect concurrent replacement."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise ReleaseGateError(f"{label} is not a regular non-symlink file: {path}")
        if before.st_nlink != 1:
            raise ReleaseGateError(f"{label} must not be hard-linked: {path}")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read()
            after_read = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise ReleaseGateError(f"cannot read {label} {path}: {exc}") from exc
    if not (_identity(before) == _identity(opened) == _identity(after_read) == _identity(after)):
        raise ReleaseGateError(f"{label} changed while being read: {path}")
    return data, after


def read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes, os.stat_result]:
    data, info = stable_read(path, label)
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseGateError(f"{label} must be a JSON object")
    return value, data, info


def exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseGateError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise ReleaseGateError(
            f"{label} key mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}")
    return value


def string(value: Any, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseGateError(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ReleaseGateError(f"{label} has invalid syntax")
    return value


def integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReleaseGateError(f"{label} must be an integer >= {minimum}")
    return value


def digest(value: Any, label: str) -> str:
    return string(value, label, SHA256_RE)


def decimal_string(value: Any, label: str) -> Decimal:
    text = string(value, label)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ReleaseGateError(f"{label} must be a decimal string") from exc
    if not number.is_finite():
        raise ReleaseGateError(f"{label} must be finite")
    return number


def contained(root: Path, value: Any, label: str) -> Path:
    relative = Path(string(value, label))
    if relative.is_absolute() or ".." in relative.parts or str(relative) != str(value):
        raise ReleaseGateError(f"{label} must be a normalized contained relative path")
    root = root.resolve(strict=True)
    candidate = root / relative
    if root not in candidate.resolve(strict=False).parents:
        raise ReleaseGateError(f"{label} escapes the bundle root")
    component = root
    for part in relative.parts:
        component /= part
        try:
            if stat.S_ISLNK(component.lstat().st_mode):
                raise ReleaseGateError(f"{label} contains a symlink: {relative}")
        except FileNotFoundError:
            break
    return candidate


def validate_provenance(value: Any, label: str) -> dict[str, Any]:
    provenance = exact_keys(value, {"liberty", "pvt", "sdc", "load", "workload"}, label)
    liberty = exact_keys(provenance["liberty"], {"library_set_id", "sha256"},
                         f"{label}.liberty")
    string(liberty["library_set_id"], f"{label}.liberty.library_set_id", ID_RE)
    digest(liberty["sha256"], f"{label}.liberty.sha256")

    pvt = exact_keys(provenance["pvt"],
                     {"process", "voltage_v", "temperature_c", "operating_condition"},
                     f"{label}.pvt")
    string(pvt["process"], f"{label}.pvt.process", ID_RE)
    decimal_string(pvt["voltage_v"], f"{label}.pvt.voltage_v")
    decimal_string(pvt["temperature_c"], f"{label}.pvt.temperature_c")
    string(pvt["operating_condition"], f"{label}.pvt.operating_condition", ID_RE)

    sdc = exact_keys(provenance["sdc"], {"constraint_set_id", "sha256"}, f"{label}.sdc")
    string(sdc["constraint_set_id"], f"{label}.sdc.constraint_set_id", ID_RE)
    digest(sdc["sha256"], f"{label}.sdc.sha256")

    load = exact_keys(provenance["load"], {"model_id", "sha256", "output_load_pf"},
                      f"{label}.load")
    string(load["model_id"], f"{label}.load.model_id", ID_RE)
    digest(load["sha256"], f"{label}.load.sha256")
    if decimal_string(load["output_load_pf"], f"{label}.load.output_load_pf") < 0:
        raise ReleaseGateError(f"{label}.load.output_load_pf must be nonnegative")

    workload = exact_keys(
        provenance["workload"],
        {"suite_id", "generator_version", "full_run_count", "capacity_run_count",
         "full_manifest_sha256", "capacity_manifest_sha256", "trace_bundle_sha256"},
        f"{label}.workload")
    string(workload["suite_id"], f"{label}.workload.suite_id", ID_RE)
    if integer(workload["generator_version"], f"{label}.workload.generator_version", 1) != 4:
        raise ReleaseGateError(f"{label}.workload.generator_version must be frozen v4")
    if integer(workload["full_run_count"], f"{label}.workload.full_run_count", 1) != 50:
        raise ReleaseGateError(f"{label}.workload.full_run_count must equal 50")
    if integer(workload["capacity_run_count"],
               f"{label}.workload.capacity_run_count", 1) != 22:
        raise ReleaseGateError(f"{label}.workload.capacity_run_count must equal 22")
    for name in ("full_manifest_sha256", "capacity_manifest_sha256", "trace_bundle_sha256"):
        digest(workload[name], f"{label}.workload.{name}")
    return provenance


def validate_campaign(value: Any, label: str) -> dict[str, Any]:
    campaign = exact_keys(
        value,
        {"campaign_id", "generation", "nonce", "cohort_id", "candidate_ids",
         "candidate_commits", "provenance"}, label)
    string(campaign["campaign_id"], f"{label}.campaign_id", ID_RE)
    integer(campaign["generation"], f"{label}.generation", 1)
    digest(campaign["nonce"], f"{label}.nonce")
    string(campaign["cohort_id"], f"{label}.cohort_id", ID_RE)
    candidates = campaign["candidate_ids"]
    if (not isinstance(candidates, list) or len(candidates) < 2 or
            any(not isinstance(item, str) or ID_RE.fullmatch(item) is None for item in candidates)):
        raise ReleaseGateError(f"{label}.candidate_ids must contain at least two valid IDs")
    if len(candidates) != len(set(candidates)) or candidates != sorted(candidates):
        raise ReleaseGateError(f"{label}.candidate_ids must be unique and sorted")
    commits = campaign["candidate_commits"]
    if not isinstance(commits, dict) or set(commits) != set(candidates):
        raise ReleaseGateError(f"{label}.candidate_commits must exactly match candidate_ids")
    for candidate, commit in commits.items():
        string(commit, f"{label}.candidate_commits.{candidate}", GIT_SHA1_RE)
    validate_provenance(campaign["provenance"], f"{label}.provenance")
    return campaign


def validate_candidate_results(receipt: dict[str, Any], candidates: list[str], label: str) -> None:
    results = receipt.get("candidate_results")
    if not isinstance(results, dict) or set(results) != set(candidates):
        raise ReleaseGateError(f"{label}.candidate_results must exactly match campaign candidates")
    for candidate in candidates:
        row = results[candidate]
        if not isinstance(row, dict) or row.get("status") != "PASS":
            raise ReleaseGateError(f"{label}.candidate_results.{candidate} is not PASS")


def validate_frequency_sweeps(receipt: dict[str, Any], candidates: list[str]) -> None:
    sweeps = receipt.get("frequency_sweeps")
    if not isinstance(sweeps, dict) or set(sweeps) != set(candidates):
        raise ReleaseGateError("innovus.frequency_sweeps must exactly match campaign candidates")
    for candidate in candidates:
        label = f"innovus.frequency_sweeps.{candidate}"
        sweep = exact_keys(
            sweeps[candidate],
            {"status", "points", "qualified_bracket", "selected_period_ns",
             "cherry_pick_forbidden"}, label)
        if sweep["status"] != "MONOTONIC_QUALIFIED" or sweep["cherry_pick_forbidden"] is not True:
            raise ReleaseGateError(f"{label} is not a monotonic qualified sweep")
        points = sweep["points"]
        if not isinstance(points, list) or len(points) < 2:
            raise ReleaseGateError(f"{label}.points must contain a fail/pass sweep")
        periods: list[Decimal] = []
        slacks: list[Decimal] = []
        passed: list[bool] = []
        for index, point in enumerate(points):
            point = exact_keys(point, {"period_ns", "wns_ns", "qualified"},
                               f"{label}.points[{index}]")
            period = decimal_string(point["period_ns"], f"{label}.points[{index}].period_ns")
            slack = decimal_string(point["wns_ns"], f"{label}.points[{index}].wns_ns")
            qualified = point["qualified"]
            if not isinstance(qualified, bool) or qualified != (slack >= 0):
                raise ReleaseGateError(f"{label}.points[{index}] qualification contradicts WNS")
            if period <= 0:
                raise ReleaseGateError(f"{label}.points[{index}].period_ns must be positive")
            periods.append(period)
            slacks.append(slack)
            passed.append(qualified)
        if any(right <= left for left, right in zip(periods, periods[1:])):
            raise ReleaseGateError(f"{label} periods are not strictly increasing")
        if any(right < left for left, right in zip(slacks, slacks[1:])):
            raise ReleaseGateError(f"{label} has non-monotonic Fmax slack")
        if True not in passed or False not in passed:
            raise ReleaseGateError(f"{label} does not contain both a fail and a pass")
        first_pass = passed.index(True)
        if first_pass == 0 or any(not item for item in passed[first_pass:]):
            raise ReleaseGateError(f"{label} has a pass-to-fail reversion")
        bracket = exact_keys(
            sweep["qualified_bracket"], {"last_fail_period_ns", "first_pass_period_ns"},
            f"{label}.qualified_bracket")
        last_fail = decimal_string(bracket["last_fail_period_ns"],
                                   f"{label}.qualified_bracket.last_fail_period_ns")
        first = decimal_string(bracket["first_pass_period_ns"],
                               f"{label}.qualified_bracket.first_pass_period_ns")
        selected = decimal_string(sweep["selected_period_ns"], f"{label}.selected_period_ns")
        if (last_fail != periods[first_pass - 1] or first != periods[first_pass] or
                selected != periods[first_pass]):
            raise ReleaseGateError(f"{label} bracket/selection is cherry-picked or inconsistent")


def validate_activity_power(receipt: dict[str, Any], candidates: list[str]) -> None:
    activity = exact_keys(
        receipt.get("activity"),
        {"mode", "saif_sha256", "scope_sha256", "window_sha256",
         "coverage_percent", "authentication"}, "activity_power.activity")
    if activity["mode"] != "SAIF":
        raise ReleaseGateError("activity_power.activity.mode must be SAIF, not vectorless")
    for name in ("saif_sha256", "scope_sha256", "window_sha256"):
        digest(activity[name], f"activity_power.activity.{name}")
    coverage = activity["coverage_percent"]
    if (isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or
            not math.isfinite(coverage) or coverage <= 0 or coverage > 100):
        raise ReleaseGateError("activity_power.activity.coverage_percent must be in (0,100]")
    authentication = exact_keys(
        activity["authentication"], {"method", "boundary_role", "scope"},
        "activity_power.activity.authentication")
    if authentication != {
        "method": "BOUNDARY_HMAC_SHA256",
        "boundary_role": "boundary",
        "scope": "ENTIRE_ACTIVITY_POWER_RECEIPT_SHA256",
    }:
        raise ReleaseGateError("activity-power evidence is unauthenticated")
    validate_candidate_results(receipt, candidates, "activity_power")


def validate_functional(receipt: dict[str, Any], candidates: list[str]) -> None:
    claim = exact_keys(
        receipt.get("claim_boundary"),
        {"loss_accounting", "accepted_event_conservation", "official_common_receipt",
         "workspace_diff", "ppa_usage"}, "functional_loss.claim_boundary")
    expected = {
        "loss_accounting": "GO",
        "accepted_event_conservation": "GO",
        "official_common_receipt": "GO",
        "workspace_diff": False,
        "ppa_usage": "FORBIDDEN",
    }
    if claim != expected:
        raise ReleaseGateError("functional-loss receipt is non-official, stale, or exceeds loss scope")
    validate_candidate_results(receipt, candidates, "functional_loss")


def validate_role_receipt(role: str, receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
    label = f"{role} receipt"
    if receipt.get("schema") != ROLE_SCHEMAS[role]:
        raise ReleaseGateError(f"{label} schema mismatch")
    if receipt.get("role") != role:
        raise ReleaseGateError(f"{label} role mismatch")
    string(receipt.get("receipt_id"), f"{label}.receipt_id", ID_RE)
    if receipt.get("status") != "PASS":
        raise ReleaseGateError(f"{label} status is not PASS")
    binding = receipt.get("release_binding")
    validate_campaign(binding, f"{label}.release_binding")
    if binding != campaign:
        raise ReleaseGateError(f"{label} is stale or belongs to a cross-cohort campaign")
    candidates = campaign["candidate_ids"]
    validate_candidate_results(receipt, candidates, label)
    if role == "innovus":
        validate_frequency_sweeps(receipt, candidates)
    elif role == "activity_power":
        validate_activity_power(receipt, candidates)
    elif role == "functional_loss":
        validate_functional(receipt, candidates)


def validate_keyring(value: Any) -> dict[str, bytes]:
    keyring = exact_keys(value, {"schema", "keys"}, "keyring")
    if keyring["schema"] != KEYRING_SCHEMA or not isinstance(keyring["keys"], dict):
        raise ReleaseGateError("keyring schema mismatch")
    keys: dict[str, bytes] = {}
    for key_id, row in keyring["keys"].items():
        string(key_id, "keyring key_id", ID_RE)
        row = exact_keys(row, {"algorithm", "secret_hex"}, f"keyring.keys.{key_id}")
        if row["algorithm"] != "hmac-sha256":
            raise ReleaseGateError(f"keyring.keys.{key_id} algorithm must be hmac-sha256")
        secret = string(row["secret_hex"], f"keyring.keys.{key_id}.secret_hex")
        if len(secret) < 64 or len(secret) % 2 or re.fullmatch(r"[0-9a-f]+", secret) is None:
            raise ReleaseGateError(f"keyring.keys.{key_id}.secret_hex must hold >=256 bits")
        keys[key_id] = bytes.fromhex(secret)
    if not keys:
        raise ReleaseGateError("keyring contains no keys")
    return keys


def validate_boundary_authentication(
    boundary: dict[str, Any], release_id: str, campaign: dict[str, Any],
    receipt_hashes: dict[str, str], keys: dict[str, bytes],
) -> dict[str, str]:
    attestation = exact_keys(
        boundary.get("attestation"), {"algorithm", "key_id", "payload", "mac_sha256"},
        "boundary.attestation")
    if attestation["algorithm"] != "hmac-sha256":
        raise ReleaseGateError("boundary attestation algorithm mismatch")
    key_id = string(attestation["key_id"], "boundary.attestation.key_id", ID_RE)
    if key_id not in keys:
        raise ReleaseGateError("boundary attestation key is not trusted")
    payload = exact_keys(
        attestation["payload"],
        {"schema", "release_id", "campaign", "receipt_sha256", "boundary_body_sha256"},
        "boundary.attestation.payload")
    if payload["schema"] != BOUNDARY_ATTESTATION_SCHEMA or payload["release_id"] != release_id:
        raise ReleaseGateError("boundary attestation release identity mismatch")
    validate_campaign(payload["campaign"], "boundary.attestation.payload.campaign")
    if payload["campaign"] != campaign:
        raise ReleaseGateError("boundary attestation campaign mismatch")
    expected_hashes = {role: receipt_hashes[role] for role in METRIC_ROLES}
    if payload["receipt_sha256"] != expected_hashes:
        raise ReleaseGateError("boundary attestation does not bind every metric receipt")
    boundary_body = {key: value for key, value in boundary.items() if key != "attestation"}
    if digest(payload["boundary_body_sha256"],
              "boundary.attestation.payload.boundary_body_sha256") != sha256(canonical(boundary_body)):
        raise ReleaseGateError("boundary attestation does not bind the boundary receipt body")
    supplied = digest(attestation["mac_sha256"], "boundary.attestation.mac_sha256")
    calculated = hmac.new(keys[key_id], canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, calculated):
        raise ReleaseGateError("boundary attestation MAC mismatch")
    return {
        "algorithm": "hmac-sha256",
        "key_id": key_id,
        "authenticated_scope": (
            "RELEASE_ID_EXACT_CAMPAIGN_FOUR_METRIC_RECEIPT_SHA256_AND_BOUNDARY_BODY"),
    }


def load_and_gate(bundle_root: Path, manifest_path: Path, keyring_path: Path,
                  expected_keyring_sha256: str) -> dict[str, Any]:
    root = bundle_root.resolve(strict=True)
    manifest_resolved = manifest_path.resolve(strict=False)
    if root not in manifest_resolved.parents:
        raise ReleaseGateError("release manifest must be contained by the bundle root")
    manifest, manifest_data, manifest_info = read_json(manifest_path, "release manifest")
    manifest = exact_keys(manifest, {"schema", "release_id", "campaign", "receipts"},
                          "release manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ReleaseGateError("release manifest schema mismatch")
    release_id = string(manifest["release_id"], "release manifest.release_id", ID_RE)
    campaign = validate_campaign(manifest["campaign"], "release manifest.campaign")
    references = manifest["receipts"]
    if not isinstance(references, list):
        raise ReleaseGateError("release manifest.receipts must be an array")

    by_role: dict[str, tuple[dict[str, Any], bytes, os.stat_result, Path]] = {}
    receipt_ids: set[str] = set()
    paths: set[Path] = set()
    hashes: set[str] = set()
    inodes: set[tuple[int, int]] = {(manifest_info.st_dev, manifest_info.st_ino)}
    for index, reference in enumerate(references):
        reference = exact_keys(reference, {"role", "path", "sha256"},
                               f"release manifest.receipts[{index}]")
        role = string(reference["role"], f"release manifest.receipts[{index}].role")
        if role not in ROLE_SCHEMAS:
            raise ReleaseGateError(f"unknown receipt role: {role}")
        if role in by_role:
            raise ReleaseGateError(f"duplicate receipt role: {role}")
        path = contained(root, reference["path"], f"receipt {role} path")
        if path in paths:
            raise ReleaseGateError(f"duplicate receipt path: {path}")
        paths.add(path)
        expected_hash = digest(reference["sha256"], f"receipt {role} SHA256")
        if expected_hash in hashes:
            raise ReleaseGateError(f"duplicate receipt SHA256: {expected_hash}")
        hashes.add(expected_hash)
        receipt, data, info = read_json(path, f"{role} receipt")
        if sha256(data) != expected_hash:
            raise ReleaseGateError(f"{role} receipt SHA256 mismatch")
        inode = (info.st_dev, info.st_ino)
        if inode in inodes:
            raise ReleaseGateError(f"{role} receipt reuses an already claimed inode")
        inodes.add(inode)
        receipt_id = string(receipt.get("receipt_id"), f"{role} receipt.receipt_id", ID_RE)
        if receipt_id in receipt_ids:
            raise ReleaseGateError(f"duplicate receipt_id: {receipt_id}")
        receipt_ids.add(receipt_id)
        by_role[role] = (receipt, data, info, path)
    missing = sorted(set(ROLES) - set(by_role))
    extra = sorted(set(by_role) - set(ROLES))
    if missing or extra or len(references) != len(ROLES):
        raise ReleaseGateError(f"receipt role inventory mismatch; missing={missing}, extra={extra}")

    for role in ROLES:
        validate_role_receipt(role, by_role[role][0], campaign)

    expected_keyring_sha256 = digest(expected_keyring_sha256, "trusted keyring SHA256")
    keyring, keyring_data, keyring_info = read_json(keyring_path, "release keyring")
    actual_keyring_sha256 = sha256(keyring_data)
    if actual_keyring_sha256 != expected_keyring_sha256:
        raise ReleaseGateError("release keyring does not match the out-of-band trusted SHA256")
    keys = validate_keyring(keyring)
    receipt_hashes = {role: sha256(by_role[role][1]) for role in ROLES}
    authentication = validate_boundary_authentication(
        by_role["boundary"][0], release_id, campaign, receipt_hashes, keys)

    # Repeat all file identities after semantic and cryptographic validation.
    current_manifest = manifest_path.lstat()
    if _identity(current_manifest) != _identity(manifest_info):
        raise ReleaseGateError("release manifest changed before publication")
    for role, (_, _, expected_info, path) in by_role.items():
        try:
            current = path.lstat()
        except OSError as exc:
            raise ReleaseGateError(f"{role} receipt vanished before publication") from exc
        if stat.S_ISLNK(current.st_mode) or _identity(current) != _identity(expected_info):
            raise ReleaseGateError(f"{role} receipt changed before publication")
    current_keyring = keyring_path.lstat()
    if stat.S_ISLNK(current_keyring.st_mode) or _identity(current_keyring) != _identity(keyring_info):
        raise ReleaseGateError("release keyring changed before publication")

    gate_source, _ = stable_read(Path(__file__), "release gate source")
    return {
        "schema": OUTPUT_SCHEMA,
        "status": "RANKING_PERMITTED",
        "release_id": release_id,
        "campaign_id": campaign["campaign_id"],
        "generation": campaign["generation"],
        "cohort_id": campaign["cohort_id"],
        "candidate_ids": campaign["candidate_ids"],
        "candidate_commits": campaign["candidate_commits"],
        "manifest_sha256": sha256(manifest_data),
        "gate_source_sha256": sha256(gate_source),
        "upstream_receipt_sha256": receipt_hashes,
        "authentication": authentication,
        "trusted_keyring_sha256": actual_keyring_sha256,
        "decision": {
            "final_ranking": "PERMITTED_NOT_COMPUTED",
            "metric_copy_or_fabrication": "NONE",
            "raw_report_reparsing": "NONE",
        },
    }


def write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def diagnostic(error: Exception) -> dict[str, Any]:
    return {
        "schema": OUTPUT_SCHEMA,
        "status": "RANKING_HOLD",
        "diagnostic": str(error),
        "decision": {
            "final_ranking": "FORBIDDEN",
            "metric_copy_or_fabrication": "NONE",
            "raw_report_reparsing": "NONE",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--keyring", type=Path, required=True)
    parser.add_argument("--keyring-sha256", required=True,
                        help="out-of-band trusted SHA256 of the exact keyring bytes")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = load_and_gate(args.bundle_root, args.manifest, args.keyring,
                               args.keyring_sha256)
    except (ReleaseGateError, OSError) as exc:
        try:
            write_exclusive(args.output, canonical(diagnostic(exc)))
        except (ReleaseGateError, OSError) as publish_exc:
            print(f"K2_W2_RELEASE_GATE_ERROR: {exc}; diagnostic publication failed: {publish_exc}",
                  file=sys.stderr)
            return 1
        print(f"K2_W2_RELEASE_HOLD: {exc}", file=sys.stderr)
        return 2
    try:
        write_exclusive(args.output, canonical(result))
    except (ReleaseGateError, OSError) as exc:
        print(f"K2_W2_RELEASE_GATE_ERROR: cannot publish permit receipt: {exc}", file=sys.stderr)
        return 1
    print(f"K2_W2_RELEASE_RANKING_PERMITTED release_id={result['release_id']} "
          f"cohort_id={result['cohort_id']} candidates={len(result['candidate_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
