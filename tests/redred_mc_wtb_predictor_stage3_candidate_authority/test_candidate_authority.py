from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    dspb_output,
    pll_output,
    rg3_output,
)
from benchmarks.redred_mc_wtb_predictor_stage3.candidate_authority import (
    AUTHORITY_SCHEMA,
    CAMPAIGN_SCHEMA,
    CANDIDATE_NAMES,
    CandidateAuthorityError,
    build_campaign_authority,
    build_candidate_authority,
    candidate_config_bytes,
    candidate_config_mapping,
    candidate_config_sha256,
    candidate_dependency_paths,
    candidate_executable_artifact_bytes,
    candidate_executable_sha256,
    candidate_native_id,
    verify_campaign_authority,
    verify_candidate_authority,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from tests.redred_mc_wtb_predictor_stage3_real_candidates.production_gate import (
    generate_production_output,
    make_motion_fixture,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ADAPTERS = {
    "RG3": "benchmarks/redred_mc_wtb_predictor_stage3/rg3_output.py",
    "DSPB": "benchmarks/redred_mc_wtb_predictor_stage3/dspb_output.py",
    "PLL": "benchmarks/redred_mc_wtb_predictor_stage3/pll_output.py",
}
EXPECTED_MODELS = {
    "RG3": "benchmarks/redred_mc_wtb_predictor_stage3/rg3.py",
    "DSPB": "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
    "PLL": "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py",
}
REQUIRED_SHARED_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
)
EXCLUDED_NONEXECUTION_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/candidate_authority.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/output_common.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/screen108.py",
)


def _adapter_config_exports():
    return {
        "RG3": (
            rg3_output.RG3_OUTPUT_CANDIDATE_ID,
            bytes(rg3_output.RG3_CONFIG_BYTES),
            rg3_output.RG3_CONFIG_SHA256,
        ),
        "DSPB": (
            dspb_output.DSPBConfig().candidate_id,
            dspb_output.locked_dspb_config_bytes(),
            dspb_output.locked_dspb_config_sha256(),
        ),
        "PLL": (
            pll_output.CANDIDATE_ID,
            pll_output.locked_config_bytes(),
            pll_output.locked_config_sha256(),
        ),
    }


def _adapter_executable_exports():
    dspb_manifest = dict(dspb_output.locked_dspb_executable_manifest())
    dspb_sha = dspb_manifest.pop("manifest_sha256")
    return {
        "RG3": (
            bytes(rg3_output.RG3_EXECUTABLE_MANIFEST_BYTES),
            rg3_output.RG3_EXECUTABLE_SHA256,
            tuple(row["path"] for row in rg3_output.RG3_EXECUTABLE_MANIFEST["files"]),
        ),
        "DSPB": (
            canonical_json_bytes(dspb_manifest),
            dspb_sha,
            tuple(row["path"] for row in dspb_manifest["files"]),
        ),
        "PLL": (
            canonical_json_bytes(pll_output.executable_dependency_manifest()),
            pll_output.generator_executable_sha256(),
            tuple(
                row["path"]
                for row in pll_output.executable_dependency_manifest()["files"]
            ),
        ),
    }


def _copy_dependency_tree(authority, destination):
    for dependency in authority.dependencies:
        source = REPO_ROOT / dependency.path
        target = destination / dependency.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))


def _reseal_manifest(manifest, dependencies_changed=False):
    if dependencies_changed:
        manifest["dependency_aggregate_sha256"] = canonical_sha256(
            manifest["dependencies"]
        )
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_sha256(unsigned)
    return manifest


class CandidateAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authorities = {
            name: build_candidate_authority(name) for name in CANDIDATE_NAMES
        }

    def test_native_ids_and_config_bytes_are_exact_adapter_exports(self):
        for name, (native_id, config_bytes, config_sha) in (
            _adapter_config_exports().items()
        ):
            with self.subTest(candidate=name):
                authority = self.authorities[name]
                self.assertEqual(candidate_native_id(name), native_id)
                self.assertEqual(candidate_config_bytes(name), config_bytes)
                self.assertEqual(candidate_config_sha256(name), config_sha)
                self.assertEqual(hashlib.sha256(config_bytes).hexdigest(), config_sha)
                self.assertEqual(
                    candidate_config_mapping(name),
                    json.loads(config_bytes.decode("ascii")),
                )
                self.assertEqual(authority.native_candidate_id, native_id)
                self.assertEqual(authority.config_bytes, config_bytes)
                self.assertEqual(authority.config_sha256, config_sha)

    def test_executable_artifacts_match_adapter_output_authority(self):
        exports = _adapter_executable_exports()
        for name, (artifact, executable_sha, _) in exports.items():
            with self.subTest(candidate=name):
                authority = self.authorities[name]
                self.assertEqual(candidate_executable_artifact_bytes(name), artifact)
                self.assertEqual(candidate_executable_sha256(name), executable_sha)
                self.assertEqual(hashlib.sha256(artifact).hexdigest(), executable_sha)
                self.assertEqual(authority.executable_artifact_bytes, artifact)
                self.assertEqual(authority.executable_sha256, executable_sha)

        sealed_dspb = dspb_output.locked_dspb_executable_manifest()
        self.assertNotEqual(
            hashlib.sha256(canonical_json_bytes(sealed_dspb)).hexdigest(),
            exports["DSPB"][1],
        )
        self.assertNotIn(
            "manifest_sha256",
            json.loads(exports["DSPB"][0].decode("ascii")),
        )

    def test_real_adapter_outputs_publish_authority_hashes(self):
        fixture = make_motion_fixture(window_count=1)
        for name in CANDIDATE_NAMES:
            with self.subTest(candidate=name):
                output = generate_production_output(name, fixture)
                authority = self.authorities[name]
                self.assertEqual(output["candidate_id"], authority.native_candidate_id)
                self.assertEqual(
                    output["candidate_config_sha256"], authority.config_sha256
                )
                self.assertEqual(
                    output["candidate_executable_sha256"],
                    authority.executable_sha256,
                )

    def test_adapter_hashes_are_derived_without_authority_literals(self):
        source = (
            REPO_ROOT
            / "benchmarks/redred_mc_wtb_predictor_stage3/candidate_authority.py"
        ).read_text(encoding="utf-8")
        for name, authority in self.authorities.items():
            with self.subTest(candidate=name):
                self.assertNotIn(authority.config_sha256, source)
                self.assertNotIn(authority.executable_sha256, source)

    def test_dependency_aggregate_covers_native_and_required_shared_sources(self):
        executable_exports = _adapter_executable_exports()
        for name in CANDIDATE_NAMES:
            with self.subTest(candidate=name):
                authority = self.authorities[name]
                paths = tuple(row.path for row in authority.dependencies)
                roles = tuple(row.role for row in authority.dependencies)
                self.assertEqual(paths, candidate_dependency_paths(name))
                self.assertEqual(paths[0], EXPECTED_ADAPTERS[name])
                self.assertEqual(paths[1], EXPECTED_MODELS[name])
                self.assertEqual(roles[0:2], ("output_adapter", "model"))
                self.assertEqual(len(paths), len(set(paths)))
                for required in REQUIRED_SHARED_PATHS:
                    self.assertIn(required, paths)
                for native in executable_exports[name][2]:
                    self.assertIn(native, paths)
                for excluded in EXCLUDED_NONEXECUTION_PATHS:
                    self.assertNotIn(excluded, paths)
                self.assertEqual(
                    authority.dependency_aggregate_sha256,
                    canonical_sha256([
                        row.to_mapping() for row in authority.dependencies
                    ]),
                )
                self.assertEqual(
                    verify_candidate_authority(authority),
                    authority.manifest_sha256,
                )
                mapping = authority.to_mapping()
                self.assertEqual(mapping["schema"], AUTHORITY_SCHEMA)
                self.assertEqual(
                    bytes.fromhex(mapping["executable_bytes_hex"]),
                    authority.executable_artifact_bytes,
                )

    def test_campaign_api_binds_fixed_candidate_order(self):
        campaign = build_campaign_authority()
        self.assertEqual(campaign["schema"], CAMPAIGN_SCHEMA)
        self.assertEqual(campaign["candidate_order"], list(CANDIDATE_NAMES))
        self.assertEqual(
            [row["native_candidate_id"] for row in campaign["candidates"]],
            [candidate_native_id(name) for name in CANDIDATE_NAMES],
        )
        self.assertEqual(
            verify_campaign_authority(campaign), campaign["aggregate_sha256"]
        )

        reordered = deepcopy(campaign)
        reordered["candidates"][0:2] = reversed(reordered["candidates"][0:2])
        unsigned = dict(reordered)
        unsigned.pop("aggregate_sha256")
        reordered["aggregate_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(CandidateAuthorityError, "manifest order"):
            verify_campaign_authority(reordered)

    def test_every_aggregated_source_mutation_fails(self):
        for name, authority in self.authorities.items():
            for dependency in authority.dependencies:
                with (
                    self.subTest(candidate=name, path=dependency.path),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    root = Path(tmp)
                    _copy_dependency_tree(authority, root)
                    target = root / dependency.path
                    target.write_bytes(target.read_bytes() + b"\n# mutation\n")
                    with self.assertRaisesRegex(
                        CandidateAuthorityError, "digest differs"
                    ):
                        verify_candidate_authority(authority, root)

    def test_dependency_drop_extra_and_reorder_fail_after_reseal(self):
        authority = self.authorities["DSPB"]
        original = authority.to_mapping()

        dropped = deepcopy(original)
        dropped["dependencies"].pop()
        _reseal_manifest(dropped, dependencies_changed=True)
        with self.assertRaisesRegex(CandidateAuthorityError, "order or closure"):
            verify_candidate_authority(dropped)

        extra = deepcopy(original)
        extra["dependencies"].append({
            "role": "untrusted_extra",
            "path": "benchmarks/redred_mc_wtb_predictor_stage3/candidate_authority.py",
            "sha256": hashlib.sha256(
                (REPO_ROOT / "benchmarks/redred_mc_wtb_predictor_stage3/candidate_authority.py").read_bytes()
            ).hexdigest(),
        })
        _reseal_manifest(extra, dependencies_changed=True)
        with self.assertRaisesRegex(CandidateAuthorityError, "order or closure"):
            verify_candidate_authority(extra)

        reordered = deepcopy(original)
        reordered["dependencies"][0:2] = reversed(reordered["dependencies"][0:2])
        _reseal_manifest(reordered, dependencies_changed=True)
        with self.assertRaisesRegex(CandidateAuthorityError, "order or closure"):
            verify_candidate_authority(reordered)

    def test_config_bytes_are_not_a_replaceable_digest(self):
        manifest = deepcopy(self.authorities["PLL"].to_mapping())
        config = bytearray.fromhex(manifest["config_bytes_hex"])
        config[0] ^= 1
        manifest["config_bytes_hex"] = bytes(config).hex()
        manifest["config_sha256"] = hashlib.sha256(bytes(config)).hexdigest()
        _reseal_manifest(manifest)
        with self.assertRaisesRegex(CandidateAuthorityError, "config bytes differ"):
            verify_candidate_authority(manifest)

    def test_executable_artifact_is_not_a_replaceable_digest(self):
        manifest = deepcopy(self.authorities["RG3"].to_mapping())
        artifact = bytearray.fromhex(manifest["executable_bytes_hex"])
        artifact[0] ^= 1
        manifest["executable_bytes_hex"] = bytes(artifact).hex()
        manifest["executable_sha256"] = hashlib.sha256(bytes(artifact)).hexdigest()
        _reseal_manifest(manifest)
        with self.assertRaisesRegex(
            CandidateAuthorityError, "executable artifact bytes differ"
        ):
            verify_candidate_authority(manifest)

    def test_missing_dependency_and_path_alias_fail_closed(self):
        authority = self.authorities["PLL"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_dependency_tree(authority, root)
            (root / EXPECTED_MODELS["PLL"]).unlink()
            with self.assertRaisesRegex(CandidateAuthorityError, "missing"):
                verify_candidate_authority(authority, root)

        manifest = deepcopy(self.authorities["RG3"].to_mapping())
        path = manifest["dependencies"][0]["path"]
        manifest["dependencies"][0]["path"] = path.replace("/", "//", 1)
        with self.assertRaisesRegex(CandidateAuthorityError, "path alias"):
            verify_candidate_authority(manifest)

    def test_manifest_exact_schema_rejects_future_or_missing_fields(self):
        manifest = deepcopy(self.authorities["DSPB"].to_mapping())
        manifest["future_hash"] = "0" * 64
        with self.assertRaisesRegex(CandidateAuthorityError, "field schema"):
            verify_candidate_authority(manifest)

        manifest = deepcopy(self.authorities["DSPB"].to_mapping())
        manifest.pop("executable_bytes_hex")
        with self.assertRaisesRegex(CandidateAuthorityError, "field schema"):
            verify_candidate_authority(manifest)


if __name__ == "__main__":
    unittest.main()
