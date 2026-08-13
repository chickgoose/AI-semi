#!/usr/bin/env python3
"""Fail-closed plan/descriptor gate for tech-staged W2 Innovus runs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "k2_physical_innovus_cohorts.json"
AUTHORITY_PATH = HERE / "k2_physical_server_environment.json"
GENERIC_RUNNER = HERE / "run_k2_physical_innovus.sh"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MODULE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.MULTILINE)
DECLARATION = re.compile(
    r"^\s*(input|output|inout)\s+(?:(?:wire|logic|reg)\s+)?"
    r"(?:signed\s+)?(\[[^\]]+\])?\s*([^;]+);", re.MULTILINE,
)
GET_PORTS = re.compile(r"\[\s*get_ports\s+(?:\{([^}]*)\}|([^\]]+))\]")
INSTANCE = re.compile(
    r"\bDFFNSRX1\s+(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;",
    re.DOTALL,
)
CONNECTION = re.compile(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^()]*?)\s*\)")
TECH_CELLS = ("TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1")
CELL_INSTANCE = re.compile(
    rf"\b({'|'.join(TECH_CELLS)})\s+(\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
    r"\s*\((.*?)\)\s*;", re.DOTALL,
)
ALL_CELL_INSTANCE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_$]*)\s+"
    r"(\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;", re.DOTALL,
)


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    design: str
    top: str
    cohort: str
    period_ns: str
    netlist: Path
    netlist_sha256: str
    sdc: Path
    sdc_sha256: str
    output_dir: Path
    producer_path: Path
    producer_sha256: str
    handoff_path: Path
    handoff_sha256: str
    endpoint_map_path: Path
    endpoint_map_sha256: str
    mapped_functional_path: Path
    mapped_functional_sha256: str
    staged_manifest_path: Path
    staged_manifest_sha256: str
    environment_path: Path
    environment_sha256: str
    activity_path: Path
    activity_sha256: str
    activity_format: str
    activity_scope: str
    activity_window_start_ns: str
    activity_window_end_ns: str


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path) -> bytes:
    if not path.is_absolute():
        raise PlanError(f"input path is not absolute: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PlanError(f"cannot open immutable input {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PlanError(f"input is not a regular single-link file: {path}")
        chunks = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if identity(before) != identity(after):
            raise PlanError(f"input changed while read: {path}")
        payload = b"".join(chunks)
        if not payload:
            raise PlanError(f"input is empty: {path}")
        return payload
    finally:
        os.close(descriptor)


def load_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlanError(f"invalid {label} JSON: {error}") from error
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be a JSON object")
    return value


def bound_payload(document: Any, label: str) -> tuple[Path, str, bytes]:
    if not isinstance(document, dict) or set(document) != {"path", "sha256"}:
        raise PlanError(f"{label} must contain exactly path and sha256")
    if not isinstance(document["sha256"], str) or not SHA256.fullmatch(document["sha256"]):
        raise PlanError(f"{label} SHA-256 is malformed")
    path = Path(document["path"])
    payload = stable_read(path)
    if sha256(payload) != document["sha256"]:
        raise PlanError(f"{label} SHA-256 mismatch: {path}")
    return path, document["sha256"], payload


def tracked_json(path: Path, schema: str) -> dict[str, Any]:
    value = load_json(path.read_bytes(), path.name)
    if value.get("schema") != schema:
        raise PlanError(f"{path.name} schema mismatch")
    return value


def load_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    registry = tracked_json(REGISTRY_PATH, "k2_w2_innovus_cohort_registry_v3")
    authority = tracked_json(AUTHORITY_PATH, "k2_w2_innovus_server_environment_v2")
    if registry.get("server_environment_authority") != \
            "scripts/ppa/k2_physical_server_environment.json":
        raise PlanError("server environment authority path mismatch")
    cohort = registry.get("cohorts", {}).get("tech_staged_complete_compositions")
    if set(registry.get("cohorts", {})) != {"tech_staged_complete_compositions"}:
        raise PlanError("final registry must contain only the tech-staged cohort")
    pointer = registry.get("committed_techmap_manifest", {})
    if registry.get("integration_state") != "ready" or set(pointer) != {
            "source_repository_commit", "publication_repository_commit", "path",
            "sha256"} or any(not isinstance(pointer.get(key), str) or
                             not re.fullmatch(r"[0-9a-f]{40}", pointer[key])
                             for key in ("source_repository_commit",
                                         "publication_repository_commit")) or \
            pointer.get("path") != \
            "rtl/technology/physical_staging/physical_staging_manifest.json" or \
            not isinstance(pointer.get("sha256"), str) or \
            not SHA256.fullmatch(pointer["sha256"]):
        raise PlanError("launch blocked until one committed techmap manifest is hash-bound")
    tops = {row["top"] for row in cohort["designs"].values()}
    if tops & set(registry["forbidden_final_tops"]):
        raise PlanError("tech-staged cohort contains a forbidden generic/debug top")
    for row in authority["constraint_templates"].values():
        path = HERE.parents[1] / row["path"]
        if sha256(path.read_bytes()) != row["sha256"]:
            raise PlanError(f"tracked constraint template changed: {row['path']}")
    mmmc = authority.get("mmmc_template", {})
    if set(mmmc) != {"path", "sha256"} or \
            not SHA256.fullmatch(str(mmmc.get("sha256", ""))) or \
            sha256((HERE.parents[1] / mmmc["path"]).read_bytes()) != mmmc["sha256"]:
        raise PlanError("tracked producer-aligned MMMC template changed")
    return registry, authority


def staged_common_ports(cohort: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize the final manifest's literal mixed-width common port order."""
    nonlink = cohort["common_ports"]
    before_link = nonlink[:5]
    after_link = nonlink[5:]
    widths = {name: row["link_pins"][1]["width"]
              for name, row in cohort["designs"].items()}
    return before_link + [
        {"direction": "output", "name": "link_clk_o", "width": 1},
        {"direction": "output", "name": "link_data_o", "width_by_design": widths},
    ] + after_link


def staged_port_signature(ports: list[dict[str, Any]], design: str) -> list[str]:
    result = []
    for port in ports:
        width = port.get("width", port.get("width_by_design", {}).get(design))
        if not isinstance(width, int) or width <= 0:
            raise PlanError("tech-staged manifest contains an invalid port width")
        result.append(port["name"] if width == 1 else f"{port['name']}[{width - 1}:0]")
    return result


