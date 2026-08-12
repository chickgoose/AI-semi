#!/usr/bin/env python3
"""Reproduce the A7 K2 identical-boundary, identical-flow structural audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


AUDIT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = AUDIT_ROOT.parents[1]
TOP = "k2_common_boundary"
FLOW = (
    "read_verilog -sv -DSYNTHESIS; hierarchy -check -top k2_common_boundary; "
    "proc; flatten; opt; memory_map; opt; setundef -zero; opt; "
    "techmap; opt; abc -g simple; clean; check; stat"
)
BOUNDARY = (
    "clk,rst,pending[15:0],bundle_ready -> "
    "grant_count[1:0],grant_addr0[3:0],grant_addr1[3:0]"
)
SEQUENTIAL_MARKERS = ("DFF", "LATCH")


WRAPPER_A3 = """module k2_common_boundary (
  input logic clk,
  input logic rst,
  input logic [15:0] pending,
  input logic bundle_ready,
  output logic [1:0] grant_count,
  output logic [3:0] grant_addr0,
  output logic [3:0] grant_addr1
);
  a3_exact_scalar_prefix_k2 dut (
    .clk(clk), .rst(rst), .source_pending(pending),
    .grant_count(grant_count), .lane0_addr(grant_addr0),
    .lane1_addr(grant_addr1), .bundle_ready(bundle_ready)
  );
endmodule
"""

WRAPPER_A2 = """module k2_common_boundary (
  input logic clk,
  input logic rst,
  input logic [15:0] pending,
  input logic bundle_ready,
  output logic [1:0] grant_count,
  output logic [3:0] grant_addr0,
  output logic [3:0] grant_addr1
);
  logic [15:0] unused_grant_bitmap;
  logic unused_drain_idle;
  a2_batched_iwrr_k2 dut (
    .clk(clk), .rst(rst), .req(pending), .grant_count(grant_count),
    .grant_addr0(grant_addr0), .grant_addr1(grant_addr1),
    .grant_bitmap(unused_grant_bitmap), .bundle_ready(bundle_ready),
    .drain_idle(unused_drain_idle)
  );
endmodule
"""

WRAPPER_A4 = """module k2_common_boundary (
  input logic clk,
  input logic rst,
  input logic [15:0] pending,
  input logic bundle_ready,
  output logic [1:0] grant_count,
  output logic [3:0] grant_addr0,
  output logic [3:0] grant_addr1
);
  logic [15:0] unused_source_ready;
  logic [7:0] packed_grant_addr;
  logic unused_drain_idle;
  a4_paired_cortical_column_k2 dut (
    .clk(clk), .rst_n(~rst), .source_valid(pending),
    .source_ready(unused_source_ready), .grant_count(grant_count),
    .grant_addr(packed_grant_addr), .bundle_ready(bundle_ready),
    .drain_idle(unused_drain_idle)
  );
  assign grant_addr0 = packed_grant_addr[3:0];
  assign grant_addr1 = packed_grant_addr[7:4];
