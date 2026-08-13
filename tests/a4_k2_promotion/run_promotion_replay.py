#!/usr/bin/env python3
"""Run A4 frozen-v4 transaction vectors on the final A2/A3/A4 K2 owners."""

from __future__ import annotations

import argparse
import copy
import hashlib
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
    "a4": {
        "commit": "0e613b6933f1bb92e9b2f75b79a50663187f17d3",
        "source": "rtl/candidates/a4_paired_cortical_column_k2/a4_paired_cortical_column_k2.sv",
        "source_sha256": "56bde1a765cd750e5b4581e51d90ec1cf6893bcea9cbe904b09aeeafe89a0185",
        "binding": HERE / "a4_owner_binding.sv",
    },
}
OWNER_ORDER = ("a2", "a3", "a4")
PASS_RE = re.compile(r"^A4_K2_REPLAY_PASS (.*)$", re.MULTILINE)
COMMON_ORDERING_PASS_RE = re.compile(
    r"^A4_K2_COMMON_ORDERING_PASS owner=(\S+) generated=3 overrun=1 accepted=2 retired=2$",
    re.MULTILINE)
PRE_ALIGNMENT_BASELINE_COMMIT = "0dda9a738ddd3fc7339063e2bdcdd7034674a354"
# Receipt values produced by PRE_ALIGNMENT_BASELINE_COMMIT.  They are used
# only to report a delta; current results always come from fresh RTL runs.
PRE_ALIGNMENT_BASELINE = {
    "a2": {
        "full50": (106416, 2370, 0, 104046, 104046, 104046, 23, 0),
        "capacity22": (65616, 2370, 0, 63246, 63246, 63246, 23, 0),
        "directed": (7, 1, 4, 2, 2, 2, 0, 0),
    },
    "a3": {
        "full50": (106416, 12771, 0, 93645, 93645, 93611, 265, 0),
        "capacity22": (65616, 8336, 0, 57280, 57280, 57260, 265, 0),
        "directed": (7, 1, 4, 2, 2, 2, 1, 0),
    },
    "a4": {
        "full50": (106416, 4245, 0, 102171, 102171, 102169, 23, 0),
        "capacity22": (65616, 4236, 0, 61380, 61380, 61378, 23, 0),
        "directed": (7, 1, 4, 2, 2, 2, 0, 0),
    },
}
DELTA_FIELDS = (
    "generated", "overrun", "reset_aborted", "accepted", "retired",
    "measured_retired", "max_occ_accept", "max_accept_retire",
)


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