def port_width(token: str | None) -> int:
    if token is None:
        return 1
    match = re.fullmatch(r"\[\s*([0-9]+)\s*:\s*([0-9]+)\s*\]", token)
    if not match:
        raise PlanError(f"nonconstant mapped-netlist port range: {token}")
    high, low = map(int, match.groups())
    return abs(high - low) + 1


def mapped_ports(text: str) -> dict[str, tuple[str, int]]:
    ports: dict[str, tuple[str, int]] = {}
    for direction, width_token, names in DECLARATION.findall(text):
        width = port_width(width_token or None)
        for raw in names.split(","):
            name = raw.strip().split("=", 1)[0].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) or name in ports:
                raise PlanError(f"invalid/duplicate mapped-netlist port: {name}")
            ports[name] = (direction, width)
    return ports


def compact_net(value: str) -> str:
    """Use the same logical spelling for pins while accepting Verilog spacing."""
    compact = "".join(value.split())
    if compact in {"0", "1'b0", "1'h0", "1'd0"}:
        return "1'b0"
    if compact in {"1", "1'b1", "1'h1", "1'd1"}:
        return "1'b1"
    return compact


def flattened_endpoint_records(top: str, body: str, root: str,
                               prefixes: dict[str, str]) -> tuple[
                                   list[dict[str, Any]], dict[str, int]]:
    """Recreate the canonical flattened records emitted by the Genus producer."""
    records = []
    whole = {cell: 0 for cell in TECH_CELLS}
    role_prefixes = set(prefixes.values())
    for cell, raw_name, body_text in CELL_INSTANCE.findall(body):
        name = raw_name.lstrip("\\")
        pins = dict(CONNECTION.findall(body_text))
        whole[cell] += 1
        present_roles = [prefix for prefix in role_prefixes if prefix in name]
        if not present_roles:
            continue
        if root not in name:
            raise PlanError(f"endpoint leaf escaped preserved root: {cell} {name}")
        if present_roles != [prefixes[cell]]:
            raise PlanError(f"endpoint leaf role/cell prefix mismatch: {cell} {name}")
        records.append({
            "hierarchy": f"{top}.{name}",
            "mapped_instance": name,
            "cell_type": cell,
            "pin_bindings": dict(sorted(pins.items())),
            "provenance_root": root,
        })
    return records, whole


def mapped_module_bodies(text: str) -> dict[str, str]:
    rows = re.findall(
        r"(?ms)(?:^|\n)\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b.*?;"
        r"(.*?)\bendmodule\b", text)
    modules = {name: body for name, body in rows}
    if len(modules) != len(rows):
        raise PlanError("mapped netlist contains duplicate module definitions")
    return modules


def recursive_technology_counts(top: str, modules: dict[str, str],
                                active: tuple[str, ...] = ()) -> dict[str, int]:
    if top not in modules:
        raise PlanError(f"mapped hierarchy root missing: {top}")
    if top in active:
        raise PlanError(f"recursive mapped hierarchy at {top}")
    counts = {cell: 0 for cell in TECH_CELLS}
    for kind, _, _ in ALL_CELL_INSTANCE.findall(modules[top]):
        if kind in counts:
            counts[kind] += 1
        elif kind in modules:
            child = recursive_technology_counts(kind, modules, active + (top,))
            for cell, count in child.items():
                counts[cell] += count
    return counts


def verify_endpoint_pin_contract(body: str, records: list[dict[str, Any]],
                                 link_width: int) -> None:
    required_pins = {
        "TLATNTSCAX2": {"CK", "E", "SE", "ECK"},
        "MX2X1": {"A", "B", "S0", "Y"},
        "DFFRHQX1": {"RN", "CK", "D", "Q"},
        "DFFNSRX1": {"CKN", "D", "RN", "SN", "Q"},
    }
    optional_pins = {"DFFNSRX1": {"QN"}}
    by_cell = {cell: [] for cell in TECH_CELLS}
    for record in records:
        pins = record["pin_bindings"]
        cell = record["cell_type"]
        if (not required_pins[cell].issubset(pins) or
                set(pins) - required_pins[cell] - optional_pins.get(cell, set())):
            raise PlanError(f"endpoint {cell} exact pin set mismatch")
        by_cell[cell].append({pin: compact_net(net) for pin, net in pins.items()})

    icg = by_cell["TLATNTSCAX2"]
    if len(icg) != 1:
        raise PlanError("endpoint TLATNTSCAX2 exact count mismatch")
    gate = icg[0]
    if (gate["CK"] != "sample_clk_i" or gate["ECK"] != "link_clk_o" or
            gate["SE"] != "1'b0" or not gate["E"] or gate["E"] == "1'b0"):
        raise PlanError("endpoint TLATNTSCAX2 exact pin binding mismatch")
    drivers = []
    for kind, _, cell_body in ALL_CELL_INSTANCE.findall(body):
        pins = {pin: compact_net(net) for pin, net in CONNECTION.findall(cell_body)}
        if pins.get("Y") == gate["E"]:
            drivers.append((kind, pins))
    if len(drivers) != 1 or not drivers[0][0].startswith("AND2"):
        raise PlanError("endpoint ICG enable driver mismatch")
    fanin = {drivers[0][1].get("A", ""), drivers[0][1].get("B", "")}
    if "rst_n" not in fanin or not any("frame_active" in net for net in fanin):
        raise PlanError("endpoint ICG enable fanin mismatch")

    all_instances = [
        (kind, {pin: compact_net(net)
                for pin, net in CONNECTION.findall(cell_body)})
        for kind, _, cell_body in ALL_CELL_INSTANCE.findall(body)
    ]

    def buffered_output(net: str) -> str:
        current = compact_net(net)
        for _ in range(8):
            if re.fullmatch(r"link_data_o\[\d+\]", current):
                return current
            forward = [pins for kind, pins in all_instances
                       if re.fullmatch(r"(?:CLK)?BUF[A-Za-z0-9_$]*", kind)
                       and pins.get("A") == current and pins.get("Y")]
            if len(forward) != 1:
                raise PlanError("endpoint MX2X1 output lineage mismatch")
            current = forward[0]["Y"]
        raise PlanError("endpoint MX2X1 output lineage is too deep")

    link_bits = {f"link_data_o[{index}]" for index in range(link_width)}
    mux_outputs = []
    for pins in by_cell["MX2X1"]:
        if pins["S0"] != "ref_clk_i" or not pins["A"] or not pins["B"]:
            raise PlanError("endpoint MX2X1 exact pin binding mismatch")
        mux_outputs.append(buffered_output(pins["Y"]))
    if set(mux_outputs) != link_bits or len(mux_outputs) != len(link_bits):
        raise PlanError("endpoint MX2X1 outputs do not exactly cover link data")

    for pins in by_cell["DFFRHQX1"]:
        if (pins["CK"] != "link_clk_o" or pins["RN"] != "rst_n" or
                not pins["D"] or not pins["Q"]):
            raise PlanError("endpoint DFFRHQX1 exact pin binding mismatch")

    for pins in by_cell["DFFNSRX1"]:
        if (pins["CKN"] != "link_clk_o" or pins["RN"] != "rst_n" or
                pins["SN"] != "1'b1" or not pins["D"] or not pins["Q"]):
            raise PlanError("endpoint DFFNSRX1 exact pin binding mismatch")


