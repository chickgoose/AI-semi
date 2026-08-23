"""Fixed-dispatch clean-process replay coordinator for refreeze v4.

This development-only coordinator executes no caller-supplied callable and
writes no result file.  It runs the fixed worker twice under the resolved
``sys.executable`` with isolated/no-site flags, requires byte-identical
canonical responses, discards candidate rows, and returns a compact HOLD
receipt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping

from benchmarks.redred_mc_wtb_predictor_stage3.clean_process_child import (
    CLEAN_PROCESS_REQUEST_SCHEMA,
    CleanProcessChildError,
    _verify_child_response,
)
from benchmarks.redred_mc_wtb_predictor_stage3.post_output_oracle_child import (
    EXTERNAL_ORACLE_RELEASE_HOLD,
    FILESYSTEM_PUBLICATION_HOLD as ORACLE_FILESYSTEM_PUBLICATION_HOLD,
    POST_OUTPUT_RECEIPT_SCHEMA,
    POST_OUTPUT_REQUEST_SCHEMA,
    RESOURCE_PPA_HOLD,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.refreeze_v4 import (
    DSPB_CANDIDATE_ID,
    PLL_CANDIDATE_ID,
    RG3_CANDIDATE_ID,
    build_refreeze_v4_contract,
    verify_refreeze_v4_contract,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


CLEAN_PROCESS_RECEIPT_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.clean_process_receipt/v2"
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WORKER_PATH = Path(__file__).resolve().with_name("clean_process_child.py")
_ORACLE_WORKER_PATH = Path(__file__).resolve().with_name(
    "post_output_oracle_child.py"
)
_CHILD_TIMEOUT_SECONDS = 120
_SANITIZED_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
_CANDIDATE_IDS = frozenset((
    RG3_CANDIDATE_ID, DSPB_CANDIDATE_ID, PLL_CANDIDATE_ID,
))
_RECEIPT_FIELDS = frozenset((
    "schema", "status", "authority_go", "candidate_id",
    "candidate_output_schema", "execution_input_aggregate_sha256",
    "neutral_input_sha256", "ordered_query_event_ids_sha256",
    "refreeze_contract_sha256", "candidate_manifest_sha256",
    "config_manifest_sha256", "candidate_safe_core_manifest_sha256",
    "coordinator_manifest_sha256", "runtime_manifest",
    "runtime_manifest_sha256", "child_response_sha256",
    "candidate_output_sha256", "candidate_output_aggregate_sha256",
    "candidate_replay_sha256", "query_path_sha256", "windows_sha256",
    "query_event_count", "warmup_rows_emitted",
    "runtime_isolation_evidence", "external_runner_authority_hold",
    "code_signing_hold", "candidate_domain_hold",
    "candidate_domain_hold_sha256", "output_authority_hold",
    "filesystem_publication_hold", "post_output_verification",
    "post_output_verification_sha256", "aggregate_sha256",
))
_ORACLE_RECEIPT_FIELDS = frozenset((
    "schema", "status", "authority_go", "candidate_id",
    "candidate_output_schema", "execution_input_aggregate_sha256",
    "refreeze_contract_sha256", "candidate_output_sha256",
    "candidate_output_aggregate_sha256", "candidate_child_response_sha256",
    "runtime_manifest", "runtime_manifest_sha256", "verification_mode",
    "batch_output_schema", "batch_output_aggregate_sha256",
    "batch_query_projection_sha256", "candidate_query_projection_sha256",
    "window_count", "query_event_count", "all_nested_self_seals_verified",
    "query_projection_verified", "provenance_verification",
    "output_authority_hold", "external_oracle_release_hold",
    "resource_ppa_hold", "filesystem_publication_hold", "aggregate_sha256",
))

EXTERNAL_RUNNER_AUTHORITY_HOLD = {
    "status": "HOLD",
    "authority_go": False,
    "reason": (
        "the clean-process runner and worker do not yet have an externally "
        "preauthorized release-manifest digest"
    ),
}
CODE_SIGNING_HOLD = {
    "status": "HOLD",
    "authority_go": False,
    "reason": "the Python executable and repository sources are hashed but not code-signed",
}
FILESYSTEM_PUBLICATION_HOLD = {
    "status": "HOLD",
    "publication_allowed": False,
    "reason": "candidate output is verified in memory and no publication API exists",
}
RUNTIME_ISOLATION_EVIDENCE = {
    "status": "PASS",
    "fixed_sys_executable": True,
    "isolated_python": True,
    "no_site": True,
    "dont_write_bytecode": True,
    "sanitized_environment": True,
    "fixed_repository_root_and_cwd": True,
    "fixed_candidate_id_dispatch": True,
    "separate_child_execution_count": 2,
    "canonical_child_responses_byte_identical": True,
}


class CleanProcessRunnerError(ValueError):
    """The parent rejected input, process behavior, replay, or evidence."""


def _snapshot(value: object, where: str) -> Mapping[str, object]:
    try:
        result = json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CleanProcessRunnerError("%s is not canonical JSON" % where) from exc
    if not isinstance(result, dict):
        raise CleanProcessRunnerError("%s must be an object" % where)
    return result


def _expected_runtime_manifest() -> Mapping[str, object]:
    executable = Path(sys.executable).resolve()
    return {
        "schema": "redred.mc_wtb_predictor_stage3.clean_process_runtime/v1",
        "python_executable_path": str(executable),
        "python_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_executable_size_bytes": executable.stat().st_size,
        "python_version": sys.version,
        "python_implementation": sys.implementation.name,
        "python_cache_tag": sys.implementation.cache_tag,
        "isolated": True,
        "no_site": True,
        "dont_write_bytecode": True,
        "repository_root": str(_REPOSITORY_ROOT),
        "worker_path": str(_WORKER_PATH),
        "worker_sha256": hashlib.sha256(_WORKER_PATH.read_bytes()).hexdigest(),
    }


def _expected_oracle_runtime_manifest() -> Mapping[str, object]:
    executable = Path(sys.executable).resolve()
    return {
        "schema": "redred.mc_wtb_predictor_stage3.post_output_oracle_runtime/v1",
        "python_executable_path": str(executable),
        "python_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_executable_size_bytes": executable.stat().st_size,
        "python_version": sys.version,
        "python_implementation": sys.implementation.name,
        "python_cache_tag": sys.implementation.cache_tag,
        "isolated": True,
        "no_site": True,
        "dont_write_bytecode": True,
        "repository_root": str(_REPOSITORY_ROOT),
        "worker_path": str(_ORACLE_WORKER_PATH),
        "worker_sha256": hashlib.sha256(_ORACLE_WORKER_PATH.read_bytes()).hexdigest(),
    }


def _request(
    execution: Mapping[str, object],
    contract: Mapping[str, object],
    candidate_id: str,
    runtime: Mapping[str, object],
) -> Mapping[str, object]:
    body = {
        "schema": CLEAN_PROCESS_REQUEST_SCHEMA,
        "expected_candidate_id": candidate_id,
        "execution_input": execution,
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "refreeze_contract": contract,
        "refreeze_contract_sha256": contract["aggregate_sha256"],
        "expected_runtime_manifest": runtime,
        "expected_runtime_manifest_sha256": canonical_sha256(runtime),
    }
    return dict(body, request_sha256=canonical_sha256(body))


def _invoke_child(request_bytes: bytes, candidate_id: str) -> bytes:
    executable = str(Path(sys.executable).resolve())
    command = (
        executable,
        "-I",
        "-S",
        "-B",
        str(_WORKER_PATH),
        candidate_id,
    )
    try:
        result = subprocess.run(
            command,
            input=request_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_REPOSITORY_ROOT),
            env=dict(_SANITIZED_ENVIRONMENT),
            timeout=_CHILD_TIMEOUT_SECONDS,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanProcessRunnerError("clean child failed or timed out") from exc
    if result.returncode != 0:
        raise CleanProcessRunnerError("clean child returned nonzero")
    if result.stderr != b"":
        raise CleanProcessRunnerError("clean child wrote stderr")
    if not result.stdout:
        raise CleanProcessRunnerError("clean child wrote empty stdout")
    try:
        parsed = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CleanProcessRunnerError("clean child stdout is not JSON") from exc
    if canonical_json_bytes(parsed) != result.stdout:
        raise CleanProcessRunnerError(
            "clean child stdout is trailing or noncanonical"
        )
    return result.stdout


def _oracle_request(
    execution: Mapping[str, object], contract: Mapping[str, object],
    output: Mapping[str, object], child_response_sha256: str,
    candidate_id: str, runtime: Mapping[str, object],
) -> Mapping[str, object]:
    body = {
        "schema": POST_OUTPUT_REQUEST_SCHEMA,
        "candidate_id": candidate_id,
        "execution_input": execution,
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "refreeze_contract": contract,
        "refreeze_contract_sha256": contract["aggregate_sha256"],
        "candidate_output": output,
        "candidate_output_sha256": canonical_sha256(output),
        "candidate_output_aggregate_sha256": output["aggregate_sha256"],
        "candidate_child_response_sha256": child_response_sha256,
        "expected_runtime_manifest": runtime,
        "expected_runtime_manifest_sha256": canonical_sha256(runtime),
    }
    return dict(body, request_sha256=canonical_sha256(body))


def _invoke_oracle(request_bytes: bytes, candidate_id: str) -> bytes:
    executable = str(Path(sys.executable).resolve())
    command = (
        executable, "-I", "-S", "-B", str(_ORACLE_WORKER_PATH), candidate_id,
    )
    try:
        result = subprocess.run(
            command,
            input=request_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_REPOSITORY_ROOT),
            env=dict(_SANITIZED_ENVIRONMENT),
            timeout=_CHILD_TIMEOUT_SECONDS,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanProcessRunnerError("post-output oracle failed or timed out") from exc
    if result.returncode != 0:
        raise CleanProcessRunnerError("post-output oracle returned nonzero")
    if result.stderr != b"":
        raise CleanProcessRunnerError("post-output oracle wrote stderr")
    if not result.stdout:
        raise CleanProcessRunnerError("post-output oracle wrote empty stdout")
    try:
        parsed = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CleanProcessRunnerError("post-output oracle stdout is not JSON") from exc
    if canonical_json_bytes(parsed) != result.stdout:
        raise CleanProcessRunnerError(
            "post-output oracle stdout is trailing or noncanonical"
        )
    return result.stdout


def _verify_oracle_receipt(
    value: object, execution: Mapping[str, object],
    contract: Mapping[str, object], output: Mapping[str, object],
    child_response_sha256: str, candidate_id: str,
    expected_runtime: Mapping[str, object],
) -> Mapping[str, object]:
    receipt = _snapshot(value, "post-output oracle receipt")
    if frozenset(receipt) != _ORACLE_RECEIPT_FIELDS:
        raise CleanProcessRunnerError("post-output oracle receipt fields are not exact")
    body = {
        key: item for key, item in receipt.items() if key != "aggregate_sha256"
    }
    if receipt["aggregate_sha256"] != canonical_sha256(body):
        raise CleanProcessRunnerError("post-output oracle receipt seal differs")
    candidate = contract["candidate_manifest"]
    expected_mode = {
        RG3_CANDIDATE_ID: "exact_ordered_query_row_projection",
        DSPB_CANDIDATE_ID: "exact_ordered_query_row_projection",
        PLL_CANDIDATE_ID: (
            "published_semantics_plus_dependency_count_and_direct_anchor"
        ),
    }[candidate_id]
    expected_batch_schema = {
        RG3_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.rg3_output/v2",
        DSPB_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.dspb_output/v2",
        PLL_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.pll_output/v2",
    }[candidate_id]
    if (
        receipt["schema"] != POST_OUTPUT_RECEIPT_SCHEMA
        or receipt["status"] != "DEVELOPMENT_HOLD"
        or receipt["authority_go"] is not False
        or receipt["candidate_id"] != candidate_id
        or receipt["candidate_output_schema"] != output["schema"]
        or receipt["execution_input_aggregate_sha256"] != execution["aggregate_sha256"]
        or receipt["refreeze_contract_sha256"] != contract["aggregate_sha256"]
        or receipt["candidate_output_sha256"] != canonical_sha256(output)
        or receipt["candidate_output_aggregate_sha256"] != output["aggregate_sha256"]
        or receipt["candidate_child_response_sha256"] != child_response_sha256
        or receipt["runtime_manifest"] != expected_runtime
        or receipt["runtime_manifest_sha256"] != canonical_sha256(expected_runtime)
        or receipt["verification_mode"] != expected_mode
        or receipt["batch_output_schema"] != expected_batch_schema
        or receipt["window_count"] != execution["window_count"]
        or receipt["query_event_count"] != execution["query_event_count"]
        or receipt["all_nested_self_seals_verified"] is not True
        or receipt["query_projection_verified"] is not True
        or receipt["output_authority_hold"] != output["output_authority_hold"]
        or receipt["external_oracle_release_hold"] != EXTERNAL_ORACLE_RELEASE_HOLD
        or receipt["resource_ppa_hold"] != RESOURCE_PPA_HOLD
        or receipt["filesystem_publication_hold"]
        != ORACLE_FILESYSTEM_PUBLICATION_HOLD
        or receipt["candidate_output_schema"] != candidate["candidate_schema"]
    ):
        raise CleanProcessRunnerError("post-output oracle receipt binding differs")
    if (
        candidate_id != PLL_CANDIDATE_ID
        and receipt["batch_query_projection_sha256"]
        != receipt["candidate_query_projection_sha256"]
    ):
        raise CleanProcessRunnerError("exact batch projection digest differs")
    provenance = receipt["provenance_verification"]
    if candidate_id == PLL_CANDIDATE_ID:
        if provenance != output["batch_provenance_equivalence_hold"]:
            raise CleanProcessRunnerError("PLL provenance HOLD differs")
    elif provenance != {
        "status": "PASS", "mode": "exact_ordered_query_row_projection"
    }:
        raise CleanProcessRunnerError("exact batch projection evidence differs")
    return receipt


def run_clean_process_replay(
    execution_input: object,
    candidate_id: object,
) -> Mapping[str, object]:
    """Run two fixed clean children and return only a compact HOLD receipt."""

    if not isinstance(candidate_id, str) or candidate_id not in _CANDIDATE_IDS:
        raise CleanProcessRunnerError("candidate ID is not fixed dispatch")
    execution = _snapshot(execution_input, "execution input")
    try:
        verify_stage3_execution_input(
            execution,
            expected_aggregate_sha256=execution.get("aggregate_sha256"),
            repo_root=_REPOSITORY_ROOT,
        )
        contract = _snapshot(
            build_refreeze_v4_contract(execution, candidate_id),
            "refreeze contract",
        )
        verify_refreeze_v4_contract(contract, execution, candidate_id)
        runtime = _snapshot(_expected_runtime_manifest(), "runtime manifest")
        request = _request(execution, contract, candidate_id, runtime)
        request_bytes = canonical_json_bytes(request)
        first_bytes = _invoke_child(request_bytes, candidate_id)
        second_bytes = _invoke_child(request_bytes, candidate_id)
    except CleanProcessRunnerError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise CleanProcessRunnerError("clean-process replay failed: %s" % exc) from exc
    if first_bytes != second_bytes:
        raise CleanProcessRunnerError("clean child responses differ")
    try:
        first = json.loads(first_bytes.decode("utf-8"))
        second = json.loads(second_bytes.decode("utf-8"))
        first_checked = _verify_child_response(
            first, execution, contract, candidate_id, runtime
        )
        _verify_child_response(second, execution, contract, candidate_id, runtime)
    except (CleanProcessChildError, KeyError, TypeError, ValueError) as exc:
        raise CleanProcessRunnerError("clean child response failed: %s" % exc) from exc

    output = first_checked["candidate_output"]
    child_response_sha256 = canonical_sha256(first_checked)
    try:
        oracle_runtime = _snapshot(
            _expected_oracle_runtime_manifest(), "post-output oracle runtime"
        )
        oracle_request = _oracle_request(
            execution, contract, output, child_response_sha256,
            candidate_id, oracle_runtime,
        )
        oracle_bytes = _invoke_oracle(
            canonical_json_bytes(oracle_request), candidate_id
        )
        oracle_value = json.loads(oracle_bytes.decode("utf-8"))
        oracle_receipt = _verify_oracle_receipt(
            oracle_value, execution, contract, output,
            child_response_sha256, candidate_id, oracle_runtime,
        )
    except CleanProcessRunnerError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise CleanProcessRunnerError(
            "post-output oracle verification failed: %s" % exc
        ) from exc
    candidate = contract["candidate_manifest"]
    body = {
        "schema": CLEAN_PROCESS_RECEIPT_SCHEMA,
        "status": "DEVELOPMENT_HOLD",
        "authority_go": False,
        "candidate_id": candidate_id,
        "candidate_output_schema": output["schema"],
        "execution_input_aggregate_sha256": execution["aggregate_sha256"],
        "neutral_input_sha256": execution["neutral_input_sha256"],
        "ordered_query_event_ids_sha256": execution[
            "ordered_query_event_ids_sha256"
        ],
        "refreeze_contract_sha256": contract["aggregate_sha256"],
        "candidate_manifest_sha256": candidate["manifest_sha256"],
        "config_manifest_sha256": candidate["config_manifest_sha256"],
        "candidate_safe_core_manifest_sha256": candidate[
            "candidate_safe_core_manifest_sha256"
        ],
        "coordinator_manifest_sha256": candidate[
            "coordinator_manifest_sha256"
        ],
        "runtime_manifest": runtime,
        "runtime_manifest_sha256": canonical_sha256(runtime),
        "child_response_sha256": child_response_sha256,
        "candidate_output_sha256": first_checked["candidate_output_sha256"],
        "candidate_output_aggregate_sha256": output["aggregate_sha256"],
        "candidate_replay_sha256": output["replay_sha256"],
        "query_path_sha256": output["query_path_sha256"],
        "windows_sha256": output["windows_sha256"],
        "query_event_count": output["query_event_count"],
        "warmup_rows_emitted": output["warmup_rows_emitted"],
        "runtime_isolation_evidence": dict(RUNTIME_ISOLATION_EVIDENCE),
        "external_runner_authority_hold": dict(
            EXTERNAL_RUNNER_AUTHORITY_HOLD
        ),
        "code_signing_hold": dict(CODE_SIGNING_HOLD),
        "candidate_domain_hold": candidate["candidate_domain_hold"],
        "candidate_domain_hold_sha256": candidate[
            "candidate_domain_hold_sha256"
        ],
        "output_authority_hold": output["output_authority_hold"],
        "filesystem_publication_hold": dict(FILESYSTEM_PUBLICATION_HOLD),
        "post_output_verification": oracle_receipt,
        "post_output_verification_sha256": oracle_receipt["aggregate_sha256"],
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def verify_clean_process_receipt(
    receipt: object,
    execution_input: object,
    candidate_id: object,
) -> str:
    """Re-run the fixed clean replay and require an exact receipt match."""

    if not isinstance(candidate_id, str) or candidate_id not in _CANDIDATE_IDS:
        raise CleanProcessRunnerError("candidate ID is not fixed dispatch")
    checked = _snapshot(receipt, "clean-process receipt")
    if frozenset(checked) != _RECEIPT_FIELDS:
        raise CleanProcessRunnerError("clean-process receipt fields are not exact")
    body = {
        key: value for key, value in checked.items() if key != "aggregate_sha256"
    }
    if checked["aggregate_sha256"] != canonical_sha256(body):
        raise CleanProcessRunnerError("clean-process receipt seal differs")
    reproduced = run_clean_process_replay(execution_input, candidate_id)
    if canonical_json_bytes(checked) != canonical_json_bytes(reproduced):
        raise CleanProcessRunnerError("clean-process receipt differs from replay")
    return checked["aggregate_sha256"]


__all__ = (
    "CLEAN_PROCESS_RECEIPT_SCHEMA",
    "CODE_SIGNING_HOLD",
    "CleanProcessRunnerError",
    "EXTERNAL_RUNNER_AUTHORITY_HOLD",
    "FILESYSTEM_PUBLICATION_HOLD",
    "RUNTIME_ISOLATION_EVIDENCE",
    "run_clean_process_replay",
    "verify_clean_process_receipt",
)
