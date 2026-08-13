#!/usr/bin/env python3
"""Immutable two-row Kanghee core-only Genus/Innovus cohort producer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Callable, Sequence


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_PATH = HERE / "contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SIMPLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BAD_LOG = re.compile(
    r"(?mi)^\s*(?:ERROR|FATAL)\s*[:\[]|\*\*(?:ERROR|FATAL):|"
    r"SEG(?:MENTATION)?\s+FAULT|INTERRUPT|K2_CORE_(?:GENUS|INNOVUS)_FATAL"
)
UNSAFE_TCL_PATH = re.compile(r"[\s{}\[\]$;\\\"]")


class CohortError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path, *, require_single_link: bool = True) -> bytes:
    path = path.resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CohortError(f"input is not a regular file: {path}")
        if require_single_link and before.st_nlink != 1:
            raise CohortError(f"input is not a single-link immutable file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        first = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        second = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if first != second:
            raise CohortError(f"input changed while read: {path}")
        payload = b"".join(chunks)
        if not payload:
            raise CohortError(f"input is empty: {path}")
        return payload
    finally:
        os.close(descriptor)


def write_exclusive(path: Path, payload: bytes, mode: int = 0o444) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CohortError(f"invalid {label} JSON: {error}") from error
    if not isinstance(value, dict):
        raise CohortError(f"{label} must be a JSON object")
    return value


def load_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    payload = stable_read(path)
    return payload, load_json_bytes(payload, label)


def seal(document: dict[str, Any]) -> dict[str, Any]:
    if "document_sha256" in document:
        raise CohortError("cannot seal a document that already has document_sha256")
    result = dict(document)
    result["document_sha256"] = sha256(canonical(document))
    return result


def verify_seal(document: dict[str, Any], label: str) -> None:
    recorded = document.get("document_sha256")
    unsigned = dict(document)
    unsigned.pop("document_sha256", None)
    if not isinstance(recorded, str) or not SHA256.fullmatch(recorded) or \
            sha256(canonical(unsigned)) != recorded:
        raise CohortError(f"{label} self-hash mismatch")


def relative_file(identity: dict[str, Any], label: str) -> tuple[Path, bytes]:
    if set(identity) != {"path", "sha256"} or not SHA256.fullmatch(
            str(identity.get("sha256", ""))):
        raise CohortError(f"{label} identity is malformed")
    raw = identity["path"]
    if not isinstance(raw, str) or not raw or raw.startswith("/"):
        raise CohortError(f"{label} path must be repository-relative")
    path = (ROOT / raw).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as error:
        raise CohortError(f"{label} path escapes the repository") from error
    payload = stable_read(path)
    if sha256(payload) != identity["sha256"]:
        raise CohortError(f"{label} SHA-256 mismatch: {raw}")
    return path, payload


def validate_contract() -> tuple[bytes, dict[str, Any]]:
    payload, contract = load_json(CONTRACT_PATH, "cohort contract")
    if contract.get("schema") != "k2_core_physical_cohort_contract_v1" or \
            contract.get("status") != "READY_FOR_EXPLICIT_SERVER_EXECUTION_ONLY":
        raise CohortError("cohort contract schema/status mismatch")
    order = contract.get("candidate_order")
    if order != ["fovea", "cluster2"] or \
            set(contract.get("candidates", {})) != set(order):
        raise CohortError("cohort must contain exactly ordered fovea and cluster2 rows")
    common = contract.get("common_conditions", {})
    exact_common = {
        "period_ns": "5.0", "uncertainty_ns": "0.25",
        "input_delay_ns": "0.5", "output_delay_ns": "0.5",
        "output_load_pf": "0.01", "aspect_ratio": "1.0",
        "core_utilization": "0.35", "core_margin_um": "10",
    }
    observed = {
        "period_ns": common.get("clock", {}).get("period_ns"),
        "uncertainty_ns": common.get("clock", {}).get("uncertainty_ns"),
        "input_delay_ns": common.get("io", {}).get("input_delay_ns"),
        "output_delay_ns": common.get("io", {}).get("output_delay_ns"),
        "output_load_pf": common.get("io", {}).get("output_load_pf"),
        "aspect_ratio": common.get("physical", {}).get("aspect_ratio"),
        "core_utilization": common.get("physical", {}).get("core_utilization"),
        "core_margin_um": common.get("physical", {}).get("core_margin_um"),
    }
    if observed != exact_common or common.get("boundary") != \
            "raw_core_only_no_wrapper_no_endpoint":
        raise CohortError("common timing/load/floorplan contract mismatch")
    if common.get("physical", {}).get("site_normalization") != \
            "REPLACE_BUFX2_WITH_BUFX4_AND_DONT_USE_BUFX2":
        raise CohortError("common placement-site normalization mismatch")
    if common.get("synthesis") != {
            "clock_gating_insertion": True, "scan_mapping": False}:
        raise CohortError("common synthesis policy mismatch")
    if common.get("power") != {
            "mode": "VECTORLESS_DISCLOSED_SCREENING_ONLY",
            "ranking_or_signoff_eligible": False}:
        raise CohortError("power disclosure policy mismatch")

    for label, identity in contract.get("authorities", {}).items():
        relative_file(identity, f"authority {label}")
    for label, identity in contract.get("flow_templates", {}).items():
        relative_file(identity, f"flow template {label}")

    server = load_json_bytes(relative_file(
        contract["authorities"]["server_environment_contract"],
        "server environment authority")[1], "server environment authority")
    innovus = load_json_bytes(relative_file(
        contract["authorities"]["innovus_environment"],
        "Innovus environment authority")[1], "Innovus environment authority")
    if server.get("schema") != "k2_w2_server_env_contract_v1" or \
            innovus.get("schema") != "k2_w2_innovus_server_environment_v2":
        raise CohortError("upstream environment authority schema mismatch")
    if contract["technology"]["pdk_root"] != server["server_pdk_root"]:
        raise CohortError("PDK root differs from server authority")
    role_map = {
        "setup_liberty": "setup_liberty", "hold_liberty": "hold_liberty",
        "tech_lef": "tech_lef", "macro_lef": "macro_lef",
        "shared_qrc": "setup_qrc",
    }
    for local, upstream in role_map.items():
        row = contract["technology"][local]
        source = server["technology"][upstream]
        if row["relative_path"] != source["relative_path"] or \
                row["sha256"] != source["sha256"]:
            raise CohortError(f"technology role differs from server authority: {local}")
    hold_qrc = server["technology"]["hold_qrc"]
    if contract["technology"]["shared_qrc"]["sha256"] != hold_qrc["sha256"]:
        raise CohortError("setup/hold QRC is not the same authority byte")
    for tool in ("genus", "innovus"):
        local = contract["tools"][tool]
        upstream = server["tools"][tool]
        expected = {
            "path": upstream["observed_path"],
            "resolved_path": upstream["resolved_path"],
            "sha256": upstream["sha256"],
            "version": upstream["version"],
        }
        if local != expected:
            raise CohortError(f"{tool} identity differs from server authority")
    policy = innovus["physical_policy"]
    physical = common["physical"]
    pairs = {
        "process_node_nm": "process_node_nm", "aspect_ratio": "aspect_ratio",
        "core_utilization": "core_utilization", "core_margin_um": "core_margin_um",
        "vdd_net": "vdd_net", "vss_net": "vss_net",
    }
    if any(physical[left] != policy[right] for left, right in pairs.items()):
        raise CohortError("physical policy differs from current Innovus authority")
    ring = physical["ring"]
    if ring != {
            "horizontal_layer": policy["ring_horizontal_layer"],
            "vertical_layer": policy["ring_vertical_layer"],
            "width_um": policy["ring_width_um"],
            "spacing_um": policy["ring_spacing_um"],
            "offset_um": policy["ring_offset_um"]}:
        raise CohortError("ring policy differs from current Innovus authority")
    return payload, contract


def archive_members(contract: dict[str, Any], archive_path: Path) -> tuple[bytes, dict[str, bytes]]:
    archive_payload = stable_read(archive_path)
    expected_archive = contract["source_archive"]["sha256"]
    if sha256(archive_payload) != expected_archive:
        raise CohortError("raw source archive SHA-256 mismatch")
    expected: dict[str, str] = {}
    for candidate in contract["candidate_order"]:
        row = contract["candidates"][candidate]
        if not SIMPLE_NAME.fullmatch(row.get("top", "")) or \
                row.get("clock_port") != "clk":
            raise CohortError(f"invalid core boundary: {candidate}")
        for source in row.get("sources", []):
            name, digest = source.get("member"), source.get("sha256")
            if not isinstance(name, str) or name.startswith("/") or ".." in Path(name).parts or \
                    not SHA256.fullmatch(str(digest or "")):
                raise CohortError(f"malformed source member: {candidate}")
            if name in expected and expected[name] != digest:
                raise CohortError(f"conflicting shared source identity: {name}")
            expected[name] = digest
    found: dict[str, list[tarfile.TarInfo]] = {name: [] for name in expected}
    with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            if member.name in found:
                found[member.name].append(member)
        payloads: dict[str, bytes] = {}
        for name, matches in found.items():
            if len(matches) != 1 or not matches[0].isfile():
                raise CohortError(f"source member missing/duplicate/non-file: {name}")
            stream = bundle.extractfile(matches[0])
            if stream is None:
                raise CohortError(f"cannot read source member: {name}")
            member_payload = stream.read()
            if sha256(member_payload) != expected[name]:
                raise CohortError(f"source member SHA-256 mismatch: {name}")
            payloads[name] = member_payload
    for candidate in contract["candidate_order"]:
        top = contract["candidates"][candidate]["top"]
        combined = b"\n".join(payloads[row["member"]]
                              for row in contract["candidates"][candidate]["sources"])
        modules = re.findall(rb"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_]*)\b", combined)
        if modules.count(top.encode()) != 1:
            raise CohortError(f"source closure does not define top exactly once: {candidate}")
    return archive_payload, payloads


def condition_sha(contract: dict[str, Any]) -> str:
    return sha256(canonical(contract["common_conditions"]))


def create_plan(archive_path: Path, output: Path) -> Path:
    contract_payload, contract = validate_contract()
    archive_payload, _ = archive_members(contract, archive_path)
    rows = []
    for candidate in contract["candidate_order"]:
        row = contract["candidates"][candidate]
        rows.append({
            "candidate": candidate,
            "top": row["top"],
            "clock_port": row["clock_port"],
            "reset": row["reset"],
            "ports": row["ports"],
            "sources": row["sources"],
        })
    document = seal({
        "schema": "k2_core_physical_cohort_plan_v1",
        "state": "PLANNED_NOT_EXECUTED",
        "contract": {"path": str(CONTRACT_PATH), "sha256": sha256(contract_payload)},
        "source_archive": {"path": str(archive_path.resolve()),
                           "sha256": sha256(archive_payload)},
        "authority_identities": contract["authorities"],
        "flow_template_identities": contract["flow_templates"],
        "common_conditions": contract["common_conditions"],
        "common_condition_sha256": condition_sha(contract),
        "technology": contract["technology"],
        "tools": contract["tools"],
        "rows": rows,
        "execution_policy": contract["execution_policy"],
    })
    write_exclusive(output.resolve(), canonical(document))
    return output.resolve()


def validate_plan(path: Path) -> tuple[bytes, dict[str, Any], dict[str, Any], dict[str, bytes]]:
    plan_payload, plan = load_json(path, "cohort plan")
    verify_seal(plan, "cohort plan")
    contract_payload, contract = validate_contract()
    if plan.get("schema") != "k2_core_physical_cohort_plan_v1" or \
            plan.get("state") != "PLANNED_NOT_EXECUTED" or \
            plan.get("contract", {}).get("sha256") != sha256(contract_payload) or \
            plan.get("common_condition_sha256") != condition_sha(contract) or \
            plan.get("common_conditions") != contract["common_conditions"] or \
            plan.get("technology") != contract["technology"] or \
            plan.get("tools") != contract["tools"] or \
            plan.get("authority_identities") != contract["authorities"] or \
            plan.get("flow_template_identities") != contract["flow_templates"]:
        raise CohortError("plan differs from current immutable contract")
    expected_rows = [{
        "candidate": name,
        "top": contract["candidates"][name]["top"],
        "clock_port": contract["candidates"][name]["clock_port"],
        "reset": contract["candidates"][name]["reset"],
        "ports": contract["candidates"][name]["ports"],
        "sources": contract["candidates"][name]["sources"],
    } for name in contract["candidate_order"]]
    if plan.get("rows") != expected_rows:
        raise CohortError("plan rows are not the exact two-row contract")
    archive = Path(plan.get("source_archive", {}).get("path", ""))
    archive_payload, members = archive_members(contract, archive)
    if plan["source_archive"].get("sha256") != sha256(archive_payload):
        raise CohortError("plan source archive identity mismatch")
    return plan_payload, plan, contract, members


def verify_server_receipt(path: Path, contract: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    preflight_path = ROOT / "physical/k2_w2_server_env/preflight.py"
    spec = importlib.util.spec_from_file_location("k2_core_server_preflight", preflight_path)
    if spec is None or spec.loader is None:
        raise CohortError("cannot load server environment verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload, receipt = load_json(path, "server GO receipt")
    authority_payload = relative_file(
        contract["authorities"]["server_environment_contract"],
        "server environment authority")[1]
    try:
        module.verify_go_document(receipt, sha256(authority_payload))
    except module.PreflightError as error:
        raise CohortError(f"server environment receipt is not GO: {error}") from error
    gates = receipt.get("gates", {})
    tools = gates.get("tool_executables", {}).get("evidence", {})
    for name in ("genus", "innovus"):
        expected = contract["tools"][name]
        observed = tools.get(name, {})
        if (observed.get("path") != expected["path"] or
                observed.get("resolved_path") != expected["resolved_path"] or
                observed.get("sha256") != expected["sha256"] or
                observed.get("parsed_version") != expected["version"]):
            raise CohortError(f"GO receipt does not prove exact {name} identity")
    evidence = gates.get("technology_files", {}).get("evidence", {})
    role_map = {
        "setup_liberty": "setup_liberty", "hold_liberty": "hold_liberty",
        "tech_lef": "tech_lef", "macro_lef": "macro_lef",
        "setup_qrc": "shared_qrc", "hold_qrc": "shared_qrc",
    }
    for upstream, local in role_map.items():
        expected = contract["technology"][local]
        observed = evidence.get(upstream, {})
        exact_path = str(Path(contract["technology"]["pdk_root"]) /
                         expected["relative_path"])
        if observed.get("path") != exact_path or observed.get("sha256") != expected["sha256"]:
            raise CohortError(f"GO receipt does not prove technology role {upstream}")
    return payload, receipt


def sdc_payload(contract: dict[str, Any]) -> bytes:
    clock = contract["common_conditions"]["clock"]
    io_contract = contract["common_conditions"]["io"]
    text = (
        f"create_clock -name {clock['name']} -period {clock['period_ns']} "
        f"-waveform {{{clock['waveform_ns'][0]} {clock['waveform_ns'][1]}}} "
        "[get_ports clk]\n"
        f"set_clock_uncertainty {clock['uncertainty_ns']} [get_clocks {clock['name']}]\n"
        f"set_input_delay -clock {clock['name']} {io_contract['input_delay_ns']} "
        "[remove_from_collection [all_inputs] [get_ports clk]]\n"
        f"set_output_delay -clock {clock['name']} {io_contract['output_delay_ns']} "
        "[all_outputs]\n"
        f"set_load {io_contract['output_load_pf']} [all_outputs]\n"
    )
    return text.encode("utf-8")


def safe_runtime_path(path: Path, label: str) -> None:
    if UNSAFE_TCL_PATH.search(str(path)):
        raise CohortError(f"{label} contains Tcl-list-unsafe characters: {path}")


def prepare_run(plan_path: Path, receipt_path: Path, output_root: Path,
                verifier: Callable[[Path, dict[str, Any]], tuple[bytes, dict[str, Any]]] =
                verify_server_receipt) -> Path:
    plan_payload, plan, contract, members = validate_plan(plan_path)
    server_payload, server = verifier(receipt_path, contract)
    output_root = output_root.resolve()
    safe_runtime_path(output_root, "output root")
    if output_root.exists():
        raise CohortError(f"prepared output already exists: {output_root}")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(output_root, 0o755)
    bundle = output_root / "bundle"
    os.mkdir(bundle, 0o755)
    write_exclusive(bundle / "plan.json", plan_payload)
    write_exclusive(bundle / "contract.json", stable_read(CONTRACT_PATH))
    write_exclusive(bundle / "server_environment_go.json", server_payload)
    for name, identity in contract["flow_templates"].items():
        source, payload = relative_file(identity, f"flow template {name}")
        write_exclusive(bundle / source.name, payload)

    common_sdc = sdc_payload(contract)
    descriptors: dict[str, dict[str, Any]] = {}
    for candidate in contract["candidate_order"]:
        row_root = output_root / candidate
        input_root = row_root / "input"
        source_root = input_root / "sources"
        source_root.mkdir(parents=True)
        row = contract["candidates"][candidate]
        staged_sources = []
        for index, identity in enumerate(row["sources"]):
            source_path = source_root / f"{index:02d}_{Path(identity['member']).name}"
            write_exclusive(source_path, members[identity["member"]])
            staged_sources.append({
                "archive_member": identity["member"],
                "path": str(source_path),
                "sha256": identity["sha256"],
            })
        sdc_path = input_root / "common_5ns.sdc"
        write_exclusive(sdc_path, common_sdc)
        filelist_path = input_root / "sources.f"
        filelist = "".join(f"{item['path']}\n" for item in staged_sources).encode()
        write_exclusive(filelist_path, filelist)
        descriptor = seal({
            "schema": "k2_core_physical_execution_descriptor_v1",
            "state": "PREPARED_NOT_EXECUTED",
            "candidate": candidate,
            "top": row["top"],
            "plan_sha256": sha256(plan_payload),
            "server_environment_receipt_sha256": sha256(server_payload),
            "environment_binding_sha256": server["environment_binding_sha256"],
            "common_condition_sha256": condition_sha(contract),
            "source_archive_sha256": contract["source_archive"]["sha256"],
            "sources": staged_sources,
            "filelist": {"path": str(filelist_path), "sha256": sha256(filelist)},
            "sdc": {"path": str(sdc_path), "sha256": sha256(common_sdc)},
            "technology": contract["technology"],
            "tools": contract["tools"],
            "flow_templates": contract["flow_templates"],
            "power_mode": "VECTORLESS_DISCLOSED_SCREENING_ONLY",
        })
        descriptor_path = input_root / "execution_descriptor.json"
        descriptor_payload = canonical(descriptor)
        write_exclusive(descriptor_path, descriptor_payload)
        descriptors[candidate] = {
            "path": str(descriptor_path), "sha256": sha256(descriptor_payload)}

    receipt = seal({
        "schema": "k2_core_physical_preparation_receipt_v1",
        "state": "PREPARED_NOT_EXECUTED",
        "plan": {"path": str(plan_path.resolve()), "sha256": sha256(plan_payload)},
        "server_environment_receipt": {
            "path": str(receipt_path.resolve()), "sha256": sha256(server_payload),
            "environment_binding_sha256": server["environment_binding_sha256"],
        },
        "common_condition_sha256": condition_sha(contract),
        "candidate_order": contract["candidate_order"],
        "descriptors": descriptors,
    })
    receipt_output = output_root / "PREPARATION_RECEIPT.json"
    write_exclusive(receipt_output, canonical(receipt))
    return receipt_output


def validate_prepared(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    payload, receipt = load_json(root / "PREPARATION_RECEIPT.json", "preparation receipt")
    verify_seal(receipt, "preparation receipt")
    if receipt.get("schema") != "k2_core_physical_preparation_receipt_v1" or \
            receipt.get("state") != "PREPARED_NOT_EXECUTED" or \
            receipt.get("candidate_order") != ["fovea", "cluster2"]:
        raise CohortError("preparation receipt contract mismatch")
    plan_path = Path(receipt["plan"]["path"])
    plan_payload, plan, contract, _ = validate_plan(plan_path)
    if sha256(plan_payload) != receipt["plan"]["sha256"] or \
            receipt["common_condition_sha256"] != condition_sha(contract):
        raise CohortError("preparation receipt plan/condition binding mismatch")
    copied = stable_read(root / "bundle/plan.json")
    if copied != plan_payload:
        raise CohortError("prepared plan snapshot mismatch")
    if stable_read(root / "bundle/contract.json") != stable_read(CONTRACT_PATH):
        raise CohortError("prepared contract snapshot mismatch")
    go_snapshot = stable_read(root / "bundle/server_environment_go.json")
    if sha256(go_snapshot) != receipt["server_environment_receipt"]["sha256"]:
        raise CohortError("prepared server environment receipt snapshot mismatch")
    for name, identity in contract["flow_templates"].items():
        source = Path(identity["path"])
        if sha256(stable_read(root / "bundle" / source.name)) != identity["sha256"]:
            raise CohortError(f"prepared flow template mutated: {name}")
    for candidate in contract["candidate_order"]:
        descriptor_identity = receipt["descriptors"].get(candidate, {})
        descriptor_path = Path(descriptor_identity.get("path", ""))
        expected_descriptor_path = root / candidate / "input/execution_descriptor.json"
        if descriptor_path != expected_descriptor_path:
            raise CohortError(f"prepared descriptor path mismatch: {candidate}")
        descriptor_payload, descriptor = load_json(descriptor_path, "execution descriptor")
        verify_seal(descriptor, "execution descriptor")
        if (sha256(descriptor_payload) != descriptor_identity.get("sha256") or
                descriptor.get("candidate") != candidate or
                descriptor.get("top") != contract["candidates"][candidate]["top"] or
                descriptor.get("common_condition_sha256") != condition_sha(contract) or
                descriptor.get("plan_sha256") != sha256(plan_payload) or
                descriptor.get("technology") != contract["technology"] or
                descriptor.get("tools") != contract["tools"] or
                descriptor.get("flow_templates") != contract["flow_templates"] or
                descriptor.get("power_mode") != "VECTORLESS_DISCLOSED_SCREENING_ONLY" or
                descriptor.get("source_archive_sha256") !=
                contract["source_archive"]["sha256"] or
                descriptor.get("server_environment_receipt_sha256") !=
                receipt["server_environment_receipt"]["sha256"] or
                descriptor.get("environment_binding_sha256") !=
                receipt["server_environment_receipt"]["environment_binding_sha256"]):
            raise CohortError(f"prepared descriptor binding mismatch: {candidate}")
        expected_sources = contract["candidates"][candidate]["sources"]
        if len(descriptor.get("sources", [])) != len(expected_sources):
            raise CohortError(f"prepared source count mismatch: {candidate}")
        # Server Python is 3.8; zip(strict=...) is only available from 3.10.
        # The exact length equality above provides the same fail-closed guard.
        for index, (source, expected) in enumerate(
                zip(descriptor["sources"], expected_sources)):
            expected_path = root / candidate / "input/sources" / \
                f"{index:02d}_{Path(expected['member']).name}"
            if source != {
                    "archive_member": expected["member"],
                    "path": str(expected_path),
                    "sha256": expected["sha256"]}:
                raise CohortError(f"prepared source identity mismatch: {candidate}")
            if sha256(stable_read(Path(source["path"]))) != source["sha256"]:
                raise CohortError(f"prepared source mutated: {candidate}")
        expected_sdc = root / candidate / "input/common_5ns.sdc"
        if descriptor.get("sdc") != {
                "path": str(expected_sdc), "sha256": sha256(sdc_payload(contract))} or \
                stable_read(expected_sdc) != sdc_payload(contract):
            raise CohortError(f"prepared SDC mutated: {candidate}")
        expected_filelist_path = root / candidate / "input/sources.f"
        if descriptor.get("filelist", {}).get("path") != str(expected_filelist_path):
            raise CohortError(f"prepared filelist path mismatch: {candidate}")
        filelist = stable_read(expected_filelist_path)
        expected_filelist = "".join(
            f"{source['path']}\n" for source in descriptor["sources"]).encode()
        if filelist != expected_filelist or sha256(filelist) != \
                descriptor["filelist"]["sha256"]:
            raise CohortError(f"prepared filelist mutated: {candidate}")
    return receipt, plan, contract


def live_identity(path: Path, expected_resolved: str, expected_sha: str, label: str) -> None:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CohortError(f"missing live {label}: {path}") from error
    if str(resolved) != expected_resolved or sha256(stable_read(resolved)) != expected_sha or \
            not os.access(resolved, os.X_OK):
        raise CohortError(f"live {label} identity mismatch")


def validate_live_technology(contract: dict[str, Any]) -> dict[str, Path]:
    root = Path(contract["technology"]["pdk_root"])
    paths: dict[str, Path] = {}
    for role in ("setup_liberty", "hold_liberty", "tech_lef", "macro_lef", "shared_qrc"):
        identity = contract["technology"][role]
        path = root / identity["relative_path"]
        if sha256(stable_read(path)) != identity["sha256"]:
            raise CohortError(f"live technology SHA mismatch: {role}")
        paths[role] = path
    return paths


def artifact_inventory(root: Path, relative_names: Sequence[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for name in relative_names:
        payload = stable_read(root / name)
        result[name] = {"sha256": sha256(payload), "size_bytes": len(payload)}
    return result


def clean_genus(log: str, top: str, version: str) -> None:
    if f"K2_CORE_GENUS_COMMANDS_COMPLETE top={top}" not in log or \
            f"Version: {version}" not in log or "Normal exit." not in log or \
            not re.search(r"Info=\d+, Warn=\d+, Error=0, Fatal=0", log) or \
            BAD_LOG.search(log):
        raise CohortError("Genus log lacks exact native completion/zero-error evidence")


def timing_machine(path: Path, expected_view: str, expected_check: str) -> dict[str, Any]:
    rows = {}
    for line in stable_read(path).decode("utf-8").splitlines():
        if line.count("=") != 1:
            raise CohortError(f"malformed timing machine summary: {path}")
        key, value = line.split("=", 1)
        if key in rows:
            raise CohortError(f"duplicate timing machine field: {path}")
        rows[key] = value
    if set(rows) != {"schema", "view", "check", "path_count", "violation_count", "wns", "tns"} or \
            rows["schema"] != "k2_core_timing_summary_v1" or \
            rows["view"] != expected_view or rows["check"] != expected_check:
        raise CohortError(f"timing machine contract mismatch: {path}")
    try:
        count, violations = int(rows["path_count"]), int(rows["violation_count"])
        wns, tns = float(rows["wns"]), float(rows["tns"])
    except ValueError as error:
        raise CohortError(f"timing machine numeric mismatch: {path}") from error
    if count <= 0 or violations != 0 or wns < 0 or tns != 0 or \
            not math.isfinite(wns) or not math.isfinite(tns):
        raise CohortError(f"timing is not clean: {path}")
    return {"path_count": count, "violation_count": violations, "wns": wns, "tns": tns}


def floorplan_machine(path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, str] = {}
    for line in stable_read(path).decode("utf-8").splitlines():
        if line.count("=") != 1:
            raise CohortError(f"malformed floorplan machine receipt: {path}")
        key, value = line.split("=", 1)
        if key in rows or not value:
            raise CohortError(f"duplicate/empty floorplan machine field: {path}")
        rows[key] = value
    expected_keys = {"schema", "aspect_ratio", "core_utilization", "core_margin_um",
                     "site", "row_count", "row_sites", "core_bbox"}
    physical = contract["common_conditions"]["physical"]
    try:
        count = int(rows.get("row_count", ""))
    except ValueError as error:
        raise CohortError(f"invalid floorplan row count: {path}") from error
    if (set(rows) != expected_keys or rows["schema"] != "k2_core_floorplan_receipt_v1" or
            rows["aspect_ratio"] != physical["aspect_ratio"] or
            rows["core_utilization"] != physical["core_utilization"] or
            rows["core_margin_um"] != physical["core_margin_um"] or
            rows["site"] != physical["site"] or rows["row_sites"] != physical["site"] or
            count <= 0):
        raise CohortError(f"floorplan machine receipt differs from common contract: {path}")
    return {"aspect_ratio": rows["aspect_ratio"],
            "core_utilization": rows["core_utilization"],
            "core_margin_um": rows["core_margin_um"], "site": rows["site"],
            "row_count": count, "core_bbox": rows["core_bbox"]}


def require_zero_report(path: Path, kind: str) -> None:
    text = stable_read(path).decode("utf-8", errors="strict")
    patterns = {
        "drc": (r"No DRC violations were found", r"(?:DRC\s+)?violations?\s*[:=]\s*0"),
        "antenna": (r"No Violations Found", r"antenna\s+violations?\s*[:=]\s*0"),
        "connectivity": (r"Found no problems or warnings\.",
                         r"connectivity\s+(?:violations?|errors?)\s*[:=]\s*0"),
    }[kind]
    if not any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
        raise CohortError(f"{kind} report lacks native zero-count evidence: {path}")
    nonzero = re.search(
        rf"(?i){kind}[^\n]*(?:violations?|errors?)\s*[:=]\s*([1-9][0-9]*)", text)
    if nonzero:
        raise CohortError(f"{kind} report contains nonzero violations: {path}")


def require_clean_check_timing(path: Path) -> None:
    text = stable_read(path).decode("utf-8", errors="strict")
    if "TIMING CHECK SUMMARY" not in text or "TIMING CHECK DETAIL" not in text:
        raise CohortError("check_timing report lacks native summary/detail")
    blockers = ("no_clock", "no_input_delay", "no_output_delay", "unconstrained",
                "no_drive", "no_load")
    for name in blockers:
        counts = [int(value) for value in re.findall(
            rf"(?i)\|\s*{name}\s*\|[^\n|]*\|\s*([0-9]+)\s*\|", text)]
        if any(counts):
            raise CohortError(f"check_timing contains {name} endpoints")


def clean_innovus(log: str, version: str) -> None:
    versions = re.findall(r"(?m)^Version:\s+v([^,\s]+)", log)
    summaries = [int(value) for value in re.findall(
        r"(?m)^\*\*\* Message Summary:\s+\d+ warning\(s\),\s+(\d+) error\(s\)\s*$", log)]
    if versions != [version] or not summaries or any(summaries) or BAD_LOG.search(log) or \
            not re.search(r'(?m)^--- Ending "Innovus" \([^\n]*\) ---\s*\Z', log):
        raise CohortError("Innovus log lacks exact native completion/zero-error evidence")


def execute_stage(root: Path, candidate: str, stage: str, authorization: str) -> Path:
    receipt, _, contract = validate_prepared(root.resolve())
    expected_auth = f"I_UNDERSTAND_THIS_LAUNCHES_{stage.upper()}"
    if authorization != expected_auth:
        raise CohortError(f"explicit execution authorization must equal {expected_auth}")
    if candidate not in contract["candidate_order"] or stage not in {"genus", "innovus"}:
        raise CohortError("unknown candidate or EDA stage")
    descriptor_path = Path(receipt["descriptors"][candidate]["path"])
    _, descriptor = load_json(descriptor_path, "execution descriptor")
    verify_seal(descriptor, "execution descriptor")
    go_payload, go_document = verify_server_receipt(
        root / "bundle/server_environment_go.json", contract)
    if (sha256(go_payload) != receipt["server_environment_receipt"]["sha256"] or
            go_document["environment_binding_sha256"] !=
            receipt["server_environment_receipt"]["environment_binding_sha256"]):
        raise CohortError("prepared GO receipt no longer verifies")
    row_root = root / candidate
    technology = validate_live_technology(contract)
    tool = contract["tools"][stage]
    live_identity(Path(tool["path"]), tool["resolved_path"], tool["sha256"], stage)
    output = row_root / stage
    if output.exists():
        raise CohortError(f"no-overwrite output already exists: {output}")
    os.mkdir(output, 0o755)
    work = output / "work"
    temporary = output / "tmp"
    os.mkdir(work, 0o755)
    os.mkdir(temporary, 0o755)
    log_path = output / "tool.log"
    env = dict(os.environ)
    env["TMPDIR"] = str(temporary)
    env["CORE_TOP"] = descriptor["top"]
    if stage == "genus":
        env.update({
            "CORE_SOURCES": " ".join(item["path"] for item in descriptor["sources"]),
            "CORE_SDC": descriptor["sdc"]["path"],
            "CORE_SETUP_LIB": str(technology["setup_liberty"]),
            "CORE_GENUS_OUT": str(output),
        })
        template = root / "bundle/genus_core.tcl"
        command = [tool["path"], "-batch", "-files", str(template)]
    else:
        genus_receipt_path = row_root / "genus/EXECUTION_RECEIPT.json"
        validate_execution_receipt(genus_receipt_path, root, candidate, "genus")
        genus_output = row_root / "genus"
        mapped_netlist = genus_output / "netlist" / f"{descriptor['top']}.mapped.v"
        mapped_sdc = genus_output / "netlist" / f"{descriptor['top']}.mapped.sdc"
        env.update({
            "CORE_MAPPED_NETLIST": str(mapped_netlist),
            "CORE_MAPPED_SDC": str(mapped_sdc),
            "CORE_SETUP_LIB": str(technology["setup_liberty"]),
            "CORE_HOLD_LIB": str(technology["hold_liberty"]),
            "CORE_SHARED_QRC": str(technology["shared_qrc"]),
            "CORE_TECH_LEF": str(technology["tech_lef"]),
            "CORE_MACRO_LEF": str(technology["macro_lef"]),
            "CORE_MMMC": str(root / "bundle/innovus_mmmc_core.tcl"),
            "CORE_INNOVUS_OUT": str(output),
            "CORE_SITE": contract["common_conditions"]["physical"]["site"],
            "CORE_PROCESS": contract["common_conditions"]["physical"]["process_node_nm"],
            "CORE_ASPECT": contract["common_conditions"]["physical"]["aspect_ratio"],
            "CORE_UTIL": contract["common_conditions"]["physical"]["core_utilization"],
            "CORE_MARGIN": contract["common_conditions"]["physical"]["core_margin_um"],
            "CORE_VDD": contract["common_conditions"]["physical"]["vdd_net"],
            "CORE_VSS": contract["common_conditions"]["physical"]["vss_net"],
            "CORE_RING_H": contract["common_conditions"]["physical"]["ring"]["horizontal_layer"],
            "CORE_RING_V": contract["common_conditions"]["physical"]["ring"]["vertical_layer"],
            "CORE_RING_WIDTH": contract["common_conditions"]["physical"]["ring"]["width_um"],
            "CORE_RING_SPACING": contract["common_conditions"]["physical"]["ring"]["spacing_um"],
            "CORE_RING_OFFSET": contract["common_conditions"]["physical"]["ring"]["offset_um"],
        })
        template = root / "bundle/innovus_core.tcl"
        command = [tool["path"], "-no_gui", "-files", str(template)]
    with open(log_path, "xb") as handle:
        result = subprocess.run(command, cwd=work, env=env, stdout=handle,
                                stderr=subprocess.STDOUT, check=False)
        handle.flush()
        os.fsync(handle.fileno())
    if result.returncode != 0:
        raise CohortError(f"{stage} exited nonzero ({result.returncode}); no receipt written")
    log = stable_read(log_path).decode("utf-8", errors="replace")
    if stage == "genus":
        clean_genus(log, descriptor["top"], tool["version"])
        names = [
            "tool.log", "reports/area.rpt", "reports/timing.rpt",
            "reports/power_vectorless.rpt", "reports/qor.rpt",
            "reports/timing_intent.rpt", "reports/clocks.rpt",
            f"netlist/{descriptor['top']}.mapped.v",
            f"netlist/{descriptor['top']}.mapped.sdc",
            f"netlist/{descriptor['top']}.mapped.sdf",
        ]
        metrics: dict[str, Any] = {"power_mode": "VECTORLESS_DISCLOSED_SCREENING_ONLY"}
    else:
        clean_innovus(log, tool["version"])
        marker = stable_read(output / "status/COMMANDS_COMPLETE").decode().strip()
        if marker != f"K2_CORE_INNOVUS_COMMANDS_COMPLETE top={descriptor['top']}" or \
                (output / "status/COMMANDS_FAILED").exists():
            raise CohortError("Innovus Tcl completion marker mismatch")
        setup = timing_machine(output / "reports/setup_timing.machine", "core_setup_view", "setup")
        hold = timing_machine(output / "reports/hold_timing.machine", "core_hold_view", "hold")
        floorplan = floorplan_machine(output / "reports/floorplan.machine", contract)
        require_zero_report(output / "reports/drc.rpt", "drc")
        require_zero_report(output / "reports/antenna.rpt", "antenna")
        require_zero_report(output / "reports/connectivity.rpt", "connectivity")
        require_zero_report(output / "reports/pg_connectivity.rpt", "connectivity")
        require_clean_check_timing(output / "reports/check_timing.rpt")
        names = [
            "tool.log", "status/COMMANDS_COMPLETE", "reports/area.rpt",
            "reports/power_vectorless.rpt", "reports/congestion.rpt",
            "reports/floorplan.machine",
            "reports/setup_timing.rpt", "reports/setup_timing.machine",
            "reports/hold_timing.rpt", "reports/hold_timing.machine",
            "reports/check_timing.rpt", "reports/check_design_pre_place.rpt",
            "reports/check_design_post_route.rpt", "reports/check_place.rpt",
            "reports/connectivity.rpt", "reports/pg_connectivity.rpt",
            "reports/drc.rpt", "reports/antenna.rpt", "reports/route.rpt",
            f"netlist/{descriptor['top']}.postroute.v",
            f"netlist/{descriptor['top']}.postroute.sdf",
            f"netlist/{descriptor['top']}.postroute.spef",
        ]
        database = output / "database"
        if not database.is_dir() or not any(database.iterdir()):
            raise CohortError("Innovus database output is missing/empty")
        metrics = {"setup": setup, "hold": hold, "floorplan": floorplan,
                   "power_mode": "VECTORLESS_DISCLOSED_SCREENING_ONLY"}
    artifacts = artifact_inventory(output, names)
    execution = seal({
        "schema": "k2_core_physical_execution_receipt_v1",
        "state": "NATIVE_TOOL_COMPLETED_AND_VERIFIED",
        "stage": stage,
        "candidate": candidate,
        "top": descriptor["top"],
        "prepared_receipt_sha256": sha256(stable_read(root / "PREPARATION_RECEIPT.json")),
        "descriptor_sha256": sha256(stable_read(descriptor_path)),
        "common_condition_sha256": condition_sha(contract),
        "tool": tool,
        "source_archive_sha256": contract["source_archive"]["sha256"],
        "power_disclosure": "VECTORLESS_DISCLOSED_SCREENING_ONLY_NOT_SIGNOFF",
        "metrics": metrics,
        "artifacts": artifacts,
    })
    receipt_path = output / "EXECUTION_RECEIPT.json"
    write_exclusive(receipt_path, canonical(execution))
    return receipt_path


def validate_execution_receipt(path: Path, root: Path, candidate: str,
                               stage: str) -> dict[str, Any]:
    payload, document = load_json(path, f"{stage} execution receipt")
    verify_seal(document, f"{stage} execution receipt")
    _, _, contract = validate_prepared(root)
    if (document.get("schema") != "k2_core_physical_execution_receipt_v1" or
            document.get("state") != "NATIVE_TOOL_COMPLETED_AND_VERIFIED" or
            document.get("candidate") != candidate or document.get("stage") != stage or
            document.get("top") != contract["candidates"][candidate]["top"] or
            document.get("common_condition_sha256") != condition_sha(contract) or
            document.get("tool") != contract["tools"][stage] or
            document.get("power_disclosure") !=
            "VECTORLESS_DISCLOSED_SCREENING_ONLY_NOT_SIGNOFF"):
        raise CohortError(f"{stage} execution receipt binding mismatch")
    output = path.parent
    for name, identity in document.get("artifacts", {}).items():
        artifact = stable_read(output / name)
        if sha256(artifact) != identity.get("sha256") or len(artifact) != identity.get("size_bytes"):
            raise CohortError(f"artifact changed after {stage} receipt: {name}")
    log = stable_read(output / "tool.log").decode("utf-8", errors="replace")
    if stage == "genus":
        clean_genus(log, document["top"], contract["tools"]["genus"]["version"])
    else:
        clean_innovus(log, contract["tools"]["innovus"]["version"])
        marker = stable_read(output / "status/COMMANDS_COMPLETE").decode().strip()
        if marker != f"K2_CORE_INNOVUS_COMMANDS_COMPLETE top={document['top']}" or \
                (output / "status/COMMANDS_FAILED").exists():
            raise CohortError("retained Innovus completion marker mismatch")
        setup = timing_machine(
            output / "reports/setup_timing.machine", "core_setup_view", "setup")
        hold = timing_machine(
            output / "reports/hold_timing.machine", "core_hold_view", "hold")
        floorplan = floorplan_machine(output / "reports/floorplan.machine", contract)
        if document.get("metrics", {}).get("setup") != setup or \
                document.get("metrics", {}).get("hold") != hold or \
                document.get("metrics", {}).get("floorplan") != floorplan:
            raise CohortError("retained Innovus timing metrics/receipt mismatch")
        for report, kind in (
                ("drc.rpt", "drc"), ("antenna.rpt", "antenna"),
                ("connectivity.rpt", "connectivity"),
                ("pg_connectivity.rpt", "connectivity")):
            require_zero_report(output / "reports" / report, kind)
        require_clean_check_timing(output / "reports/check_timing.rpt")
    return document


def seal_cohort(root: Path, output: Path) -> Path:
    _, _, contract = validate_prepared(root.resolve())
    executions = {}
    for candidate in contract["candidate_order"]:
        for stage in ("genus", "innovus"):
            receipt_path = root / candidate / stage / "EXECUTION_RECEIPT.json"
            document = validate_execution_receipt(receipt_path, root, candidate, stage)
            executions[f"{candidate}:{stage}"] = {
                "path": str(receipt_path),
                "sha256": sha256(stable_read(receipt_path)),
                "state": document["state"],
            }
    final = seal({
        "schema": "k2_core_physical_cohort_receipt_v1",
        "state": "EXACT_TWO_ROW_COHORT_COMPLETE",
        "candidate_order": contract["candidate_order"],
        "common_condition_sha256": condition_sha(contract),
        "source_archive_sha256": contract["source_archive"]["sha256"],
        "power_disclosure": "VECTORLESS_DISCLOSED_SCREENING_ONLY_NOT_SIGNOFF",
        "executions": executions,
    })
    write_exclusive(output.resolve(), canonical(final))
    return output.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="write an immutable no-EDA cohort plan")
    plan.add_argument("--source-archive", type=Path, default=None)
    plan.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="prepare exclusive server run directories")
    prepare.add_argument("--plan", type=Path, required=True)
    prepare.add_argument("--server-environment-receipt", type=Path, required=True)
    prepare.add_argument("--output-root", type=Path, required=True)
    execute = commands.add_parser("execute", help="explicitly launch one native EDA stage")
    execute.add_argument("--prepared-root", type=Path, required=True)
    execute.add_argument("--candidate", choices=("fovea", "cluster2"), required=True)
    execute.add_argument("--stage", choices=("genus", "innovus"), required=True)
    execute.add_argument("--authorization", required=True)
    final = commands.add_parser("seal", help="verify all four executions and seal cohort")
    final.add_argument("--prepared-root", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            _, contract = validate_contract()
            archive = args.source_archive or Path(contract["source_archive"]["default_path"])
            result = create_plan(archive, args.output)
            print(f"K2_CORE_COHORT_PLAN_READY path={result}")
        elif args.command == "prepare":
            result = prepare_run(args.plan, args.server_environment_receipt, args.output_root)
            print(f"K2_CORE_COHORT_PREPARED_NOT_EXECUTED receipt={result}")
        elif args.command == "execute":
            result = execute_stage(args.prepared_root, args.candidate, args.stage,
                                   args.authorization)
            print(f"K2_CORE_COHORT_NATIVE_STAGE_VERIFIED receipt={result}")
        else:
            result = seal_cohort(args.prepared_root, args.output)
            print(f"K2_CORE_COHORT_COMPLETE receipt={result}")
    except (CohortError, OSError, tarfile.TarError) as error:
        print(f"K2_CORE_COHORT_FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