def validate_netlist(payload: bytes, top: str, contract: dict[str, Any],
                     common_ports: list[dict[str, Any]],
                     endpoint_contract: dict[str, Any],
                     endpoint_map: dict[str, Any]) -> dict[str, int]:
    try:
        text = payload.decode()
    except UnicodeDecodeError as error:
        raise PlanError("mapped netlist is not UTF-8") from error
    if top not in MODULE.findall(text):
        raise PlanError(f"mapped netlist does not define exact top {top}")
    body = re.search(rf"\bmodule\s+{re.escape(top)}\b.*?\bendmodule\b", text, re.DOTALL)
    if body is None:
        raise PlanError(f"cannot isolate mapped top module {top}")
    ports = mapped_ports(body.group(0))
    exact_ports = common_ports + contract["link_pins"]
    expected_ports = {row["name"]: (row["direction"], row["width"])
                      for row in exact_ports}
    if ports != expected_ports:
        extras = sorted(set(ports) - set(expected_ports))
        missing = sorted(set(expected_ports) - set(ports))
        raise PlanError(f"mapped netlist exact canonical port signature mismatch; "
                        f"extra={extras} missing={missing}")
    forbidden_aliases = {"load_i", "pending_i", "source_ready_o", "protocol_fault_o"}
    if forbidden_aliases & set(ports):
        raise PlanError("mapped netlist contains a forbidden final-top port alias")
    for row in exact_ports:
        expected = (row["direction"], row["width"])
        if ports.get(row["name"]) != expected:
            raise PlanError(f"mapped netlist port mismatch for {top}.{row['name']}")
    if re.search(r"\b(?:SDFF\w*|SCAN\w*)\b", text, re.IGNORECASE):
        raise PlanError("mapped netlist contains forbidden scan/SDFF cells")
    expected_counts = endpoint_contract.get("leaf_counts", {})
    prefixes = endpoint_contract.get("preserved_name_prefixes", {})
    if set(expected_counts) != set(TECH_CELLS) or set(prefixes) != set(TECH_CELLS) or \
            any(not isinstance(prefix, str) or not prefix for prefix in prefixes.values()) or \
            len(set(prefixes.values())) != len(TECH_CELLS) or \
            any(not isinstance(value, int) or value <= 0
                              for value in expected_counts.values()):
        raise PlanError("endpoint leaf inventory contract mismatch")
    root = contract.get("endpoint_root", {}).get("stable_prefix")
    endpoint_roots = endpoint_contract.get("endpoint_link_roots")
    if (not isinstance(root, str) or not root or
            not isinstance(endpoint_roots, list) or len(endpoint_roots) != 2 or
            any(not isinstance(value, str) or not value for value in endpoint_roots)):
        raise PlanError("endpoint root contract mismatch")
    records, _ = flattened_endpoint_records(
        top, body.group(0), root, prefixes)
    records.sort(key=lambda row: (row["hierarchy"], row["cell_type"]))
    whole = recursive_technology_counts(top, mapped_module_bodies(text))
    endpoint_counts = {cell: 0 for cell in TECH_CELLS}
    for row in records:
        endpoint_counts[row["cell_type"]] += 1
    if endpoint_counts != expected_counts:
        raise PlanError("preserved endpoint leaf counts differ from exact contract")
    verify_endpoint_pin_contract(
        body.group(0), records, contract["link_pins"][1]["width"])
    expected_map_fields = {
        "schema", "design", "top", "mapped_netlist_sha256",
        "endpoint_link_roots", "preserved_name_prefixes", "leaf_counts",
        "no_other_negedge_state_proven", "instances",
    }
    if set(endpoint_map) != expected_map_fields or \
            endpoint_map.get("schema") != "k2_w2_endpoint_connectivity_map_v1" or \
            endpoint_map.get("top") != top or \
            endpoint_map.get("mapped_netlist_sha256") != sha256(payload) or \
            endpoint_map.get("endpoint_link_roots") != endpoint_roots or \
            sorted(endpoint_map.get("instances", []),
                   key=lambda row: (row.get("hierarchy", ""),
                                    row.get("cell_type", ""))) != records or \
            endpoint_map.get("leaf_counts") != expected_counts or \
            endpoint_map.get("preserved_name_prefixes") != prefixes:
        raise PlanError("Genus canonical endpoint provenance/connectivity map mismatch")
    no_other_negedge = endpoint_contract.get("no_other_negedge_state_proven") is True
    if endpoint_map.get("no_other_negedge_state_proven") is not no_other_negedge:
        raise PlanError("endpoint negedge provenance claim mismatch")
    if no_other_negedge and whole["DFFNSRX1"] != expected_counts["DFFNSRX1"]:
        raise PlanError("whole-top DFFNS total violates proven no-other-negedge claim")
    if any(whole[cell] < expected_counts[cell] for cell in TECH_CELLS):
        raise PlanError("whole-top inventory cannot undercount endpoint leaves")
    return whole


def sdc_tokens(text: str) -> set[str]:
    result = set()
    for braced, plain in GET_PORTS.findall(text):
        result.update(token.strip('"') for token in (braced or plain).split())
    return result


def command_tokens(text: str, command: str) -> set[str]:
    result = set()
    for row in re.split(r"[;\n]", text.replace("\\\n", " ")):
        if re.search(rf"\b{re.escape(command)}\b", row):
            result.update(sdc_tokens(row))
    return result


def covered(tokens: set[str], name: str, width: int) -> bool:
    return name in tokens or name + "*" in tokens or name + "[*]" in tokens or \
        (width > 1 and all(f"{name}[{index}]" in tokens for index in range(width)))


