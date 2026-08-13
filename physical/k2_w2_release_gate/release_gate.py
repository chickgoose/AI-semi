#!/usr/bin/env python3
"""Receipt-only, fail-closed release gate for one K2 W2 ranking cohort.

This module deliberately does not open or parse EDA reports, waveforms, trace
CSVs, or metric tables.  Those are responsibilities of the upstream receipt
producers.  It authenticates the immutable receipt bytes, checks that all seven
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
    "server_environment": "k2_w2_server_env_receipt_v1",
    "tech_staged_manifest": "k2_w2_tech_staged_manifest_receipt_v1",
    "genus": "k2_w2_genus_receipt_v2",
    "innovus": "k2_w2_innovus_receipt_v1",
    "activity_power": "k2_w2_activity_power_receipt_v1",
    "functional_loss": "k2_w2_functional_loss_receipt_v1",
    "boundary": "k2_w2_boundary_receipt_v1",
}
ATTESTED_ROLES = tuple(role for role in ROLE_SCHEMAS if role != "boundary")
ROLES = tuple(ROLE_SCHEMAS)

EXPECTED_CANDIDATES = ["fovea_a7", "a2_p6", "a3_p6"]
EXPECTED_COHORT = "tech_staged_complete_compositions"
EXPECTED_TOPS = {
    "fovea_a7": "w2_fovea_r1_physical_staging_top",
    "a2_p6": "w2_a2_p6_physical_staging_top",
    "a3_p6": "w2_a3_p6_physical_staging_top",
}
EXPECTED_LINK_PORTS = {
    "fovea_a7": ["link_clk_o", "link_data_o[1:0]"],
    "a2_p6": ["link_clk_o", "link_data_o[4:0]"],
    "a3_p6": ["link_clk_o", "link_data_o[4:0]"],
}
EXPECTED_LINK_BITS = {"fovea_a7": 3, "a2_p6": 6, "a3_p6": 6}
POINT_RECEIPT_SCHEMAS = {
    "innovus": "k2_w2_innovus_run_receipt_v1",
    "sta": "k2_w2_postroute_sta_receipt_v1",
    "drc": "k2_w2_postroute_drc_receipt_v1",
    "connectivity": "k2_w2_postroute_connectivity_receipt_v1",
}

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


def _validate_corner(value: Any, label: str) -> dict[str, Any]:
    corner = exact_keys(value,
                        {"process", "voltage_v", "temperature_c", "operating_condition"},
                        label)
    string(corner["process"], f"{label}.process", ID_RE)
    decimal_string(corner["voltage_v"], f"{label}.voltage_v")
    decimal_string(corner["temperature_c"], f"{label}.temperature_c")
    string(corner["operating_condition"], f"{label}.operating_condition", ID_RE)
    return corner


def validate_provenance(value: Any, label: str) -> dict[str, Any]:
    provenance = exact_keys(
        value,
        {"server_environment", "technology", "pvt", "sdc", "load",
         "staged_manifest", "workload"}, label)
    server = exact_keys(provenance["server_environment"],
                        {"environment_id", "contract_sha256"},
                        f"{label}.server_environment")
    string(server["environment_id"], f"{label}.server_environment.environment_id", ID_RE)
    digest(server["contract_sha256"], f"{label}.server_environment.contract_sha256")

    technology = exact_keys(
        provenance["technology"],
        {"setup_liberty_sha256", "hold_liberty_sha256", "tech_lef_sha256",
         "cell_lef_sha256", "shared_qrc_sha256"}, f"{label}.technology")
    for name, value_ in technology.items():
        digest(value_, f"{label}.technology.{name}")
    if technology["setup_liberty_sha256"] == technology["hold_liberty_sha256"]:
        raise ReleaseGateError(f"{label} setup/hold Liberty identities must differ")

    pvt = exact_keys(provenance["pvt"], {"setup", "hold", "shared_rc_corner"},
                     f"{label}.pvt")
    _validate_corner(pvt["setup"], f"{label}.pvt.setup")
    _validate_corner(pvt["hold"], f"{label}.pvt.hold")
    if pvt["setup"] == pvt["hold"]:
        raise ReleaseGateError(f"{label} setup/hold PVT views must differ")
    if pvt["shared_rc_corner"] != "gpdk045_typical_shared":
        raise ReleaseGateError(f"{label}.pvt.shared_rc_corner mismatch")

    sdc = exact_keys(provenance["sdc"],
                     {"constraint_set_id", "sha256", "clock_schema"}, f"{label}.sdc")
    string(sdc["constraint_set_id"], f"{label}.sdc.constraint_set_id", ID_RE)
    digest(sdc["sha256"], f"{label}.sdc.sha256")
    if sdc["clock_schema"] != "k2_w2_multiclock_full_link_v6":
        raise ReleaseGateError(f"{label}.sdc.clock_schema must be multi-clock full-link v6")

    load = exact_keys(provenance["load"], {"model_id", "sha256", "output_load_pf"},
                      f"{label}.load")
    string(load["model_id"], f"{label}.load.model_id", ID_RE)
    digest(load["sha256"], f"{label}.load.sha256")
    if decimal_string(load["output_load_pf"], f"{label}.load.output_load_pf") < 0:
        raise ReleaseGateError(f"{label}.load.output_load_pf must be nonnegative")

    staged = exact_keys(
        provenance["staged_manifest"],
        {"schema", "sha256", "repository_commit", "normalized_boundary_sha256"},
        f"{label}.staged_manifest")
    if staged["schema"] != "k2_w2_tech_staged_compositions_v1":
        raise ReleaseGateError(f"{label}.staged_manifest schema mismatch")
    digest(staged["sha256"], f"{label}.staged_manifest.sha256")
    string(staged["repository_commit"], f"{label}.staged_manifest.repository_commit",
           GIT_SHA1_RE)
    digest(staged["normalized_boundary_sha256"],
           f"{label}.staged_manifest.normalized_boundary_sha256")

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
    if candidates != EXPECTED_CANDIDATES:
        raise ReleaseGateError(
            f"{label}.candidate_ids must equal the exact ordered final three-candidate set")
    if campaign["cohort_id"] != EXPECTED_COHORT:
        raise ReleaseGateError(f"{label}.cohort_id is not the final tech-staged cohort")
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


def validate_server_environment(receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
    provenance = campaign["provenance"]
    environment = exact_keys(
        receipt.get("environment"),
        {"environment_id", "contract_sha256", "qualification_status", "tools",
         "technology", "pvt", "final_cohort_only"}, "server_environment.environment")
    if (environment["environment_id"] != provenance["server_environment"]["environment_id"] or
            environment["contract_sha256"] !=
            provenance["server_environment"]["contract_sha256"]):
        raise ReleaseGateError("server environment identity differs from campaign provenance")
    if (environment["qualification_status"] != "PROVEN" or
            environment["final_cohort_only"] is not True):
        raise ReleaseGateError("server environment is not PROVEN for the final cohort")
    tools = exact_keys(environment["tools"], {"genus", "innovus"},
                       "server_environment.environment.tools")
    expected_versions = {"genus": "23.14-s090_1", "innovus": "23.14-s088_1"}
    for tool, version in expected_versions.items():
        row = exact_keys(tools[tool], {"version", "executable_sha256"},
                         f"server_environment.environment.tools.{tool}")
        if row["version"] != version:
            raise ReleaseGateError(f"server environment {tool} version mismatch")
        digest(row["executable_sha256"],
               f"server_environment.environment.tools.{tool}.executable_sha256")
    if environment["technology"] != provenance["technology"]:
        raise ReleaseGateError("server environment technology differs from campaign provenance")
    if environment["pvt"] != provenance["pvt"]:
        raise ReleaseGateError("server environment PVT differs from campaign provenance")


def validate_staged_manifest(receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
    manifest = exact_keys(
        receipt.get("manifest"),
        {"schema", "sha256", "repository_commit", "normalized_boundary_sha256",
         "candidate_ids", "tops", "link_ports_preserved"},
        "tech_staged_manifest.manifest")
    expected = campaign["provenance"]["staged_manifest"]
    for key in ("schema", "sha256", "repository_commit", "normalized_boundary_sha256"):
        if manifest[key] != expected[key]:
            raise ReleaseGateError(f"tech-staged manifest {key} differs from campaign provenance")
    if (manifest["candidate_ids"] != EXPECTED_CANDIDATES or
            manifest["tops"] != EXPECTED_TOPS or manifest["link_ports_preserved"] is not True):
        raise ReleaseGateError("tech-staged manifest is not the exact normalized three-top set")
    results = receipt["candidate_results"]
    for candidate in EXPECTED_CANDIDATES:
        row = exact_keys(results[candidate], {"status", "top", "link_ports", "link_bits"},
                         f"tech_staged_manifest.candidate_results.{candidate}")
        if row != {"status": "PASS", "top": EXPECTED_TOPS[candidate],
                   "link_ports": EXPECTED_LINK_PORTS[candidate],
                   "link_bits": EXPECTED_LINK_BITS[candidate]}:
            raise ReleaseGateError(f"tech-staged manifest candidate contract mismatch: {candidate}")


def validate_genus(receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
    provenance = campaign["provenance"]
    if (receipt.get("boundary_cohort") != EXPECTED_COHORT or
            receipt.get("source_origin") != "tech_staged_repository_exact" or
            receipt.get("staged_manifest_sha256") != provenance["staged_manifest"]["sha256"] or
            receipt.get("server_environment_contract_sha256") !=
            provenance["server_environment"]["contract_sha256"]):
        raise ReleaseGateError("Genus v2 receipt is not bound to the final staged server cohort")
    results = receipt["candidate_results"]
    hashes: set[str] = set()
    for candidate in EXPECTED_CANDIDATES:
        row = exact_keys(
            results[candidate],
            {"status", "top", "mapped_netlist_sha256", "mapped_sdc_sha256",
             "constraint_set_sha256", "report_receipt_sha256", "mapped_smoke_sha256"},
            f"genus.candidate_results.{candidate}")
        if row["status"] != "PASS" or row["top"] != EXPECTED_TOPS[candidate]:
            raise ReleaseGateError(f"Genus v2 candidate/top is not PASS: {candidate}")
        if row["constraint_set_sha256"] != provenance["sdc"]["sha256"]:
            raise ReleaseGateError(f"Genus v2 constraint provenance mismatch: {candidate}")
        for name in ("mapped_netlist_sha256", "mapped_sdc_sha256",
                     "report_receipt_sha256", "mapped_smoke_sha256"):
            value = digest(row[name], f"genus.candidate_results.{candidate}.{name}")
            if value in hashes:
                raise ReleaseGateError(f"Genus v2 reuses evidence SHA: {value}")
            hashes.add(value)


def _load_auxiliary_receipt(
    root: Path, reference: Any, label: str, claimed: dict[str, set[Any]],
    auxiliary: dict[str, tuple[Path, os.stat_result, str]],
) -> dict[str, Any]:
    reference = exact_keys(reference, {"path", "sha256"}, label)
    path = contained(root, reference["path"], f"{label}.path")
    expected = digest(reference["sha256"], f"{label}.sha256")
    if path in claimed["paths"]:
        raise ReleaseGateError(f"duplicate receipt path: {path}")
    if expected in claimed["hashes"]:
        raise ReleaseGateError(f"duplicate receipt SHA256: {expected}")
    document, data, info = read_json(path, label)
    if sha256(data) != expected:
        raise ReleaseGateError(f"{label} SHA256 mismatch")
    inode = (info.st_dev, info.st_ino)
    if inode in claimed["inodes"]:
        raise ReleaseGateError(f"{label} reuses an already claimed inode")
    receipt_id = string(document.get("receipt_id"), f"{label}.receipt_id", ID_RE)
    if receipt_id in claimed["receipt_ids"]:
        raise ReleaseGateError(f"duplicate receipt_id: {receipt_id}")
    claimed["paths"].add(path)
    claimed["hashes"].add(expected)
    claimed["inodes"].add(inode)
    claimed["receipt_ids"].add(receipt_id)
    auxiliary[label] = (path, info, expected)
    return document


def _validate_point_receipt_base(
    document: dict[str, Any], role: str, campaign: dict[str, Any], candidate: str,
    period: Decimal, label: str,
) -> None:
    if document.get("schema") != POINT_RECEIPT_SCHEMAS[role] or document.get("role") != role:
        raise ReleaseGateError(f"{label} schema/role mismatch")
    if document.get("status") != "COMPLETE":
        raise ReleaseGateError(f"{label} is not a complete actual receipt")
    if (document.get("candidate_id") != candidate or
            document.get("top") != EXPECTED_TOPS[candidate] or
            decimal_string(document.get("period_ns"), f"{label}.period_ns") != period):
        raise ReleaseGateError(f"{label} candidate/top/period mismatch")
    binding = document.get("release_binding")
    validate_campaign(binding, f"{label}.release_binding")
    if binding != campaign:
        raise ReleaseGateError(f"{label} is stale or cross-cohort")


def _validate_sta_checks(document: dict[str, Any], label: str) -> tuple[dict[str, Decimal], bool]:
    checks = document.get("checks")
    names = ("setup", "hold", "recovery", "removal")
    if not isinstance(checks, dict) or set(checks) != set(names):
        raise ReleaseGateError(f"{label}.checks must contain setup/hold/recovery/removal")
    slacks: dict[str, Decimal] = {}
    qualified = True
    for name in names:
        row = exact_keys(checks[name], {"report_sha256", "wns_ns", "tns_ns", "violations"},
                         f"{label}.checks.{name}")
        digest(row["report_sha256"], f"{label}.checks.{name}.report_sha256")
        wns = decimal_string(row["wns_ns"], f"{label}.checks.{name}.wns_ns")
        tns = decimal_string(row["tns_ns"], f"{label}.checks.{name}.tns_ns")
        violations = integer(row["violations"], f"{label}.checks.{name}.violations")
        clean = wns >= 0 and tns >= 0 and violations == 0
        if clean != (violations == 0 and wns >= 0 and tns >= 0):
            raise ReleaseGateError(f"{label}.checks.{name} is incoherent")
        if violations == 0 and (wns < 0 or tns < 0):
            raise ReleaseGateError(f"{label}.checks.{name} negative timing lacks violations")
        if violations > 0 and wns >= 0 and tns >= 0:
            raise ReleaseGateError(f"{label}.checks.{name} violations contradict timing")
        slacks[name] = wns
        qualified = qualified and clean
    return slacks, qualified


def _validate_point_receipts(
    root: Path, refs: Any, campaign: dict[str, Any], candidate: str, period: Decimal,
    label: str, claimed: dict[str, set[Any]],
    auxiliary: dict[str, tuple[Path, os.stat_result, str]],
) -> tuple[Decimal, bool]:
    if not isinstance(refs, dict) or set(refs) != set(POINT_RECEIPT_SCHEMAS):
        raise ReleaseGateError(f"{label}.receipts must contain Innovus/STA/DRC/connectivity")
    documents = {}
    for role in POINT_RECEIPT_SCHEMAS:
        receipt_label = f"{label}.{role}"
        document = _load_auxiliary_receipt(
            root, refs[role], receipt_label, claimed, auxiliary)
        _validate_point_receipt_base(document, role, campaign, candidate, period, receipt_label)
        documents[role] = document

    innovus = documents["innovus"]
    if innovus.get("clean_exit") is not True:
        raise ReleaseGateError(f"{label}.innovus did not exit cleanly")
    for name in ("postroute_netlist_sha256", "database_sha256", "tool_log_sha256"):
        digest(innovus.get(name), f"{label}.innovus.{name}")

    slacks, timing_clean = _validate_sta_checks(documents["sta"], f"{label}.sta")
    drc = exact_keys(documents["drc"].get("checks"), {"drc", "antenna"},
                     f"{label}.drc.checks")
    drc_clean = True
    for name, row in drc.items():
        row = exact_keys(row, {"report_sha256", "violations"}, f"{label}.drc.checks.{name}")
        digest(row["report_sha256"], f"{label}.drc.checks.{name}.report_sha256")
        drc_clean = drc_clean and integer(
            row["violations"], f"{label}.drc.checks.{name}.violations") == 0

    connectivity = exact_keys(documents["connectivity"].get("checks"), {"signal", "pg"},
                              f"{label}.connectivity.checks")
    connectivity_clean = True
    for name, row in connectivity.items():
        row = exact_keys(row, {"report_sha256", "opens", "shorts", "unconnected"},
                         f"{label}.connectivity.checks.{name}")
        digest(row["report_sha256"], f"{label}.connectivity.checks.{name}.report_sha256")
        for field in ("opens", "shorts", "unconnected"):
            connectivity_clean = connectivity_clean and integer(
                row[field], f"{label}.connectivity.checks.{name}.{field}") == 0
    return slacks["setup"], timing_clean and drc_clean and connectivity_clean


def validate_frequency_sweeps(
    receipt: dict[str, Any], campaign: dict[str, Any], root: Path,
    claimed: dict[str, set[Any]], auxiliary: dict[str, tuple[Path, os.stat_result, str]],
) -> None:
    candidates = campaign["candidate_ids"]
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
        setup_slacks: list[Decimal] = []
        passed: list[bool] = []
        for index, point in enumerate(points):
            point = exact_keys(point, {"period_ns", "receipts"},
                               f"{label}.points[{index}]")
            period = decimal_string(point["period_ns"], f"{label}.points[{index}].period_ns")
            if period <= 0:
                raise ReleaseGateError(f"{label}.points[{index}].period_ns must be positive")
            setup_slack, qualified = _validate_point_receipts(
                root, point["receipts"], campaign, candidate, period,
                f"{label}.points[{index}]", claimed, auxiliary)
            periods.append(period)
            setup_slacks.append(setup_slack)
            passed.append(qualified)
        if any(right <= left for left, right in zip(periods, periods[1:])):
            raise ReleaseGateError(f"{label} periods are not strictly increasing")
        if any(right < left for left, right in zip(setup_slacks, setup_slacks[1:])):
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


def validate_activity_power(receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
    activity = exact_keys(
        receipt.get("activity"),
        {"mode", "measurement", "authentication"}, "activity_power.activity")
    if activity["mode"] != "SAIF":
        raise ReleaseGateError("activity_power.activity.mode must be SAIF, not vectorless")
    measurement = exact_keys(
        activity["measurement"],
        {"trace_bundle_sha256", "workload_window_id", "window_start_cycle",
         "window_end_cycle_exclusive", "measurement_cycles", "clock_period_ns"},
        "activity_power.activity.measurement")
    if measurement["trace_bundle_sha256"] != \
            campaign["provenance"]["workload"]["trace_bundle_sha256"]:
        raise ReleaseGateError("activity-power trace differs from campaign workload")
    string(measurement["workload_window_id"],
           "activity_power.activity.measurement.workload_window_id", ID_RE)
    start = integer(measurement["window_start_cycle"],
                    "activity_power.activity.measurement.window_start_cycle")
    end = integer(measurement["window_end_cycle_exclusive"],
                  "activity_power.activity.measurement.window_end_cycle_exclusive", 1)
    cycles = integer(measurement["measurement_cycles"],
                     "activity_power.activity.measurement.measurement_cycles", 1)
    period = decimal_string(measurement["clock_period_ns"],
                            "activity_power.activity.measurement.clock_period_ns")
    if end - start != cycles or period <= 0:
        raise ReleaseGateError("activity-power measurement window is inconsistent")
    authentication = exact_keys(
        activity["authentication"], {"method", "boundary_role", "scope"},
        "activity_power.activity.authentication")
    if authentication != {
        "method": "BOUNDARY_HMAC_SHA256",
        "boundary_role": "boundary",
        "scope": "ENTIRE_ACTIVITY_POWER_RECEIPT_SHA256",
    }:
        raise ReleaseGateError("activity-power evidence is unauthenticated")
    results = receipt.get("candidate_results")
    if not isinstance(results, dict) or set(results) != set(EXPECTED_CANDIDATES):
        raise ReleaseGateError("activity-power candidate results are not the exact ordered cohort")
    evidence_hashes: set[str] = set()
    for candidate in EXPECTED_CANDIDATES:
        row = exact_keys(
            results[candidate],
            {"status", "saif_sha256", "power_report_sha256", "scope_sha256",
             "coverage_percent", "retired_events", "total_power_mw", "dynamic_power_mw",
             "leakage_power_mw", "energy_pj_per_event"},
            f"activity_power.candidate_results.{candidate}")
        if row["status"] != "PASS":
            raise ReleaseGateError(f"activity-power candidate is not PASS: {candidate}")
        for name in ("saif_sha256", "power_report_sha256", "scope_sha256"):
            value = digest(row[name], f"activity_power.candidate_results.{candidate}.{name}")
            if value in evidence_hashes:
                raise ReleaseGateError("activity-power reuses candidate evidence hashes")
            evidence_hashes.add(value)
        coverage = row["coverage_percent"]
        if (isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or
                not math.isfinite(coverage) or coverage <= 0 or coverage > 100):
            raise ReleaseGateError(f"activity-power coverage is invalid: {candidate}")
        retired = integer(row["retired_events"],
                          f"activity_power.candidate_results.{candidate}.retired_events", 1)
        total = decimal_string(row["total_power_mw"],
                               f"activity_power.candidate_results.{candidate}.total_power_mw")
        dynamic = decimal_string(row["dynamic_power_mw"],
                                 f"activity_power.candidate_results.{candidate}.dynamic_power_mw")
        leakage = decimal_string(row["leakage_power_mw"],
                                 f"activity_power.candidate_results.{candidate}.leakage_power_mw")
        energy = decimal_string(row["energy_pj_per_event"],
                                f"activity_power.candidate_results.{candidate}.energy_pj_per_event")
        if min(total, dynamic, leakage, energy) < 0 or total != dynamic + leakage:
            raise ReleaseGateError(f"activity-power component total is inconsistent: {candidate}")
        if energy * retired != total * cycles * period:
            raise ReleaseGateError(f"activity-power energy/event is not derived: {candidate}")


def validate_functional(receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
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
    measurement = exact_keys(
        receipt.get("measurement"),
        {"trace_bundle_sha256", "full_manifest_sha256", "capacity_manifest_sha256",
         "full_run_count", "capacity_run_count"}, "functional_loss.measurement")
    workload = campaign["provenance"]["workload"]
    expected_measurement = {
        "trace_bundle_sha256": workload["trace_bundle_sha256"],
        "full_manifest_sha256": workload["full_manifest_sha256"],
        "capacity_manifest_sha256": workload["capacity_manifest_sha256"],
        "full_run_count": 50, "capacity_run_count": 22,
    }
    if measurement != expected_measurement:
        raise ReleaseGateError("functional-loss workload provenance differs from campaign")
    results = receipt.get("candidate_results")
    if not isinstance(results, dict) or set(results) != set(EXPECTED_CANDIDATES):
        raise ReleaseGateError("functional-loss candidate results are not the exact ordered cohort")
    result_hashes: set[str] = set()
    for candidate in EXPECTED_CANDIDATES:
        row = exact_keys(
            results[candidate],
            {"status", "result_receipt_sha256", "generated", "source_overrun",
             "accepted", "delivered", "errors"},
            f"functional_loss.candidate_results.{candidate}")
        evidence = digest(row["result_receipt_sha256"],
                          f"functional_loss.candidate_results.{candidate}.result_receipt_sha256")
        if evidence in result_hashes:
            raise ReleaseGateError("functional-loss reuses candidate evidence hash")
        result_hashes.add(evidence)
        values = {name: integer(row[name], f"functional_loss.{candidate}.{name}")
                  for name in ("generated", "source_overrun", "accepted", "delivered", "errors")}
        if (row["status"] != "PASS" or values["errors"] != 0 or
                values["generated"] != values["source_overrun"] + values["accepted"] or
                values["accepted"] != values["delivered"]):
            raise ReleaseGateError(f"functional-loss conservation failed: {candidate}")


def validate_boundary(receipt: dict[str, Any], campaign: dict[str, Any]) -> None:
    seam = digest(receipt.get("common_non_link_seam_sha256"),
                  "boundary.common_non_link_seam_sha256")
    if seam != campaign["provenance"]["staged_manifest"]["normalized_boundary_sha256"]:
        raise ReleaseGateError("boundary common non-link seam differs from staged manifest")
    policy = exact_keys(
        receipt.get("seam_policy"),
        {"common_non_link_seam_identical", "hidden_storage", "link_outputs_retained"},
        "boundary.seam_policy")
    if policy != {"common_non_link_seam_identical": True, "hidden_storage": False,
                  "link_outputs_retained": True}:
        raise ReleaseGateError("boundary seam policy permits hidden adaptation or removed link ports")
    results = receipt["candidate_results"]
    for candidate in EXPECTED_CANDIDATES:
        row = exact_keys(results[candidate], {"status", "top", "clock_contract", "link_cut"},
                         f"boundary.candidate_results.{candidate}")
        if row["status"] != "PASS" or row["top"] != EXPECTED_TOPS[candidate]:
            raise ReleaseGateError(f"boundary candidate/top mismatch: {candidate}")
        clocks = exact_keys(
            row["clock_contract"],
            {"schema", "input_clocks", "generated_clocks", "gated_clocks"},
            f"boundary.candidate_results.{candidate}.clock_contract")
        if clocks != {
            "schema": "k2_w2_multiclock_full_link_v6",
            "input_clocks": ["ref_clk_i", "sample_clk_i"],
            "generated_clocks": ["link_clk_o"],
            "gated_clocks": ["link_clk_o"],
        }:
            raise ReleaseGateError(f"boundary multi-clock contract mismatch: {candidate}")
        cut = exact_keys(
            row["link_cut"],
            {"marker", "ports", "physical_link_bits", "native_boundary_link_bits",
             "link_cut_accounted_bits", "total_accounted_link_bits",
             "tx_rx_same_nets_connected", "external_load_applied_once"},
            f"boundary.candidate_results.{candidate}.link_cut")
        expected_bits = EXPECTED_LINK_BITS[candidate]
        if (cut["marker"] != "AER_LINK_CUT" or cut["ports"] != EXPECTED_LINK_PORTS[candidate] or
                cut["physical_link_bits"] != expected_bits or
                cut["native_boundary_link_bits"] != 0 or
                cut["link_cut_accounted_bits"] != expected_bits or
                cut["total_accounted_link_bits"] != expected_bits or
                cut["native_boundary_link_bits"] + cut["link_cut_accounted_bits"] != expected_bits or
                cut["tx_rx_same_nets_connected"] is not True or
                cut["external_load_applied_once"] is not True):
            raise ReleaseGateError(f"boundary AER_LINK_CUT accounting is omitted or doubled: {candidate}")


def validate_role_receipt(
    role: str, receipt: dict[str, Any], campaign: dict[str, Any], root: Path,
    claimed: dict[str, set[Any]], auxiliary: dict[str, tuple[Path, os.stat_result, str]],
) -> None:
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
    if role == "server_environment":
        validate_server_environment(receipt, campaign)
    elif role == "tech_staged_manifest":
        validate_staged_manifest(receipt, campaign)
    elif role == "genus":
        validate_genus(receipt, campaign)
    elif role == "innovus":
        validate_frequency_sweeps(receipt, campaign, root, claimed, auxiliary)
    elif role == "activity_power":
        validate_activity_power(receipt, campaign)
    elif role == "functional_loss":
        validate_functional(receipt, campaign)
    elif role == "boundary":
        validate_boundary(receipt, campaign)


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
    expected_hashes = {role: receipt_hashes[role] for role in ATTESTED_ROLES}
    if payload["receipt_sha256"] != expected_hashes:
        raise ReleaseGateError("boundary attestation does not bind every upstream receipt")
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
            "RELEASE_ID_EXACT_CAMPAIGN_SIX_UPSTREAM_RECEIPT_SHA256_AND_BOUNDARY_BODY"),
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

    claimed: dict[str, set[Any]] = {
        "receipt_ids": receipt_ids, "paths": paths, "hashes": hashes, "inodes": inodes,
    }
    auxiliary: dict[str, tuple[Path, os.stat_result, str]] = {}
    for role in ROLES:
        validate_role_receipt(
            role, by_role[role][0], campaign, root, claimed, auxiliary)

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
    for label, (path, expected_info, _) in auxiliary.items():
        try:
            current = path.lstat()
        except OSError as exc:
            raise ReleaseGateError(f"{label} vanished before publication") from exc
        if stat.S_ISLNK(current.st_mode) or _identity(current) != _identity(expected_info):
            raise ReleaseGateError(f"{label} changed before publication")
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
        "postroute_receipt_sha256": {
            label: value[2] for label, value in sorted(auxiliary.items())},
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
