#!/usr/bin/env python3
"""Run both native v2 adapters, attest full50 policy, and aggregate their views.

The output is a campaign-scoped recommendation receipt.  It has no authority
to select a final candidate or to claim official, physical, power, or release
closure.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
from typing import Any


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
POLICY_PATH = "benchmarks/redred_single_edge_campaign/team_canonical_policy.json"
POLICY_SHA256 = "cc83d17cbf0f405e930fb46d45602c4466c446687c8abc11ad819c28f050e505"
POLICY_SIZE_BYTES = 2590
PIPELINE_SCHEMA = "redred_single_edge_native_pipeline_result_v1"
CLAIMS = {
    "final_selection": "HOLD",
    "official": False,
    "physical": False,
    "power": False,
    "release": False,
}
PROMOTION_POINTERS = (
    "/shared_gates/canonical_campaign_policy",
    "/candidates/A2",
    "/candidates/A3",
)
PINNED_MODULES = {
    "aggregate_gate": {
        "path": "benchmarks/redred_single_edge_campaign/aggregate_gate.py",
        "sha256": "734ed2d09f9a2f02950ccd9ba3af49bf372c803d23e79b83aeea0ecc489dfd26",
    },
    "synthetic_v2_native_adapter": {
        "path": "benchmarks/redred_single_edge_campaign/synthetic_v2_native_adapter.py",
        "sha256": "153c2038f6773eef28ff8bb50675164da7f265d3e8beb8d034c570311ad70895",
    },
    "public_v2_native_adapter": {
        "path": "benchmarks/redred_single_edge_campaign/public_v2_native_adapter.py",
        "sha256": "7b2bbebba1d67a4ab52096fe8f7e8f43e5dfdb1e4d1554f7aa23a1c5f147be32",
    },
}


class NativePipelineError(RuntimeError):
    """The native evidence, policy attestation, or aggregate is inconsistent."""


def load_local_module(name: str, path: Path, data: bytes) -> ModuleType:
    """Execute exactly the module bytes captured and hashed below."""
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except Exception as error:
        sys.modules.pop(name, None)
        raise NativePipelineError(f"cannot execute captured pipeline module: {path}") from error
    return module


def verify_module_file(
    name: str, root: Path = PROJECT,
) -> tuple[dict[str, Any], bytes]:
    pin = PINNED_MODULES[name]
    path = root.joinpath(*Path(pin["path"]).parts)
    cursor = root
    for part in Path(pin["path"]).parts:
        cursor /= part
        if cursor.is_symlink():
            raise NativePipelineError(f"pinned module traverses a symlink: {name}")
    try:
        resolved = path.resolve(strict=True)
        before = resolved.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise NativePipelineError(f"pinned module is not one regular file: {name}")
        descriptor = os.open(
            resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise NativePipelineError(f"cannot read pinned module: {name}") from error
    try:
        opened = os.fstat(descriptor)
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    data = b"".join(blocks)
    if any(getattr(before, field) != getattr(opened, field)
           or getattr(opened, field) != getattr(after, field) for field in fields) \
            or len(data) != after.st_size \
            or hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise NativePipelineError(f"pinned module bytes differ: {name}")
    return ({"path": pin["path"], "sha256": pin["sha256"], "size_bytes": len(data)}, data)


_MODULE_CAPTURES = {
    name: verify_module_file(name) for name in PINNED_MODULES
}


aggregate = load_local_module(
    "redred_pipeline_aggregate_gate", PACKAGE / "aggregate_gate.py",
    _MODULE_CAPTURES["aggregate_gate"][1],
)
synthetic_adapter = load_local_module(
    "redred_pipeline_synthetic_adapter", PACKAGE / "synthetic_v2_native_adapter.py",
    _MODULE_CAPTURES["synthetic_v2_native_adapter"][1],
)
public_adapter = load_local_module(
    "redred_pipeline_public_adapter", PACKAGE / "public_v2_native_adapter.py",
    _MODULE_CAPTURES["public_v2_native_adapter"][1],
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def pretty(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NativePipelineError(f"{label} must be an object")
    if set(value) != keys:
        raise NativePipelineError(
            f"{label} keys differ: missing={sorted(keys-set(value))} "
            f"extra={sorted(set(value)-keys)}"
        )
    return value


def expected_candidate(role: str, semantics: str, status: str) -> dict[str, Any]:
    if status == "HOLD":
        return {
            "role": role,
            "semantic_class": semantics,
            "gate_status": "HOLD",
            "failure_scope": "UNRESOLVED",
            "reason_codes": ["CANONICAL_POLICY_NOT_ATTESTED_BY_NATIVE_VERIFICATION"],
        }
    return {
        "role": role,
        "semantic_class": semantics,
        "gate_status": "PASS",
        "failure_scope": "NONE",
        "reason_codes": [],
    }


def expected_promotions() -> list[dict[str, Any]]:
    return [
        {
            "json_pointer": PROMOTION_POINTERS[0],
            "from": "HOLD",
            "to": "PASS",
        },
        {
            "json_pointer": PROMOTION_POINTERS[1],
            "from": expected_candidate("PRIMARY", "AGGREGATE_WEIGHTED_PERFORMANCE", "HOLD"),
            "to": expected_candidate("PRIMARY", "AGGREGATE_WEIGHTED_PERFORMANCE", "PASS"),
        },
        {
            "json_pointer": PROMOTION_POINTERS[2],
            "from": expected_candidate("SEMANTIC_FALLBACK", "EXACT_SCALAR_PREFIX", "HOLD"),
            "to": expected_candidate("SEMANTIC_FALLBACK", "EXACT_SCALAR_PREFIX", "PASS"),
        },
    ]


def validate_policy(policy: Any) -> dict[str, Any]:
    policy = exact(policy, {
        "schema", "policy_id", "status", "authority", "canonical_synthetic",
        "public_extension", "authorized_synthetic_promotions", "candidate_policy",
        "claim_boundary",
    }, "team canonical policy")
    expected_scalars = {
        "schema": "redred_single_edge_team_canonical_policy_v1",
        "policy_id": "redred-team-full50-canonical-2026-08-19",
        "status": "ATTESTED_TEAM_POLICY",
        "authority": "TEAM_DEFINED_CAMPAIGN_POLICY_ONLY",
    }
    for key, value in expected_scalars.items():
        if not aggregate.strict_equal(policy[key], value):
            raise NativePipelineError(f"team canonical policy {key} differs")
    expected_synthetic = {
        "slot": "synthetic_v2", "dataset_id": "full50",
        "family_id": "team-full50-family", "source_class": "TEAM_DEFINED_SYNTHETIC",
        "trace_count": 50, "canonical_redred_traffic": True,
        "organizer_official": False,
    }
    expected_public = {
        "slot": "public_v2", "family_id": "uzh-shapes-rotation-public-projected-v2",
        "source_class": "PUBLIC_PROJECTED_EXTENSION", "canonical_redred_traffic": False,
        "independent_sample_count": 1, "retiming_labels": ["1x", "64x", "256x"],
        "retimings_are_independent_samples": False, "pool_with_synthetic": False,
    }
    expected_candidates = {
        "primary": "A2", "fallback": "A3",
        "fallback_allowed_triggers": ["EXACT_PREFIX_REQUIRED", "A2_SPECIFIC_GATE_FAILURE"],
        "fallback_forbidden_triggers": [
            "SHARED_CAMPAIGN_FAILURE", "SHARED_INTERFACE_FAILURE",
            "SHARED_CDC_RDC_FAILURE", "SHARED_PDK_IO_FAILURE",
        ],
    }
    for key, value in (
        ("canonical_synthetic", expected_synthetic),
        ("public_extension", expected_public),
        ("authorized_synthetic_promotions", expected_promotions()),
        ("candidate_policy", expected_candidates),
        ("claim_boundary", CLAIMS),
    ):
        if not aggregate.strict_equal(policy[key], value):
            raise NativePipelineError(f"team canonical policy {key} differs")
    return policy


def load_policy(root: Path) -> tuple[dict[str, Any], dict[str, Any], Path,
                                     tuple[int, int, int, int, int]]:
    path = root.joinpath(*Path(POLICY_PATH).parts)
    resolved, data, identity = aggregate.stable_file(path, "team canonical policy")
    if len(data) != POLICY_SIZE_BYTES or digest(data) != POLICY_SHA256:
        raise NativePipelineError("team canonical policy committed bytes differ")
    try:
        policy = aggregate.load_json(data, "team canonical policy")
    except aggregate.AggregateGateError as error:
        raise NativePipelineError(str(error)) from error
    validate_policy(policy)
    return policy, {
        "path": POLICY_PATH,
        "sha256": POLICY_SHA256,
        "size_bytes": POLICY_SIZE_BYTES,
        "policy_id": policy["policy_id"],
        "authority": policy["authority"],
    }, resolved, identity


def pointer_parts(pointer: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise NativePipelineError("policy promotion JSON pointer is malformed")
    parts = tuple(pointer[1:].split("/"))
    if not parts or any(not part or "~" in part for part in parts):
        raise NativePipelineError("policy promotion JSON pointer is unsupported")
    return parts


def pointer_get(document: dict[str, Any], pointer: str) -> Any:
    value: Any = document
    for part in pointer_parts(pointer):
        if not isinstance(value, dict) or part not in value:
            raise NativePipelineError(f"policy promotion target is missing: {pointer}")
        value = value[part]
    return value


def pointer_set(document: dict[str, Any], pointer: str, value: Any) -> None:
    parts = pointer_parts(pointer)
    target: Any = document
    for part in parts[:-1]:
        if not isinstance(target, dict) or part not in target:
            raise NativePipelineError(f"policy promotion parent is missing: {pointer}")
        target = target[part]
    if not isinstance(target, dict) or parts[-1] not in target:
        raise NativePipelineError(f"policy promotion target is missing: {pointer}")
    target[parts[-1]] = copy.deepcopy(value)


def changed_pointers(before: Any, after: Any, prefix: str = "") -> set[str]:
    if type(before) is not type(after):
        return {prefix or "/"}
    if isinstance(before, dict):
        if set(before) != set(after):
            return {prefix or "/"}
        changed: set[str] = set()
        for key in before:
            changed.update(changed_pointers(before[key], after[key], f"{prefix}/{key}"))
        return changed
    if isinstance(before, list):
        return set() if aggregate.strict_equal(before, after) else {prefix or "/"}
    return set() if aggregate.strict_equal(before, after) else {prefix or "/"}


def attest_synthetic_view(common_view: dict[str, Any], policy: dict[str, Any]) -> tuple[
        dict[str, Any], dict[str, Any]]:
    try:
        aggregate.validate_view(common_view, "synthetic_v2")
    except aggregate.AggregateGateError as error:
        raise NativePipelineError(f"synthetic common view failed before attestation: {error}") from error
    expected_units = policy["canonical_synthetic"]
    if common_view["classification"] != {
        "evidence_status": "PASS", "source_class": expected_units["source_class"],
        "canonical_redred_traffic": expected_units["canonical_redred_traffic"],
        "official_contest_traffic": False, "p6_evidence_used": False,
    } or common_view["campaign_units"] != {
        "family_id": expected_units["family_id"], "unit_kind": "SYNTHETIC_TRACE_CAMPAIGN",
        "independent_sample_count": expected_units["trace_count"], "retiming_labels": [],
        "retimings_are_independent_samples": False,
        "pooling_with_other_slots_permitted": False,
    }:
        raise NativePipelineError("synthetic common view does not match full50 team policy")
    if common_view["shared_gates"] != {
        "native_tuple_integrity": "PASS", "canonical_campaign_policy": "HOLD",
    }:
        raise NativePipelineError("synthetic common view pre-attestation shared gates differ")

    promoted = copy.deepcopy(common_view)
    applied = []
    for row in policy["authorized_synthetic_promotions"]:
        pointer = row["json_pointer"]
        if not aggregate.strict_equal(pointer_get(promoted, pointer), row["from"]):
            raise NativePipelineError(f"synthetic promotion source differs: {pointer}")
        pointer_set(promoted, pointer, row["to"])
        applied.append(pointer)
    changed = changed_pointers(common_view, promoted)
    if tuple(applied) != PROMOTION_POINTERS or not changed or any(
        not any(path == allowed or path.startswith(f"{allowed}/") for allowed in PROMOTION_POINTERS)
        for path in changed
    ):
        raise NativePipelineError("synthetic policy changed unauthorized common-view fields")
    restored = copy.deepcopy(promoted)
    for row in policy["authorized_synthetic_promotions"]:
        pointer_set(restored, row["json_pointer"], row["from"])
    if not aggregate.strict_equal(restored, common_view):
        raise NativePipelineError("synthetic policy did not preserve non-promoted fields")
    try:
        aggregate.validate_view(promoted, "synthetic_v2")
    except aggregate.AggregateGateError as error:
        raise NativePipelineError(f"synthetic common view failed after attestation: {error}") from error
    return promoted, {
        "status": "PASS_EXACT_AUTHORIZED_PROMOTIONS",
        "changed_json_pointers": list(PROMOTION_POINTERS),
        "before_sha256": digest(canonical(common_view)),
        "after_sha256": digest(canonical(promoted)),
        "other_fields_changed": False,
    }


def validate_native_boundaries(synthetic_native: dict[str, Any],
                               public_native: dict[str, Any]) -> None:
    synthetic_boundary = synthetic_native["claim_boundary"]
    if synthetic_boundary != {
        "native_schema_preserved": True, "native_paths_preserved": True,
        "archive_repacked": False, "traffic_relabeled": False,
        "official_contest_claimed": False, "physical_claimed": False,
        "power_claimed": False, "selection_claimed": False,
        "release_claimed": False,
        "canonical_campaign_status": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
    }:
        raise NativePipelineError("synthetic native claim boundary differs")
    if public_native["claim_boundary"] != {
        "canonical_campaign_promoted": False,
        "official_contest_evidence_claimed": False,
        "synthetic_public_pooling": "FORBIDDEN",
        "archive_extracted_or_repacked": False,
        "producer_schema_relabelled": False,
        "system_release": "HOLD",
    } or public_native["release_status"] != "HOLD" \
            or public_native["selection_status"] != "HOLD":
        raise NativePipelineError("public native claim boundary differs")


def run_native_adapters(root: Path) -> tuple[dict[str, Any], dict[str, Any],
                                              dict[str, Any], dict[str, Any]]:
    try:
        synthetic_native = synthetic_adapter.evaluate(root)
        synthetic_common = copy.deepcopy(
            synthetic_adapter.campaign_normalized_view(synthetic_native)
        )
        public_native = public_adapter.validate_tuple(
            root / "tests/a23_public_projected_v2/public_projected_v2_publication.json",
            root / "tests/a23_public_projected_v2/public_projected_v2_export.tar.gz",
            root,
        )
        public_common = copy.deepcopy(public_adapter.normalized_view(public_native))
    except Exception as error:
        raise NativePipelineError(f"native adapter failed: {error}") from error
    validate_native_boundaries(synthetic_native, public_native)
    try:
        aggregate.validate_view(synthetic_common, "synthetic_v2")
        aggregate.validate_view(public_common, "public_v2")
    except aggregate.AggregateGateError as error:
        raise NativePipelineError(f"native adapter common view failed: {error}") from error
    return synthetic_native, synthetic_common, public_native, public_common


def aggregate_common_views(synthetic: dict[str, Any], public: dict[str, Any]) -> dict[str, Any]:
    try:
        result = aggregate.evaluate_authenticated_views(
            synthetic, public, "AGGREGATE_WEIGHTED",
            authentication=aggregate._PIPELINE_CONTEXT,
        )
    except aggregate.AggregateGateError as error:
        raise NativePipelineError(f"aggregate gate failed: {error}") from error
    expected_decision = {
        "status": "A2_PRIMARY", "campaign_recommendation": "A2",
        "fallback_activated": False, "fallback_trigger": None,
        "final_selected_candidate": None, "final_selection_status": "HOLD",
        "final_release_status": "HOLD", "release_authority": False,
    }
    for key, expected in expected_decision.items():
        if not aggregate.strict_equal(result["decision"].get(key), expected):
            raise NativePipelineError(f"aggregate campaign decision differs: {key}")
    if result["status"] != "PASS_SCOPED_CAMPAIGN_RECOMMENDATION" \
            or result["claims"] != {
                "official": False, "physical": False, "power": False,
                "release": False, "final_candidate_selection": False,
            }:
        raise NativePipelineError("aggregate claim boundary differs")
    return result


def evaluate(root: Path = PROJECT) -> dict[str, Any]:
    if root.is_symlink() or ".." in root.parts:
        raise NativePipelineError("repository root is aliased or symlinked")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NativePipelineError("repository root is not a directory")
    module_pins = {
        name: verify_module_file(name, root)[0] for name in PINNED_MODULES
    }
    policy, policy_record, policy_path, policy_identity = load_policy(root)
    synthetic_native, synthetic_common, public_native, public_common = run_native_adapters(root)
    promoted_synthetic, promotion = attest_synthetic_view(synthetic_common, policy)
    aggregate_result = aggregate_common_views(promoted_synthetic, public_common)
    public_units = public_common["campaign_units"]
    if public_units["independent_sample_count"] != 1 \
            or public_units["retimings_are_independent_samples"] is not False \
            or aggregate_result["aggregation"]["pooled_totals_emitted"] is not False:
        raise NativePipelineError("public retiming family was pooled")
    aggregate.recheck_file(policy_path, policy_identity, "team canonical policy")
    if synthetic_common["verification"]["adapter_sha256"] != \
            module_pins["synthetic_v2_native_adapter"]["sha256"] \
            or public_common["verification"]["adapter_sha256"] != \
            module_pins["public_v2_native_adapter"]["sha256"]:
        raise NativePipelineError("common view adapter hash is not the pinned in-process module")

    result = {
        "schema": PIPELINE_SCHEMA,
        "status": "PASS_SCOPED_NATIVE_CAMPAIGN_PIPELINE",
        "policy_attestation": policy_record,
        "adapter_execution": {
            "synthetic_v2": {
                "adapter_called": True,
                "code": module_pins["synthetic_v2_native_adapter"],
                "native_schema": synthetic_native["schema"],
                "native_status": synthetic_native["status"],
                "native_report_sha256": digest(canonical(synthetic_native)),
            },
            "public_v2": {
                "adapter_called": True,
                "code": module_pins["public_v2_native_adapter"],
                "native_schema": public_native["schema"],
                "native_status": public_native["status"],
                "native_report_sha256": digest(canonical(public_native)),
            },
        },
        "upstream_raw_hashes": {
            "synthetic_v2": synthetic_native["native_artifacts"],
            "public_v2": public_native["raw_artifacts"],
        },
        "full_metrics": {
            "synthetic_v2": synthetic_native["normalized"],
            "public_v2": public_native["owners"],
        },
        "common_views": {
            "synthetic_v2_before_policy": synthetic_common,
            "synthetic_v2_attested": promoted_synthetic,
            "public_v2": public_common,
        },
        "synthetic_policy_promotion": promotion,
        "aggregation_accounting": {
            "synthetic_public_pooling": "FORBIDDEN",
            "public_family_id": public_units["family_id"],
            "public_independent_sample_count": 1,
            "public_retiming_labels": public_units["retiming_labels"],
            "public_retimings_counted_as_independent_samples": False,
            "pooled_totals_emitted": False,
        },
        "aggregate_result": aggregate_result,
        "campaign_recommendation": "A2",
        "claims": copy.deepcopy(CLAIMS),
    }
    result["seal"] = {
        "algorithm": "SHA256_CANONICAL_JSON_EXCLUDING_SEAL",
        "semantic_sha256": digest(canonical(result)),
    }
    return result


def write_new(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise NativePipelineError("output already exists")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise NativePipelineError("output parent is not a real directory")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644,
    )
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?", choices=("evaluate",))
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        result = evaluate(arguments.repo_root)
        payload = pretty(result)
        if arguments.output:
            write_new(arguments.output, payload)
        sys.stdout.buffer.write(payload)
        return 0
    except (NativePipelineError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"REDRED_NATIVE_PIPELINE_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
