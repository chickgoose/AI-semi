from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator, ValidationError

from benchmarks.redred_mc_wtb_predictor_stage3 import refreeze_v4
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    build_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.refreeze_v4 import (
    CALLABLE_RUNTIME_ISOLATION_HOLD,
    CONTRACT_CAPABILITIES,
    DSPB_CANDIDATE_ID,
    PLL_CANDIDATE_ID,
    REFREEZE_V4_CONTRACT_SCHEMA,
    RG3_CANDIDATE_ID,
    RefreezeV4ContractError,
    build_refreeze_v4_contract,
    verify_dspb_refreeze_v4_contract,
    verify_pll_refreeze_v4_contract,
    verify_refreeze_v4_contract,
    verify_rg3_refreeze_v4_contract,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from tests.redred_mc_wtb_predictor_stage3_execution_authority.test_execution_authority import (
    neutral_fixture,
    source_authority,
)


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "benchmarks" / "redred_mc_wtb_predictor_stage3" / "refreeze_v4.py"
SCHEMA_PATH = MODULE.with_name("refreeze_v4_contract.schema.json")


def _execution():
    registry, events, poses = neutral_fixture()
    return build_stage3_execution_input(
        registry,
        events,
        poses,
        source_events_authority=source_authority(),
        repo_root=ROOT,
    )


def _reseal_manifest(manifest):
    manifest["manifest_sha256"] = canonical_sha256({
        key: value for key, value in manifest.items()
        if key != "manifest_sha256"
    })


def _reseal_contract(contract):
    candidate = contract["candidate_manifest"]
    _reseal_manifest(candidate)
    contract["candidate_manifest_sha256"] = candidate["manifest_sha256"]
    contract["aggregate_sha256"] = canonical_sha256({
        key: value for key, value in contract.items()
        if key != "aggregate_sha256"
    })


class RefreezeV4ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.execution = _execution()
        cls.schema = json.loads(SCHEMA_PATH.read_text())
        cls.validator = Draft202012Validator(cls.schema)
        cls.cases = (
            (RG3_CANDIDATE_ID, verify_rg3_refreeze_v4_contract),
            (DSPB_CANDIDATE_ID, verify_dspb_refreeze_v4_contract),
            (PLL_CANDIDATE_ID, verify_pll_refreeze_v4_contract),
        )

    def test_three_pinned_candidates_build_validate_and_verify(self):
        expected_schemas = {
            RG3_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.rg3_query_stream/v1",
            DSPB_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.dspb_query_stream/v1",
            PLL_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.pll_query_stream/v1",
        }
        for candidate_id, verifier in self.cases:
            with self.subTest(candidate=candidate_id):
                first = build_refreeze_v4_contract(self.execution, candidate_id)
                second = build_refreeze_v4_contract(self.execution, candidate_id)
                self.assertEqual(first, second)
                self.validator.validate(first)
                self.assertEqual(first["schema"], REFREEZE_V4_CONTRACT_SCHEMA)
                self.assertEqual(first["authority_decision"], "HOLD")
                self.assertEqual(
                    first["candidate_manifest"]["candidate_schema"],
                    expected_schemas[candidate_id],
                )
                self.assertEqual(
                    first["callable_runtime_isolation_hold"],
                    CALLABLE_RUNTIME_ISOLATION_HOLD,
                )
                self.assertEqual(first["capabilities"], CONTRACT_CAPABILITIES)
                self.assertEqual(
                    verifier(first, self.execution), first["aggregate_sha256"]
                )
                self.assertEqual(
                    verify_refreeze_v4_contract(
                        first, self.execution, candidate_id
                    ),
                    first["aggregate_sha256"],
                )

    def test_exact_pinned_dependency_and_manifest_hashes(self):
        expected = {
            RG3_CANDIDATE_ID: (
                "65f62f1d72f65d0915f5c89a6954e65e07758ba769b504cd419d36cf90aff173",
                "ef9a6b358718b0794dd0f25188a0af0b5b11d40e89cc8749b9321b7ef4bb0cc9",
            ),
            DSPB_CANDIDATE_ID: (
                "a06e0381bc8caa26c444c49cc7affac2d28e2bece95c4ba289310366bff4beab",
                "bf509e116fe13021101ea0b372f8e27e743cfc22a393ac40395165ad790b7923",
            ),
            PLL_CANDIDATE_ID: (
                "20de2f3e0f003d5fd929fd6b76f3aa9b2d1c0304af02bfeb6b68805980bd5c18",
                "2318c90e5b58a7b1603e56d854d93bf06a919f664bedc71a3c5423bd82b419c8",
            ),
        }
        for candidate_id, _ in self.cases:
            manifest = build_refreeze_v4_contract(
                self.execution, candidate_id
            )["candidate_manifest"]
            core = manifest["candidate_safe_core_manifest"]
            coordinator = manifest["coordinator_manifest"]
            self.assertEqual(
                (core["manifest_sha256"], coordinator["manifest_sha256"]),
                expected[candidate_id],
            )
            self.assertEqual(
                coordinator["candidate_safe_core_manifest_sha256"],
                core["manifest_sha256"],
            )
            for executable_manifest in (core, coordinator):
                body = dict(executable_manifest)
                supplied = body.pop("manifest_sha256")
                self.assertEqual(supplied, canonical_sha256(body))
                for row in executable_manifest["files"]:
                    import hashlib
                    self.assertEqual(
                        row["sha256"],
                        hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest(),
                    )

    def test_config_core_domain_and_runtime_resealed_mutations_fail(self):
        base = build_refreeze_v4_contract(self.execution, DSPB_CANDIDATE_ID)
        mutations = []

        config = copy.deepcopy(base)
        config_manifest = config["candidate_manifest"]["config_manifest"]
        config_manifest["configuration"]["max_horizon_ns"] = 1
        config_manifest["configuration_canonical_sha256"] = canonical_sha256(
            config_manifest["configuration"]
        )
        _reseal_manifest(config_manifest)
        config["candidate_manifest"]["config_manifest_sha256"] = config_manifest[
            "manifest_sha256"
        ]
        _reseal_contract(config)
        mutations.append(config)

        core = copy.deepcopy(base)
        core_manifest = core["candidate_manifest"]["candidate_safe_core_manifest"]
        core_manifest["files"][0]["sha256"] = "0" * 64
        _reseal_manifest(core_manifest)
        core["candidate_manifest"]["candidate_safe_core_manifest_sha256"] = (
            core_manifest["manifest_sha256"]
        )
        _reseal_contract(core)
        mutations.append(core)

        domain = copy.deepcopy(base)
        domain_hold = domain["candidate_manifest"]["candidate_domain_hold"]
        domain_hold["maximum_window_pose_occurrences"] = 255
        domain["candidate_manifest"]["candidate_domain_hold_sha256"] = (
            canonical_sha256(domain_hold)
        )
        _reseal_contract(domain)
        mutations.append(domain)

        runtime = copy.deepcopy(base)
        runtime["callable_runtime_isolation_hold"]["authority_go"] = True
        runtime["aggregate_sha256"] = canonical_sha256({
            key: value for key, value in runtime.items()
            if key != "aggregate_sha256"
        })
        mutations.append(runtime)

        authority_go = copy.deepcopy(base)
        authority_go["authority_decision"] = "GO"
        authority_go["aggregate_sha256"] = canonical_sha256({
            key: value for key, value in authority_go.items()
            if key != "aggregate_sha256"
        })
        mutations.append(authority_go)

        candidate_schema = copy.deepcopy(base)
        candidate_schema["candidate_manifest"]["candidate_schema"] = (
            "redred.mc_wtb_predictor_stage3.rg3_query_stream/v1"
        )
        _reseal_contract(candidate_schema)
        mutations.append(candidate_schema)

        for index, changed in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(RefreezeV4ContractError):
                verify_refreeze_v4_contract(
                    changed, self.execution, DSPB_CANDIDATE_ID
                )

    def test_unknown_candidate_and_wrong_candidate_verifier_fail(self):
        with self.assertRaisesRegex(RefreezeV4ContractError, "not preauthorized"):
            build_refreeze_v4_contract(self.execution, "CALLER-CANDIDATE")
        rg3 = build_refreeze_v4_contract(self.execution, RG3_CANDIDATE_ID)
        dspb = build_refreeze_v4_contract(self.execution, DSPB_CANDIDATE_ID)
        with self.assertRaisesRegex(RefreezeV4ContractError, "not DSPB"):
            verify_dspb_refreeze_v4_contract(rg3, self.execution)
        with self.assertRaisesRegex(
            RefreezeV4ContractError, "does not match expectation"
        ):
            verify_refreeze_v4_contract(
                dspb, self.execution, RG3_CANDIDATE_ID
            )

    def test_invalid_v3_fails_before_candidate_manifest_derivation(self):
        invalid = copy.deepcopy(self.execution)
        invalid["query_event_count"] += 1
        with mock.patch.object(refreeze_v4, "_candidate_manifest") as candidate:
            with self.assertRaises(RefreezeV4ContractError):
                build_refreeze_v4_contract(invalid, RG3_CANDIDATE_ID)
            candidate.assert_not_called()

    def test_fixed_repository_root_is_not_caller_controlled(self):
        real = refreeze_v4.verify_stage3_execution_input
        with mock.patch.object(
            refreeze_v4, "verify_stage3_execution_input", wraps=real
        ) as verifier:
            build_refreeze_v4_contract(self.execution, RG3_CANDIDATE_ID)
        self.assertEqual(verifier.call_args.kwargs["repo_root"], ROOT)
        self.assertEqual(
            tuple(inspect.signature(build_refreeze_v4_contract).parameters),
            ("execution_input", "candidate_id"),
        )

    def test_closed_schema_and_verifier_reject_unknown_fields(self):
        Draft202012Validator.check_schema(self.schema)
        stack = [self.schema]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        changed = build_refreeze_v4_contract(self.execution, PLL_CANDIDATE_ID)
        changed["candidate_result"] = {}
        changed["aggregate_sha256"] = canonical_sha256({
            key: value for key, value in changed.items()
            if key != "aggregate_sha256"
        })
        with self.assertRaises(ValidationError):
            self.validator.validate(changed)
        with self.assertRaises(RefreezeV4ContractError):
            verify_refreeze_v4_contract(
                changed, self.execution, PLL_CANDIDATE_ID
            )

    def test_clean_import_and_source_have_no_candidate_callable_surface(self):
        program = "\n".join((
            "import json,sys",
            "import benchmarks.redred_mc_wtb_predictor_stage3.refreeze_v4 as m",
            "blocked=('rg3_query_stream','dspb_query_stream','pll_query_stream')",
            "print(json.dumps(sorted(k for k in sys.modules if any(x in k for x in blocked))))",
        ))
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), [])
        source = MODULE.read_text()
        tree = ast.parse(source, feature_version=(3, 8))
        imported = []
        public_functions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    public_functions.append(node.name)
        self.assertFalse(any("query_stream" in name for name in imported))
        self.assertFalse(any("evaluator" in name for name in imported))
        self.assertFalse(any("label" in name for name in imported))
        self.assertNotIn("Callable", source)
        self.assertNotIn("subprocess", imported)
        self.assertFalse(any(
            name.startswith(("run", "generate", "publish", "write", "score"))
            for name in public_functions
        ))
        self.assertNotIn("build_refreeze_v4_in_memory", source)

    def test_parent_candidate_monkeypatch_is_neither_called_nor_authorized(self):
        from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_query_stream

        with mock.patch.object(
            rg3_query_stream,
            "generate_rg3_query_stream",
            side_effect=AssertionError("candidate callable must not run"),
        ) as candidate_callable:
            contract = build_refreeze_v4_contract(
                self.execution, RG3_CANDIDATE_ID
            )
            verify_refreeze_v4_contract(
                contract, self.execution, RG3_CANDIDATE_ID
            )
        candidate_callable.assert_not_called()
        self.assertEqual(contract["authority_decision"], "HOLD")
        self.assertIs(
            contract["callable_runtime_isolation_hold"]["authority_go"], False
        )


if __name__ == "__main__":
    unittest.main()
