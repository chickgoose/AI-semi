#!/usr/bin/env python3
"""Produce trusted canonical full-link hierarchy and feature inventory.

The producer is intentionally candidate-neutral.  It derives candidate module
instances from the mapped structural Verilog, binds each module to the verified
source bundle, and uses the flow hierarchy export only for accounting kinds and
stable block names.  Missing hierarchy rows and extra candidate-module
instances are fatal rather than silently omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
FEATURE_CATEGORY = {
    "codec": "codec",
    "encoder": "codec",
    "decoder": "codec",
    "serializer": "serializer",
    "deserializer": "deserializer",
    "buffer": "buffer",
    "cdc": "cdc",
    "clocking": "cdc",
    "normalizer": "normalizer",
    "adapter": "normalizer",
}
MODULE_RE = re.compile(
    r"\bmodule\s+([A-Za-z_$][\w$]*)\b[^;]*;(.*?)\bendmodule\b", re.S
)
INSTANCE_RE = re.compile(
    r"(?:^|;)\s*([A-Za-z_$][\w$]*)\s*"
    r"(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    r"([A-Za-z_$][\w$]*)\s*\(",
    re.M | re.S,
)
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


class InventoryError(ValueError):
    """Raised when producer inputs cannot close to one canonical inventory."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{label} must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"{label} must be a JSON object")
    return value


