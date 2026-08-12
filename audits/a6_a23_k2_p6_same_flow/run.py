#!/usr/bin/env python3
"""Reproduce complete A2/A3 normalized K2 and P6 Yosys proxies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from pathlib import PurePosixPath


AUDIT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = AUDIT_ROOT.parents[1]
REGISTRY = AUDIT_ROOT / "registry.json"
WRAPPERS = AUDIT_ROOT / "k2_wrappers.sv"
FLOW = (
    "read_verilog -sv -DSYNTHESIS <all exact sources>; "
    "hierarchy -check -top <top>; proc; opt; memory_map; opt; "
    "setundef -zero; opt; write_json hierarchy; flatten; opt; "
    "write_json generic; techmap; opt; "
    "abc -g simple; clean; check; stat; write_json mapped"
)
SEQ_MARKERS = ("DFF", "LATCH")
EXPECTED_YOSYS_SHA256 = "30aa795bec7533dac08bad56309edb6ac70dd33f017c28082d3c1dae1012112f"
EXPECTED_ABC_SHA256 = "21869d0f63b6a2962ad7e54044e7a694f6cc392db6443ad7bf70cdb8ad6ca16a"
EXPECTED_YOSYS_VERSION = "Yosys 0.52 (git sha1 fee39a3284c90249e1d9684cf6944ffbbcbb8f90)"


class AuditError(RuntimeError):
    pass


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def strict_json(path: Path) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        answer: dict[str, Any] = {}
        for key, value in pairs:
            if key in answer:
                raise AuditError(f"duplicate JSON key in registry: {key}")
            answer[key] = value
        return answer
    try:
        return json.loads(path.read_text(), object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as error:
        raise AuditError(f"malformed registry: {error}") from error


def git_bytes(commit: str, path: str) -> bytes:
    run = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if run.returncode:
        raise AuditError(
            f"source unavailable {commit}:{path}: "
            f"{run.stderr.decode(errors='replace').strip()}")
    return run.stdout


def resolve_commit(commit: str) -> str:
    run = subprocess.run(
        ["git", "rev-parse", commit], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if run.returncode:
        raise AuditError(f"commit unavailable: {commit}")
    return run.stdout.strip()


def sequential(cell_type: str) -> bool:
    upper = cell_type.upper()
    return any(marker in upper for marker in SEQ_MARKERS)


def width(cell: dict[str, Any]) -> int:
    value = cell.get("parameters", {}).get("WIDTH", "1")
    return int(value, 2) if isinstance(value, str) else int(value)


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def module_from(path: Path, top: str) -> dict[str, Any]:
    document = json.loads(path.read_text())
    try:
        return document["modules"][top]
    except KeyError as error:
        raise AuditError(f"top {top} absent from {path}") from error


def generic_metrics(path: Path, top: str) -> dict[str, Any]:
    module = module_from(path, top)
    cells = [cell for cell in module.get("cells", {}).values()
             if cell["type"] != "$scopeinfo"]
    types: dict[str, int] = {}
    for cell in cells:
        types[cell["type"]] = types.get(cell["type"], 0) + 1
    return {
        "cells": len(cells),
        "state_bits": sum(width(cell) for cell in cells
                          if sequential(cell["type"])),
        "cell_types": dict(sorted(types.items())),
    }


def hierarchy_metrics(path: Path, top: str) -> dict[str, Any]:
    document = json.loads(path.read_text())
    modules = document.get("modules", {})
    if top not in modules:
        raise AuditError(f"top {top} absent from hierarchy JSON")
    reachable: set[str] = set()
    pending = [top]
    instance_types: dict[str, int] = {}
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        for cell in modules[name].get("cells", {}).values():
            cell_type = cell["type"]
            if cell_type in modules:
                instance_types[cell_type] = instance_types.get(cell_type, 0) + 1
                pending.append(cell_type)
    return {
        "reachable_modules": sorted(reachable),
        "reachable_module_count": len(reachable),
        "hierarchical_instance_types": dict(sorted(instance_types.items())),
    }


def mapped_metrics(path: Path, top: str) -> dict[str, Any]:
    module = module_from(path, top)
    cells = {name: cell for name, cell in module.get("cells", {}).items()
             if cell["type"] != "$scopeinfo"}
    seq = {name: cell for name, cell in cells.items()
           if sequential(cell["type"])}
    comb = {name: cell for name, cell in cells.items()
            if not sequential(cell["type"])}

    primary_inputs: set[int] = set()
    primary_outputs: list[int] = []
    clock_reset_bits: set[int] = set()
    for name, port in module.get("ports", {}).items():
        bits = [bit for bit in port["bits"] if isinstance(bit, int)]
        if port["direction"] == "input":
            primary_inputs.update(bits)
            if re.search(r"(^|_)(clk|clock|rst|reset)(_|$)", name):
                clock_reset_bits.update(bits)
        else:
            primary_outputs.extend(bits)

    seq_outputs: set[int] = set()
    endpoints = list(primary_outputs)
    for cell in seq.values():
        for port, direction in cell["port_directions"].items():
            bits = [bit for bit in cell["connections"].get(port, [])
                    if isinstance(bit, int)]
            if direction == "output":
                seq_outputs.update(bits)
            elif port.upper() not in ("C", "CLK", "CK", "R", "RN", "S", "SN"):
                endpoints.extend(bits)

    drivers: dict[int, list[int]] = {}
    for cell in comb.values():
        inputs = [bit for port, direction in cell["port_directions"].items()
                  if direction == "input"
                  for bit in cell["connections"].get(port, [])
                  if isinstance(bit, int)]
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"].get(port, []):
                    if isinstance(bit, int):
                        drivers[bit] = inputs

    memo: dict[int, int] = {}
    visiting: set[int] = set()

    def depth(bit: int) -> int:
        if bit in primary_inputs or bit in seq_outputs or bit not in drivers:
            return 0
        if bit in memo:
            return memo[bit]
        if bit in visiting:
            raise AuditError(f"combinational loop reaches bit {bit}")
        visiting.add(bit)
        answer = 1 + max((depth(item) for item in drivers[bit]), default=0)
        visiting.remove(bit)
        memo[bit] = answer
        return answer

    fanout: dict[int, int] = {}
    sink_pins = 0
    for cell in cells.values():
        for port, direction in cell["port_directions"].items():
            if direction != "input":
                continue
            if sequential(cell["type"]) and port.upper() in (
                    "C", "CLK", "CK", "R", "RN", "S", "SN"):
                continue
            for bit in cell["connections"].get(port, []):
                if isinstance(bit, int):
                    fanout[bit] = fanout.get(bit, 0) + 1
                    sink_pins += 1
    for bit in primary_outputs:
        fanout[bit] = fanout.get(bit, 0) + 1
    values = [count for bit, count in fanout.items()
              if bit not in clock_reset_bits]

    types: dict[str, int] = {}
    for cell in cells.values():
        types[cell["type"]] = types.get(cell["type"], 0) + 1
    return {
        "cells": len(cells),
        "combinational_cells": len(comb),
        "state_bits": len(seq),
        "depth_levels": max((depth(bit) for bit in endpoints), default=0),
        "nets": len(fanout),
        "fanout_max": max(values, default=0),
        "fanout_p95": percentile(values, 0.95),
        "nets_fanout_ge16": sum(value >= 16 for value in values),
        "sink_pin_net_proxy": sink_pins,
        "cell_types": dict(sorted(types.items())),
    }


def tool_identity(argument: Path) -> tuple[Path, dict[str, str], dict[str, str]]:
    if argument.is_symlink():
        raise AuditError("Yosys entrypoint must not be a symlink")
    yosys = argument.resolve(strict=True)
    if not os.access(yosys, os.X_OK):
        raise AuditError(f"Yosys not executable: {yosys}")
    usr = yosys.parent.parent
    abc = yosys.parent / "yosys-abc"
    datdir = usr / "share/yosys"
    libdir = usr / "lib/x86_64-linux-gnu"
    for required in (abc, datdir, libdir):
        if not required.exists() or required.is_symlink():
            raise AuditError(f"tool dependency absent or symlinked: {required}")
    environment = os.environ.copy()
    environment["YOSYS_DATDIR"] = str(datdir.resolve())
    environment["LD_LIBRARY_PATH"] = str(libdir.resolve())
    version_run = subprocess.run(
        [str(yosys), "-V"], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if version_run.returncode:
        raise AuditError(f"Yosys version command failed: {version_run.stderr.strip()}")
    identity = {
        "yosys_path": str(yosys),
        "yosys_sha256": digest_file(yosys),
        "yosys_version": version_run.stdout.strip(),
        "abc_path": str(abc.resolve()),
        "abc_sha256": digest_file(abc),
        "datdir": str(datdir.resolve()),
    }
    if identity["yosys_sha256"] != EXPECTED_YOSYS_SHA256:
        raise AuditError("Yosys executable SHA is not the pinned same-flow tool")
    if identity["abc_sha256"] != EXPECTED_ABC_SHA256:
        raise AuditError("ABC executable SHA is not the pinned same-flow tool")
    if identity["yosys_version"] != EXPECTED_YOSYS_VERSION:
        raise AuditError("Yosys version is not the pinned same-flow version")
    return yosys, environment, identity


def classify_warnings(log: str) -> dict[str, int]:
    classes = {"abc_combinational_network": 0, "memory_to_register": 0}
    unknown = []
    for line in log.splitlines():
        if "Warning:" not in line:
            continue
        if "ABC: Warning: The network is combinational" in line:
            classes["abc_combinational_network"] += 1
        elif line.startswith("Warning: Replacing memory "):
            classes["memory_to_register"] += 1
        else:
            unknown.append(line.strip())
    if unknown:
        raise AuditError(f"unclassified Yosys warning(s): {unknown}")
    return {**classes, "unclassified": 0, "total": sum(classes.values())}


def synthesize(target: str, spec: dict[str, Any], sources: list[Path],
               yosys: Path, environment: dict[str, str], work: Path
               ) -> tuple[dict[str, Any], dict[str, int]]:
    work.mkdir(parents=True, exist_ok=False)
    generic = work / "generic.json"
    hierarchy = work / "hierarchy.json"
    mapped = work / "mapped.json"
    source_args = " ".join(str(path) for path in sources)
    script = "; ".join((
        f"read_verilog -sv -DSYNTHESIS {source_args}",
        f"hierarchy -check -top {spec['top']}", "proc", "opt",
        "memory_map", "opt", "setundef -zero", "opt",
        f"write_json {hierarchy}", "flatten", "opt", f"write_json {generic}",
        "techmap", "opt",
        "abc -g simple", "clean", "check", "stat", f"write_json {mapped}",
    ))
    run = subprocess.run(
        [str(yosys), "-Q", "-p", script], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    (work / "yosys.log").write_text(run.stdout)
    if run.returncode or "ERROR:" in run.stdout:
        raise AuditError(f"Yosys failed for {target}; see {work / 'yosys.log'}")
    if run.stdout.count("Found and reported 0 problems.") != 1:
        raise AuditError(f"Yosys check marker count is not one for {target}")
    metrics = {
        "hierarchy": hierarchy_metrics(hierarchy, spec["top"]),
        "generic": generic_metrics(generic, spec["top"]),
        "mapped": mapped_metrics(mapped, spec["top"]),
    }
    return metrics, classify_warnings(run.stdout)


def run_target(target: str, spec: dict[str, Any], registry_sha: str,
               wrapper_sha: str, yosys: Path, environment: dict[str, str],
               tool: dict[str, str], work: Path) -> dict[str, Any]:
    if resolve_commit(spec["commit"]) != spec["commit"]:
        raise AuditError(f"non-exact commit identity for {target}")
    source_dir = work / "sources"
    source_dir.mkdir(parents=True, exist_ok=False)
    source_rows = []
    source_paths = []
    source_digests: set[str] = set()
    for index, (path, expected) in enumerate(spec["sources"].items()):
        logical = PurePosixPath(path)
        if logical.is_absolute() or ".." in logical.parts or str(logical) != path:
            raise AuditError(f"source path is not normalized relative: {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise AuditError(f"invalid source SHA syntax for {target}:{path}")
        if expected in source_digests:
            raise AuditError(f"duplicate source blob in {target}: {path}")
        source_digests.add(expected)
        data = git_bytes(spec["commit"], path)
        actual = digest_bytes(data)
        if actual != expected:
            raise AuditError(f"source SHA mismatch for {target}:{path}")
        local = source_dir / f"{index:02d}_{Path(path).name}"
        local.write_bytes(data)
        source_paths.append(local)
        source_rows.append({"path": path, "sha256": actual})
    if target.endswith("_k2"):
        source_paths.append(WRAPPERS)
    metrics, warnings = synthesize(
        target, spec, source_paths, yosys, environment, work / "synthesis")
    if metrics["hierarchy"]["reachable_modules"] != sorted(spec["expected_modules"]):
        raise AuditError(
            f"hierarchy closure mismatch for {target}: "
            f"{metrics['hierarchy']['reachable_modules']}")
    boundary = "normalized_atomic_K2_scheduler" if target.endswith("_k2") else (
        "complete_scheduler_plus_P6_TX_link_RX_endpoint")
    included = (["scheduler", "A6 normalized observation wrapper"]
                if target.endswith("_k2") else
                ["scheduler", "integration wrapper", "elastic/front-end adapter",
                 "P6 launch/control", "P6 TX", "P6 RX", "retire observer"])
    return {
        "schema": "a6-a23-k2-p6-same-flow-result-v1",
        "target": target,
        "commit": spec["commit"],
        "top": spec["top"],
        "boundary": boundary,
        "included_components": included,
        "source_inventory_closed": True,
        "sources": source_rows,
        "source_bundle_sha256": digest_bytes(canonical_bytes(source_rows)),
        "registry_sha256": registry_sha,
        "runner_sha256": digest_file(Path(__file__).resolve()),
        "k2_wrapper_sha256": wrapper_sha if target.endswith("_k2") else None,
        "flow": FLOW,
        "metrics": metrics,
        "warnings": warnings,
        "tool": tool,
        "qualification": {
            "digital_structural_proxy": "PASS",
            "functional_equivalence": "NOT_TESTED_BY_THIS_RUNNER",
            "physical_ppa": "HOLD_GENERIC_YOSYS_ONLY"
        },
    }


def generate(output: Path, registry_path: Path, yosys: Path,
             environment: dict[str, str], tool: dict[str, str],
             target_filter: str | None = None) -> None:
    if output.exists():
        raise AuditError(f"refusing existing output: {output}")
    if registry_path.is_symlink() or not registry_path.is_file():
        raise AuditError("registry must be a regular non-symlink file")
    registry = strict_json(registry_path)
    if registry.get("schema") != "a6-a23-k2-p6-source-registry-v1":
        raise AuditError("registry schema mismatch")
    if set(registry.get("targets", {})) != {"a2_k2", "a3_k2", "a2_p6", "a3_p6"}:
        raise AuditError("registry target inventory mismatch")
    registry_sha = digest_file(registry_path)
    wrapper_sha = digest_file(WRAPPERS)
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="a6-a23-k2-p6-") as temp:
        work = Path(temp)
        keys = [target_filter] if target_filter else sorted(registry["targets"])
        results = []
        for key in keys:
            if key not in registry["targets"]:
                raise AuditError(f"unknown target: {key}")
            result = run_target(
                key, registry["targets"][key], registry_sha, wrapper_sha,
                yosys, environment, tool, work / key)
            (output / f"{key}.json").write_bytes(canonical_bytes(result))
            results.append(result)
    summary = {
        "schema": "a6-a23-k2-p6-same-flow-summary-v1",
        "targets": {row["target"]: row["metrics"] for row in results},
        "comparison_rule": (
            "Compare A2/A3 only within K2 or within P6; K2 and P6 have "
            "different charged boundaries."),
        "tool": tool,
    }
    (output / "summary.json").write_bytes(canonical_bytes(summary))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yosys", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--target", choices=("a2_k2", "a3_k2", "a2_p6", "a3_p6"))
    args = parser.parse_args()
    yosys, environment, tool = tool_identity(args.yosys)
    generate(args.output_dir, args.registry, yosys, environment, tool, args.target)
    print(f"A6_A23_K2_P6_SAME_FLOW_PASS output={args.output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as error:
        raise SystemExit(f"A6_A23_K2_P6_SAME_FLOW_FAIL {error}") from error
