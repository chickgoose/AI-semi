#!/usr/bin/env python3
"""Fail-closed validator and dry-run planner for the canonical REDRED campaign."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("campaign.json")
PINS_PATH = "tests/a23_full_p6_replay/pins.json"
SHA256_LEN = 64
EXPECTED_CANDIDATES = ["fovea", "cluster2", "a2_p6", "a3_p6"]
EXPECTED_DATASET_CLASSES = {
    "full50": "synthetic",
    "capacity22": "synthetic",
    "organizer_supplied": "supplied",
    "public_dataset": "public",
}
EXPECTED_OWNERS = ["a2", "a3", "a4"]
EXPECTED_ACCEPTANCE = "actual_atomic_bundle_commit_count_and_ordered_addresses"
EXPECTED_RETIREMENT = "actual_P6_retire_valid_and_addresses_in_global_accept_order"
EXPECTED_BOUNDARY = "actual_scheduler_plus_actual_phase_related_always_ready_P6"
EXPECTED_PUBLICATION = "immutable_two_commit_package_then_result"
MUTATION_CONTRACT = {
    "drop": ("A23_REPLAY_DROP_FAIL", None, True),
    "duplicate": ("A23_REPLAY_DUP_FAIL", None, True),
    "swap": ("A23_REPLAY_SWAP_FAIL", "A7_P6_MUTATE_SWAP_PAIR", False),
    "microstep": ("A23_REPLAY_MICROSTEP_FAIL", "A7_P6_MUTATE_PARTIAL_PAIR_COMMIT", False),
    "reset": ("A23_REPLAY_RESET_FAIL", "A7_P6_MUTATE_RESET_PHANTOM", False),
}
RUN_KEYS = {
    "trace_sha256", "prepared_trace_sha256", "generated", "source_overrun",
    "accepted", "retired", "fixed_window_retired", "fixed_window_cycles",
    "observation_cycles", "reset_test", "occurrence_to_accept",
    "accept_to_retire", "fixed_window_events_per_cycle", "summary_sha256",
    "events_sha256",
}
RESET_KEYS = RUN_KEYS - {"trace_sha256", "prepared_trace_sha256"}
LATENCY_KEYS = {"count", "mean", "p50", "p95", "p99", "max"}
SUMMARY_FIELDS = {
    "owner", "trace", "generated", "source_overrun", "accepted", "retired",
    "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
    "reset_test",
}
EVENT_FIELDS = {
    "owner", "trace", "tb_only_event_id", "logical_source", "occurrence_cycle",
    "accept_cycle", "retire_cycle", "deadline_cycle", "event_state",
}


class CampaignError(RuntimeError):
    """The manifest or claimed evidence is malformed, inconsistent, or tampered."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must be an object")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise CampaignError(f"{label} keys differ: missing={missing} extra={extra}")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignError(f"{label} must be a nonempty string")
    return value


def _sha_string(value: Any, label: str) -> str:
    digest = _nonempty(value, label)
    if len(digest) != SHA256_LEN or any(ch not in "0123456789abcdef" for ch in digest):
        raise CampaignError(f"{label} is not lowercase SHA-256")
    return digest


