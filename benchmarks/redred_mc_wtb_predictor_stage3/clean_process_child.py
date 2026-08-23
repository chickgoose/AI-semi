"""Private clean-process worker for Stage-3 query-stream replay.

The worker accepts one canonical JSON request on stdin and emits one
canonical JSON response on stdout.  Candidate selection is an exact
three-way dispatch; no module, callable, root, configuration, or timeout is
accepted from the caller.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (  # noqa: E402
    EXECUTION_INPUT_SCHEMA,
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.refreeze_v4 import (  # noqa: E402
    DSPB_CANDIDATE_ID,
    PLL_CANDIDATE_ID,
    RG3_CANDIDATE_ID,
    verify_refreeze_v4_contract,
)
from benchmarks.redred_mc_wtb_stage4_contract import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)


CLEAN_PROCESS_REQUEST_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.clean_process_request/v1"
)
CLEAN_PROCESS_CHILD_RESPONSE_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.clean_process_child_response/v1"
)

_REQUEST_FIELDS = frozenset((
    "schema", "expected_candidate_id", "execution_input",
    "execution_input_aggregate_sha256", "refreeze_contract",
    "refreeze_contract_sha256", "expected_runtime_manifest",
    "expected_runtime_manifest_sha256", "request_sha256",
))
_RESPONSE_FIELDS = frozenset((
    "schema", "candidate_id", "execution_input_aggregate_sha256",
    "refreeze_contract_sha256", "runtime_manifest",
    "runtime_manifest_sha256", "candidate_output",
    "candidate_output_sha256", "aggregate_sha256",
))

_RG3_OUTPUT_FIELDS = frozenset((
    "schema", "candidate_id", "status", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "candidate_safe_core_manifest",
    "candidate_safe_core_manifest_sha256", "coordinator_manifest",
    "coordinator_manifest_sha256", "verified_input_complexity_hold",
    "native_transition_complexity_hold", "output_authority_hold",
    "query_path_sha256", "deterministic_replay_count",
    "deterministic_double_replay_verified", "replay_sha256", "windows",
    "windows_sha256", "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows", "maximum_retained_candidate_pose_count",
    "aggregate_sha256",
))
_DSPB_OUTPUT_FIELDS = frozenset((
    "schema", "candidate_id", "status", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "candidate_config",
    "candidate_config_sha256", "bounded_state_profile",
    "candidate_safe_core_manifest", "candidate_safe_core_manifest_sha256",
    "coordinator_manifest", "coordinator_manifest_sha256",
    "verified_input_complexity_hold", "output_authority_hold",
    "input_domain_hold", "query_path_sha256", "deterministic_replay_count",
    "deterministic_double_replay_verified", "replay_sha256", "windows",
    "windows_sha256", "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows", "maximum_retained_native_pose_count",
    "maximum_equal_time_cluster_count", "aggregate_sha256",
))
_PLL_OUTPUT_FIELDS = frozenset((
    "schema", "candidate_id", "status", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "configuration_sha256",
    "candidate_safe_core_manifest", "candidate_safe_core_manifest_sha256",
    "coordinator_manifest", "coordinator_manifest_sha256",
    "verified_input_complexity_hold", "input_domain_hold",
    "native_transition_complexity_hold", "batch_provenance_equivalence_hold",
    "output_authority_hold", "candidate_provenance_representation",
    "query_path_sha256", "deterministic_replay_count",
    "deterministic_double_replay_verified", "replay_sha256", "windows",
    "windows_sha256", "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows",
    "maximum_retained_effective_pending_state_count",
    "maximum_retained_fallback_pose_count", "query_transition_count",
    "aggregate_sha256",
))
_OUTPUT_AUTHORITY_HOLDS = {
    RG3_CANDIDATE_ID: {
        "status": "HOLD",
        "reason": (
            "this development slice has no externally pinned candidate "
            "authority or independent closed-schema replay verifier"
        ),
    },
    DSPB_CANDIDATE_ID: {
        "status": "HOLD",
        "reason": (
            "this development slice has no externally pinned candidate "
            "authority or independent closed-schema replay verifier"
        ),
    },
    PLL_CANDIDATE_ID: {
        "status": "HOLD",
        "reason": (
            "development slice has no externally pinned candidate authority "
            "or independent closed-schema replay verifier"
        ),
    },
}


class CleanProcessChildError(ValueError):
    """The worker rejected its runtime, request, replay, or output."""


def _snapshot(value: object, where: str) -> Mapping[str, object]:
    try:
        result = json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CleanProcessChildError("%s is not canonical JSON" % where) from exc
    if not isinstance(result, dict):
        raise CleanProcessChildError("%s must be an object" % where)
    return result


def _sha(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CleanProcessChildError("%s must be lowercase SHA-256" % where)
    return value


def _runtime_manifest() -> Mapping[str, object]:
    executable = Path(sys.executable).resolve()
    worker = Path(__file__).resolve()
    return {
        "schema": "redred.mc_wtb_predictor_stage3.clean_process_runtime/v1",
        "python_executable_path": str(executable),
        "python_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_executable_size_bytes": executable.stat().st_size,
        "python_version": sys.version,
        "python_implementation": sys.implementation.name,
        "python_cache_tag": sys.implementation.cache_tag,
        "isolated": bool(sys.flags.isolated),
        "no_site": bool(sys.flags.no_site),
        "dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
        "repository_root": str(_REPOSITORY_ROOT),
        "worker_path": str(worker),
        "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
    }


def _dispatch(candidate_id: str, execution: object) -> Mapping[str, object]:
    if candidate_id == RG3_CANDIDATE_ID:
        from benchmarks.redred_mc_wtb_predictor_stage3.rg3_query_stream import (
            generate_rg3_query_stream,
        )
        return generate_rg3_query_stream(execution)
    if candidate_id == DSPB_CANDIDATE_ID:
        from benchmarks.redred_mc_wtb_predictor_stage3.dspb_query_stream import (
            generate_dspb_query_stream,
        )
        return generate_dspb_query_stream(execution)
    if candidate_id == PLL_CANDIDATE_ID:
        from benchmarks.redred_mc_wtb_predictor_stage3.pll_query_stream import (
            generate_pll_query_stream,
        )
        return generate_pll_query_stream(execution)
    raise CleanProcessChildError("candidate ID is not fixed dispatch")


def _candidate_domain_hold(
    output: Mapping[str, object], candidate_id: str
) -> object:
    if candidate_id == RG3_CANDIDATE_ID:
        return output.get("native_transition_complexity_hold")
    return output.get("input_domain_hold")


def _verify_candidate_output(
    output_value: object,
    execution_value: object,
    contract_value: object,
    candidate_id: str,
) -> Mapping[str, object]:
    output = _snapshot(output_value, "candidate output")
    execution = _snapshot(execution_value, "execution input")
    contract = _snapshot(contract_value, "refreeze contract")
    candidate = contract["candidate_manifest"]
    expected_fields = {
        RG3_CANDIDATE_ID: _RG3_OUTPUT_FIELDS,
        DSPB_CANDIDATE_ID: _DSPB_OUTPUT_FIELDS,
        PLL_CANDIDATE_ID: _PLL_OUTPUT_FIELDS,
    }.get(candidate_id)
    if expected_fields is None or frozenset(output) != expected_fields:
        raise CleanProcessChildError("candidate output fields are not exact")
    if output["schema"] != candidate["candidate_schema"]:
        raise CleanProcessChildError("candidate output schema differs")
    if output["candidate_id"] != candidate_id:
        raise CleanProcessChildError("candidate output identity differs")
    if output["status"] != "DEVELOPMENT_HOLD":
        raise CleanProcessChildError("candidate output is not development HOLD")
    for field in (
        "execution_input_aggregate_sha256",
        "neutral_input_sha256",
        "ordered_query_event_ids_sha256",
    ):
        if output[field] != execution[field if field != "execution_input_aggregate_sha256" else "aggregate_sha256"]:
            raise CleanProcessChildError("candidate output %s differs" % field)
    if output["execution_input_schema"] != EXECUTION_INPUT_SCHEMA:
        raise CleanProcessChildError("candidate execution schema differs")
    if output["candidate_safe_core_manifest"] != candidate[
        "candidate_safe_core_manifest"
    ]:
        raise CleanProcessChildError("candidate core manifest differs")
    if output["candidate_safe_core_manifest_sha256"] != candidate[
        "candidate_safe_core_manifest_sha256"
    ]:
        raise CleanProcessChildError("candidate core digest differs")
    if output["coordinator_manifest"] != candidate["coordinator_manifest"]:
        raise CleanProcessChildError("candidate coordinator manifest differs")
    if output["coordinator_manifest_sha256"] != candidate[
        "coordinator_manifest_sha256"
    ]:
        raise CleanProcessChildError("candidate coordinator digest differs")
    config = candidate["config_manifest"]
    if candidate_id == DSPB_CANDIDATE_ID:
        if output["candidate_config"] != config["configuration"]:
            raise CleanProcessChildError("DSPB configuration differs")
        if output["candidate_config_sha256"] != config[
            "candidate_native_config_sha256"
        ]:
            raise CleanProcessChildError("DSPB configuration digest differs")
    if candidate_id == PLL_CANDIDATE_ID and output[
        "configuration_sha256"
    ] != config["candidate_native_config_sha256"]:
        raise CleanProcessChildError("PLL configuration digest differs")
    if _candidate_domain_hold(output, candidate_id) != candidate[
        "candidate_domain_hold"
    ]:
        raise CleanProcessChildError("candidate domain HOLD differs")
    output_hold = output["output_authority_hold"]
    if output_hold != _OUTPUT_AUTHORITY_HOLDS[candidate_id]:
        raise CleanProcessChildError("candidate output-authority HOLD differs")
    if (
        output["deterministic_replay_count"] != 2
        or output["deterministic_double_replay_verified"] is not True
        or output["warmup_rows_emitted"] != 0
        or output["query_event_count"] != execution["query_event_count"]
    ):
        raise CleanProcessChildError("candidate replay counts differ")
    windows = output["windows"]
    if not isinstance(windows, list) or len(windows) != len(execution["windows"]):
        raise CleanProcessChildError("candidate window cardinality differs")
    for actual, source in zip(windows, execution["windows"]):
        if not isinstance(actual, dict) or actual.get("window_id") != source["window_id"]:
            raise CleanProcessChildError("candidate window identity differs")
        expected_query = [event for event in source["events"] if event["is_query"]]
        rows = actual.get("query_rows")
        if not isinstance(rows, list) or len(rows) != len(expected_query):
            raise CleanProcessChildError("candidate query cardinality differs")
        if actual.get("query_event_count") != len(expected_query):
            raise CleanProcessChildError("candidate window query count differs")
        if actual.get("warmup_rows_emitted") != 0:
            raise CleanProcessChildError("candidate window emitted warmup rows")
        if actual.get("query_rows_sha256") != canonical_sha256(rows):
            raise CleanProcessChildError("candidate query-row seal differs")
        for row, event in zip(rows, expected_query):
            if (
                row.get("event_id") != event["event_id"]
                or row.get("event_content_sha256")
                != event["event_content_sha256"]
            ):
                raise CleanProcessChildError("candidate query identity differs")
    if output["windows_sha256"] != canonical_sha256(windows):
        raise CleanProcessChildError("candidate windows seal differs")
    body = {key: value for key, value in output.items() if key != "aggregate_sha256"}
    if output["aggregate_sha256"] != canonical_sha256(body):
        raise CleanProcessChildError("candidate aggregate seal differs")
    return output


def _parse_request(raw: bytes, argv_candidate_id: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CleanProcessChildError("request is not JSON") from exc
    if not isinstance(value, dict) or frozenset(value) != _REQUEST_FIELDS:
        raise CleanProcessChildError("request fields are not exact")
    if canonical_json_bytes(value) != raw:
        raise CleanProcessChildError("request bytes are not canonical")
    supplied = _sha(value["request_sha256"], "request seal")
    body = {key: item for key, item in value.items() if key != "request_sha256"}
    if supplied != canonical_sha256(body):
        raise CleanProcessChildError("request seal differs")
    if value["schema"] != CLEAN_PROCESS_REQUEST_SCHEMA:
        raise CleanProcessChildError("request schema differs")
    if value["expected_candidate_id"] != argv_candidate_id:
        raise CleanProcessChildError("argv and request candidate differ")
    return value


def _run(raw: bytes, argv_candidate_id: str) -> bytes:
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or Path.cwd().resolve() != _REPOSITORY_ROOT
    ):
        raise CleanProcessChildError("clean-process runtime flags or root differ")
    request = _parse_request(raw, argv_candidate_id)
    runtime = _runtime_manifest()
    if runtime != request["expected_runtime_manifest"]:
        raise CleanProcessChildError("runtime manifest differs")
    if canonical_sha256(runtime) != request["expected_runtime_manifest_sha256"]:
        raise CleanProcessChildError("runtime manifest seal differs")
    execution = request["execution_input"]
    contract = request["refreeze_contract"]
    execution_digest = verify_stage3_execution_input(
        execution,
        expected_aggregate_sha256=request["execution_input_aggregate_sha256"],
        repo_root=_REPOSITORY_ROOT,
    )
    if execution_digest != request["execution_input_aggregate_sha256"]:
        raise CleanProcessChildError("execution digest differs")
    contract_digest = verify_refreeze_v4_contract(
        contract, execution, argv_candidate_id
    )
    if contract_digest != request["refreeze_contract_sha256"]:
        raise CleanProcessChildError("refreeze contract digest differs")
    output = _verify_candidate_output(
        _dispatch(argv_candidate_id, execution),
        execution,
        contract,
        argv_candidate_id,
    )
    body = {
        "schema": CLEAN_PROCESS_CHILD_RESPONSE_SCHEMA,
        "candidate_id": argv_candidate_id,
        "execution_input_aggregate_sha256": execution_digest,
        "refreeze_contract_sha256": contract_digest,
        "runtime_manifest": runtime,
        "runtime_manifest_sha256": canonical_sha256(runtime),
        "candidate_output": output,
        "candidate_output_sha256": canonical_sha256(output),
    }
    return canonical_json_bytes(dict(body, aggregate_sha256=canonical_sha256(body)))


def _verify_child_response(
    value: object,
    execution: object,
    contract: object,
    candidate_id: str,
    expected_runtime: Mapping[str, object],
) -> Mapping[str, object]:
    response = _snapshot(value, "child response")
    if frozenset(response) != _RESPONSE_FIELDS:
        raise CleanProcessChildError("child response fields are not exact")
    body = {
        key: item for key, item in response.items() if key != "aggregate_sha256"
    }
    if response["aggregate_sha256"] != canonical_sha256(body):
        raise CleanProcessChildError("child response seal differs")
    if (
        response["schema"] != CLEAN_PROCESS_CHILD_RESPONSE_SCHEMA
        or response["candidate_id"] != candidate_id
        or response["execution_input_aggregate_sha256"]
        != execution["aggregate_sha256"]
        or response["refreeze_contract_sha256"] != contract["aggregate_sha256"]
        or response["runtime_manifest"] != expected_runtime
        or response["runtime_manifest_sha256"] != canonical_sha256(expected_runtime)
    ):
        raise CleanProcessChildError("child response binding differs")
    output = _verify_candidate_output(
        response["candidate_output"], execution, contract, candidate_id
    )
    if response["candidate_output_sha256"] != canonical_sha256(output):
        raise CleanProcessChildError("child candidate-output seal differs")
    return response


def main() -> int:
    try:
        if len(sys.argv) != 2:
            return 64
        candidate_id = sys.argv[1]
        if candidate_id not in (
            RG3_CANDIDATE_ID, DSPB_CANDIDATE_ID, PLL_CANDIDATE_ID
        ):
            return 64
        response = _run(sys.stdin.buffer.read(), candidate_id)
        sys.stdout.buffer.write(response)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:
        return 70


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "CLEAN_PROCESS_CHILD_RESPONSE_SCHEMA",
    "CLEAN_PROCESS_REQUEST_SCHEMA",
    "CleanProcessChildError",
)
