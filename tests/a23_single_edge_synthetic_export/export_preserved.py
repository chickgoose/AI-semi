#!/usr/bin/env python3
"""Fail-closed export of a preserved A2/A3 single-edge replay run.

This is intentionally separate from the replay producer.  It never repairs or
regenerates evidence.  A source root is either byte-bound to the requested
result and exported with a closed manifest, or described by a deterministic
HOLD manifest.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
from typing import Any, Iterable


EXPECTED_RESULT_SHA256 = "e21e714e4c4ebbeba4caf63ad5656b2b29fc05881ebb74ea6d93114c5f7d8cf4"
EXPECTED_SOURCE_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
EXPECTED_INTEGRATION_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
EXPECTED_PINS_SHA256 = "0daba2132010272a78b56ec2a1541f30f7cb5d2b0d8562102cb70cf9e098d8e0"
EXPECTED_OWNERS = ("a2", "a3")
EXPECTED_MUTATIONS = ("drop", "duplicate", "reorder", "reset_escape")
EXPECTED_DIAGNOSTICS = {
    "drop": "A23_SE_DROP_FAIL",
    "duplicate": "A23_SE_DUPLICATE_FAIL",
    "reorder": "A23_SE_REORDER_FAIL",
    "reset_escape": "A23_SE_RESET_ESCAPE_FAIL",
}
EXPECTED_EXECUTION_ACCOUNTING = {
    "owners": 2,
    "full50_actual_RTL_executions": 100,
    "reset_actual_RTL_executions": 2,
    "mutation_activation_actual_RTL_executions": 2,
    "mutation_actual_RTL_executions": 8,
    "receipt_only_executions": 0,
}
RESULT_SCHEMA = "a23_full_single_edge_replay_result_v1"
EXPORT_SCHEMA = "a23_single_edge_synthetic_export_v1"
STATUS_SCHEMA = "a23_single_edge_synthetic_export_status_v1"
ARCHIVE_PREFIX = "a23-single-edge-synthetic-export"
PASS_SENTINEL = "A23_SE_ACTUAL_RTL_PASS"
DIAGNOSTIC_RE = re.compile(r"A23_SE_[A-Z_]+")


class RejectError(RuntimeError):
    """Unsafe, ambiguous, or internally inconsistent evidence."""


class HoldError(RuntimeError):
    """Required receipt-bound bytes are absent from the preserved root."""

    def __init__(self, reasons: list[dict[str, Any]]):
        super().__init__("preserved run is not bindable")
        self.reasons = reasons


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_relative(value: str, label: str = "path") -> str:
    if not isinstance(value, str) or not value or "\\" in value \
            or any(ord(character) < 32 for character in value):
        raise RejectError(f"{label} is not a nonempty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RejectError(f"{label} escapes its root: {value!r}")
    normalized = path.as_posix()
    if normalized != value:
        raise RejectError(f"{label} is not canonical: {value!r}")
    return normalized


def load_json_bytes(data: bytes, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RejectError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RejectError(f"{label} is not canonical UTF-8 JSON: {error}") from error


def scan_regular_tree(root: Path) -> tuple[dict[str, os.stat_result], set[str]]:
    """Return every regular file and directory without following any link."""
    try:
        root_stat = root.lstat()
    except FileNotFoundError as error:
        raise HoldError([{"code": "RUN_ROOT_MISSING", "path": str(root)}]) from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise RejectError("run root must be a real directory, not a symlink")

    files: dict[str, os.stat_result] = {}
    directories: set[str] = set()
    inodes: dict[tuple[int, int], str] = {}

    def visit(directory: Path, relative: PurePosixPath) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise RejectError(f"cannot scan {directory}: {error}") from error
        for entry in entries:
            if entry.name in (".", "..") or "/" in entry.name:
                raise RejectError(f"noncanonical directory entry below {directory}")
            child_rel = relative / entry.name
            rel = safe_relative(child_rel.as_posix(), "source path")
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise RejectError(f"symlink is forbidden: {rel}")
            if stat.S_ISDIR(info.st_mode):
                directories.add(rel)
                visit(Path(entry.path), child_rel)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise RejectError(f"hardlinked evidence is forbidden: {rel}")
                identity = (info.st_dev, info.st_ino)
                if identity in inodes:
                    raise RejectError(
                        f"duplicate file inode: {inodes[identity]} and {rel}"
                    )
                inodes[identity] = rel
                files[rel] = info
            else:
                raise RejectError(f"non-regular filesystem object is forbidden: {rel}")

    visit(root, PurePosixPath())
    return files, directories


def read_regular(root: Path, relative: str, expected: os.stat_result) -> bytes:
    relative = safe_relative(relative)
    path = root.joinpath(*PurePosixPath(relative).parts)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RejectError(f"cannot safely open {relative}: {error}") from error
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise RejectError(f"evidence changed type or link count: {relative}")
        if (current.st_dev, current.st_ino, current.st_size) != (
            expected.st_dev, expected.st_ino, expected.st_size
        ):
            raise RejectError(f"evidence changed during validation: {relative}")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if (after.st_mtime_ns, after.st_ctime_ns, after.st_size) != (
            current.st_mtime_ns, current.st_ctime_ns, current.st_size
        ):
            raise RejectError(f"evidence changed while being read: {relative}")
        return data
    finally:
        os.close(descriptor)


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RejectError(f"{label} must be an object")
    return value


def validate_result_contract(result: dict[str, Any]) -> list[str]:
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "PASS":
        raise RejectError("result schema/status is not a passing actual replay")
    if result.get("execution_accounting") != EXPECTED_EXECUTION_ACCOUNTING:
        raise RejectError("result execution accounting is not the exact 112+8 campaign")
    if result.get("generator", {}).get("trace_count") != 50:
        raise RejectError("result does not bind exactly 50 traces")
    owners = require_object(result.get("owners"), "result owners")
    if set(owners) != set(EXPECTED_OWNERS):
        raise RejectError("result owner roster differs from a2/a3")
    rosters: list[list[str]] = []
    for owner in EXPECTED_OWNERS:
        runs = require_object(
            require_object(owners[owner].get("full50"), f"{owner} full50").get("runs"),
            f"{owner} full50 runs",
        )
        if len(runs) != 50:
            raise RejectError(f"{owner} does not contain exactly 50 run claims")
        for name in runs:
            safe_relative(name, f"{owner} run name")
            if "/" in name:
                raise RejectError(f"{owner} run name contains a separator")
        rosters.append(sorted(runs))
    if rosters[0] != rosters[1]:
        raise RejectError("A2/A3 full50 rosters differ")
    mutations = result.get("mutations")
    if not isinstance(mutations, list) or len(mutations) != 8:
        raise RejectError("result must contain exactly eight mutation claims")
    observed = [(row.get("owner"), row.get("mutation")) for row in mutations]
    expected = [(owner, mutation) for owner in EXPECTED_OWNERS for mutation in EXPECTED_MUTATIONS]
    if observed != expected:
        raise RejectError("mutation roster/order differs")
    return rosters[0]


def binding_reasons(result_sha: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    provenance = require_object(result.get("provenance"), "result provenance")
    rtl = require_object(provenance.get("actual_rtl_git"), "result actual_rtl_git")
    checks = (
        ("RESULT_SHA256_MISMATCH", EXPECTED_RESULT_SHA256, result_sha),
        ("SOURCE_COMMIT_MISMATCH", EXPECTED_SOURCE_COMMIT, rtl.get("source_commit")),
        ("INTEGRATION_COMMIT_MISMATCH", EXPECTED_INTEGRATION_COMMIT, rtl.get("integration_commit")),
        ("PINS_SHA256_MISMATCH", EXPECTED_PINS_SHA256, provenance.get("pins_sha256")),
    )
    return [
        {"code": code, "expected": expected, "observed": observed}
        for code, expected, observed in checks if observed != expected
    ]


def expected_evidence_paths(names: list[str]) -> set[str]:
    paths = {"result.json", "work/generator-v4/generation-index.json",
             "work/logs/generator-v4.log"}
    for name in names:
        paths.update({
            f"work/generator-v4/{name}.events.jsonl",
            f"work/generator-v4/{name}.manifest.json",
            f"work/prepared/{name}.trace",
            f"work/logs/prepare-{name}.log",
        })
    for owner in EXPECTED_OWNERS:
        for mutation in ("none", *EXPECTED_MUTATIONS):
            paths.add(f"work/logs/build-{owner}-{mutation}.log")
        for name in (*names, "reset_drain_epochs", "directed_distinct_pair"):
            base = f"work/artifacts/{owner}/none/{name}"
            paths.update({f"{base}/events.csv", f"{base}/summary.csv",
                          f"{base}/simulation.log"})
        for mutation in EXPECTED_MUTATIONS:
            case = "reset_drain_epochs" if mutation == "reset_escape" else "directed_distinct_pair"
            paths.add(f"work/artifacts/{owner}/{mutation}/{case}/simulation.log")
            paths.add(
                f"work/mutated-rtl/{owner}/{mutation}/w2_single_edge_pair_rx.sv"
            )
    return paths


def ancestors(paths: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in paths:
        parts = PurePosixPath(value).parts[:-1]
        for index in range(1, len(parts) + 1):
            result.add(PurePosixPath(*parts[:index]).as_posix())
    return result


def closed_inventory(
    files: dict[str, os.stat_result], directories: set[str], required: set[str]
) -> tuple[set[str], set[str]]:
    missing = required - set(files)
    scratch = {path for path in files if path.startswith("work/build/")}
    extra = set(files) - required - scratch
    if extra:
        raise RejectError(f"unexpected source-root files: {sorted(extra)}")
    allowed_directories = ancestors(required) | ancestors(scratch) | {"work/build"}
    extra_directories = directories - allowed_directories
    if extra_directories:
        raise RejectError(f"unexpected source-root directories: {sorted(extra_directories)}")
    return missing, scratch


def latency_summary(values: list[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {
        "count": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(.50), "p95": percentile(.95),
        "p99": percentile(.99), "max": ordered[-1],
    }


def csv_rows(data: bytes, expected_fields: set[str], label: str) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    except UnicodeDecodeError as error:
        raise RejectError(f"{label} is not UTF-8 CSV") from error
    if set(reader.fieldnames or ()) != expected_fields:
        raise RejectError(f"{label} CSV fields differ")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise RejectError(f"{label} contains malformed CSV rows")
    return rows


def validate_case(
    owner: str, name: str, claim: dict[str, Any], event_data: bytes,
    summary_data: bytes, log_data: bytes, trace_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    event_fields = {"owner", "trace", "tb_event_id", "logical_source",
                    "occurrence_cycle", "accept_cycle", "retire_cycle",
                    "deadline_cycle", "event_state"}
    summary_fields = {"owner", "trace", "generated", "source_overrun", "accepted",
                      "retired", "fixed_window_retired", "fixed_window_cycles",
                      "observation_cycles", "count2_commits", "reset_test",
                      "pre_reset_clean_drain"}
    events = csv_rows(event_data, event_fields, f"{owner}/{name}/events")
    summaries = csv_rows(summary_data, summary_fields, f"{owner}/{name}/summary")
    if len(summaries) != 1:
        raise RejectError(f"{owner}/{name} summary cardinality differs")
    summary = summaries[0]
    if summary["owner"] != owner or summary["trace"] != name:
        raise RejectError(f"{owner}/{name} summary identity differs")
    if trace_rows is not None and len(trace_rows) != len(events):
        raise RejectError(f"{owner}/{name} trace/event cardinality differs")
    occurrence_offset: int | None = None
    occurrence_accept: list[int] = []
    accept_retire: list[int] = []
    overruns = retired = 0
    for index, event in enumerate(events):
        if (event["owner"] != owner or event["trace"] != name
                or int(event["tb_event_id"]) != index):
            raise RejectError(f"{owner}/{name} event identity/order differs")
        source = int(event["logical_source"])
        occurrence = int(event["occurrence_cycle"])
        deadline = int(event["deadline_cycle"])
        if not 0 <= source < 16 or deadline < occurrence:
            raise RejectError(f"{owner}/{name} event provenance differs")
        if trace_rows is not None:
            trace = trace_rows[index]
            identity = (trace.get("tb_only_event_id"), trace.get("logical_source"),
                        trace.get("deadline"))
            if identity != (index, source, deadline):
                raise RejectError(f"{owner}/{name} event differs from generated trace")
            offset = occurrence - int(trace["occurrence_cycle"])
            occurrence_offset = offset if occurrence_offset is None else occurrence_offset
            if offset != occurrence_offset:
                raise RejectError(f"{owner}/{name} occurrence-cycle mapping is inconsistent")
        if event["event_state"] == "source_overrun":
            if int(event["accept_cycle"]) != -1 or int(event["retire_cycle"]) != -1:
                raise RejectError(f"{owner}/{name} overrun has endpoint timing")
            overruns += 1
        elif event["event_state"] == "retired":
            accept, retire = int(event["accept_cycle"]), int(event["retire_cycle"])
            if not occurrence <= accept <= retire:
                raise RejectError(f"{owner}/{name} endpoint latency is inverted")
            occurrence_accept.append(accept - occurrence)
            accept_retire.append(retire - accept)
            retired += 1
        else:
            raise RejectError(f"{owner}/{name} contains nonterminal event state")
    numeric_keys = ("generated", "source_overrun", "accepted", "retired",
                    "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
                    "count2_commits", "reset_test", "pre_reset_clean_drain")
    numeric = {key: int(summary[key]) for key in numeric_keys}
    if numeric["generated"] != len(events) or numeric["source_overrun"] != overruns:
        raise RejectError(f"{owner}/{name} summary/event accounting differs")
    if numeric["generated"] != overruns + retired or numeric["accepted"] != retired \
            or numeric["retired"] != retired:
        raise RejectError(f"{owner}/{name} conservation differs")
    if numeric["fixed_window_retired"] > retired:
        raise RejectError(f"{owner}/{name} fixed-window retirement exceeds total")
    computed = {
        **numeric,
        "occurrence_to_accept": latency_summary(occurrence_accept),
        "accept_to_retire": latency_summary(accept_retire),
        "fixed_window_events_per_cycle": round(
            numeric["fixed_window_retired"] / max(1, numeric["fixed_window_cycles"]), 9
        ),
        "summary_sha256": sha256_bytes(summary_data),
        "events_sha256": sha256_bytes(event_data),
    }
    for key, value in computed.items():
        if claim.get(key) != value:
            raise RejectError(f"{owner}/{name} receipt claim differs for {key}")
    log_text = log_data.decode("utf-8", errors="strict")
    if log_text.count(PASS_SENTINEL) != 1:
        raise RejectError(f"{owner}/{name} simulator log lacks exactly one PASS sentinel")
    mode = ("reset" if name == "reset_drain_epochs" else
            "pair" if name == "directed_distinct_pair" else "full")
    expected_fragment = (
        f"owner={owner} trace={name} mode={mode} "
        f"generated={numeric['generated']} source_overrun={numeric['source_overrun']} "
        f"accepted={numeric['accepted']} retired={numeric['retired']}"
    )
    if expected_fragment not in log_text:
        raise RejectError(f"{owner}/{name} PASS log accounting differs")
    return {
        "public": computed,
        "occurrence_accept": occurrence_accept,
        "accept_retire": accept_retire,
    }


def load_json_lines(data: bytes, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RejectError(f"{label} is not UTF-8 JSONL") from error
    for index, line in enumerate(lines):
        value = load_json_bytes(line.encode(), f"{label} line {index + 1}")
        rows.append(require_object(value, f"{label} line {index + 1}"))
    return rows


def validate_prepared(data: bytes, trace_rows: list[dict[str, Any]], label: str) -> None:
    try:
        lines = data.decode("utf-8").splitlines()
        header = lines[0].split()
    except (UnicodeDecodeError, IndexError) as error:
        raise RejectError(f"{label} is not a prepared trace") from error
    if len(header) != 9 or int(header[0]) != 4 or int(header[1]) != len(trace_rows):
        raise RejectError(f"{label} prepared header differs")
    if len(lines) != len(trace_rows) + 1:
        raise RejectError(f"{label} prepared cardinality differs")
    for index, (line, trace) in enumerate(zip(lines[1:], trace_rows)):
        fields = [int(value) for value in line.split()]
        expected = [int(trace["occurrence_cycle"]), index, int(trace["logical_source"]),
                    int(trace["logical_source"]), int(trace["deadline"])]
        if fields != expected:
            raise RejectError(f"{label} prepared row {index} differs from JSONL")


def validate_claims(
    root: Path, files: dict[str, os.stat_result], result: dict[str, Any], names: list[str]
) -> None:
    cache: dict[str, bytes] = {}
    def read(path: str) -> bytes:
        if path not in cache:
            cache[path] = read_regular(root, path, files[path])
        return cache[path]

    index = require_object(
        load_json_bytes(read("work/generator-v4/generation-index.json"), "generation index"),
        "generation index",
    )
    index_runs = index.get("runs")
    index_names = ({row.get("run", {}).get("name") for row in index_runs}
                   if isinstance(index_runs, list) else set())
    if not isinstance(index_runs, list) or index_names != set(names):
        raise RejectError("generation index roster differs from result")
    full_runs: dict[str, list[dict[str, Any]]] = {owner: [] for owner in EXPECTED_OWNERS}
    for name in names:
        event_path = f"work/generator-v4/{name}.events.jsonl"
        manifest_path = f"work/generator-v4/{name}.manifest.json"
        prepared_path = f"work/prepared/{name}.trace"
        rows = load_json_lines(read(event_path), f"trace {name}")
        manifest = require_object(load_json_bytes(read(manifest_path), f"manifest {name}"),
                                  f"manifest {name}")
        if manifest.get("run", {}).get("name") != name or manifest.get("event_count") != len(rows):
            raise RejectError(f"generator manifest identity/count differs: {name}")
        trace_sha = sha256_bytes(read(event_path))
        if manifest.get("trace_sha256") != trace_sha:
            raise RejectError(f"generator manifest trace hash differs: {name}")
        validate_prepared(read(prepared_path), rows, f"prepared {name}")
        for owner in EXPECTED_OWNERS:
            claim = result["owners"][owner]["full50"]["runs"][name]
            if claim.get("trace_sha256") != trace_sha:
                raise RejectError(f"{owner}/{name} trace hash differs")
            if claim.get("prepared_trace_sha256") != sha256_bytes(read(prepared_path)):
                raise RejectError(f"{owner}/{name} prepared hash differs")
            base = f"work/artifacts/{owner}/none/{name}"
            full_runs[owner].append(validate_case(
                owner, name, claim, read(f"{base}/events.csv"),
                read(f"{base}/summary.csv"), read(f"{base}/simulation.log"), rows
            ))

    for owner in EXPECTED_OWNERS:
        owner_result = result["owners"][owner]
        if owner_result["full50"].get("actual_execution_count") != 50:
            raise RejectError(f"{owner} full50 execution count differs")
        runs = full_runs[owner]
        totals = {
            key: sum(run["public"][key] for run in runs)
            for key in ("generated", "source_overrun", "accepted", "retired",
                        "fixed_window_retired", "fixed_window_cycles", "count2_commits")
        }
        occurrence = [value for run in runs for value in run["occurrence_accept"]]
        internal = [value for run in runs for value in run["accept_retire"]]
        aggregate = {
            "actual_execution_count": 50,
            "totals": totals,
            "occurrence_to_accept": latency_summary(occurrence),
            "accept_to_retire": latency_summary(internal),
            "fixed_window_events_per_cycle": round(
                totals["fixed_window_retired"] / max(1, totals["fixed_window_cycles"]), 9
            ),
        }
        if owner_result["full50"].get("aggregate") != aggregate:
            raise RejectError(f"{owner} full50 aggregate claims differ")
        baseline_log = read(f"work/logs/build-{owner}-none.log")
        if sha256_bytes(baseline_log) != owner_result["baseline_build_log_sha256"]:
            raise RejectError(f"{owner} baseline build log hash differs")
        for name, key in (("reset_drain_epochs", "reset"),
                          ("directed_distinct_pair", "mutation_activation")):
            base = f"work/artifacts/{owner}/none/{name}"
            claim = owner_result[key]
            validate_case(owner, name, claim, read(f"{base}/events.csv"),
                          read(f"{base}/summary.csv"), read(f"{base}/simulation.log"), None)
            if sha256_bytes(read(f"{base}/simulation.log")) != claim["simulation_log_sha256"]:
                raise RejectError(f"{owner}/{name} simulator log hash differs")

    mutation_map = {(row["owner"], row["mutation"]): row for row in result["mutations"]}
    for owner in EXPECTED_OWNERS:
        for mutation in EXPECTED_MUTATIONS:
            claim = mutation_map[(owner, mutation)]
            if any(claim.get(key) is not True for key in (
                "actual_endpoint_RTL_source_rewrite", "compiled_successfully",
                "executed", "killed",
            )) or not isinstance(claim.get("exit_code"), int) or claim["exit_code"] == 0:
                raise RejectError(f"{owner}/{mutation} mutation outcome differs")
            if claim.get("first_diagnostic") != EXPECTED_DIAGNOSTICS[mutation]:
                raise RejectError(f"{owner}/{mutation} receipt diagnostic differs")
            build = read(f"work/logs/build-{owner}-{mutation}.log")
            if sha256_bytes(build) != claim["build_log_sha256"]:
                raise RejectError(f"{owner}/{mutation} build log hash differs")
            case = "reset_drain_epochs" if mutation == "reset_escape" else "directed_distinct_pair"
            sim_path = f"work/artifacts/{owner}/{mutation}/{case}/simulation.log"
            simulation = read(sim_path)
            if sha256_bytes(simulation) != claim["simulation_log_sha256"]:
                raise RejectError(f"{owner}/{mutation} simulation log hash differs")
            text = simulation.decode("utf-8", errors="strict")
            diagnostic = DIAGNOSTIC_RE.search(text)
            if diagnostic is None or diagnostic.group(0) != EXPECTED_DIAGNOSTICS[mutation] \
                    or PASS_SENTINEL in text:
                raise RejectError(f"{owner}/{mutation} mutation activation differs")
            source_path = f"work/mutated-rtl/{owner}/{mutation}/w2_single_edge_pair_rx.sv"
            identity = require_object(claim.get("source_identity"),
                                      f"{owner}/{mutation} source identity")
            if identity.get("target") != "rtl/technology/single_edge/w2_single_edge_pair_rx.sv" \
                    or identity.get("literal_replacement_count") != 1:
                raise RejectError(f"{owner}/{mutation} source identity contract differs")
            if sha256_bytes(read(source_path)) != identity.get("mutant_sha256"):
                raise RejectError(f"{owner}/{mutation} mutant source hash differs")


def inventory_entries(
    root: Path, files: dict[str, os.stat_result], selected: Iterable[str]
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    entries: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    for source_path in sorted(selected):
        data = read_regular(root, source_path, files[source_path])
        archive_path = safe_relative(f"payload/{source_path}", "archive path")
        contents[archive_path] = data
        entries.append({
            "archive_path": archive_path,
            "source_path": source_path,
            "size_bytes": len(data),
            "sha256": sha256_bytes(data),
        })
    return entries, contents


def deterministic_archive(path: Path, manifest: dict[str, Any], contents: dict[str, bytes]) -> None:
    if path.exists() or path.is_symlink():
        raise RejectError(f"archive output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    all_contents = {f"{ARCHIVE_PREFIX}/manifest.json": canonical_bytes(manifest)}
    all_contents.update({f"{ARCHIVE_PREFIX}/{name}": data for name, data in contents.items()})
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, mtime=0, fileobj=raw) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name in sorted(all_contents):
                    data = all_contents[name]
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o644
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(data))


def validate_archive(path: Path) -> dict[str, Any]:
    """Independently validate archive closure, paths, sizes, and hashes."""
    seen: set[str] = set()
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                name = safe_relative(member.name, "archive member")
                if name in seen:
                    raise RejectError(f"duplicate archive member: {name}")
                seen.add(name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise RejectError(f"archive member is not a regular file: {name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RejectError(f"archive member cannot be read: {name}")
                data = extracted.read()
                if len(data) != member.size:
                    raise RejectError(f"archive member size differs: {name}")
                members[name] = data
    except (tarfile.TarError, OSError) as error:
        raise RejectError(f"cannot read export archive: {error}") from error
    manifest_name = f"{ARCHIVE_PREFIX}/manifest.json"
    if manifest_name not in members:
        raise RejectError("archive manifest is missing")
    manifest = require_object(load_json_bytes(members.pop(manifest_name), "archive manifest"),
                              "archive manifest")
    if manifest.get("schema") != EXPORT_SCHEMA or manifest.get("status") != "PASS":
        raise RejectError("archive manifest schema/status differs")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise RejectError("archive inventory is not a list")
    expected: dict[str, dict[str, Any]] = {}
    for entry in inventory:
        entry = require_object(entry, "archive inventory entry")
        if set(entry) != {"archive_path", "source_path", "size_bytes", "sha256"}:
            raise RejectError("archive inventory entry keys differ")
        name = f"{ARCHIVE_PREFIX}/{safe_relative(entry['archive_path'], 'inventory path')}"
        if name in expected:
            raise RejectError(f"duplicate inventory path: {name}")
        expected[name] = entry
    if set(members) != set(expected):
        raise RejectError(
            f"archive closure differs: missing={sorted(set(expected)-set(members))} "
            f"extra={sorted(set(members)-set(expected))}"
        )
    for name, entry in expected.items():
        data = members[name]
        if len(data) != entry["size_bytes"] or sha256_bytes(data) != entry["sha256"]:
            raise RejectError(f"archive hash/size differs: {name}")
    return manifest


def scratch_summary(
    root: Path, files: dict[str, os.stat_result], scratch: set[str]
) -> dict[str, Any]:
    rows = []
    total = 0
    for path in sorted(scratch):
        data = read_regular(root, path, files[path])
        rows.append({"path": path, "size_bytes": len(data), "sha256": sha256_bytes(data)})
        total += len(data)
    return {"namespace": "work/build/", "file_count": len(rows), "size_bytes": total,
            "inventory_sha256": sha256_bytes(canonical_bytes(rows))}


def hold_manifest(root: Path, result_sha: str | None, reasons: list[dict[str, Any]],
                  files: dict[str, os.stat_result]) -> dict[str, Any]:
    return {
        "schema": STATUS_SCHEMA,
        "status": "HOLD",
        "archive_emitted": False,
        "expected_result_sha256": EXPECTED_RESULT_SHA256,
        "observed_result_sha256": result_sha,
        "source_root": str(root),
        "source_scan": {
            "regular_file_count": len(files),
            "size_bytes": sum(item.st_size for item in files.values()),
        },
        "hold_reasons": sorted(reasons, key=lambda row: (row["code"], str(row))),
        "nonclaims": [
            "no missing byte was regenerated, copied from Git, or inferred",
            "no hardened synthetic replay export exists",
            "no canonical campaign or physical qualification is claimed",
        ],
    }


def export(root: Path, archive_path: Path) -> tuple[dict[str, Any], int]:
    root = root.absolute()
    files, directories = scan_regular_tree(root)
    if "result.json" not in files:
        raise HoldError([{"code": "RESULT_MISSING", "path": "result.json"}])
    result_data = read_regular(root, "result.json", files["result.json"])
    result_sha = sha256_bytes(result_data)
    result = require_object(load_json_bytes(result_data, "preserved result"), "preserved result")
    validate_result_contract(result)
    reasons = binding_reasons(result_sha, result)
    if reasons:
        return hold_manifest(root, result_sha, reasons, files), 3
    names = validate_result_contract(result)
    required = expected_evidence_paths(names)
    missing, scratch = closed_inventory(files, directories, required)
    if missing:
        reasons = [{"code": "REQUIRED_EVIDENCE_MISSING", "path": path}
                   for path in sorted(missing)]
        return hold_manifest(root, result_sha, reasons, files), 3
    validate_claims(root, files, result, names)
    entries, contents = inventory_entries(root, files, required)
    manifest = {
        "schema": EXPORT_SCHEMA,
        "status": "PASS",
        "source_root_basename": root.name,
        "result_sha256": result_sha,
        "result_size_bytes": len(result_data),
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "integration_commit": EXPECTED_INTEGRATION_COMMIT,
        "pins_sha256": EXPECTED_PINS_SHA256,
        "owners": list(EXPECTED_OWNERS),
        "full50_run_count_per_owner": 50,
        "inventory_file_count": len(entries),
        "inventory_size_bytes": sum(entry["size_bytes"] for entry in entries),
        "inventory": entries,
        "excluded_reproducible_build_scratch": scratch_summary(root, files, scratch),
    }
    deterministic_archive(archive_path, manifest, contents)
    validated = validate_archive(archive_path)
    if validated != manifest:
        raise RejectError("post-write archive manifest differs")
    return {
        "schema": STATUS_SCHEMA,
        "status": "PASS",
        "archive_emitted": True,
        "archive_path": str(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "archive_sha256": sha256_bytes(archive_path.read_bytes()),
        "manifest_sha256": sha256_bytes(canonical_bytes(manifest)),
        "inventory_file_count": len(entries),
        "inventory_size_bytes": manifest["inventory_size_bytes"],
        "result_sha256": result_sha,
    }, 0


def write_status(path: Path, status: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RejectError(f"status output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(canonical_bytes(status))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--status-output", type=Path)
    parser.add_argument("--archive-output", type=Path)
    parser.add_argument("--validate-archive", type=Path)
    args = parser.parse_args()
    try:
        if args.validate_archive:
            if any((args.run_root, args.status_output, args.archive_output)):
                parser.error("--validate-archive is exclusive")
            manifest = validate_archive(args.validate_archive)
            print(json.dumps({"status": "PASS", "manifest": manifest["schema"]}, sort_keys=True))
            return 0
        if not all((args.run_root, args.status_output, args.archive_output)):
            parser.error("--run-root, --status-output, and --archive-output are required together")
        assert args.run_root is not None and args.status_output is not None \
            and args.archive_output is not None
        status, code = export(args.run_root, args.archive_output)
        write_status(args.status_output, status)
        marker = "PASS" if code == 0 else "HOLD"
        print(f"A23_SYNTHETIC_EXPORT_{marker} status={args.status_output}")
        return code
    except HoldError as error:
        try:
            files, _ = scan_regular_tree(args.run_root.absolute())
        except (RejectError, HoldError):
            files = {}
        status = hold_manifest(args.run_root.absolute(), None, error.reasons, files)
        write_status(args.status_output, status)
        print(f"A23_SYNTHETIC_EXPORT_HOLD status={args.status_output}")
        return 3
    except (RejectError, OSError, ValueError, KeyError) as error:
        print(f"A23_SYNTHETIC_EXPORT_REJECT {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
