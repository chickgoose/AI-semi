#!/usr/bin/env python3
"""Rebuild same-flow full A2/A3 plus charged-P6 structural receipts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "audits/a7_k2_same_flow_structural/run_audit.py"
TOP = "k2_p6_cost_boundary"
P6_TOP = "k2_p6_endpoint_cost_boundary"
P6_COMMIT = "747db00c0913a0681f482443c66e22a0f75c7373"
BOUNDARY = (
    "ref_clk,sample_clk,rst_n,link_enable,pending[15:0] -> "
    "p6_clk,p6_data[4:0],retire_valid[1:0],retire_addr0[3:0],"
    "retire_addr1[3:0],protocol_error,drain_idle"
)


class CostReceiptError(RuntimeError):
    pass


def load_base(top: str = TOP):
    spec = importlib.util.spec_from_file_location("a7_same_flow", BASE_PATH)
    if spec is None or spec.loader is None:
        raise CostReceiptError("cannot load same-flow implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TOP = top
    return module


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def compact(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git_blob(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{path}"], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise CostReceiptError(result.stderr.decode(errors="replace").strip())
    return result.stdout


WRAPPER_A2 = """module k2_p6_cost_boundary (
  input logic ref_clk, sample_clk, rst_n, link_enable,
  input logic [15:0] pending,
  output logic p6_clk,
  output logic [4:0] p6_data,
  output logic [1:0] retire_valid,
  output logic [3:0] retire_addr0, retire_addr1,
  output logic protocol_error, drain_idle
);
  logic unused_commit;
  logic [1:0] unused_count;
  logic [3:0] unused_addr0, unused_addr1;
  logic [15:0] unused_bitmap;
  a2_batched_iwrr_p6_top dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n(rst_n),
    .link_enable_i(link_enable), .req_i(pending),
    .grant_commit_o(unused_commit), .grant_count_o(unused_count),
    .grant_addr0_o(unused_addr0), .grant_addr1_o(unused_addr1),
    .grant_bitmap_o(unused_bitmap), .p6_clk_o(p6_clk),
    .p6_data_o(p6_data), .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0), .retire_addr1_o(retire_addr1),
    .protocol_error_o(protocol_error), .drain_idle_o(drain_idle)
  );
endmodule
"""


WRAPPER_A3 = """module k2_p6_cost_boundary (
  input logic ref_clk, sample_clk, rst_n, link_enable,
  input logic [15:0] pending,
  output logic p6_clk,
  output logic [4:0] p6_data,
  output logic [1:0] retire_valid,
  output logic [3:0] retire_addr0, retire_addr1,
  output logic protocol_error, drain_idle
);
  logic unused_valid, unused_ready, unused_commit;
  logic [1:0] unused_count, unused_steps;
  logic [3:0] unused_addr0, unused_addr1;
  logic bundle_error, retire_error;
  a3_exact_scalar_prefix_k2_p6_top dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n(rst_n),
    .link_enable_i(link_enable), .source_pending_i(pending),
    .bundle_valid_o(unused_valid), .bundle_ready_o(unused_ready),
    .bundle_commit_o(unused_commit), .grant_count_o(unused_count),
    .grant_addr0_o(unused_addr0), .grant_addr1_o(unused_addr1),
    .policy_microsteps_o(unused_steps),
    .bundle_protocol_error_o(bundle_error), .p6_clk_o(p6_clk),
    .p6_data_o(p6_data), .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0), .retire_addr1_o(retire_addr1),
    .retire_protocol_error_o(retire_error), .drain_idle_o(drain_idle)
  );
  assign protocol_error = bundle_error | retire_error;
endmodule
"""


P6_WRAPPER = """module k2_p6_endpoint_cost_boundary (
  input logic ref_clk, sample_clk, rst_n,
  input logic bundle_valid,
  input logic [1:0] grant_count,
  input logic [3:0] grant_addr0, grant_addr1,
  output logic bundle_ready, bundle_commit,
  output logic [1:0] policy_microsteps,
  output logic bundle_protocol_error,
  output logic p6_clk,
  output logic [4:0] p6_data,
  output logic [1:0] retire_valid,
  output logic [3:0] retire_addr0, retire_addr1,
  output logic retire_protocol_error, drain_idle
);
  a7_p6_atomic_bundle_adapter dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n(rst_n),
    .bundle_valid_i(bundle_valid), .grant_count_i(grant_count),
    .grant_addr0_i(grant_addr0), .grant_addr1_i(grant_addr1),
    .bundle_ready_o(bundle_ready), .bundle_commit_o(bundle_commit),
    .policy_microsteps_o(policy_microsteps),
    .bundle_protocol_error_o(bundle_protocol_error), .p6_clk_o(p6_clk),
    .p6_data_o(p6_data), .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0), .retire_addr1_o(retire_addr1),
    .retire_protocol_error_o(retire_protocol_error), .drain_idle_o(drain_idle)
  );