def validate_sdc(payload: bytes, top: str, contract: dict[str, Any], period: str) -> None:
    try:
        text = payload.decode()
    except UnicodeDecodeError as error:
        raise PlanError("mapped SDC is not UTF-8") from error
    if re.search(r"\b(?:load_i|pending_i|source_ready_o|protocol_fault_o)\b", text):
        raise PlanError("mapped SDC contains a forbidden final-top port alias")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", period) or float(period) <= 0:
        raise PlanError("period_ns must be a canonical positive decimal")
    current = re.findall(r"^\s*current_design\s+\{?([A-Za-z_]\w*)\}?\s*$", text, re.MULTILINE)
    if current != [top]:
        raise PlanError("mapped SDC current_design/top mismatch")
    for port in contract["clocks"]["input"]:
        if not covered(command_tokens(text, "create_clock"), port, 1):
            raise PlanError(f"mapped SDC missing input clock {port}")
    for port in contract["clocks"]["generated"]:
        if not covered(command_tokens(text, "create_generated_clock"), port, 1):
            raise PlanError(f"mapped SDC missing generated clock {port}")
    for pin in contract["link_pins"]:
        if pin["name"] not in contract["clocks"]["generated"] and not covered(
                command_tokens(text, "set_output_delay"), pin["name"], pin["width"]):
            raise PlanError(f"mapped SDC does not constrain link pin {pin['name']}")
    periods = re.findall(r"\bcreate_clock\b[^\n;]*?-period\s+([0-9.]+)", text)
    if not periods or any(float(value) != float(period) for value in periods):
        raise PlanError("mapped SDC create_clock period mismatch")
    if "set_load" not in text or "set_input_transition" not in text or \
            "set_clock_gating_check" not in text or "-clock_fall" not in text:
        raise PlanError("mapped SDC lost load/driver/gating/DDR constraint classes")
    if re.search(r"set_false_path[^\n]*(?:rst_n|RN)", text):
        raise PlanError("mapped SDC false-paths reset recovery/removal")