def _counter(value: Any, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise CampaignError(f"{label} must be a {qualifier} integer (bool is forbidden)")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CampaignError(f"{label} must be a finite number (bool is forbidden)")
    return float(value)


def _named(rows: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise CampaignError(f"{label} must be a nonempty list")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CampaignError(f"{label}[{index}] must be an object")
        name = _nonempty(row.get("id"), f"{label}[{index}].id")
        if name in result:
            raise CampaignError(f"{label} contains duplicate id {name}")
        result[name] = row
    return result


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CampaignError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must contain a JSON object")
    return value


def _file_ref(root: Path, value: Any, label: str) -> tuple[Path, dict[str, str]]:
    row = _exact_keys(value, {"path", "sha256"}, label)
    raw_path = _nonempty(row["path"], f"{label}.path")
    expected = _sha_string(row["sha256"], f"{label}.sha256")
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    if path.is_symlink() or not path.is_file():
        raise CampaignError(f"{label} is missing, non-file, or symlinked: {path}")
    actual = sha256(path)
    if actual != expected:
        raise CampaignError(f"{label} SHA-256 mismatch: expected={expected} actual={actual}")
    return path.resolve(), {"path": raw_path, "sha256": actual}


def _load_official(root: Path, reference: Any) -> tuple[ModuleType, dict[str, str]]:
    path, identity = _file_ref(root, reference, "official_registry")
    spec = importlib.util.spec_from_file_location("redred_common_suite_official", path)
    if spec is None or spec.loader is None:
        raise CampaignError("official_registry cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required = ("SOURCE_COMMIT", "GENERATOR_VERSION", "TRACE_SHA256", "FULL50", "CAPACITY22", "SUITES")
    if any(not hasattr(module, name) for name in required):
        raise CampaignError("official_registry lacks required frozen identities")
    return module, identity


def _validate_measurements(rows: Any) -> dict[str, dict[str, Any]]:
    measurements = _named(rows, "measurement_definitions")
    expected = {
        "id", "cycle_semantics", "generated", "source_overrun", "accepted",
        "delivered", "hard_correct_conservation", "throughput", "fixed_window",
        "occurrence_to_accept_latency", "accept_to_deliver_latency",
    }
    for name, row in measurements.items():
        _exact_keys(row, expected, f"measurement_definitions.{name}")
        for key in expected - {"hard_correct_conservation"}:
            _nonempty(row[key], f"measurement_definitions.{name}.{key}")
        if row["hard_correct_conservation"] != [
            "generated=source_overrun+accepted", "accepted=delivered"
        ]:
            raise CampaignError(f"measurement_definitions.{name} changes hard-correct conservation")
    return measurements


def _manifest_runs(path: Path, label: str) -> tuple[list[str], dict[str, int]]:
    document = _load_json(path, label)
    if document.get("schema_version") != 1 or not isinstance(document.get("runs"), list):
        raise CampaignError(f"{label} is not a schema-version-1 suite manifest")
    names: list[str] = []
    cycles: dict[str, int] = {}
    for index, row in enumerate(document["runs"]):
        if not isinstance(row, dict):
            raise CampaignError(f"{label}.runs[{index}] must be an object")
        name = _nonempty(row.get("name"), f"{label}.runs[{index}].name")
        stim_cycles = _counter(row.get("stim_cycles"), f"{label}.runs[{index}].stim_cycles", positive=True)
        if name in cycles:
            raise CampaignError(f"{label} contains duplicate run {name}")
        names.append(name)
        cycles[name] = stim_cycles
    return names, cycles


def _validate_dataset_provenance(
    root: Path, rows: Any, official: ModuleType
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    datasets = _named(copy.deepcopy(rows), "datasets")
    if list(datasets) != list(EXPECTED_DATASET_CLASSES):
        raise CampaignError("dataset IDs/order are hard-bound to full50, capacity22, organizer_supplied, public_dataset")
    expected = {
        "id", "source_class", "required_for_release", "suite_key", "manifest",
        "provenance_manifest", "subset_of",
    }
    summary: dict[str, Any] = {}
    holds: list[str] = []
    for name, row in datasets.items():
        _exact_keys(row, expected, f"datasets.{name}")
        source_class = row["source_class"]
        if source_class != EXPECTED_DATASET_CLASSES[name]:
            raise CampaignError(f"datasets.{name} class is hard-bound to {EXPECTED_DATASET_CLASSES[name]}")
        if type(row["required_for_release"]) is not bool:
            raise CampaignError(f"datasets.{name}.required_for_release must be boolean")
        if source_class == "synthetic":
            suite_key = _nonempty(row["suite_key"], f"datasets.{name}.suite_key")
            if suite_key != name or suite_key not in official.SUITES:
                raise CampaignError(f"datasets.{name} must use frozen suite key {name}")
            path, identity = _file_ref(root, row["manifest"], f"datasets.{name}.manifest")
            frozen = official.SUITES[suite_key]
            if identity["sha256"] != frozen["manifest_sha256"] or path.name != frozen["manifest_name"]:
                raise CampaignError(f"datasets.{name} differs from the official frozen manifest")
            names, cycles = _manifest_runs(path, f"datasets.{name}.manifest")
            if tuple(names) != tuple(frozen["names"]):
                raise CampaignError(f"datasets.{name} ordered run names differ from official registry")
            expected_subset = "full50" if name == "capacity22" else None
            if row["subset_of"] != expected_subset or row["provenance_manifest"] is not None:
                raise CampaignError(f"datasets.{name} synthetic provenance/subset contract differs")
            summary[name] = {
                "status": "MANIFEST_PINNED", "source_class": source_class,
                "manifest": identity, "run_count": len(names),
                "trace_hash_source": "official_registry",
            }
            row["_run_names"] = names
            row["_stim_cycles"] = cycles
        else:
            if row["suite_key"] is not None or row["manifest"] is not None or row["subset_of"] is not None:
                raise CampaignError(f"datasets.{name} cannot be relabeled as synthetic or subset data")
            provenance_ref = row["provenance_manifest"]
            if provenance_ref is None:
                summary[name] = {
                    "status": "MISSING", "source_class": source_class,
                    "provenance_manifest": None, "content": None,
                    "adapter": None, "trace_manifest": None,
                }
                if row["required_for_release"]:
                    holds.append(f"dataset {name}: missing pinned {source_class} bytes and provenance")
                continue
            path, identity = _file_ref(root, provenance_ref, f"datasets.{name}.provenance_manifest")
            provenance = _load_json(path, f"datasets.{name}.provenance_manifest")
            common = {
                "schema", "dataset_id", "source_class", "license", "content",
                "adapter", "trace_manifest",
            }
            specific = {"provider", "delivery_id"} if source_class == "supplied" else {
                "source_url", "dataset_version", "retrieved_at"
            }
            _exact_keys(provenance, common | specific, f"datasets.{name}.provenance")
            if provenance["schema"] != "redred_dataset_provenance_v2" or \
                    provenance["dataset_id"] != name or provenance["source_class"] != source_class:
                raise CampaignError(f"datasets.{name} provenance identity mismatch")
            _nonempty(provenance["license"], f"datasets.{name}.provenance.license")
            for key in specific:
                _nonempty(provenance[key], f"datasets.{name}.provenance.{key}")
            pinned = {}
            for key in ("content", "adapter", "trace_manifest"):
                _, pinned[key] = _file_ref(root, provenance[key], f"datasets.{name}.provenance.{key}")
            summary[name] = {
                "status": "BYTES_PINNED_NO_CANDIDATE_RESULTS", "source_class": source_class,
                "provenance_manifest": identity, **pinned,
            }
            if row["required_for_release"]:
                holds.append(f"dataset {name}: bytes pinned but canonical candidate results are missing")

    capacity = datasets["capacity22"]
    full = datasets["full50"]
    if tuple(capacity["_run_names"]) != tuple(official.CAPACITY22) or \
            any(name not in full["_stim_cycles"] for name in capacity["_run_names"]):
        raise CampaignError("capacity22 is not the exact ordered full50 subset")
    for name in capacity["_run_names"]:
        if capacity["_stim_cycles"][name] != full["_stim_cycles"][name]:
            raise CampaignError(f"capacity22 run {name} does not inherit its full50 window")
    return datasets, summary, holds


def _validate_latency(value: Any, label: str, expected_count: int) -> dict[str, Any]:
    row = _exact_keys(value, LATENCY_KEYS, label)
    count = _counter(row["count"], f"{label}.count")
    if count != expected_count:
        raise CampaignError(f"{label}.count differs from accepted/delivered count")
    mean = _number(row["mean"], f"{label}.mean")
    percentiles = {
        key: _counter(row[key], f"{label}.{key}") for key in ("p50", "p95", "p99", "max")
    }
    if not (percentiles["p50"] <= percentiles["p95"] <= percentiles["p99"] <= percentiles["max"]):
        raise CampaignError(f"{label} percentiles are not monotonic")
    if count == 0:
        if mean != 0.0 or any(percentiles.values()):
            raise CampaignError(f"{label} nonzero latency with zero count")
    elif not 0.0 <= mean <= percentiles["max"]:
        raise CampaignError(f"{label}.mean is outside its observed range")
    return {"count": count, "mean": row["mean"], **percentiles}


def _rate(events: int, cycles: int) -> float:
    return round(events / max(1, cycles), 9)


def _validate_run_row(
    value: Any, label: str, *, owner: str, trace: str, trace_sha: str | None,
    prepared_required: bool, fixed_cycles: int, reset_test: int,
) -> dict[str, Any]:
    expected_keys = RUN_KEYS if prepared_required else RESET_KEYS
    row = _exact_keys(value, expected_keys, label)
    if prepared_required:
        if row["trace_sha256"] != trace_sha:
            raise CampaignError(f"{label} trace SHA differs")
        _sha_string(row["prepared_trace_sha256"], f"{label}.prepared_trace_sha256")
    summary_sha = _sha_string(row["summary_sha256"], f"{label}.summary_sha256")
    events_sha = _sha_string(row["events_sha256"], f"{label}.events_sha256")
    generated = _counter(row["generated"], f"{label}.generated")
    overrun = _counter(row["source_overrun"], f"{label}.source_overrun")
    accepted = _counter(row["accepted"], f"{label}.accepted")
    retired = _counter(row["retired"], f"{label}.retired")
    fixed_events = _counter(row["fixed_window_retired"], f"{label}.fixed_window_retired")
    cycles = _counter(row["fixed_window_cycles"], f"{label}.fixed_window_cycles")
    observation = _counter(row["observation_cycles"], f"{label}.observation_cycles", positive=True)
    reset = _counter(row["reset_test"], f"{label}.reset_test")
    if reset != reset_test or cycles != fixed_cycles:
        raise CampaignError(f"{label} reset flag or fixed measurement window differs")
    if generated != overrun + accepted:
        raise CampaignError(f"{label} violates generated=source_overrun+accepted")
    if accepted != retired:
        raise CampaignError(f"{label} violates accepted=delivered")
    if fixed_events > retired or (cycles == 0 and fixed_events != 0):
        raise CampaignError(f"{label} has invalid fixed measurement window")
    rate = _number(row["fixed_window_events_per_cycle"], f"{label}.fixed_window_events_per_cycle")
    if rate != _rate(fixed_events, cycles):
        raise CampaignError(f"{label} fixed-window throughput differs")
    occurrence = _validate_latency(row["occurrence_to_accept"], f"{label}.occurrence_to_accept", accepted)
    internal = _validate_latency(row["accept_to_retire"], f"{label}.accept_to_retire", accepted)
    result = {
        "generated": generated, "source_overrun": overrun, "accepted": accepted,
        "retired": retired, "delivered": retired,
        "fixed_window_retired": fixed_events, "fixed_window_cycles": cycles,
        "fixed_window_events_per_cycle": row["fixed_window_events_per_cycle"],
        "observation_cycles": observation, "reset_test": reset,
        "occurrence_to_accept": occurrence, "accept_to_retire": internal,
        "summary_sha256": summary_sha, "events_sha256": events_sha,
        "owner": owner, "trace": trace,
    }
    if prepared_required:
        result["trace_sha256"] = row["trace_sha256"]
        result["prepared_trace_sha256"] = row["prepared_trace_sha256"]
    return result


def _sum_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "generated", "source_overrun", "accepted", "retired",
        "fixed_window_retired", "fixed_window_cycles",
    )
    selected = list(rows)
    return {key: sum(row[key] for row in selected) for key in keys}


def _validate_aggregate(value: Any, label: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    row = _exact_keys(value, {
        "run_count", "totals", "occurrence_to_accept", "accept_to_retire",
        "fixed_window_events_per_cycle",
    }, label)
    run_count = _counter(row["run_count"], f"{label}.run_count")
    if run_count != len(runs):
        raise CampaignError(f"{label}.run_count differs")
    totals_row = _exact_keys(row["totals"], {
        "generated", "source_overrun", "accepted", "retired",
        "fixed_window_retired", "fixed_window_cycles",
    }, f"{label}.totals")
    totals = {key: _counter(value, f"{label}.totals.{key}") for key, value in totals_row.items()}
    calculated = _sum_counts(runs)
    if totals != calculated:
        raise CampaignError(f"{label}.totals differ from exact per-run sum")
    if totals["generated"] != totals["source_overrun"] + totals["accepted"] or \
            totals["accepted"] != totals["retired"]:
        raise CampaignError(f"{label} violates hard-correct conservation")
    rate = _number(row["fixed_window_events_per_cycle"], f"{label}.fixed_window_events_per_cycle")
    if rate != _rate(totals["fixed_window_retired"], totals["fixed_window_cycles"]):
        raise CampaignError(f"{label} fixed-window throughput differs")
    occurrence = _validate_latency(
        row["occurrence_to_accept"], f"{label}.occurrence_to_accept", totals["accepted"]
    )
    internal = _validate_latency(
        row["accept_to_retire"], f"{label}.accept_to_retire", totals["accepted"]
    )
    return {
        "run_count": run_count, "totals": {**totals, "delivered": totals["retired"]},
        "fixed_window_events_per_cycle": row["fixed_window_events_per_cycle"],
        "occurrence_to_accept": occurrence, "accept_to_retire": internal,
    }


def _validate_owner(
    owner: str, value: Any, official: ModuleType, datasets: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    row = _exact_keys(value, {"full50", "capacity22", "reset"}, f"owners.{owner}")
    full = _exact_keys(row["full50"], {"execution_count", "aggregate", "runs"},
                       f"owners.{owner}.full50")
    execution_count = _counter(full["execution_count"], f"owners.{owner}.full50.execution_count")
    if execution_count != 50:
        raise CampaignError(f"owners.{owner}.full50 must contain exactly 50 executions")
    runs = full["runs"]
    if not isinstance(runs, dict) or list(runs) != sorted(official.FULL50):
        raise CampaignError(f"owners.{owner}.full50 serialized run membership/order differs")
    normalized_runs: dict[str, dict[str, Any]] = {}
    for name in official.FULL50:
        normalized_runs[name] = _validate_run_row(
            runs[name], f"owners.{owner}.full50.runs.{name}", owner=owner, trace=name,
            trace_sha=official.TRACE_SHA256[name], prepared_required=True,
            fixed_cycles=datasets["full50"]["_stim_cycles"][name], reset_test=0,
        )
    full_aggregate = _validate_aggregate(
        full["aggregate"], f"owners.{owner}.full50.aggregate",
        [normalized_runs[name] for name in official.FULL50],
    )
    capacity = _exact_keys(row["capacity22"], {
        "execution_count", "derived_from_full50_execution", "independent_additional_sample_count",
        "run_names", "run_trace_sha256", "aggregate",
    }, f"owners.{owner}.capacity22")
    if capacity["derived_from_full50_execution"] is not True:
        raise CampaignError(f"owners.{owner}.capacity22 is not a full50 subset view")
    if _counter(capacity["execution_count"], f"owners.{owner}.capacity22.execution_count") != 0 or \
            _counter(capacity["independent_additional_sample_count"],
                     f"owners.{owner}.capacity22.independent_additional_sample_count") != 0:
        raise CampaignError(f"owners.{owner}.capacity22 claims independent/additional samples")
    if capacity["run_names"] != list(official.CAPACITY22):
        raise CampaignError(f"owners.{owner}.capacity22 ordered membership differs")
    expected_hashes = {name: official.TRACE_SHA256[name] for name in official.CAPACITY22}
    if capacity["run_trace_sha256"] != expected_hashes:
        raise CampaignError(f"owners.{owner}.capacity22 trace hashes differ")
    capacity_runs = [normalized_runs[name] for name in official.CAPACITY22]
    for run in capacity_runs:
        expected_cycles = datasets["full50"]["_stim_cycles"][run["trace"]]
        if run["fixed_window_cycles"] != expected_cycles:
            raise CampaignError(f"owners.{owner}.capacity22 does not inherit full50 windows")
    capacity_aggregate = _validate_aggregate(
        capacity["aggregate"], f"owners.{owner}.capacity22.aggregate", capacity_runs
    )
    reset = _validate_run_row(
        row["reset"], f"owners.{owner}.reset", owner=owner, trace="basic_reset_drain",
        trace_sha=None, prepared_required=False, fixed_cycles=0, reset_test=1,
    )
    if (reset["generated"], reset["source_overrun"], reset["accepted"], reset["retired"]) != (8, 0, 8, 8):
        raise CampaignError(f"owners.{owner}.reset scenario counts differ")
    return {
        "full50": {
            "execution_count": execution_count, "run_names": list(official.FULL50),
            "runs": normalized_runs, "aggregate": full_aggregate,
        },
        "capacity22": {
            "execution_count": 0, "independent_additional_sample_count": 0,
            "derived_from_full50_execution": True, "subset_of": "full50",
            "run_names": list(official.CAPACITY22),
            "runs": {name: normalized_runs[name] for name in official.CAPACITY22},
            "aggregate": capacity_aggregate,
        },
        "reset": reset,
    }


def _git(root: Path, arguments: list[str], label: str) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise CampaignError(f"{label} failed: {process.stderr.decode(errors='replace').strip()}")
    return process


def _validate_pins(root: Path, provenance: dict[str, Any]) -> dict[str, Any]:
    if provenance["publication_model"] != EXPECTED_PUBLICATION:
        raise CampaignError("provenance publication_model differs")
    commit = _nonempty(provenance["package_commit"], "provenance.package_commit")
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise CampaignError("provenance.package_commit is not a full Git object ID")
    if provenance["pins_path"] != PINS_PATH:
        raise CampaignError("provenance.pins_path differs from immutable package path")
    pins_path = root / PINS_PATH
    if pins_path.is_symlink() or not pins_path.is_file():
        raise CampaignError("immutable pins file is absent or symlinked")
    pins_sha = _sha_string(provenance["pins_sha256"], "provenance.pins_sha256")
    if sha256(pins_path) != pins_sha:
        raise CampaignError("provenance pins hash differs from local pins bytes")
    pins = _load_json(pins_path, "actual-P6 pins")
    _exact_keys(pins, {"schema", "tool_version", "files", "tools"}, "actual-P6 pins")
    if pins["schema"] != "a23_full_p6_replay_pins_v1":
        raise CampaignError("actual-P6 pins schema differs")
    _nonempty(pins["tool_version"], "actual-P6 pins.tool_version")
    if provenance["verified_files"] != pins["files"] or provenance["verified_tools"] != pins["tools"]:
        raise CampaignError("provenance verified_files/verified_tools differ from pins.json")
    if not isinstance(pins["files"], dict) or not pins["files"] or \
            not isinstance(pins["tools"], dict) or not pins["tools"]:
        raise CampaignError("actual-P6 pins file/tool maps are empty or malformed")
    for relative, expected in pins["files"].items():
        _nonempty(relative, "actual-P6 pins.files path")
        _sha_string(expected, f"actual-P6 pins.files.{relative}")
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise CampaignError(f"local pinned file bytes differ: {relative}")
    for raw_path, expected in pins["tools"].items():
        _sha_string(expected, f"actual-P6 pins.tools.{raw_path}")
        path = Path(_nonempty(raw_path, "actual-P6 pins.tools path"))
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise CampaignError(f"local pinned tool bytes differ: {raw_path}")
    _git(root, ["cat-file", "-e", f"{commit}^{{commit}}"], "package commit verification")
    committed_pins = _git(root, ["show", f"{commit}:{PINS_PATH}"], "committed pins read").stdout
    if hashlib.sha256(committed_pins).hexdigest() != pins_sha:
        raise CampaignError("package commit does not contain the immutable pins bytes")
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", commit, "--", PINS_PATH, *pins["files"]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if diff.returncode != 0:
        raise CampaignError("local pinned package bytes differ from package commit")
    return {
        "package_commit": commit, "pins_path": PINS_PATH, "pins_sha256": pins_sha,
        "verified_file_count": len(pins["files"]), "verified_tool_count": len(pins["tools"]),
        "local_bytes": "HASH_VERIFIED", "package_commit_bytes": "HASH_VERIFIED",
    }


def _validate_mutations(value: Any, pins: dict[str, str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 15:
        raise CampaignError("mutations must contain exactly 15 results")
    expected_order = [(owner, mutation) for owner in EXPECTED_OWNERS for mutation in MUTATION_CONTRACT]
    normalized = []
    tx_path = "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv"
    for index, (row, expected_identity) in enumerate(zip(value, expected_order)):
        label = f"mutations[{index}]"
        _exact_keys(row, {
            "owner", "mutation", "actual_rtl", "killed", "first_required_diagnostic",
            "exit_code", "compile_define", "source_mutation",
        }, label)
        owner, mutation = expected_identity
        diagnostic, compile_define, has_source = MUTATION_CONTRACT[mutation]
        if row["owner"] != owner or row["mutation"] != mutation:
            raise CampaignError(f"{label} owner/mutation order differs")
        if row["actual_rtl"] is not True or row["killed"] is not True:
            raise CampaignError(f"{label} is not a killed actual-RTL mutation")
        if row["first_required_diagnostic"] != diagnostic or row["compile_define"] != compile_define:
            raise CampaignError(f"{label} diagnostic/compile define differs")
        if type(row["exit_code"]) is not int or row["exit_code"] != -6:
            raise CampaignError(f"{label}.exit_code differs or is boolean")
        source = row["source_mutation"]
        if has_source:
            source = _exact_keys(source, {
                "base_path", "base_sha256", "mutated_path", "mutated_sha256",
            }, f"{label}.source_mutation")
            if source["base_path"] != tx_path or source["base_sha256"] != pins[tx_path] or \
                    source["mutated_path"] != f"mutated-rtl/{mutation}/a7_p6_pair_tx.sv":
                raise CampaignError(f"{label}.source_mutation identity differs")
            mutated_sha = _sha_string(source["mutated_sha256"], f"{label}.mutated_sha256")
            if mutated_sha == source["base_sha256"]:
                raise CampaignError(f"{label} mutation did not change source bytes")
        elif source is not None:
            raise CampaignError(f"{label}.source_mutation must be null")
        normalized.append({
            "owner": owner, "mutation": mutation, "actual_rtl": True, "killed": True,
            "first_required_diagnostic": diagnostic,
        })
    return normalized


def _validate_result_envelope(
    root: Path, result: dict[str, Any], official: ModuleType,
    measurement: dict[str, Any], datasets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _exact_keys(result, {
        "schema", "status", "boundary", "ordered_link_adapter",
        "observation_wrapper_state_bits", "acceptance_observation",
        "retirement_scoreboard", "cycle_semantics", "generator",
        "execution_accounting", "owners", "mutations", "provenance", "qualification",
    }, "actual-P6 result")
    if result["schema"] != "a23_full_p6_replay_result_v1" or result["status"] != "PASS":
        raise CampaignError("actual-P6 result schema/status differs")
    if result["boundary"] != EXPECTED_BOUNDARY or result["cycle_semantics"] != measurement["cycle_semantics"]:
        raise CampaignError("actual-P6 boundary/measurement declaration differs")
    if result["ordered_link_adapter"] is not False or \
            type(result["observation_wrapper_state_bits"]) is not int or \
            result["observation_wrapper_state_bits"] != 0:
        raise CampaignError("actual-P6 wrapper/link-adapter boundary differs")
    if result["acceptance_observation"] != EXPECTED_ACCEPTANCE or \
            result["retirement_scoreboard"] != EXPECTED_RETIREMENT:
        raise CampaignError("actual-P6 acceptance/retirement declaration differs")
    generator = _exact_keys(result["generator"], {
        "version", "source_commit", "full50_manifest_sha256",
        "capacity22_manifest_sha256", "capacity22_is_full50_subset_view",
    }, "actual-P6 generator")
    if generator != {
        "version": official.GENERATOR_VERSION,
        "source_commit": official.SOURCE_COMMIT,
        "full50_manifest_sha256": official.SUITES["full50"]["manifest_sha256"],
        "capacity22_manifest_sha256": official.SUITES["capacity22"]["manifest_sha256"],
        "capacity22_is_full50_subset_view": True,
    }:
        raise CampaignError("actual-P6 generator/frozen-manifest provenance differs")
    accounting = _exact_keys(result["execution_accounting"], {
        "owners", "full50_actual_executions", "capacity22_subset_references",
        "capacity22_additional_executions", "reset_actual_executions",
        "mutation_actual_RTL_executions",
    }, "actual-P6 execution_accounting")
    expected_counts = {
        "owners": 3, "full50_actual_executions": 150,
        "capacity22_subset_references": 66, "capacity22_additional_executions": 0,
        "reset_actual_executions": 3, "mutation_actual_RTL_executions": 15,
    }
    for key, expected in expected_counts.items():
        if _counter(accounting[key], f"execution_accounting.{key}") != expected:
            raise CampaignError(f"execution_accounting.{key} differs")
    qualification = _exact_keys(result["qualification"], {"digital_RTL", "physical", "CDC_RDC"},
                                "actual-P6 qualification")
    if qualification != {"digital_RTL": "GO", "physical": "HOLD", "CDC_RDC": "HOLD"}:
        raise CampaignError("actual-P6 qualification boundary differs")
    provenance = _exact_keys(result["provenance"], {
        "publication_model", "package_commit", "pins_path", "pins_sha256",
        "verified_files", "verified_tools",
    }, "actual-P6 provenance")
    pin_summary = _validate_pins(root, provenance)
    pins = provenance["verified_files"]
    mutations = _validate_mutations(result["mutations"], pins)
    owners = result["owners"]
    if not isinstance(owners, dict) or list(owners) != EXPECTED_OWNERS:
        raise CampaignError("actual-P6 owners membership/order differs")
    normalized_owners = {
        owner: _validate_owner(owner, owners[owner], official, datasets) for owner in EXPECTED_OWNERS
    }
    reference = normalized_owners["a2"]["full50"]
    for owner in ("a3", "a4"):
        current = normalized_owners[owner]["full50"]
        for name in official.FULL50:
            for key in ("trace_sha256", "prepared_trace_sha256", "generated", "fixed_window_cycles"):
                if current["runs"][name][key] != reference["runs"][name][key]:
                    raise CampaignError(f"actual-P6 owners disagree on {name} {key}")
    summary = {
        "status": "RECEIPT_CONSISTENT", "schema": result["schema"],
        "boundary": result["boundary"], "pins": pin_summary,
        "qualification": qualification, "execution_accounting": expected_counts,
        "mutation_count": len(mutations), "mutation_status": "15_KILLED_ACTUAL_RTL",
        "acceptance_observation": EXPECTED_ACCEPTANCE,
        "retirement_scoreboard": EXPECTED_RETIREMENT,
    }
    return normalized_owners, summary


def _validate_provider(
    root: Path, row: dict[str, Any], official: ModuleType,
    measurement: dict[str, Any], datasets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider_id = _nonempty(row.get("id"), "providers.id")
    _exact_keys(row, {
        "id", "kind", "runner", "entrypoint", "result", "candidate_owner_map",
        "dry_run_command",
    }, f"providers.{provider_id}")
    if row["kind"] != "a23_full_p6_replay_result_v1":
        raise CampaignError(f"providers.{provider_id} has unsupported kind")
    _, runner_identity = _file_ref(root, row["runner"], f"providers.{provider_id}.runner")
    _, entry_identity = _file_ref(root, row["entrypoint"], f"providers.{provider_id}.entrypoint")
    result_path, result_identity = _file_ref(root, row["result"], f"providers.{provider_id}.result")
    if row["candidate_owner_map"] != {"a2_p6": "a2", "a3_p6": "a3"}:
        raise CampaignError(f"providers.{provider_id} must expose only actual A2/A3 P6 owners")
    command = row["dry_run_command"]
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command) or \
            entry_identity["path"] not in command:
        raise CampaignError(f"providers.{provider_id} dry-run does not reuse its pinned entrypoint")
    result = _load_json(result_path, f"providers.{provider_id}.result")
    owners, envelope = _validate_result_envelope(root, result, official, measurement, datasets)
    identity = {
        "kind": row["kind"], "runner": runner_identity, "entrypoint": entry_identity,
        "result": result_identity, "envelope": envelope,
    }
    plan = {
        "provider": provider_id, "runner": runner_identity, "entrypoint": entry_identity,
        "command": command, "executed": False,
    }
    return owners, {"identity": identity, "plan": plan}


def _csv_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise CampaignError(f"{label} is not an integer") from error
    if str(parsed) != value:
        raise CampaignError(f"{label} is not canonical decimal")
    return parsed


def _latency_summary(values: Iterable[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {
        "count": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(0.50), "p95": percentile(0.95),
        "p99": percentile(0.99), "max": ordered[-1],
    }


def _recompute_artifact_case(
    work_root: Path, owner: str, trace: str, expected: dict[str, Any]
) -> dict[str, Any]:
    case = work_root / "artifacts" / owner / "none" / trace
    summary_path = case / "summary.csv"
    events_path = case / "events.csv"
    for path, expected_sha, label in (
        (summary_path, expected["summary_sha256"], "summary"),
        (events_path, expected["events_sha256"], "events"),
    ):
        if path.is_symlink() or not path.is_file() or sha256(path) != expected_sha:
            raise CampaignError(f"artifact {owner}/{trace} {label} bytes/hash differ")
    with summary_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if set(reader.fieldnames or ()) != SUMMARY_FIELDS:
            raise CampaignError(f"artifact {owner}/{trace} summary schema differs")
        summary_rows = list(reader)
    if len(summary_rows) != 1:
        raise CampaignError(f"artifact {owner}/{trace} summary row count differs")
    summary = summary_rows[0]
    if summary["owner"] != owner or summary["trace"] != trace:
        raise CampaignError(f"artifact {owner}/{trace} summary identity differs")
    numeric = {
        key: _csv_int(summary[key], f"artifact {owner}/{trace}.{key}")
        for key in SUMMARY_FIELDS - {"owner", "trace"}
    }
    with events_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if set(reader.fieldnames or ()) != EVENT_FIELDS:
            raise CampaignError(f"artifact {owner}/{trace} event schema differs")
        events = list(reader)
    occurrence_latency: list[int] = []
    internal_latency: list[int] = []
    retired = 0
    overrun = 0
    for expected_id, event in enumerate(events):
        if event["owner"] != owner or event["trace"] != trace or \
                _csv_int(event["tb_only_event_id"], "event id") != expected_id:
            raise CampaignError(f"artifact {owner}/{trace} event identity/order differs")
        source = _csv_int(event["logical_source"], "logical_source")
        occurrence = _csv_int(event["occurrence_cycle"], "occurrence_cycle")
        deadline = _csv_int(event["deadline_cycle"], "deadline_cycle")
        if not 0 <= source < 16 or occurrence < 0 or deadline < occurrence:
            raise CampaignError(f"artifact {owner}/{trace} event provenance differs")
        if event["event_state"] == "retired":
            accept = _csv_int(event["accept_cycle"], "accept_cycle")
            retire = _csv_int(event["retire_cycle"], "retire_cycle")
            if not occurrence <= accept <= retire:
                raise CampaignError(f"artifact {owner}/{trace} latency is inverted")
            occurrence_latency.append(accept - occurrence)
            internal_latency.append(retire - accept)
            retired += 1
        elif event["event_state"] == "source_overrun":
            if event["accept_cycle"] != "-1" or event["retire_cycle"] != "-1":
                raise CampaignError(f"artifact {owner}/{trace} overrun carries timing")
            overrun += 1
        else:
            raise CampaignError(f"artifact {owner}/{trace} has nonterminal event state")
    computed = {
        "generated": len(events), "source_overrun": overrun,
        "accepted": retired, "retired": retired,
        "fixed_window_retired": numeric["fixed_window_retired"],
        "fixed_window_cycles": numeric["fixed_window_cycles"],
        "observation_cycles": numeric["observation_cycles"],
        "reset_test": numeric["reset_test"],
        "fixed_window_events_per_cycle": _rate(
            numeric["fixed_window_retired"], numeric["fixed_window_cycles"]
        ),
        "occurrence_to_accept": _latency_summary(occurrence_latency),
        "accept_to_retire": _latency_summary(internal_latency),
        "summary_sha256": sha256(summary_path), "events_sha256": sha256(events_path),
        "_occurrence_values": occurrence_latency, "_internal_values": internal_latency,
    }
    for key in ("generated", "source_overrun", "accepted", "retired"):
        if numeric[key] != computed[key]:
            raise CampaignError(f"artifact {owner}/{trace} summary/event {key} differs")
    for key, value in computed.items():
        if key.startswith("_"):
            continue
        if expected[key] != value:
            raise CampaignError(f"artifact {owner}/{trace} recomputed {key} differs from receipt")
    return computed


def _verify_run_root(work_root: Path, owners: dict[str, Any]) -> dict[str, Any]:
    root = work_root.resolve()
    if root.is_symlink() or not root.is_dir() or not (root / "artifacts").is_dir():
        raise CampaignError("verify-run-root must be the actual replay work directory containing artifacts/")
    cases = 0
    for owner in ("a2", "a3"):
        recomputed: dict[str, dict[str, Any]] = {}
        for name in owners[owner]["full50"]["run_names"]:
            recomputed[name] = _recompute_artifact_case(
                root, owner, name, owners[owner]["full50"]["runs"][name]
            )
            cases += 1
        _recompute_artifact_case(root, owner, "basic_reset_drain", owners[owner]["reset"])
        cases += 1
        for suite, names in (
            ("full50", owners[owner]["full50"]["run_names"]),
            ("capacity22", owners[owner]["capacity22"]["run_names"]),
        ):
            occurrence = [value for name in names for value in recomputed[name]["_occurrence_values"]]
            internal = [value for name in names for value in recomputed[name]["_internal_values"]]
            aggregate = owners[owner][suite]["aggregate"]
            if aggregate["occurrence_to_accept"] != _latency_summary(occurrence) or \
                    aggregate["accept_to_retire"] != _latency_summary(internal):
                raise CampaignError(f"artifact {owner}/{suite} aggregate latency differs")
    return {
        "status": "ARTIFACT_RECOMPUTED", "independent_event_replay": True,
        "work_root": str(root), "owners": ["a2", "a3"], "case_count": cases,
        "event_and_summary_hashes": "MATCH_RECEIPT",
        "counts_conservation_and_latencies": "RECOMPUTED_FROM_EVENT_CSV",
    }


def _validate_aggregation(rows: Any, dataset_ids: set[str]) -> None:
    groups = _named(rows, "aggregation_groups")
    for name, row in groups.items():
        _exact_keys(row, {"id", "datasets"}, f"aggregation_groups.{name}")
        selected = row["datasets"]
        if not isinstance(selected, list) or not selected or any(item not in dataset_ids for item in selected):
            raise CampaignError(f"aggregation_groups.{name} names an invalid dataset")
        if len(selected) != len(set(selected)):
            raise CampaignError(f"aggregation_groups.{name} repeats a dataset")
        if {"full50", "capacity22"}.issubset(selected):
            raise CampaignError("refuse to pool capacity22 with full50")


def validate_campaign(
    manifest: dict[str, Any], root: Path = PROJECT, mode: str = "validate",
    verify_run_root: Path | None = None,
) -> dict[str, Any]:
    """Validate configured evidence and return an honest deterministic HOLD receipt."""
    if mode not in {"validate", "dry-run"}:
        raise CampaignError("mode must be validate or dry-run")
    _exact_keys(manifest, {
        "schema", "campaign_id", "official_registry", "measurement_definitions",
        "datasets", "providers", "candidates", "comparisons", "aggregation_groups",
    }, "campaign")
    if manifest["schema"] != "redred_canonical_campaign_manifest_v2":
        raise CampaignError("campaign schema mismatch")
    campaign_id = _nonempty(manifest["campaign_id"], "campaign_id")
    official, official_identity = _load_official(root, manifest["official_registry"])
    measurements = _validate_measurements(manifest["measurement_definitions"])
    if len(measurements) != 1:
        raise CampaignError("canonical REDRED campaign requires exactly one common measurement definition")
    measurement_id, measurement = next(iter(measurements.items()))
    datasets, dataset_summary, holds = _validate_dataset_provenance(root, manifest["datasets"], official)
    _validate_aggregation(manifest["aggregation_groups"], set(datasets))

    provider_rows = _named(manifest["providers"], "providers")
    if list(provider_rows) != ["actual_a23_p6"]:
        raise CampaignError("provider IDs/order differ from the canonical actual-P6 provider")
    provider_owners: dict[str, dict[str, Any]] = {}
    provider_summary: dict[str, Any] = {}
    plans = []
    for provider_id, row in provider_rows.items():
        owners, summary = _validate_provider(root, row, official, measurement, datasets)
        provider_owners[provider_id] = owners
        provider_summary[provider_id] = summary["identity"]
        plans.append(summary["plan"])

    artifact_summary = {
        "status": "NOT_PROVIDED", "independent_event_replay": False,
        "work_root": None, "case_count": 0,
        "statement": "receipt hashes and summaries were checked; event/summary CSVs were not independently replayed",
    }
    event_evidence_trust = "NOT_REPLAYED"
    if verify_run_root is not None:
        artifact_summary = _verify_run_root(verify_run_root, provider_owners["actual_a23_p6"])
        event_evidence_trust = "ARTIFACT_RECOMPUTED"
    else:
        holds.append("actual-P6 event artifacts not provided: no independent event replay")

    candidates = _named(manifest["candidates"], "candidates")
    if list(candidates) != EXPECTED_CANDIDATES:
        raise CampaignError(f"candidate order must be {EXPECTED_CANDIDATES}")
    candidate_summary: dict[str, Any] = {}
    evidence_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate_id, row in candidates.items():
        _exact_keys(row, {"id", "required_for_release", "measurement_definition", "evidence"},
                    f"candidates.{candidate_id}")
        if row["measurement_definition"] != measurement_id:
            raise CampaignError(f"candidates.{candidate_id} measurement definition differs")
        if type(row["required_for_release"]) is not bool:
            raise CampaignError(f"candidates.{candidate_id}.required_for_release must be boolean")
        evidence = row["evidence"]
        if not isinstance(evidence, dict) or list(evidence) != list(EXPECTED_DATASET_CLASSES):
            raise CampaignError(f"candidates.{candidate_id}.evidence dataset IDs/order differ")
        candidate_summary[candidate_id] = {
            "measurement_definition": measurement_id,
            "measurement_definition_sha256": object_sha256(measurement), "datasets": {},
        }
        for dataset_id, pointer in evidence.items():
            if pointer is None:
                candidate_summary[candidate_id]["datasets"][dataset_id] = {"status": "MISSING"}
                if row["required_for_release"] and datasets[dataset_id]["required_for_release"]:
                    holds.append(f"candidate {candidate_id}/{dataset_id}: missing canonical evidence")
                continue
            pointer = _exact_keys(pointer, {"provider", "owner"},
                                  f"candidates.{candidate_id}.evidence.{dataset_id}")
            provider_id = _nonempty(pointer["provider"], "evidence.provider")
            owner = _nonempty(pointer["owner"], "evidence.owner")
            if provider_id not in provider_rows or \
                    provider_rows[provider_id]["candidate_owner_map"].get(candidate_id) != owner:
                raise CampaignError(f"candidate {candidate_id}/{dataset_id} provider-owner mapping differs")
            if datasets[dataset_id]["source_class"] != "synthetic":
                raise CampaignError(f"actual-P6 synthetic provider cannot be used for {dataset_id}")
            normalized = provider_owners[provider_id][owner][dataset_id]
            evidence_cache[(candidate_id, dataset_id)] = normalized
            candidate_summary[candidate_id]["datasets"][dataset_id] = {
                "status": "RECEIPT_CONSISTENT", "provider": provider_id, "owner": owner,
                "run_names": normalized["run_names"],
                "trace_count": len(normalized["run_names"]),
                "execution_count": normalized["execution_count"],
                "subset_view": dataset_id == "capacity22",
                "totals": normalized["aggregate"]["totals"],
                "per_run_evidence": {
                    name: {
                        key: normalized["runs"][name][key] for key in (
                            "trace_sha256", "prepared_trace_sha256", "summary_sha256",
                            "events_sha256", "occurrence_to_accept", "accept_to_retire",
                            "fixed_window_cycles",
                        )
                    } for name in normalized["run_names"]
                },
                "event_evidence_status": event_evidence_trust,
                "independent_event_replay": artifact_summary["independent_event_replay"],
            }

    comparisons = _named(manifest["comparisons"], "comparisons")
    comparison_summary: dict[str, Any] = {}
    for comparison_id, row in comparisons.items():
        _exact_keys(row, {"id", "datasets", "candidates", "measurement_definition"},
                    f"comparisons.{comparison_id}")
        selected_datasets = row["datasets"]
        if not isinstance(selected_datasets, list) or len(selected_datasets) != 1 or \
                selected_datasets[0] not in datasets:
            raise CampaignError(f"comparisons.{comparison_id} must select exactly one dataset")
        if row["candidates"] != EXPECTED_CANDIDATES or row["measurement_definition"] != measurement_id:
            raise CampaignError(f"comparisons.{comparison_id} candidate order/measurement differs")
        dataset_id = selected_datasets[0]
        available = [name for name in EXPECTED_CANDIDATES if (name, dataset_id) in evidence_cache]
        missing = [name for name in EXPECTED_CANDIDATES if name not in available]
        if missing:
            holds.append(f"comparison {comparison_id}: missing candidates {','.join(missing)}")
        if len(available) >= 2:
            reference = evidence_cache[(available[0], dataset_id)]
            for candidate_id in available[1:]:
                current = evidence_cache[(candidate_id, dataset_id)]
                if current["run_names"] != reference["run_names"]:
                    raise CampaignError(f"comparisons.{comparison_id} run names differ")
                for name in reference["run_names"]:
                    for key in ("trace_sha256", "prepared_trace_sha256", "generated", "fixed_window_cycles"):
                        if current["runs"][name][key] != reference["runs"][name][key]:
                            raise CampaignError(f"comparisons.{comparison_id} {name} {key} differs")
        comparison_summary[comparison_id] = {
            "status": "HOLD" if missing else "RECEIPT_CONSISTENT",
            "dataset": dataset_id, "source_class": datasets[dataset_id]["source_class"],
            "measurement_definition": measurement_id,
            "measurement_definition_sha256": object_sha256(measurement),
            "consistent_candidates": available, "missing_candidates": missing,
            "trace_identity_cross_check": "RECEIPT_CONSISTENT" if len(available) >= 2 else "NOT_ENOUGH_EVIDENCE",
            "event_evidence_status": event_evidence_trust,
            "independent_event_replay": artifact_summary["independent_event_replay"],
        }

    holds = sorted(set(holds))
    return {
        "schema": "redred_canonical_campaign_receipt_v2", "status": "HOLD",
        "trust_level": {
            "receipt_envelope": "RECEIPT_CONSISTENT",
            "event_evidence": event_evidence_trust,
            "release": "HOLD",
        },
        "campaign_id": campaign_id, "mode": mode,
        "commands_executed": False, "official_registry": official_identity,
        "measurement_definitions": {
            name: {"sha256": object_sha256(row)} for name, row in measurements.items()
        },
        "datasets": dataset_summary, "providers": provider_summary,
        "event_artifact_verification": artifact_summary,
        "candidates": candidate_summary, "comparisons": comparison_summary,
        "aggregation_policy": {
            "capacity22_is_full50_subset_view": True,
            "capacity22_full50_pooling": "FORBIDDEN",
            "groups": manifest["aggregation_groups"],
        },
        "execution_plan": plans if mode == "dry-run" else [], "hold_reasons": holds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "dry-run"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--verify-run-root", type=Path,
                        help="actual replay work directory containing artifacts/")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-hold", action="store_true",
                        help="return zero for a valid HOLD receipt; status remains HOLD")
    args = parser.parse_args()
    try:
        manifest_path = args.manifest.resolve()
        manifest = _load_json(manifest_path, "campaign manifest")
        receipt = validate_campaign(
            manifest, args.repo_root.resolve(), args.mode, args.verify_run_root
        )
        receipt["manifest"] = {"path": str(manifest_path), "sha256": sha256(manifest_path)}
        payload = canonical(receipt)
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
        sys.stdout.buffer.write(payload)
        return 0 if args.allow_hold else 3
    except (CampaignError, OSError) as error:
        print(f"REDRED_CAMPAIGN_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