endmodule
"""


CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "a3",
        "name": "A3 exact scalar prefix",
        "commit": "29a5003bb47c9c502a3bec9a727de2ed14afcfeb",
        "supersedes_commit": None,
        "rtl_origin_commit": "632e68d247ec36a35b62dbd5c100b0a23d47cf7b",
        "rtl_path": (
            "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/"
            "a3_exact_scalar_prefix_k2.sv"
        ),
        "rtl_sha256": "bd00ade6ebd5f6c5e03ff356393a59f1baf6d890cfb3809a10bf0cda3bb1b0d9",
        "wrapper": WRAPPER_A3,
        "canonical": "a3_exact_scalar_prefix.json",
        "expected_generic_state_bits": 34,
        "expected_mapped_state_bits": 26,
        "expected_warnings": {"abc_combinational_network": 1, "memory_to_register": 0},
        "semantic_grade": "EXACT_SCALAR_PREFIX_K2",
        "semantic_limits": [
            "Independent retire-lane backpressure is outside the scheduler boundary.",
            "Address bitmap carries one outstanding occurrence per source.",
        ],
        "state_note": (
            "The generic post-proc netlist contains the documented 34 bits: "
            "12 committed policy, 12 saved post-bundle policy, and 10 registered "
            "bundle bits. Fixed CENTER_MASK=0110 and PERIPH_MASK=1001 make two "
            "leaf-pair bits in each center/peripheral arbiter state irrelevant. "
            "Uniform techmap/opt removes four committed and four saved bits while "
            "retaining all 10 bundle bits, leaving 26 one-bit mapped flops."
        ),
    },
    {
        "key": "a2",
        "name": "A2 batched IWRR",
        "commit": "d74ff962aaf07c5209f1a1d1c69832735c654a0d",
        "supersedes_commit": None,
        "rtl_origin_commit": "d74ff962aaf07c5209f1a1d1c69832735c654a0d",
        "rtl_path": "candidates/a2_batched_iwrr_k2/rtl/a2_batched_iwrr_k2.sv",
        "rtl_sha256": "800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d",
        "wrapper": WRAPPER_A2,
        "canonical": "a2_batched_iwrr.json",
        "expected_generic_state_bits": 22,
        "expected_mapped_state_bits": 22,
        "expected_warnings": {"abc_combinational_network": 1, "memory_to_register": 3},
        "semantic_grade": "WEIGHTED_AGGREGATE_NOT_A5_SCALAR_PREFIX",
        "semantic_limits": [
            "Its IWRR calendar differs from the A5 scalar-prefix wheel.",
            "A5 semantic compatibility and physical integration remain HOLD.",
        ],
        "state_note": (
            "memory_map keeps the combinational calendar case combinational, so "
            "generic and mapped state both equal the documented 22 bits. The "
            "rejected exploratory memory flow created a non-architectural "
            "two-bit proc_rom read register and is not used in this audit."
        ),
    },
    {
        "key": "a4",
        "name": "A4 paired cortical column",
        "commit": "0e613b6933f1bb92e9b2f75b79a50663187f17d3",
        "supersedes_commit": "2884eb831cc6437efaa52bcd21929ab288f3d265",
        "rtl_origin_commit": "0e613b6933f1bb92e9b2f75b79a50663187f17d3",
        "rtl_path": (
            "rtl/candidates/a4_paired_cortical_column_k2/"
            "a4_paired_cortical_column_k2.sv"
        ),
        "rtl_sha256": "56bde1a765cd750e5b4581e51d90ec1cf6893bcea9cbe904b09aeeafe89a0185",
        "wrapper": WRAPPER_A4,
        "canonical": "a4_paired_cortical_column.json",
        "expected_generic_state_bits": 49,
        "expected_mapped_state_bits": 49,
        "expected_warnings": {"abc_combinational_network": 1, "memory_to_register": 6},
        "semantic_grade": "AGGREGATE_ONLY_NOT_A5_SCALAR_PREFIX",
        "semantic_limits": [
            "Its paired/debt calendar is not A5 scalar-prefix equivalent.",
            "Commit 2884eb8 is superseded because live requests could leak "
            "offers during reset; 0e613b6 adds quiet reset gating.",
            "The active-low reset is normalized to the common active-high reset; "
            "the resulting inverter remains charged.",
        ],
        "state_note": "Generic and mapped state both contain 49 bits.",
    },
)


class AuditError(RuntimeError):
    """The audit cannot produce trustworthy deterministic evidence."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_output(arguments: list[str]) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=REPO_ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise AuditError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def sequential(cell_type: str) -> bool:
    upper = cell_type.upper()
    return any(marker in upper for marker in SEQUENTIAL_MARKERS)


