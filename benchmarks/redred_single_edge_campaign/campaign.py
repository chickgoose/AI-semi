#!/usr/bin/env python3
"""Validate the committed hardened A2/A3 single-edge receipt without overclaiming."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from types import ModuleType
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("campaign.json")
CAMPAIGN_ID = "redred-a2-a3-single-edge-campaign-v2"
EVIDENCE_CLASS = "A23_FULL_SINGLE_EDGE_REPLAY_ACTUAL_RTL_V1"
SOURCE_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
INTEGRATION_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
SOURCE_TREE = "e6030c7990f602a7fc1c73ac529b008b8e2c4133"
INTEGRATION_TREE = "d0fda8da2c10693b5d7093e0e2d505590722c1ea"
PACKAGE_COMMIT = "ce711a4c553c5fb6cc4b2c8afc45509c4b4bf993"
PUBLICATION_COMMIT = "72491e45a35e6883bd4ee65d5c30409c108ab190"
RESULT_PATH = "tests/a23_full_single_edge_replay/result.json"
RESULT_SHA256 = "e21e714e4c4ebbeba4caf63ad5656b2b29fc05881ebb74ea6d93114c5f7d8cf4"
RESULT_SEMANTIC_SHA256 = "9fd365edc6b5b57db8a99de32bde95117f08a6ada547abd6a0c44a8149cad56f"
PINS_PATH = "tests/a23_full_single_edge_replay/pins.json"
PINS_SHA256 = "0daba2132010272a78b56ec2a1541f30f7cb5d2b0d8562102cb70cf9e098d8e0"
FULL50_MANIFEST_PATH = "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
FULL50_MANIFEST_SHA256 = "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9"
TRACE_REGISTRY_PATH = "scripts/common_suite_official.py"
TRACE_REGISTRY_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
RETAINED_SCHEMA_PATH = "benchmarks/redred_single_edge_campaign/replay_receipt.schema.json"
RETAINED_SCHEMA_SHA256 = "cb8b0e91c7a4f25191bbaff33692de440169d63cc97c8ed8a06ac9512c4500f4"
EXPECTED_CANDIDATES = ("a2", "a3")
EXPECTED_MUTATIONS = ("drop", "duplicate", "reorder", "reset_escape")
EXPECTED_DIAGNOSTICS = {
    "drop": "A23_SE_DROP_FAIL",
    "duplicate": "A23_SE_DUPLICATE_FAIL",
    "reorder": "A23_SE_REORDER_FAIL",
    "reset_escape": "A23_SE_RESET_ESCAPE_FAIL",
}
EXPECTED_TOTALS = {
    "a2": {
        "generated": 106416, "source_overrun": 2370, "accepted": 104046,
        "retired": 104046, "fixed_window_retired": 103940,
        "fixed_window_cycles": 115968, "count2_commits": 26953,
    },
    "a3": {
        "generated": 106416, "source_overrun": 12771, "accepted": 93645,
        "retired": 93645, "fixed_window_retired": 93548,
        "fixed_window_cycles": 115968, "count2_commits": 22284,
    },
}
EXPECTED_ACCEPT_RETIRE = {"a2": 3, "a3": 2}
PUBLIC_COMMIT = "a2ca2a492eeb6760f2d9aeca1c34445df245b304"
PUBLIC_TREE = "500d100332b2e33c73a8eef4012a66f26d25d9e5"
PUBLIC_SPEC_PATH = "benchmarks/redred_uzh_shapes_projection/projection_spec.json"
PUBLIC_SPEC_SHA256 = "5c5d63eb86897247908df1bc9bcca3eb31dd12db4f8a34653f7727abaaecbd03"
PUBLIC_IMPLEMENTATION_PATH = "benchmarks/redred_uzh_shapes_projection/project.py"
PUBLIC_IMPLEMENTATION_SHA256 = "15fc4521e4812abfa53fd8690076f3446e5f4544bc7d2e2780232a79bc2e1e3f"
PUBLIC_TEST_PATH = "tests/redred_uzh_shapes_projection/test_projection.py"
PUBLIC_TEST_SHA256 = "9fc90b51f4b45a6951d69ce5bb161d4fa414247cafc790bf908b2384d066c30c"
PUBLIC_SCENARIOS = {
    "1x": {
        "trace_sha256": "c02aa20d8dc6cb2b85a500648e91f320d05f1f7e3b2d6e11d7189550b639ec94",
        "last_cycle": 153692, "fixed_window_cycles": 153693,
        "same_source_cycle_collision_extras": 81,
    },
    "64x": {
        "trace_sha256": "b005def64b130bc0e83b73cd9e6e4ab6d0a8f6e83f12b5b008b03642e17dcebb",
        "last_cycle": 2401, "fixed_window_cycles": 2402,
        "same_source_cycle_collision_extras": 81,
    },
    "256x": {
        "trace_sha256": "8428936e62b494747e9b75445e2a9b1f40677b92ede9f6569923729237c6f14a",
        "last_cycle": 600, "fixed_window_cycles": 601,
        "same_source_cycle_collision_extras": 133,
    },
}
LATENCY_KEYS = {"count", "mean", "p50", "p95", "p99", "max"}
RUN_KEYS = {
    "trace_sha256", "prepared_trace_sha256", "generated", "source_overrun",
    "accepted", "retired", "fixed_window_retired", "fixed_window_cycles",
    "observation_cycles", "count2_commits", "reset_test",
    "pre_reset_clean_drain", "occurrence_to_accept", "accept_to_retire",
    "fixed_window_events_per_cycle", "summary_sha256", "events_sha256",
}
AUXILIARY_KEYS = {
    "accept_to_retire", "accepted", "count2_commits", "events_sha256",
    "fixed_window_cycles", "fixed_window_events_per_cycle", "fixed_window_retired",
    "generated", "observation_cycles", "occurrence_to_accept",
    "pre_reset_clean_drain", "reset_test", "retired", "simulation_log_sha256",
    "source_overrun", "summary_sha256",
}


class CampaignError(RuntimeError):
    """A manifest, committed receipt, or claimed artifact is inconsistent."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def semantic_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def semantic_sha256(value: Any) -> str:
    return bytes_sha256(semantic_bytes(value))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must be an object")
    if set(value) != keys:
        raise CampaignError(
            f"{label} keys differ: missing={sorted(keys - set(value))} "
            f"extra={sorted(set(value) - keys)}"
        )
    return value


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignError(f"{label} must be a nonempty string")
    return value


