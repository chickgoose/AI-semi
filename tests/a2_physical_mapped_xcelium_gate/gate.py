#!/usr/bin/env python3
"""Fail-closed mapped-netlist Xcelium functional gate for A2/A3/A4 P6 tops."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


SCHEMA = "a2_physical_mapped_xcelium_manifest_v1"
ENDPOINTS = {
    "a2": "a2_batched_iwrr_p6_top",
    "a3": "a3_exact_scalar_prefix_k2_p6_top",
    "a4": "a4_paired_cortical_column_k2_p6_top",
}
MODULE_RE = re.compile(
    r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"(?:#\s*\(.*?\)\s*)?(?:\((.*?)\))?\s*;(.*?)\bendmodule\b",
    re.DOTALL,
)
DECL_RE = re.compile(r"\b(input|output|inout)\b([^;]*);", re.DOTALL)
INSTANCE_RE = re.compile(
    r"(?:^|;)\s*([A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"(?:#\s*\(.*?\)\s*)?([A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"\((.*?)\)\s*;",
    re.DOTALL,
)
PIN_CONNECTION_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(")
SDF_DESIGN_RE = re.compile(r"\(\s*DESIGN\s+\"([^\"]+)\"", re.IGNORECASE)
SDF_ENTRY_RE = re.compile(r"\(\s*(?:IOPATH|INTERCONNECT|SETUP|HOLD|SETUPHOLD|RECREM)\b", re.IGNORECASE)
SDF_PROOF_PATTERNS = (
    re.compile(r"(?i)annotated\s*(?:=|:)\s*([0-9]+)"),
    re.compile(r"(?i)([0-9]+)\s+(?:path delays?|timing checks?)\s+annotated"),
    re.compile(r"(?i)sdf[^\n]*annotation[^\n]*count\s*(?:=|:)\s*([0-9]+)"),
)
CONSERVATION_RE = re.compile(
    r"^A2_MAPPED_XCELIUM_CONSERVATION_PASS\s+"
    r"endpoint=(a[234])\s+generated=([0-9]+)\s+overrun=([0-9]+)\s+"
    r"accepted=([0-9]+)\s+retired=([0-9]+)\s+phantom=([0-9]+)\s+"
    r"duplicate=([0-9]+)\s+order_errors=([0-9]+)$",
    re.MULTILINE,
)
PASS_RE = re.compile(r"^A2_MAPPED_XCELIUM_PASS\s+endpoint=(a[234])$", re.MULTILINE)


class GateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def declared_pins(header: str, body: str) -> set[str]:
    pins: set[str] = set()
    # ANSI headers may change direction/type on each comma-delimited port.
    for segment in header.split(","):
        if not re.search(r"\b(?:input|output|inout)\b", segment):
            continue
        payload = re.sub(r"\[[^\]]+\]", " ", segment)
        payload = re.sub(
            r"\b(?:input|output|inout|wire|wand|wor|tri|tri0|tri1|supply0|supply1|reg|logic|signed|unsigned)\b",
            " ", payload,
        )
        identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", payload)
        if identifiers:
            pins.add(identifiers[-1])
    # Traditional vendor models list names in the header and directions in the
    # body.  Body declarations may contain several comma-separated pins.
    for declaration in DECL_RE.finditer(body):
        payload = re.sub(r"\[[^\]]+\]", " ", declaration.group(2))
        payload = re.sub(
            r"\b(?:wire|wand|wor|tri|tri0|tri1|supply0|supply1|reg|logic|signed|unsigned)\b",
            " ", payload,
        )
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", payload):
            pins.add(token)
    return pins


def parse_modules(path: Path) -> dict[str, dict[str, Any]]:
    text = strip_comments(path.read_text(encoding="utf-8", errors="strict"))
    modules: dict[str, dict[str, Any]] = {}
    for match in MODULE_RE.finditer(text):
        name, header, body = match.groups()
        if name in modules:
            raise GateError(f"duplicate module {name} within {path}")
        modules[name] = {
            "pins": declared_pins(header or "", body),
            "body": body,
        }
    if not modules:
        raise GateError(f"no Verilog module found in {path}")
    return modules


def checked_artifact(root: Path, entry: Any, label: str) -> Path:
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
        raise GateError(f"{label} must contain exactly path and sha256")
    raw = entry["path"]
    expected = entry["sha256"]
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise GateError(f"{label}.path must be a safe relative path")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise GateError(f"{label}.sha256 must be lowercase SHA-256")
    path = root / raw
    if not path.is_file() or path.is_symlink():
        raise GateError(f"{label} is not a regular non-symlink file: {path}")
    if sha256(path) != expected:
        raise GateError(f"{label} SHA-256 mismatch: {path}")
    return path


def load_manifest(path: Path) -> tuple[dict[str, Any], Path]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GateError(f"cannot read manifest: {error}") from error
    required = {"schema", "endpoint", "netlist", "sdf", "testbench", "vendor_models"}
    if not isinstance(document, dict) or set(document) != required:
        raise GateError("manifest keys differ from the frozen schema")
    if document.get("schema") != SCHEMA:
        raise GateError("manifest schema mismatch")
    endpoint = document.get("endpoint")
    if endpoint not in ENDPOINTS:
        raise GateError("endpoint must be exactly a2, a3, or a4")
    return document, path.resolve().parent


def preflight(manifest_path: Path) -> dict[str, Any]:
    document, root = load_manifest(manifest_path)
    endpoint = document["endpoint"]
    expected_top = ENDPOINTS[endpoint]

    netlist_entry = document["netlist"]
    if not isinstance(netlist_entry, dict) or set(netlist_entry) != {"path", "sha256", "top"}:
        raise GateError("netlist must contain exactly path, sha256, and top")
    if netlist_entry.get("top") != expected_top:
        raise GateError(f"netlist top must be canonical {expected_top}")
    netlist_path = checked_artifact(
        root, {key: netlist_entry[key] for key in ("path", "sha256")}, "netlist"
    )

    tb_entry = document["testbench"]
    if not isinstance(tb_entry, dict) or set(tb_entry) != {"path", "sha256", "top", "dut_instance"}:
        raise GateError("testbench keys differ from the frozen schema")
    expected_tb_top = f"{expected_top}_mapped_tb"
    if tb_entry.get("top") != expected_tb_top or tb_entry.get("dut_instance") != "dut":
        raise GateError(f"testbench top/instance must be {expected_tb_top}/dut")
    testbench_path = checked_artifact(
        root, {key: tb_entry[key] for key in ("path", "sha256")}, "testbench"
    )

    sdf_entry = document["sdf"]
    if not isinstance(sdf_entry, dict) or set(sdf_entry) != {"path", "sha256", "design", "scope"}:
        raise GateError("sdf keys differ from the frozen schema")
    exact_scope = f"{expected_tb_top}.dut"
    if sdf_entry.get("design") != expected_top or sdf_entry.get("scope") != exact_scope:
        raise GateError(f"SDF design/scope must be {expected_top}/{exact_scope}")
    sdf_path = checked_artifact(
        root, {key: sdf_entry[key] for key in ("path", "sha256")}, "sdf"
    )

    vendor_entries = document["vendor_models"]
    if not isinstance(vendor_entries, list) or not vendor_entries:
        raise GateError("vendor_models must be a nonempty list")
    vendor_paths: list[Path] = []
    expected_vendor: dict[str, set[str]] = {}
    for index, entry in enumerate(vendor_entries):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "modules"}:
            raise GateError(f"vendor_models[{index}] keys differ from the frozen schema")
        vendor_paths.append(checked_artifact(
            root, {key: entry[key] for key in ("path", "sha256")},
            f"vendor_models[{index}]",
        ))
        modules = entry["modules"]
        if not isinstance(modules, dict) or not modules:
            raise GateError(f"vendor_models[{index}].modules must be nonempty")
        for module, pins in modules.items():
            if (not isinstance(module, str) or
                    not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", module) or
                    not isinstance(pins, list) or not pins or
                    any(not isinstance(pin, str) for pin in pins)):
                raise GateError(f"invalid vendor module/pin declaration for {module!r}")
            if module in expected_vendor:
                raise GateError(f"duplicate declared vendor module {module}")
            expected_vendor[module] = set(pins)
            if len(expected_vendor[module]) != len(pins):
                raise GateError(f"duplicate declared pin in vendor module {module}")

    all_sources = [netlist_path, testbench_path, *vendor_paths]
    parsed_by_path = {path: parse_modules(path) for path in all_sources}
    definitions = Counter(
        module for modules in parsed_by_path.values() for module in modules
    )
    duplicate_definitions = sorted(module for module, count in definitions.items() if count != 1)
    if duplicate_definitions:
        raise GateError(f"duplicate module definitions in compile closure: {duplicate_definitions}")

    netlist_modules = parsed_by_path[netlist_path]
    tb_modules = parsed_by_path[testbench_path]
    if set(netlist_modules) != {expected_top}:
        raise GateError("mapped netlist must define exactly the canonical endpoint top")
    if set(tb_modules) != {expected_tb_top}:
        raise GateError("testbench must define exactly the canonical mapped TB top")
    tb_instances = [
        (kind, instance) for kind, instance, _ in INSTANCE_RE.findall(
            ";" + tb_modules[expected_tb_top]["body"]
        )
    ]
    if tb_instances.count((expected_top, "dut")) != 1:
        raise GateError("testbench must instantiate the canonical endpoint exactly once as dut")

    actual_vendor: dict[str, set[str]] = {}
    for path in vendor_paths:
        for module, parsed in parsed_by_path[path].items():
            actual_vendor[module] = parsed["pins"]
    if set(actual_vendor) != set(expected_vendor):
        raise GateError("vendor-model module set differs from manifest preflight declaration")
    for module, expected_pins in expected_vendor.items():
        if actual_vendor[module] != expected_pins:
            raise GateError(
                f"vendor model {module} pins differ: expected={sorted(expected_pins)} "
                f"actual={sorted(actual_vendor[module])}"
            )

    instantiated_vendor: set[str] = set()
    for cell_type, instance, connections in INSTANCE_RE.findall(
            ";" + netlist_modules[expected_top]["body"]):
        if cell_type not in actual_vendor:
            raise GateError(f"mapped instance {instance} has unresolved vendor cell {cell_type}")
        pins = PIN_CONNECTION_RE.findall(connections)
        if not pins or len(pins) != len(set(pins)):
            raise GateError(f"mapped instance {instance} must use unique named pin connections")
        unknown = sorted(set(pins) - actual_vendor[cell_type])
        if unknown:
            raise GateError(f"mapped instance {instance} uses unknown pins {unknown}")
        instantiated_vendor.add(cell_type)
    if instantiated_vendor != set(actual_vendor):
        raise GateError("every declared vendor module must be instantiated by the mapped netlist")

    sdf_text = sdf_path.read_text(encoding="utf-8", errors="strict")
    designs = SDF_DESIGN_RE.findall(sdf_text)
    if designs != [expected_top]:
        raise GateError("SDF must contain exactly one matching DESIGN declaration")
    sdf_entries = len(SDF_ENTRY_RE.findall(sdf_text))
    if sdf_entries <= 0:
        raise GateError("SDF contains zero annotatable path/timing entries")

    return {
        "endpoint": endpoint,
        "top": expected_top,
        "tb_top": expected_tb_top,
        "scope": exact_scope,
        "netlist": netlist_path,
        "testbench": testbench_path,
        "sdf": sdf_path,
        "vendor_models": vendor_paths,
        "sdf_entries": sdf_entries,
        "artifact_sha256": {
            "manifest": sha256(manifest_path),
            "netlist": sha256(netlist_path),
            "testbench": sha256(testbench_path),
            "sdf": sha256(sdf_path),
            "vendor_models": [sha256(path) for path in vendor_paths],
        },
    }


def parse_transcript(text: str, endpoint: str) -> dict[str, int]:
    proof_counts = [
        int(match.group(1))
        for pattern in SDF_PROOF_PATTERNS
        for match in pattern.finditer(text)
    ]
    if not proof_counts or max(proof_counts) <= 0:
        raise GateError("transcript lacks nonzero Xcelium SDF annotation proof")
    conservation = CONSERVATION_RE.findall(text)
    if len(conservation) != 1:
        raise GateError("transcript must contain exactly one conservation sentinel")
    observed_endpoint, *raw_numbers = conservation[0]
    if observed_endpoint != endpoint:
        raise GateError("conservation endpoint differs from manifest")
    generated, overrun, accepted, retired, phantom, duplicate, order_errors = map(
        int, raw_numbers
    )
    if generated <= 0 or accepted <= 0:
        raise GateError("conservation transcript must exercise nonzero traffic")
    if generated != overrun + accepted:
        raise GateError("transcript violates generated=overrun+accepted")
    if accepted != retired:
        raise GateError("transcript violates accepted=retired")
    if phantom or duplicate or order_errors:
        raise GateError("transcript reports phantom, duplicate, or order errors")
    passes = PASS_RE.findall(text)
    if passes != [endpoint]:
        raise GateError("transcript must contain exactly one matching final PASS sentinel")
    return {
        "sdf_annotated": max(proof_counts),
        "generated": generated,
        "overrun": overrun,
        "accepted": accepted,
        "retired": retired,
        "phantom": phantom,
        "duplicate": duplicate,
        "order_errors": order_errors,
    }


def execute(manifest: Path, xrun: Path, work: Path, output: Path) -> dict[str, Any]:
    if work.exists() or output.exists():
        raise GateError("work and output paths must not already exist")
    if not xrun.is_file() or xrun.is_symlink():
        raise GateError("xrun must be a regular non-symlink executable")
    identity = preflight(manifest)
    work.mkdir(parents=True)
    sdf_log = work / "sdf-annotation.log"
    sdf_command = work / "annotation.sdf_cmd"
    sdf_command.write_text(
        f'COMPILED_SDF_FILE = "{identity["sdf"]}";\n'
        f'SCOPE = {identity["scope"]};\n'
        'MTM_CONTROL = "MAXIMUM";\n'
        f'LOG_FILE = "{sdf_log}";\n',
        encoding="ascii",
    )
    transcript = work / "xrun.log"
    command = [
        str(xrun), "-64bit", "-sv", "-timescale", "1ns/1ps",
        "-top", identity["tb_top"], "-sdf_cmd_file", str(sdf_command),
        *(str(path) for path in identity["vendor_models"]),
        str(identity["netlist"]), str(identity["testbench"]),
    ]
    process = subprocess.run(
        command, cwd=work, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    transcript.write_text(process.stdout, encoding="utf-8")
    if process.returncode:
        raise GateError(f"Xcelium failed with exit {process.returncode}")
    combined = process.stdout
    if sdf_log.is_file():
        combined += "\n" + sdf_log.read_text(encoding="utf-8", errors="strict")
    conservation = parse_transcript(combined, identity["endpoint"])
    result = {
        "schema": "a2_physical_mapped_xcelium_result_v1",
        "status": "PASS",
        "endpoint": identity["endpoint"],
        "canonical_top": identity["top"],
        "testbench_top": identity["tb_top"],
        "sdf_scope": identity["scope"],
        "preflight": {
            "vendor_module_pin_match": True,
            "duplicate_modules": 0,
            "sdf_annotatable_entries": identity["sdf_entries"],
        },
        "transcript": conservation,
        "provenance": {
            **identity["artifact_sha256"],
            "xrun_sha256": sha256(xrun),
            "transcript_sha256": sha256(transcript),
        },
        "scope": "mapped_Xcelium_functional_only",
        "physical_qualification": "HOLD",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--xrun", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = execute(args.manifest.resolve(), args.xrun.resolve(),
                         args.work_dir.resolve(), args.output.resolve())
    except (GateError, OSError) as error:
        print(f"A2_MAPPED_XCELIUM_GATE_FAIL {error}", file=sys.stderr)
        return 2
    print(
        f"A2_MAPPED_XCELIUM_GATE_PASS endpoint={result['endpoint']} "
        f"accepted={result['transcript']['accepted']} "
        f"sdf_annotated={result['transcript']['sdf_annotated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
