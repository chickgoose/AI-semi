#!/usr/bin/env python3
"""Offline fail-closed verifier for REDRED mapped/post-route CDC/RDC evidence.

The canonical evidence is intentionally diagnostic.  It proves structural
single-clock properties of exact archived netlists, but cannot promote the
physical campaign beyond its unauthenticated-producer HOLD boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any


CONTRACT_SCHEMA = "redred-single-edge-mapped-cdc-rdc-contract-v1"
BINDING_SCHEMA = "redred-single-edge-mapped-cdc-evidence-binding-v1"
SEMANTICS_SCHEMA = "redred-gpdk045-used-cell-semantics-v1"
MAXIMUM_DECISION = "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE"
EVIDENCE_CLASS = "CALLER_SELF_SEALED_UNAUTHENTICATED_DIAGNOSTIC_ONLY"
CANONICAL_BINDING_SHA256 = "75b1098aa72c31b047338669399b5244b05a3b38526cf8f0af18437c7ed4881a"
CANONICAL_SEMANTICS_SHA256 = "47c513955dd892b7e293adee72256b96d4291fa0c4c6591e7adcb3f63ffa5f8e"
CANONICAL_ARCHIVE_SHA256 = "9c85f74d4fd399149891bf39c56674132c46a554a15baa3d4c00d60ea198b698"
SOURCE_CDC_CONTRACT_SHA256 = "d4c96072e2edbda18a79fada7d14ffc9b6dc01ee65d5d9795336ee7ff20dbf34"
PHYSICAL_CONTRACT_SHA256 = "10e0de608ecf1992a2d253a7756a2256117c76a1c507932e65f8fbcf3742b6d2"
SOURCE_COMMIT = "eb298fe1416a4312269a6f9232e1445f8958dda2"
INTEGRATION_COMMIT = "bfb4b998049bbf9c66c4af9ffabba2c8ff096363"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
TOKEN_CLK = re.compile(r"(?<![A-Za-z0-9_$])clk_i(?![A-Za-z0-9_$])")
EXPECTED_POLICY = {
    "primary_clock": "clk_i",
    "clock_domains": 1,
    "clock_edge": "posedge",
    "sequential_clock_connection": "DIRECT_PRIMARY_CLOCK_ONLY",
    "generated_gated_forwarded_clocks": "FORBIDDEN",
    "latches": "FORBIDDEN",
    "asynchronous_controls": "FORBIDDEN",
    "clock_as_data": "FORBIDDEN",
    "external_inputs": "ASSUMED_SYNCHRONOUS_TO_PRIMARY_CLOCK",
    "unknown_cells_or_ports": "FAIL",
    "mapped_and_postroute_views_required": True,
}


class ContractError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exact_keys(value: Any, expected: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    actual = set(value)
    if actual != expected:
        raise ContractError(
            f"{where} keys differ: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}")


def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_bytes(data: bytes, where: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON in {where}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object in {where}")
    return value


def load_file(path: Path, where: str) -> tuple[bytes, dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read {where}: {exc}") from exc
    return data, load_json_bytes(data, where)


def safe_relative(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ContractError(f"{where} must be a nonempty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ContractError(f"unsafe path in {where}: {value!r}")
    return value


def require_hex64(value: Any, where: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise ContractError(f"{where} must be 64 lowercase hex digits")
    return value


def validate_contract(document: dict[str, Any]) -> None:
    exact_keys(document, {"schema", "contract_id", "decision", "binding",
                          "cell_semantics", "policy", "evidence_class",
                          "maximum_decision"}, "contract")
    if document["schema"] != CONTRACT_SCHEMA:
        raise ContractError("contract schema differs")
    if document["contract_id"] != "REDRED_A2_A3_MAPPED_AND_POSTROUTE_SINGLE_EDGE":
        raise ContractError("contract id differs")
    if document["decision"] != "DIAGNOSTIC_VERIFY_REQUIRED":
        raise ContractError("canonical contract must require diagnostic verification")
    if document["policy"] != EXPECTED_POLICY:
        raise ContractError("contract policy differs")
    if document["evidence_class"] != EVIDENCE_CLASS:
        raise ContractError("evidence class differs")
    if document["maximum_decision"] != MAXIMUM_DECISION:
        raise ContractError("contract illegally changes the release ceiling")
    for key, expected_path, expected_sha in (
        ("binding", "evidence_binding.json", CANONICAL_BINDING_SHA256),
        ("cell_semantics", "cell_semantics.json", CANONICAL_SEMANTICS_SHA256),
    ):
        exact_keys(document[key], {"path", "sha256"}, f"contract.{key}")
        if document[key]["path"] != expected_path or document[key]["sha256"] != expected_sha:
            raise ContractError(f"canonical {key} pin differs")


def validate_semantics(document: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    exact_keys(document, {"schema", "setup_liberty_sha256", "evidence_scope",
                          "sequential_cells", "combinational_cells"}, "cell semantics")
    if document["schema"] != SEMANTICS_SCHEMA:
        raise ContractError("cell semantics schema differs")
    if document["setup_liberty_sha256"] != "dec616b7b53aa5166eac9660ba83561a4057ee3b7e62f59f3d4bebad495ffe10":
        raise ContractError("setup Liberty hash differs")
    if document["evidence_scope"] != "HASH_BOUND_EXTRACT_OF_USED_GPDK045_CELL_SEMANTICS":
        raise ContractError("cell semantics scope differs")
    sequential = document["sequential_cells"]
    if not isinstance(sequential, dict) or not sequential:
        raise ContractError("sequential cell semantics must be nonempty")
    parsed: dict[str, dict[str, Any]] = {}
    for cell, value in sequential.items():
        if IDENT.fullmatch(cell) is None:
            raise ContractError(f"invalid sequential cell name: {cell!r}")
        exact_keys(value, {"clock_pin", "edge", "data_pins", "output_pins",
                           "async_control_pins"}, f"sequential cell {cell}")
        if value["clock_pin"] != "CK" or value["edge"] != "posedge":
            raise ContractError(f"{cell} is not a direct positive-edge CK cell")
        for field in ("data_pins", "output_pins", "async_control_pins"):
            pins = value[field]
            if not isinstance(pins, list) or len(pins) != len(set(pins)):
                raise ContractError(f"{cell}.{field} must be a unique list")
            if any(not isinstance(pin, str) or IDENT.fullmatch(pin) is None for pin in pins):
                raise ContractError(f"{cell}.{field} has an invalid pin")
        if not value["data_pins"] or not value["output_pins"]:
            raise ContractError(f"{cell} needs data and output pins")
        if value["async_control_pins"]:
            raise ContractError(f"asynchronous controls are forbidden: {cell}")
        parsed[cell] = value
    combinational = document["combinational_cells"]
    if (not isinstance(combinational, list) or combinational != sorted(combinational)
            or len(combinational) != len(set(combinational))):
        raise ContractError("combinational cell list must be sorted and unique")
    comb_set = set(combinational)
    if any(not isinstance(cell, str) or IDENT.fullmatch(cell) is None for cell in comb_set):
        raise ContractError("invalid combinational cell name")
    if comb_set & set(parsed):
        raise ContractError("cell cannot be both sequential and combinational")
    return parsed, comb_set


def validate_binding(document: dict[str, Any]) -> set[str]:
    exact_keys(document, {"schema", "archive", "source_authority", "cohort",
                          "candidates", "evidence_class"}, "binding")
    if document["schema"] != BINDING_SCHEMA:
        raise ContractError("binding schema differs")
    if document["evidence_class"] != EVIDENCE_CLASS:
        raise ContractError("binding evidence class differs")
    archive = document["archive"]
    exact_keys(archive, {"path", "sha256", "size_bytes"}, "binding.archive")
    safe_relative(archive["path"], "binding.archive.path")
    if archive["sha256"] != CANONICAL_ARCHIVE_SHA256 or archive["size_bytes"] != 358400:
        raise ContractError("canonical archive identity differs")
    source = document["source_authority"]
    exact_keys(source, {"repository_commit", "integration_commit",
                        "source_cdc_contract_sha256", "physical_contract_sha256"},
               "binding.source_authority")
    expected_source = {
        "repository_commit": SOURCE_COMMIT,
        "integration_commit": INTEGRATION_COMMIT,
        "source_cdc_contract_sha256": SOURCE_CDC_CONTRACT_SHA256,
        "physical_contract_sha256": PHYSICAL_CONTRACT_SHA256,
    }
    if source != expected_source:
        raise ContractError("source authority differs")
    cohort = document["cohort"]
    exact_keys(cohort, {"member", "sha256", "document_sha256", "decision",
                        "same_environment_snapshot_sha256"}, "binding.cohort")
    if cohort["decision"] != MAXIMUM_DECISION:
        raise ContractError("cohort decision differs")
    required_members = {safe_relative(cohort["member"], "binding.cohort.member")}
    require_hex64(cohort["sha256"], "binding.cohort.sha256")
    require_hex64(cohort["document_sha256"], "binding.cohort.document_sha256")
    require_hex64(cohort["same_environment_snapshot_sha256"],
                  "binding.cohort.same_environment_snapshot_sha256")
    candidates = document["candidates"]
    exact_keys(candidates, {"a2", "a3"}, "binding.candidates")
    for design, candidate in candidates.items():
        exact_keys(candidate, {"top", "ledger", "qualification", "mapped_netlist",
                               "mapped_sdc", "postroute_netlist", "physical_reports",
                               "expected_metrics"},
                   f"binding.candidates.{design}")
        expected_top = ("a2_batched_iwrr_single_edge_top" if design == "a2"
                        else "a3_exact_scalar_prefix_k2_single_edge_top")
        if candidate["top"] != expected_top:
            raise ContractError(f"{design} top differs")
        for role in ("ledger", "mapped_sdc"):
            exact_keys(candidate[role], {"member", "sha256"}, f"{design}.{role}")
        exact_keys(candidate["qualification"], {"member", "sha256", "document_sha256"},
                   f"{design}.qualification")
        for role in ("mapped_netlist", "postroute_netlist"):
            exact_keys(candidate[role], {"member", "sha256", "instance_count",
                                         "sequential_count"}, f"{design}.{role}")
            if (not isinstance(candidate[role]["instance_count"], int)
                    or not isinstance(candidate[role]["sequential_count"], int)
                    or candidate[role]["sequential_count"] <= 0
                    or candidate[role]["instance_count"] < candidate[role]["sequential_count"]):
                raise ContractError(f"invalid counts in {design}.{role}")
        for role in ("ledger", "qualification", "mapped_netlist", "mapped_sdc",
                     "postroute_netlist"):
            entry = candidate[role]
            required_members.add(safe_relative(entry["member"], f"{design}.{role}.member"))
            require_hex64(entry["sha256"], f"{design}.{role}.sha256")
        require_hex64(candidate["qualification"]["document_sha256"],
                      f"{design}.qualification.document_sha256")
        reports = candidate["physical_reports"]
        report_roles = {"final_timing", "area", "power", "drc", "antenna",
                        "connectivity", "pg_connectivity", "check_timing"}
        exact_keys(reports, report_roles, f"{design}.physical_reports")
        for role, entry in reports.items():
            exact_keys(entry, {"member", "sha256"}, f"{design}.physical_reports.{role}")
            required_members.add(safe_relative(
                entry["member"], f"{design}.physical_reports.{role}.member"))
            require_hex64(entry["sha256"], f"{design}.physical_reports.{role}.sha256")
        metrics = candidate["expected_metrics"]
        metric_keys = {"setup_wns_ns", "hold_wns_ns", "area_um2",
                       "internal_power_mw", "switching_power_mw", "leakage_power_mw",
                       "total_power_mw"}
        exact_keys(metrics, metric_keys, f"{design}.expected_metrics")
        if any(type(metrics[key]) not in (int, float) for key in metric_keys):
            raise ContractError(f"{design} expected metric is not numeric")
    if len(required_members) != 27:
        raise ContractError("archive member names are not unique")
    return required_members


def read_archive(path: Path, expected_size: int, expected_sha: str,
                 required_members: set[str]) -> dict[str, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read archive: {exc}") from exc
    if len(raw) != expected_size or sha256(raw) != expected_sha:
        raise ContractError("archive byte identity differs")
    result: dict[str, bytes] = {}
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:") as archive:
            for member in archive.getmembers():
                name = safe_relative(member.name.rstrip("/"), "archive member")
                if name in seen:
                    raise ContractError(f"duplicate archive member: {name}")
                seen.add(name)
                if member.isdir():
                    if name not in {"a2", "a2/reports", "a3", "a3/reports", "cohort"}:
                        raise ContractError(f"unexpected archive directory: {name}")
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise ContractError(f"archive member is not a regular file: {name}")
                if name not in required_members:
                    raise ContractError(f"unexpected archive file: {name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ContractError(f"cannot read archive member: {name}")
                data = stream.read()
                if len(data) != member.size:
                    raise ContractError(f"short archive member: {name}")
                result[name] = data
    except (tarfile.TarError, OSError) as exc:
        raise ContractError(f"invalid archive: {exc}") from exc
    if set(result) != required_members:
        raise ContractError(
            f"archive file set differs: missing={sorted(required_members - set(result))} "
            f"extra={sorted(set(result) - required_members)}")
    return result


def strip_verilog_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def parse_ports(connection_text: str, where: str) -> dict[str, str]:
    pattern = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^()]*)\s*\)", re.S)
    ports: dict[str, str] = {}
    spans: list[tuple[int, int]] = []
    for match in pattern.finditer(connection_text):
        pin, expression = match.group(1), match.group(2).strip()
        if pin in ports:
            raise ContractError(f"duplicate port {pin} in {where}")
        if not expression:
            raise ContractError(f"empty port {pin} in {where}")
        ports[pin] = expression
        spans.append(match.span())
    residue_parts = []
    cursor = 0
    for start, end in spans:
        residue_parts.append(connection_text[cursor:start])
        cursor = end
    residue_parts.append(connection_text[cursor:])
    residue = "".join(residue_parts)
    if residue.replace(",", "").strip():
        raise ContractError(f"unsupported or positional port syntax in {where}")
    if not ports:
        raise ContractError(f"instance has no named ports in {where}")
    return ports


def analyze_netlist(data: bytes, top: str, sequential: dict[str, dict[str, Any]],
                    combinational: set[str], expected_instances: int,
                    expected_sequential: int, view: str) -> dict[str, Any]:
    try:
        clean = strip_verilog_comments(data.decode("utf-8"))
    except UnicodeError as exc:
        raise ContractError(f"{view} netlist is not UTF-8") from exc
    forbidden = re.search(r"\b(always|always_ff|always_latch|initial|generate|primitive)\b", clean)
    if forbidden:
        raise ContractError(f"behavioral or generated construct in {view}: {forbidden.group(1)}")
    modules = list(re.finditer(
        r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;(.*?)\bendmodule\b",
        clean, flags=re.S))
    if len(modules) != 1 or modules[0].group(1) != top:
        raise ContractError(f"{view} must contain exactly top module {top}")
    outside = clean[:modules[0].start()] + clean[modules[0].end():]
    if outside.strip():
        raise ContractError(f"unexpected text outside module in {view}")
    body = modules[0].group(3)
    declarations = {"input", "output", "inout", "wire", "tri", "supply0", "supply1",
                    "parameter", "localparam"}
    instances: list[tuple[str, str, dict[str, str]]] = []
    assigns = 0
    for raw_statement in body.split(";"):
        statement = raw_statement.strip()
        if not statement:
            continue
        first = statement.split(None, 1)[0]
        if first in declarations:
            continue
        if first == "assign":
            assigns += 1
            if TOKEN_CLK.search(statement):
                raise ContractError(f"primary clock is used in combinational assignment in {view}")
            continue
        match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_$]*)\s+((?:\\\S+)|(?:[A-Za-z_][A-Za-z0-9_$]*))\s*\((.*)\)",
            statement, flags=re.S)
        if match is None:
            raise ContractError(f"unparsed gate-level statement in {view}: {statement[:80]!r}")
        cell, name, connections = match.groups()
        ports = parse_ports(connections, f"{view}:{name}")
        instances.append((cell, name, ports))
    if len({name for _, name, _ in instances}) != len(instances):
        raise ContractError(f"duplicate instance name in {view}")
    if len(instances) != expected_instances:
        raise ContractError(
            f"{view} instance count differs: {len(instances)} != {expected_instances}")
    seq_counts: Counter[str] = Counter()
    cell_counts: Counter[str] = Counter()
    for cell, name, ports in instances:
        cell_counts[cell] += 1
        if cell in sequential:
            semantics = sequential[cell]
            seq_counts[cell] += 1
            allowed_ports = ({semantics["clock_pin"]} | set(semantics["data_pins"])
                             | set(semantics["output_pins"])
                             | set(semantics["async_control_pins"]))
            if set(ports) != allowed_ports:
                raise ContractError(
                    f"unknown/missing sequential port on {view}:{name}: "
                    f"actual={sorted(ports)} expected={sorted(allowed_ports)}")
            if semantics["edge"] != "posedge" or semantics["async_control_pins"]:
                raise ContractError(f"forbidden sequential semantics on {view}:{name}")
            clock = ports[semantics["clock_pin"]].replace(" ", "")
            if clock != "clk_i":
                raise ContractError(
                    f"generated/gated/forwarded/second clock on {view}:{name}: {clock}")
        elif cell not in combinational:
            raise ContractError(f"unknown or forbidden cell in {view}: {cell}")
        for pin, expression in ports.items():
            if TOKEN_CLK.search(expression):
                if cell not in sequential or pin != sequential[cell]["clock_pin"] \
                        or expression.replace(" ", "") != "clk_i":
                    raise ContractError(
                        f"primary clock used as data or combinational input on {view}:{name}.{pin}")
    if sum(seq_counts.values()) != expected_sequential:
        raise ContractError(
            f"{view} sequential count differs: {sum(seq_counts.values())} != {expected_sequential}")
    return {
        "top": top,
        "instance_count": len(instances),
        "sequential_count": sum(seq_counts.values()),
        "sequential_cells": dict(sorted(seq_counts.items())),
        "unique_cell_count": len(cell_counts),
        "continuous_assign_count": assigns,
        "all_sequential_clocks": "DIRECT_clk_i_POSEDGE",
        "asynchronous_control_count": 0,
        "unknown_cell_count": 0,
    }


def analyze_sdc(data: bytes, design: str) -> dict[str, Any]:
    try:
        text = re.sub(r"#[^\n]*", "", data.decode("utf-8"))
    except UnicodeError as exc:
        raise ContractError(f"{design} mapped SDC is not UTF-8") from exc
    commands = [line.strip() for line in text.splitlines() if line.strip()]
    create = [line for line in commands if re.match(r"^create_clock\b", line)]
    if len(create) != 1:
        raise ContractError(f"{design} SDC must create exactly one clock")
    normalized = re.sub(r"\s+", " ", create[0])
    if ("-name \"se_primary_clk\"" not in normalized
            or "-period 6.5" not in normalized
            or re.search(r"\[get_ports\s+clk_i\]", normalized) is None):
        raise ContractError(f"{design} SDC primary clock differs")
    forbidden = ("create_generated_clock", "set_clock_groups", "set_false_path",
                 "set_multicycle_path")
    for command in commands:
        if any(re.match(rf"^{name}\b", command) for name in forbidden):
            raise ContractError(f"forbidden clock exception in {design} SDC: {command}")
    return {
        "primary_clock": "se_primary_clk",
        "clock_port": "clk_i",
        "period_ns": 6.5,
        "generated_clock_count": 0,
        "clock_exception_count": 0,
    }


def parse_machine_receipt(data: bytes, where: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ContractError(f"{where} is not UTF-8") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            raise ContractError(f"malformed machine receipt line in {where}: {line!r}")
        key, value = line.split("=", 1)
        if key in result or not key:
            raise ContractError(f"duplicate/empty machine receipt key in {where}: {key!r}")
        result[key] = value
    return result


def report_text(data: bytes, where: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise ContractError(f"{where} is not UTF-8") from exc


def exact_float(actual: str, expected: float, where: str) -> float:
    try:
        value = float(actual)
    except ValueError as exc:
        raise ContractError(f"invalid numeric value in {where}: {actual!r}") from exc
    if abs(value - float(expected)) > 1e-12:
        raise ContractError(f"{where} differs: {value} != {expected}")
    return value


def analyze_physical_reports(design: str, candidate: dict[str, Any],
                             members: dict[str, bytes], ledger: dict[str, Any]) -> dict[str, Any]:
    reports = candidate["physical_reports"]
    role_map = {
        "final_timing": "final_timing_receipt",
        "area": "postroute_area",
        "power": "postroute_power",
        "drc": "drc",
        "antenna": "antenna",
        "connectivity": "connectivity",
        "pg_connectivity": "pg_connectivity",
        "check_timing": "check_timing",
    }
    payloads: dict[str, bytes] = {}
    for role, ledger_role in role_map.items():
        entry = reports[role]
        payload = members[entry["member"]]
        if sha256(payload) != entry["sha256"]:
            raise ContractError(f"{design} physical report hash differs: {role}")
        row = artifact_by_role(ledger, ledger_role, design)
        if row["sha256"] != entry["sha256"] or row["size_bytes"] != len(payload):
            raise ContractError(f"{design} physical report ledger binding differs: {role}")
        payloads[role] = payload

    expected = candidate["expected_metrics"]
    timing = parse_machine_receipt(payloads["final_timing"], f"{design} final timing")
    required_timing = {
        "schema", "setup_view", "setup_check", "setup_path_count",
        "setup_violation_count", "setup_wns", "setup_tns", "hold_view",
        "hold_check", "hold_path_count", "hold_violation_count", "hold_wns",
        "hold_tns", "final_hold_phase_receipt",
    }
    if set(timing) != required_timing:
        raise ContractError(f"{design} final timing receipt fields differ")
    if (timing["schema"] != "k2_single_edge_final_timing_receipt_v1"
            or timing["setup_view"] != "se_setup_view"
            or timing["setup_check"] != "setup"
            or timing["hold_view"] != "se_hold_view"
            or timing["hold_check"] != "hold"
            or timing["final_hold_phase_receipt"] != "eco_hold_final.machine"
            or int(timing["setup_path_count"]) <= 0
            or int(timing["hold_path_count"]) <= 0
            or int(timing["setup_violation_count"]) != 0
            or int(timing["hold_violation_count"]) != 0
            or float(timing["setup_tns"]) != 0.0
            or float(timing["hold_tns"]) != 0.0):
        raise ContractError(f"{design} final timing is not closed")
    setup_wns = exact_float(timing["setup_wns"], expected["setup_wns_ns"],
                            f"{design} setup WNS")
    hold_wns = exact_float(timing["hold_wns"], expected["hold_wns_ns"],
                           f"{design} hold WNS")
    if setup_wns < 0.0 or hold_wns < 0.0:
        raise ContractError(f"{design} final timing has negative slack")

    area_text = report_text(payloads["area"], f"{design} area")
    area_match = re.search(
        rf"^{re.escape(candidate['top'])}\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)\s*$",
        area_text, flags=re.M)
    if area_match is None:
        raise ContractError(f"{design} area row missing")
    if int(area_match.group(1)) != candidate["postroute_netlist"]["instance_count"]:
        raise ContractError(f"{design} area instance count differs")
    area = exact_float(area_match.group(2), expected["area_um2"], f"{design} area")

    power_text = report_text(payloads["power"], f"{design} power")
    power_patterns = {
        "internal_power_mw": r"^Total Internal Power:\s*([0-9.eE+-]+)",
        "switching_power_mw": r"^Total Switching Power:\s*([0-9.eE+-]+)",
        "leakage_power_mw": r"^Total Leakage Power:\s*([0-9.eE+-]+)",
        "total_power_mw": r"^Total Power:\s*([0-9.eE+-]+)",
    }
    power: dict[str, float] = {}
    for metric, pattern in power_patterns.items():
        match = re.search(pattern, power_text, flags=re.M)
        if match is None:
            raise ContractError(f"{design} power metric missing: {metric}")
        power[metric] = exact_float(match.group(1), expected[metric],
                                    f"{design} {metric}")
    if abs((power["internal_power_mw"] + power["switching_power_mw"]
            + power["leakage_power_mw"]) - power["total_power_mw"]) > 1e-8:
        raise ContractError(f"{design} power components do not sum to total")
    if "User-Defined Activity : N.A." not in power_text or "Primary Input Activity: 0.200000" not in power_text:
        raise ContractError(f"{design} power report is not the canonical vectorless profile")

    zero_markers = {
        "drc": "No DRC violations were found",
        "antenna": "No Violations Found",
        "connectivity": "Found no problems or warnings.",
        "pg_connectivity": "Found no problems or warnings.",
    }
    for role, marker in zero_markers.items():
        text = report_text(payloads[role], f"{design} {role}")
        if marker not in text:
            raise ContractError(f"{design} {role} zero-violation marker missing")
    check_timing = report_text(payloads["check_timing"], f"{design} check timing")
    if ("TIMING CHECK SUMMARY" not in check_timing
            or "se_primary_clk" not in check_timing
            or "kind=check_timing context=postroute" not in check_timing):
        raise ContractError(f"{design} post-route timing check context differs")
    return {
        "setup_wns_ns": setup_wns,
        "hold_wns_ns": hold_wns,
        "setup_violations": 0,
        "hold_violations": 0,
        "area_um2": area,
        **power,
        "drc_violations": 0,
        "antenna_violations": 0,
        "connectivity_problems": 0,
        "pg_connectivity_problems": 0,
        "power_method": "POSTROUTE_VECTORLESS_DEFAULT_ACTIVITY_0.2",
    }


def artifact_by_role(ledger: dict[str, Any], role: str, design: str) -> dict[str, Any]:
    artifacts = ledger.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractError(f"{design} ledger artifacts missing")
    rows = [row for row in artifacts if isinstance(row, dict) and row.get("role") == role]
    if len(rows) != 1:
        raise ContractError(f"{design} ledger role {role} is not unique")
    row = rows[0]
    for key in ("path", "sha256", "size_bytes", "producer_command_sha256"):
        if key not in row:
            raise ContractError(f"{design} ledger role {role} lacks {key}")
    return row


def verify_provenance(binding: dict[str, Any], members: dict[str, bytes]) -> dict[str, Any]:
    cohort_entry = binding["cohort"]
    cohort_data = members[cohort_entry["member"]]
    if sha256(cohort_data) != cohort_entry["sha256"]:
        raise ContractError("cohort file hash differs")
    cohort = load_json_bytes(cohort_data, "cohort")
    if (cohort.get("document_sha256") != cohort_entry["document_sha256"]
            or cohort.get("decision") != MAXIMUM_DECISION
            or cohort.get("producer_authenticated") is not False
            or cohort.get("comparison_ready") is not False
            or cohort.get("freshness_verified") is not False
            or cohort.get("diagnostic_same_environment_snapshot_sha256")
            != cohort_entry["same_environment_snapshot_sha256"]):
        raise ContractError("cohort trust boundary differs")
    rows = cohort.get("rows")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ContractError("cohort must have exactly two rows")
    row_by_design = {row.get("design"): row for row in rows if isinstance(row, dict)}
    if set(row_by_design) != {"a2", "a3"}:
        raise ContractError("cohort candidate rows differ")
    result: dict[str, Any] = {}
    for design, candidate in binding["candidates"].items():
        ledger_data = members[candidate["ledger"]["member"]]
        qualification_data = members[candidate["qualification"]["member"]]
        if sha256(ledger_data) != candidate["ledger"]["sha256"]:
            raise ContractError(f"{design} ledger file hash differs")
        if sha256(qualification_data) != candidate["qualification"]["sha256"]:
            raise ContractError(f"{design} qualification file hash differs")
        ledger = load_json_bytes(ledger_data, f"{design} ledger")
        qualification = load_json_bytes(qualification_data, f"{design} qualification")
        if (qualification.get("design") != design
                or qualification.get("artifact_ledger_sha256") != candidate["ledger"]["sha256"]
                or qualification.get("document_sha256")
                != candidate["qualification"]["document_sha256"]
                or qualification.get("contract_sha256") != PHYSICAL_CONTRACT_SHA256
                or qualification.get("decision") != MAXIMUM_DECISION
                or qualification.get("producer_authenticated") is not False
                or qualification.get("candidate_physical_go") is not False
                or qualification.get("diagnostic_artifact_checks_completed") is not True):
            raise ContractError(f"{design} qualification boundary differs")
        row = row_by_design[design]
        if (row.get("qualification_sha256") != candidate["qualification"]["sha256"]
                or row.get("artifact_ledger_sha256") != candidate["ledger"]["sha256"]
                or row.get("environment_receipt_sha256")
                != cohort_entry["same_environment_snapshot_sha256"]):
            raise ContractError(f"{design} cohort row differs")
        for binding_role, ledger_role in (("mapped_netlist", "mapped_netlist"),
                                          ("mapped_sdc", "mapped_sdc"),
                                          ("postroute_netlist", "postroute_netlist")):
            entry = candidate[binding_role]
            payload = members[entry["member"]]
            if sha256(payload) != entry["sha256"]:
                raise ContractError(f"{design} {binding_role} member hash differs")
            ledger_row = artifact_by_role(ledger, ledger_role, design)
            if (ledger_row["sha256"] != entry["sha256"]
                    or ledger_row["size_bytes"] != len(payload)):
                raise ContractError(f"{design} {binding_role} ledger binding differs")
        result[design] = {
            "qualification_sha256": candidate["qualification"]["sha256"],
            "artifact_ledger_sha256": candidate["ledger"]["sha256"],
            "environment_snapshot_sha256": row["environment_receipt_sha256"],
            "physical_metrics": analyze_physical_reports(
                design, candidate, members, ledger),
        }
    return result


def verify_repository_authority(root: Path, binding: dict[str, Any]) -> None:
    source_contract = root / "contracts/redred_single_edge_cdc_rdc/contract.json"
    physical_contract = root / "physical/k2_single_edge_endpoint/contract.json"
    try:
        if sha256(source_contract.read_bytes()) != SOURCE_CDC_CONTRACT_SHA256:
            raise ContractError("source CDC contract hash differs")
        if sha256(physical_contract.read_bytes()) != PHYSICAL_CONTRACT_SHA256:
            raise ContractError("physical contract hash differs")
    except OSError as exc:
        raise ContractError(f"cannot read repository authority: {exc}") from exc
    for commit in (SOURCE_COMMIT, INTEGRATION_COMMIT):
        process = subprocess.run(["git", "-C", str(root), "rev-parse", commit], text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if process.returncode or process.stdout.strip() != commit:
            raise ContractError(f"required Git commit is unavailable: {commit}")
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", SOURCE_COMMIT,
         INTEGRATION_COMMIT], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if ancestor.returncode:
        raise ContractError("source commit is not an ancestor of integration commit")
    source_verify = root / "contracts/redred_single_edge_cdc_rdc/verify_contract.py"
    process = subprocess.run([sys.executable, "-B", str(source_verify)], cwd=root, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if (process.returncode != 0
            or "REDRED_SINGLE_EDGE_CDC_RDC_PASS designs=a2,a3 domains=1" not in process.stdout):
        raise ContractError("current source-level CDC/RDC authority does not pass")
    if binding["source_authority"]["repository_commit"] != SOURCE_COMMIT:
        raise ContractError("binding source commit differs")


def verify(contract_path: Path, archive_override: Path | None = None) -> dict[str, Any]:
    contract_data, contract = load_file(contract_path, "contract")
    validate_contract(contract)
    contract_dir = contract_path.resolve().parent
    root = contract_dir.parents[1]
    binding_path = contract_dir / contract["binding"]["path"]
    semantics_path = contract_dir / contract["cell_semantics"]["path"]
    binding_data, binding = load_file(binding_path, "binding")
    semantics_data, semantics = load_file(semantics_path, "cell semantics")
    if sha256(binding_data) != CANONICAL_BINDING_SHA256:
        raise ContractError("binding document SHA-256 differs")
    if sha256(semantics_data) != CANONICAL_SEMANTICS_SHA256:
        raise ContractError("cell semantics SHA-256 differs")
    required_members = validate_binding(binding)
    sequential, combinational = validate_semantics(semantics)
    archive_entry = binding["archive"]
    archive_path = archive_override or (root / archive_entry["path"])
    members = read_archive(archive_path, archive_entry["size_bytes"],
                           archive_entry["sha256"], required_members)
    verify_repository_authority(root, binding)
    provenance = verify_provenance(binding, members)
    candidates: dict[str, Any] = {}
    for design, candidate in binding["candidates"].items():
        mapped = candidate["mapped_netlist"]
        postroute = candidate["postroute_netlist"]
        candidate_provenance = provenance[design]
        candidates[design] = {
            "mapped": analyze_netlist(
                members[mapped["member"]], candidate["top"], sequential, combinational,
                mapped["instance_count"], mapped["sequential_count"], f"{design}:mapped"),
            "postroute": analyze_netlist(
                members[postroute["member"]], candidate["top"], sequential, combinational,
                postroute["instance_count"], postroute["sequential_count"],
                f"{design}:postroute"),
            "sdc": analyze_sdc(members[candidate["mapped_sdc"]["member"]], design),
            "physical": candidate_provenance["physical_metrics"],
            "provenance": {
                key: value for key, value in candidate_provenance.items()
                if key != "physical_metrics"
            },
        }
    return {
        "schema": "redred-single-edge-mapped-cdc-rdc-receipt-v1",
        "status": "DIAGNOSTIC_PASS_RELEASE_HOLD",
        "decision": MAXIMUM_DECISION,
        "evidence_class": EVIDENCE_CLASS,
        "contract_sha256": sha256(contract_data),
        "archive_sha256": archive_entry["sha256"],
        "source_commit": SOURCE_COMMIT,
        "integration_commit": INTEGRATION_COMMIT,
        "source_cdc_status": "PASS",
        "mapped_cdc_rdc_diagnostic_status": "PASS",
        "producer_authenticated": False,
        "freshness_verified": False,
        "final_cdc_rdc_gate": "HOLD",
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=here / "contract.json")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = verify(args.contract, args.archive)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        print("REDRED_SINGLE_EDGE_MAPPED_CDC_RDC_DIAGNOSTIC_PASS_RELEASE_HOLD "
              "designs=a2,a3 views=mapped,postroute domains=1")
        return 0
    except (ContractError, OSError) as exc:
        receipt = {
            "schema": "redred-single-edge-mapped-cdc-rdc-receipt-v1",
            "status": "FAIL",
            "decision": MAXIMUM_DECISION,
            "diagnostic": str(exc),
        }
        print(json.dumps(receipt, indent=2, sort_keys=True))
        print(f"REDRED_SINGLE_EDGE_MAPPED_CDC_RDC_FAIL reason={exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
