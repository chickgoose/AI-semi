#!/usr/bin/env python3
"""Run, seal, and independently validate hardened synthetic replay v2 evidence."""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Iterable


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
BASE_PACKAGE = PROJECT / "tests/a23_full_single_edge_replay"
BASE_RUNNER = BASE_PACKAGE / "run_replay.py"
BASE_PINS = BASE_PACKAGE / "pins.json"
BASE_PINS_RELATIVE = "tests/a23_full_single_edge_replay/pins.json"
ORDINAL_TB = PACKAGE / "a23_synthetic_v2_ordinal_tb.sv"
EXPORT_HELPER_DIR = PROJECT / "tests/a23_single_edge_synthetic_export"
EXPECTED_SOURCE_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
EXPECTED_SOURCE_TREE = "e6030c7990f602a7fc1c73ac529b008b8e2c4133"
EXPECTED_INTEGRATION_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
EXPECTED_INTEGRATION_TREE = "d0fda8da2c10693b5d7093e0e2d505590722c1ea"
EXPECTED_PINS_SHA256 = "0daba2132010272a78b56ec2a1541f30f7cb5d2b0d8562102cb70cf9e098d8e0"
RESULT_SCHEMA = "a23_full_single_edge_replay_result_v1"
V2_RESULT_SCHEMA = "a23_single_edge_synthetic_v2_result_v1"
EXPORT_SCHEMA = "a23_single_edge_synthetic_v2_export_manifest_v1"
PUBLICATION_SCHEMA = "a23_single_edge_synthetic_v2_publication_v1"
STATUS = "PASS_HARDENED_SYNTHETIC_V2"
ARCHIVE_PREFIX = "a23-single-edge-synthetic-v2"
MAX_ARCHIVE_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBER_COUNT = 2048
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 160 * 1024 * 1024
IMPLEMENTATION_FILES = (
    BASE_PINS_RELATIVE,
    "tests/a23_single_edge_synthetic_v2/README.md",
    "tests/a23_single_edge_synthetic_v2/run_all.sh",
    "tests/a23_single_edge_synthetic_v2/run_v2.py",
    "tests/a23_single_edge_synthetic_v2/test_synthetic_v2.py",
    "tests/a23_single_edge_synthetic_v2/a23_synthetic_v2_ordinal_tb.sv",
    "tests/a23_single_edge_synthetic_export/export_preserved.py",
)
EVENT_CSV_FIELDS = (
    "owner", "trace", "tb_event_id", "logical_source", "occurrence_cycle",
    "accept_cycle", "retire_cycle", "deadline_cycle", "event_state",
)
ORDINAL_CSV_FIELDS = (
    "owner", "trace", "tb_event_id", "logical_source", "occurrence_cycle",
    "accept_cycle", "accept_ordinal", "retire_cycle", "retire_ordinal",
    "event_state",
)
V2_RESULT_KEYS = {
    "schema", "status", "evidence_class", "dataset", "execution_accounting",
    "identities", "primary", "semantic_reproduction", "sequence_evidence",
    "qualification",
}
PUBLICATION_KEYS = {
    "schema", "status", "package_commit", "package_tree",
    "package_input_identity_sha256", "source_commit", "source_tree",
    "integration_commit", "integration_tree", "tool_identity_sha256",
    "trace_identity_sha256", "pins_sha256", "semantic_digest_sha256",
    "ordinal_semantic_digest_sha256", "primary_legacy_result_sha256",
    "primary_legacy_result_size_bytes", "reproduction_legacy_result_sha256",
    "reproduction_legacy_result_size_bytes", "v2_result_sha256",
    "v2_result_size_bytes", "export_sha256", "export_size_bytes",
    "export_manifest_sha256", "export_inventory_entry_count",
    "physical_status", "canonical_campaign_status",
}

sys.path.insert(0, str(EXPORT_HELPER_DIR))
import export_preserved as retained  # noqa: E402


class V2Error(RuntimeError):
    pass


def pretty(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def semantic_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    value = retained.load_json_bytes(data, label)
    return retained.require_object(value, label)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_json_bytes(path.read_bytes(), label)
    except OSError as error:
        raise V2Error(f"cannot read {label}: {error}") from error


def ephemeral_log_pointers() -> list[str]:
    pointers: list[str] = []
    for owner in ("a2", "a3"):
        pointers.extend((
            f"/owners/{owner}/baseline_build_log_sha256",
            f"/owners/{owner}/mutation_activation/simulation_log_sha256",
            f"/owners/{owner}/reset/simulation_log_sha256",
        ))
    for index in range(8):
        pointers.extend((
            f"/mutations/{index}/build_log_sha256",
            f"/mutations/{index}/simulation_log_sha256",
        ))
    return sorted(pointers)


EPHEMERAL_LOG_POINTERS = tuple(ephemeral_log_pointers())


def decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise V2Error(f"JSON pointer is not absolute: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~")
            for part in pointer[1:].split("/")]


def remove_pointer(document: Any, pointer: str) -> None:
    parts = decode_pointer(pointer)
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as error:
                raise V2Error(f"missing semantic exclusion pointer: {pointer}") from error
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise V2Error(f"missing semantic exclusion pointer: {pointer}")
    final = parts[-1]
    if isinstance(current, list):
        try:
            del current[int(final)]
        except (ValueError, IndexError) as error:
            raise V2Error(f"missing semantic exclusion pointer: {pointer}") from error
    elif isinstance(current, dict) and final in current:
        del current[final]
    else:
        raise V2Error(f"missing semantic exclusion pointer: {pointer}")


