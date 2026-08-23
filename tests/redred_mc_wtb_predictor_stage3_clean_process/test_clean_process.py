from __future__ import annotations

import ast
import copy
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator, ValidationError

from benchmarks.redred_mc_wtb_predictor_stage3 import clean_process_runner
from benchmarks.redred_mc_wtb_predictor_stage3.clean_process_child import (
    _verify_child_response,
)
from benchmarks.redred_mc_wtb_predictor_stage3.clean_process_runner import (
    CLEAN_PROCESS_RECEIPT_SCHEMA,
    CODE_SIGNING_HOLD,
    EXTERNAL_RUNNER_AUTHORITY_HOLD,
    FILESYSTEM_PUBLICATION_HOLD,
    CleanProcessRunnerError,
    run_clean_process_replay,
    verify_clean_process_receipt,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    build_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.refreeze_v4 import (
    DSPB_CANDIDATE_ID,
    PLL_CANDIDATE_ID,
    RG3_CANDIDATE_ID,
    build_refreeze_v4_contract,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_query_stream import (
    generate_pll_query_stream,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from tests.redred_mc_wtb_predictor_stage3_execution_authority.test_execution_authority import (
    neutral_fixture,
    source_authority,
)
from tests.redred_mc_wtb_predictor_stage3_pll_query_stream.test_pll_query_stream import (
    _clustered_execution,
)
from tests.redred_mc_wtb_predictor_stage3_rg3_output.test_rg3_output import (
    _fixture as _rg3_fixture,
)
from tests.redred_mc_wtb_predictor_stage3_rg3_query_stream.test_rg3_query_stream import (
    _decreasing_event_ids,
    _execution as _rg3_execution,
)


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "benchmarks" / "redred_mc_wtb_predictor_stage3"
RUNNER_PATH = MODULE_ROOT / "clean_process_runner.py"
CHILD_PATH = MODULE_ROOT / "clean_process_child.py"
ORACLE_PATH = MODULE_ROOT / "post_output_oracle_child.py"
SCHEMA_PATH = MODULE_ROOT / "clean_process_receipt.schema.json"


def _execution():
    registry, events, poses = neutral_fixture()
    return build_stage3_execution_input(
        registry,
        events,
        poses,
        source_events_authority=source_authority(),
        repo_root=ROOT,
    )


def _reseal(mapping):
    mapping["aggregate_sha256"] = canonical_sha256({
        key: value for key, value in mapping.items()
        if key != "aggregate_sha256"
    })


def _reseal_manifest(mapping):
    mapping["manifest_sha256"] = canonical_sha256({
        key: value for key, value in mapping.items()
        if key != "manifest_sha256"
    })


def _reseal_rg3_stream(output, reseal_decision=True):
    row = output["windows"][0]["query_rows"][0]
    if reseal_decision:
        row["decision_sha256"] = canonical_sha256({
            key: value for key, value in row.items()
            if key != "decision_sha256"
        })
    window = output["windows"][0]
    window["query_rows_sha256"] = canonical_sha256(window["query_rows"])
    output["windows_sha256"] = canonical_sha256(output["windows"])
    core_keys = (
        "windows", "windows_sha256", "query_event_count",
        "warmup_rows_emitted", "retained_candidate_event_rows",
        "maximum_retained_candidate_pose_count",
    )
    output["replay_sha256"] = canonical_sha256({
        key: output[key] for key in core_keys
    })
    _reseal(output)


def _reseal_pll_stream(output):
    window = output["windows"][0]
    window["query_rows_sha256"] = canonical_sha256(window["query_rows"])
    window["query_transitions_sha256"] = canonical_sha256(
        window["query_transitions"]
    )
    output["windows_sha256"] = canonical_sha256(output["windows"])
    core_keys = (
        "windows", "windows_sha256", "query_event_count",
        "query_transition_count", "warmup_rows_emitted",
        "retained_candidate_event_rows",
        "maximum_retained_fallback_pose_count",
        "maximum_retained_effective_pending_state_count",
    )
    output["replay_sha256"] = canonical_sha256({
        key: output[key] for key in core_keys
    })
    _reseal(output)


class CleanProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.execution = _execution()
        cls.schema = json.loads(SCHEMA_PATH.read_text())
        cls.validator = Draft202012Validator(cls.schema)
        cls.candidates = (
            RG3_CANDIDATE_ID,
            DSPB_CANDIDATE_ID,
            PLL_CANDIDATE_ID,
        )
        cls.receipts = {
            candidate: run_clean_process_replay(cls.execution, candidate)
            for candidate in cls.candidates
        }
        contract = build_refreeze_v4_contract(
            cls.execution, RG3_CANDIDATE_ID
        )
        runtime = clean_process_runner._expected_runtime_manifest()
        request = clean_process_runner._request(
            cls.execution, contract, RG3_CANDIDATE_ID, runtime
        )
        cls.rg3_contract = contract
        cls.runtime = runtime
        cls.rg3_request = request
        cls.valid_child_bytes = clean_process_runner._invoke_child(
            canonical_json_bytes(request), RG3_CANDIDATE_ID
        )

    def test_all_candidates_produce_closed_hold_receipts(self):
        expected_schema = {
            RG3_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.rg3_query_stream/v1",
            DSPB_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.dspb_query_stream/v1",
            PLL_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.pll_query_stream/v1",
        }
        for candidate, receipt in self.receipts.items():
            with self.subTest(candidate=candidate):
                self.validator.validate(receipt)
                self.assertEqual(receipt["schema"], CLEAN_PROCESS_RECEIPT_SCHEMA)
                self.assertEqual(receipt["candidate_output_schema"], expected_schema[candidate])
                self.assertEqual(receipt["status"], "DEVELOPMENT_HOLD")
                self.assertIs(receipt["authority_go"], False)
                self.assertEqual(receipt["warmup_rows_emitted"], 0)
                self.assertNotIn("candidate_output", receipt)
                self.assertNotIn("windows", receipt)
                self.assertNotIn("execution_input", receipt)
                self.assertNotIn(
                    "candidate_output", receipt["post_output_verification"]
                )
                self.assertIs(
                    receipt["post_output_verification"]["authority_go"], False
                )
                oracle = receipt["post_output_verification"]
                self.assertEqual(
                    oracle["external_oracle_release_hold"]["status"], "HOLD"
                )
                self.assertIs(
                    oracle["external_oracle_release_hold"]["authority_go"],
                    False,
                )
                self.assertEqual(oracle["resource_ppa_hold"]["status"], "HOLD")
                self.assertIs(oracle["resource_ppa_hold"]["resource_go"], False)
                self.assertIs(oracle["resource_ppa_hold"]["ppa_go"], False)
                self.assertIs(
                    oracle["filesystem_publication_hold"]["publication_allowed"],
                    False,
                )
                if candidate == PLL_CANDIDATE_ID:
                    self.assertEqual(
                        oracle["provenance_verification"]["status"], "HOLD"
                    )
                self.assertEqual(
                    receipt["post_output_verification_sha256"],
                    receipt["post_output_verification"]["aggregate_sha256"],
                )
                body = dict(receipt)
                supplied = body.pop("aggregate_sha256")
                self.assertEqual(supplied, canonical_sha256(body))

    def test_two_real_children_are_invoked_with_fixed_command_and_environment(self):
        real = clean_process_runner.subprocess.run
        with mock.patch.object(
            clean_process_runner.subprocess, "run", wraps=real
        ) as run:
            receipt = run_clean_process_replay(
                self.execution, RG3_CANDIDATE_ID
            )
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list[:2]:
            command = call.args[0]
            self.assertEqual(command[0], str(Path(sys.executable).resolve()))
            self.assertEqual(command[1:4], ("-I", "-S", "-B"))
            self.assertEqual(command[4], str(CHILD_PATH))
            self.assertEqual(command[5], RG3_CANDIDATE_ID)
            self.assertEqual(call.kwargs["cwd"], str(ROOT))
            self.assertEqual(
                call.kwargs["env"], {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
            )
            self.assertEqual(call.kwargs["timeout"], 120)
            self.assertIs(call.kwargs["close_fds"], True)
        oracle_call = run.call_args_list[2]
        oracle_command = oracle_call.args[0]
        self.assertEqual(oracle_command[0], str(Path(sys.executable).resolve()))
        self.assertEqual(oracle_command[1:4], ("-I", "-S", "-B"))
        self.assertEqual(
            oracle_command[4],
            str(MODULE_ROOT / "post_output_oracle_child.py"),
        )
        self.assertEqual(oracle_command[5], RG3_CANDIDATE_ID)
        self.assertEqual(oracle_call.kwargs["cwd"], str(ROOT))
        self.assertEqual(
            oracle_call.kwargs["env"],
            {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
        )
        self.assertEqual(oracle_call.kwargs["timeout"], 120)
        self.assertIs(oracle_call.kwargs["close_fds"], True)
        evidence = receipt["runtime_isolation_evidence"]
        self.assertEqual(evidence["separate_child_execution_count"], 2)
        self.assertIs(evidence["canonical_child_responses_byte_identical"], True)

    def test_parent_candidate_monkeypatch_and_environment_do_not_cross_boundary(self):
        from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_query_stream

        with mock.patch.object(
            rg3_query_stream,
            "generate_rg3_query_stream",
            side_effect=AssertionError("parent monkeypatch crossed boundary"),
        ) as candidate, mock.patch.dict(
            os.environ,
            {"PYTHONPATH": "/attacker", "PYTHONINSPECT": "1"},
            clear=False,
        ):
            receipt = run_clean_process_replay(
                self.execution, RG3_CANDIDATE_ID
            )
        candidate.assert_not_called()
        self.assertIs(receipt["authority_go"], False)

    def test_no_caller_callable_root_config_or_timeout_surface(self):
        self.assertEqual(
            tuple(inspect.signature(run_clean_process_replay).parameters),
            ("execution_input", "candidate_id"),
        )
        with self.assertRaises(TypeError):
            run_clean_process_replay(  # type: ignore[call-arg]
                self.execution,
                RG3_CANDIDATE_ID,
                timeout=1,
            )

    def test_invalid_execution_and_unknown_candidate_never_spawn(self):
        invalid = copy.deepcopy(self.execution)
        invalid["query_event_count"] += 1
        for execution, candidate in (
            (invalid, RG3_CANDIDATE_ID),
            (self.execution, "CALLER-CANDIDATE"),
        ):
            with self.subTest(candidate=candidate), mock.patch.object(
                clean_process_runner, "_invoke_child"
            ) as child:
                with self.assertRaises(CleanProcessRunnerError):
                    run_clean_process_replay(execution, candidate)
                child.assert_not_called()

    def test_nonzero_stderr_timeout_empty_and_noncanonical_stdout_fail_closed(self):
        completed = (
            subprocess.CompletedProcess((), 1, b"", b""),
            subprocess.CompletedProcess((), 0, b"{}", b"error"),
            subprocess.CompletedProcess((), 0, b"", b""),
            subprocess.CompletedProcess((), 0, b"{}\n\n", b""),
            subprocess.CompletedProcess((), 0, b'{"x": 1}', b""),
        )
        for index, result in enumerate(completed):
            with self.subTest(index=index), mock.patch.object(
                clean_process_runner.subprocess, "run", return_value=result
            ):
                with self.assertRaises(CleanProcessRunnerError):
                    clean_process_runner._invoke_child(b"{}", RG3_CANDIDATE_ID)
        with mock.patch.object(
            clean_process_runner.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(("python",), 120),
        ):
            with self.assertRaises(CleanProcessRunnerError):
                clean_process_runner._invoke_child(b"{}", RG3_CANDIDATE_ID)

    def test_distinct_child_response_bytes_are_rejected_before_receipt(self):
        with mock.patch.object(
            clean_process_runner,
            "_invoke_child",
            side_effect=(self.valid_child_bytes, self.valid_child_bytes + b" "),
        ):
            with self.assertRaisesRegex(CleanProcessRunnerError, "responses differ"):
                run_clean_process_replay(self.execution, RG3_CANDIDATE_ID)

    def test_byte_identical_resealed_forged_candidate_output_is_rejected(self):
        response = json.loads(self.valid_child_bytes.decode("utf-8"))
        output = response["candidate_output"]
        output["caller_extra"] = True
        _reseal(output)
        response["candidate_output_sha256"] = canonical_sha256(output)
        _reseal(response)
        forged = canonical_json_bytes(response)
        with mock.patch.object(
            clean_process_runner,
            "_invoke_child",
            side_effect=(forged, forged),
        ):
            with self.assertRaises(CleanProcessRunnerError):
                run_clean_process_replay(self.execution, RG3_CANDIDATE_ID)

    def test_resealed_core_coordinator_identity_and_schema_output_mutations_fail(self):
        base = json.loads(self.valid_child_bytes.decode("utf-8"))
        mutations = []
        for field, value in (
            ("candidate_id", DSPB_CANDIDATE_ID),
            ("schema", "redred.mc_wtb_predictor_stage3.dspb_query_stream/v1"),
        ):
            response = copy.deepcopy(base)
            output = response["candidate_output"]
            output[field] = value
            _reseal(output)
            response["candidate_output_sha256"] = canonical_sha256(output)
            _reseal(response)
            mutations.append(response)
        for manifest_field, digest_field in (
            ("candidate_safe_core_manifest", "candidate_safe_core_manifest_sha256"),
            ("coordinator_manifest", "coordinator_manifest_sha256"),
        ):
            response = copy.deepcopy(base)
            output = response["candidate_output"]
            manifest = output[manifest_field]
            manifest["files"][0]["sha256"] = "0" * 64
            _reseal_manifest(manifest)
            output[digest_field] = manifest["manifest_sha256"]
            _reseal(output)
            response["candidate_output_sha256"] = canonical_sha256(output)
            _reseal(response)
            mutations.append(response)
        response = copy.deepcopy(base)
        output = response["candidate_output"]
        output["output_authority_hold"]["reason"] = "caller reason"
        _reseal(output)
        response["candidate_output_sha256"] = canonical_sha256(output)
        _reseal(response)
        mutations.append(response)
        for index, response in enumerate(mutations):
            forged = canonical_json_bytes(response)
            with self.subTest(index=index), mock.patch.object(
                clean_process_runner,
                "_invoke_child",
                side_effect=(forged, forged),
            ):
                with self.assertRaises(CleanProcessRunnerError):
                    run_clean_process_replay(self.execution, RG3_CANDIDATE_ID)

    def test_child_rejects_resealed_runtime_and_contract_cross_binding(self):
        runtime = copy.deepcopy(self.rg3_request)
        runtime["expected_runtime_manifest"]["worker_sha256"] = "0" * 64
        runtime["expected_runtime_manifest_sha256"] = canonical_sha256(
            runtime["expected_runtime_manifest"]
        )
        runtime["request_sha256"] = canonical_sha256({
            key: value for key, value in runtime.items()
            if key != "request_sha256"
        })

        crossed = copy.deepcopy(self.rg3_request)
        dspb_contract = build_refreeze_v4_contract(
            self.execution, DSPB_CANDIDATE_ID
        )
        crossed["refreeze_contract"] = dspb_contract
        crossed["refreeze_contract_sha256"] = dspb_contract["aggregate_sha256"]
        crossed["request_sha256"] = canonical_sha256({
            key: value for key, value in crossed.items()
            if key != "request_sha256"
        })
        for request in (runtime, crossed):
            with self.assertRaises(CleanProcessRunnerError):
                clean_process_runner._invoke_child(
                    canonical_json_bytes(request), RG3_CANDIDATE_ID
                )

    def test_direct_child_rejects_noncanonical_and_unknown_dispatch_silently(self):
        command = (
            str(Path(sys.executable).resolve()), "-I", "-S", "-B",
            str(CHILD_PATH), RG3_CANDIDATE_ID,
        )
        for raw, candidate in (
            (b"{}\n\n", RG3_CANDIDATE_ID),
            (b"{}", "CALLER-CANDIDATE"),
        ):
            actual = command[:-1] + (candidate,)
            result = subprocess.run(
                actual,
                input=raw,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(ROOT),
                env={"LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
                timeout=120,
                check=False,
                close_fds=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")

    def test_response_cross_bind_and_holds_are_retained(self):
        response = json.loads(self.valid_child_bytes.decode("utf-8"))
        checked = _verify_child_response(
            response,
            self.execution,
            self.rg3_contract,
            RG3_CANDIDATE_ID,
            self.runtime,
        )
        self.assertEqual(checked, response)
        receipt = self.receipts[RG3_CANDIDATE_ID]
        self.assertEqual(
            receipt["external_runner_authority_hold"],
            EXTERNAL_RUNNER_AUTHORITY_HOLD,
        )
        self.assertEqual(receipt["code_signing_hold"], CODE_SIGNING_HOLD)
        self.assertEqual(
            receipt["filesystem_publication_hold"],
            FILESYSTEM_PUBLICATION_HOLD,
        )
        self.assertEqual(receipt["candidate_domain_hold"]["status"], "HOLD")
        self.assertEqual(receipt["output_authority_hold"]["status"], "HOLD")
        self.assertEqual(
            verify_clean_process_receipt(
                receipt, self.execution, RG3_CANDIDATE_ID
            ),
            receipt["aggregate_sha256"],
        )

    def test_closed_schema_rejects_resealed_go_and_unknown_fields(self):
        Draft202012Validator.check_schema(self.schema)
        receipt = copy.deepcopy(self.receipts[DSPB_CANDIDATE_ID])
        receipt["authority_go"] = True
        receipt["candidate_rows"] = []
        _reseal(receipt)
        with self.assertRaises(ValidationError):
            self.validator.validate(receipt)
        stack = [self.schema]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

        hash_mutation = copy.deepcopy(self.receipts[DSPB_CANDIDATE_ID])
        hash_mutation["candidate_output_aggregate_sha256"] = "0" * 64
        _reseal(hash_mutation)
        self.validator.validate(hash_mutation)
        with self.assertRaises(CleanProcessRunnerError):
            verify_clean_process_receipt(
                hash_mutation, self.execution, DSPB_CANDIDATE_ID
            )

    def test_sources_are_python38_and_do_not_expose_forbidden_apis(self):
        sources = (
            RUNNER_PATH,
            CHILD_PATH,
            ORACLE_PATH,
            Path(__file__),
        )
        for path in sources:
            tree = ast.parse(
                path.read_text(), filename=str(path), feature_version=(3, 8)
            )
            if path != Path(__file__):
                imported = []
                public = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        imported.append(node.module or "")
                    elif isinstance(node, ast.Import):
                        imported.extend(alias.name for alias in node.names)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not node.name.startswith("_"):
                            public.append(node.name)
                if path != ORACLE_PATH:
                    self.assertFalse(any("evaluator" in name for name in imported))
                self.assertFalse(any("label" in name for name in imported))
                self.assertFalse(any("scoring" in name for name in imported))
                self.assertFalse(any(
                    name.startswith(("publish", "write", "score"))
                    for name in public
                ))

    def test_post_output_oracle_rejects_fully_resealed_row_attacks(self):
        base_response = json.loads(self.valid_child_bytes.decode("utf-8"))
        attacks = []
        for field, value in (
            ("world_ray", [0.0, 0.0, 0.0]),
            ("route", "SENSOR_FIXED"),
            ("decision_cycle", 999999999),
            ("used_pose_ids", [999]),
        ):
            output = copy.deepcopy(base_response["candidate_output"])
            output["windows"][0]["query_rows"][0][field] = value
            _reseal_rg3_stream(output)
            attacks.append(output)
        output = copy.deepcopy(base_response["candidate_output"])
        output["windows"][0]["query_rows"][0]["nested_extra"] = True
        _reseal_rg3_stream(output)
        attacks.append(output)
        output = copy.deepcopy(base_response["candidate_output"])
        output["windows"][0]["query_rows"][0]["world_ray"] = [0.0, 0.0, 0.0]
        _reseal_rg3_stream(output, reseal_decision=False)
        attacks.append(output)

        runtime = clean_process_runner._expected_oracle_runtime_manifest()
        child_sha = canonical_sha256(base_response)
        for index, output in enumerate(attacks):
            request = clean_process_runner._oracle_request(
                self.execution, self.rg3_contract, output, child_sha,
                RG3_CANDIDATE_ID, runtime,
            )
            with self.subTest(index=index), self.assertRaises(
                CleanProcessRunnerError
            ):
                clean_process_runner._invoke_oracle(
                    canonical_json_bytes(request), RG3_CANDIDATE_ID
                )

    def test_post_output_oracle_rejects_cross_candidate_and_bad_framing(self):
        response = json.loads(self.valid_child_bytes.decode("utf-8"))
        output = response["candidate_output"]
        runtime = clean_process_runner._expected_oracle_runtime_manifest()
        dspb_contract = build_refreeze_v4_contract(
            self.execution, DSPB_CANDIDATE_ID
        )
        crossed = clean_process_runner._oracle_request(
            self.execution, dspb_contract, output,
            canonical_sha256(response), DSPB_CANDIDATE_ID, runtime,
        )
        with self.assertRaises(CleanProcessRunnerError):
            clean_process_runner._invoke_oracle(
                canonical_json_bytes(crossed), DSPB_CANDIDATE_ID
            )

        completed = (
            subprocess.CompletedProcess((), 1, b"", b""),
            subprocess.CompletedProcess((), 0, b"{}\n", b"error"),
            subprocess.CompletedProcess((), 0, b"", b""),
            subprocess.CompletedProcess((), 0, b"{}\n\n", b""),
            subprocess.CompletedProcess((), 0, b'{"x": 1}', b""),
        )
        for index, result in enumerate(completed):
            with self.subTest(index=index), mock.patch.object(
                clean_process_runner.subprocess, "run", return_value=result
            ):
                with self.assertRaises(CleanProcessRunnerError):
                    clean_process_runner._invoke_oracle(
                        b"{}", RG3_CANDIDATE_ID
                    )
        with mock.patch.object(
            clean_process_runner.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(("python",), 120),
        ):
            with self.assertRaises(CleanProcessRunnerError):
                clean_process_runner._invoke_oracle(b"{}", RG3_CANDIDATE_ID)

    def test_pll_oracle_rejects_boundary_extra_and_resealed_transition_cycle(self):
        execution = _clustered_execution()
        nominal = run_clean_process_replay(execution, PLL_CANDIDATE_ID)
        self.assertTrue(
            nominal["post_output_verification"]["query_projection_verified"]
        )
        contract = build_refreeze_v4_contract(execution, PLL_CANDIDATE_ID)
        base = generate_pll_query_stream(execution)
        attacks = []

        output = copy.deepcopy(base)
        boundary = output["windows"][0]["first_query_state_boundary"]
        boundary["nested_extra"] = True
        boundary["boundary_sha256"] = canonical_sha256({
            key: value for key, value in boundary.items()
            if key != "boundary_sha256"
        })
        _reseal_pll_stream(output)
        attacks.append(output)

        output = copy.deepcopy(base)
        transition = output["windows"][0]["query_transitions"][0]
        transition["commit_cycle"] += 1
        transition["transition_sha256"] = canonical_sha256({
            key: value for key, value in transition.items()
            if key != "transition_sha256"
        })
        _reseal_pll_stream(output)
        attacks.append(output)

        runtime = clean_process_runner._expected_oracle_runtime_manifest()
        for index, output in enumerate(attacks):
            request = clean_process_runner._oracle_request(
                execution, contract, output, "a" * 64,
                PLL_CANDIDATE_ID, runtime,
            )
            with self.subTest(index=index), self.assertRaises(
                CleanProcessRunnerError
            ):
                clean_process_runner._invoke_oracle(
                    canonical_json_bytes(request), PLL_CANDIDATE_ID
                )

    def test_post_output_oracle_preserves_decreasing_unique_rg3_ids(self):
        registry, events, poses, _ = _rg3_fixture()
        decreasing = _decreasing_event_ids(events)
        execution = _rg3_execution(registry, decreasing, poses)
        receipt = run_clean_process_replay(execution, RG3_CANDIDATE_ID)
        self.assertEqual(
            receipt["post_output_verification"]["verification_mode"],
            "exact_ordered_query_row_projection",
        )
        self.assertIs(receipt["authority_go"], False)


if __name__ == "__main__":
    unittest.main()