def materialize_git_source(repo: Path, commit: str, source_path: str,
                           expected_sha256: str, destination: Path,
                           label: str) -> dict[str, Any]:
    """Materialize one regular source blob from an exact commit object.

    Mutable HEAD, index, and worktree bytes are deliberately never consulted
    for source selection or content validation.
    """
    if not repo.is_absolute():
        raise ReplayError(f"{label}: owner repository path must be absolute")
    if git_output(repo, "rev-parse", "--is-inside-work-tree") != "true":
        raise ReplayError(f"{label}: owner repository is not a Git worktree")
    if git_output(repo, "cat-file", "-t", commit) != "commit":
        raise ReplayError(f"{label}: pinned owner object is not a commit")

    tree_line = git_output(repo, "ls-tree", commit, "--", source_path)
    metadata, separator, listed_path = tree_line.partition("\t")
    fields = metadata.split()
    if (separator != "\t" or listed_path != source_path or len(fields) != 3 or
            fields[0] != "100644" or fields[1] != "blob"):
        raise ReplayError(f"{label}: pinned owner source is not one regular Git blob")
    blob_oid = fields[2]

    extracted = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{source_path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if extracted.returncode:
        diagnostic = extracted.stderr.decode("utf-8", errors="replace").strip()
        raise ReplayError(f"{label}: cannot extract pinned owner source: {diagnostic}")
    observed_sha256 = hashlib.sha256(extracted.stdout).hexdigest()
    if observed_sha256 != expected_sha256:
        raise ReplayError(
            f"{label}: pinned owner blob SHA-256 mismatch expected={expected_sha256} "
            f"observed={observed_sha256}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(extracted.stdout)
    if exporter.file_sha256(destination) != expected_sha256:
        raise ReplayError(f"{label}: materialized owner source changed after extraction")
    return {
        "path": destination,
        "commit": commit,
        "source_path": source_path,
        "source_blob_oid": blob_oid,
        "source_sha256": expected_sha256,
        "source_origin": "exact_git_commit_object",
    }


def materialize_owner(repo: Path, owner: str, work_root: Path) -> dict[str, Any]:
    pin = OWNER_PINS[owner]
    destination = work_root / "owner-sources" / owner / Path(str(pin["source"])).name
    return materialize_git_source(
        repo, str(pin["commit"]), str(pin["source"]),
        str(pin["source_sha256"]), destination, owner)


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


def compile_common_ordering_test(iverilog: Path, owner: str, source: Path,
                                 work_root: Path) -> Path:
    executable = work_root / f"{owner}-a4-k2-common-ordering.vvp"
    arguments = [
        str(iverilog), "-g2012", "-s", "a4_k2_common_ordering_tb",
        "-o", str(executable), str(source), str(OWNER_PINS[owner]["binding"]),
        str(HERE / "a4_k2_transaction_boundary.sv"),
        str(HERE / "a4_k2_common_ordering_tb.sv"),
    ]
    result = command(arguments, cwd=HERE)
    if result.returncode:
        raise ReplayError(f"{owner}: common-ordering compilation failed:\n{result.stdout}")
    return executable


def run_common_ordering_test(vvp: Path, executable: Path, owner: str) -> dict[str, Any]:
    result = command([str(vvp), str(executable), f"+OWNER={owner}"])
    match = COMMON_ORDERING_PASS_RE.search(result.stdout)
    if result.returncode or match is None or match.group(1) != owner:
        raise ReplayError(f"{owner}: common-ordering RTL test failed:\n{result.stdout}")
    return {
        "owner": owner, "generated": 3, "source_overrun": 1,
        "accepted": 2, "retired": 2, "status": "PASS",
    }


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


def result_delta(owner_results: list[dict[str, Any]]) -> dict[str, Any]:
    owners: dict[str, Any] = {}
    unchanged = True
    for owner_result in owner_results:
        owner = owner_result["owner"]
        suites: dict[str, Any] = {}
        for suite in ("full50", "capacity22", "directed"):
            rows = [row for row in owner_result["runs"] if row["suite"] == suite]
            observed = (
                sum(row["generated"] for row in rows),
                sum(row["overrun"] for row in rows),
                sum(row["reset_aborted"] for row in rows),
                sum(row["accepted"] for row in rows),
                sum(row["retired"] for row in rows),
                sum(row["measured_retired"] for row in rows),
                max(row["max_occ_accept"] for row in rows),
                max(row["max_accept_retire"] for row in rows),
            )
            baseline = PRE_ALIGNMENT_BASELINE[owner][suite]
            delta = {field: current - old for field, current, old in
                     zip(DELTA_FIELDS, observed, baseline)}
            unchanged = unchanged and all(value == 0 for value in delta.values())
            suites[suite] = {
                "old": dict(zip(DELTA_FIELDS, baseline)),
                "new": dict(zip(DELTA_FIELDS, observed)),
                "delta": delta,
            }
        owners[owner] = suites
    return {
        "baseline_commit": PRE_ALIGNMENT_BASELINE_COMMIT,
        "baseline_role": "comparison_only_never_reused_as_current_result",
        "all_metrics_unchanged": unchanged,
        "owners": owners,
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
    parser.add_argument("--a4-repo", type=Path, default=HERE.parents[1])
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
        iverilog = resolve_tool(args.iverilog, "iverilog")
        vvp = resolve_tool(args.vvp, "vvp")
        args.work_dir.mkdir(parents=True)
        owner_materialization = {
            "a2": materialize_owner(args.a2_repo.resolve(), "a2", args.work_dir),
            "a3": materialize_owner(args.a3_repo.resolve(), "a3", args.work_dir),
            "a4": materialize_owner(args.a4_repo.resolve(), "a4", args.work_dir),
        }
        owner_sources = {owner: record["path"]
                         for owner, record in owner_materialization.items()}
        trace_root = args.work_dir / "traces"
        vector_root = args.work_dir / "vectors"
        bundle = exporter.build_export(a1_repo, trace_root, vector_root)
        exporter.verify_export(bundle, vector_root, trace_root)
        exporter.write_new(args.work_dir / "vector-bundle.json", bundle)

        executables = {owner: compile_owner(iverilog, owner, source, args.work_dir)
                       for owner, source in owner_sources.items()}
        ordering_executables = {
            owner: compile_common_ordering_test(iverilog, owner, source, args.work_dir)
            for owner, source in owner_sources.items()
        }
        ordering_results = [
            run_common_ordering_test(vvp, ordering_executables[owner], owner)
            for owner in OWNER_ORDER
        ]
        mutations = run_mutation_gate(a1_repo, bundle, trace_root, vector_root,
                                      vvp, executables["a2"])
        owner_results = []
        for owner in OWNER_ORDER:
            owner_results.append(simulate_owner(vvp, executables[owner], owner,
                                                bundle, vector_root))
            print(f"A4_K2_OWNER_REPLAY_PASS owner={owner} runs={owner_results[-1]['run_count']}")

        report = {
            "schema": "a4_a2_a3_a4_k2_digital_promotion_replay_v2",
            "qualification": "OWNER_RTL_TRANSACTION_REPLAY_PASS",
            "vector_bundle_sha256": bundle["bundle_sha256"],
            "provenance": {
                owner: {
                    "commit": OWNER_PINS[owner]["commit"],
                    "source_path": OWNER_PINS[owner]["source"],
                    "source_blob_oid": owner_materialization[owner]["source_blob_oid"],
                    "source_sha256": OWNER_PINS[owner]["source_sha256"],
                    "source_origin": owner_materialization[owner]["source_origin"],
                    "binding_sha256": exporter.file_sha256(OWNER_PINS[owner]["binding"]),
                } for owner in OWNER_ORDER
            },
            "suite_run_counts": {"full50": 50, "capacity22": 22, "directed": 1},
            "common_edge_ordering": {
                "a1_common_tb_sha256": exporter.PINNED["common_tb_sha256"],
                "rule": "occurrence_and_overrun_classification_precedes_following_posedge_accept_and_retire",
                "same_source_same_edge_outcome": "new_occurrence_is_source_overrun_then_old_event_fires",
                "next_occurrence_edge_outcome": "source_is_rearmed",
                "focused_owner_results": ordering_results,
            },
            "old_new_result_delta": result_delta(owner_results),
            "mutation_kills": mutations,
            "owners": owner_results,
        }
        write_new(args.output, report)
    except (ReplayError, exporter.ExportError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"A4_K2_PROMOTION_REPLAY_FAIL {error}", file=sys.stderr)
        return 2
    print(f"A4_K2_PROMOTION_REPLAY_PASS owners=3 runs_per_owner=73 ordering_tests=3 mutations={len(mutations)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
