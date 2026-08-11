#!/usr/bin/env python3
"""Same-flow generic structural accounting for A7 owner and A9 W5 wrapper."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl/candidates/a9_w5_ddr_technology_boundary"
A7_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
A7_RTL = {
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv":
        "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv":
        "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv":
        "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv":
        "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv":
        "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv":
        "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv":
        "151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748",
}
EXPECTED = {
    "a7_ddr2": {"charged_functional_cells": 29, "state_bits": 20,
                 "operator_depth": 7, "generic_gate_depth": 7},
    "a7_parallel4": {"charged_functional_cells": 27, "state_bits": 18,
                     "operator_depth": 7, "generic_gate_depth": 7},
    "a9_generic_ddr2": {"charged_functional_cells": 33, "state_bits": 22,
                        "operator_depth": 7, "generic_gate_depth": 7},
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")


def tool_path(override: str | None, name: str, fallback: str) -> pathlib.Path:
    candidate = override or shutil.which(name) or fallback
    path = pathlib.Path(candidate).resolve()
    if not path.is_file():
        raise RuntimeError(f"required {name} not found: {path}")
    return path


def yosys_environment(yosys: pathlib.Path) -> dict[str, str]:
    environment = os.environ.copy()
    candidate_lib = yosys.parents[1] / "lib/x86_64-linux-gnu"
    if candidate_lib.is_dir():
        old = environment.get("LD_LIBRARY_PATH")
        environment["LD_LIBRARY_PATH"] = str(candidate_lib) + (f":{old}" if old else "")
    return environment


def materialize_a7(git: pathlib.Path, repo: pathlib.Path,
                   output: pathlib.Path) -> list[pathlib.Path]:
    sources = []
    for index, (repo_path, expected_hash) in enumerate(A7_RTL.items()):
        result = subprocess.run(
            [str(git), "-C", str(repo), "show", f"{A7_COMMIT}:{repo_path}"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        actual_hash = hashlib.sha256(result.stdout).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"A7 source hash mismatch: {repo_path}")
        target = output / f"a7_{index}_{pathlib.Path(repo_path).name}"
        target.write_bytes(result.stdout)
        sources.append(target)
    return sources


def state_bits(histogram: dict[str, int]) -> int:
    total = 0
    for name, count in histogram.items():
        lowered = name.lower()
        if "dff" not in lowered and "dlatch" not in lowered:
            continue
        width = re.search(r"_(\d+)$", name)
        total += count * (int(width.group(1)) if width else 1)
    return total


def synthesize(yosys: pathlib.Path, environment: dict[str, str], top: str,
               sources: list[pathlib.Path], defines: list[str],
               include_dirs: list[pathlib.Path]) -> dict[str, int]:
    with tempfile.TemporaryDirectory(prefix="a9-w5-yosys-") as directory:
        stat_path = pathlib.Path(directory) / "stat.json"
        read_options = ["read_verilog", "-sv"]
        read_options += [f"-I{path}" for path in include_dirs]
        read_options += [f"-D{define}" for define in defines]
        command = (
            " ".join(read_options + [str(path) for path in sources]) + "; "
            f"hierarchy -top {top}; proc; flatten; opt; "
            f"tee -o {stat_path} stat -json -width; ltp -noff; "
            "techmap; opt; ltp -noff"
        )
        result = subprocess.run(
            [str(yosys), "-Q", "-p", command], cwd=ROOT, env=environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:])
        document = json.loads(stat_path.read_text())
        module = next(iter(document["modules"].values()))
        histogram = module["num_cells_by_type"]
        scopeinfo = sum(
            count for name, count in histogram.items()
            if "scopeinfo" in name.lower()
        )
        depths = [int(value) for value in DEPTH_RE.findall(result.stdout)]
        if len(depths) != 2:
            raise RuntimeError("expected pre/post-techmap depth reports")
        return {
            "charged_functional_cells": module["num_cells"] - scopeinfo,
            "state_bits": state_bits(histogram),
            "operator_depth": depths[0],
            "generic_gate_depth": depths[1],
            "excluded_scopeinfo_cells": scopeinfo,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--a7-repo", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get("A7_REPO", ROOT.parent / "a7")))
    parser.add_argument("--yosys", default=os.environ.get("YOSYS"))
    args = parser.parse_args()

    yosys = tool_path(args.yosys, "yosys", "/tmp/a7-yosys/usr/bin/yosys")
    git = tool_path(None, "git", "/usr/bin/git")
    environment = yosys_environment(yosys)
    with tempfile.TemporaryDirectory(prefix="a9-w5-a7-rtl-") as directory:
        a7_sources = materialize_a7(git, args.a7_repo.resolve(), pathlib.Path(directory))
        rows = {
            "a7_ddr2": synthesize(yosys, environment, "a7_r1_candidate_endpoint",
                                   a7_sources, [], []),
            "a7_parallel4": synthesize(
                yosys, environment, "a7_r1_parallel_reference_top", a7_sources, [], []
            ),
            "a9_generic_ddr2": synthesize(
                yosys, environment, "a9_w5_ddr_link",
                sorted(RTL.glob("*.sv")), ["A9_W5_TECH_GENERIC"], [RTL]
            ),
        }

    for name, expected in EXPECTED.items():
        observed = {key: rows[name][key] for key in expected}
        if observed != expected:
            raise RuntimeError(f"{name} structural drift: {observed} != {expected}")
    delta = {
        key: rows["a9_generic_ddr2"][key] - rows["a7_ddr2"][key]
        for key in ("charged_functional_cells", "state_bits",
                    "operator_depth", "generic_gate_depth")
    }
    document = {
        "classification": "generic same-flow structural proxy, not physical PPA",
        "yosys_version": subprocess.run(
            [str(yosys), "-V"], env=environment, check=True,
            text=True, stdout=subprocess.PIPE
        ).stdout.strip(),
        "flow": "read_verilog -sv; hierarchy; proc; flatten; opt; stat -json -width; ltp -noff; techmap; opt; ltp -noff",
        "rows": rows,
        "a9_minus_a7_ddr2": delta,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