def parameter_width(cell: dict[str, Any]) -> int:
    width = cell.get("parameters", {}).get("WIDTH", "1")
    return int(width, 2) if isinstance(width, str) else int(width)


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def classify_warnings(log: str) -> dict[str, int]:
    classified = {"abc_combinational_network": 0, "memory_to_register": 0}
    unknown: list[str] = []
    for line in log.splitlines():
        if "Warning:" not in line:
            continue
        if "ABC: Warning: The network is combinational" in line:
            classified["abc_combinational_network"] += 1
        elif line.startswith("Warning: Replacing memory "):
            classified["memory_to_register"] += 1
        else:
            unknown.append(line)
    if unknown:
        raise AuditError(f"unclassified Yosys warnings: {unknown}")
    return {
        **classified,
        "total": sum(classified.values()),
        "unclassified": 0,
    }


def analyze_generic(path: Path) -> dict[str, int]:
    module = json.loads(path.read_text(encoding="utf-8"))["modules"][TOP]
    cells = [cell for cell in module.get("cells", {}).values()
             if cell["type"] != "$scopeinfo"]
    muxes = [cell for cell in cells if cell["type"] in ("$mux", "$pmux")]
    return {
        "generic_cells": len(cells),
        "generic_state_bits": sum(
            parameter_width(cell) for cell in cells if sequential(cell["type"])),
        "generic_mux_cells": len(muxes),
        "generic_mux_select_bits": sum(
            len(cell["connections"].get("S", [])) for cell in muxes),
        "generic_mux_data_input_bits": sum(
            len(cell["connections"].get("A", [])) +
            len(cell["connections"].get("B", [])) for cell in muxes),
    }


def analyze_mapped(path: Path) -> dict[str, Any]:
    module = json.loads(path.read_text(encoding="utf-8"))["modules"][TOP]
    cells = {name: cell for name, cell in module.get("cells", {}).items()
             if cell["type"] != "$scopeinfo"}
    seq = {name: cell for name, cell in cells.items()
           if sequential(cell["type"])}
    comb = {name: cell for name, cell in cells.items()
            if not sequential(cell["type"])}

    primary_inputs: set[int] = set()
    primary_outputs: list[int] = []
    ignored: set[int] = set()
    for name, port in module["ports"].items():
        bits = [bit for bit in port["bits"] if isinstance(bit, int)]
        if port["direction"] == "input":
            primary_inputs.update(bits)
            if name in ("clk", "rst"):
                ignored.update(bits)
        else:
            primary_outputs.extend(bits)

    seq_outputs: set[int] = set()
    endpoints = list(primary_outputs)
    for cell in seq.values():
        for port, direction in cell["port_directions"].items():
            bits = [bit for bit in cell["connections"][port]
                    if isinstance(bit, int)]
            if direction == "output":
                seq_outputs.update(bits)
            elif port not in ("C", "R", "S"):
                endpoints.extend(bits)

    drivers: dict[int, list[int]] = {}
    for cell in comb.values():
        inputs = [bit for port, direction in cell["port_directions"].items()
                  if direction == "input"
                  for bit in cell["connections"][port]
                  if isinstance(bit, int)]
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
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
            raise AuditError(f"combinational loop at net {bit}")
        visiting.add(bit)
        value = 1 + max((depth(item) for item in drivers[bit]), default=0)
        visiting.remove(bit)
        memo[bit] = value
        return value

    fanout: dict[int, int] = {}
    sink_pin_count = 0
    for cell in cells.values():
        for port, direction in cell["port_directions"].items():
            if direction != "input" or (
                    sequential(cell["type"]) and port in ("C", "R", "S")):
                continue
            for bit in cell["connections"][port]:
                if isinstance(bit, int):
                    fanout[bit] = fanout.get(bit, 0) + 1
                    sink_pin_count += 1
    for bit in primary_outputs:
        fanout[bit] = fanout.get(bit, 0) + 1
    fanout_values = [value for bit, value in fanout.items()
                     if bit not in ignored]

    types: dict[str, int] = {}
    for cell in cells.values():
        types[cell["type"]] = types.get(cell["type"], 0) + 1
    return {
        "mapped_cells": len(cells),
        "mapped_comb_cells": len(comb),
        "mapped_state_bits": len(seq),
        "logic_depth_levels": max((depth(bit) for bit in endpoints), default=0),
        "fanout_proxy_max": max(fanout_values, default=0),
        "fanout_proxy_p95": percentile(fanout_values, 0.95),
        "nets_fanout_ge16": sum(value >= 16 for value in fanout_values),
        "fanout_net_count": len(fanout_values),
        "sink_pin_wire_proxy": sink_pin_count,
        "mapped_cell_types": dict(sorted(types.items())),
    }


