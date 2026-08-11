#!/usr/bin/env python3
"""Commit-pinned generic synthesis/scaling audit for A4 moving-block RTL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


A4_COMMIT = "850fbcfa4ad168b1250223610780f11378f6c391"
TOP = "a4_moving_block_tree"
RTL_PATH = "rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.sv"
FILELIST_PATH = "rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.f"
RTL_SHA256 = "18e00a2acba587af7f81f2f1608268f4c37d9068a3e7e3f2b29611c4f8ea5677"
FILELIST_SHA256 = "d7a70ae9e7764e35b26618bdd0411f34c8d85d0ca01bf179423d25a3a8f2799e"
EXPECTED_FILELIST = RTL_PATH + "\n"
EXPECTED_YOSYS_SHA256 = "30aa795bec7533dac08bad56309edb6ac70dd33f017c28082d3c1dae1012112f"
EXPECTED_ABC_SHA256 = "21869d0f63b6a2962ad7e54044e7a694f6cc392db6443ad7bf70cdb8ad6ca16a"
EXPECTED_TCL_SHA256 = "6dfbe2faf2a776485be94cb87bed369337bcf9236ee4c955e45004f8253ade8a"
EXPECTED_YOSYS_VERSION = (
    "Yosys 0.52 (git sha1 fee39a3284c90249e1d9684cf6944ffbbcbb8f90)"
)
EXPECTED_ABC_VERSION = "UC Berkeley, ABC 1.01 (compiled May  4 2025 16:37:33)"
EXPECTED_NORMALIZED_SHA256 = (
    "f8225ab51572f90a0074e515716e4289483d022757e1668db0e1dcd155d922ed"
)
CONFIGS = ((16, 1), (16, 2), (64, 1), (64, 2))

PORT_DECL = (
    "input  logic [ADDR_WIDTH-1:0]        source_event [NUM_SOURCES],"
)
FLAT_PORT_DECL = (
    "input  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] source_event,"
)
ARRAY_DECLARATION_REWRITES = {
    "logic slot_valid_q [TOTAL_NODES];":
        "logic [TOTAL_NODES-1:0] slot_valid_q;",
    "logic slot_valid_d [TOTAL_NODES];":
        "logic [TOTAL_NODES-1:0] slot_valid_d;",
    "logic [ADDR_WIDTH-1:0] slot_event_q [TOTAL_NODES];":
        "logic [TOTAL_NODES-1:0][ADDR_WIDTH-1:0] slot_event_q;",
    "logic [ADDR_WIDTH-1:0] slot_event_d [TOTAL_NODES];":
        "logic [TOTAL_NODES-1:0][ADDR_WIDTH-1:0] slot_event_d;",
    "logic [SOURCE_WIDTH-1:0] slot_source_q [TOTAL_NODES];":
        "logic [TOTAL_NODES-1:0][SOURCE_WIDTH-1:0] slot_source_q;",
    "logic [SOURCE_WIDTH-1:0] slot_source_d [TOTAL_NODES];":
        "logic [TOTAL_NODES-1:0][SOURCE_WIDTH-1:0] slot_source_d;",
    "logic branch_phase_q [FIRST_LEAF];":
        "logic [FIRST_LEAF-1:0] branch_phase_q;",
    "logic branch_phase_d [FIRST_LEAF];":
        "logic [FIRST_LEAF-1:0] branch_phase_d;",
}


class AuditError(RuntimeError):
    """A fail-closed provenance, synthesis, or structural-analysis error."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        diagnostic = result.stdout[-16000:]
        raise AuditError(
            f"command failed ({result.returncode}): {' '.join(argv)}\n"
            f"[last 16000 characters]\n{diagnostic}"
        )
    return result


