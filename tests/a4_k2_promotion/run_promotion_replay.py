#!/usr/bin/env python3
"""Run A4 frozen-v4 transaction vectors on the final A2/A3 K2 owners."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import export_frozen_v4 as exporter


HERE = Path(__file__).resolve().parent
OWNER_PINS = {
    "a2": {
        "commit": "d74ff962aaf07c5209f1a1d1c69832735c654a0d",
        "source": "candidates/a2_batched_iwrr_k2/rtl/a2_batched_iwrr_k2.sv",
        "source_sha256": "800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d",
        "binding": HERE / "a2_owner_binding.sv",
    },
    "a3": {
        "commit": "bd1c1ee955685fc077afe930116a03bc49a8218f",
        "source": "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv",
        "source_sha256": "bd00ade6ebd5f6c5e03ff356393a59f1baf6d890cfb3809a10bf0cda3bb1b0d9",
        "binding": HERE / "a3_owner_binding.sv",
    },
}
PASS_RE = re.compile(r"^A4_K2_REPLAY_PASS (.*)$", re.MULTILINE)


class ReplayError(RuntimeError):
    pass


def command(arguments: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)


def git_output(repo: Path, *arguments: str) -> str:
    result = command(["git", "-C", str(repo), *arguments])
    if result.returncode:
        raise ReplayError(f"git {' '.join(arguments)} failed in {repo}:\n{result.stdout}")
    return result.stdout.strip()


def verify_owner(repo: Path, owner: str) -> Path:
    pin = OWNER_PINS[owner]
    if not repo.is_absolute() or not (repo / ".git").exists():
        raise ReplayError(f"{owner}: owner repository must be an absolute Git worktree")
    observed_head = git_output(repo, "rev-parse", "HEAD")
    if observed_head != pin["commit"]:
        raise ReplayError(f"{owner}: expected final owner commit {pin['commit']}, observed {observed_head}")
    source = repo / str(pin["source"])
    if exporter.file_sha256(source) != pin["source_sha256"]:
        raise ReplayError(f"{owner}: final owner source SHA-256 mismatch")
    tree = git_output(repo, "ls-tree", pin["commit"], str(pin["source"])).split()
    if len(tree) != 4 or tree[0] != "100644" or tree[1] != "blob":
        raise ReplayError(f"{owner}: owner source is not a regular committed blob")
    committed = command(["git", "-C", str(repo), "show",
                         f"{pin['commit']}:{pin['source']}"])
    if committed.returncode or committed.stdout.encode() != source.read_bytes():
        raise ReplayError(f"{owner}: worktree source differs from pinned commit blob")
    return source


def resolve_tool(explicit: Path | None, name: str) -> Path:
    candidates = ([explicit] if explicit else []) + [
        Path(found) if (found := shutil.which(name)) else None,
        Path(f"/tmp/a7-toolchain/usr/bin/{name}"),
        Path(f"/tmp/a6-w7-iverilog-pkg/usr/bin/{name}"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise ReplayError(f"required simulator tool unavailable: {name}")


def compile_owner(iverilog: Path, owner: str, source: Path, work_root: Path) -> Path:
    executable = work_root / f"{owner}-a4-k2-replay.vvp"
    arguments = [str(iverilog), "-g2012", "-s", "a4_k2_replay_driver", "-o", str(executable),
                 str(source), str(OWNER_PINS[owner]["binding"]),
                 str(HERE / "a4_k2_transaction_boundary.sv"),
                 str(HERE / "a4_k2_replay_driver.sv")]
    result = command(arguments, cwd=HERE)
    if result.returncode:
        raise ReplayError(f"{owner}: replay compilation failed:\n{result.stdout}")
    return executable


def parse_metrics(output: str) -> dict[str, Any]:
    matches = PASS_RE.findall(output)
    if len(matches) != 1:
        raise ReplayError(f"simulator did not emit exactly one replay PASS:\n{output}")
    metrics: dict[str, Any] = {}
    for token in matches[0].split():
        key, value = token.split("=", 1)
        metrics[key] = value if key in {"suite", "run"} else int(value)
    return metrics


def all_records(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    records = [record for suite in ("full50", "capacity22")
               for record in bundle["suites"][suite]["runs"]]
    return records + bundle["directed_runs"]


def simulate_owner(vvp: Path, executable: Path, owner: str, bundle: dict[str, Any],
                   vector_root: Path) -> dict[str, Any]:
    results = []
    for record in all_records(bundle):
        vector = vector_root / record["vector_file"]
        result = command([str(vvp), str(executable), f"+VECTOR={vector}",
                          f"+SUITE={record['suite']}", f"+RUN={record['name']}"])
        if result.returncode:
            raise ReplayError(f"{owner}/{record['suite']}/{record['name']} failed:\n{result.stdout}")
        metrics = parse_metrics(result.stdout)
        if (metrics["generated"] != record["expected_generated_events"] or
                metrics["measured_generated"] != record["expected_measurement_generated_events"] or
                metrics["accepted"] != metrics["retired"] or
                metrics["max_accept_retire"] != 0):
            raise ReplayError(f"{owner}/{record['name']}: replay receipt differs from vector contract")
        if record["origin"] == "a4_directed_reset_drain":
            if (metrics["overrun"] != record["expected_source_overrun_events"] or
                    metrics["reset_aborted"] != record["expected_reset_aborted_events"] or
                    metrics["reset_cycles"] != len(record["expected_reset_cycles"])):
                raise ReplayError(
                    f"{owner}/reset_drain: directed reset accounting mismatch "
                    f"observed={metrics} expected_overrun={record['expected_source_overrun_events']} "
                    f"expected_aborted={record['expected_reset_aborted_events']}")
        results.append(metrics)
    return {
        "owner": owner,
        "run_count": len(results),
        "generated": sum(item["generated"] for item in results),
        "source_overrun": sum(item["overrun"] for item in results),
        "reset_aborted": sum(item["reset_aborted"] for item in results),
        "accepted": sum(item["accepted"] for item in results),
        "retired": sum(item["retired"] for item in results),
        "measurement_retired": sum(item["measured_retired"] for item in results),
        "max_occurrence_to_accept_latency": max(item["max_occ_accept"] for item in results),
        "max_accept_to_retire_latency": max(item["max_accept_retire"] for item in results),
        "runs": results,
    }


def _rehash_bundle(document: dict[str, Any]) -> None:
    document.pop("bundle_sha256", None)
    document["bundle_sha256"] = exporter.object_sha256(document)


def run_mutation_gate(a1_repo: Path, bundle: dict[str, Any], trace_root: Path,
                      vector_root: Path, vvp: Path | None = None,
                      executable: Path | None = None) -> list[str]:
    official, _, _ = exporter.load_official(a1_repo)
    manifest = exporter.manifest_path(a1_repo, official, "full50")
    generated = trace_root / "full50"
    first = bundle["suites"]["full50"]["runs"][0]
    killed: list[str] = []

    trace = generated / first["trace_file"]
    original_trace = trace.read_bytes()
    try:
        lines = trace.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[0])
        event["logical_source"] = (int(event["logical_source"]) + 1) % 16
        lines[0] = json.dumps(event, separators=(",", ":"))
        trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            exporter.validate_generation(generated, "full50", manifest, official)
        except exporter.ExportError:
            killed.append("malicious_trace")
        else:
            raise ReplayError("malicious trace mutation escaped frozen SHA validation")
    finally:
        trace.write_bytes(original_trace)

    index = generated / "generation-index.json"
    original_index = index.read_bytes()
    try:
        mutated_index = json.loads(original_index)
        mutated_index["runs"][0], mutated_index["runs"][1] = (
            mutated_index["runs"][1], mutated_index["runs"][0])
        index.write_text(json.dumps(mutated_index, indent=2) + "\n", encoding="utf-8")
        try:
            exporter.validate_generation(generated, "full50", manifest, official)
        except exporter.ExportError:
            killed.append("malicious_generation_index")
        else:
            raise ReplayError("malicious generation-index mutation escaped validation")
    finally:
        index.write_bytes(original_index)

    vector = vector_root / first["vector_file"]
    original_vector = vector.read_bytes()
    try:
        lines = vector.read_text(encoding="ascii").splitlines()
        shifted = False
        for line_index in range(1, len(lines) - 1):
            fields = [int(field) for field in lines[line_index].split()]
            next_fields = [int(field) for field in lines[line_index + 1].split()]
            for source in range(16):
                slot = 4 + source
                if fields[slot] and not next_fields[slot]:
                    next_fields[slot] = fields[slot]
                    fields[slot] = 0
                    fields[3] &= ~(1 << source)
                    next_fields[3] |= 1 << source
                    lines[line_index] = " ".join(str(field) for field in fields)
                    lines[line_index + 1] = " ".join(str(field) for field in next_fields)
                    shifted = True
                    break
            if shifted:
                break
        if not shifted:
            raise ReplayError("could not construct malicious vector time shift")
        vector.write_text("\n".join(lines) + "\n", encoding="ascii")
        malicious = copy.deepcopy(bundle)
        malicious_record = malicious["suites"]["full50"]["runs"][0]
        malicious_record["vector_sha256"] = exporter.file_sha256(vector)
        malicious_record["occurrence_stream_sha256"] = exporter.object_sha256(
            exporter.parse_vector(vector)["occurrences"])
        _rehash_bundle(malicious)
        try:
            exporter.verify_export(malicious, vector_root, trace_root)
        except exporter.ExportError:
            killed.append("malicious_occurrence_time_shift_rehashed")
        else:
            raise ReplayError("self-consistent vector time shift escaped trace comparison")
    finally:
        vector.write_bytes(original_vector)

    if vvp is not None and executable is not None:
        vector = vector_root / first["vector_file"]
        result = command([str(vvp), str(executable), f"+VECTOR={vector}",
                          "+SUITE=full50", f"+RUN={first['name']}",
                          "+A4_MUTATE_TIME_SHIFT=1"])
        if result.returncode == 0 or "vector/index time shift detected" not in result.stdout:
            raise ReplayError("driver time-index mutation was not killed")
        killed.append("malicious_driver_time_index_shift")
    return killed


def write_new(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a1-repo", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--a2-repo", type=Path, default=Path("/home/chickgoose/projects/a2"))
    parser.add_argument("--a3-repo", type=Path, default=Path("/home/chickgoose/projects/a3"))
    parser.add_argument("--iverilog", type=Path)
    parser.add_argument("--vvp", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.work_dir.exists() or args.output.exists():
        print("error: work directory and output must not already exist", file=sys.stderr)
        return 2
    try:
        a1_repo = args.a1_repo.resolve()
        owner_sources = {
            "a2": verify_owner(args.a2_repo.resolve(), "a2"),
            "a3": verify_owner(args.a3_repo.resolve(), "a3"),
        }
        iverilog = resolve_tool(args.iverilog, "iverilog")
        vvp = resolve_tool(args.vvp, "vvp")
        args.work_dir.mkdir(parents=True)
        trace_root = args.work_dir / "traces"
        vector_root = args.work_dir / "vectors"
        bundle = exporter.build_export(a1_repo, trace_root, vector_root)
        exporter.verify_export(bundle, vector_root, trace_root)
        exporter.write_new(args.work_dir / "vector-bundle.json", bundle)

        executables = {owner: compile_owner(iverilog, owner, source, args.work_dir)
                       for owner, source in owner_sources.items()}
        mutations = run_mutation_gate(a1_repo, bundle, trace_root, vector_root,
                                      vvp, executables["a2"])
        owner_results = []
        for owner in ("a2", "a3"):
            owner_results.append(simulate_owner(vvp, executables[owner], owner,
                                                bundle, vector_root))
            print(f"A4_K2_OWNER_REPLAY_PASS owner={owner} runs={owner_results[-1]['run_count']}")

        report = {
            "schema": "a4_a2_a3_k2_digital_promotion_replay_v1",
            "qualification": "OWNER_RTL_TRANSACTION_REPLAY_PASS",
            "vector_bundle_sha256": bundle["bundle_sha256"],
            "provenance": {
                owner: {
                    "commit": OWNER_PINS[owner]["commit"],
                    "source_path": OWNER_PINS[owner]["source"],
                    "source_sha256": OWNER_PINS[owner]["source_sha256"],
                    "binding_sha256": exporter.file_sha256(OWNER_PINS[owner]["binding"]),
                } for owner in ("a2", "a3")
            },
            "suite_run_counts": {"full50": 50, "capacity22": 22, "directed": 1},
            "mutation_kills": mutations,
            "owners": owner_results,
        }
        write_new(args.output, report)
    except (ReplayError, exporter.ExportError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"A4_K2_PROMOTION_REPLAY_FAIL {error}", file=sys.stderr)
        return 2
    print(f"A4_K2_PROMOTION_REPLAY_PASS owners=2 runs_per_owner=73 mutations={len(mutations)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