endmodule
"""


P6_PATHS = (
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_launch.sv",
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv",
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv",
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_observer.sv",
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv",
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_frontend.sv",
    "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_adapter.sv",
)
P6_CLOSURE_SHA256 = "91fe26ba23ea9e872d77664663a2f9f7fd0b8fdad0657cc31fd164edbf9d684b"


SPECS = (
    {
        "key": "a2", "name": "A2 batched IWRR plus P6",
        "commit": "e1d5598f1aa53dfadbdad47cce07afa51caac32c",
        "filelist": "rtl/candidates/a2_batched_iwrr_p6/a2_batched_iwrr_p6.f",
        "filelist_sha256": "645197b9fc529853df479032798e1dc6b36b13c143e4c45c34933b8afafbd043",
        "closure_sha256": "27a19e2cf43b9ac7c5f62a8d050ce093c57d65da29ad8ae03005977e597d6412",
        "scheduler_commit": "d74ff962aaf07c5209f1a1d1c69832735c654a0d",
        "scheduler_rtl_sha256": "800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d",
        "adapter_contract_state_bits": 11,
        "wrapper": WRAPPER_A2,
    },
    {
        "key": "a3", "name": "A3 exact scalar prefix plus P6",
        "commit": "599f24c948b06d2cf8ebbf112542b7b25fba3742",
        "filelist": "rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6.f",
        "filelist_sha256": "ed04b6ac6dc76b6b993180763f116257c1188a6e9b71da3d299d0dd9746ef3fc",
        "closure_sha256": "3094f56c4bcd9178edf04da8ee8622edac502ec87f8c63246b14c43d7ed5b900",
        "scheduler_commit": "29a5003bb47c9c502a3bec9a727de2ed14afcfeb",
        "scheduler_rtl_sha256": "bd00ade6ebd5f6c5e03ff356393a59f1baf6d890cfb3809a10bf0cda3bb1b0d9",
        "adapter_contract_state_bits": 0,
        "wrapper": WRAPPER_A3,
    },
)


def source_closure(spec: dict[str, Any]) -> tuple[bytes, list[dict[str, str]]]:
    filelist = git_blob(spec["commit"], spec["filelist"])
    if digest(filelist) != spec["filelist_sha256"]:
        raise CostReceiptError(f"{spec['key']} filelist identity mismatch")
    paths = [line.strip() for line in filelist.decode().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if len(paths) != len(set(paths)) or not paths:
        raise CostReceiptError(f"{spec['key']} invalid or duplicate source closure")
    rows, payloads = [], []
    for path in paths:
        payload = git_blob(spec["commit"], path)
        if path.startswith("rtl/candidates/a7_p6_exact_pair_endpoint/"):
            if payload != git_blob(P6_COMMIT, path):
                raise CostReceiptError(
                    f"{spec['key']} P6 source differs from isolated A7 commit: {path}")
        rows.append({"path": path, "sha256": digest(payload)})
        payloads.append(payload)
    if digest(compact(rows)) != spec["closure_sha256"]:
        raise CostReceiptError(f"{spec['key']} source closure identity mismatch")
    if rows[0]["sha256"] != spec["scheduler_rtl_sha256"]:
        raise CostReceiptError(f"{spec['key']} scheduler RTL is not the pinned owner blob")
    return b"\n".join(payloads), rows


def build(spec: dict[str, Any], yosys: Path, env: dict[str, str],
          tool: dict[str, str], work: Path) -> dict[str, Any]:
    base = load_base()
    rtl, sources = source_closure(spec)
    metrics, warnings = base.synthesize(rtl, spec["wrapper"], yosys, env, work)
    flow = base.FLOW.replace("k2_common_boundary", TOP)
    return {
        "schema": "a7_k2_p6_integration_cost_v1",
        "candidate": {
            "key": spec["key"], "name": spec["name"],
            "integration_commit_sha": spec["commit"],
            "scheduler_commit_sha": spec["scheduler_commit"],
            "source_filelist_path": spec["filelist"],
            "source_filelist_sha256": spec["filelist_sha256"],
            "source_closure_sha256": spec["closure_sha256"],
            "sources": sources,
        },
        "common_method": {
            "top": TOP, "boundary": BOUNDARY, "flow": flow,
            "wrapper_sha256": digest(spec["wrapper"].encode()), "tool": tool,
        },
        "closure": {
            "full_composition_synthesized": True,
            "unlisted_rtl_allowed": False,
            "components": [
                {"role": "normalized_scheduler", "charged": True,
                 "contract_state_bits": None,
                 "identity": spec["scheduler_rtl_sha256"]},
                {"role": "integration_adapter", "charged": True,
                 "contract_state_bits": spec["adapter_contract_state_bits"],
                 "identity": sources[-1]["sha256"]},
                {"role": "p6_endpoint", "charged": True,
                 "contract_state_bits": 40,
                 "identity": P6_COMMIT},
            ],
        },
        "metrics": metrics,
        "warnings": warnings,
        "limits": {
            "area": "UNAVAILABLE_NO_LIBERTY_AREA",
            "power": "UNAVAILABLE_NO_ACTIVITY_OR_POWER_FLOW",
            "physical_ppa": "HOLD_GENERIC_YOSYS_PROXY_ONLY",
            "delta_interpretation": "whole-cone structural delta, not isolated additive cell cost",
        },
    }


def build_p6(yosys: Path, env: dict[str, str], tool: dict[str, str],
             work: Path) -> dict[str, Any]:
    rows, payloads = [], []
    for path in P6_PATHS:
        payload = git_blob(P6_COMMIT, path)
        rows.append({"path": path, "sha256": digest(payload)})
        payloads.append(payload)
    if digest(compact(rows)) != P6_CLOSURE_SHA256:
        raise CostReceiptError("isolated P6 source closure identity mismatch")
    base = load_base(P6_TOP)
    metrics, warnings = base.synthesize(
        b"\n".join(payloads), P6_WRAPPER, yosys, env, work)
    return {
        "schema": "a7_k2_p6_endpoint_cost_v1",
        "component": {
            "name": "A7 P6 atomic bundle endpoint",
            "commit_sha": P6_COMMIT,
            "source_closure_sha256": P6_CLOSURE_SHA256,
            "sources": rows,
            "contract_state_bits": 40,
        },
        "common_method": {
            "top": P6_TOP,
            "boundary": (
                "ref_clk,sample_clk,rst_n,bundle_valid,grant_count[1:0],"
                "grant_addr0[3:0],grant_addr1[3:0] -> bundle_ready,"
                "bundle_commit,policy_microsteps[1:0],bundle_protocol_error,"
                "p6_clk,p6_data[4:0],retire_valid[1:0],retire_addr0[3:0],"
                "retire_addr1[3:0],retire_protocol_error,drain_idle"
            ),
            "flow": base.FLOW.replace("k2_common_boundary", P6_TOP),
            "wrapper_sha256": digest(P6_WRAPPER.encode()),
            "tool": tool,
        },
        "metrics": metrics,
        "warnings": warnings,
        "limits": {
            "area": "UNAVAILABLE_NO_LIBERTY_AREA",
            "power": "UNAVAILABLE_NO_ACTIVITY_OR_POWER_FLOW",
            "physical_ppa": "HOLD_GENERIC_YOSYS_PROXY_ONLY",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yosys", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise CostReceiptError(f"refusing existing output: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    base = load_base()
    yosys, env, tool = base.tool_identity(args.yosys)
    with tempfile.TemporaryDirectory(prefix="a7-k2-p6-cost-") as temporary:
        for spec in SPECS:
            document = build(spec, yosys, env, tool, Path(temporary) / spec["key"])
            (args.output_dir / f"{spec['key']}_p6_integration.json").write_bytes(canonical(document))
        p6 = build_p6(yosys, env, tool, Path(temporary) / "p6")
        (args.output_dir / "p6_endpoint.json").write_bytes(canonical(p6))
    print("A7_K2_P6_COST_RECEIPTS_PASS candidates=2 isolated_p6=1")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CostReceiptError as error:
        raise SystemExit(f"A7_K2_P6_COST_RECEIPT_FAIL {error}") from error