def semantic_projection(result: dict[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(result)
    for pointer in EPHEMERAL_LOG_POINTERS:
        remove_pointer(projected, pointer)
    return projected


def semantic_digest(result: dict[str, Any]) -> str:
    return digest(semantic_bytes(semantic_projection(result)))


def difference_pointers(left: Any, right: Any, prefix: str = "") -> set[str]:
    if type(left) is not type(right):
        return {prefix or "/"}
    if isinstance(left, dict):
        differences: set[str] = set()
        for key in set(left) | set(right):
            escaped = key.replace("~", "~0").replace("/", "~1")
            child = f"{prefix}/{escaped}"
            if key not in left or key not in right:
                differences.add(child)
            else:
                differences.update(difference_pointers(left[key], right[key], child))
        return differences
    if isinstance(left, list):
        if len(left) != len(right):
            return {prefix or "/"}
        differences: set[str] = set()
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            differences.update(
                difference_pointers(left_item, right_item, f"{prefix}/{index}")
            )
        return differences
    return set() if left == right else {prefix or "/"}


def git_output(arguments: list[str], *, binary: bool = False) -> bytes | str:
    process = subprocess.run(
        ["git", *arguments], cwd=PROJECT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=not binary, check=False,
    )
    if process.returncode:
        error = process.stderr if not binary else process.stderr.decode(errors="replace")
        raise V2Error(f"Git command failed: {' '.join(arguments)}: {error.strip()}")
    return process.stdout


def git_bytes(commit: str, relative: str) -> bytes:
    return git_output(["show", f"{commit}:{relative}"], binary=True)  # type: ignore[return-value]


def current_commit() -> str:
    return str(git_output(["rev-parse", "HEAD"])).strip()


def verified_commit_tree(commit: str, label: str) -> str:
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise V2Error(f"{label} is not a full lowercase Git object identity")
    resolved = str(git_output(["rev-parse", f"{commit}^{{commit}}"]).strip())
    if resolved != commit:
        raise V2Error(f"{label} does not resolve to the exact commit")
    return str(git_output(["rev-parse", f"{commit}^{{tree}}"]).strip())


def authoritative_roster(package_commit: str) -> tuple[dict[str, str], list[str]]:
    pins_data = git_bytes(package_commit, BASE_PINS_RELATIVE)
    if digest(pins_data) != EXPECTED_PINS_SHA256:
        raise V2Error("package pins bytes differ")
    pins = load_json_bytes(pins_data, "pinned replay document")
    if set(pins) != {
        "schema", "integration_state", "rtl_provenance", "files", "owners",
        "mutation_anchor_sha256", "mutations", "tools",
    } or pins.get("schema") != "a23_full_single_edge_replay_pins_v1":
        raise V2Error("pinned replay document fields differ")
    files = retained.require_object(pins.get("files"), "pinned file roster")
    for path, expected_sha in files.items():
        relative = retained.safe_relative(path, "pinned file path")
        if (relative != path or not isinstance(expected_sha, str) or
                re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None):
            raise V2Error("pinned file roster value differs")
        if digest(git_bytes(package_commit, path)) != expected_sha:
            raise V2Error(f"pinned package blob differs: {path}")
    expected_rtl = {path for path in files if path.startswith("rtl/")}
    filelists: set[str] = set()
    sources: set[str] = set()

    def walk(path: str) -> None:
        if path in filelists:
            return
        filelists.add(path)
        for raw in git_bytes(package_commit, path).decode("utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) == 2 and parts[0] == "-f":
                walk(retained.safe_relative(parts[1], "nested filelist path"))
            elif len(parts) == 1 and not parts[0].startswith("+"):
                sources.add(retained.safe_relative(parts[0], "filelist source path"))
            else:
                raise V2Error(f"unsupported pinned filelist entry: {path}: {line}")

    owners = retained.require_object(pins.get("owners"), "pinned owner roster")
    if set(owners) != {"a2", "a3"}:
        raise V2Error("pinned owner roster differs")
    owner_keys = {"define", "scheduler", "filelist", "top", "top_module",
                  "mutation_target", "wrapper"}
    for owner in ("a2", "a3"):
        spec = retained.require_object(owners[owner], f"pinned {owner} owner")
        if set(spec) != owner_keys:
            raise V2Error(f"pinned {owner} fields differ")
        walk(retained.safe_relative(spec["filelist"], "owner filelist path"))
    if filelists | sources != expected_rtl:
        raise V2Error("pinned recursive RTL/filelist closure differs from file roster")
    return files, sorted(expected_rtl)


def require_exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    record = retained.require_object(value, label)
    if set(record) != keys:
        raise V2Error(f"{label} fields differ")
    return record


def require_int(value: Any, label: str, *, minimum: int | None = 0) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise V2Error(f"{label} is not an exact integer")
    return value


def require_float(value: Any, label: str) -> float:
    if type(value) is not float:
        raise V2Error(f"{label} is not an exact float")
    return value


def require_string(value: Any, label: str) -> str:
    if type(value) is not str:
        raise V2Error(f"{label} is not an exact string")
    return value


def require_typed_equal(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected):
        raise V2Error(f"{label} scalar type differs")
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            raise V2Error(f"{label} fields differ")
        for key in expected:
            require_typed_equal(actual[key], expected[key], f"{label}/{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise V2Error(f"{label} list length differs")
        for index, (left, right) in enumerate(zip(actual, expected)):
            require_typed_equal(left, right, f"{label}/{index}")
    elif actual != expected:
        raise V2Error(f"{label} value differs")


def validate_latency_schema(value: Any, label: str) -> None:
    record = require_exact_keys(
        value, {"count", "max", "mean", "p50", "p95", "p99"}, label
    )
    for key in ("count", "max", "p50", "p95", "p99"):
        require_int(record[key], f"{label}/{key}")
    require_float(record["mean"], f"{label}/mean")


def validate_base_result_schema(result: dict[str, Any]) -> None:
    require_exact_keys(result, {
        "acceptance_observation", "boundary", "conservation", "event_identity_scope",
        "execution_accounting", "generator", "mutations", "owners", "provenance",
        "qualification", "reset_qualification", "retirement_scoreboard", "schema",
        "source_overrun_semantics", "status",
    }, "base result")
    exact_values = {
        "acceptance_observation":
            "actual_endpoint_atomic_source_accept_count_and_ordered_addresses",
        "boundary": "actual_A2_A3_scheduler_plus_actual_single_edge_endpoint",
        "conservation": [
            "generated = source_overrun + accepted",
            "after bounded drain: accepted = retired",
        ],
        "event_identity_scope": "TB_identity_bound_to_observable_logical_source_stream",
        "reset_qualification":
            "reset_only_after_external_clean_drain_and_no_protocol_error",
        "retirement_scoreboard": "actual_single_edge_retire_prefix_in_global_accept_order",
        "source_overrun_semantics":
            "same_source_occurrence_while_one_entry_source_latch_occupied",
    }
    for key, expected in exact_values.items():
        require_typed_equal(result[key], expected, f"base semantic definition/{key}")
    require_exact_keys(result["execution_accounting"],
                       set(retained.EXPECTED_EXECUTION_ACCOUNTING),
                       "base execution accounting")
    require_typed_equal(result["execution_accounting"],
                        retained.EXPECTED_EXECUTION_ACCOUNTING,
                        "base execution accounting")
    generator = require_exact_keys(result["generator"], {
        "full50_manifest_sha256", "source_commit", "trace_count", "version",
    }, "base generator")
    require_typed_equal(generator, {
        "full50_manifest_sha256":
            "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
        "source_commit": "abd6a721b515ded8a9ef76cb96129b7e0af21e2b",
        "trace_count": 50, "version": "4.0",
    }, "base generator definition")
    require_exact_keys(result["qualification"], {
        "CDC_RDC", "physical", "power", "single_edge_digital_RTL",
    }, "base qualification")
    require_typed_equal(result["qualification"], {
        "CDC_RDC": "HOLD", "physical": "HOLD", "power": "HOLD",
        "single_edge_digital_RTL": "GO",
    }, "base qualification definition")
    provenance = require_exact_keys(result["provenance"], {
        "actual_rtl_git", "package_commit", "pins_path", "pins_sha256",
        "verified_files", "verified_tools",
    }, "base provenance")
    require_exact_keys(provenance["actual_rtl_git"], {
        "integration_commit", "integration_tree", "source_commit", "source_tree",
        "verified_rtl_paths",
    }, "base actual RTL Git")
    tools = require_exact_keys(provenance["verified_tools"], {
        "python", "verilator", "verilator_bin", "make", "cxx",
    }, "base verified tools")
    for role, identity in tools.items():
        identity = require_exact_keys(identity, {"path", "sha256", "version"},
                                      f"base tool {role}")
        for key in identity:
            require_string(identity[key], f"base tool {role}/{key}")
    owners = require_exact_keys(result["owners"], {"a2", "a3"}, "base owners")
    run_keys = {
        "accept_to_retire", "accepted", "count2_commits", "events_sha256",
        "fixed_window_cycles", "fixed_window_events_per_cycle", "fixed_window_retired",
        "generated", "observation_cycles", "occurrence_to_accept",
        "pre_reset_clean_drain", "prepared_trace_sha256", "reset_test", "retired",
        "source_overrun", "summary_sha256", "trace_sha256",
    }
    special_keys = (run_keys - {"prepared_trace_sha256", "trace_sha256"}) | {
        "simulation_log_sha256",
    }
    for owner in ("a2", "a3"):
        owner_record = require_exact_keys(owners[owner], {
            "baseline_build_log_sha256", "full50", "mutation_activation", "reset",
        }, f"base owner {owner}")
        full50 = require_exact_keys(owner_record["full50"], {
            "actual_execution_count", "aggregate", "runs",
        }, f"base {owner} full50")
        require_int(full50["actual_execution_count"],
                    f"base {owner} full50 actual execution count")
        aggregate = require_exact_keys(full50["aggregate"], {
            "accept_to_retire", "actual_execution_count", "fixed_window_events_per_cycle",
            "occurrence_to_accept", "totals",
        }, f"base {owner} aggregate")
        require_int(aggregate["actual_execution_count"],
                    f"base {owner} aggregate execution count")
        require_float(aggregate["fixed_window_events_per_cycle"],
                      f"base {owner} aggregate rate")
        validate_latency_schema(aggregate["accept_to_retire"],
                                f"base {owner} aggregate accept latency")
        validate_latency_schema(aggregate["occurrence_to_accept"],
                                f"base {owner} aggregate occurrence latency")
        totals = require_exact_keys(aggregate["totals"], {
            "accepted", "count2_commits", "fixed_window_cycles", "fixed_window_retired",
            "generated", "retired", "source_overrun",
        }, f"base {owner} aggregate totals")
        for key, value in totals.items():
            require_int(value, f"base {owner} aggregate totals/{key}")
        runs = retained.require_object(full50["runs"], f"base {owner} runs")
        for name, run in runs.items():
            record = require_exact_keys(run, run_keys, f"base {owner} run {name}")
            validate_latency_schema(record["accept_to_retire"],
                                    f"base {owner} run {name} accept latency")
            validate_latency_schema(record["occurrence_to_accept"],
                                    f"base {owner} run {name} occurrence latency")
            for key in ("accepted", "count2_commits", "fixed_window_cycles",
                        "fixed_window_retired", "generated", "observation_cycles",
                        "pre_reset_clean_drain", "reset_test", "retired",
                        "source_overrun"):
                require_int(record[key], f"base {owner} run {name}/{key}")
            require_float(record["fixed_window_events_per_cycle"],
                          f"base {owner} run {name}/fixed window rate")
            for key in ("events_sha256", "prepared_trace_sha256", "summary_sha256",
                        "trace_sha256"):
                require_string(record[key], f"base {owner} run {name}/{key}")
        for label in ("reset", "mutation_activation"):
            record = require_exact_keys(owner_record[label], special_keys,
                                        f"base {owner} {label}")
            validate_latency_schema(record["accept_to_retire"],
                                    f"base {owner} {label} accept latency")
            validate_latency_schema(record["occurrence_to_accept"],
                                    f"base {owner} {label} occurrence latency")
            for key in ("accepted", "count2_commits", "fixed_window_cycles",
                        "fixed_window_retired", "generated", "observation_cycles",
                        "pre_reset_clean_drain", "reset_test", "retired",
                        "source_overrun"):
                require_int(record[key], f"base {owner} {label}/{key}")
            require_float(record["fixed_window_events_per_cycle"],
                          f"base {owner} {label}/fixed window rate")
            for key in ("events_sha256", "simulation_log_sha256", "summary_sha256"):
                require_string(record[key], f"base {owner} {label}/{key}")
        reset = owner_record["reset"]
        if reset["reset_test"] != 1 or reset["pre_reset_clean_drain"] != 1:
            raise V2Error(f"base {owner} reset lacks clean-drain/reset flags")
        if owner_record["mutation_activation"]["count2_commits"] < 1:
            raise V2Error(f"base {owner} pair activation lacks count-two commit")
    mutations = result["mutations"]
    if not isinstance(mutations, list) or len(mutations) != 8:
        raise V2Error("base mutation roster differs")
    for index, mutation in enumerate(mutations):
        record = require_exact_keys(mutation, {
            "actual_endpoint_RTL_source_rewrite", "build_log_sha256",
            "compiled_successfully", "executed", "exit_code", "first_diagnostic",
            "killed", "mutation", "owner", "simulation_log_sha256", "source_identity",
        }, f"base mutation {index}")
        require_exact_keys(record["source_identity"], {
            "base_sha256", "literal_replacement_count", "mutant_sha256",
            "new_anchor_sha256", "old_anchor_sha256", "target",
        }, f"base mutation {index} source identity")
        for key in ("actual_endpoint_RTL_source_rewrite", "compiled_successfully",
                    "executed", "killed"):
            if type(record[key]) is not bool:
                raise V2Error(f"base mutation {index}/{key} is not an exact boolean")
        require_int(record["exit_code"], f"base mutation {index}/exit_code", minimum=None)
        for key in ("build_log_sha256", "first_diagnostic", "mutation", "owner",
                    "simulation_log_sha256"):
            require_string(record[key], f"base mutation {index}/{key}")
        source_identity = record["source_identity"]
        require_int(source_identity["literal_replacement_count"],
                    f"base mutation {index}/literal replacement count")
        for key in ("base_sha256", "mutant_sha256", "new_anchor_sha256",
                    "old_anchor_sha256", "target"):
            require_string(source_identity[key], f"base mutation {index}/source/{key}")


def verify_result_identity(result: dict[str, Any], package_commit: str | None = None) -> list[str]:
    validate_base_result_schema(result)
    names = retained.validate_result_contract(result)
    provenance = retained.require_object(result.get("provenance"), "result provenance")
    rtl = retained.require_object(provenance.get("actual_rtl_git"), "actual RTL Git")
    expected_rtl = {
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_tree": EXPECTED_SOURCE_TREE,
        "integration_commit": EXPECTED_INTEGRATION_COMMIT,
        "integration_tree": EXPECTED_INTEGRATION_TREE,
    }
    for key, expected in expected_rtl.items():
        if rtl.get(key) != expected:
            raise V2Error(f"actual RTL identity differs for {key}")
    if provenance.get("pins_sha256") != EXPECTED_PINS_SHA256:
        raise V2Error("hardened replay pins identity differs")
    if provenance.get("pins_path") != BASE_PINS_RELATIVE:
        raise V2Error("hardened replay pins path differs")
    claimed_package = provenance.get("package_commit")
    if package_commit is not None and claimed_package != package_commit:
        raise V2Error("replay package commit differs from v2 implementation commit")
    verified_commit_tree(claimed_package, "replay package commit")
    authoritative_files, authoritative_rtl = authoritative_roster(claimed_package)
    if provenance.get("verified_files") != authoritative_files:
        raise V2Error("producer verified-file roster/hash map differs from pins")
    if rtl.get("verified_rtl_paths") != authoritative_rtl:
        raise V2Error("producer verified RTL roster differs from recursive pins closure")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXPECTED_INTEGRATION_COMMIT,
         claimed_package], cwd=PROJECT, check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if ancestor.returncode != 0:
        raise V2Error("integration commit is not an ancestor of replay package commit")
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "PASS":
        raise V2Error("base replay result is not PASS")
    return names


def verify_tools(result: dict[str, Any]) -> str:
    tools = result["provenance"]["verified_tools"]
    if set(tools) != {"python", "verilator", "verilator_bin", "make", "cxx"}:
        raise V2Error("tool role roster differs")
    pins_data = git_bytes(result["provenance"]["package_commit"], BASE_PINS_RELATIVE)
    pins = load_json_bytes(pins_data, "pinned replay document")
    pinned_tools = retained.require_object(pins.get("tools"), "pinned tools")
    expected_tools: dict[str, dict[str, str]] = {}
    if set(pinned_tools) != set(tools):
        raise V2Error("pinned tool role roster differs")
    for role, pinned in pinned_tools.items():
        pinned = require_exact_keys(pinned, {"path", "sha256", "version_args", "version"},
                                    f"pinned tool {role}")
        expected_tools[role] = {
            "path": pinned["path"], "sha256": pinned["sha256"],
            "version": pinned["version"],
        }
    if tools != expected_tools:
        raise V2Error("producer tool identities differ from pins")
    for role, identity in tools.items():
        path = Path(identity["path"])
        if path.is_symlink() or not path.is_file():
            raise V2Error(f"tool is absent or symlinked: {role}")
        if file_digest(path) != identity["sha256"]:
            raise V2Error(f"tool bytes differ: {role}")
    return digest(semantic_bytes(tools))


def verify_package_inputs(
    result: dict[str, Any], package_commit: str,
) -> tuple[dict[str, bytes], str]:
    verified, _ = authoritative_roster(package_commit)
    if result["provenance"]["verified_files"] != verified:
        raise V2Error("producer verified-file inventory differs from pins")
    contents: dict[str, bytes] = {}
    identities: dict[str, str] = {}
    for relative, expected_sha in sorted(verified.items()):
        relative = retained.safe_relative(relative, "producer input path")
        data = git_bytes(package_commit, relative)
        if digest(data) != expected_sha:
            raise V2Error(f"producer input differs in package commit: {relative}")
        contents[f"inputs/repository/{relative}"] = data
        identities[relative] = expected_sha
    for relative in IMPLEMENTATION_FILES:
        data = git_bytes(package_commit, relative)
        sha = digest(data)
        contents[f"inputs/repository/{relative}"] = data
        identities[relative] = sha
    return contents, digest(semantic_bytes(identities))


def verify_rtl_git_blobs(result: dict[str, Any]) -> None:
    rtl = result["provenance"]["actual_rtl_git"]
    verified, rtl_paths = authoritative_roster(result["provenance"]["package_commit"])
    if rtl["verified_rtl_paths"] != rtl_paths:
        raise V2Error("producer verified RTL roster differs from pins")
    for label in ("source", "integration"):
        commit = rtl[f"{label}_commit"]
        tree = str(git_output(["rev-parse", f"{commit}^{{tree}}"])).strip()
        if tree != rtl[f"{label}_tree"]:
            raise V2Error(f"{label} RTL tree differs")
        for relative in rtl_paths:
            if digest(git_bytes(commit, relative)) != verified[relative]:
                raise V2Error(f"{label} RTL blob differs: {relative}")


def trace_identity(result: dict[str, Any], names: list[str]) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    for name in names:
        a2 = result["owners"]["a2"]["full50"]["runs"][name]
        a3 = result["owners"]["a3"]["full50"]["runs"][name]
        if (a2["trace_sha256"] != a3["trace_sha256"] or
                a2["prepared_trace_sha256"] != a3["prepared_trace_sha256"]):
            raise V2Error(f"A2/A3 input identity differs: {name}")
        rows.append({
            "name": name,
            "trace_sha256": a2["trace_sha256"],
            "prepared_trace_sha256": a2["prepared_trace_sha256"],
        })
    claimed_digest = digest(semantic_bytes(rows))
    regenerated_rows, regenerated_digest = regenerate_trace_identity(result, names)
    if regenerated_rows != rows or regenerated_digest != claimed_digest:
        raise V2Error("claimed trace identity differs from canonical regeneration")
    return rows, claimed_digest


def regenerate_trace_identity(
    result: dict[str, Any], names: list[str],
) -> tuple[list[dict[str, Any]], str]:
    commit = result["provenance"]["package_commit"]
    inputs = {
        "generate.py": "benchmarks/clean_slate_aer/generate_trace.py",
        "prepare.py": "benchmarks/clean_slate_aer/prepare_sv_trace.py",
        "official.py": "scripts/common_suite_official.py",
        "manifest.json":
            "tests/common_suite_receipt/fixtures/manifest.neutrality-n16.json",
    }
    with tempfile.TemporaryDirectory(prefix="a23-v2-trace-regenerate-") as temporary:
        work = Path(temporary)
        for local, relative in inputs.items():
            (work / local).write_bytes(git_bytes(commit, relative))
        registry = runpy.run_path(str(work / "official.py"))
        official_names = tuple(registry["FULL50"])
        official_hashes = registry["TRACE_SHA256"]
        if len(names) != 50 or set(names) != set(official_names):
            raise V2Error("result trace roster differs from pinned full50 registry")
        python = result["provenance"]["verified_tools"]["python"]["path"]
        generated = work / "generated"
        process = subprocess.run(
            [python, str(work / "generate.py"), "--manifest", str(work / "manifest.json"),
             "--output-dir", str(generated)],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, check=False,
        )
        if process.returncode:
            raise V2Error("pinned canonical trace regeneration failed")
        rows: list[dict[str, Any]] = []
        for name in names:
            raw = generated / f"{name}.events.jsonl"
            if file_digest(raw) != official_hashes[name]:
                raise V2Error(f"pinned registry/raw trace identity differs: {name}")
            prepared = work / f"prepared/{name}.trace"
            prepared.parent.mkdir(exist_ok=True)
            process = subprocess.run(
                [python, str(work / "prepare.py"), "--trace", str(raw),
                 "--run-manifest", str(generated / f"{name}.manifest.json"),
                 "--output", str(prepared), "--addr-width", "4"],
                cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, check=False,
            )
            if process.returncode:
                raise V2Error(f"pinned canonical trace preparation failed: {name}")
            row = {
                "name": name,
                "trace_sha256": file_digest(raw),
                "prepared_trace_sha256": file_digest(prepared),
            }
            claimed = result["owners"]["a2"]["full50"]["runs"][name]
            if (row["trace_sha256"] != claimed["trace_sha256"] or
                    row["prepared_trace_sha256"] != claimed["prepared_trace_sha256"]):
                raise V2Error(f"regenerated canonical trace identity differs: {name}")
            rows.append(row)
    return rows, digest(semantic_bytes(rows))


def sequence_record(
    event_path: Path, ordinal_path: Path, simulation_path: Path,
    owner: str, name: str,
) -> dict[str, Any]:
    with event_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != EVENT_CSV_FIELDS:
            raise V2Error(f"event CSV schema differs: {owner}/{name}")
        event_rows = list(reader)
    with ordinal_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != ORDINAL_CSV_FIELDS:
            raise V2Error(f"ordinal CSV schema differs: {owner}/{name}")
        ordinal_rows = list(reader)
    fields = ("tb_event_id", "logical_source", "occurrence_cycle", "accept_cycle",
              "retire_cycle", "event_state")
    try:
        sequence = [[int(row[key]) if key != "event_state" else row[key]
                     for key in fields] for row in event_rows]
    except (KeyError, TypeError, ValueError) as error:
        raise V2Error(f"event CSV value differs: {owner}/{name}") from error
    if len(ordinal_rows) != len(sequence):
        raise V2Error(f"ordinal/event cardinality differs: {owner}/{name}")
    if [row[0] for row in sequence] != list(range(len(sequence))):
        raise V2Error(f"event row sequence is not contiguous: {owner}/{name}")
    accepted_order: list[list[int]] = []
    retired_order: list[list[int]] = []
    try:
        for index, (event, ordinal) in enumerate(zip(event_rows, ordinal_rows)):
            common = ("owner", "trace", "tb_event_id", "logical_source",
                      "occurrence_cycle", "accept_cycle", "retire_cycle", "event_state")
            if event["owner"] != owner or event["trace"] != name:
                raise V2Error(f"event CSV owner/trace differs: {owner}/{name}/{index}")
            if any(event[key] != ordinal[key] for key in common):
                raise V2Error(f"ordinal/event row differs: {owner}/{name}/{index}")
            accept_ordinal = int(ordinal["accept_ordinal"])
            retire_ordinal = int(ordinal["retire_ordinal"])
            if event["event_state"] == "retired":
                accepted_order.append([
                    accept_ordinal, index, int(event["logical_source"]),
                    int(event["accept_cycle"]),
                ])
                retired_order.append([
                    retire_ordinal, index, int(event["logical_source"]),
                    int(event["retire_cycle"]),
                ])
            elif event["event_state"] == "source_overrun":
                if accept_ordinal != -1 or retire_ordinal != -1:
                    raise V2Error(f"overrun carries ordinal: {owner}/{name}/{index}")
            else:
                raise V2Error(f"unknown event state: {owner}/{name}/{index}")
    except (KeyError, TypeError, ValueError) as error:
        raise V2Error(f"ordinal CSV value differs: {owner}/{name}") from error
    accepted_order.sort()
    retired_order.sort()
    expected_ordinals = list(range(len(accepted_order)))
    if ([row[0] for row in accepted_order] != expected_ordinals or
            [row[0] for row in retired_order] != expected_ordinals):
        raise V2Error(f"ordinals are not contiguous: {owner}/{name}")
    if [row[1:3] for row in accepted_order] != [row[1:3] for row in retired_order]:
        raise V2Error(f"accepted/retired ordinal identity differs: {owner}/{name}")
    log = simulation_path.read_text(encoding="utf-8")
    sentinel = (
        f"A23_SYNTHETIC_V2_ORDINAL_PASS owner={owner} trace={name} "
        f"generated={len(sequence)} accepted={len(accepted_order)} "
        f"retired={len(retired_order)}"
    )
    if log.splitlines().count(sentinel) != 1 or "A23_SYNTHETIC_V2_ORDINAL_FAIL" in log:
        raise V2Error(f"ordinal simulation PASS log differs: {owner}/{name}")
    return {
        "owner": owner,
        "trace": name,
        "event_row_count": len(sequence),
        "accepted_ordinal_count": len(accepted_order),
        "retired_ordinal_count": len(retired_order),
        "event_row_sequence_sha256": digest(semantic_bytes(sequence)),
        "accept_order_sha256": digest(semantic_bytes(accepted_order)),
        "retire_order_sha256": digest(semantic_bytes(retired_order)),
        "ordinal_csv_sha256": file_digest(ordinal_path),
        "ordinal_simulation_log_sha256": file_digest(simulation_path),
    }


def sequence_evidence(root: Path, names: list[str]) -> list[dict[str, Any]]:
    expected_progress = [
        f"A23_SYNTHETIC_V2_ORDINAL_PROGRESS owner={owner} full50={count}/50"
        for owner in ("a2", "a3") for count in (10, 20, 30, 40, 50)
    ]
    if (root / "ordinal_campaign.log").read_text(
            encoding="utf-8").splitlines() != expected_progress:
        raise V2Error("ordinal campaign PASS/progress log differs")
    evidence: list[dict[str, Any]] = []
    for owner in ("a2", "a3"):
        for name in names:
            evidence.append(sequence_record(
                root / f"work/artifacts/{owner}/none/{name}/events.csv",
                root / f"work/ordinal/{owner}/{name}/ordinals.csv",
                root / f"work/ordinal/{owner}/{name}/simulation.log",
                owner, name,
            ))
    return evidence


def ordinal_semantic_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected = copy.deepcopy(rows)
    for row in projected:
        if set(row) != {
            "owner", "trace", "event_row_count", "accepted_ordinal_count",
            "retired_ordinal_count", "event_row_sequence_sha256",
            "accept_order_sha256", "retire_order_sha256", "ordinal_csv_sha256",
            "ordinal_simulation_log_sha256",
        }:
            raise V2Error("sequence evidence row fields differ")
        for key in ("event_row_count", "accepted_ordinal_count",
                    "retired_ordinal_count"):
            require_int(row[key], f"sequence evidence/{key}")
        for key in ("owner", "trace", "event_row_sequence_sha256",
                    "accept_order_sha256", "retire_order_sha256",
                    "ordinal_csv_sha256", "ordinal_simulation_log_sha256"):
            require_string(row[key], f"sequence evidence/{key}")
        del row["ordinal_simulation_log_sha256"]
    return projected


def ordinal_semantic_digest(rows: list[dict[str, Any]]) -> str:
    return digest(semantic_bytes(ordinal_semantic_projection(rows)))


def run_campaign(retained_root: Path) -> int:
    retained_root = retained_root.absolute()
    if retained_root.exists() or retained_root.is_symlink():
        raise V2Error(f"retained root must not exist: {retained_root}")
    retained_root.mkdir(parents=True)
    command = [
        sys.executable, str(BASE_RUNNER),
        "--work-dir", str(retained_root / "work"),
        "--output", str(retained_root / "result.json"),
    ]
    process = subprocess.run(
        command, cwd=PROJECT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    (retained_root / "campaign.log").write_text(process.stdout, encoding="utf-8")
    sys.stdout.write(process.stdout)
    if process.returncode:
        raise V2Error(f"base replay failed with exit {process.returncode}")
    result = load_json(retained_root / "result.json", "fresh replay result")
    verify_result_identity(result, current_commit())
    if "A23_FULL_SINGLE_EDGE_REPLAY_PASS" not in process.stdout:
        raise V2Error("fresh replay lacks campaign PASS sentinel")
    run_ordinal_campaign(retained_root, result)
    return 0


def run_logged(
    command: list[str], log: Path, *, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command, cwd=PROJECT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if process.returncode:
        raise V2Error(f"ordinal command failed exit={process.returncode}: {' '.join(command)}")
    return process


def run_ordinal_campaign(root: Path, result: dict[str, Any]) -> None:
    sys.path.insert(0, str(BASE_PACKAGE))
    import run_replay as base  # noqa: PLC0415

    document = base.load_document()
    sources, _, _, _, _ = base.validate_integration(
        document, Path(result["provenance"]["verified_tools"]["verilator"]["path"]),
        allow_dirty=False,
    )
    environment = os.environ.copy()
    environment["MAKE"] = document["tools"]["make"]["path"]
    environment["CXX"] = document["tools"]["cxx"]["path"]
    verilator = document["tools"]["verilator"]["path"]
    campaign_lines: list[str] = []
    names = sorted(result["owners"]["a2"]["full50"]["runs"])
    for owner in ("a2", "a3"):
        build = root / f"work/build/ordinal/{owner}"
        build.mkdir(parents=True, exist_ok=False)
        binary = build / "sim"
        config = document["owners"][owner]
        command = [
            verilator, "--binary", "--timing", "--assert", "-Wall",
            "-Wno-fatal", "-Wno-BLKSEQ", "-Wno-WIDTHEXPAND",
            "-Wno-WIDTHTRUNC", "-Wno-UNUSEDSIGNAL", "-Wno-SYNCASYNCNET",
            f"-D{config['define']}", "--top-module", "a23_synthetic_v2_ordinal_tb",
            "--Mdir", str(build), "-o", "sim",
            *[str(path) for path in sources[owner]],
            str(PROJECT / config["wrapper"]), str(ORDINAL_TB),
        ]
        run_logged(command, root / f"work/ordinal/logs/build-{owner}.log",
                   environment=environment)
        if not binary.is_file():
            raise V2Error(f"ordinal simulator is absent: {owner}")
        for index, name in enumerate(names, start=1):
            case = root / f"work/ordinal/{owner}/{name}"
            case.mkdir(parents=True, exist_ok=False)
            ordinal = case / "ordinals.csv"
            simulation = case / "simulation.log"
            process = run_logged([
                str(binary), f"+OWNER={owner}", f"+TRACE_NAME={name}",
                f"+TRACE_FILE={root / f'work/prepared/{name}.trace'}",
                f"+ORDINAL_OUTPUT={ordinal}",
            ], simulation)
            if "A23_SYNTHETIC_V2_ORDINAL_PASS" not in process.stdout:
                raise V2Error(f"ordinal PASS sentinel is absent: {owner}/{name}")
            if index % 10 == 0:
                line = f"A23_SYNTHETIC_V2_ORDINAL_PROGRESS owner={owner} full50={index}/50"
                campaign_lines.append(line)
                print(line, flush=True)
    (root / "ordinal_campaign.log").write_text(
        "\n".join(campaign_lines) + "\n", encoding="utf-8"
    )


def ordinal_paths(names: list[str]) -> set[str]:
    paths = {"ordinal_campaign.log"}
    for owner in ("a2", "a3"):
        paths.add(f"work/ordinal/logs/build-{owner}.log")
        for name in names:
            paths.update({
                f"work/ordinal/{owner}/{name}/ordinals.csv",
                f"work/ordinal/{owner}/{name}/simulation.log",
            })
    return paths


def retained_payload(
    root: Path, result: dict[str, Any], names: list[str], prefix: str,
) -> dict[str, bytes]:
    files, directories = retained.scan_regular_tree(root)
    required = (retained.expected_evidence_paths(names) | {"campaign.log"} |
                ordinal_paths(names))
    missing, scratch = retained.closed_inventory(files, directories, required)
    if missing:
        raise V2Error(f"retained evidence is missing: {sorted(missing)}")
    retained.validate_claims(root, files, result, names)
    selected = required
    payload: dict[str, bytes] = {}
    for relative in sorted(selected):
        payload[f"{prefix}/{relative}"] = retained.read_regular(root, relative, files[relative])
    return payload


def semantic_definition() -> dict[str, Any]:
    return {
        "algorithm": "SHA-256",
        "input": "parsed a23_full_single_edge_replay_result_v1",
        "exclusions": "remove exactly these JSON pointers before serialization",
        "excluded_json_pointers": list(EPHEMERAL_LOG_POINTERS),
        "serialization": (
            "UTF-8 JSON; sort_keys=true; separators=(',',':'); ensure_ascii=true; "
            "no trailing newline"
        ),
        "all_other_fields_including_package_source_integration_tool_and_trace_identities":
            "included",
    }


def inventory(payload: dict[str, bytes], roles: dict[str, str]) -> list[dict[str, Any]]:
    return [{
        "path": path,
        "role": roles.get(path, "retained_campaign_evidence"),
        "size_bytes": len(data),
        "sha256": digest(data),
    } for path, data in sorted(payload.items())]


def write_archive(
    path: Path, manifest: dict[str, Any], payload: dict[str, bytes],
) -> None:
    if path.exists() or path.is_symlink():
        raise V2Error(f"archive output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = {f"{ARCHIVE_PREFIX}/MANIFEST.json": pretty(manifest)}
    entries.update({f"{ARCHIVE_PREFIX}/{name}": data for name, data in payload.items()})
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                           compresslevel=9) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in sorted(entries.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o444
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(data))


def read_archive(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    try:
        compressed_size = path.stat().st_size
    except OSError as error:
        raise V2Error(f"cannot stat export: {error}") from error
    if compressed_size > MAX_ARCHIVE_COMPRESSED_BYTES:
        raise V2Error("sealed export compressed-size limit exceeded")
    seen: set[str] = set()
    contents: dict[str, bytes] = {}
    expanded_size = 0
    member_count = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBER_COUNT:
                    raise V2Error("sealed export member-count limit exceeded")
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise V2Error("sealed export per-member size limit exceeded")
                expanded_size += member.size
                if expanded_size > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise V2Error("sealed export expanded-size limit exceeded")
                name = retained.safe_relative(member.name, "archive member")
                if name in seen:
                    raise V2Error(f"duplicate archive member: {name}")
                seen.add(name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise V2Error(f"archive member is not a regular file: {name}")
                if (member.mode != 0o444 or member.uid != 0 or member.gid != 0 or
                        member.uname not in ("", None) or member.gname not in ("", None) or
                        member.mtime != 0):
                    raise V2Error(f"unsafe or nondeterministic archive metadata: {name}")
                if set(member.pax_headers) - {"path"}:
                    raise V2Error(f"unexpected PAX metadata: {name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise V2Error(f"archive member cannot be read: {name}")
                remaining = member.size
                blocks: list[bytes] = []
                while remaining:
                    block = extracted.read(min(1024 * 1024, remaining))
                    if not block:
                        raise V2Error(f"archive member size differs: {name}")
                    blocks.append(block)
                    remaining -= len(block)
                if extracted.read(1):
                    raise V2Error(f"archive member size differs: {name}")
                data = b"".join(blocks)
                contents[name] = data
    except (tarfile.TarError, OSError) as error:
        raise V2Error(f"cannot reopen export: {error}") from error
    manifest_name = f"{ARCHIVE_PREFIX}/MANIFEST.json"
    if manifest_name not in contents:
        raise V2Error("sealed export manifest is absent")
    manifest = load_json_bytes(contents.pop(manifest_name), "sealed export manifest")
    if manifest.get("schema") != EXPORT_SCHEMA or manifest.get("status") != STATUS:
        raise V2Error("sealed export manifest schema/status differs")
    if set(manifest) != {
        "schema", "status", "archive_prefix", "safe_metadata", "closure",
        "inventory_entry_count", "inventory_size_bytes", "inventory",
    }:
        raise V2Error("sealed export manifest fields differ")
    if manifest.get("archive_prefix") != ARCHIVE_PREFIX:
        raise V2Error("sealed export prefix differs")
    if manifest.get("safe_metadata") != {
        "regular_files_only": True,
        "mode": "0444",
        "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
        "gzip_mtime": 0,
    }:
        raise V2Error("sealed export metadata contract differs")
    if manifest.get("closure") != {
        "manifest_is_the_only_non_inventory_member": True,
        "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
        "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
        "missing_or_extra_entries": "FORBIDDEN",
    }:
        raise V2Error("sealed export closure contract differs")
    expected: dict[str, dict[str, Any]] = {}
    rows = manifest.get("inventory")
    if not isinstance(rows, list):
        raise V2Error("sealed export inventory is not a list")
    for row in rows:
        row = retained.require_object(row, "sealed export inventory row")
        if set(row) != {"path", "role", "size_bytes", "sha256"}:
            raise V2Error("sealed export inventory row fields differ")
        if (not isinstance(row["role"], str) or not row["role"] or
                not isinstance(row["size_bytes"], int) or
                isinstance(row["size_bytes"], bool) or row["size_bytes"] < 0 or
                not isinstance(row["sha256"], str) or
                re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None):
            raise V2Error("sealed export inventory row value differs")
        name = f"{ARCHIVE_PREFIX}/{retained.safe_relative(row['path'], 'inventory path')}"
        if name in expected:
            raise V2Error(f"duplicate inventory path: {name}")
        expected[name] = row
    require_int(manifest.get("inventory_entry_count"),
                "sealed export inventory counter")
    require_int(manifest.get("inventory_size_bytes"),
                "sealed export inventory byte counter")
    if manifest.get("inventory_entry_count") != len(rows):
        raise V2Error("sealed export inventory counter differs")
    if manifest.get("inventory_size_bytes") != sum(row["size_bytes"] for row in rows):
        raise V2Error("sealed export inventory byte counter differs")
    if set(contents) != set(expected):
        raise V2Error(
            f"sealed export is not closed: missing={sorted(set(expected)-set(contents))} "
            f"extra={sorted(set(contents)-set(expected))}"
        )
    for name, row in expected.items():
        data = contents[name]
        if len(data) != row["size_bytes"] or digest(data) != row["sha256"]:
            raise V2Error(f"sealed export hash/size differs: {name}")
    payload = {name.removeprefix(f"{ARCHIVE_PREFIX}/"): data
               for name, data in contents.items()}
    return manifest, payload


def validate_publication_inventory_counter(
    manifest: dict[str, Any], publication: dict[str, Any],
) -> None:
    actual = len(manifest["inventory"])
    require_int(publication.get("export_inventory_entry_count"),
                "publication inventory counter")
    if publication.get("export_inventory_entry_count") != actual:
        raise V2Error("publication inventory counter differs")


def campaign_execution_accounting() -> dict[str, int]:
    return {
        **retained.EXPECTED_EXECUTION_ACCOUNTING,
        "ordinal_observation_actual_RTL_executions": 100,
        "total_actual_RTL_executions": 212,
    }


def v2_execution_accounting() -> dict[str, Any]:
    return {
        "primary_campaign": campaign_execution_accounting(),
        "reproduction_campaign": campaign_execution_accounting(),
        "combined": {
            "full50_actual_RTL_executions": 200,
            "reset_actual_RTL_executions": 4,
            "mutation_activation_actual_RTL_executions": 4,
            "mutation_actual_RTL_executions": 16,
            "ordinal_observation_actual_RTL_executions": 200,
            "total_actual_RTL_executions": 424,
            "receipt_only_executions": 0,
        },
    }


def validate_v2_metadata(v2_result: dict[str, Any]) -> None:
    if set(v2_result) != V2_RESULT_KEYS:
        raise V2Error("published v2 result fields differ")
    if v2_result.get("schema") != V2_RESULT_SCHEMA or v2_result.get("status") != STATUS:
        raise V2Error("published v2 result schema/status differs")
    if v2_result.get("evidence_class") != (
            "TEAM_DEFINED_SYNTHETIC_FULL50_ACTUAL_SINGLE_EDGE_RTL_V2"):
        raise V2Error("published v2 evidence class differs")
    dataset = retained.require_object(v2_result.get("dataset"), "v2 dataset")
    if set(dataset) != {
        "id", "source_class", "organizer_official", "trace_count",
        "shared_prepared_trace_count", "per_campaign_actual_full50_executions",
        "combined_actual_full50_executions", "trace_identities",
    }:
        raise V2Error("v2 dataset fields differ")
    for key in ("trace_count", "shared_prepared_trace_count",
                "per_campaign_actual_full50_executions",
                "combined_actual_full50_executions"):
        require_int(dataset[key], f"v2 dataset/{key}")
    if type(dataset["organizer_official"]) is not bool:
        raise V2Error("v2 dataset organizer flag is not an exact boolean")
    if (dataset["id"] != "full50" or
            dataset["source_class"] != "TEAM_DEFINED_SYNTHETIC" or
            dataset["organizer_official"] is not False or
            dataset["trace_count"] != 50 or
            dataset["shared_prepared_trace_count"] != 50 or
            dataset["per_campaign_actual_full50_executions"] != 100 or
            dataset["combined_actual_full50_executions"] != 200 or
            not isinstance(dataset["trace_identities"], list) or
            len(dataset["trace_identities"]) != 50):
        raise V2Error("v2 dataset counters differ")
    require_typed_equal(v2_result.get("execution_accounting"),
                        v2_execution_accounting(), "v2 execution accounting")
    identities = retained.require_object(v2_result.get("identities"), "v2 identities")
    if set(identities) != {
        "package_commit", "package_tree", "package_input_identity_sha256",
        "source_commit", "source_tree", "integration_commit", "integration_tree",
        "tool_identity_sha256", "trace_identity_sha256", "pins_sha256",
    }:
        raise V2Error("v2 identity fields differ")
    primary = require_exact_keys(v2_result.get("primary"), {
        "legacy_result_sha256", "legacy_result_size_bytes",
    }, "v2 primary")
    if (not isinstance(primary["legacy_result_size_bytes"], int) or
            isinstance(primary["legacy_result_size_bytes"], bool) or
            primary["legacy_result_size_bytes"] < 0):
        raise V2Error("v2 primary result size differs")
    reproduction = retained.require_object(
        v2_result.get("semantic_reproduction"), "v2 semantic reproduction"
    )
    if set(reproduction) != {
        "definition", "semantic_digest_sha256", "ordinal_semantic_digest_sha256",
        "primary_legacy_result_sha256", "reproduction_legacy_result_sha256",
        "reproduction_legacy_result_size_bytes", "observed_difference_json_pointers",
        "reproduction_full50_runs",
    }:
        raise V2Error("v2 semantic reproduction fields differ")
    if reproduction.get("definition") != semantic_definition():
        raise V2Error("v2 semantic reproduction definition differs")
    if (not isinstance(reproduction["reproduction_legacy_result_size_bytes"], int) or
            isinstance(reproduction["reproduction_legacy_result_size_bytes"], bool) or
            reproduction["reproduction_legacy_result_size_bytes"] < 0):
        raise V2Error("v2 reproduction result size differs")
    sequence = retained.require_object(v2_result.get("sequence_evidence"),
                                       "v2 sequence evidence")
    if set(sequence) != {
        "primary_full50_runs", "event_row_order",
        "execution_time_global_retire_order",
        "primary_ordinal_observation_actual_RTL_executions",
        "reproduction_ordinal_observation_actual_RTL_executions",
        "within_same_cycle_global_order_reconstructable_from_ordinal_sidecars",
        "ordinal_definition", "ordinal_semantic_projection_exclusion",
    }:
        raise V2Error("v2 sequence evidence fields differ")
    require_int(sequence["primary_ordinal_observation_actual_RTL_executions"],
                "v2 primary ordinal execution count")
    require_int(sequence["reproduction_ordinal_observation_actual_RTL_executions"],
                "v2 reproduction ordinal execution count")
    require_typed_equal({
        "event_row_order": sequence["event_row_order"],
        "execution_time_global_retire_order":
            sequence["execution_time_global_retire_order"],
        "within_same_cycle_global_order_reconstructable_from_ordinal_sidecars":
            sequence["within_same_cycle_global_order_reconstructable_from_ordinal_sidecars"],
        "ordinal_definition": sequence["ordinal_definition"],
        "ordinal_semantic_projection_exclusion":
            sequence["ordinal_semantic_projection_exclusion"],
    }, {
        "event_row_order": "trace/TB event-id row order retained and hashed",
        "execution_time_global_retire_order":
            "checked by the pinned TB accepted FIFO and bound by each retained PASS log",
        "within_same_cycle_global_order_reconstructable_from_ordinal_sidecars": True,
        "ordinal_definition":
            "monotonic global ordinal assigned lane0 then lane1 on each observed edge",
        "ordinal_semantic_projection_exclusion":
            ["/each_row/ordinal_simulation_log_sha256"],
    }, "v2 sequence definition")
    if (len(sequence["primary_full50_runs"]) != 100 or
            len(reproduction["reproduction_full50_runs"]) != 100 or
            sequence["primary_ordinal_observation_actual_RTL_executions"] != 100 or
            sequence["reproduction_ordinal_observation_actual_RTL_executions"] != 100 or
            sequence["ordinal_semantic_projection_exclusion"] !=
            ["/each_row/ordinal_simulation_log_sha256"]):
        raise V2Error("v2 sequence evidence counter/definition differs")
    ordinal_semantic_projection(sequence["primary_full50_runs"])
    ordinal_semantic_projection(reproduction["reproduction_full50_runs"])
    if v2_result.get("qualification") != {
        "hardened_synthetic_single_edge_RTL": "PASS",
        "canonical_campaign": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
        "physical": "HOLD", "power": "HOLD", "CDC_RDC": "HOLD",
    }:
        raise V2Error("v2 qualification fields differ")


def validate_publication_metadata(publication: dict[str, Any]) -> None:
    if set(publication) != PUBLICATION_KEYS:
        raise V2Error("publication fields differ")
    if publication.get("schema") != PUBLICATION_SCHEMA or publication.get("status") != STATUS:
        raise V2Error("publication schema/status differs")
    if publication.get("pins_sha256") != EXPECTED_PINS_SHA256:
        raise V2Error("publication pins identity differs")
    for key in ("primary_legacy_result_size_bytes",
                "reproduction_legacy_result_size_bytes", "v2_result_size_bytes",
                "export_size_bytes", "export_inventory_entry_count"):
        require_int(publication.get(key), f"publication/{key}")
    if publication.get("physical_status") != "HOLD" or publication.get(
            "canonical_campaign_status") != "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT":
        raise V2Error("publication qualification status differs")


def archived_package_identity(
    payload: dict[str, bytes], primary: dict[str, Any], package_commit: str,
) -> str:
    expected, _ = authoritative_roster(package_commit)
    if primary["provenance"]["verified_files"] != expected:
        raise V2Error("embedded producer roster differs from pins")
    expected = dict(expected)
    for relative in IMPLEMENTATION_FILES:
        expected[relative] = digest(git_bytes(package_commit, relative))
    observed: dict[str, str] = {}
    for relative, expected_sha in sorted(expected.items()):
        path = f"inputs/repository/{relative}"
        if path not in payload or digest(payload[path]) != expected_sha:
            raise V2Error(f"archived package input differs: {relative}")
        observed[relative] = expected_sha
    archived_paths = {
        path.removeprefix("inputs/repository/") for path in payload
        if path.startswith("inputs/repository/")
    }
    if archived_paths != set(expected):
        raise V2Error("archived package input inventory is not closed")
    return digest(semantic_bytes(observed))


def materialize_campaign(payload: dict[str, bytes], root: Path, campaign: str) -> None:
    prefix = f"{campaign}/"
    for name, data in payload.items():
        if not name.startswith(prefix):
            continue
        relative = retained.safe_relative(
            name.removeprefix(prefix), f"{campaign} payload path"
        )
        destination = root.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def validate_reopened(
    archive_path: Path, result_path: Path, publication_path: Path,
) -> dict[str, Any]:
    manifest, payload = read_archive(archive_path)
    v2_result = load_json(result_path, "published v2 result")
    publication = load_json(publication_path, "v2 publication")
    validate_v2_metadata(v2_result)
    validate_publication_metadata(publication)
    checks = {
        "v2_result_sha256": file_digest(result_path),
        "export_sha256": file_digest(archive_path),
        "export_manifest_sha256": digest(pretty(manifest)),
    }
    for key, actual in checks.items():
        if publication.get(key) != actual:
            raise V2Error(f"publication tuple differs for {key}")
    if publication.get("v2_result_size_bytes") != result_path.stat().st_size:
        raise V2Error("publication v2 result size differs")
    if publication.get("export_size_bytes") != archive_path.stat().st_size:
        raise V2Error("publication export size differs")
    validate_publication_inventory_counter(manifest, publication)
    embedded_result = payload.get("result/synthetic_v2_result.json")
    if embedded_result != result_path.read_bytes():
        raise V2Error("embedded and published v2 results differ")
    primary_result = load_json_bytes(payload["primary/result.json"], "embedded primary result")
    reproduction_result = load_json_bytes(
        payload["reproduction/result.json"], "embedded reproduction result"
    )
    for campaign, result in (("primary", primary_result),
                             ("reproduction", reproduction_result)):
        data = payload[f"{campaign}/result.json"]
        sha_key = f"{campaign}_legacy_result_sha256"
        size_key = f"{campaign}_legacy_result_size_bytes"
        if publication.get(sha_key) != digest(data) or publication.get(size_key) != len(data):
            raise V2Error(f"publication {campaign} result identity differs")
    primary_metadata = v2_result["primary"]
    if set(primary_metadata) != {
        "legacy_result_sha256", "legacy_result_size_bytes",
    }:
        raise V2Error("v2 primary fields differ")
    if (primary_metadata["legacy_result_sha256"] != digest(payload["primary/result.json"]) or
            primary_metadata["legacy_result_size_bytes"] !=
            len(payload["primary/result.json"])):
        raise V2Error("v2 primary result identity differs")
    reproduction_metadata = v2_result["semantic_reproduction"]
    if (reproduction_metadata["primary_legacy_result_sha256"] !=
            digest(payload["primary/result.json"]) or
            reproduction_metadata["reproduction_legacy_result_sha256"] !=
            digest(payload["reproduction/result.json"]) or
            reproduction_metadata["reproduction_legacy_result_size_bytes"] !=
            len(payload["reproduction/result.json"])):
        raise V2Error("v2 reproduction result identity differs")
    primary_semantic = semantic_digest(primary_result)
    reproduction_semantic = semantic_digest(reproduction_result)
    if primary_semantic != reproduction_semantic:
        raise V2Error("reopened semantic reproduction differs")
    if v2_result["semantic_reproduction"]["semantic_digest_sha256"] != primary_semantic:
        raise V2Error("v2 semantic digest differs after reopen")
    if publication["semantic_digest_sha256"] != primary_semantic:
        raise V2Error("publication semantic digest differs after reopen")
    differences = sorted(difference_pointers(primary_result, reproduction_result))
    if differences != v2_result["semantic_reproduction"]["observed_difference_json_pointers"]:
        raise V2Error("v2 observed difference pointers differ after reopen")
    if not set(differences) <= set(EPHEMERAL_LOG_POINTERS):
        raise V2Error("non-ephemeral result field differs across reproduction")
    names = verify_result_identity(primary_result, publication["package_commit"])
    if verify_result_identity(reproduction_result, publication["package_commit"]) != names:
        raise V2Error("reopened reproduction identity differs")
    package_tree = verified_commit_tree(publication["package_commit"],
                                        "publication package commit")
    if publication["package_tree"] != package_tree:
        raise V2Error("publication package tree differs")
    verify_rtl_git_blobs(primary_result)
    verify_rtl_git_blobs(reproduction_result)
    actual_tool_digest = verify_tools(primary_result)
    if verify_tools(reproduction_result) != actual_tool_digest:
        raise V2Error("reproduction tool identity differs")
    traces, actual_trace_digest = trace_identity(primary_result, names)
    reproduction_traces, reproduction_trace_digest = trace_identity(
        reproduction_result, names
    )
    if reproduction_traces != traces or reproduction_trace_digest != actual_trace_digest:
        raise V2Error("reproduction trace identity differs")
    package_payload, verified_package_digest = verify_package_inputs(
        primary_result, publication["package_commit"]
    )
    required_campaign = (
        retained.expected_evidence_paths(names) | {"campaign.log"} |
        ordinal_paths(names)
    )
    expected_payload = (
        {f"primary/{name}" for name in required_campaign} |
        {f"reproduction/{name}" for name in required_campaign} |
        set(package_payload) | {"result/synthetic_v2_result.json"}
    )
    if set(payload) != expected_payload:
        raise V2Error("sealed export payload inventory differs from closed requirement")
    actual_package_digest = archived_package_identity(
        payload, primary_result, publication["package_commit"]
    )
    if verified_package_digest != actual_package_digest or any(
            payload.get(path) != data for path, data in package_payload.items()):
        raise V2Error("archived package inputs differ after Git revalidation")
    with tempfile.TemporaryDirectory(prefix="a23-v2-reopen-") as temporary:
        extracted = Path(temporary)
        sequences_by_campaign: dict[str, list[dict[str, Any]]] = {}
        for campaign, result in (("primary", primary_result),
                                 ("reproduction", reproduction_result)):
            campaign_root = extracted / campaign
            materialize_campaign(payload, campaign_root, campaign)
            files, directories = retained.scan_regular_tree(campaign_root)
            required = (retained.expected_evidence_paths(names) | {"campaign.log"} |
                        ordinal_paths(names))
            missing, scratch = retained.closed_inventory(files, directories, required)
            if missing or scratch:
                raise V2Error(
                    f"reopened {campaign} payload is missing evidence or contains scratch"
                )
            retained.validate_claims(campaign_root, files, result, names)
            sequences_by_campaign[campaign] = sequence_evidence(campaign_root, names)
        primary_sequences = sequences_by_campaign["primary"]
        reproduction_sequences = sequences_by_campaign["reproduction"]
        if primary_sequences != v2_result["sequence_evidence"]["primary_full50_runs"]:
            raise V2Error("reopened primary sequence evidence differs")
        if reproduction_sequences != v2_result["semantic_reproduction"][
                "reproduction_full50_runs"]:
            raise V2Error("reopened reproduction sequence evidence differs")
        primary_ordinal = ordinal_semantic_digest(primary_sequences)
        reproduction_ordinal = ordinal_semantic_digest(reproduction_sequences)
        if primary_ordinal != reproduction_ordinal:
            raise V2Error("reproduction ordinal semantics differ")
        if (v2_result["semantic_reproduction"]["ordinal_semantic_digest_sha256"] !=
                primary_ordinal or
                publication["ordinal_semantic_digest_sha256"] != primary_ordinal):
            raise V2Error("published ordinal semantic digest differs")
    identity = v2_result["identities"]
    if v2_result["dataset"]["trace_identities"] != traces:
        raise V2Error("v2 trace identities differ")
    independently_recomputed = {
        "tool_identity_sha256": actual_tool_digest,
        "trace_identity_sha256": actual_trace_digest,
        "package_input_identity_sha256": actual_package_digest,
        "package_tree": package_tree,
    }
    for key, actual in independently_recomputed.items():
        if identity.get(key) != actual:
            raise V2Error(f"independently recomputed identity differs for {key}")
    for key in ("package_commit", "package_tree", "source_commit", "source_tree",
                "integration_commit", "integration_tree", "tool_identity_sha256",
                "trace_identity_sha256", "package_input_identity_sha256",
                "pins_sha256"):
        if publication.get(key) != identity.get(key):
            raise V2Error(f"publication identity tuple differs for {key}")
    return {
        "status": STATUS,
        "archive_sha256": checks["export_sha256"],
        "archive_size_bytes": archive_path.stat().st_size,
        "inventory_entry_count": len(manifest["inventory"]),
        "semantic_digest_sha256": primary_semantic,
    }


def seal(
    primary_root: Path, reproduction_root: Path, result_path: Path,
    archive_path: Path, publication_path: Path,
) -> dict[str, Any]:
    outputs = (result_path, archive_path, publication_path)
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise V2Error("v2 publication outputs must not exist")
    package_commit = current_commit()
    primary = load_json(primary_root / "result.json", "primary result")
    reproduction = load_json(reproduction_root / "result.json", "reproduction result")
    names = verify_result_identity(primary, package_commit)
    if verify_result_identity(reproduction, package_commit) != names:
        raise V2Error("reproduction trace roster differs")
    differences = sorted(difference_pointers(primary, reproduction))
    if not set(differences) <= set(EPHEMERAL_LOG_POINTERS):
        raise V2Error(f"non-ephemeral reproduction differences: {differences}")
    primary_semantic = semantic_digest(primary)
    reproduction_semantic = semantic_digest(reproduction)
    if primary_semantic != reproduction_semantic:
        raise V2Error("semantic reproduction digest differs")
    verify_rtl_git_blobs(primary)
    tool_digest = verify_tools(primary)
    package_payload, package_digest = verify_package_inputs(primary, package_commit)
    traces, trace_digest = trace_identity(primary, names)
    primary_payload = retained_payload(
        primary_root, primary, names, "primary",
    )
    reproduction_payload = retained_payload(
        reproduction_root, reproduction, names, "reproduction",
    )
    primary_sequences = sequence_evidence(primary_root, names)
    reproduction_sequences = sequence_evidence(reproduction_root, names)
    primary_ordinal_digest = ordinal_semantic_digest(primary_sequences)
    reproduction_ordinal_digest = ordinal_semantic_digest(reproduction_sequences)
    if primary_ordinal_digest != reproduction_ordinal_digest:
        raise V2Error("semantic reproduction ordinal digest differs")
    package_tree = verified_commit_tree(package_commit, "v2 package commit")
    identities = {
        "package_commit": package_commit,
        "package_tree": package_tree,
        "package_input_identity_sha256": package_digest,
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_tree": EXPECTED_SOURCE_TREE,
        "integration_commit": EXPECTED_INTEGRATION_COMMIT,
        "integration_tree": EXPECTED_INTEGRATION_TREE,
        "tool_identity_sha256": tool_digest,
        "trace_identity_sha256": trace_digest,
        "pins_sha256": EXPECTED_PINS_SHA256,
    }
    v2_result = {
        "schema": V2_RESULT_SCHEMA,
        "status": STATUS,
        "evidence_class": "TEAM_DEFINED_SYNTHETIC_FULL50_ACTUAL_SINGLE_EDGE_RTL_V2",
        "dataset": {
            "id": "full50",
            "source_class": "TEAM_DEFINED_SYNTHETIC",
            "organizer_official": False,
            "trace_count": 50,
            "shared_prepared_trace_count": 50,
            "per_campaign_actual_full50_executions": 100,
            "combined_actual_full50_executions": 200,
            "trace_identities": traces,
        },
        "execution_accounting": v2_execution_accounting(),
        "identities": identities,
        "primary": {
            "legacy_result_sha256": file_digest(primary_root / "result.json"),
            "legacy_result_size_bytes": (primary_root / "result.json").stat().st_size,
        },
        "semantic_reproduction": {
            "definition": semantic_definition(),
            "semantic_digest_sha256": primary_semantic,
            "ordinal_semantic_digest_sha256": primary_ordinal_digest,
            "primary_legacy_result_sha256": file_digest(primary_root / "result.json"),
            "reproduction_legacy_result_sha256": file_digest(
                reproduction_root / "result.json"
            ),
            "reproduction_legacy_result_size_bytes": (
                reproduction_root / "result.json"
            ).stat().st_size,
            "observed_difference_json_pointers": differences,
            "reproduction_full50_runs": reproduction_sequences,
        },
        "sequence_evidence": {
            "primary_full50_runs": primary_sequences,
            "event_row_order": "trace/TB event-id row order retained and hashed",
            "execution_time_global_retire_order": (
                "checked by the pinned TB accepted FIFO and bound by each retained PASS log"
            ),
            "primary_ordinal_observation_actual_RTL_executions": 100,
            "reproduction_ordinal_observation_actual_RTL_executions": 100,
            "within_same_cycle_global_order_reconstructable_from_ordinal_sidecars": True,
            "ordinal_definition": (
                "monotonic global ordinal assigned lane0 then lane1 on each observed edge"
            ),
            "ordinal_semantic_projection_exclusion": [
                "/each_row/ordinal_simulation_log_sha256"
            ],
        },
        "qualification": {
            "hardened_synthetic_single_edge_RTL": "PASS",
            "canonical_campaign": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
            "physical": "HOLD",
            "power": "HOLD",
            "CDC_RDC": "HOLD",
        },
    }
    validate_v2_metadata(v2_result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(pretty(v2_result))
    payload = {}
    payload.update(primary_payload)
    payload.update(reproduction_payload)
    payload.update(package_payload)
    payload["result/synthetic_v2_result.json"] = result_path.read_bytes()
    roles = {path: "verified_repository_input" for path in package_payload}
    roles["primary/result.json"] = "primary_legacy_result"
    roles["primary/campaign.log"] = "primary_campaign_driver_log"
    roles["reproduction/result.json"] = "semantic_reproduction_legacy_result"
    roles["reproduction/campaign.log"] = "semantic_reproduction_campaign_driver_log"
    roles["reproduction/ordinal_campaign.log"] = (
        "semantic_reproduction_ordinal_campaign_driver_log"
    )
    roles["result/synthetic_v2_result.json"] = "published_v2_result"
    rows = inventory(payload, roles)
    manifest = {
        "schema": EXPORT_SCHEMA,
        "status": STATUS,
        "archive_prefix": ARCHIVE_PREFIX,
        "safe_metadata": {
            "regular_files_only": True,
            "mode": "0444",
            "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
            "gzip_mtime": 0,
        },
        "closure": {
            "manifest_is_the_only_non_inventory_member": True,
            "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
            "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
            "missing_or_extra_entries": "FORBIDDEN",
        },
        "inventory_entry_count": len(rows),
        "inventory_size_bytes": sum(row["size_bytes"] for row in rows),
        "inventory": rows,
    }
    write_archive(archive_path, manifest, payload)
    publication = {
        "schema": PUBLICATION_SCHEMA,
        "status": STATUS,
        **identities,
        "semantic_digest_sha256": primary_semantic,
        "ordinal_semantic_digest_sha256": primary_ordinal_digest,
        "primary_legacy_result_sha256": file_digest(primary_root / "result.json"),
        "primary_legacy_result_size_bytes": (primary_root / "result.json").stat().st_size,
        "reproduction_legacy_result_sha256": file_digest(
            reproduction_root / "result.json"
        ),
        "reproduction_legacy_result_size_bytes": (
            reproduction_root / "result.json"
        ).stat().st_size,
        "v2_result_sha256": file_digest(result_path),
        "v2_result_size_bytes": result_path.stat().st_size,
        "export_sha256": file_digest(archive_path),
        "export_size_bytes": archive_path.stat().st_size,
        "export_manifest_sha256": digest(pretty(manifest)),
        "export_inventory_entry_count": len(rows),
        "physical_status": "HOLD",
        "canonical_campaign_status": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
    }
    validate_publication_metadata(publication)
    publication_path.write_bytes(pretty(publication))
    return validate_reopened(archive_path, result_path, publication_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    campaign = subparsers.add_parser("campaign")
    campaign.add_argument("--retained-root", required=True, type=Path)
    sealer = subparsers.add_parser("seal")
    sealer.add_argument("--primary-root", required=True, type=Path)
    sealer.add_argument("--reproduction-root", required=True, type=Path)
    sealer.add_argument("--result", required=True, type=Path)
    sealer.add_argument("--archive", required=True, type=Path)
    sealer.add_argument("--publication", required=True, type=Path)
    validator = subparsers.add_parser("validate")
    validator.add_argument("--result", required=True, type=Path)
    validator.add_argument("--archive", required=True, type=Path)
    validator.add_argument("--publication", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "campaign":
            run_campaign(args.retained_root)
            print(f"A23_SYNTHETIC_V2_CAMPAIGN_PASS retained_root={args.retained_root}")
        elif args.command == "seal":
            report = seal(
                args.primary_root, args.reproduction_root,
                args.result, args.archive, args.publication,
            )
            print("A23_SYNTHETIC_V2_SEAL_PASS " + json.dumps(report, sort_keys=True))
        else:
            report = validate_reopened(args.archive, args.result, args.publication)
            print("A23_SYNTHETIC_V2_VALIDATE_PASS " + json.dumps(report, sort_keys=True))
        return 0
    except (V2Error, retained.RejectError, retained.HoldError, OSError,
            subprocess.SubprocessError, ValueError, KeyError) as error:
        print(f"A23_SYNTHETIC_V2_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