def _strict(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise InventoryError(
            f"{label} keys mismatch: missing={sorted(keys - set(value))!r}, "
            f"extra={sorted(set(value) - keys)!r}"
        )


def _modules(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryError(f"{label} must be UTF-8 structural Verilog") from exc
    text = COMMENT_RE.sub("", text)
    modules: dict[str, str] = {}
    for match in MODULE_RE.finditer(text):
        name, body = match.groups()
        if name in modules:
            raise InventoryError(f"{label} defines module {name!r} more than once")
        modules[name] = body
    if not modules:
        raise InventoryError(f"{label} contains no Verilog modules")
    return modules


def _instances(body: str) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in INSTANCE_RE.finditer(body)]


def _candidate_hierarchy(
    modules: dict[str, str], candidate_modules: set[str], top: str
) -> dict[str, str]:
    if top not in modules:
        raise InventoryError(f"mapped netlist does not define synthesis_top {top!r}")
    found: dict[str, str] = {}

    def walk(module: str, hierarchy_path: str, ancestors: tuple[str, ...]) -> None:
        if module in ancestors:
            raise InventoryError(f"recursive candidate hierarchy through {module!r}")
        for child_module, instance in _instances(modules[module]):
            if child_module not in candidate_modules:
                continue
            child_path = f"{hierarchy_path}.{instance}"
            if child_path in found:
                raise InventoryError(f"duplicate hierarchy path {child_path!r}")
            found[child_path] = child_module
            if child_module in modules:
                walk(child_module, child_path, ancestors + (module,))

    walk(top, top, ())
    return found


def produce_inventory(
    *,
    bundle_data: bytes,
    filelist_data: bytes,
    mapped_netlist_data: bytes,
    hierarchy_source_data: bytes,
    synthesis_command_data: bytes,
    input_paths: dict[str, str],
    source_loader: Callable[[str], bytes],
    output_path: str,
    generator_sha256: str,
) -> dict[str, Any]:
    """Return the canonical JSON value emitted by the trusted producer."""

    required_roles = {
        "bundle_inventory", "filelist", "mapped_netlist", "hierarchy_source",
        "synthesis_command",
    }
    if set(input_paths) != required_roles:
        raise InventoryError("producer input roles are incomplete")
    bundle = _json(bundle_data, "bundle inventory")
    _strict(bundle, {"schema_version", "files"}, "bundle inventory")
    if bundle.get("schema_version") != 1 or not isinstance(bundle.get("files"), list):
        raise InventoryError("bundle inventory schema_version/files are invalid")
    try:
        filelist = filelist_data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise InventoryError("filelist must be UTF-8") from exc

    synthesis = _json(synthesis_command_data, "synthesis command")
    _strict(
        synthesis,
        {
            "schema_version", "synthesis_top", "command", "filelist",
            "tool_config", "sdc", "mapped_netlist", "hierarchy_source",
            "include_files", "generated_ip", "libraries",
        },
        "synthesis command",
    )
    if synthesis.get("schema_version") != 1:
        raise InventoryError("synthesis command schema_version must equal 1")
    command = synthesis.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(token, str) and token for token in command)
    ):
        raise InventoryError("synthesis command must be a nonempty string array")

    nested_inputs: list[tuple[str, str, bytes]] = []

    def bind_artifact(role: str, value: Any) -> bytes:
        if not isinstance(value, dict):
            raise InventoryError(f"synthesis command {role} must be an artifact")
        _strict(value, {"path", "sha256"}, f"synthesis command {role}")
        path = value.get("path")
        if not isinstance(path, str) or not path:
            raise InventoryError(f"synthesis command {role}.path is invalid")
        data = source_loader(path)
        if sha256(data) != value.get("sha256"):
            raise InventoryError(f"synthesis command {role} digest mismatch")
        if path not in command:
            raise InventoryError(
                f"synthesis command does not contain bound {role} path {path!r}"
            )
        if role not in {"filelist", "mapped_netlist", "hierarchy_source"}:
            nested_inputs.append((role, path, data))
        return data

    bound_filelist = bind_artifact("filelist", synthesis.get("filelist"))
    if bound_filelist != filelist_data:
        raise InventoryError("synthesis command filelist differs from candidate filelist")
    bind_artifact("tool_config", synthesis.get("tool_config"))
    bind_artifact("sdc", synthesis.get("sdc"))
    if bind_artifact("mapped_netlist", synthesis.get("mapped_netlist")) != mapped_netlist_data:
        raise InventoryError("synthesis command mapped netlist binding differs from input")
    if bind_artifact("hierarchy_source", synthesis.get("hierarchy_source")) != hierarchy_source_data:
        raise InventoryError("synthesis command hierarchy binding differs from input")
    closure_artifacts: dict[str, list[tuple[str, bytes]]] = {}
    for group in ("include_files", "generated_ip", "libraries"):
        values = synthesis.get(group)
        if not isinstance(values, list):
            raise InventoryError(f"synthesis command {group} must be an array")
        closure_artifacts[group] = []
        for index, value in enumerate(values):
            data = bind_artifact(f"{group}[{index}]", value)
            closure_artifacts[group].append((value["path"], data))

    source_paths: list[str] = []
    module_sources: dict[str, list[str]] = {}
    for index, entry in enumerate(bundle["files"]):
        if not isinstance(entry, dict):
            raise InventoryError(f"bundle files[{index}] must be an object")
        _strict(entry, {"path", "sha256"}, f"bundle files[{index}]")
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise InventoryError(f"bundle files[{index}].path is invalid")
        data = source_loader(path)
        if sha256(data) != entry.get("sha256"):
            raise InventoryError(f"bundle source digest mismatch for {path!r}")
        source_paths.append(path)
        for module in _modules(data, path):
            module_sources.setdefault(module, []).append(path)
    if source_paths != filelist:
        raise InventoryError("bundle inventory and filelist order differ")
    for path, data in closure_artifacts["generated_ip"]:
        for module in _modules(data, path):
            module_sources.setdefault(module, []).append(path)

    mapped_modules = _modules(mapped_netlist_data, "mapped netlist")
    hierarchy = _json(hierarchy_source_data, "hierarchy source")
    _strict(
        hierarchy, {"schema_version", "synthesis_top", "blocks"},
        "hierarchy source",
    )
    if hierarchy.get("schema_version") != 1:
        raise InventoryError("hierarchy source schema_version must equal 1")
    top = hierarchy.get("synthesis_top")
    if not isinstance(top, str) or not top:
        raise InventoryError("hierarchy source synthesis_top is invalid")
    if synthesis.get("synthesis_top") != top:
        raise InventoryError("synthesis command top differs from hierarchy source top")
    discovered = _candidate_hierarchy(mapped_modules, set(module_sources), top)

    declared_by_path: dict[str, dict[str, str]] = {}
    blocks_value = hierarchy.get("blocks")
    if not isinstance(blocks_value, list):
        raise InventoryError("hierarchy source blocks must be an array")
    for index, raw in enumerate(blocks_value):
        if not isinstance(raw, dict):
            raise InventoryError(f"hierarchy source blocks[{index}] must be an object")
        _strict(raw, {"name", "kind", "hierarchy_path", "module"},
                f"hierarchy source blocks[{index}]")
        if not all(isinstance(raw.get(key), str) and raw[key] for key in raw):
            raise InventoryError(f"hierarchy source blocks[{index}] fields are invalid")
        path = raw["hierarchy_path"]
        if path in declared_by_path:
            raise InventoryError(f"hierarchy source duplicates {path!r}")
        declared_by_path[path] = raw
    if set(declared_by_path) != set(discovered):
        raise InventoryError(
            "hierarchy source does not cover every candidate-module instance "
            f"(missing={sorted(set(discovered) - set(declared_by_path))!r}, "
            f"extra={sorted(set(declared_by_path) - set(discovered))!r})"
        )

    blocks = []
    features = []
    seen_names: set[str] = set()
    for path in sorted(discovered):
        row = declared_by_path[path]
        module = discovered[path]
        if row["module"] != module:
            raise InventoryError(f"hierarchy module mismatch at {path!r}")
        name = row["name"]
        if name in seen_names:
            raise InventoryError(f"hierarchy source duplicates block name {name!r}")
        seen_names.add(name)
        kind = row["kind"]
        source_files = sorted(module_sources[module])
        block = {
            "name": name,
            "kind": kind,
            "top": module,
            "hierarchy_path": path,
            "source_files": source_files,
        }
        blocks.append(block)
        category = FEATURE_CATEGORY.get(kind)
        if category is not None:
            features.append({
                "name": name,
                "category": category,
                "charged_block": name,
                "hierarchy_path": path,
            })

    producer_command = [
        "python3", "generate_full_link_inventory.py",
        "--bundle-inventory", input_paths["bundle_inventory"],
        "--filelist", input_paths["filelist"],
        "--mapped-netlist", input_paths["mapped_netlist"],
        "--hierarchy-source", input_paths["hierarchy_source"],
        "--synthesis-command", input_paths["synthesis_command"],
        "--output", output_path,
    ]
    inputs = [
        {"role": role, "path": input_paths[role], "sha256": sha256(data)}
        for role, data in (
            ("bundle_inventory", bundle_data),
            ("filelist", filelist_data),
            ("mapped_netlist", mapped_netlist_data),
            ("hierarchy_source", hierarchy_source_data),
            ("synthesis_command", synthesis_command_data),
        )
    ]
    inputs.extend(
        {"role": role, "path": path, "sha256": sha256(data)}
        for role, path, data in nested_inputs
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "producer": {
            "tool": "a8-flow-owned-full-link-inventory",
            "generator_sha256": generator_sha256,
            "command": producer_command,
            "inputs": inputs,
        },
        "synthesis_top": top,
        "blocks": blocks,
        "features": features,
    }


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InventoryError(f"cannot read {path}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-inventory", required=True)
    parser.add_argument("--filelist", required=True)
    parser.add_argument("--mapped-netlist", required=True)
    parser.add_argument("--hierarchy-source", required=True)
    parser.add_argument("--synthesis-command", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    paths = {
        "bundle_inventory": args.bundle_inventory,
        "filelist": args.filelist,
        "mapped_netlist": args.mapped_netlist,
        "hierarchy_source": args.hierarchy_source,
        "synthesis_command": args.synthesis_command,
    }
    generator_digest = sha256(Path(__file__).read_bytes())
    try:
        result = produce_inventory(
            bundle_data=_read(Path(args.bundle_inventory)),
            filelist_data=_read(Path(args.filelist)),
            mapped_netlist_data=_read(Path(args.mapped_netlist)),
            hierarchy_source_data=_read(Path(args.hierarchy_source)),
            synthesis_command_data=_read(Path(args.synthesis_command)),
            input_paths=paths,
            source_loader=lambda path: _read(Path(path)),
            output_path=args.output,
            generator_sha256=generator_digest,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (InventoryError, OSError) as exc:
        print(f"NOT_PRODUCED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