def sha_string(value: Any, label: str) -> str:
    digest = nonempty(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CampaignError(f"{label} must be lowercase SHA-256")
    return digest


def commit_string(value: Any, label: str) -> str:
    commit = nonempty(value, label)
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise CampaignError(f"{label} must be a full lowercase Git object ID")
    return commit


def counter(value: Any, label: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        qualifier = "positive" if positive else "nonnegative"
        raise CampaignError(f"{label} must be a {qualifier} integer (bool is forbidden)")
    return value


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CampaignError(f"{label} must be a finite number (bool is forbidden)")
    number = float(value)
    if not math.isfinite(number):
        raise CampaignError(f"{label} must be finite")
    return number


def load_json_bytes(value: bytes, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, item in pairs:
            if key in document:
                raise CampaignError(f"{label} contains duplicate JSON key: {key}")
            document[key] = item
        return document

    def reject_nonstandard_constant(value: str) -> None:
        raise CampaignError(f"{label} contains non-standard JSON constant: {value}")

    try:
        document = json.loads(
            value.decode("utf-8"), object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignError(f"cannot decode {label}: {error}") from error
    if not isinstance(document, dict):
        raise CampaignError(f"{label} must contain an object")
    return document


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_json_bytes(path.read_bytes(), label)
    except OSError as error:
        raise CampaignError(f"cannot read {label}: {error}") from error


def git_bytes(root: Path, commit: str, path: str, label: str) -> bytes:
    commit_string(commit, f"{label} commit")
    if not path or Path(path).is_absolute() or ".." in PurePosixPath(path).parts:
        raise CampaignError(f"{label} has unsafe Git path")
    process = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise CampaignError(f"cannot read {label} from {commit}: {path}")
    return process.stdout


def git_tree(root: Path, commit: str, label: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise CampaignError(f"{label} Git commit is unavailable")
    return process.stdout.strip()


def checked_local_ref(root: Path, value: Any, label: str) -> dict[str, str]:
    row = exact(value, {"path", "sha256"}, label)
    raw_path = nonempty(row["path"], f"{label}.path")
    relative_path = PurePosixPath(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise CampaignError(f"{label} path must stay below the repository root")
    expected = sha_string(row["sha256"], f"{label}.sha256")
    path = root.joinpath(*relative_path.parts)
    cursor = root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CampaignError(f"{label} path traverses a symlink")
    if path.is_symlink() or not path.is_file():
        raise CampaignError(f"{label} is missing, not a file, or symlinked")
    actual = file_sha256(path)
    if actual != expected:
        raise CampaignError(f"{label} SHA-256 mismatch")
    return {"path": raw_path, "sha256": actual}


def resolve_cli_input(path: Path, label: str, *, directory: bool) -> Path:
    """Reject aliases and every symlink component before canonicalization."""
    if ".." in path.parts:
        raise CampaignError(f"{label} path aliases through '..'")
    absolute = path if path.is_absolute() else Path.cwd() / path
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CampaignError(f"{label} path traverses a symlink")
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise CampaignError(f"{label} is missing") from error
    if directory:
        if not resolved.is_dir():
            raise CampaignError(f"{label} is not a directory")
    elif not resolved.is_file():
        raise CampaignError(f"{label} is not a regular file")
    return resolved


def resolve_explicit_inputs(
    schema_path: Path, index_path: Path, artifact_root: Path,
) -> tuple[Path, Path, Path]:
    schema = resolve_cli_input(schema_path, "retained schema", directory=False)
    index = resolve_cli_input(index_path, "retained artifact index", directory=False)
    root = resolve_cli_input(artifact_root, "artifact root", directory=True)
    if schema == index or schema.samefile(index):
        raise CampaignError("retained schema and artifact index are path aliases")
    if schema.is_relative_to(root) or index.is_relative_to(root):
        raise CampaignError("retained schema/index must be outside the artifact root")
    return schema, index, root


def load_registry(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("redred_single_edge_registry", path)
    if spec is None or spec.loader is None:
        raise CampaignError("cannot load frozen trace registry")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(getattr(module, "FULL50", None), tuple) or not isinstance(
        getattr(module, "TRACE_SHA256", None), dict
    ):
        raise CampaignError("frozen trace registry is malformed")
    return module


def validate_latency(value: Any, expected_count: int, label: str) -> dict[str, Any]:
    row = exact(value, LATENCY_KEYS, label)
    count_value = counter(row["count"], f"{label}.count")
    mean_value = finite_number(row["mean"], f"{label}.mean")
    quantiles = {key: counter(row[key], f"{label}.{key}") for key in ("p50", "p95", "p99", "max")}
    if count_value != expected_count:
        raise CampaignError(f"{label}.count differs from accepted/retired")
    if not (quantiles["p50"] <= quantiles["p95"] <= quantiles["p99"] <= quantiles["max"]):
        raise CampaignError(f"{label} percentiles are not monotonic")
    if count_value == 0:
        if mean_value != 0.0 or any(quantiles.values()):
            raise CampaignError(f"{label} is nonzero with zero observations")
    elif not 0.0 <= mean_value <= quantiles["max"]:
        raise CampaignError(f"{label}.mean is outside the observed range")
    return {"count": count_value, "mean": row["mean"], **quantiles}


def validate_run(
    value: Any, label: str, expected_trace_sha: str, expected_cycles: int,
) -> dict[str, Any]:
    row = exact(value, RUN_KEYS, label)
    if row["trace_sha256"] != expected_trace_sha:
        raise CampaignError(f"{label} trace SHA differs from frozen full50")
    for key in ("prepared_trace_sha256", "summary_sha256", "events_sha256"):
        sha_string(row[key], f"{label}.{key}")
    numeric = {
        key: counter(row[key], f"{label}.{key}")
        for key in (
            "generated", "source_overrun", "accepted", "retired",
            "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
            "count2_commits", "reset_test", "pre_reset_clean_drain",
        )
    }
    if numeric["fixed_window_cycles"] != expected_cycles:
        raise CampaignError(f"{label} fixed window differs from team full50 manifest")
    if numeric["reset_test"] != 0 or numeric["pre_reset_clean_drain"] != 0:
        raise CampaignError(f"{label} is mislabeled as a reset run")
    if numeric["generated"] != numeric["source_overrun"] + numeric["accepted"]:
        raise CampaignError(f"{label} violates generated=source_overrun+accepted")
    if numeric["accepted"] != numeric["retired"]:
        raise CampaignError(f"{label} violates accepted=retired")
    if numeric["fixed_window_retired"] > numeric["retired"]:
        raise CampaignError(f"{label} fixed-window retirement exceeds retirement")
    rate = finite_number(row["fixed_window_events_per_cycle"], f"{label}.fixed_window_events_per_cycle")
    expected_rate = round(numeric["fixed_window_retired"] / numeric["fixed_window_cycles"], 9)
    if rate != expected_rate:
        raise CampaignError(f"{label} fixed-window throughput differs")
    occurrence = validate_latency(row["occurrence_to_accept"], numeric["accepted"], f"{label}.occurrence_to_accept")
    internal = validate_latency(row["accept_to_retire"], numeric["retired"], f"{label}.accept_to_retire")
    return {
        **numeric,
        "prepared_trace_sha256": row["prepared_trace_sha256"],
        "occurrence_to_accept": occurrence,
        "accept_to_retire": internal,
    }


def validate_result_semantics(
    result: dict[str, Any], registry: ModuleType, windows: dict[str, int], root: Path,
) -> dict[str, Any]:
    exact(result, {
        "schema", "status", "boundary", "acceptance_observation",
        "retirement_scoreboard", "event_identity_scope", "source_overrun_semantics",
        "reset_qualification", "conservation", "generator", "execution_accounting",
        "owners", "mutations", "provenance", "qualification",
    }, "committed replay result")
    if result["schema"] != "a23_full_single_edge_replay_result_v1" or result["status"] != "PASS":
        raise CampaignError("committed replay schema/status differs")
    expected_boundary = {
        "boundary": "actual_A2_A3_scheduler_plus_actual_single_edge_endpoint",
        "acceptance_observation": "actual_endpoint_atomic_source_accept_count_and_ordered_addresses",
        "retirement_scoreboard": "actual_single_edge_retire_prefix_in_global_accept_order",
        "event_identity_scope": "TB_identity_bound_to_observable_logical_source_stream",
        "source_overrun_semantics": "same_source_occurrence_while_one_entry_source_latch_occupied",
        "reset_qualification": "reset_only_after_external_clean_drain_and_no_protocol_error",
    }
    for key, expected in expected_boundary.items():
        if result[key] != expected:
            raise CampaignError(f"committed replay {key} differs")
    if result["conservation"] != [
        "generated = source_overrun + accepted",
        "after bounded drain: accepted = retired",
    ]:
        raise CampaignError("committed replay conservation contract differs")
    generator = exact(result["generator"], {
        "version", "source_commit", "full50_manifest_sha256", "trace_count",
    }, "committed replay generator")
    if generator != {
        "version": registry.GENERATOR_VERSION,
        "source_commit": registry.SOURCE_COMMIT,
        "full50_manifest_sha256": registry.SUITES["full50"]["manifest_sha256"],
        "trace_count": 50,
    }:
        raise CampaignError("committed replay generator/full50 identity differs")
    accounting = exact(result["execution_accounting"], {
        "owners", "full50_actual_RTL_executions", "reset_actual_RTL_executions",
        "mutation_activation_actual_RTL_executions", "mutation_actual_RTL_executions",
        "receipt_only_executions",
    }, "committed replay execution_accounting")
    expected_accounting = {
        "owners": 2, "full50_actual_RTL_executions": 100,
        "reset_actual_RTL_executions": 2,
        "mutation_activation_actual_RTL_executions": 2,
        "mutation_actual_RTL_executions": 8, "receipt_only_executions": 0,
    }
    for key, expected in expected_accounting.items():
        if counter(accounting[key], f"execution_accounting.{key}") != expected:
            raise CampaignError(f"committed replay execution_accounting.{key} differs")
    provenance = exact(result["provenance"], {
        "package_commit", "pins_path", "pins_sha256", "verified_files",
        "verified_tools", "actual_rtl_git",
    }, "committed replay provenance")
    if provenance["package_commit"] != PACKAGE_COMMIT or provenance["pins_path"] != PINS_PATH or \
            provenance["pins_sha256"] != PINS_SHA256:
        raise CampaignError("committed replay package/pins provenance differs")
    pins_bytes = git_bytes(root, PACKAGE_COMMIT, PINS_PATH, "hardened replay pins")
    if bytes_sha256(pins_bytes) != PINS_SHA256:
        raise CampaignError("hardened replay pins bytes differ")
    pins = load_json_bytes(pins_bytes, "hardened replay pins")
    verified_files = provenance["verified_files"]
    pinned_files = pins.get("files")
    if not isinstance(verified_files, dict) or not isinstance(pinned_files, dict) or \
            verified_files != pinned_files:
        raise CampaignError("committed replay verified_files differ from pins")
    for path, expected_sha in verified_files.items():
        nonempty(path, "verified_files path")
        sha_string(expected_sha, f"verified_files.{path}")
        if bytes_sha256(git_bytes(root, PACKAGE_COMMIT, path, f"pinned package file {path}")) != expected_sha:
            raise CampaignError(f"package commit file differs from verified hash: {path}")
    verified_tools = provenance["verified_tools"]
    pinned_tools = pins.get("tools")
    if not isinstance(verified_tools, dict) or not isinstance(pinned_tools, dict) or \
            set(verified_tools) != set(pinned_tools):
        raise CampaignError("committed replay tool roles differ from pins")
    for role, tool in verified_tools.items():
        tool = exact(tool, {"path", "sha256", "version"}, f"verified_tools.{role}")
        pinned_tool = pinned_tools[role]
        if not isinstance(pinned_tool, dict):
            raise CampaignError(f"pinned tool identity is malformed: {role}")
        if any(tool[key] != pinned_tool[key] for key in ("path", "sha256", "version")):
            raise CampaignError(f"committed replay tool identity differs: {role}")
    rtl = exact(provenance["actual_rtl_git"], {
        "source_commit", "integration_commit", "source_tree", "integration_tree",
        "verified_rtl_paths",
    }, "committed replay actual_rtl_git")
    if rtl["source_commit"] != SOURCE_COMMIT or rtl["integration_commit"] != INTEGRATION_COMMIT or \
            rtl["source_tree"] != SOURCE_TREE or rtl["integration_tree"] != INTEGRATION_TREE:
        raise CampaignError("committed replay hardened RTL Git provenance differs")
    if git_tree(root, SOURCE_COMMIT, "source") != SOURCE_TREE or \
            git_tree(root, INTEGRATION_COMMIT, "integration") != INTEGRATION_TREE:
        raise CampaignError("hardened RTL Git tree differs")
    expected_rtl_paths = sorted(path for path in verified_files if path.startswith("rtl/"))
    if rtl["verified_rtl_paths"] != expected_rtl_paths:
        raise CampaignError("committed replay RTL path closure differs")
    for path in expected_rtl_paths:
        expected_sha = verified_files[path]
        for commit, label in ((SOURCE_COMMIT, "source"), (INTEGRATION_COMMIT, "integration")):
            if bytes_sha256(git_bytes(root, commit, path, f"{label} RTL {path}")) != expected_sha:
                raise CampaignError(f"{label} RTL bytes differ from receipt: {path}")
    owners = result["owners"]
    if not isinstance(owners, dict) or list(owners) != list(EXPECTED_CANDIDATES):
        raise CampaignError("committed replay owners must be exactly ordered a2,a3")
    owner_summary: dict[str, Any] = {}
    for owner in EXPECTED_CANDIDATES:
        owner_row = exact(owners[owner], {
            "baseline_build_log_sha256", "full50", "reset", "mutation_activation",
        }, f"owners.{owner}")
        sha_string(owner_row["baseline_build_log_sha256"], f"owners.{owner}.baseline_build_log_sha256")
        full = exact(owner_row["full50"], {
            "actual_execution_count", "aggregate", "runs",
        }, f"owners.{owner}.full50")
        if counter(full["actual_execution_count"], f"owners.{owner}.full50.actual_execution_count") != 50:
            raise CampaignError(f"owners.{owner}.full50 execution count differs")
        runs = full["runs"]
        if not isinstance(runs, dict) or set(runs) != set(registry.FULL50):
            raise CampaignError(f"owners.{owner}.full50 roster differs")
        normalized_runs = {
            name: validate_run(
                runs[name], f"owners.{owner}.full50.runs.{name}",
                registry.TRACE_SHA256[name], windows[name],
            )
            for name in registry.FULL50
        }
        aggregate = exact(full["aggregate"], {
            "actual_execution_count", "totals", "occurrence_to_accept",
            "accept_to_retire", "fixed_window_events_per_cycle",
        }, f"owners.{owner}.full50.aggregate")
        if counter(aggregate["actual_execution_count"], "aggregate.actual_execution_count") != 50:
            raise CampaignError(f"owners.{owner}.aggregate execution count differs")
        totals = exact(aggregate["totals"], set(EXPECTED_TOTALS[owner]), f"owners.{owner}.aggregate.totals")
        calculated = {
            key: sum(run[key] for run in normalized_runs.values()) for key in totals
        }
        if totals != calculated or totals != EXPECTED_TOTALS[owner]:
            raise CampaignError(f"owners.{owner} aggregate totals differ from runs/pinned semantics")
        occurrence = validate_latency(
            aggregate["occurrence_to_accept"], totals["accepted"],
            f"owners.{owner}.aggregate.occurrence_to_accept",
        )
        internal = validate_latency(
            aggregate["accept_to_retire"], totals["retired"],
            f"owners.{owner}.aggregate.accept_to_retire",
        )
        fixed_rate = finite_number(
            aggregate["fixed_window_events_per_cycle"],
            f"owners.{owner}.aggregate.fixed_window_events_per_cycle",
        )
        if fixed_rate != round(totals["fixed_window_retired"] / totals["fixed_window_cycles"], 9):
            raise CampaignError(f"owners.{owner} aggregate throughput differs")
        if internal != {
            "count": totals["retired"], "mean": float(EXPECTED_ACCEPT_RETIRE[owner]),
            "p50": EXPECTED_ACCEPT_RETIRE[owner], "p95": EXPECTED_ACCEPT_RETIRE[owner],
            "p99": EXPECTED_ACCEPT_RETIRE[owner], "max": EXPECTED_ACCEPT_RETIRE[owner],
        }:
            raise CampaignError(f"owners.{owner} accept-to-retire semantics differ")
        for reset_key, reset_expected in (("reset", 1), ("mutation_activation", 0)):
            auxiliary = exact(
                owner_row[reset_key], AUXILIARY_KEYS, f"owners.{owner}.{reset_key}"
            )
            for key in ("summary_sha256", "events_sha256", "simulation_log_sha256"):
                sha_string(auxiliary.get(key), f"owners.{owner}.{reset_key}.{key}")
            if counter(auxiliary.get("reset_test"), f"owners.{owner}.{reset_key}.reset_test") != reset_expected:
                raise CampaignError(f"owners.{owner}.{reset_key} reset identity differs")
            auxiliary_counts = {
                key: counter(auxiliary[key], f"owners.{owner}.{reset_key}.{key}")
                for key in (
                    "generated", "source_overrun", "accepted", "retired",
                    "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
                    "count2_commits", "pre_reset_clean_drain",
                )
            }
            if auxiliary_counts["generated"] != auxiliary_counts["source_overrun"] + auxiliary_counts["accepted"] or \
                    auxiliary_counts["accepted"] != auxiliary_counts["retired"]:
                raise CampaignError(f"owners.{owner}.{reset_key} conservation differs")
            validate_latency(
                auxiliary["occurrence_to_accept"], auxiliary_counts["accepted"],
                f"owners.{owner}.{reset_key}.occurrence_to_accept",
            )
            validate_latency(
                auxiliary["accept_to_retire"], auxiliary_counts["retired"],
                f"owners.{owner}.{reset_key}.accept_to_retire",
            )
            finite_number(
                auxiliary["fixed_window_events_per_cycle"],
                f"owners.{owner}.{reset_key}.fixed_window_events_per_cycle",
            )
        owner_summary[owner] = {
            "actual_execution_count": 50, "totals": totals,
            "fixed_window_events_per_cycle": fixed_rate,
            "occurrence_to_accept": occurrence, "accept_to_retire": internal,
        }
    for name in registry.FULL50:
        a2_prepared = owners["a2"]["full50"]["runs"][name]["prepared_trace_sha256"]
        a3_prepared = owners["a3"]["full50"]["runs"][name]["prepared_trace_sha256"]
        if a2_prepared != a3_prepared:
            raise CampaignError(f"A2/A3 prepared full50 input differs: {name}")
    mutations = result["mutations"]
    expected_order = [(owner, mutation) for owner in EXPECTED_CANDIDATES for mutation in EXPECTED_MUTATIONS]
    if not isinstance(mutations, list) or len(mutations) != len(expected_order):
        raise CampaignError("committed replay mutation count differs")
    for index, (mutation, expected_identity) in enumerate(zip(mutations, expected_order)):
        mutation = exact(mutation, {
            "owner", "mutation", "actual_endpoint_RTL_source_rewrite",
            "compiled_successfully", "executed", "killed", "exit_code",
            "first_diagnostic", "build_log_sha256", "simulation_log_sha256",
            "source_identity",
        }, f"mutations[{index}]")
        if (mutation["owner"], mutation["mutation"]) != expected_identity or \
                mutation["actual_endpoint_RTL_source_rewrite"] is not True or \
                mutation["compiled_successfully"] is not True or mutation["executed"] is not True or \
                mutation["killed"] is not True or type(mutation["exit_code"]) is not int or \
                mutation["exit_code"] != -6 or \
                mutation["first_diagnostic"] != EXPECTED_DIAGNOSTICS[mutation["mutation"]]:
            raise CampaignError(f"mutations[{index}] is not the expected killed actual-RTL mutation")
        sha_string(mutation["build_log_sha256"], f"mutations[{index}].build_log_sha256")
        sha_string(mutation["simulation_log_sha256"], f"mutations[{index}].simulation_log_sha256")
        identity = exact(mutation["source_identity"], {
            "target", "base_sha256", "old_anchor_sha256", "new_anchor_sha256",
            "literal_replacement_count", "mutant_sha256",
        }, f"mutations[{index}].source_identity")
        owner, mutation_name = expected_identity
        pinned_mutations = pins.get("mutations")
        if not isinstance(pinned_mutations, dict) or not isinstance(pinned_mutations.get(owner), dict) or \
                not isinstance(pinned_mutations[owner].get(mutation_name), dict):
            raise CampaignError(f"pinned mutation identity is malformed: {owner}/{mutation_name}")
        pinned_mutation = pinned_mutations[owner][mutation_name]
        target = pinned_mutation.get("target")
        old = pinned_mutation.get("old")
        new = pinned_mutation.get("new")
        if not all(isinstance(item, str) for item in (target, old, new)):
            raise CampaignError(f"pinned mutation rewrite is malformed: {owner}/{mutation_name}")
        base = git_bytes(root, PACKAGE_COMMIT, target, f"mutation base {owner}/{mutation_name}")
        old_bytes = old.encode("utf-8")
        new_bytes = new.encode("utf-8")
        if base.count(old_bytes) != 1:
            raise CampaignError(f"pinned mutation anchor is not unique: {owner}/{mutation_name}")
        mutant = base.replace(old_bytes, new_bytes, 1)
        expected_identity_row = {
            "target": target,
            "base_sha256": bytes_sha256(base),
            "old_anchor_sha256": bytes_sha256(old_bytes),
            "new_anchor_sha256": bytes_sha256(new_bytes),
            "literal_replacement_count": 1,
            "mutant_sha256": bytes_sha256(mutant),
        }
        if identity != expected_identity_row:
            raise CampaignError(f"mutations[{index}] source rewrite identity differs")
    qualification = exact(result["qualification"], {
        "single_edge_digital_RTL", "physical", "power", "CDC_RDC",
    }, "committed replay qualification")
    if qualification != {
        "single_edge_digital_RTL": "GO", "physical": "HOLD",
        "power": "HOLD", "CDC_RDC": "HOLD",
    }:
        raise CampaignError("committed replay qualification boundary differs")
    return {
        "owners": owner_summary, "execution_accounting": expected_accounting,
        "qualification_claim": qualification,
        "provenance": {
            "package_commit": PACKAGE_COMMIT, "pins_sha256": PINS_SHA256,
            "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
            "integration_commit": INTEGRATION_COMMIT, "integration_tree": INTEGRATION_TREE,
        },
    }


def validate_public_extension(value: Any, root: Path) -> dict[str, Any]:
    row = exact(value, {
        "id", "source_class", "canonical_redred_traffic", "official_contest_traffic",
        "projection_commit", "projection_tree", "projection_spec",
        "projection_implementation", "projection_test", "unique_source_occurrences",
        "scenario_relation", "scenarios", "retained_projection_receipt",
        "actual_replay_receipt",
    }, "datasets.public_projected_extension")
    if row["id"] != "uzh_shapes_rotation_41p321_41p322" or \
            row["source_class"] != "PUBLIC_PROJECTED_EXTENSION" or \
            row["canonical_redred_traffic"] is not False or \
            row["official_contest_traffic"] is not False:
        raise CampaignError("public extension was relabeled as canonical or official contest traffic")
    if row["projection_commit"] != PUBLIC_COMMIT or row["projection_tree"] != PUBLIC_TREE or \
            git_tree(root, PUBLIC_COMMIT, "public projection") != PUBLIC_TREE:
        raise CampaignError("public projection Git provenance differs")
    expected_refs = {
        "projection_spec": (PUBLIC_SPEC_PATH, PUBLIC_SPEC_SHA256),
        "projection_implementation": (PUBLIC_IMPLEMENTATION_PATH, PUBLIC_IMPLEMENTATION_SHA256),
        "projection_test": (PUBLIC_TEST_PATH, PUBLIC_TEST_SHA256),
    }
    blobs: dict[str, bytes] = {}
    for key, (expected_path, expected_sha) in expected_refs.items():
        reference = exact(row[key], {"path", "sha256"}, f"public extension {key}")
        if reference != {"path": expected_path, "sha256": expected_sha}:
            raise CampaignError(f"public extension {key} reference differs")
        blob = git_bytes(root, PUBLIC_COMMIT, expected_path, f"public extension {key}")
        if bytes_sha256(blob) != expected_sha:
            raise CampaignError(f"public extension {key} bytes differ")
        blobs[key] = blob
    spec = load_json_bytes(blobs["projection_spec"], "public projection spec")
    if spec.get("status") != "PUBLIC_PROJECTED_EXTENSION_UNREPLAYED" or \
            spec.get("release_status") != "HOLD" or \
            spec.get("dataset", {}).get("canonical_redred_traffic") is not False or \
            spec.get("dataset", {}).get("official_redred_traffic") is not False or \
            spec.get("window", {}).get("expected_event_count") != 1100:
        raise CampaignError("public projection spec classification/count differs")
    if counter(row["unique_source_occurrences"], "public unique_source_occurrences", positive=True) != 1100 or \
            row["scenario_relation"] != "TIMING_VARIANTS_OF_ONE_SOURCE_WINDOW_NOT_INDEPENDENT_SAMPLES":
        raise CampaignError("public extension occurrence/scenario semantics differ")
    scenarios = row["scenarios"]
    if not isinstance(scenarios, list) or [scenario.get("id") for scenario in scenarios if isinstance(scenario, dict)] != list(PUBLIC_SCENARIOS):
        raise CampaignError("public extension scenario roster/order differs")
    test_text = blobs["projection_test"].decode("utf-8")
    for scenario in scenarios:
        scenario_id = scenario["id"]
        expected = {"id": scenario_id, **PUBLIC_SCENARIOS[scenario_id]}
        if scenario != expected:
            raise CampaignError(f"public extension scenario differs: {scenario_id}")
        if expected["trace_sha256"] not in test_text:
            raise CampaignError(f"public extension scenario hash is not bound by committed test: {scenario_id}")
    if row["retained_projection_receipt"] is not None or row["actual_replay_receipt"] is not None:
        raise CampaignError("public extension claims uncommitted projection/replay evidence")
    return {
        "status": "HOLD_PUBLIC_PROJECTED_EXTENSION_UNREPLAYED",
        "source_class": "PUBLIC_PROJECTED_EXTENSION",
        "canonical_redred_traffic": False, "official_contest_traffic": False,
        "unique_source_occurrences": 1100,
        "scenario_relation": row["scenario_relation"], "scenarios": scenarios,
        "projection_commit": PUBLIC_COMMIT, "projection_spec_sha256": PUBLIC_SPEC_SHA256,
        "remaining_dependency": "retained projection package plus actual A2/A3 replay on identical projected traces",
    }


def validate_manifest(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    exact(manifest, {
        "schema", "campaign_id", "datasets", "producer", "committed_replay_result",
        "retained_artifact_schema", "policies",
    }, "campaign manifest")
    if manifest["schema"] != "redred_single_edge_campaign_manifest_v2" or \
            manifest["campaign_id"] != CAMPAIGN_ID:
        raise CampaignError("campaign manifest identity differs")
    datasets = exact(manifest["datasets"], {
        "canonical_synthetic_full50", "public_projected_extension",
    }, "campaign datasets")
    canonical_dataset = exact(datasets["canonical_synthetic_full50"], {
        "id", "source_class", "canonical_redred_traffic", "official_contest_traffic",
        "manifest", "trace_registry",
    }, "datasets.canonical_synthetic_full50")
    if canonical_dataset["id"] != "full50" or \
            canonical_dataset["source_class"] != "TEAM_DEFINED_SYNTHETIC" or \
            canonical_dataset["canonical_redred_traffic"] is not True or \
            canonical_dataset["official_contest_traffic"] is not False:
        raise CampaignError("team full50 classification changed or was relabeled as official contest traffic")
    if canonical_dataset["manifest"] != {
        "path": FULL50_MANIFEST_PATH, "sha256": FULL50_MANIFEST_SHA256,
    } or canonical_dataset["trace_registry"] != {
        "path": TRACE_REGISTRY_PATH, "sha256": TRACE_REGISTRY_SHA256,
    }:
        raise CampaignError("team full50 manifest/registry identity differs")
    manifest_ref = checked_local_ref(root, canonical_dataset["manifest"], "team full50 manifest")
    registry_ref = checked_local_ref(root, canonical_dataset["trace_registry"], "team full50 registry")
    suite = load_json(root / manifest_ref["path"], "team full50 manifest")
    registry = load_registry(root / registry_ref["path"])
    if suite.get("schema_version") != 1 or not isinstance(suite.get("runs"), list):
        raise CampaignError("team full50 manifest schema differs")
    names = [run.get("name") for run in suite["runs"] if isinstance(run, dict)]
    windows = {
        run["name"]: counter(run.get("stim_cycles"), f"team full50 {run.get('name')} stim_cycles", positive=True)
        for run in suite["runs"] if isinstance(run, dict)
    }
    if tuple(names) != registry.FULL50 or len(names) != 50 or set(registry.TRACE_SHA256) != set(names):
        raise CampaignError("team full50 manifest/registry roster differs")
    public_summary = validate_public_extension(datasets["public_projected_extension"], root)
    producer = exact(manifest["producer"], {
        "id", "path", "evidence_class", "source_commit", "source_tree",
        "integration_commit", "integration_tree", "package_commit",
    }, "campaign producer")
    if producer != {
        "id": "a23_full_single_edge_replay", "path": "tests/a23_full_single_edge_replay",
        "evidence_class": EVIDENCE_CLASS, "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE, "integration_commit": INTEGRATION_COMMIT,
        "integration_tree": INTEGRATION_TREE, "package_commit": PACKAGE_COMMIT,
    }:
        raise CampaignError("campaign hardened replay producer provenance differs")
    result_ref = exact(manifest["committed_replay_result"], {
        "publication_commit", "path", "sha256", "semantic_sha256",
    }, "committed_replay_result")
    if result_ref != {
        "publication_commit": PUBLICATION_COMMIT, "path": RESULT_PATH,
        "sha256": RESULT_SHA256, "semantic_sha256": RESULT_SEMANTIC_SHA256,
    }:
        raise CampaignError("committed replay result identity/hash differs")
    schema_ref = checked_local_ref(root, manifest["retained_artifact_schema"], "retained artifact schema")
    if schema_ref != {"path": RETAINED_SCHEMA_PATH, "sha256": RETAINED_SCHEMA_SHA256}:
        raise CampaignError("retained artifact schema identity differs")
    schema_document = load_json(root / schema_ref["path"], "retained artifact schema")
    if schema_document.get("$id") != "redred_single_edge_retained_artifact_index_v2":
        raise CampaignError("retained artifact schema document identity differs")
    policies = exact(manifest["policies"], {
        "require_retained_artifacts_for_campaign_pass", "full50_public_pooling",
        "public_extension_blocks_canonical", "receipt_claim_is_not_artifact_replay",
    }, "campaign policies")
    if policies != {
        "require_retained_artifacts_for_campaign_pass": True,
        "full50_public_pooling": "FORBIDDEN",
        "public_extension_blocks_canonical": False,
        "receipt_claim_is_not_artifact_replay": True,
    }:
        raise CampaignError("campaign release/provenance policy differs")
    return {
        "registry": registry, "windows": windows,
        "canonical_dataset": {**canonical_dataset, "manifest": manifest_ref, "trace_registry": registry_ref},
        "public_summary": public_summary, "result_ref": result_ref,
        "schema_ref": schema_ref,
    }


def load_committed_result(root: Path, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    reference = context["result_ref"]
    raw = git_bytes(root, reference["publication_commit"], reference["path"], "committed hardened replay result")
    actual_raw = bytes_sha256(raw)
    if actual_raw != reference["sha256"]:
        raise CampaignError("committed hardened replay result raw SHA-256 differs")
    result = load_json_bytes(raw, "committed hardened replay result")
    actual_semantic = semantic_sha256(result)
    if actual_semantic != reference["semantic_sha256"]:
        raise CampaignError("committed hardened replay result semantic SHA-256 differs")
    summary = validate_result_semantics(result, context["registry"], context["windows"], root)
    return result, {
        "status": "PASS", "trust": "COMMITTED_RECEIPT_CONSISTENT",
        "publication_commit": reference["publication_commit"], "path": reference["path"],
        "sha256": actual_raw, "semantic_sha256": actual_semantic, **summary,
    }


def explicit_input_state(values: Iterable[Any]) -> str:
    supplied = [value is not None for value in values]
    if not any(supplied):
        return "NONE"
    if not all(supplied):
        raise CampaignError(
            "retained schema path/hash, artifact index path/hash, and artifact root must be supplied together"
        )
    return "ALL"


def read_bound_artifact(
    artifact_root: Path, value: Any, label: str,
    seen_paths: set[Path], seen_inodes: set[tuple[int, int]],
    *, keep_bytes: bool,
) -> tuple[dict[str, Any], bytes | None]:
    artifact = exact(value, {"path", "sha256", "size_bytes"}, label)
    raw_path = nonempty(artifact["path"], f"{label}.path")
    relative_path = PurePosixPath(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts or \
            relative_path.as_posix() != raw_path:
        raise CampaignError(f"{label} path is unsafe or aliases another spelling")
    path = artifact_root.joinpath(*relative_path.parts)
    cursor = artifact_root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CampaignError(f"{label} traverses a symlink")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(artifact_root)
        before = resolved.stat()
    except (OSError, ValueError) as error:
        raise CampaignError(f"{label} is missing or escapes artifact root") from error
    if not stat.S_ISREG(before.st_mode):
        raise CampaignError(f"{label} is not a regular file")
    identity = (before.st_dev, before.st_ino)
    if resolved in seen_paths or identity in seen_inodes:
        raise CampaignError(f"{label} is a duplicate path or file alias")
    expected_sha = sha_string(artifact["sha256"], f"{label}.sha256")
    expected_size = counter(artifact["size_bytes"], f"{label}.size_bytes", positive=True)
    try:
        content = resolved.read_bytes() if keep_bytes else None
        actual_sha = bytes_sha256(content) if content is not None else file_sha256(resolved)
        after = resolved.stat()
    except OSError as error:
        raise CampaignError(f"cannot read {label}") from error
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise CampaignError(f"{label} changed while it was inspected")
    if before.st_size != expected_size or actual_sha != expected_sha:
        raise CampaignError(f"{label} bytes/size differ")
    seen_paths.add(resolved)
    seen_inodes.add(identity)
    return {
        "path": raw_path, "sha256": actual_sha, "size_bytes": before.st_size,
    }, content


def validate_prepared_inputs(
    value: Any, artifact_root: Path, result: dict[str, Any], registry: ModuleType,
    seen_paths: set[Path], seen_inodes: set[tuple[int, int]],
) -> dict[str, Any]:
    owners = exact(value, set(EXPECTED_CANDIDATES), "prepared_inputs")
    retained: dict[str, dict[str, tuple[dict[str, Any], bytes]]] = {}
    for owner in EXPECTED_CANDIDATES:
        owner_inputs = owners[owner]
        if not isinstance(owner_inputs, dict) or set(owner_inputs) != set(registry.FULL50):
            raise CampaignError(f"prepared_inputs.{owner} roster differs from full50")
        retained[owner] = {}
        for name in registry.FULL50:
            metadata, content = read_bound_artifact(
                artifact_root, owner_inputs[name], f"prepared_inputs.{owner}.{name}",
                seen_paths, seen_inodes, keep_bytes=True,
            )
            assert content is not None
            expected_sha = result["owners"][owner]["full50"]["runs"][name]["prepared_trace_sha256"]
            if metadata["sha256"] != expected_sha:
                raise CampaignError(
                    f"prepared_inputs.{owner}.{name} differs from the committed run digest"
                )
            retained[owner][name] = (metadata, content)
    for name in registry.FULL50:
        a2_metadata, a2_content = retained["a2"][name]
        a3_metadata, a3_content = retained["a3"][name]
        if a2_metadata["sha256"] != a3_metadata["sha256"] or a2_content != a3_content:
            raise CampaignError(f"retained A2/A3 prepared input bytes differ: {name}")
    return {
        "run_count": len(EXPECTED_CANDIDATES) * len(registry.FULL50),
        "unique_trace_count": len({
            retained["a2"][name][0]["sha256"] for name in registry.FULL50
        }),
        "cross_owner_bytes_equal": True,
    }


def validate_explicit_artifact_claim(
    schema_path: Path, schema_sha: str, index_path: Path, index_sha: str,
    artifact_root: Path, context: dict[str, Any], result: dict[str, Any],
) -> dict[str, Any]:
    if schema_path.is_symlink() or not schema_path.is_file() or file_sha256(schema_path) != sha_string(schema_sha, "retained schema SHA"):
        raise CampaignError("explicit retained artifact schema bytes/hash differ")
    if file_sha256(schema_path) != context["schema_ref"]["sha256"]:
        raise CampaignError("explicit retained artifact schema is not the pinned schema")
    schema = load_json(schema_path, "retained artifact schema")
    if schema.get("$id") != "redred_single_edge_retained_artifact_index_v2":
        raise CampaignError("explicit retained artifact schema identity differs")
    if index_path.is_symlink() or not index_path.is_file() or file_sha256(index_path) != sha_string(index_sha, "artifact index SHA"):
        raise CampaignError("explicit retained artifact index bytes/hash differ")
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise CampaignError("artifact root is missing, not a directory, or symlinked")
    index = load_json(index_path, "retained artifact index")
    exact(index, {
        "schema", "evidence_class", "replay_result_sha256",
        "replay_result_semantic_sha256", "prepared_inputs", "artifacts",
    }, "retained artifact index")
    if index["schema"] != "redred_single_edge_retained_artifact_index_v2" or \
            index["evidence_class"] != EVIDENCE_CLASS or \
            index["replay_result_sha256"] != RESULT_SHA256 or \
            index["replay_result_semantic_sha256"] != RESULT_SEMANTIC_SHA256:
        raise CampaignError("retained artifact index is not bound to the committed replay result")
    expected_hashes: set[str] = set()
    for owner in EXPECTED_CANDIDATES:
        owner_row = result["owners"][owner]
        expected_hashes.add(owner_row["baseline_build_log_sha256"])
        for run in owner_row["full50"]["runs"].values():
            expected_hashes.update((run["summary_sha256"], run["events_sha256"]))
        for key in ("reset", "mutation_activation"):
            expected_hashes.update(
                owner_row[key][hash_key] for hash_key in
                ("summary_sha256", "events_sha256", "simulation_log_sha256")
            )
    for mutation in result["mutations"]:
        expected_hashes.update((mutation["build_log_sha256"], mutation["simulation_log_sha256"]))
    seen_paths: set[Path] = set()
    seen_inodes: set[tuple[int, int]] = set()
    artifacts = index["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise CampaignError("retained artifact index must contain artifacts")
    prepared_summary = validate_prepared_inputs(
        index["prepared_inputs"], artifact_root, result, context["registry"],
        seen_paths, seen_inodes,
    )
    observed: set[str] = set()
    root = artifact_root
    for position, artifact in enumerate(artifacts):
        metadata, _ = read_bound_artifact(
            root, artifact, f"artifact[{position}]", seen_paths, seen_inodes,
            keep_bytes=False,
        )
        expected_sha = metadata["sha256"]
        if expected_sha not in expected_hashes or expected_sha in observed:
            raise CampaignError(f"artifact[{position}] is extra, duplicate, or not receipt-bound")
        observed.add(expected_sha)
    if observed != expected_hashes:
        raise CampaignError("retained artifact index is incomplete for receipt-bound hashes")
    return {
        "status": "PASS_RECEIPT_BOUND_HASHES_ONLY",
        "verified_hash_count": len(observed),
        "prepared_inputs": prepared_summary,
        "campaign_gate": "HOLD_FULL50_LOGS_AND_PRODUCER_SEALED_BUNDLE_ABSENT",
        "reason": "full50 simulation logs are not receipt-bound and no producer-compatible sealed bundle is retained",
    }


def evaluate(
    manifest_path: Path, root: Path, replay_schema: Path | None = None,
    replay_schema_sha256: str | None = None, replay_receipt: Path | None = None,
    replay_receipt_sha256: str | None = None, artifact_root: Path | None = None,
) -> dict[str, Any]:
    manifest = load_json(manifest_path, "campaign manifest")
    context = validate_manifest(manifest, root)
    result, receipt = load_committed_result(root, context)
    explicit_state = explicit_input_state((
        replay_schema, replay_schema_sha256, replay_receipt,
        replay_receipt_sha256, artifact_root,
    ))
    if explicit_state == "ALL":
        assert replay_schema is not None and replay_schema_sha256 is not None
        assert replay_receipt is not None and replay_receipt_sha256 is not None
        assert artifact_root is not None
        replay_schema, replay_receipt, artifact_root = resolve_explicit_inputs(
            replay_schema, replay_receipt, artifact_root,
        )
        artifact_status = validate_explicit_artifact_claim(
            replay_schema, replay_schema_sha256, replay_receipt,
            replay_receipt_sha256, artifact_root, context, result,
        )
    else:
        artifact_status = {
            "status": "HOLD_MISSING_RETAINED_ARTIFACTS",
            "verified_hash_count": 0,
            "campaign_gate": "HOLD",
            "reason": "the committed receipt contains hashes and summaries but retained run artifacts were not committed or supplied",
        }
    return {
        "schema": "redred_single_edge_campaign_evidence_v2",
        "status": "HOLD",
        "campaign_id": CAMPAIGN_ID,
        "manifest": {"path": str(manifest_path.resolve()), "sha256": file_sha256(manifest_path)},
        "receipt_validation": receipt,
        "retained_artifact_validation": artifact_status,
        "gates": {
            "committed_hardened_receipt": "PASS",
            "canonical_synthetic_receipt_semantics": "PASS",
            "retained_replay_artifacts": artifact_status["campaign_gate"],
            "canonical_single_edge_campaign": "HOLD",
            "public_projected_extension": "HOLD",
            "system_release": "HOLD",
        },
        "datasets": {
            "canonical_synthetic_full50": {
                "status": "PASS_RECEIPT_ONLY_ARTIFACT_HOLD",
                "id": "full50", "source_class": "TEAM_DEFINED_SYNTHETIC",
                "canonical_redred_traffic": True, "official_contest_traffic": False,
                "run_count": 50, "unique_trace_count": 50,
                "candidates": receipt["owners"],
            },
            "public_projected_extension": context["public_summary"],
        },
        "aggregation_policy": {
            "full50_public_pooling": "FORBIDDEN",
            "public_scenarios_are_timing_variants_not_independent_samples": True,
        },
        "claim_boundary": {
            "producer_claim": receipt["qualification_claim"],
            "campaign_accepts_receipt_claim_as_artifact_replay": False,
            "new_evidence_inferred": False,
        },
        "remaining_dependencies": [
            "retained prepared inputs and receipt-bound replay artifacts; full50 logs and a producer-compatible sealed bundle remain absent",
            "retained UZH projection package and actual A2/A3 replay on identical projected traces",
            "physical, power, and CDC/RDC gates remain outside this receipt",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?", choices=("evaluate",))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--replay-schema", type=Path,
                        help="explicit retained-artifact index schema")
    parser.add_argument("--replay-schema-sha256")
    parser.add_argument("--replay-receipt", type=Path,
                        help="explicit retained-artifact index (not a replacement replay result)")
    parser.add_argument("--replay-receipt-sha256")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-hold", action="store_true")
    args = parser.parse_args()
    try:
        report = evaluate(
            args.manifest.resolve(), args.repo_root.resolve(),
            args.replay_schema if args.replay_schema else None,
            args.replay_schema_sha256,
            args.replay_receipt if args.replay_receipt else None,
            args.replay_receipt_sha256,
            args.artifact_root if args.artifact_root else None,
        )
        payload = canonical(report)
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
        sys.stdout.buffer.write(payload)
        return 0 if args.allow_hold else 3
    except (CampaignError, OSError, subprocess.SubprocessError) as error:
        print(f"REDRED_SINGLE_EDGE_CAMPAIGN_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
