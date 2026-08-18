#!/usr/bin/env python3
"""Fail-closed elaborated verifier for the REDRED single-edge fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTRACT_SCHEMA = "redred-single-edge-cdc-rdc-contract-v1"
BINDING_SCHEMA = "redred-single-edge-source-binding-v1"
HOLD = "HOLD_A2_SOURCE_SET_UNBOUND"
CANONICAL_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
CANONICAL_INTEGRATION_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
CANONICAL_BINDING_SHA256 = "48974c5831f75177703b00853fdad1b3074d77b9faa8e5ef3a64cbbadd46887f"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
CLOCKISH = re.compile(r"(?:^|_)(?:clk|clock)(?:$|_)", re.I)
EXPECTED_POLICY = {
    "clock_domains": 1,
    "clock_edge": "posedge",
    "generated_gated_forwarded_clocks": "FORBIDDEN",
    "negedge_sequential_processes": "FORBIDDEN",
    "event_transfer": "REGISTERED_TX_TO_REGISTERED_RX_SAME_EDGE",
    "reset": "SYNCHRONOUS_ASSERT_SYNCHRONOUS_DEASSERT_DRAIN_BEFORE_ASSERT",
    "unsynchronized_crossings": "FORBIDDEN",
    "external_input_domain": "BOUND_SYNCHRONOUS_TO_PRIMARY_CLOCK_ASSUMPTION",
    "unknown_modules_or_clocks": "FAIL",
}


class ContractError(RuntimeError):
    pass


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


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def validate_contract(document: dict[str, Any]) -> dict[str, str] | None:
    exact_keys(document, {"schema", "contract_id", "decision",
                          "a2_source_set", "policy"}, "contract")
    if document["schema"] != CONTRACT_SCHEMA:
        raise ContractError("contract schema differs")
    if document["contract_id"] != "REDRED_A2_A3_SINGLE_EDGE_FALLBACK":
        raise ContractError("contract id differs")
    if document["policy"] != EXPECTED_POLICY:
        raise ContractError("contract policy differs")
    source_set = document["a2_source_set"]
    if source_set is None:
        if document["decision"] != HOLD:
            raise ContractError("unbound contract must remain HOLD")
        return None
    if document["decision"] != "BOUND_VERIFY_REQUIRED":
        raise ContractError("bound contract decision must require verification")
    exact_keys(source_set, {"binding", "binding_sha256", "repository_commit",
                            "integration_commit"},
               "contract.a2_source_set")
    binding = source_set["binding"]
    commit = source_set["repository_commit"]
    integration_commit = source_set["integration_commit"]
    binding_sha = source_set["binding_sha256"]
    if not isinstance(binding, str) or Path(binding).name != binding:
        raise ContractError("contract binding must be a local filename")
    if commit != CANONICAL_COMMIT:
        raise ContractError("contract repository commit differs from canonical source set")
    if integration_commit != CANONICAL_INTEGRATION_COMMIT:
        raise ContractError("contract integration commit differs from canonical source set")
    if binding_sha != CANONICAL_BINDING_SHA256:
        raise ContractError("contract binding digest differs from canonical source set")
    return {"binding": binding, "binding_sha256": binding_sha,
            "repository_commit": commit, "integration_commit": integration_commit}


def valid_ident(value: Any, where: str) -> str:
    if not isinstance(value, str) or IDENT.fullmatch(value) is None:
        raise ContractError(f"{where} is not a SystemVerilog identifier")
    return value


def parse_design(value: Any, where: str) -> dict[str, Any]:
    exact_keys(value, {"top", "primary_clock", "reset", "reset_active_low",
                       "transfer_scope", "tx_instance", "rx_instance",
                       "drain_output", "scope_drain_port", "rx_pending_port",
                       "clean_drain_error", "scope_error_port",
                       "top_drain_require_one", "top_drain_require_zero",
                       "synchronous_inputs", "channels"}, where)
    result = {
        key: valid_ident(value[key], f"{where}.{key}")
        for key in ("top", "primary_clock", "reset", "transfer_scope",
                    "tx_instance", "rx_instance", "drain_output",
                    "scope_drain_port", "rx_pending_port", "clean_drain_error",
                    "scope_error_port")
    }
    if type(value["reset_active_low"]) is not bool:
        raise ContractError(f"{where}.reset_active_low must be boolean")
    result["reset_active_low"] = value["reset_active_low"]
    synchronous_inputs = value["synchronous_inputs"]
    if (not isinstance(synchronous_inputs, list) or not synchronous_inputs
            or len(set(synchronous_inputs)) != len(synchronous_inputs)):
        raise ContractError(f"{where}.synchronous_inputs must be a unique nonempty list")
    result["synchronous_inputs"] = [
        valid_ident(item, f"{where}.synchronous_inputs") for item in synchronous_inputs]
    for key in ("top_drain_require_one", "top_drain_require_zero"):
        values = value[key]
        if not isinstance(values, list) or len(set(values)) != len(values):
            raise ContractError(f"{where}.{key} must be a unique list")
        result[key] = [valid_ident(item, f"{where}.{key}") for item in values]
    channels = value["channels"]
    if not isinstance(channels, list) or len(channels) < 2:
        raise ContractError(f"{where}.channels must contain valid and payload channels")
    parsed_channels = []
    seen: set[tuple[str, str]] = set()
    for index, channel in enumerate(channels):
        label = f"{where}.channels[{index}]"
        exact_keys(channel, {"tx_port", "rx_port"}, label)
        pair = (valid_ident(channel["tx_port"], label + ".tx_port"),
                valid_ident(channel["rx_port"], label + ".rx_port"))
        if pair in seen:
            raise ContractError(f"duplicate channel pair in {where}")
        seen.add(pair)
        parsed_channels.append({"tx_port": pair[0], "rx_port": pair[1]})
    result["channels"] = parsed_channels
    if result["tx_instance"] == result["rx_instance"]:
        raise ContractError(f"{where} TX and RX instances must be distinct")
    return result


def safe_git_path(relative: Any) -> str:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ContractError("source path must be a nonempty POSIX relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ContractError(f"unsafe source path: {relative!r}")
    return relative


def git_blob(repo: Path, commit: str, relative: str) -> bytes:
    process = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{relative}"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if process.returncode:
        raise ContractError(
            f"cannot read pinned source {commit}:{relative}: "
            f"{process.stderr.decode(errors='replace').strip()}")
    return process.stdout


def validate_binding(document: dict[str, Any], repo: Path,
                     required_commit: str | None = None) -> tuple[list[tuple[str, bytes]], dict[str, Any]]:
    exact_keys(document, {"schema", "source_set_id", "repository_commit",
                          "integration_commit",
                          "files", "designs"}, "binding")
    if document["schema"] != BINDING_SCHEMA:
        raise ContractError("binding schema differs")
    if not isinstance(document["source_set_id"], str) or not document["source_set_id"].strip():
        raise ContractError("binding source_set_id must be nonempty")
    commit = document["repository_commit"]
    integration_commit = document["integration_commit"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ContractError("binding repository_commit must be full lowercase hex")
    resolved = subprocess.run(["git", "-C", str(repo), "rev-parse", commit], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if resolved.returncode or resolved.stdout.strip() != commit:
        raise ContractError(f"pinned repository commit is unavailable: {commit}")
    if required_commit is not None and commit != required_commit:
        raise ContractError("binding commit differs from canonical contract")
    if integration_commit != CANONICAL_INTEGRATION_COMMIT:
        raise ContractError("binding integration commit differs from canonical contract")
    integration_resolved = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", integration_commit], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if (integration_resolved.returncode
            or integration_resolved.stdout.strip() != integration_commit):
        raise ContractError(f"pinned integration commit is unavailable: {integration_commit}")
    exact_keys(document["designs"], {"a2", "a3"}, "binding.designs")
    designs = {name: parse_design(document["designs"][name], f"binding.designs.{name}")
               for name in ("a2", "a3")}
    files = document["files"]
    if not isinstance(files, list) or not files:
        raise ContractError("binding.files must be a nonempty ordered list")
    blobs: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for index, item in enumerate(files):
        exact_keys(item, {"path", "sha256"}, f"binding.files[{index}]")
        relative = safe_git_path(item["path"])
        if relative in seen:
            raise ContractError(f"duplicate source path: {relative}")
        seen.add(relative)
        expected = item["sha256"]
        if not isinstance(expected, str) or HEX64.fullmatch(expected) is None:
            raise ContractError(f"invalid SHA-256 for source {relative!r}")
        data = git_blob(repo, commit, relative)
        if git_blob(repo, integration_commit, relative) != data:
            raise ContractError(f"integration blob differs from hardened source: {relative}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ContractError(f"source SHA-256 mismatch {relative}: {actual}")
        blobs.append((relative, data))
    return blobs, designs


def find_verilator(requested: str | None) -> str:
    candidates = []
    if requested:
        candidates.append(requested)
    env = os.environ.get("VERILATOR")
    if env:
        candidates.append(env)
    found = shutil.which("verilator")
    if found:
        candidates.append(found)
    candidates.append("/tmp/a7-toolchain/usr/bin/verilator")
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise ContractError("bound verification requires an executable Verilator")


def source_without_comments_strings(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return text


def check_source_constructs(sources: list[Path]) -> None:
    for path in sources:
        code = source_without_comments_strings(path.read_text(encoding="utf-8"))
        if re.search(r"\balways_latch\b", code):
            raise ContractError(f"{path.name}: latch process is forbidden")
        for match in re.finditer(r"\balways(?:_ff)?\s*@\s*\((.*?)\)", code, re.S):
            event = re.sub(r"\s+", " ", match.group(1)).strip()
            if "edge" in event and re.fullmatch(r"posedge (?:clk|clk_i)", event) is None:
                raise ContractError(
                    f"{path.name}: sequential event must be direct posedge clock input: {event}")
        for match in re.finditer(r"\.(clk|clk_i)\s*\(([^()]*)\)", code):
            expression = match.group(2).strip()
            if expression not in ("clk", "clk_i"):
                raise ContractError(
                    f"{path.name}: clock port connection is derived/forwarded: {expression}")


@dataclass(frozen=True)
class Requirement:
    port: str
    active_low: bool | None = None


class XmlDesign:
    def __init__(self, xml_path: Path, top: str, log: str):
        try:
            self.root = ET.parse(xml_path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise ContractError(f"invalid Verilator XML for {top}: {exc}") from exc
        self.top = top
        self.log = log
        self.modules = {node.get("name"): node for node in self.root.findall("./netlist/module")}
        if top not in self.modules or self.modules[top].get("topModule") != "1":
            raise ContractError(f"elaborated top is missing or ambiguous: {top}")
        if any(marker in log for marker in ("%Warning-MODDUP", "%Warning-LATCH")):
            raise ContractError("elaboration reported duplicate modules or inferred latches")
        self._clock_cache: dict[str, Requirement | None] = {}
        self._reset_cache: dict[str, Requirement | None] = {}
        self._active: set[tuple[str, str]] = set()

    @staticmethod
    def vars(module: ET.Element) -> dict[str, ET.Element]:
        return {node.get("name", ""): node for node in module.findall("./var")}

    @staticmethod
    def instances(module: ET.Element) -> list[ET.Element]:
        return module.findall("./instance")

    @staticmethod
    def direct_var(expression: ET.Element | None) -> str | None:
        if expression is None or expression.tag != "varref":
            return None
        return expression.get("name")

    @staticmethod
    def port_expression(port: ET.Element) -> ET.Element | None:
        children = list(port)
        return children[0] if len(children) == 1 else None

    @staticmethod
    def reset_condition(node: ET.Element) -> Requirement | None:
        if node.tag == "varref":
            name = node.get("name")
            return Requirement(name, False) if name else None
        if node.tag == "not" and len(node) == 1 and node[0].tag == "varref":
            name = node[0].get("name")
            return Requirement(name, True) if name else None
        return None

    @staticmethod
    def sequential_always(module: ET.Element) -> list[ET.Element]:
        return [node for node in module.findall(".//always") if node.find("./sentree") is not None]

    def direct_requirements(self, module_name: str) -> tuple[list[Requirement], list[Requirement]]:
        module = self.modules[module_name]
        ports = self.vars(module)
        clocks: list[Requirement] = []
        resets: list[Requirement] = []
        for block in self.sequential_always(module):
            sentree = block.find("./sentree")
            assert sentree is not None
            items = sentree.findall("./senitem")
            if len(items) != 1:
                raise ContractError(
                    f"{module_name}: sequential process must have exactly one edge; "
                    f"negedge/asynchronous or multi-edge process found")
            item = items[0]
            if item.get("edgeType") != "POS":
                raise ContractError(f"{module_name}: negedge/unknown sequential edge is forbidden")
            clock = self.direct_var(item.find("./varref"))
            if clock is None or clock not in ports or ports[clock].get("dir") != "input":
                raise ContractError(f"{module_name}: generated/gated/unknown clock {clock!r}")
            clocks.append(Requirement(clock))
            body = [child for child in block if child.tag != "sentree"]
            if len(body) != 1:
                raise ContractError(f"{module_name}: malformed sequential process body")
            container = body[0]
            statements = list(container) if container.tag == "begin" else [container]
            if not statements or statements[0].tag != "if" or len(statements[0]) < 2:
                raise ContractError(f"{module_name}: state process lacks outer synchronous reset")
            reset = self.reset_condition(statements[0][0])
            if reset is None or reset.port not in ports or ports[reset.port].get("dir") != "input":
                raise ContractError(f"{module_name}: reset guard is not a direct input polarity test")
            resets.append(reset)
        return clocks, resets

    def requirement(self, module_name: str, kind: str) -> Requirement | None:
        cache = self._clock_cache if kind == "clock" else self._reset_cache
        if module_name in cache:
            return cache[module_name]
        marker = (module_name, kind)
        if marker in self._active:
            raise ContractError(f"recursive module hierarchy at {module_name}")
        self._active.add(marker)
        module = self.modules[module_name]
        direct_clocks, direct_resets = self.direct_requirements(module_name)
        requirements = direct_clocks if kind == "clock" else direct_resets
        demands: list[Requirement] = list(requirements)
        for instance in self.instances(module):
            child_name = instance.get("defName", "")
            if child_name not in self.modules:
                raise ContractError(f"{module_name}: unknown module {child_name!r}")
            child = self.requirement(child_name, kind)
            if child is None:
                continue
            port = next((p for p in instance.findall("./port")
                         if p.get("name") == child.port), None)
            if port is None:
                raise ContractError(f"{module_name}.{instance.get('name')}: missing {kind} port")
            expression = self.port_expression(port)
            if kind == "clock":
                name = self.direct_var(expression)
                if name is None:
                    raise ContractError(
                        f"{module_name}.{instance.get('name')}: generated/gated/inverted clock")
                demands.append(Requirement(name))
            else:
                polarity = child.active_low
                if expression is not None and expression.tag == "not" and len(expression) == 1:
                    name = self.direct_var(expression[0])
                    polarity = not bool(polarity)
                else:
                    name = self.direct_var(expression)
                if name is None:
                    raise ContractError(f"{module_name}.{instance.get('name')}: derived reset expression")
                demands.append(Requirement(name, polarity))
        self._active.remove(marker)
        if not demands:
            result = None
        else:
            if any(demand != demands[0] for demand in demands[1:]):
                raise ContractError(f"{module_name}: multiple or inconsistent {kind} domains")
            port = self.vars(module).get(demands[0].port)
            if port is None or port.get("dir") != "input":
                raise ContractError(
                    f"{module_name}: {kind} {demands[0].port!r} is internal/generated")
            result = demands[0]
        cache[module_name] = result
        return result

    def parent_map(self, module: ET.Element) -> dict[ET.Element, ET.Element]:
        return {child: parent for parent in module.iter() for child in parent}

    @staticmethod
    def ancestor(node: ET.Element, parents: dict[ET.Element, ET.Element], tag: str) -> ET.Element | None:
        current = node
        while current in parents:
            current = parents[current]
            if current.tag == tag:
                return current
        return None

    def validate_special_uses(self, module_name: str, clock: Requirement | None,
                              reset: Requirement | None) -> None:
        module = self.modules[module_name]
        parents = self.parent_map(module)
        child_requirements: dict[tuple[str, str], str] = {}
        for instance in self.instances(module):
            child_name = instance.get("defName", "")
            for kind in ("clock", "reset"):
                req = self.requirement(child_name, kind)
                if req:
                    child_requirements[(instance.get("name", ""), req.port)] = kind
        for kind, requirement in (("clock", clock), ("reset", reset)):
            if requirement is None:
                continue
            # Reset is an externally constrained synchronous control.  It may
            # also quiesce ready/error combinational outputs, but it may not
            # appear in a sensitivity list because every sentree was already
            # required to contain only the primary positive clock edge.
            if kind == "reset":
                continue
            declaration = self.vars(module)[requirement.port]
            for ref in module.findall(f".//varref[@name='{requirement.port}']"):
                if ref is declaration:
                    continue
                senitem = self.ancestor(ref, parents, "senitem")
                if kind == "clock" and senitem is not None:
                    continue
                always = self.ancestor(ref, parents, "always")
                port = self.ancestor(ref, parents, "port")
                instance = self.ancestor(ref, parents, "instance")
                if port is not None and instance is not None:
                    expected = child_requirements.get(
                        (instance.get("name", ""), port.get("name", "")))
                    if expected == kind:
                        continue
                raise ContractError(
                    f"{module_name}: {kind} {requirement.port} is used as data, "
                    "gating, or a forwarded output")

    def validate_hierarchy(self, design: dict[str, Any]) -> dict[str, Any]:
        clock = self.requirement(self.top, "clock")
        reset = self.requirement(self.top, "reset")
        if clock is None:
            raise ContractError(f"{self.top}: vacuous design has no sequential state")
        expected_clock = Requirement(design["primary_clock"])
        expected_reset = Requirement(design["reset"], design["reset_active_low"])
        if clock != expected_clock:
            raise ContractError(
                f"{self.top}: unknown/second primary clock; elaborated={clock.port} "
                f"bound={expected_clock.port}")
        if reset != expected_reset:
            raise ContractError(
                f"{self.top}: reset policy differs; elaborated={reset} bound={expected_reset}")
        top_ports = self.vars(self.modules[self.top])
        actual_inputs = {name for name, port in top_ports.items()
                         if port.get("dir") == "input"}
        expected_inputs = {clock.port, reset.port, *design["synchronous_inputs"]}
        if actual_inputs != expected_inputs:
            raise ContractError(
                f"{self.top}: top input-domain inventory differs: "
                f"actual={sorted(actual_inputs)} expected={sorted(expected_inputs)}")
        for name, port in top_ports.items():
            if name == clock.port or "enable" in name.lower() or name.lower().endswith("_en"):
                continue
            if port.get("dir") in ("input", "output", "inout") and CLOCKISH.search(name):
                raise ContractError(f"{self.top}: unknown/forwarded clock-like top port {name}")
        for module_name in self.modules:
            module_clock = self.requirement(module_name, "clock")
            module_reset = self.requirement(module_name, "reset")
            if (module_clock is None) != (module_reset is None):
                raise ContractError(f"{module_name}: clock/reset coverage is incomplete")
            self.validate_special_uses(module_name, module_clock, module_reset)
        transfer = self.validate_transfer(design)
        drain = self.validate_drain_policy(design, transfer)
        return {
            "top": self.top,
            "reachable_modules": sorted(self.modules),
            "primary_clock": clock.port,
            "edge": "posedge",
            "clock_domains": 1,
            "reset": reset.port,
            "reset_active_low": reset.active_low,
            "reset_policy": "SYNCHRONOUS_ASSERT_SYNCHRONOUS_DEASSERT",
            "reset_assertion_precondition": f"{design['drain_output']} == 1",
            "drain_evidence": drain,
            "transfer": transfer,
        }

    def instance_by_name(self, module: ET.Element, name: str) -> ET.Element:
        matches = [node for node in self.instances(module) if node.get("name") == name]
        if len(matches) != 1:
            raise ContractError(f"{self.top}: expected exactly one direct instance {name!r}")
        return matches[0]

    def scope_module(self, design: dict[str, Any]) -> tuple[ET.Element, ET.Element]:
        top_module = self.modules[self.top]
        scope_instance = self.instance_by_name(top_module, design["transfer_scope"])
        scope_name = scope_instance.get("defName", "")
        scope = self.modules.get(scope_name)
        if scope is None:
            raise ContractError(f"{self.top}: transfer scope module is unknown")
        return scope_instance, scope

    @staticmethod
    def sequential_assignment(module: ET.Element, port_name: str, *, read: bool) -> bool:
        for block in XmlDesign.sequential_always(module):
            if read:
                sentree_refs = set(block.find("./sentree").iter("varref"))
                if any(ref.get("name") == port_name and ref not in sentree_refs
                       for ref in block.iter("varref")):
                    return True
                continue
            for assignment in block.findall(".//assigndly"):
                children = list(assignment)
                if len(children) < 2:
                    continue
                side = children[-1:]
                if any(ref.get("name") == port_name
                       for node in side for ref in node.iter("varref")):
                    return True
        return False

    @staticmethod
    def has_continuous_driver(module: ET.Element, port_name: str) -> bool:
        for assignment in module.findall(".//contassign"):
            children = list(assignment)
            if children and any(ref.get("name") == port_name
                                for ref in children[-1].iter("varref")):
                return True
        return False

    @staticmethod
    def reset_assigns_zero(module: ET.Element, port_name: str) -> bool:
        for block in XmlDesign.sequential_always(module):
            body = [child for child in block if child.tag != "sentree"]
            container = body[0] if len(body) == 1 else None
            statements = list(container) if container is not None and container.tag == "begin" else []
            if not statements or statements[0].tag != "if" or len(statements[0]) < 2:
                continue
            reset_branch = statements[0][1]
            for assignment in reset_branch.findall(".//assigndly"):
                children = list(assignment)
                if len(children) < 2:
                    continue
                lhs = {ref.get("name", "") for ref in children[-1].iter("varref")}
                constants = list(children[0].iter("const"))
                if (port_name in lhs and len(constants) == 1
                        and constants[0].get("name", "").endswith("'h0")):
                    return True
        return False

    def validate_transfer(self, design: dict[str, Any]) -> dict[str, Any]:
        _, scope_module = self.scope_module(design)
        tx = self.instance_by_name(scope_module, design["tx_instance"])
        rx = self.instance_by_name(scope_module, design["rx_instance"])
        tx_module_name = tx.get("defName", "")
        rx_module_name = rx.get("defName", "")
        if tx_module_name == rx_module_name and tx.get("name") == rx.get("name"):
            raise ContractError(f"{self.top}: TX/RX roles collapse")
        tx_module = self.modules.get(tx_module_name)
        rx_module = self.modules.get(rx_module_name)
        if tx_module is None or rx_module is None:
            raise ContractError(f"{self.top}: TX/RX module is unknown")
        if self.requirement(tx_module_name, "clock") is None or self.requirement(rx_module_name, "clock") is None:
            raise ContractError(f"{self.top}: TX and RX must both contain registered state")
        observations = []
        permitted: dict[str, set[tuple[str, str]]] = {}
        for channel_index, channel in enumerate(design["channels"]):
            tx_port = next((p for p in tx.findall("./port")
                            if p.get("name") == channel["tx_port"]), None)
            rx_port = next((p for p in rx.findall("./port")
                            if p.get("name") == channel["rx_port"]), None)
            if tx_port is None or tx_port.get("direction") != "out":
                raise ContractError(f"{self.top}: TX output port {channel['tx_port']} is absent")
            if rx_port is None or rx_port.get("direction") != "in":
                raise ContractError(f"{self.top}: RX input port {channel['rx_port']} is absent")
            tx_expr = self.port_expression(tx_port)
            rx_expr = self.port_expression(rx_port)
            tx_net = self.direct_var(tx_expr)
            rx_net = self.direct_var(rx_expr)
            if tx_net is None or tx_net != rx_net:
                raise ContractError(
                    f"{self.top}: TX/RX channel {channel['tx_port']}->{channel['rx_port']} "
                    "does not share one direct net")
            if tx_expr.get("dtype_id") != rx_expr.get("dtype_id"):
                raise ContractError(f"{self.top}: TX/RX channel width/type differs for {tx_net}")
            if tx_net in (design["primary_clock"], design["reset"]):
                raise ContractError(f"{self.top}: event channel aliases clock/reset")
            if not self.sequential_assignment(tx_module, channel["tx_port"], read=False):
                raise ContractError(
                    f"{tx_module_name}: TX port {channel['tx_port']} is not registered")
            if self.has_continuous_driver(tx_module, channel["tx_port"]):
                raise ContractError(
                    f"{tx_module_name}: TX port {channel['tx_port']} has a combinational driver")
            if channel_index == 0 and not self.reset_assigns_zero(
                    tx_module, channel["tx_port"]):
                raise ContractError(
                    f"{tx_module_name}: TX valid is not reset to quiescent zero")
            if not self.sequential_assignment(rx_module, channel["rx_port"], read=True):
                raise ContractError(
                    f"{rx_module_name}: RX port {channel['rx_port']} is not synchronously sampled")
            permitted.setdefault(tx_net, set()).update({
                (tx.get("name", ""), channel["tx_port"]),
                (rx.get("name", ""), channel["rx_port"]),
            })
            observations.append({"net": tx_net, "is_valid": channel_index == 0,
                                 **channel})
        if not self.reset_assigns_zero(rx_module, design["rx_pending_port"]):
            raise ContractError(f"{rx_module_name}: RX pending state is not reset to zero")
        parents = self.parent_map(scope_module)
        for net, allowed in permitted.items():
            actual: set[tuple[str, str]] = set()
            for ref in scope_module.findall(f".//varref[@name='{net}']"):
                port = self.ancestor(ref, parents, "port")
                instance = self.ancestor(ref, parents, "instance")
                if port is None or instance is None:
                    assignment = None
                    current = ref
                    while current in parents:
                        current = parents[current]
                        if current.tag in ("contassign", "assign", "assigndly"):
                            assignment = current
                            break
                    valid_net = observations[0]["net"]
                    if assignment is not None and net == valid_net:
                        children = list(assignment)
                        lhs = ({item.get("name", "")
                                for item in children[-1].iter("varref")}
                               if children else set())
                        if design["scope_drain_port"] in lhs:
                            continue
                    raise ContractError(f"{self.top}: channel net {net} has a bypass/fanout driver")
                actual.add((instance.get("name", ""), port.get("name", "")))
            if actual != allowed:
                raise ContractError(
                    f"{self.top}: channel net {net} connectivity differs: {sorted(actual)}")
        return {
            "kind": "REGISTERED_TX_TO_REGISTERED_RX_SAME_EDGE",
            "scope_instance": design["transfer_scope"],
            "tx_instance": design["tx_instance"],
            "rx_instance": design["rx_instance"],
            "channels": observations,
        }

    @staticmethod
    def assignment_rhs_refs(module: ET.Element, target: str) -> set[str]:
        refs: set[str] = set()
        for tag in ("contassign", "assign", "assigndly"):
            for assignment in module.findall(f".//{tag}"):
                children = list(assignment)
                if len(children) < 2:
                    continue
                lhs_names = {ref.get("name", "") for ref in children[-1].iter("varref")}
                if target in lhs_names:
                    refs.update(ref.get("name", "")
                                for child in children[:-1] for ref in child.iter("varref"))
        return refs

    @staticmethod
    def assignment_rhs(module: ET.Element, target: str) -> ET.Element | None:
        matches = []
        for tag in ("contassign", "assign", "assigndly"):
            for assignment in module.findall(f".//{tag}"):
                children = list(assignment)
                if len(children) < 2:
                    continue
                lhs = {ref.get("name", "") for ref in children[-1].iter("varref")}
                if target in lhs:
                    matches.append(children[0])
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def true_requires(node: ET.Element, signal: str, value: bool) -> bool:
        """Conservatively prove `node == 1` implies `signal == value`."""
        if value and node.tag == "varref" and node.get("name") == signal:
            return True
        if not value and node.tag == "not" and len(node) == 1:
            return node[0].tag == "varref" and node[0].get("name") == signal
        if not value and node.tag == "eq" and len(node) == 2:
            for variable, constant in ((node[0], node[1]), (node[1], node[0])):
                if (variable.tag == "varref" and variable.get("name") == signal
                        and constant.tag == "const"
                        and constant.get("name", "").endswith("'h0")):
                    return True
        if node.tag == "and":
            return any(XmlDesign.true_requires(child, signal, value) for child in node)
        if node.tag == "or":
            return len(node) > 0 and all(
                XmlDesign.true_requires(child, signal, value) for child in node)
        return False

    def true_requires_in(self, module: ET.Element, node: ET.Element,
                         signal: str, value: bool,
                         seen: set[str] | None = None) -> bool:
        if self.true_requires(node, signal, value):
            return True
        if node.tag == "varref":
            name = node.get("name", "")
            visited = set() if seen is None else seen
            if name in visited:
                return False
            rhs = self.assignment_rhs(module, name)
            return (rhs is not None and self.true_requires_in(
                module, rhs, signal, value, visited | {name}))
        if node.tag == "and":
            return any(self.true_requires_in(module, child, signal, value, seen)
                       for child in node)
        if node.tag == "or":
            return len(node) > 0 and all(
                self.true_requires_in(module, child, signal, value, seen)
                for child in node)
        return False

    def validate_drain_policy(self, design: dict[str, Any],
                              transfer: dict[str, Any]) -> dict[str, Any]:
        top_module = self.modules[self.top]
        top_ports = self.vars(top_module)
        drain_output = design["drain_output"]
        if drain_output not in top_ports or top_ports[drain_output].get("dir") != "output":
            raise ContractError(f"{self.top}: drain output {drain_output!r} is absent")
        scope_instance, scope_module = self.scope_module(design)
        scope_port = next((port for port in scope_instance.findall("./port")
                           if port.get("name") == design["scope_drain_port"]), None)
        if scope_port is None or scope_port.get("direction") != "out":
            raise ContractError(f"{self.top}: endpoint drain port is absent")
        scope_net = self.direct_var(self.port_expression(scope_port))
        if scope_net is None:
            raise ContractError(f"{self.top}: endpoint drain connection is not direct")
        top_drain_refs = self.assignment_rhs_refs(top_module, drain_output)
        if scope_net not in top_drain_refs:
            raise ContractError(
                f"{self.top}: top drain does not depend on endpoint drain state")
        top_drain_rhs = self.assignment_rhs(top_module, drain_output)
        if top_drain_rhs is None or not self.true_requires_in(
                top_module, top_drain_rhs, scope_net, True):
            raise ContractError(
                f"{self.top}: top drain does not conservatively require endpoint idle")
        for signal in design["top_drain_require_one"]:
            if not self.true_requires_in(top_module, top_drain_rhs, signal, True):
                raise ContractError(f"{self.top}: clean drain does not require {signal}")
        for signal in design["top_drain_require_zero"]:
            if not self.true_requires_in(top_module, top_drain_rhs, signal, False):
                raise ContractError(f"{self.top}: clean drain does not require !{signal}")
        scope_ports = self.vars(scope_module)
        scope_drain = design["scope_drain_port"]
        if scope_drain not in scope_ports or scope_ports[scope_drain].get("dir") != "output":
            raise ContractError(f"{self.top}: scope drain output is absent")
        scope_drain_refs = self.assignment_rhs_refs(scope_module, scope_drain)
        valid_net = transfer["channels"][0]["net"]
        rx = self.instance_by_name(scope_module, design["rx_instance"])
        rx_pending = next((port for port in rx.findall("./port")
                           if port.get("name") == design["rx_pending_port"]), None)
        if rx_pending is None or rx_pending.get("direction") != "out":
            raise ContractError(f"{self.top}: RX retirement-pending port is absent")
        pending_net = self.direct_var(self.port_expression(rx_pending))
        if pending_net is None:
            raise ContractError(f"{self.top}: RX retirement-pending connection is not direct")
        missing = {valid_net, pending_net} - scope_drain_refs
        if missing:
            raise ContractError(
                f"{self.top}: endpoint drain omits TX/RX in-flight state {sorted(missing)}")
        scope_drain_rhs = self.assignment_rhs(scope_module, scope_drain)
        if scope_drain_rhs is None:
            raise ContractError(f"{self.top}: endpoint drain has ambiguous drivers")
        for state_net in (valid_net, pending_net):
            if not self.true_requires_in(scope_module, scope_drain_rhs, state_net, False):
                raise ContractError(
                    f"{self.top}: endpoint drain can assert with in-flight state {state_net}")
        scope_error = design["scope_error_port"]
        if not self.true_requires_in(scope_module, scope_drain_rhs, scope_error, False):
            raise ContractError(
                f"{self.top}: endpoint clean drain does not require !{scope_error}")
        return {
            "top_output": drain_output,
            "scope_output": scope_drain,
            "covers_tx_valid_net": valid_net,
            "covers_rx_pending_net": pending_net,
            "requires_no_protocol_error": design["clean_drain_error"],
            "policy": "DRAIN_BEFORE_RESET_ASSERTION_REQUIRED",
        }


def elaborate(verilator: str, repo: Path, sources: list[Path], design: dict[str, Any],
              work: Path) -> tuple[XmlDesign, str]:
    check_source_constructs(sources)
    xml = work / f"{design['top']}.xml"
    command = [verilator, "--xml-only", "--xml-output", str(xml),
               "--top-module", design["top"], "-DSYNTHESIS", "-Wno-fatal",
               *map(str, sources)]
    process = subprocess.run(command, cwd=repo, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, check=False)
    log = process.stdout
    if process.returncode != 0 or not xml.is_file():
        tail = "\n".join(log.splitlines()[-20:])
        if "Cannot find file containing module" in log or "Can't find definition of module" in log:
            reason = "unknown module"
        elif "Unsupported" in log and "clock" in log.lower():
            reason = "unknown clock"
        else:
            reason = "elaboration failed"
        raise ContractError(f"{design['top']}: {reason}:\n{tail}")
    # Verilator records absolute source paths.  Remove only this invocation's
    # random temp prefix so equivalent elaborations have a stable fingerprint.
    normalized_xml = xml.read_bytes().replace(str(work).encode(), b"<WORK>")
    return XmlDesign(xml, design["top"], log), hashlib.sha256(normalized_xml).hexdigest()


def verify_bound(repo: Path, binding_path: Path, verilator_request: str | None,
                 required_commit: str | None = None) -> dict[str, Any]:
    if required_commit == CANONICAL_COMMIT:
        try:
            binding_digest = hashlib.sha256(binding_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ContractError(f"cannot read canonical binding: {exc}") from exc
        if binding_digest != CANONICAL_BINDING_SHA256:
            raise ContractError("binding document SHA-256 differs from canonical source set")
    binding = load_json(binding_path)
    blobs, designs = validate_binding(binding, repo, required_commit)
    verilator = find_verilator(verilator_request)
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="redred-single-edge-") as directory:
        work = Path(directory)
        source_dir = work / "sources"
        source_dir.mkdir()
        sources: list[Path] = []
        for index, (relative, data) in enumerate(blobs):
            destination = source_dir / f"{index:02d}_{Path(relative).name}"
            destination.write_bytes(data)
            sources.append(destination)
        for name in ("a2", "a3"):
            parsed, xml_sha = elaborate(verilator, repo, sources, designs[name], work)
            evidence = parsed.validate_hierarchy(designs[name])
            evidence["elaboration_xml_sha256"] = xml_sha
            results[name] = evidence
    return {
        "status": "PASS",
        "source_set_id": binding["source_set_id"],
        "repository_commit": binding["repository_commit"],
        "integration_commit": binding["integration_commit"],
        "source_sha256": {
            item["path"]: item["sha256"] for item in binding["files"]
        },
        "designs": results,
        "analyzer": subprocess.run([verilator, "--version"], text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   check=False).stdout.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=here / "contract.json")
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--repo", type=Path, default=here.parents[1])
    parser.add_argument("--verilator")
    args = parser.parse_args(argv)
    receipt: dict[str, Any] = {
        "schema": "redred-single-edge-cdc-rdc-receipt-v1",
        "status": "FAIL",
        "diagnostic": None,
    }
    try:
        contract = load_json(args.contract)
        source_set = validate_contract(contract)
        if args.binding is None and source_set is None:
            receipt["status"] = HOLD
            receipt["diagnostic"] = "A2 source set is not bound"
            print(json.dumps(receipt, indent=2, sort_keys=True))
            print("REDRED_SINGLE_EDGE_CDC_RDC_HOLD reason=A2_SOURCE_SET_UNBOUND")
            return 0
        if args.binding is not None:
            binding_path = args.binding
            required_commit = source_set["repository_commit"] if source_set else None
        else:
            assert source_set is not None
            binding_path = args.contract.resolve().parent / source_set["binding"]
            required_commit = source_set["repository_commit"]
        evidence = verify_bound(args.repo, binding_path, args.verilator, required_commit)
        receipt.update(evidence)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        print("REDRED_SINGLE_EDGE_CDC_RDC_PASS designs=a2,a3 domains=1")
        return 0
    except (ContractError, OSError) as exc:
        receipt["diagnostic"] = str(exc)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        print(f"REDRED_SINGLE_EDGE_CDC_RDC_FAIL reason={exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
