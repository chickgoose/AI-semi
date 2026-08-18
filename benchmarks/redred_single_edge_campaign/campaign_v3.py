#!/usr/bin/env python3
"""Version-three REDRED campaign consumer for two independent sealed tuples."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
DEFAULT_MANIFEST = PACKAGE / "campaign_v3.json"
DEFAULT_MANIFEST_SHA256 = "6e99e568702d2ff59d17af5ca0d84922cb3631fdd19d07eeb12a603cfd392a15"
LEGACY_PATH = "benchmarks/redred_single_edge_campaign/campaign.json"
LEGACY_SHA256 = "316f9bee92255616dfaa80b74a6cf0868c01c3f5e9798177124aae7bc2035fba"
CONTRACT_PATH = "benchmarks/redred_single_edge_campaign/sealed_tuple.schema.json"
CONTRACT_SHA256 = "859ca3006b0d5367e60555cd2a10d218e096490484ae5f80012105cee19c8289"
PRODUCER_KEYS = {
    "state", "publication_schema", "evidence_class", "status", "source_class",
    "canonical_redred_traffic", "official_contest_traffic", "p6_evidence_used",
    "release_status", "selection_status", "required_binding_fields",
}
PRODUCER_IDENTITIES = {
    "synthetic_v2": {
        "publication_schema": "redred_single_edge_synthetic_publication_v2",
        "evidence_class": "REDRED_SINGLE_EDGE_SYNTHETIC_ACTUAL_RTL_SEALED_V2",
        "status": "PASS", "source_class": "TEAM_DEFINED_SYNTHETIC",
        "canonical_redred_traffic": True,
    },
    "public_v2": {
        "publication_schema": "redred_single_edge_public_projected_publication_v2",
        "evidence_class": "REDRED_SINGLE_EDGE_PUBLIC_PROJECTED_ACTUAL_RTL_SEALED_V2",
        "status": "PUBLIC_PROJECTED_EXTENSION",
        "source_class": "PUBLIC_PROJECTED_EXTENSION",
        "canonical_redred_traffic": False,
    },
}


class CampaignV3Error(RuntimeError):
    """The v3 policy, producer binding, or supplied tuple is contradictory."""


def load_local_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise CampaignV3Error(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


sealed = load_local_module("redred_campaign_sealed_v2", PACKAGE / "sealed_v2.py")
legacy = load_local_module("redred_campaign_legacy_v2", PACKAGE / "campaign.py")


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CampaignV3Error(f"{label} must be an object")
    if set(value) != keys:
        raise CampaignV3Error(
            f"{label} keys differ: missing={sorted(keys-set(value))} "
            f"extra={sorted(set(value)-keys)}"
        )
    return value


def local_ref(root: Path, value: Any, expected_path: str, expected_sha: str, label: str) -> Path:
    row = exact(value, {"path", "sha256"}, label)
    if row != {"path": expected_path, "sha256": expected_sha}:
        raise CampaignV3Error(f"{label} identity differs")
    path = root.joinpath(*Path(expected_path).parts)
    cursor = root
    for part in Path(expected_path).parts:
        cursor /= part
        if cursor.is_symlink():
            raise CampaignV3Error(f"{label} traverses a symlink")
    if not path.is_file() or sealed.digest(path.read_bytes()) != expected_sha:
        raise CampaignV3Error(f"{label} bytes differ")
    return path


def validate_producer(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CampaignV3Error(f"sealed_producers.{name} must be an object")
    state = value.get("state")
    keys = PRODUCER_KEYS if state == "UNBOUND" else PRODUCER_KEYS | {"binding"}
    row = exact(value, keys, f"sealed_producers.{name}")
    if state not in ("UNBOUND", "BOUND"):
        raise CampaignV3Error(f"sealed_producers.{name}.state differs")
    expected = PRODUCER_IDENTITIES[name]
    for key, wanted in expected.items():
        if not sealed.strict_equal(row[key], wanted):
            raise CampaignV3Error(f"sealed_producers.{name}.{key} differs")
    if type(row["canonical_redred_traffic"]) is not bool \
            or row["official_contest_traffic"] is not False \
            or row["p6_evidence_used"] is not False \
            or row["release_status"] != "HOLD" or row["selection_status"] != "HOLD":
        raise CampaignV3Error(f"sealed_producers.{name} classification expands evidence")
    required = row["required_binding_fields"]
    if not isinstance(required, list) or len(required) != len(set(required)) \
            or any(not isinstance(item, str) or not item for item in required):
        raise CampaignV3Error(f"sealed_producers.{name} required fields are malformed")
    if state == "UNBOUND" and "binding" in row:
        raise CampaignV3Error(f"sealed_producers.{name} has an unbound placeholder")
    if state == "BOUND":
        binding = row["binding"]
        flattened = {
            "publication_sha256", "publication_size_bytes", "producer.commit",
            "producer.tree", "producer.verifier_sha256", "producer.schema_sha256",
            "producer.runner_sha256", "producer.testbench_sha256",
            "producer.tool_pins_sha256", "rtl.source_commit", "rtl.source_tree",
            "rtl.integration_commit", "rtl.integration_tree", "bundle_sha256",
            "bundle_size_bytes", "manifest_schema", "manifest_member",
            "manifest_sha256", "entry_count", "result_schema", "result_member",
            "result_sha256", "result_semantic_sha256", "owners", "traffic_runs",
            "result_size_bytes", "reset_run", "activation_run", "mutations", "diagnostics",
        }
        if set(required) != flattened or not isinstance(binding, dict):
            raise CampaignV3Error(f"sealed_producers.{name} binding contract differs")
    return row


def validate_manifest(path: Path, root: Path) -> dict[str, Any]:
    _, manifest_data = sealed.stable_file(path, "campaign v3 manifest")
    if sealed.digest(manifest_data) != DEFAULT_MANIFEST_SHA256:
        raise CampaignV3Error("campaign v3 manifest binding differs")
    manifest = exact(sealed.load_json_bytes(manifest_data, "campaign v3 manifest"), {
        "schema", "campaign_id", "legacy_v2", "sealed_tuple_contract",
        "sealed_producers", "policies",
    }, "campaign v3 manifest")
    if manifest["schema"] != "redred_single_edge_campaign_manifest_v3" or \
            manifest["campaign_id"] != "redred-a2-a3-single-edge-campaign-v3":
        raise CampaignV3Error("campaign v3 identity differs")
    legacy_path = local_ref(root, manifest["legacy_v2"], LEGACY_PATH, LEGACY_SHA256, "legacy v2")
    contract_path = local_ref(
        root, manifest["sealed_tuple_contract"], CONTRACT_PATH, CONTRACT_SHA256,
        "sealed tuple contract",
    )
    contract = sealed.load_json_bytes(contract_path.read_bytes(), "sealed tuple contract")
    if contract.get("$id") != "redred_single_edge_sealed_tuple_publication_v2":
        raise CampaignV3Error("sealed tuple contract schema identity differs")
    producers = exact(manifest["sealed_producers"], set(PRODUCER_IDENTITIES), "sealed producers")
    normalized = {name: validate_producer(name, producers[name]) for name in PRODUCER_IDENTITIES}
    policies = exact(manifest["policies"], {
        "synthetic_public_pooling", "official_contest_evidence", "physical_gate",
        "power_gate", "system_release_requires_independent_gates",
        "unbound_or_missing_tuple_is_hold", "partial_or_unpinned_tuple_is_error",
    }, "campaign v3 policies")
    if policies != {
        "synthetic_public_pooling": "FORBIDDEN",
        "official_contest_evidence": "ABSENT",
        "physical_gate": "INDEPENDENT_HOLD", "power_gate": "INDEPENDENT_HOLD",
        "system_release_requires_independent_gates": True,
        "unbound_or_missing_tuple_is_hold": True,
        "partial_or_unpinned_tuple_is_error": True,
    }:
        raise CampaignV3Error("campaign v3 policy differs")
    return {
        "manifest": manifest, "legacy_path": legacy_path,
        "producers": normalized, "policies": policies,
    }


def tuple_state(
    name: str, producer: dict[str, Any], publication: Path | None, bundle: Path | None,
) -> dict[str, Any]:
    if publication is None and bundle is None:
        return {
            "status": f"HOLD_MISSING_{name.upper()}_PRODUCER_TUPLE",
            "binding_state": producer["state"], "validated": False,
            "missing": ["publication", "bundle"],
        }
    if publication is None or bundle is None:
        raise CampaignV3Error(f"{name} publication and bundle must be supplied together")
    if producer["state"] != "BOUND":
        raise CampaignV3Error(f"{name} exact producer binding is unavailable")
    try:
        validated = sealed.validate_tuple(publication, bundle, producer["binding"], name)
    except sealed.SealedTupleError as error:
        raise CampaignV3Error(str(error)) from error
    return {"status": "PASS", "binding_state": "BOUND", "validated": True, **validated}


def evaluate(
    manifest_path: Path, root: Path,
    synthetic_publication: Path | None = None, synthetic_bundle: Path | None = None,
    public_publication: Path | None = None, public_bundle: Path | None = None,
) -> dict[str, Any]:
    context = validate_manifest(manifest_path, root)
    legacy_report = legacy.evaluate(context["legacy_path"], root)
    synthetic = tuple_state(
        "synthetic_v2", context["producers"]["synthetic_v2"],
        synthetic_publication, synthetic_bundle,
    )
    public = tuple_state(
        "public_v2", context["producers"]["public_v2"],
        public_publication, public_bundle,
    )
    synthetic_pass = synthetic["status"] == "PASS"
    public_pass = public["status"] == "PASS"
    return {
        "schema": "redred_single_edge_campaign_evidence_v3",
        "status": "HOLD",
        "campaign_id": "redred-a2-a3-single-edge-campaign-v3",
        "legacy_v2": {
            "status": legacy_report["status"],
            "committed_receipt": legacy_report["gates"]["committed_hardened_receipt"],
            "campaign_gate": legacy_report["gates"]["canonical_single_edge_campaign"],
        },
        "sealed_tuples": {"synthetic_v2": synthetic, "public_v2": public},
        "gates": {
            "synthetic_v2_sealed_tuple": "PASS" if synthetic_pass else synthetic["status"],
            "canonical_synthetic_campaign": "PASS" if synthetic_pass else "HOLD",
            "public_v2_sealed_tuple": "PASS" if public_pass else public["status"],
            "public_projected_extension": "PASS" if public_pass else "HOLD",
            "official_contest_evidence": "HOLD_ABSENT",
            "physical": "HOLD_INDEPENDENT",
            "power": "HOLD_INDEPENDENT",
            "system_release": "HOLD",
        },
        "aggregation": {
            "synthetic_public_pooling": "FORBIDDEN",
            "public_timing_variants_are_not_independent_samples": True,
        },
        "producer_requirements": {
            name: {
                "binding_state": producer["state"],
                "evidence_class": producer["evidence_class"],
                "required_binding_fields": producer["required_binding_fields"],
            }
            for name, producer in context["producers"].items()
        },
        "claim_boundary": {
            "official_contest_claimed": False, "release_claimed": False,
            "physical_claimed": False, "power_claimed": False,
            "new_evidence_inferred": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?", choices=("evaluate",))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--synthetic-v2-publication", type=Path)
    parser.add_argument("--synthetic-v2-bundle", type=Path)
    parser.add_argument("--public-v2-publication", type=Path)
    parser.add_argument("--public-v2-bundle", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-hold", action="store_true")
    args = parser.parse_args()
    try:
        if ".." in args.repo_root.parts or args.repo_root.is_symlink():
            raise CampaignV3Error("repository root is aliased or symlinked")
        repo_root = args.repo_root.resolve(strict=True)
        if not repo_root.is_dir():
            raise CampaignV3Error("repository root is not a directory")
        report = evaluate(
            args.manifest, repo_root,
            args.synthetic_v2_publication, args.synthetic_v2_bundle,
            args.public_v2_publication, args.public_v2_bundle,
        )
        payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        if args.output:
            if args.output.exists() or args.output.is_symlink():
                raise CampaignV3Error("output already exists")
            args.output.write_bytes(payload)
        sys.stdout.buffer.write(payload)
        return 0 if args.allow_hold else 3
    except (CampaignV3Error, sealed.SealedTupleError, OSError, ValueError) as error:
        print(f"REDRED_SINGLE_EDGE_CAMPAIGN_V3_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