def git_object(repo: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise AuditError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def verify_a4(repo: Path) -> dict[str, Any]:
    resolved = run_command(
        ["git", "-C", str(repo), "rev-parse", f"{A4_COMMIT}^{{commit}}"]
    ).stdout.strip()
    if resolved != A4_COMMIT:
        raise AuditError(f"A4 commit mismatch: expected {A4_COMMIT}, got {resolved}")
    rtl = git_object(repo, A4_COMMIT, RTL_PATH)
    filelist = git_object(repo, A4_COMMIT, FILELIST_PATH)
    if sha256_bytes(rtl) != RTL_SHA256:
        raise AuditError("pinned A4 RTL SHA256 mismatch")
    if sha256_bytes(filelist) != FILELIST_SHA256:
        raise AuditError("pinned A4 filelist SHA256 mismatch")
    if filelist.decode("utf-8") != EXPECTED_FILELIST:
        raise AuditError("filelist has an unexpected dependency or ordering")
    return {
        "commit": resolved,
        "top": TOP,
        "rtl_path": RTL_PATH,
        "rtl_sha256": RTL_SHA256,
        "filelist_path": FILELIST_PATH,
        "filelist_sha256": FILELIST_SHA256,
        "filelist_entries": [RTL_PATH],
        "rtl_bytes": rtl,
    }


def normalize_yosys_port(rtl: bytes) -> tuple[bytes, dict[str, Any]]:
    """Pack unsupported unpacked dimensions without changing indices or state."""
    source = rtl.decode("utf-8")
    rewrites = {PORT_DECL: FLAT_PORT_DECL, **ARRAY_DECLARATION_REWRITES}
    for old in rewrites:
        if source.count(old) != 1:
            raise AuditError(f"RTL no longer has one frozen declaration: {old}")
    normalized = source
    for old, new in rewrites.items():
        normalized = normalized.replace(old, new)
    normalized_bytes = normalized.encode("utf-8")
    normalized_sha256 = sha256_bytes(normalized_bytes)
    if normalized_sha256 != EXPECTED_NORMALIZED_SHA256:
        raise AuditError("normalized RTL SHA256 mismatch")
    return normalized_bytes, {
        "reason": "Yosys 0.52 rejects unpacked ports and warns on async-reset unpacked arrays",
        "semantic_mapping": "only unpacked dimensions become packed; every [index] is unchanged",
        "state_or_logic_added": False,
        "rewrite_count": len(rewrites),
        "normalized_sha256": normalized_sha256,
    }


def verify_tool(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AuditError(f"{label} is absent or not executable: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise AuditError(
            f"{label} SHA256 mismatch: expected {expected_sha256}, got {actual}"
        )


def is_sequential(cell_type: str) -> bool:
    return "DFF" in cell_type or "LATCH" in cell_type


def numeric_bits(bits: list[Any]) -> list[int]:
    return [bit for bit in bits if isinstance(bit, int)]


def fail_closed_log_findings(log_text: str) -> dict[str, list[str]]:
    forbidden = {
        "warning": r"^warning:",
        "latch": r"^latch inferred|\$_.*latch",
        "unresolved": r"unresolved|implicitly declared",
    }
    return {
        name: re.findall(
            f".*(?:{pattern}).*", log_text, flags=re.IGNORECASE | re.MULTILINE
        )
        for name, pattern in forbidden.items()
        if re.search(pattern, log_text, flags=re.IGNORECASE | re.MULTILINE)
    }


def analyze_netlist(netlist: dict[str, Any]) -> dict[str, Any]:
    try:
        module = netlist["modules"][TOP]
        cells = module["cells"]
        ports = module["ports"]
    except KeyError as error:
        raise AuditError(f"mapped netlist is missing {error}") from error

    sequential: set[str] = set()
    combinational: set[str] = set()
    cell_inputs: dict[str, list[int]] = {}
    cell_outputs: dict[str, list[int]] = {}
    cell_types: dict[str, int] = {}
    ff_bits = 0
    for name, cell in cells.items():
        cell_type = cell["type"]
        cell_types[cell_type] = cell_types.get(cell_type, 0) + 1
        directions = cell.get("port_directions")
        if not directions:
            raise AuditError(f"cell {name} lacks port directions")
        inputs = [
            bit
            for port, direction in directions.items()
            if direction == "input"
            for bit in numeric_bits(cell["connections"][port])
        ]
        outputs = [
            bit
            for port, direction in directions.items()
            if direction == "output"
            for bit in numeric_bits(cell["connections"][port])
        ]
        cell_inputs[name] = inputs
        cell_outputs[name] = outputs
        if is_sequential(cell_type):
            sequential.add(name)
            q_bits = [
                bit
                for port, direction in directions.items()
                if direction == "output" and port.upper().startswith("Q")
                for bit in numeric_bits(cell["connections"][port])
            ]
            ff_bits += len(q_bits)
        else:
            combinational.add(name)

    latch_types = sorted(t for t in cell_types if "LATCH" in t)
    if latch_types:
        raise AuditError(f"latch cells are forbidden: {latch_types}")
    nongeneric_types = sorted(t for t in cell_types if not t.startswith("$_"))
    if nongeneric_types:
        raise AuditError(f"unresolved/non-generic cells remain: {nongeneric_types}")

    comb_driver: dict[int, str] = {}
    for name in combinational:
        for bit in cell_outputs[name]:
            if bit in comb_driver:
                raise AuditError(f"multiple combinational drivers for bit {bit}")
            comb_driver[bit] = name

    dependencies = {
        name: {comb_driver[bit] for bit in cell_inputs[name] if bit in comb_driver}
        for name in combinational
    }
    depth: dict[str, int] = {}
    pending = set(combinational)
    while pending:
        ready = sorted(name for name in pending if dependencies[name] <= depth.keys())
        if not ready:
            raise AuditError("combinational cycle prevents depth calculation")
        for name in ready:
            depth[name] = 1 + max((depth[parent] for parent in dependencies[name]), default=0)
            pending.remove(name)

    fanout: dict[int, int] = {}
    for bits in cell_inputs.values():
        for bit in bits:
            fanout[bit] = fanout.get(bit, 0) + 1
    for port in ports.values():
        if port["direction"] == "output":
            for bit in numeric_bits(port["bits"]):
                fanout[bit] = fanout.get(bit, 0) + 1
    clock_reset_bits = {
        bit
        for port_name in ("clk", "rst_n")
        for bit in numeric_bits(ports[port_name]["bits"])
    }
    data_fanout = {bit: count for bit, count in fanout.items() if bit not in clock_reset_bits}
    all_net_bits = {
        bit
        for cell in cells.values()
        for bits in cell["connections"].values()
        for bit in numeric_bits(bits)
    }
    all_net_bits.update(
        bit for port in ports.values() for bit in numeric_bits(port["bits"])
    )
    input_pin_bits = sum(len(bits) for bits in cell_inputs.values())
    clock_reset_loads = sum(fanout.get(bit, 0) for bit in clock_reset_bits)
    return {
        "total_cells": len(cells),
        "ff_bits": ff_bits,
        "comb_cells": len(combinational),
        "comb_depth_cells": max(depth.values(), default=0),
        "max_fanout_all": max(fanout.values(), default=0),
        "max_fanout_data": max(data_fanout.values(), default=0),
        "wire_unique_bit_proxy": len(all_net_bits),
        "wire_sink_pin_proxy": input_pin_bits,
        "wire_data_sink_pin_proxy": input_pin_bits - clock_reset_loads,
        "cell_types": dict(sorted(cell_types.items())),
    }


def make_yosys_script(source: Path, work: Path, sources: int, advance: int, abc: Path) -> str:
    source_width = (sources - 1).bit_length()
    commands = [
        f"read_verilog -sv -DSYNTHESIS {source}",
        (
            f"chparam -set NUM_SOURCES {sources} -set ADDR_WIDTH 32 "
            f"-set SOURCE_WIDTH {source_width} -set MAX_ADVANCE {advance} {TOP}"
        ),
        f"hierarchy -check -top {TOP}",
        f"synth -top {TOP} -flatten -noabc",
        f"abc -exe {abc} -g simple",
        "clean -purge",
        "check -assert",
        f"tee -o {work / 'stat.json'} stat -json -top {TOP}",
        f"tee -o {work / 'ltp.txt'} ltp -noff",
        f"write_json {work / 'netlist.json'}",
    ]
    return "; ".join(commands)


def synthesize(
    *,
    yosys: Path,
    abc: Path,
    lib_dir: Path,
    normalized_rtl: bytes,
    work: Path,
    sources: int,
    advance: int,
) -> dict[str, Any]:
    work.mkdir(parents=True)
    source = work / "a4_moving_block_tree.yosys_normalized.sv"
    source.write_bytes(normalized_rtl)
    script = make_yosys_script(source, work, sources, advance, abc)
    (work / "synth.ys.txt").write_text(script + "\n", encoding="utf-8")
    log = work / "yosys.log"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(lib_dir)
    result = run_command(
        [str(yosys), "-l", str(log), "-p", script], cwd=work, env=env
    )
    log_text = log.read_text(encoding="utf-8") + "\n" + result.stdout
    hits = fail_closed_log_findings(log_text)
    if hits:
        raise AuditError(f"fail-closed log detector fired: {hits}")
    if "Found and reported 0 problems." not in log_text:
        raise AuditError("Yosys check success marker is absent")
    metrics = analyze_netlist(json.loads((work / "netlist.json").read_text()))
    stat = json.loads((work / "stat.json").read_text())
    stat_module = stat["modules"][f"\\{TOP}"]
    if stat_module["num_cells"] != metrics["total_cells"]:
        raise AuditError("Yosys stat and mapped-netlist cell counts differ")
    if stat_module["num_wire_bits"] != metrics["wire_unique_bit_proxy"]:
        raise AuditError("Yosys stat and mapped-netlist wire-bit counts differ")
    if stat_module["num_processes"] or stat_module["num_memories"]:
        raise AuditError("process or memory remains after generic mapping")
    if stat_module["num_cells_by_type"] != metrics["cell_types"]:
        raise AuditError("Yosys stat and mapped-netlist cell-type counts differ")
    metrics.update(
        {
            "num_sources": sources,
            "max_advance": advance,
            "addr_width": 32,
            "source_width": (sources - 1).bit_length(),
            "synth_recipe_sha256": sha256_bytes(
                (
                    make_yosys_script(
                        Path("NORMALIZED_RTL"), Path("WORK"), sources, advance,
                        Path("YOSYS_ABC")
                    ) + "\n"
                ).encode()
            ),
            "warning_latch_unresolved_free": True,
        }
    )
    return metrics


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def comparisons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    indexed = {(row["num_sources"], row["max_advance"]): row for row in rows}
    metrics = (
        "total_cells", "ff_bits", "comb_cells", "comb_depth_cells",
        "max_fanout_data", "wire_unique_bit_proxy", "wire_data_sink_pin_proxy",
    )
    within: dict[str, Any] = {}
    for sources in (16, 64):
        fixed, moving = indexed[(sources, 1)], indexed[(sources, 2)]
        within[f"n{sources}_advance2_over_advance1"] = {
            metric: ratio(moving[metric], fixed[metric]) for metric in metrics
        }
    scaling: dict[str, Any] = {}
    for advance in (1, 2):
        small, large = indexed[(16, advance)], indexed[(64, advance)]
        scaling[f"advance{advance}_n64_over_n16"] = {
            metric: ratio(large[metric], small[metric]) for metric in metrics
        }
    return {"within_n": within, "n64_scaling": scaling}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a4-repo", type=Path, default=Path("/home/chickgoose/projects/a4"))
    parser.add_argument("--yosys", type=Path, default=Path("/tmp/a9-phase4-yosys/usr/bin/yosys"))
    parser.add_argument("--abc", type=Path, default=Path("/tmp/a9-phase4-yosys/usr/bin/yosys-abc"))
    parser.add_argument(
        "--tool-lib-dir",
        type=Path,
        default=Path("/tmp/a9-phase4-yosys/usr/lib/x86_64-linux-gnu"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-work", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        provenance = verify_a4(args.a4_repo)
        rtl = provenance.pop("rtl_bytes")
        normalized, normalization = normalize_yosys_port(rtl)
        verify_tool(args.yosys, EXPECTED_YOSYS_SHA256, "Yosys")
        verify_tool(args.abc, EXPECTED_ABC_SHA256, "ABC")
        tcl_library = args.tool_lib_dir / "libtcl8.6.so.0"
        if sha256_file(tcl_library) != EXPECTED_TCL_SHA256:
            raise AuditError("Yosys Tcl runtime SHA256 mismatch")
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(args.tool_lib_dir)
        version = run_command([str(args.yosys), "-V"], env=env).stdout.strip()
        if version != EXPECTED_YOSYS_VERSION:
            raise AuditError(f"Yosys version mismatch: {version}")
        abc_version = run_command(
            [str(args.abc), "-q", "version; quit"]
        ).stdout.strip()
        if abc_version != EXPECTED_ABC_VERSION:
            raise AuditError(f"ABC version mismatch: {abc_version}")
        temporary = Path(tempfile.mkdtemp(prefix="a3-w4-a4-synth-"))
        try:
            rows = [
                synthesize(
                    yosys=args.yosys,
                    abc=args.abc,
                    lib_dir=args.tool_lib_dir,
                    normalized_rtl=normalized,
                    work=temporary / f"n{sources}_a{advance}",
                    sources=sources,
                    advance=advance,
                )
                for sources, advance in CONFIGS
            ]
            if args.keep_work:
                if args.keep_work.exists():
                    raise AuditError(f"--keep-work destination exists: {args.keep_work}")
                shutil.copytree(temporary, args.keep_work)
        finally:
            shutil.rmtree(temporary)
        report = {
            "schema_version": 1,
            "audit": "a3_w4_a4_moving_block_generic_synthesis_scaling",
            "status": "PASS",
            "provenance": provenance,
            "normalization": normalization,
            "tool": {
                "yosys_version": version,
                "yosys_path": str(args.yosys),
                "yosys_sha256": sha256_file(args.yosys),
                "abc_path": str(args.abc),
                "abc_sha256": sha256_file(args.abc),
                "abc_version": abc_version,
                "tcl_runtime_path": str(tcl_library),
                "tcl_runtime_sha256": sha256_file(tcl_library),
                "mapping": "synth -flatten -noabc; abc -g simple; clean -purge",
                "recipe_paths": (
                    "per-run recipe SHA substitutes NORMALIZED_RTL, WORK, and "
                    "YOSYS_ABC for machine-specific paths"
                ),
            },
            "parameters": [
                {"NUM_SOURCES": n, "ADDR_WIDTH": 32, "SOURCE_WIDTH": (n - 1).bit_length(),
                 "MAX_ADVANCE": a}
                for n, a in CONFIGS
            ],
            "metric_contract": {
                "cells": "mapped Yosys cell instances after clean -purge",
                "ff_bits": "mapped sequential Q bits; latch types forbidden",
                "comb_depth_cells": "longest combinational mapped-cell dependency chain",
                "max_fanout_data": "maximum mapped sink count excluding clk/rst_n nets",
                "wire_unique_bit_proxy": "distinct mapped integer net bits",
                "wire_data_sink_pin_proxy": "mapped input-pin bits excluding clk/rst_n loads",
            },
            "runs": rows,
            "comparisons": comparisons(rows),
        }
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pending = args.output.with_suffix(args.output.suffix + ".tmp")
        pending.write_text(encoded, encoding="utf-8")
        pending.replace(args.output)
        print(json.dumps({"status": "PASS", "runs": rows}, indent=2, sort_keys=True))
        return 0
    except (AuditError, OSError, UnicodeError, json.JSONDecodeError) as error:
        diagnostic = {"status": "FAIL", "error": str(error)}
        print(json.dumps(diagnostic, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
