#!/usr/bin/env python3
"""Fail-closed validator and dry-run planner for the canonical REDRED campaign."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("campaign.json")
SHA256_LEN = 64


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
        stim_cycles = row.get("stim_cycles")
        if name in cycles or not isinstance(stim_cycles, int) or stim_cycles <= 0:
            raise CampaignError(f"{label} has duplicate name or invalid stim_cycles: {name}")
        names.append(name)
        cycles[name] = stim_cycles
    return names, cycles


def _validate_dataset_provenance(
    root: Path, rows: Any, official: ModuleType
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    datasets = _named(rows, "datasets")
    expected = {
        "id", "source_class", "required_for_release", "suite_key", "manifest",
        "provenance_manifest", "subset_of",
    }
    allowed_classes = {"synthetic", "supplied", "public"}
    summary: dict[str, Any] = {}
    holds: list[str] = []
    for name, row in datasets.items():
        _exact_keys(row, expected, f"datasets.{name}")
        source_class = row["source_class"]
        if source_class not in allowed_classes:
            raise CampaignError(f"datasets.{name}.source_class is not synthetic/supplied/public")
        if not isinstance(row["required_for_release"], bool):
            raise CampaignError(f"datasets.{name}.required_for_release must be boolean")
        if source_class == "synthetic":
            suite_key = _nonempty(row["suite_key"], f"datasets.{name}.suite_key")
            if suite_key not in official.SUITES:
                raise CampaignError(f"datasets.{name} names unknown frozen suite {suite_key}")
            path, identity = _file_ref(root, row["manifest"], f"datasets.{name}.manifest")
            frozen = official.SUITES[suite_key]
            if identity["sha256"] != frozen["manifest_sha256"] or path.name != frozen["manifest_name"]:
                raise CampaignError(f"datasets.{name} differs from the official frozen manifest")
            names, cycles = _manifest_runs(path, f"datasets.{name}.manifest")
            if tuple(names) != tuple(frozen["names"]):
                raise CampaignError(f"datasets.{name} ordered run names differ from official registry")
            if row["provenance_manifest"] is not None:
                raise CampaignError(f"datasets.{name} synthetic provenance must come from official_registry")
            summary[name] = {
                "status": "VALIDATED", "source_class": source_class,
                "manifest": identity, "run_count": len(names),
                "trace_hash_source": "official_registry",
            }
            row["_run_names"] = names
            row["_stim_cycles"] = cycles
        else:
            if row["suite_key"] is not None or row["manifest"] is not None:
                raise CampaignError(f"datasets.{name} {source_class} data cannot masquerade as a synthetic suite")
            provenance_ref = row["provenance_manifest"]
            if provenance_ref is None:
                summary[name] = {
                    "status": "MISSING", "source_class": source_class,
                    "provenance_manifest": None,
                }
                if row["required_for_release"]:
                    holds.append(f"dataset {name}: missing {source_class} provenance and trace evidence")
                continue
            path, identity = _file_ref(root, provenance_ref, f"datasets.{name}.provenance_manifest")
            provenance = _load_json(path, f"datasets.{name}.provenance_manifest")
            common = {
                "schema", "dataset_id", "source_class", "license", "archive_sha256",
                "adapter_sha256", "trace_manifest_sha256",
            }
            specific = {"provider", "delivery_id"} if source_class == "supplied" else {
                "source_url", "dataset_version", "retrieved_at"
            }
            _exact_keys(provenance, common | specific, f"datasets.{name}.provenance")
            if provenance["schema"] != "redred_dataset_provenance_v1" or \
                    provenance["dataset_id"] != name or provenance["source_class"] != source_class:
                raise CampaignError(f"datasets.{name} provenance identity mismatch")
            for key in (common | specific) - {"schema", "dataset_id", "source_class"}:
                _nonempty(provenance[key], f"datasets.{name}.provenance.{key}")
            for key in ("archive_sha256", "adapter_sha256", "trace_manifest_sha256"):
                _sha_string(provenance[key], f"datasets.{name}.provenance.{key}")
            summary[name] = {
                "status": "PROVENANCE_ONLY", "source_class": source_class,
                "provenance_manifest": identity,
            }
            if row["required_for_release"]:
                holds.append(f"dataset {name}: provenance exists but no canonical candidate trace results are configured")

    if "full50" not in datasets or "capacity22" not in datasets:
        raise CampaignError("datasets must include full50 and capacity22")
    capacity = datasets["capacity22"]
    if capacity["source_class"] != "synthetic" or capacity["subset_of"] != "full50":
        raise CampaignError("capacity22 must be a synthetic subset_of full50")
    if set(capacity["_run_names"]) - set(datasets["full50"]["_run_names"]):
        raise CampaignError("capacity22 contains a non-full50 run")
    if len(capacity["_run_names"]) != len(set(capacity["_run_names"])):
        raise CampaignError("capacity22 contains duplicate subset members")
    return datasets, summary, holds


def _validate_provider(
    root: Path, row: dict[str, Any], official: ModuleType,
    measurement: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = {
        "id", "kind", "runner", "entrypoint", "result", "candidate_owner_map",
        "dry_run_command",
    }
    provider_id = _nonempty(row.get("id"), "providers.id")
    _exact_keys(row, expected, f"providers.{provider_id}")
    if row["kind"] != "a23_full_p6_replay_result_v1":
        raise CampaignError(f"providers.{provider_id} has unsupported kind")
    _, runner_identity = _file_ref(root, row["runner"], f"providers.{provider_id}.runner")
    _, entry_identity = _file_ref(root, row["entrypoint"], f"providers.{provider_id}.entrypoint")
    result_path, result_identity = _file_ref(root, row["result"], f"providers.{provider_id}.result")
    owner_map = row["candidate_owner_map"]
    if owner_map != {"a2_p6": "a2", "a3_p6": "a3"}:
        raise CampaignError(f"providers.{provider_id} must expose only actual A2/A3 P6 owners")
    command = row["dry_run_command"]
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        raise CampaignError(f"providers.{provider_id}.dry_run_command must be a string list")
    if runner_identity["path"] == entry_identity["path"] or entry_identity["path"] not in command:
        raise CampaignError(f"providers.{provider_id} dry-run does not reuse its pinned entrypoint")
    result = _load_json(result_path, f"providers.{provider_id}.result")
    if result.get("schema") != "a23_full_p6_replay_result_v1" or result.get("status") != "PASS":
        raise CampaignError(f"providers.{provider_id} result is not a passing actual-P6 receipt")
    if result.get("boundary") != "actual_scheduler_plus_actual_phase_related_always_ready_P6":
        raise CampaignError(f"providers.{provider_id} result boundary is not actual P6")
    if result.get("cycle_semantics") != measurement["cycle_semantics"]:
        raise CampaignError(f"providers.{provider_id} measurement definition differs")
    generator = result.get("generator", {})
    if generator != {
        "version": official.GENERATOR_VERSION,
        "source_commit": official.SOURCE_COMMIT,
        "full50_manifest_sha256": official.SUITES["full50"]["manifest_sha256"],
        "capacity22_manifest_sha256": official.SUITES["capacity22"]["manifest_sha256"],
        "capacity22_is_full50_subset_view": True,
    }:
        raise CampaignError(f"providers.{provider_id} generator/frozen-manifest provenance differs")
    if not isinstance(result.get("owners"), dict):
        raise CampaignError(f"providers.{provider_id} lacks owner results")
    plan = {
        "provider": provider_id, "runner": runner_identity,
        "entrypoint": entry_identity, "command": command,
        "executed": False,
    }
    identity = {
        "kind": row["kind"], "runner": runner_identity, "entrypoint": entry_identity,
        "result": result_identity,
    }
    return result, {"identity": identity, "plan": plan}


def _integer(row: dict[str, Any], key: str, label: str) -> int:
    value = row.get(key)
    if not isinstance(value, int) or value < 0:
        raise CampaignError(f"{label}.{key} must be a nonnegative integer")
    return value


def _assert_conservation(row: dict[str, Any], label: str) -> dict[str, int]:
    generated = _integer(row, "generated", label)
    overrun = _integer(row, "source_overrun", label)
    accepted = _integer(row, "accepted", label)
    delivered = _integer(row, "retired", label)
    fixed_events = _integer(row, "fixed_window_retired", label)
    fixed_cycles = _integer(row, "fixed_window_cycles", label)
    if generated != overrun + accepted:
        raise CampaignError(f"{label} violates generated=source_overrun+accepted")
    if accepted != delivered:
        raise CampaignError(f"{label} violates accepted=delivered")
    if fixed_events > delivered or fixed_cycles == 0:
        raise CampaignError(f"{label} has invalid fixed measurement window")
    return {
        "generated": generated, "source_overrun": overrun, "accepted": accepted,
        "delivered": delivered, "fixed_window_retired": fixed_events,
        "fixed_window_cycles": fixed_cycles,
    }


def _sum_runs(runs: list[dict[str, int]]) -> dict[str, int]:
    keys = (
        "generated", "source_overrun", "accepted", "delivered",
        "fixed_window_retired", "fixed_window_cycles",
    )
    return {key: sum(row[key] for row in runs) for key in keys}


def _validate_actual_candidate(
    result: dict[str, Any], owner: str, dataset_id: str,
    dataset: dict[str, Any], official: ModuleType,
) -> dict[str, Any]:
    if owner not in {"a2", "a3"} or owner not in result["owners"]:
        raise CampaignError(f"actual-P6 result lacks permitted owner {owner}")
    owner_result = result["owners"][owner]
    full = owner_result.get("full50", {})
    runs = full.get("runs")
    if not isinstance(runs, dict):
        raise CampaignError(f"actual-P6 {owner} lacks full50 per-run evidence")
    expected_full = list(official.FULL50)
    if len(runs) != len(expected_full) or set(runs) != set(expected_full):
        raise CampaignError(f"actual-P6 {owner} full50 run membership differs")
    normalized: dict[str, dict[str, Any]] = {}
    for name in expected_full:
        row = runs[name]
        if not isinstance(row, dict) or row.get("trace_sha256") != official.TRACE_SHA256[name]:
            raise CampaignError(f"actual-P6 {owner}/{name} trace SHA differs")
        _sha_string(row.get("prepared_trace_sha256"),
                    f"actual-P6 {owner}/{name}.prepared_trace_sha256")
        counts = _assert_conservation(row, f"actual-P6 {owner}/{name}")
        if counts["fixed_window_cycles"] != dataset["_stim_cycles"].get(name, counts["fixed_window_cycles"]):
            if dataset_id == "full50":
                raise CampaignError(f"actual-P6 {owner}/{name} measurement window differs from manifest")
        normalized[name] = {
            "trace_sha256": row["trace_sha256"],
            "prepared_trace_sha256": row["prepared_trace_sha256"],
            **counts,
        }
    full_totals = _assert_conservation(full.get("aggregate", {}).get("totals", {}),
                                       f"actual-P6 {owner}/full50 aggregate")
    calculated_full = _sum_runs([normalized[name] for name in expected_full])
    for key in full_totals:
        if full_totals[key] != calculated_full[key]:
            raise CampaignError(f"actual-P6 {owner}/full50 aggregate differs from per-run {key}")
    if dataset_id == "full50":
        if full.get("execution_count") != len(expected_full):
            raise CampaignError(f"actual-P6 {owner}/full50 execution count differs")
        return {
            "run_names": expected_full,
            "runs": normalized,
            "totals": full_totals,
            "execution_count": full["execution_count"],
            "subset_view": False,
        }
    if dataset_id != "capacity22":
        raise CampaignError(f"actual-P6 provider cannot supply non-synthetic dataset {dataset_id}")
    capacity = owner_result.get("capacity22", {})
    expected_capacity = list(official.CAPACITY22)
    if capacity.get("run_names") != expected_capacity or \
            capacity.get("run_trace_sha256") != {
                name: official.TRACE_SHA256[name] for name in expected_capacity
            }:
        raise CampaignError(f"actual-P6 {owner}/capacity22 is not the exact frozen subset")
    if capacity.get("derived_from_full50_execution") is not True or \
            capacity.get("execution_count") != 0 or \
            capacity.get("independent_additional_sample_count") != 0:
        raise CampaignError(f"actual-P6 {owner}/capacity22 claims independent or additional execution")
    capacity_totals = _assert_conservation(capacity.get("aggregate", {}).get("totals", {}),
                                           f"actual-P6 {owner}/capacity22 aggregate")
    calculated_capacity = _sum_runs([normalized[name] for name in expected_capacity])
    for key in capacity_totals:
        if capacity_totals[key] != calculated_capacity[key]:
            raise CampaignError(f"actual-P6 {owner}/capacity22 aggregate differs from full50 subset {key}")
    selected = {name: normalized[name] for name in expected_capacity}
    return {
        "run_names": expected_capacity, "runs": selected,
        "totals": capacity_totals, "execution_count": 0,
        "subset_view": True, "subset_of": "full50",
        "independent_additional_sample_count": 0,
    }


def _validate_aggregation(rows: Any, dataset_ids: set[str]) -> list[dict[str, Any]]:
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
    return list(groups.values())


def validate_campaign(
    manifest: dict[str, Any], root: Path = PROJECT, mode: str = "validate"
) -> dict[str, Any]:
    """Validate all configured evidence and return a deterministic PASS/HOLD receipt."""
    if mode not in {"validate", "dry-run"}:
        raise CampaignError("mode must be validate or dry-run")
    expected_top = {
        "schema", "campaign_id", "official_registry", "measurement_definitions",
        "datasets", "providers", "candidates", "comparisons", "aggregation_groups",
    }
    _exact_keys(manifest, expected_top, "campaign")
    if manifest["schema"] != "redred_canonical_campaign_manifest_v1":
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
    provider_results: dict[str, dict[str, Any]] = {}
    provider_summary: dict[str, Any] = {}
    plans: list[dict[str, Any]] = []
    for provider_id, row in provider_rows.items():
        result, summary = _validate_provider(root, row, official, measurement)
        provider_results[provider_id] = result
        provider_summary[provider_id] = summary["identity"]
        plans.append(summary["plan"])

    candidates = _named(manifest["candidates"], "candidates")
    expected_candidates = ["fovea", "cluster2", "a2_p6", "a3_p6"]
    if list(candidates) != expected_candidates:
        raise CampaignError(f"candidate order must be {expected_candidates}")
    candidate_summary: dict[str, Any] = {}
    evidence_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate_id, row in candidates.items():
        _exact_keys(row, {"id", "required_for_release", "measurement_definition", "evidence"},
                    f"candidates.{candidate_id}")
        if row["measurement_definition"] != measurement_id:
            raise CampaignError(f"candidates.{candidate_id} measurement definition differs")
        if not isinstance(row["required_for_release"], bool):
            raise CampaignError(f"candidates.{candidate_id}.required_for_release must be boolean")
        evidence = row["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != set(datasets):
            raise CampaignError(f"candidates.{candidate_id}.evidence must name every dataset exactly once")
        candidate_summary[candidate_id] = {
            "measurement_definition": measurement_id,
            "measurement_definition_sha256": object_sha256(measurement),
            "datasets": {},
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
            cache_key = (candidate_id, dataset_id)
            normalized = _validate_actual_candidate(
                provider_results[provider_id], owner, dataset_id, datasets[dataset_id], official
            )
            evidence_cache[cache_key] = normalized
            candidate_summary[candidate_id]["datasets"][dataset_id] = {
                "status": "VALIDATED", "provider": provider_id, "owner": owner,
                "trace_count": len(normalized["run_names"]),
                "execution_count": normalized["execution_count"],
                "subset_view": normalized["subset_view"],
                "totals": normalized["totals"],
            }

    comparisons = _named(manifest["comparisons"], "comparisons")
    comparison_summary: dict[str, Any] = {}
    for comparison_id, row in comparisons.items():
        _exact_keys(row, {"id", "datasets", "candidates", "measurement_definition"},
                    f"comparisons.{comparison_id}")
        selected_datasets = row["datasets"]
        selected_candidates = row["candidates"]
        if not isinstance(selected_datasets, list) or len(selected_datasets) != 1 or \
                selected_datasets[0] not in datasets:
            raise CampaignError(f"comparisons.{comparison_id} must select exactly one dataset")
        if {"full50", "capacity22"}.issubset(selected_datasets):
            raise CampaignError("refuse to pool capacity22 with full50")
        if selected_candidates != expected_candidates:
            raise CampaignError(f"comparisons.{comparison_id} must keep canonical candidate order")
        if row["measurement_definition"] != measurement_id or any(
            candidates[name]["measurement_definition"] != measurement_id for name in selected_candidates
        ):
            raise CampaignError(f"comparisons.{comparison_id} measurement definitions differ")
        dataset_id = selected_datasets[0]
        available = [name for name in selected_candidates if (name, dataset_id) in evidence_cache]
        missing = [name for name in selected_candidates if name not in available]
        if missing:
            holds.append(f"comparison {comparison_id}: missing candidates {','.join(missing)}")
        if len(available) >= 2:
            reference = evidence_cache[(available[0], dataset_id)]
            for candidate_id in available[1:]:
                current = evidence_cache[(candidate_id, dataset_id)]
                if current["run_names"] != reference["run_names"]:
                    raise CampaignError(f"comparisons.{comparison_id} run names differ")
                for name in reference["run_names"]:
                    left, right = reference["runs"][name], current["runs"][name]
                    for key in ("trace_sha256", "prepared_trace_sha256", "generated", "fixed_window_cycles"):
                        if left[key] != right[key]:
                            raise CampaignError(
                                f"comparisons.{comparison_id} {name} {key} differs across candidates"
                            )
        comparison_summary[comparison_id] = {
            "status": "VALIDATED" if not missing else "HOLD",
            "dataset": dataset_id, "source_class": datasets[dataset_id]["source_class"],
            "measurement_definition": measurement_id,
            "measurement_definition_sha256": object_sha256(measurement),
            "validated_candidates": available, "missing_candidates": missing,
            "trace_identity_cross_check": "PASS" if len(available) >= 2 else "NOT_ENOUGH_EVIDENCE",
        }

    holds = sorted(set(holds))
    return {
        "schema": "redred_canonical_campaign_receipt_v1",
        "status": "PASS" if not holds else "HOLD",
        "campaign_id": campaign_id,
        "mode": mode,
        "commands_executed": False,
        "official_registry": official_identity,
        "measurement_definitions": {
            name: {"sha256": object_sha256(row)} for name, row in measurements.items()
        },
        "datasets": dataset_summary,
        "providers": provider_summary,
        "candidates": candidate_summary,
        "comparisons": comparison_summary,
        "aggregation_policy": {
            "capacity22_is_full50_subset_view": True,
            "capacity22_full50_pooling": "FORBIDDEN",
            "groups": manifest["aggregation_groups"],
        },
        "execution_plan": plans if mode == "dry-run" else [],
        "hold_reasons": holds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "dry-run"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-hold", action="store_true",
                        help="return zero for a valid HOLD receipt; status remains HOLD")
    args = parser.parse_args()
    try:
        manifest_path = args.manifest.resolve()
        manifest = _load_json(manifest_path, "campaign manifest")
        receipt = validate_campaign(manifest, args.repo_root.resolve(), args.mode)
        receipt["manifest"] = {
            "path": str(manifest_path), "sha256": sha256(manifest_path),
        }
        payload = canonical(receipt)
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
        sys.stdout.buffer.write(payload)
        if receipt["status"] == "HOLD" and not args.allow_hold:
            return 3
        return 0
    except (CampaignError, OSError) as error:
        print(f"REDRED_CAMPAIGN_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
