#!/usr/bin/env python3
"""Fail-closed same-boundary Yosys comparison of fovea plus A7 R1 links."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOVEA_BLOBS = {
    "arbiter2.v": "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
    "arbiter4_tree.v": "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
    "aer_tx16_trad_rowcol_fovea.v": "353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e",
}
A7_EVIDENCE_COMMIT = "0f2db4b460fab0e45c4c22756209cad400789944"
A7_ENDPOINT_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
A7_EVIDENCE_PATH = "tests/a7_r1_candidate_endpoint/structural_compare.py"
A7_EVIDENCE_SHA256 = "419c104454e44b3d8245a877550de3158b3863530d95009bdaf0df23ec84f84d"
A7_RTL_BLOBS = {
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv": "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv": "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv": "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv": "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv": "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv": "151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv": "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
}
TOPS = {
    # top, physical link pins, top input bits, top output bits
    "ddr2": ("a5_fovea_a7_ddr_top", 3, 19, 10),
    "parallel4": ("a5_fovea_a7_parallel_top", 5, 19, 12),
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")
EXPECTED_STRUCTURE = {
    "ddr2": {
        "state_bits": 37, "register_or_latch_cells": 24,
        "charged_functional_cells": 150, "excluded_scopeinfo_cells": 19,
        "operator_depth": 28, "generic_gate_depth": 33,
    },
    "parallel4": {
        "state_bits": 35, "register_or_latch_cells": 23,
        "charged_functional_cells": 148, "excluded_scopeinfo_cells": 17,
        "operator_depth": 28, "generic_gate_depth": 33,
    },
}


class ContractError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ContractError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def verify_and_materialize(fixture_dir: Path, a7_repo: Path, output: Path) -> list[Path]:
    if not a7_repo.is_dir():
        raise ContractError(f"missing A7 repository: {a7_repo}")
    for commit in (A7_EVIDENCE_COMMIT, A7_ENDPOINT_COMMIT):
        observed = git(a7_repo, "rev-parse", f"{commit}^{{commit}}").decode().strip()
        if observed != commit:
            raise ContractError(f"A7 commit mismatch: expected={commit} observed={observed}")

    evidence = git(a7_repo, "show", f"{A7_EVIDENCE_COMMIT}:{A7_EVIDENCE_PATH}")
    if digest(evidence) != A7_EVIDENCE_SHA256:
        raise ContractError("A7 0f2db4b structural evidence blob mismatch")

    sources: list[Path] = []
    for name, expected in FOVEA_BLOBS.items():
        path = fixture_dir / name
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise ContractError(f"canonical fovea blob mismatch: {path}")
        sources.append(path)

    a7_out = output / "pinned-a7-rtl"
    a7_out.mkdir(parents=True, exist_ok=False)
    for source, expected in A7_RTL_BLOBS.items():
        data = git(a7_repo, "show", f"{A7_ENDPOINT_COMMIT}:{source}")
        if digest(data) != expected:
            raise ContractError(f"A7 42377ca RTL blob mismatch: {source}")
        path = a7_out / Path(source).name
        path.write_bytes(data)
        sources.append(path)

    wrapper = HERE / "a5_fovea_a7_tops.sv"
    if not wrapper.is_file():
        raise ContractError(f"missing comparison wrapper: {wrapper}")
    sources.append(wrapper)
    return sources


def state_bits(histogram: dict[str, int]) -> tuple[int, int]:
    bits = 0
    cells = 0
    for name, count in histogram.items():
        lower = name.lower()
        if "dff" not in lower and "dlatch" not in lower:
            continue
        width = re.search(r"_(\d+)$", name)
        bits += count * (int(width.group(1)) if width else 1)
        cells += count
    return bits, cells


def synthesize(yosys: str, sources: list[Path], variant: str, output: Path) -> dict[str, object]:
    top, link_pins, input_bits, output_bits = TOPS[variant]
    stat_path = output / f"{variant}.stat.json"
    quoted = " ".join("\"" + str(path).replace("\"", "\\\"") + "\"" for path in sources)
    command = (
        f"read_verilog -sv {quoted}; hierarchy -check -top {top}; "
        "proc; check -assert; flatten; opt; check -assert; "
        f"tee -o {stat_path} stat -json -width; ltp -noff; "
        "techmap; opt; check -assert; ltp -noff"
    )
    result = subprocess.run(
        [yosys, "-Q", "-p", command], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    (output / f"{variant}.yosys.log").write_text(result.stdout)
    if result.returncode:
        raise ContractError(f"Yosys failed for {variant}:\n{result.stdout[-4000:]}")
    try:
        document = json.loads(stat_path.read_text())
        module = document["modules"][f"\\{top}"]
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise ContractError(f"invalid Yosys stat for {variant}: {error}") from error
    histogram = module["num_cells_by_type"]
    scopeinfo = sum(value for key, value in histogram.items() if "scopeinfo" in key.lower())
    bits, sequential_cells = state_bits(histogram)
    depths = [int(value) for value in DEPTH_RE.findall(result.stdout)]
    if len(depths) != 2:
        raise ContractError(f"expected two depth observations for {variant}, got {depths}")
    charged = int(module["num_cells"]) - scopeinfo
    if charged <= 0 or bits <= 0:
        raise ContractError(f"nonsensical structural result for {variant}")
    row = {
        "variant": variant,
        "top": top,
        "physical_link_pins": link_pins,
        "top_input_bits": input_bits,
        "top_output_bits": output_bits,
        "state_bits": bits,
        "register_or_latch_cells": sequential_cells,
        "charged_functional_cells": charged,
        "excluded_scopeinfo_cells": scopeinfo,
        "operator_depth": depths[0],
        "generic_gate_depth": depths[1],
    }
    observed = {key: row[key] for key in EXPECTED_STRUCTURE[variant]}
    if observed != EXPECTED_STRUCTURE[variant]:
        raise ContractError(
            f"{variant} structure mismatch: observed={observed} "
            f"expected={EXPECTED_STRUCTURE[variant]}"
        )
    return row


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a7-repo", type=Path, default=HERE.parents[2] / "a7")
    parser.add_argument("--fixture-dir", type=Path, default=HERE / "fixtures")
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="a5-fovea-a7-") as temporary:
            materialized = Path(temporary)
            sources = verify_and_materialize(args.fixture_dir, args.a7_repo, materialized)
            if args.verify_only:
                print("A5_FOVEA_A7_PROVENANCE_PASS synthesis=NOT_RUN")
                return 0
            yosys = shutil.which(args.yosys) if os.sep not in args.yosys else args.yosys
            if not yosys or not Path(yosys).is_file() or not os.access(yosys, os.X_OK):
                raise ContractError(f"required Yosys executable unavailable: {args.yosys}")
            if args.output is None:
                raise ContractError("--output is required for synthesis")
            args.output.mkdir(parents=True, exist_ok=False)
            rows = [synthesize(str(yosys), sources, variant, args.output) for variant in TOPS]
            write_csv(args.output / "structural.csv", rows)
            provenance = {
                "schema": "a5_fovea_a7_structural_v1",
                "fovea_blobs": FOVEA_BLOBS,
                "a7_evidence_commit": A7_EVIDENCE_COMMIT,
                "a7_evidence_blob_sha256": A7_EVIDENCE_SHA256,
                "a7_endpoint_commit": A7_ENDPOINT_COMMIT,
                "a7_rtl_blobs": A7_RTL_BLOBS,
                "expected_structure": EXPECTED_STRUCTURE,
                "yosys_identity": subprocess.run(
                    [str(yosys), "-V"], text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, check=True,
                ).stdout.strip(),
                "yosys_executable_sha256": digest(Path(yosys).resolve().read_bytes()),
            }
            (args.output / "provenance.json").write_text(
                json.dumps(provenance, indent=2, sort_keys=True) + "\n"
            )
            for row in rows:
                print(" ".join(f"{key}={value}" for key, value in row.items()))
            print("A5_FOVEA_A7_STRUCTURAL_PASS")
            return 0
    except (ContractError, OSError, subprocess.SubprocessError) as error:
        print(f"A5_FOVEA_A7_STRUCTURAL_FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