def tool_identity(yosys_argument: Path) -> tuple[Path, dict[str, str], dict[str, str]]:
    yosys = yosys_argument.resolve(strict=True)
    if not os.access(yosys, os.X_OK):
        raise AuditError(f"Yosys is not executable: {yosys}")
    usr_root = yosys.parent.parent
    datdir = usr_root / "share/yosys"
    library = usr_root / "lib/x86_64-linux-gnu"
    abc = yosys.parent / "yosys-abc"
    if not datdir.is_dir() or not library.is_dir() or not abc.is_file():
        raise AuditError("Yosys data, library, or ABC companion is absent")
    env = os.environ.copy()
    old_library = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = str(library) + (
        (":" + old_library) if old_library else "")
    env["YOSYS_DATDIR"] = str(datdir)
    version = subprocess.check_output([str(yosys), "-V"], env=env, text=True).strip()
    identity = {
        "abc_executable_sha256": sha256_file(abc),
        "resolved_executable": str(yosys),
        "version": version,
        "yosys_datdir": str(datdir.resolve()),
        "yosys_executable_sha256": sha256_file(yosys),
    }
    return yosys, env, identity


def synthesize(rtl_bytes: bytes, wrapper_text: str, yosys: Path,
               env: dict[str, str], work: Path) -> tuple[dict[str, Any], dict[str, int]]:
    work.mkdir(parents=True, exist_ok=False)
    rtl = work / "candidate.sv"
    wrapper = work / "wrapper.sv"
    generic = work / "generic.json"
    mapped = work / "mapped.json"
    rtl.write_bytes(rtl_bytes)
    wrapper.write_text(wrapper_text, encoding="utf-8", newline="\n")
    script = "; ".join([
        f"read_verilog -sv -DSYNTHESIS {rtl} {wrapper}",
        f"hierarchy -check -top {TOP}", "proc", "flatten", "opt",
        "memory_map", "opt", "setundef -zero", "opt",
        f"write_json {generic}", "techmap", "opt", "abc -g simple",
        "clean", "check", "stat", f"write_json {mapped}",
    ])
    run = subprocess.run(
        [str(yosys), "-Q", "-p", script], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    (work / "yosys.log").write_text(run.stdout, encoding="utf-8")
    if run.returncode or "ERROR:" in run.stdout:
        raise AuditError(f"Yosys failed; see {work / 'yosys.log'}")
    if run.stdout.count("Found and reported 0 problems.") != 1:
        raise AuditError(f"Yosys check did not pass exactly once: {work}")
    metrics = {**analyze_generic(generic), **analyze_mapped(mapped)}
    return metrics, classify_warnings(run.stdout)


def run_candidate(spec: dict[str, Any], yosys: Path, env: dict[str, str],
                  tool: dict[str, str], work: Path) -> dict[str, Any]:
    resolved = git_output(["rev-parse", spec["commit"]]).decode().strip()
    if resolved != spec["commit"]:
        raise AuditError(f"commit identity mismatch for {spec['key']}: {resolved}")
    origin = git_output(["rev-parse", spec["rtl_origin_commit"]]).decode().strip()
    if origin != spec["rtl_origin_commit"]:
        raise AuditError(f"RTL-origin identity mismatch for {spec['key']}: {origin}")
    if spec["key"] == "a3":
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", origin, resolved],
            cwd=REPO_ROOT, check=False)
        if ancestry.returncode:
            raise AuditError("A3 RTL origin is not an ancestor of its audit commit")

    rtl_bytes = git_output(["cat-file", "blob", f"{resolved}:{spec['rtl_path']}"])
    if sha256_bytes(rtl_bytes) != spec["rtl_sha256"]:
        raise AuditError(f"RTL SHA mismatch for {spec['key']}")
    origin_bytes = git_output(
        ["cat-file", "blob", f"{origin}:{spec['rtl_path']}"])
    if origin_bytes != rtl_bytes:
        raise AuditError(f"RTL changed after declared origin for {spec['key']}")

    metrics, warnings = synthesize(
        rtl_bytes, spec["wrapper"], yosys, env, work / "final")
    observed_warning_counts = {
        key: warnings[key] for key in spec["expected_warnings"]
    }
    if observed_warning_counts != spec["expected_warnings"]:
        raise AuditError(
            f"warning classification changed for {spec['key']}: {warnings}")

    if metrics["generic_state_bits"] != spec["expected_generic_state_bits"]:
        raise AuditError(f"generic state changed for {spec['key']}")
    if metrics["mapped_state_bits"] != spec["expected_mapped_state_bits"]:
        raise AuditError(f"mapped state changed for {spec['key']}")

    reset_live_gating = None
    if spec["supersedes_commit"] is not None:
        superseded = git_output(
            ["rev-parse", spec["supersedes_commit"]]).decode().strip()
        superseded_rtl = git_output(
            ["cat-file", "blob", f"{superseded}:{spec['rtl_path']}"])
        superseded_sha = sha256_bytes(superseded_rtl)
        expected_superseded_sha = (
            "6e5873ca6e30798f984b2f044e3294775acc71537b2136b4ed16ec9df3c604cf"
        )
        if superseded_sha != expected_superseded_sha:
            raise AuditError("superseded A4 RTL SHA mismatch")
        old_metrics, old_warnings = synthesize(
            superseded_rtl, spec["wrapper"], yosys, env, work / "superseded")
        if old_warnings != warnings:
            raise AuditError("A4 warning classes changed across reset-live follow-up")
        delta_keys = (
            "generic_cells", "generic_mux_cells", "generic_mux_data_input_bits",
            "generic_mux_select_bits", "generic_state_bits", "mapped_cells",
            "mapped_comb_cells", "mapped_state_bits", "logic_depth_levels",
            "fanout_proxy_max", "fanout_proxy_p95", "nets_fanout_ge16",
            "fanout_net_count", "sink_pin_wire_proxy",
        )
        cell_types = set(metrics["mapped_cell_types"]) | set(
            old_metrics["mapped_cell_types"])
        reset_live_gating = {
            "final_commit_sha": resolved,
            "final_rtl_sha256": spec["rtl_sha256"],
            "included_in_final_metrics": True,
            "interpretation": (
                "Generic deltas expose the added reset-quiet muxing before "
                "technology mapping. Negative mapped deltas reflect whole-cone "
                "ABC rewriting and are not negative physical cost."
            ),
            "metric_delta_final_minus_superseded": {
                key: metrics[key] - old_metrics[key] for key in delta_keys
            },
            "mapped_cell_type_delta_final_minus_superseded": {
                key: metrics["mapped_cell_types"].get(key, 0) -
                old_metrics["mapped_cell_types"].get(key, 0)
                for key in sorted(cell_types)
            },
            "superseded_commit_sha": superseded,
            "superseded_result_saved_as_final": False,
            "superseded_rtl_sha256": superseded_sha,
        }

    document = {
        "schema": "a7_k2_same_flow_structural_v1",
        "candidate": {
            "key": spec["key"],
            "name": spec["name"],
            "commit_sha": resolved,
            "rtl_origin_commit_sha": origin,
            "rtl_path": spec["rtl_path"],
            "rtl_sha256": spec["rtl_sha256"],
            "semantic_grade": spec["semantic_grade"],
            "semantic_limits": spec["semantic_limits"],
            "supersedes_commit_sha": spec["supersedes_commit"],
        },
        "common_method": {
            "boundary": BOUNDARY,
            "excluded_candidate_outputs": (
                "grant_bitmap/source_ready/drain_idle and downstream link adapters"
            ),
            "flow": FLOW,
            "metric_definitions": {
                "depth": (
                    "mapped combinational cell levels from primary inputs or "
                    "mapped flop outputs to common outputs or flop data/enable inputs"
                ),
                "fanout": (
                    "mapped data/enable sink-pin count per net; clock/reset/set "
                    "sinks and clock/reset primary nets excluded"
                ),
                "wire": (
                    "total mapped combinational and flop data/enable input pin bits; "
                    "a connectivity proxy, not routed length"
                ),
            },
            "self_test": {
                "canonical_committed_bytes_required": True,
                "two_run_byte_identity_required": True,
            },
            "wrapper_sha256": sha256_bytes(spec["wrapper"].encode("utf-8")),
        },
        "limits": {
            "cross_candidate_semantics": "HOLD_NON_EQUIVALENT_POLICIES",
            "physical_ppa": "HOLD_GENERIC_YOSYS_PROXY_ONLY",
            "ranking": (
                "Structural Pareto cannot upgrade or equate semantic grades."
            ),
        },
        "metrics": metrics,
        "reproducibility": {
            "canonical_committed_byte_identity": True,
            "self_test_command": (
                "python3 audits/a7_k2_same_flow_structural/run_audit.py "
                "--yosys /tmp/a7-toolchain/usr/bin/yosys --self-test"
            ),
            "two_run_byte_identity": True,
        },
        "state_interpretation": spec["state_note"],
        "tool": tool,
        "warnings": warnings,
    }
    if reset_live_gating is not None:
        document["reset_live_gating"] = reset_live_gating
    return document