def validate_staged_manifest(bound: dict[str, Any], registry: dict[str, Any],
                             authority: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    path, digest, payload = bound_payload(bound, "tech-staged manifest")
    manifest = load_json(payload, "tech-staged manifest")
    contract = registry["staged_manifest_contract"]
    if set(manifest) != {"schema", "status", "repository_commit", "goal_order",
                        "common_ports", "technology_authorities",
                        "constraint_templates", "designs", "source_hashes",
                        "test_policy", "consumer_contract"} or \
            manifest.get("schema") != contract["schema"] or \
            manifest.get("status") != contract["status"]:
        raise PlanError("tech-staged manifest schema/status mismatch")
    pointer = registry["committed_techmap_manifest"]
    if digest != pointer["sha256"] or \
            manifest.get("repository_commit") != pointer["source_repository_commit"]:
        raise PlanError("tech-staged manifest differs from committed registry pointer")
    cohort = registry["cohorts"]["tech_staged_complete_compositions"]
    common_ports = staged_common_ports(cohort)
    if manifest.get("goal_order") != contract["goal_order"] or \
            list(manifest.get("designs", {})) != contract["goal_order"] or \
            manifest.get("common_ports") != common_ports or \
            manifest.get("constraint_templates") != contract["constraint_templates"]:
        raise PlanError("tech-staged manifest goal order/canonical port contract mismatch")
    verify_committed_blob(path, pointer["publication_repository_commit"], digest)
    technology = manifest.get("technology_authorities", {})
    live = technology.get("live_gsclib045", {})
    server_technology = authority["technology"]
    live_roles = {
        "liberty": "setup_liberty", "technology_lef": "tech_lef",
        "macro_lef": "macro_lef", "qrc": "shared_qrc",
    }
    if set(technology) != {"raw_golden", "buffered_golden", "live_gsclib045", "cells"} or \
            technology.get("raw_golden") != {
                "path": "/tmp/ganghee-pnr-raw-golden-20260813.tar.gz",
                "sha256": "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3"} or \
            technology.get("buffered_golden") != {
                "path": "/tmp/ganghee-pnr-golden-20260813.tar.gz",
                "sha256": "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f"} or \
            set(live) != {"liberty", "technology_lef", "macro_lef", "qrc",
                         "dffnsrx1_cell_and_interface_verified",
                         "liberty_timing_arcs_claimed_by_manifest"} or \
            live.get("dffnsrx1_cell_and_interface_verified") is not True or \
            live.get("liberty_timing_arcs_claimed_by_manifest") is not False or \
            any(not str(live.get(field, "")).endswith(
                "/" + server_technology[role]["relative_path"])
                for field, role in live_roles.items()):
        raise PlanError("tech-staged manifest live PDK/QRC authority mismatch")
    expected_cells = {
        "TLATNTSCAX2": {"ports": ["CK", "E", "SE", "ECK"]},
        "MX2X1": {"ports": ["A", "B", "S0", "Y"]},
        "DFFRHQX1": {"ports": ["RN", "CK", "D", "Q"]},
        "DFFNSRX1": {"ports": ["CKN", "D", "RN", "SN", "Q", "QN"]},
    }
    if technology.get("cells") != expected_cells:
        raise PlanError("tech-staged manifest cell interface authority mismatch")
    source_hashes = manifest.get("source_hashes")
    if not isinstance(source_hashes, dict) or not source_hashes or \
            any(not isinstance(name, str) or not name or
                not isinstance(value, str) or not SHA256.fullmatch(value)
                for name, value in source_hashes.items()):
        raise PlanError("tech-staged manifest source hash closure mismatch")
    if manifest.get("test_policy") != {
            "acceptance_sample": "posedge_ref_active_region_pre_NBA",
            "pending_hold": "through_charged_posedge",
            "protocol_error_must_equal_zero": True,
            "epoch_accepted_equals_retired": True,
            "cell_models_test_only": True}:
        raise PlanError("tech-staged manifest test policy mismatch")
    for design, row in manifest["designs"].items():
        expected_design = cohort["designs"][design]
        endpoint = expected_design["endpoint_leaf_contract"]
        staged_endpoint = {
            "path_segment": expected_design["endpoint_root"]["stable_prefix"],
            "leaf_counts": endpoint["leaf_counts"],
            "preserved_name_prefixes": endpoint["preserved_name_prefixes"],
        }
        if set(row) != {"top", "filelists", "port_signature", "endpoint_root",
                       "endpoint_leaf_contract", "whole_top_observed_totals"} or \
                row.get("top") != expected_design["top"] or \
                row.get("filelists") != expected_design["staged_filelists"] or \
                row.get("port_signature") != staged_port_signature(common_ports, design) or \
                row.get("endpoint_root") != expected_design["endpoint_root"] or \
                row.get("endpoint_leaf_contract") != staged_endpoint or \
                row.get("whole_top_observed_totals") != {
                    "status": "PENDING_DEDICATED_GENUS_RUN", "records": []}:
            raise PlanError("tech-staged manifest design contract mismatch")
    consumer = manifest.get("consumer_contract", {})
    if consumer != {
            "consumers": ["genus", "innovus"],
            "manifest_path": pointer["path"],
            "required_schema": contract["schema"],
            "required_status": contract["status"],
            "require_repository_commit": True,
            "require_literal_common_port_signature": True,
            "require_endpoint_path_and_leaf_provenance": True,
            "forbidden_port_aliases": [
                "load_i", "pending_i", "source_ready_o", "protocol_fault_o",
                "link_enable", "link_enable_i", "burst_clk_o", "burst_data_o",
                "p6_clk_o", "p6_data_o"],
            }:
        raise PlanError("tech-staged manifest consumer contract mismatch")
    return path, digest, manifest


def verify_committed_blob(path: Path, commit: str, expected_sha: str) -> None:
    """Bind a materialized package file to bytes stored in one Git commit object."""
    root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if root_result.returncode:
        raise PlanError("techmap manifest is not materialized from a Git repository")
    root = Path(root_result.stdout.strip()).resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as error:
        raise PlanError("techmap manifest escapes its Git repository") from error
    object_result = subprocess.run(
        ["git", "show", f"{commit}:{relative.as_posix()}"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if object_result.returncode or sha256(object_result.stdout) != expected_sha:
        raise PlanError("techmap manifest is not the exact committed Git blob")


def validate_environment(bound: dict[str, Any], authority: dict[str, Any]) -> tuple[Path, str]:
    path, digest, payload = bound_payload(bound, "PROVEN_ENVIRONMENT receipt")
    receipt = load_json(payload, "PROVEN_ENVIRONMENT receipt")
    if receipt.get("schema") != "k2_w2_server_env_result_v1" or \
            receipt.get("qualification_status") != "PROVEN_ENVIRONMENT" or \
            receipt.get("campaign_launch_allowed") is not True:
        raise PlanError("server environment is not PROVEN_ENVIRONMENT")
    gates = receipt.get("gates", {})
    tool = gates.get("tool_executables", {}).get("evidence", {}).get("innovus", {})
    if any(tool.get(key) != authority["tool"][key] for key in ("path", "sha256")) or \
            tool.get("parsed_version") != authority["tool"]["version"]:
        raise PlanError("PROVEN_ENVIRONMENT Innovus identity mismatch")
    files = gates.get("technology_files", {}).get("evidence", {})
    for role in ("setup_liberty", "hold_liberty", "tech_lef", "macro_lef"):
        if files.get(role, {}).get("sha256") != authority["technology"][role]["sha256"]:
            raise PlanError(f"PROVEN_ENVIRONMENT {role} hash mismatch")
    for role in ("setup_qrc", "hold_qrc"):
        if files.get(role, {}).get("sha256") != authority["technology"]["shared_qrc"]["sha256"]:
            raise PlanError("PROVEN_ENVIRONMENT shared QRC hash mismatch")
    semantics = gates.get("library_semantics", {}).get("evidence", {})
    if semantics.get("setup", {}).get("pvt") != authority["technology"]["setup_liberty"]["pvt"] or \
            semantics.get("hold", {}).get("pvt") != authority["technology"]["hold_liberty"]["pvt"]:
        raise PlanError("PROVEN_ENVIRONMENT slow/fast PVT mismatch")
    rx_contract = authority["rx_cell"]
    direct_rx = gates.get("dffnsrx1_contract", {}).get("evidence")
    if direct_rx is not None:
        if direct_rx != rx_contract:
            raise PlanError("PROVEN_ENVIRONMENT DFFNSRX1 contract mismatch")
    else:
        for corner in ("setup", "hold"):
            cell = semantics.get(corner, {}).get("cells", {}).get(rx_contract["name"], {})
            ff = cell.get("ff", {})
            normalized_ff = {key: str(value).replace("(", "").replace(")", "")
                             for key, value in ff.items()}
            if normalized_ff != {
                    "clocked_on": "!CKN", "clear": "!RN", "preset": "!SN"}:
                raise PlanError("PROVEN_ENVIRONMENT DFFNSRX1 FF semantics mismatch")
            timing = cell.get("timing", {})
            data_checks = {row.get("type") for row in timing.get("D", [])}
            reset_checks = {row.get("type") for pin in ("RN", "SN")
                            for row in timing.get(pin, [])}
            if not set(rx_contract["data_checks"]).issubset(data_checks) or \
                    not set(rx_contract["reset_checks"]).issubset(reset_checks):
                raise PlanError("PROVEN_ENVIRONMENT DFFNSRX1 timing arcs mismatch")
        lef_cell = gates.get("site_and_cell_availability", {}).get(
            "evidence", {}).get("site_legal_macros", {}).get(rx_contract["name"], {})
        if set(lef_cell.get("pins", {})) != set(rx_contract["lef_pins"]) or \
                lef_cell.get("site") != authority["technology"]["site"]:
            raise PlanError("PROVEN_ENVIRONMENT DFFNSRX1 LEF contract mismatch")
    return path, digest


def validate_genus(producer: dict[str, Any], design: str, top: str,
                   netlist_sha: str, sdc_sha: str,
                   staged_identity: dict[str, str],
                   technology_authorities: dict[str, Any],
                   template: dict[str, str],
                   endpoint_contract: dict[str, Any],
                   whole_inventory: dict[str, int],
                   authority: dict[str, Any]) -> tuple[Path, str, Path, str, Path, str,
                                                       Path, str]:
    if set(producer) != {"kind", "receipt", "innovus_handoff",
                         "endpoint_connectivity_map", "mapped_functional_gate"} or \
            producer.get("kind") != "k2_w2_genus_exact_three_endpoint_receipt_v3":
        raise PlanError("producer must be one authenticated Genus v3 receipt/handoff")
    path, digest, payload = bound_payload(producer["receipt"], "Genus v3 receipt")
    receipt = load_json(payload, "Genus v3 receipt")
    handoff_path, handoff_sha, handoff_payload = bound_payload(
        producer["innovus_handoff"], "Genus Innovus handoff")
    handoff = load_json(handoff_payload, "Genus Innovus handoff")
    endpoint_path, endpoint_sha, endpoint_payload = bound_payload(
        producer["endpoint_connectivity_map"], "endpoint connectivity map")
    endpoint_map = load_json(endpoint_payload, "endpoint connectivity map")
    functional_path, functional_sha, functional_payload = bound_payload(
        producer["mapped_functional_gate"], "mapped functional gate receipt")
    functional = load_json(functional_payload, "mapped functional gate receipt")
    if receipt.get("schema") != "k2_w2_genus_exact_three_endpoint_receipt_v3" or \
            receipt.get("status") != "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD" or \
            receipt.get("design") != design or receipt.get("top") != top or \
            receipt.get("boundary_cohort") != "tech_staged_complete_compositions" or \
            receipt.get("source_origin") != "tech_staged_repository_exact" or \
            receipt.get("ranking_policy") != \
            "ONLY_THREE_TECH_STAGED_COMPLETE_COMPOSITIONS_COMPARABLE" or \
            receipt.get("claim_boundary") != \
            "GENUS_MAPPED_TIMING_SCREENING_ONLY_POWER_AND_PHYSICAL_PPA_HOLD":
        raise PlanError("Genus v3 receipt design/top/status/screening boundary mismatch")
    if receipt.get("staged_manifest") != staged_identity or \
            receipt.get("technology_authorities") != technology_authorities or \
            handoff.get("schema") != "k2_w2_innovus_strict_sdc_handoff_v1" or \
            receipt.get("innovus_handoff_sha256") != handoff_sha or \
            handoff.get("design") != design or handoff.get("top") != top or \
            handoff.get("strict_input_sdc_sha256") != template["sha256"] or \
            receipt.get("strict_sdc_sha256") != template["sha256"]:
        raise PlanError("Genus v3 receipt/manifest/handoff identity mismatch")
    inventory = receipt.get("mapped_inventory", {})
    endpoint_counts = endpoint_contract["leaf_counts"]
    mapped_types = inventory.get("mapped_cell_types", {})
    if not isinstance(mapped_types, dict) or \
            any(not isinstance(cell, str) or not isinstance(count, int) or count <= 0
                for cell, count in mapped_types.items()) or \
            inventory.get("mapped_cell_count") != sum(mapped_types.values()) or \
            any(mapped_types.get(cell) != count
                for cell, count in whole_inventory.items()):
        raise PlanError("Genus v3 whole-top mapped inventory accounting mismatch")
    if inventory.get("mapped_netlist_sha256") != netlist_sha or \
            receipt.get("mapped_sdc_sha256") != sdc_sha or \
            inventory.get("scan_cell_types") != [] or \
            inventory.get("required_rx_contract", {}).get("exact_instances") != \
            endpoint_counts["DFFNSRX1"] or \
            handoff.get("mapped_netlist_sha256") != netlist_sha or \
            handoff.get("mapped_sdc_sha256") != sdc_sha:
        raise PlanError("Genus v3 mapped inventory/netlist/SDC mismatch")
    endpoint_evidence = receipt.get("endpoint_leaf_inventory", {})
    if endpoint_evidence != {
            "connectivity_map_sha256": endpoint_sha,
            "preserved_name_prefixes": endpoint_contract["preserved_name_prefixes"],
            "leaf_counts": endpoint_counts,
            "no_other_negedge_state_proven":
                endpoint_contract["no_other_negedge_state_proven"],
            } or endpoint_map.get("schema") != "k2_w2_endpoint_connectivity_map_v1" or \
            endpoint_map.get("design") != design or endpoint_map.get("top") != top:
        raise PlanError("Genus v3 endpoint provenance evidence mismatch")
    scenarios = {
        "fovea_a7": ["held_pending", "conservation", "reset", "drain"],
        "a2_p6": ["ordered_pairs", "back_to_back", "reset"],
        "a3_p6": ["ordered_pairs", "back_to_back", "reset"],
    }[design]
    mapped_sdf_sha = receipt.get("mapped_sdf_sha256")
    if not isinstance(mapped_sdf_sha, str) or not SHA256.fullmatch(mapped_sdf_sha) or \
            handoff.get("mapped_sdf_sha256") != mapped_sdf_sha or \
            functional.get("schema") != "k2_w2_mapped_functional_gate_v1" or \
            functional.get("status") != "PASS" or \
            functional.get("design") != design or functional.get("top") != top or \
            functional.get("mapped_netlist_sha256") != netlist_sha or \
            functional.get("method") not in {"xcelium_vendor_models", "formal_lec"} or \
            functional.get("scenarios") != scenarios or \
            functional.get("checks") != {
                "accepted": "EXACT", "retired": "EXACT", "global_order": "EXACT",
                "conservation": "EXACT", "protocol_error": "ZERO",
                "reset_and_drain": "PASS"} or \
            not isinstance(functional.get("log_sha256"), str) or \
            not SHA256.fullmatch(functional["log_sha256"]) or \
            not isinstance(functional.get("model_sha256"), dict) or \
            not functional["model_sha256"] or \
            any(not SHA256.fullmatch(value) for value in functional["model_sha256"].values()) or \
            functional.get("sdf_status") != "ANNOTATED" or \
            functional.get("sdf_sha256") != mapped_sdf_sha:
        raise PlanError("mapped staged-vs-netlist functional gate mismatch")
    if receipt.get("mapped_functional_gate_sha256") != functional_sha:
        raise PlanError("Genus v3 receipt does not bind mapped functional gate")
    checks = receipt.get("checks", {})
    if checks.get("dffnsrx1_rx_mapping") != \
            "PASS_EXACT_COUNT_PINS_AND_NONZERO_RECOVERY_REMOVAL" or \
            checks.get("power_activity_gate") != "HOLD_VECTORLESS_IS_NOT_ACTIVITY_QUALIFIED":
        raise PlanError("Genus v3 RX arcs or screening-only power boundary mismatch")
    # Fast Liberty, LEF and shared QRC are authenticated downstream provenance;
    # only the slow Liberty drove the Genus synthesis/timing screen.
    if handoff.get("innovus_consumption_status") != \
            "PENDING_REQUIRES_EXACT_HASH_RECEIPT":
        raise PlanError("Genus v3 handoff was already or ambiguously consumed")
    technology = authority["technology"]
    handoff_technology = {
        "setup_liberty_sha256": technology["setup_liberty"]["sha256"],
        "hold_liberty_sha256": technology["hold_liberty"]["sha256"],
        "cell_lef_sha256": technology["macro_lef"]["sha256"],
        "shared_setup_hold_qrc_sha256": technology["shared_qrc"]["sha256"],
        "shared_qrc_limitation": "ONE_TYPICAL_GPDK045_TCH_FOR_SETUP_AND_HOLD",
    }
    if any(handoff.get(key) != value for key, value in handoff_technology.items()):
        raise PlanError("Genus v3 handoff technology provenance mismatch")
    return (path, digest, handoff_path, handoff_sha, endpoint_path, endpoint_sha,
            functional_path, functional_sha)


def validate_activity(value: Any) -> tuple[Path, str, str, str, str, str]:
    expected = {"file", "format", "scope", "window_start_ns", "window_end_ns"}
    if not isinstance(value, dict) or set(value) != expected:
        raise PlanError("activity must contain exact file/format/scope/window fields")
    path, digest, _ = bound_payload(value["file"], "annotated activity")
    if value["format"] not in {"SAIF", "VCD"} or not value["scope"]:
        raise PlanError("activity must be scoped SAIF or VCD")
    try:
        start, end = float(value["window_start_ns"]), float(value["window_end_ns"])
    except (TypeError, ValueError) as error:
        raise PlanError("activity window is not numeric") from error
    if start < 0 or end <= start:
        raise PlanError("activity window is empty or reversed")
    return path, digest, value["format"], value["scope"], \
        value["window_start_ns"], value["window_end_ns"]


def validate_plan(plan_path: Path) -> list[Binding]:
    plan = load_json(stable_read(plan_path.absolute()), "Innovus plan")
    required = {"schema", "cohort", "purpose", "ranking_eligible",
                "staged_manifest", "server_environment", "runs"}
    if set(plan) != required or plan.get("schema") != "k2_w2_innovus_plan_v2":
        raise PlanError("Innovus plan schema/field set mismatch")
    registry, authority = load_contracts()
    cohort_name = "tech_staged_complete_compositions"
    if plan["cohort"] != cohort_name:
        raise PlanError("only tech-staged final cohort may be launched")
    cohort = registry["cohorts"][cohort_name]
    if plan["purpose"] != cohort["purpose"] or plan["ranking_eligible"] is not True:
        raise PlanError("plan purpose/ranking mismatch")
    manifest_path, manifest_sha, manifest = validate_staged_manifest(
        plan["staged_manifest"], registry, authority)
    environment_path, environment_sha = validate_environment(
        plan["server_environment"], authority)
    pointer = registry["committed_techmap_manifest"]
    producer_staged_identity = {
        "path": pointer["path"],
        "sha256": manifest_sha,
        "source_commit": pointer["source_repository_commit"],
        "publication_commit": pointer["publication_repository_commit"],
    }
    runs = plan["runs"]
    if not isinstance(runs, list) or [row.get("design") for row in runs] != cohort["exact_design_set"]:
        raise PlanError("plan must use the exact ordered tech-staged design set")
    bindings = []
    period_seen = None
    seen: set[Path] = set()
    outputs: set[Path] = set()
    for run in runs:
        fields = {"design", "top", "clocks", "link_pins", "period_ns",
                  "mapped_netlist", "mapped_sdc", "producer", "activity", "output_dir"}
        if not isinstance(run, dict) or set(run) != fields:
            raise PlanError("plan run field set mismatch")
        design = run["design"]
        contract = cohort["designs"][design]
        top = run["top"]
        if top != contract["top"] or top in registry["forbidden_final_tops"]:
            raise PlanError("generic/debug top substitution is forbidden")
        if run["clocks"] != contract["clocks"] or run["link_pins"] != contract["link_pins"]:
            raise PlanError("plan clock/link contract mismatch")
        period = run["period_ns"]
        if period_seen is None:
            period_seen = period
        elif period != period_seen:
            raise PlanError("final cohort must use one common period")
        netlist, netlist_sha, netlist_payload = bound_payload(run["mapped_netlist"], "mapped netlist")
        sdc, sdc_sha, sdc_payload = bound_payload(run["mapped_sdc"], "mapped SDC")
        registry_endpoint = contract["endpoint_leaf_contract"]
        endpoint_contract = registry_endpoint
        _, _, endpoint_payload = bound_payload(
            run["producer"]["endpoint_connectivity_map"],
            "endpoint connectivity map")
        endpoint_map = load_json(endpoint_payload, "endpoint connectivity map")
        whole_inventory = validate_netlist(
            netlist_payload, top, contract, cohort["common_ports"],
            endpoint_contract, endpoint_map)
        validate_sdc(sdc_payload, top, contract, period)
        template = authority["constraint_templates"][contract["constraint_template"]]
        (producer, producer_sha, handoff, handoff_sha, endpoint_map_path,
         endpoint_map_sha, mapped_functional, mapped_functional_sha) = validate_genus(
            run["producer"], design, top, netlist_sha, sdc_sha,
            producer_staged_identity, manifest["technology_authorities"], template,
            endpoint_contract, whole_inventory, authority)
        activity = validate_activity(run["activity"])
        output = Path(run["output_dir"])
        if not output.is_absolute() or output.exists() or output in outputs:
            raise PlanError("output directory must be unique, absent, and absolute")
        outputs.add(output)
        for path in (netlist, sdc, producer, handoff, endpoint_map_path,
                     mapped_functional, activity[0]):
            if path in seen:
                raise PlanError("per-run evidence file reused across runs")
            seen.add(path)
        bindings.append(Binding(
            design, top, cohort_name, period, netlist, netlist_sha, sdc, sdc_sha,
            output, producer, producer_sha, handoff, handoff_sha,
            endpoint_map_path, endpoint_map_sha,
            mapped_functional, mapped_functional_sha,
            manifest_path, manifest_sha,
            environment_path, environment_sha, *activity,
        ))
    return bindings


def descriptor(binding: Binding) -> dict[str, Any]:
    value = asdict(binding)
    for key, item in list(value.items()):
        if isinstance(item, Path):
            value[key] = str(item)
    return {"schema": "k2_w2_innovus_execution_descriptor_v1", "binding": value,
            "registry_sha256": sha256(REGISTRY_PATH.read_bytes()),
            "authority_sha256": sha256(AUTHORITY_PATH.read_bytes())}


def verify_descriptor(path: Path, expected_sha: str, environment: dict[str, str]) -> Binding:
    payload = stable_read(path.absolute())
    if not SHA256.fullmatch(expected_sha) or sha256(payload) != expected_sha:
        raise PlanError("execution descriptor SHA mismatch")
    value = load_json(payload, "execution descriptor")
    if value.get("schema") != "k2_w2_innovus_execution_descriptor_v1" or \
            value.get("registry_sha256") != sha256(REGISTRY_PATH.read_bytes()) or \
            value.get("authority_sha256") != sha256(AUTHORITY_PATH.read_bytes()):
        raise PlanError("execution descriptor authority mismatch")
    raw = value.get("binding")
    if not isinstance(raw, dict) or set(raw) != {field.name for field in Binding.__dataclass_fields__.values()}:
        raise PlanError("execution descriptor binding field mismatch")
    for key in ("netlist", "sdc", "output_dir", "producer_path", "handoff_path",
                "endpoint_map_path",
                "mapped_functional_path",
                "staged_manifest_path",
                "environment_path", "activity_path"):
        raw[key] = Path(raw[key])
    binding = Binding(**raw)
    registry, authority = load_contracts()
    env_map = {
        "AER_TOP": binding.top, "AER_PNR_NETLIST": str(binding.netlist),
        "AER_PNR_SDC": str(binding.sdc), "AER_PNR_OUTPUT_DIR": str(binding.output_dir),
        "AER_W2_COHORT": binding.cohort, "AER_W2_DESIGN": binding.design,
        "AER_ACTIVITY_FILE": str(binding.activity_path),
        "AER_ACTIVITY_FORMAT": binding.activity_format,
        "AER_ACTIVITY_SCOPE": binding.activity_scope,
        "AER_ACTIVITY_WINDOW_START_NS": binding.activity_window_start_ns,
        "AER_ACTIVITY_WINDOW_END_NS": binding.activity_window_end_ns,
    }
    if any(environment.get(key) != expected for key, expected in env_map.items()):
        raise PlanError("execution environment differs from descriptor")
    identities = ((binding.netlist, binding.netlist_sha256), (binding.sdc, binding.sdc_sha256),
                  (binding.producer_path, binding.producer_sha256),
                  (binding.handoff_path, binding.handoff_sha256),
                  (binding.endpoint_map_path, binding.endpoint_map_sha256),
                  (binding.mapped_functional_path, binding.mapped_functional_sha256),
                  (binding.staged_manifest_path, binding.staged_manifest_sha256),
                  (binding.environment_path, binding.environment_sha256),
                  (binding.activity_path, binding.activity_sha256))
    for item, digest in identities:
        if sha256(stable_read(item)) != digest:
            raise PlanError(f"descriptor input changed: {item}")
    tech_paths = {
        "AER_SETUP_LIBRARY_FILE": "setup_liberty", "AER_HOLD_LIBRARY_FILE": "hold_liberty",
        "AER_TECH_LEF": "tech_lef", "AER_CELL_LEF": "macro_lef",
    }
    for variable, role in tech_paths.items():
        supplied = Path(environment.get(variable, ""))
        expected = authority["technology"][role]
        if supplied.name != Path(expected["relative_path"]).name or \
                sha256(stable_read(supplied)) != expected["sha256"]:
            raise PlanError(f"server technology identity mismatch: {role}")
    setup_qrc = Path(environment.get("AER_SETUP_QRC_TECH", ""))
    hold_qrc = Path(environment.get("AER_HOLD_QRC_TECH", ""))
    qrc = authority["technology"]["shared_qrc"]
    if setup_qrc != hold_qrc or setup_qrc.name != "gpdk045.tch" or \
            sha256(stable_read(setup_qrc)) != qrc["sha256"]:
        raise PlanError("server QRC must be exact shared gpdk045.tch")
    tool = Path(environment.get("AER_INNOVUS_BIN", "innovus"))
    if str(tool) != authority["tool"]["path"] or sha256(stable_read(tool)) != authority["tool"]["sha256"]:
        raise PlanError("Innovus executable identity mismatch")
    if binding.output_dir.exists():
        raise PlanError("descriptor output directory already exists")
    return binding


def execute_plan(bindings: list[Binding]) -> None:
    for binding in bindings:
        with tempfile.TemporaryDirectory(prefix="w2-innovus-descriptor-") as temporary:
            path = Path(temporary) / "execution.json"
            path.write_bytes(canonical(descriptor(binding)))
            path.chmod(0o444)
            digest = sha256(path.read_bytes())
            environment = os.environ.copy()
            environment.update({
                "AER_TOP": binding.top, "AER_PNR_NETLIST": str(binding.netlist),
                "AER_PNR_SDC": str(binding.sdc), "AER_PNR_OUTPUT_DIR": str(binding.output_dir),
                "AER_W2_COHORT": binding.cohort, "AER_W2_DESIGN": binding.design,
                "AER_ACTIVITY_FILE": str(binding.activity_path),
                "AER_ACTIVITY_FORMAT": binding.activity_format,
                "AER_ACTIVITY_SCOPE": binding.activity_scope,
                "AER_ACTIVITY_WINDOW_START_NS": binding.activity_window_start_ns,
                "AER_ACTIVITY_WINDOW_END_NS": binding.activity_window_end_ns,
                "AER_W2_EXECUTION_DESCRIPTOR": str(path),
                "AER_W2_EXECUTION_DESCRIPTOR_SHA256": digest,
            })
            result = subprocess.run([str(GENERIC_RUNNER)], env=environment, check=False)
            if result.returncode:
                raise PlanError(f"Innovus runner failed for {binding.design}: {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-descriptor", type=Path)
    parser.add_argument("--descriptor-sha256")
    args = parser.parse_args(argv)
    try:
        if args.verify_descriptor:
            if args.plan or args.validate_only or args.execute or not args.descriptor_sha256:
                raise PlanError("descriptor verification mode arguments mismatch")
            verify_descriptor(args.verify_descriptor, args.descriptor_sha256, dict(os.environ))
            print("W2_INNOVUS_EXECUTION_DESCRIPTOR_VALID")
            return 0
        if not args.plan or args.validate_only == args.execute:
            raise PlanError("plan mode requires exactly one of --validate-only/--execute")
        bindings = validate_plan(args.plan)
        if args.execute:
            execute_plan(bindings)
        print(f"W2_INNOVUS_PLAN_VALID cohort={bindings[0].cohort} runs={len(bindings)} "
              f"mode={'execute' if args.execute else 'validate-only'}")
        return 0
    except (OSError, PlanError, subprocess.SubprocessError) as error:
        print(f"W2_INNOVUS_PLAN_REJECTED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
