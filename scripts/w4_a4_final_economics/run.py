#!/usr/bin/env python3
"""Pinned six-way synthesis and functional-economics gate for A4 W4."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.w4_a4_moving_block_synth import run as base  # noqa: E402


A4_COMMIT = "41f239dad4a342277f33d94bb3ed3db53e3497e0"
W3_RTL_PATH = "rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.sv"
W3_FILELIST_PATH = "rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.f"
W4_RTL_PATH = "rtl/candidates/a4_moving_block_w4/a4_moving_block_w4.sv"
W4_FILELIST_PATH = "rtl/candidates/a4_moving_block_w4/a4_moving_block_w4.f"
LOCAL_SUMMARY_PATH = (
    "rtl/candidates/a4_moving_block_w4/results/w4_local_summary.json"
)
FOLLOWUP_PATH = (
    "rtl/candidates/a4_moving_block_w4/results/w4_functional_followup.json"
)
PINS = {
    W3_RTL_PATH: "18e00a2acba587af7f81f2f1608268f4c37d9068a3e7e3f2b29611c4f8ea5677",
    W3_FILELIST_PATH: "d7a70ae9e7764e35b26618bdd0411f34c8d85d0ca01bf179423d25a3a8f2799e",
    W4_RTL_PATH: "433ebc7a1d01e8c8d57e52b42235278c9367941abc0519450ee2b3076edda083",
    W4_FILELIST_PATH: "7874d0822a2d2c51e502b16a6084d0a3bfd0486c747bd08ee08e0b16168e4ca1",
    LOCAL_SUMMARY_PATH: "b3124911730c9d634a3708d3bda3ea96833f2468538d627bbc90a6babca4bf1a",
    FOLLOWUP_PATH: "40d81275ebee63380508d12dad240836f0e5ef84ae6c7f83a7ef6b601f41fbd4",
}
W3_NORMALIZED_SHA256 = (
    "632a403fdfcf2bcebb84800d237e46c10e2a417bd352e9322247de02e2c0e525"
)
W4_NORMALIZED_SHA256 = (
    "2507117cbbf87de1a075d110635a247dc4979545f21d23bc242e2ba377ec7ddb"
)
CANONICAL_TOP = "a4_moving_block_tree"
SELECTED_TOP = "a4_w4_shared_clearance_local_enable"
VARIANTS = (
    "w3_max_advance1",
    "frozen_max_advance2",
    "shared_clearance_local_enable",
)
SOURCES = (16, 64)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def percentile95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def pinned_objects(repo: Path) -> dict[str, bytes]:
    resolved = base.run_command(
        ["git", "-C", str(repo), "rev-parse", f"{A4_COMMIT}^{{commit}}"]
    ).stdout.strip()
    if resolved != A4_COMMIT:
        raise base.AuditError(f"A4 commit mismatch: {resolved}")
    objects = {
        path: base.git_object(repo, A4_COMMIT, path) for path in PINS
    }
    for path, content in objects.items():
        actual = sha256(content)
        if actual != PINS[path]:
            raise base.AuditError(f"SHA256 mismatch for {path}: {actual}")
    if objects[W3_FILELIST_PATH].decode() != W3_RTL_PATH + "\n":
        raise base.AuditError("W3 filelist is not the frozen single RTL entry")
    if objects[W4_FILELIST_PATH].decode() != W4_RTL_PATH + "\n":
        raise base.AuditError("W4 filelist is not the frozen single RTL entry")
    return objects


def exact_rewrite(source: str, rewrites: dict[str, str]) -> str:
    for old in rewrites:
        if source.count(old) != 1:
            raise base.AuditError(f"expected exactly one syntax form: {old}")
    for old, new in rewrites.items():
        source = source.replace(old, new)
    return source


def normalize_sources(objects: dict[str, bytes]) -> dict[str, Any]:
    flat_port = (
        "input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,"
    )
    w3_rewrites = {
        base.PORT_DECL: flat_port,
        **base.ARRAY_DECLARATION_REWRITES,
        "source_event[inject_source]": (
            "source_event_flat[inject_source*ADDR_WIDTH +: ADDR_WIDTH]"
        ),
    }
    w4_rewrites = {
        **base.ARRAY_DECLARATION_REWRITES,
        "logic data_write_d [TOTAL_NODES];":
            "logic [TOTAL_NODES-1:0] data_write_d;",
    }
    w3 = exact_rewrite(objects[W3_RTL_PATH].decode(), w3_rewrites).encode()
    w4 = exact_rewrite(objects[W4_RTL_PATH].decode(), w4_rewrites).encode()
    if sha256(w3) != W3_NORMALIZED_SHA256:
        raise base.AuditError("W3 normalized source SHA256 mismatch")
    if sha256(w4) != W4_NORMALIZED_SHA256:
        raise base.AuditError("W4 normalized source SHA256 mismatch")
    return {
        "w3": w3,
        "w4": w4,
        "receipt": {
            "contract": (
                "declaration packing only, plus W3 unpacked-event indexing to "
                "the identical flat source_event bus"
            ),
            "same_boundary": (
                "clk/rst_n, source_valid/ready, N*32 source_event_flat, "
                "retire_valid/ready/event/source"
            ),
            "state_or_function_added": False,
            "w3_rewrite_count": len(w3_rewrites),
            "w3_normalized_sha256": sha256(w3),
            "w4_rewrite_count": len(w4_rewrites),
            "w4_normalized_sha256": sha256(w4),
        },
    }


def functional_evidence(objects: dict[str, bytes]) -> dict[str, Any]:
    local = json.loads(objects[LOCAL_SUMMARY_PATH])
    followup = json.loads(objects[FOLLOWUP_PATH])
    status = local["status"]
    qualification = local["qualification"]
    if status["selected_local_variant"] != "shared_clearance_local_enable":
        raise base.AuditError("A4 selected variant changed")
    if status["exact_lockstep"] != "PASS":
        raise base.AuditError("A4 exact lockstep is not PASS")
    if qualification["full50_exact_rtl_lockstep_traces"] != 50:
        raise base.AuditError("full50 lockstep count is not 50")
    if qualification["capacity22_exact_rtl_lockstep_traces"] != 22:
        raise base.AuditError("capacity22 lockstep count is not 22")
    if followup["summary"]["all_signal_lockstep"] != "PASS":
        raise base.AuditError("stall/reset/N64 follow-up lockstep is not PASS")
    if followup["summary"]["total_cycles"] != 2982:
        raise base.AuditError("follow-up cycle count changed")

    suites: dict[str, Any] = {}
    for suite, expected_delta in (("full50", 41), ("capacity22", 35)):
        source = local["workload_metrics"][suite]
        accepted_delta = source["moving_accepted"] - source["fixed_accepted"]
        overrun_delta = source["moving_overrun"] - source["fixed_overrun"]
        p99_delta = source["moving_p99"] - source["fixed_p99"]
        if (accepted_delta, overrun_delta, p99_delta) != (
            expected_delta, -expected_delta, 1
        ):
            raise base.AuditError(f"functional delta changed for {suite}")
        suites[suite] = {
            **source,
            "accepted_delta": accepted_delta,
            "accepted_delta_percent": 100 * accepted_delta / source["fixed_accepted"],
            "overrun_delta": overrun_delta,
            "p99_delta": p99_delta,
        }
    return {
        "selected_exactly_matches_frozen_max_advance2": True,
        "generator_v4_lockstep_traces": 72,
        "stall_reset_n64_lockstep_cycles": 2982,
        "suites": suites,
        "source_receipts": {
            "local_summary_path": LOCAL_SUMMARY_PATH,
            "local_summary_sha256": PINS[LOCAL_SUMMARY_PATH],
            "followup_path": FOLLOWUP_PATH,
            "followup_sha256": PINS[FOLLOWUP_PATH],
        },
    }


def recipe(
    source: Path,
    work: Path,
    abc: Path,
    variant: str,
    num_sources: int,
) -> str:
    source_width = (num_sources - 1).bit_length()
    if variant == "w3_max_advance1":
        parameter = (
            f"chparam -set NUM_SOURCES {num_sources} -set ADDR_WIDTH 32 "
            f"-set SOURCE_WIDTH {source_width} -set MAX_ADVANCE 1 {CANONICAL_TOP}"
        )
        hierarchy = f"hierarchy -check -top {CANONICAL_TOP}"
    elif variant == "frozen_max_advance2":
        parameter = (
            f"chparam -set NUM_SOURCES {num_sources} -set ADDR_WIDTH 32 "
            f"-set SOURCE_WIDTH {source_width} -set MAX_ADVANCE 2 {CANONICAL_TOP}"
        )
        hierarchy = f"hierarchy -check -top {CANONICAL_TOP}"
    elif variant == "shared_clearance_local_enable":
        parameter = (
            f"chparam -set NUM_SOURCES {num_sources} -set ADDR_WIDTH 32 "
            f"-set SOURCE_WIDTH {source_width} {SELECTED_TOP}"
        )
        hierarchy = (
            f"hierarchy -check -top {SELECTED_TOP}; "
            f"rename {SELECTED_TOP} {CANONICAL_TOP}"
        )
    else:
        raise base.AuditError(f"unknown variant: {variant}")
    commands = [
        f"read_verilog -sv -DSYNTHESIS {source}",
        parameter,
        hierarchy,
        f"synth -top {CANONICAL_TOP} -flatten -noabc",
        "delete t:$scopeinfo",
        f"abc -exe {abc} -g simple",
        "clean -purge",
        "check -assert",
        f"tee -o {work / 'stat.json'} stat -json -top {CANONICAL_TOP}",
        f"tee -o {work / 'ltp.txt'} ltp -noff",
        f"write_json {work / 'netlist.json'}",
    ]
    return "; ".join(commands)


def fanout_proxies(netlist: dict[str, Any]) -> dict[str, int]:
    module = netlist["modules"][CANONICAL_TOP]
    fanout: dict[int, int] = {}
    for cell in module["cells"].values():
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                for bit in base.numeric_bits(cell["connections"][port]):
                    fanout[bit] = fanout.get(bit, 0) + 1
    for port in module["ports"].values():
        if port["direction"] == "output":
            for bit in base.numeric_bits(port["bits"]):
                fanout[bit] = fanout.get(bit, 0) + 1
    clock_reset = {
        bit
        for name in ("clk", "rst_n")
        for bit in base.numeric_bits(module["ports"][name]["bits"])
    }
    data = [count for bit, count in fanout.items() if bit not in clock_reset]
    return {
        "max_fanout_data": max(data, default=0),
        "p95_fanout_data": percentile95(data),
        "data_nets_fanout_ge16": sum(count >= 16 for count in data),
    }


def synthesize(
    *,
    source_bytes: bytes,
    variant: str,
    num_sources: int,
    work: Path,
    yosys: Path,
    abc: Path,
    lib_dir: Path,
) -> dict[str, Any]:
    work.mkdir(parents=True)
    source = work / "normalized.sv"
    source.write_bytes(source_bytes)
    script = recipe(source, work, abc, variant, num_sources)
    (work / "synth.ys.txt").write_text(script + "\n", encoding="utf-8")
    log = work / "yosys.log"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(lib_dir)
    env["TMPDIR"] = str(work)
    result = base.run_command(
        [str(yosys), "-l", str(log), "-p", script], cwd=work, env=env
    )
    log_text = log.read_text() + "\n" + result.stdout
    findings = base.fail_closed_log_findings(log_text)
    if findings:
        raise base.AuditError(f"warning/latch/unresolved: {findings}")
    if "Found and reported 0 problems." not in log_text:
        raise base.AuditError("Yosys check PASS marker missing")
    try:
        netlist = json.loads((work / "netlist.json").read_text())
    except json.JSONDecodeError as error:
        raise base.AuditError(
            f"{variant} N{num_sources} netlist JSON is incomplete: {error}"
        ) from error
    metrics = base.analyze_netlist(netlist)
    metrics.update(fanout_proxies(netlist))
    try:
        stat_payload = json.loads((work / "stat.json").read_text())
    except json.JSONDecodeError as error:
        raise base.AuditError(
            f"{variant} N{num_sources} stat JSON is incomplete: {error}"
        ) from error
    stat = stat_payload["modules"][f"\\{CANONICAL_TOP}"]
    if stat["num_cells"] != metrics["total_cells"]:
        raise base.AuditError(f"{variant} N{num_sources} stat/netlist cell mismatch")
    if stat["num_wire_bits"] < metrics["wire_unique_bit_proxy"]:
        raise base.AuditError(
            f"{variant} N{num_sources} stat wire bits omit connected bits: "
            f"{stat['num_wire_bits']} != {metrics['wire_unique_bit_proxy']}"
        )
    if stat["num_processes"] or stat["num_memories"]:
        raise base.AuditError("residual process or memory")
    metrics.update(
        {
            "variant": variant,
            "num_sources": num_sources,
            "addr_width": 32,
            "source_width": (num_sources - 1).bit_length(),
            "seq_cells": metrics["ff_bits"],
            "net_count": stat["num_wires"],
            "net_bit_count": stat["num_wire_bits"],
            "connected_net_bit_proxy": metrics["wire_unique_bit_proxy"],
            "warning_latch_unresolved_free": True,
            "recipe_sha256": sha256(
                (
                    recipe(
                        Path("NORMALIZED_RTL"), Path("WORK"), Path("YOSYS_ABC"),
                        variant, num_sources
                    ) + "\n"
                ).encode()
            ),
        }
    )
    return metrics


def pct_delta(candidate: int, reference: int) -> float:
    return 100 * (candidate - reference) / reference


def economic_gate(rows: list[dict[str, Any]], functional: dict[str, Any]) -> dict[str, Any]:
    indexed = {(row["num_sources"], row["variant"]): row for row in rows}
    compared = (
        "total_cells", "comb_cells", "seq_cells", "comb_depth_cells",
        "net_count", "net_bit_count", "max_fanout_data",
        "wire_data_sink_pin_proxy",
    )
    deltas: dict[str, Any] = {}
    max2_checks: dict[str, bool] = {}
    max1_checks: dict[str, bool] = {}
    for n in SOURCES:
        selected = indexed[n, "shared_clearance_local_enable"]
        max1 = indexed[n, "w3_max_advance1"]
        max2 = indexed[n, "frozen_max_advance2"]
        deltas[f"n{n}"] = {
            "selected_vs_max1_percent": {
                metric: pct_delta(selected[metric], max1[metric]) for metric in compared
            },
            "selected_vs_max2_percent": {
                metric: pct_delta(selected[metric], max2[metric]) for metric in compared
            },
        }
        for metric in compared:
            max2_checks[f"n{n}_{metric}_nonworse"] = selected[metric] <= max2[metric]
            max1_checks[f"n{n}_{metric}_nonworse"] = selected[metric] <= max1[metric]
    positive_service = all(
        suite["accepted_delta"] > 0 for suite in functional["suites"].values()
    )
    p99_nonworse = all(
        suite["p99_delta"] <= 0 for suite in functional["suites"].values()
    )
    max2_replacement = (
        functional["selected_exactly_matches_frozen_max_advance2"]
        and all(max2_checks.values())
        and any(
            indexed[n, "shared_clearance_local_enable"]["total_cells"]
            < indexed[n, "frozen_max_advance2"]["total_cells"]
            for n in SOURCES
        )
    )
    max1_economic_go = positive_service and p99_nonworse and all(max1_checks.values())
    return {
        "rule": (
            "MAX2 replacement requires exact functional equivalence, no regression in "
            "every reported generic proxy at N16/N64, and a strict cell reduction. "
            "MAX1 economic GO additionally requires positive service delta with no p99 "
            "or generic-proxy regression; no conversion rate is invented."
        ),
        "functional_positive_service": positive_service,
        "functional_p99_nonworse": p99_nonworse,
        "selected_vs_max2_checks": max2_checks,
        "selected_as_max2_replacement_pass": max2_replacement,
        "selected_vs_max1_checks": max1_checks,
        "selected_over_max1_economic_go": max1_economic_go,
        "final_decision": (
            "GO"
            if max1_economic_go
            else "HOLD_MAX2_REPLACEMENT_ONLY__MAX1_ECONOMIC_GATE_FAIL"
            if max2_replacement
            else "HOLD__MAX2_PARETO_AND_MAX1_ECONOMIC_GATES_FAIL"
        ),
        "deltas": deltas,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a4-repo", type=Path, default=Path("/home/chickgoose/projects/a4"))
    parser.add_argument("--yosys", type=Path, default=Path("/tmp/a9-phase4-yosys/usr/bin/yosys"))
    parser.add_argument("--abc", type=Path, default=Path("/tmp/a9-phase4-yosys/usr/bin/yosys-abc"))
    parser.add_argument(
        "--tool-lib-dir", type=Path,
        default=Path("/tmp/a9-phase4-yosys/usr/lib/x86_64-linux-gnu")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-work", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        objects = pinned_objects(args.a4_repo)
        normalized = normalize_sources(objects)
        functional = functional_evidence(objects)
        base.verify_tool(args.yosys, base.EXPECTED_YOSYS_SHA256, "Yosys")
        base.verify_tool(args.abc, base.EXPECTED_ABC_SHA256, "ABC")
        tcl = args.tool_lib_dir / "libtcl8.6.so.0"
        if base.sha256_file(tcl) != base.EXPECTED_TCL_SHA256:
            raise base.AuditError("Tcl runtime SHA mismatch")
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(args.tool_lib_dir)
        yosys_version = base.run_command([str(args.yosys), "-V"], env=env).stdout.strip()
        abc_version = base.run_command(
            [str(args.abc), "-q", "version; quit"]
        ).stdout.strip()
        if yosys_version != base.EXPECTED_YOSYS_VERSION:
            raise base.AuditError("Yosys version mismatch")
        if abc_version != base.EXPECTED_ABC_VERSION:
            raise base.AuditError("ABC version mismatch")

        temporary = Path(
            tempfile.mkdtemp(prefix=".a3-w4-final-econ-", dir=REPO_ROOT)
        )
        try:
            rows = []
            for n in SOURCES:
                for variant in VARIANTS:
                    source_key = "w4" if variant == "shared_clearance_local_enable" else "w3"
                    rows.append(
                        synthesize(
                            source_bytes=normalized[source_key], variant=variant,
                            num_sources=n, work=temporary / f"n{n}_{variant}",
                            yosys=args.yosys, abc=args.abc,
                            lib_dir=args.tool_lib_dir,
                        )
                    )
        finally:
            if args.keep_work:
                if args.keep_work.exists():
                    raise base.AuditError("--keep-work destination already exists")
                shutil.copytree(temporary, args.keep_work)
            shutil.rmtree(temporary)

        report = {
            "schema_version": 1,
            "audit": "a3_w4_final_a4_moving_block_economics",
            "status": "PASS",
            "provenance": {
                "a4_commit": A4_COMMIT,
                "git_objects": {path: {"sha256": digest} for path, digest in PINS.items()},
                "tops": {
                    "w3": CANONICAL_TOP,
                    "selected_source": SELECTED_TOP,
                    "mapped_canonical_top": CANONICAL_TOP,
                },
                "filelists": {
                    "w3": [W3_RTL_PATH], "selected": [W4_RTL_PATH]
                },
            },
            "normalization": normalized["receipt"],
            "tool": {
                "yosys_version": yosys_version,
                "yosys_sha256": base.sha256_file(args.yosys),
                "abc_version": abc_version,
                "abc_sha256": base.sha256_file(args.abc),
                "tcl_runtime_sha256": base.sha256_file(tcl),
                "mapping": (
                    "synth -top canonical -flatten -noabc; delete t:$scopeinfo; "
                    "abc -g simple; clean -purge"
                ),
            },
            "parameters": [
                {"variant": variant, "NUM_SOURCES": n, "ADDR_WIDTH": 32,
                 "SOURCE_WIDTH": (n - 1).bit_length(),
                 "MAX_ADVANCE_parameter": (
                     1 if variant.endswith("advance1") else
                     2 if variant.endswith("advance2") else None
                 ),
                 "STYLE_parameter": (
                     2 if variant == "shared_clearance_local_enable" else None
                 ),
                 "effective_advance_semantics": (
                     1 if variant.endswith("advance1") else 2
                 )}
                for n in SOURCES for variant in VARIANTS
            ],
            "functional_evidence": functional,
            "metric_contract": {
                "cells": "post-ABC generic mapped instances",
                "seq_cells": "mapped sequential Q bits/cells; latches forbidden",
                "depth": "longest mapped combinational-cell dependency chain",
                "net_count": (
                    "Yosys stat num_wires/net_bit_count; connected_net_bit_proxy "
                    "counts integer bits referenced by mapped cells or ports"
                ),
                "fanout": "mapped sink counts; data excludes clk and rst_n",
                "wire_proxy": "mapped input sink-pin bits excluding clk/rst_n loads",
            },
            "runs": rows,
            "gate": economic_gate(rows, functional),
        }
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pending = args.output.with_suffix(args.output.suffix + ".tmp")
        pending.write_text(encoded, encoding="utf-8")
        pending.replace(args.output)
        print(json.dumps({"status": "PASS", "runs": rows, "gate": report["gate"]},
                         indent=2, sort_keys=True))
        return 0
    except (base.AuditError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