def generate_set(yosys: Path, env: dict[str, str], tool: dict[str, str],
                 destination: Path, work_root: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    work_root.mkdir(parents=True, exist_ok=False)
    for spec in CANDIDATES:
        result = run_candidate(spec, yosys, env, tool, work_root / spec["key"])
        (destination / spec["canonical"]).write_bytes(canonical_bytes(result))


def compare_sets(first: Path, second: Path) -> None:
    for spec in CANDIDATES:
        name = spec["canonical"]
        if (first / name).read_bytes() != (second / name).read_bytes():
            raise AuditError(f"two-run byte identity failed for {name}")


def self_test(yosys: Path, env: dict[str, str], tool: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="a7-k2-structural-self-test-") as root_text:
        root = Path(root_text)
        first = root / "first-results"
        second = root / "second-results"
        generate_set(yosys, env, tool, first, root / "first-work")
        generate_set(yosys, env, tool, second, root / "second-work")
        compare_sets(first, second)
        for spec in CANDIDATES:
            name = spec["canonical"]
            canonical = AUDIT_ROOT / name
            if not canonical.is_file():
                raise AuditError(f"canonical result absent: {canonical}")
            if canonical.read_bytes() != (first / name).read_bytes():
                raise AuditError(f"committed byte identity failed for {name}")
    print(
        "A7_K2_SAME_FLOW_SELF_TEST_PASS candidates=3 runs=2 "
        "two_run_byte_identity=1 committed_byte_identity=1"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yosys", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output-dir", type=Path)
    action.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    yosys, env, tool = tool_identity(args.yosys)
    if args.self_test:
        self_test(yosys, env, tool)
    else:
        assert args.output_dir is not None
        if args.output_dir.exists():
            raise AuditError(f"refusing existing output directory: {args.output_dir}")
        with tempfile.TemporaryDirectory(prefix="a7-k2-structural-work-") as work:
            generate_set(yosys, env, tool, args.output_dir, Path(work) / "work")
        print(f"A7_K2_SAME_FLOW_GENERATE_PASS output={args.output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as error:
        raise SystemExit(f"A7_K2_SAME_FLOW_AUDIT_FAIL {error}") from error
