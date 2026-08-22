from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

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
    candidate_native_id,
    verify_campaign_authority,
    verify_candidate_authority,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import RG3_POLICY
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import SO3PLLConfig
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_COMMON_PATHS = (
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/output_common.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/screen108.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/candidate_authority.py",
)
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


def _copy_dependency_tree(authority, destination):
    for dependency in authority.dependencies:
        source = REPO_ROOT / dependency.path
        target = destination / dependency.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))


class CandidateAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authorities = {
            name: build_candidate_authority(name) for name in CANDIDATE_NAMES
        }

    def test_native_ids_and_exact_canonical_config_bytes(self):
        expected_ids = {
            "RG3": RG3_POLICY.candidate_id,
            "DSPB": DSPBConfig().candidate_id,
            "PLL": SO3PLLConfig().candidate_id,
        }
        expected_parameters = {
            "RG3": {
                "maximum_pose_interval_ns": RG3_POLICY.maximum_pose_interval_ns,
                "near_pi_margin_rad": RG3_POLICY.near_pi_margin_rad,
                "maximum_rate_change_ratio": RG3_POLICY.maximum_rate_change_ratio,
                "minimum_direction_cosine": RG3_POLICY.minimum_direction_cosine,
                "maximum_acceleration_contribution_ratio": (
                    RG3_POLICY.maximum_acceleration_contribution_ratio
                ),
            },
            "DSPB": dict(DSPBConfig().to_mapping()),
            "PLL": asdict(SO3PLLConfig()),
        }
        expected_parameters["DSPB"].pop("candidate_id")
        for name in CANDIDATE_NAMES:
            with self.subTest(candidate=name):
                self.assertEqual(candidate_native_id(name), expected_ids[name])
                config = candidate_config_bytes(name)
                self.assertEqual(
                    config,
                    canonical_json_bytes(json.loads(config.decode("ascii"))),
                )
                self.assertEqual(
                    hashlib.sha256(config).hexdigest(),
                    candidate_config_sha256(name),
                )
                mapping = candidate_config_mapping(name)
                self.assertEqual(mapping["native_candidate_id"], expected_ids[name])
                self.assertEqual(mapping["candidate"], name)
                self.assertEqual(mapping["parameters"], expected_parameters[name])

    def test_dependency_closure_is_ordered_complete_and_self_sealed(self):
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
                for required in REQUIRED_COMMON_PATHS:
                    self.assertIn(required, paths)
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
                    bytes.fromhex(mapping["config_bytes_hex"]),
                    authority.config_bytes,
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
        with self.assertRaisesRegex(
            CandidateAuthorityError, "manifest order"
        ):
            verify_campaign_authority(reordered)

    def test_mutated_dependency_fails_even_with_original_manifest(self):
        for name in CANDIDATE_NAMES:
            with self.subTest(candidate=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                authority = self.authorities[name]
                _copy_dependency_tree(authority, root)
                adapter = root / EXPECTED_ADAPTERS[name]
                adapter.write_bytes(adapter.read_bytes() + b"\n# mutation\n")
                with self.assertRaisesRegex(
                    CandidateAuthorityError, "source digest differs"
                ):
                    verify_candidate_authority(authority, root)

    def test_new_local_import_changes_the_dependency_closure(self):
        authority = self.authorities["PLL"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_dependency_tree(authority, root)
            injected_relative = (
                "benchmarks/redred_mc_wtb_predictor_stage3/injected_dependency.py"
            )
            injected = root / injected_relative
            injected.write_text("INJECTED = True\n", encoding="utf-8")
            adapter = root / EXPECTED_ADAPTERS["PLL"]
            adapter.write_bytes(
                adapter.read_bytes()
                + b"\nimport benchmarks.redred_mc_wtb_predictor_stage3.injected_dependency\n"
            )
            with self.assertRaisesRegex(
                CandidateAuthorityError, "order or closure differs"
            ):
                verify_candidate_authority(authority, root)

    def test_missing_dependency_fails_closed(self):
        authority = self.authorities["PLL"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_dependency_tree(authority, root)
            (root / EXPECTED_MODELS["PLL"]).unlink()
            with self.assertRaisesRegex(CandidateAuthorityError, "missing"):
                verify_candidate_authority(authority, root)

    def test_duplicate_dependency_row_fails_before_seal(self):
        manifest = deepcopy(self.authorities["DSPB"].to_mapping())
        manifest["dependencies"].insert(1, deepcopy(manifest["dependencies"][0]))
        with self.assertRaisesRegex(CandidateAuthorityError, "duplicated"):
            verify_candidate_authority(manifest)

        reordered = deepcopy(self.authorities["DSPB"].to_mapping())
        reordered["dependencies"][0:2] = reversed(reordered["dependencies"][0:2])
        reordered["dependency_aggregate_sha256"] = canonical_sha256(
            reordered["dependencies"]
        )
        unsigned = dict(reordered)
        unsigned.pop("manifest_sha256")
        reordered["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(
            CandidateAuthorityError, "order or closure differs"
        ):
            verify_candidate_authority(reordered)

    def test_textual_and_filesystem_path_aliases_fail_closed(self):
        manifest = deepcopy(self.authorities["RG3"].to_mapping())
        path = manifest["dependencies"][0]["path"]
        manifest["dependencies"][0]["path"] = path.replace("/", "//", 1)
        with self.assertRaisesRegex(CandidateAuthorityError, "path alias"):
            verify_candidate_authority(manifest)

        authority = self.authorities["RG3"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_dependency_tree(authority, root)
            model = root / EXPECTED_MODELS["RG3"]
            alias = root / "model-alias.py"
            model.rename(alias)
            model.symlink_to(alias)
            with self.assertRaisesRegex(CandidateAuthorityError, "path alias"):
                verify_candidate_authority(authority, root)

    def test_config_bytes_are_authority_not_a_replaceable_digest(self):
        manifest = deepcopy(self.authorities["PLL"].to_mapping())
        config = bytearray.fromhex(manifest["config_bytes_hex"])
        config[-2] = ord(" ")
        manifest["config_bytes_hex"] = bytes(config).hex()
        manifest["config_sha256"] = hashlib.sha256(bytes(config)).hexdigest()
        unsigned = dict(manifest)
        unsigned.pop("manifest_sha256")
        manifest["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(CandidateAuthorityError, "config bytes differ"):
            verify_candidate_authority(manifest)


if __name__ == "__main__":
    unittest.main()
