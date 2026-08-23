"""HOLD-only in-memory contract for a future Stage-3 refreeze v4 runner.

The contract pins candidate identity, configuration, query-stream schema, and
the complete candidate-safe-core and coordinator dependency manifests.  It
does not execute a candidate or accept a callback, verification token, or
caller-supplied authority digest.  A clean-process runner is a separate future
stage and is explicitly held here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    EXECUTION_INPUT_SCHEMA,
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


REFREEZE_V4_CONTRACT_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.refreeze_v4_contract/v4"
)
CANDIDATE_MANIFEST_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.refreeze_v4_candidate_manifest/v1"
)
CONFIG_MANIFEST_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.refreeze_v4_config_manifest/v1"
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

RG3_CANDIDATE_ID = (
    "redred.mc_wtb_predictor_stage3.rg3_cav/"
    "body_transport3_cadence10ms_nearpi1em6_"
    "residual0p5_dircos0_accel0p25/v1"
)
DSPB_CANDIDATE_ID = "DSPB-A4-E0E1E2E3-V1"
PLL_CANDIDATE_ID = (
    "SO3_PLL_A5_V1:0.25,0.02,0.034906585039886591,"
    "0.52359877559829882,9.9999999999999995e-07,"
    "34.906585039886593,8.7266462599716483,69.813170079773187,"
    "0.0008726646259971648,-0.94999999999999996:"
    "20000000,5000000,5000000,1000000,2"
)

CALLABLE_RUNTIME_ISOLATION_HOLD = {
    "status": "HOLD",
    "authority_go": False,
    "fresh_process_runner_implemented": False,
    "required_runtime": (
        "fixed_sys_executable_clean_subprocess_with_fixed_candidate_id_dispatch"
    ),
    "reason": (
        "same-process manifest and self-seal checks cannot exclude parent "
        "monkeypatches; callable authority is not granted by this contract"
    ),
}

CONTRACT_CAPABILITIES = {
    "candidate_callable_execution": False,
    "arbitrary_callback_api": False,
    "label_api": False,
    "scoring_api": False,
    "filesystem_publication_api": False,
}


class RefreezeV4ContractError(ValueError):
    """The pure v4 contract or one of its fixed pins failed closed."""


_COMMON_CORE_FILES = (
    ("benchmarks/redred_mc_wtb_pose_recovery/__init__.py", "4a188a7d496b5b02db50d35d6b5b0b59a4bbb9db9e7b2d4c5bfed0262e564d7a"),
    ("benchmarks/redred_mc_wtb_pose_recovery/geometry.py", "4996aca6e9b85a777334a0239ea655a40bb8ba31a78e467bbbd4cbfa95dc8057"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/__init__.py", "956d1dac6c59cd40eba6079ab7b0762e0a92b6c001594643f36a6440156defdc"),
)
_CONTRACT_FILES = (
    ("benchmarks/redred_mc_wtb_stage4_contract/__init__.py", "851a179fb715c7f30160ccd28837e94795fb361e7ae203ee5fbbf5cbb8562a7c"),
    ("benchmarks/redred_mc_wtb_stage4_contract/contract.py", "65f5131fce3b30e956e30494e7ebaa2f3fad399a396f8760625a275f63378d38"),
    ("benchmarks/redred_mc_wtb_stage4_contract/receipt.py", "dd41d3591544da08c031f1c0d6d70428df3e495865c65dbbdeb31c0ae2a91c9b"),
)
_COMMON_COORDINATOR_FILES = (
    ("benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py", "7a321d277b575c60e82f8db84c6aec37ed72b7dda6332b54d74cd36498820e9d"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py", "379da72a6d556282fb3443fc151314d363f0208810f97d8ba7f7c773f43d89df"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/framework.py", "882a8fe035b1499161abc6313095157aa39855c2328d886d656073df0e61e9b7"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py", "f33ca39118192c35592909e3b988ce2af2009ebe59fb9867c8046705832800cb"),
    ("benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py", "8357e0b4d5579dcd1d96ade00060ecce7c7e9a254aac54f094d286b851ea46af"),
    ("benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py", "ac69d2e5e35f100cca1385be728814fec5e873ce0cf81a2ca4f38880100167ee"),
)


def _rg3_config() -> Mapping[str, object]:
    return {
        "schema": "redred.mc_wtb_predictor_stage3.rg3_query_stream_config/v1",
        "policy": {
            "candidate_id": RG3_CANDIDATE_ID,
            "maximum_pose_interval_ns": 10_000_000,
            "near_pi_margin_rad": 1.0e-6,
            "maximum_rate_change_ratio": 0.5,
            "minimum_direction_cosine": 0.0,
            "maximum_acceleration_contribution_ratio": 0.25,
        },
    }


def _dspb_config() -> Mapping[str, object]:
    return {
        "candidate_id": DSPB_CANDIDATE_ID,
        "max_horizon_ns": 5_000_000,
        "zoh_max_age_ns": 1_000_000,
        "ewma_rate_alpha": 0.25,
        "credit_ewma_alpha": 0.25,
        "minimum_credit_samples": 2,
        "credit_tie_tolerance_rad": 1.0e-12,
        "winner_switch_margin_rad": 1.0e-4,
        "disagreement_probe_ns": 5_000_000,
        "maximum_expert_disagreement_rad": 0.5,
        "maximum_rate_rad_s": 100.0,
        "maximum_rg3_acceleration_rad_s2": 10_000.0,
        "maximum_cadence_ratio": 2.0,
        "rg3_minimum_direction_cosine": 0.0,
        "rg3_maximum_prior_residual_rad": 0.25,
        "axis_minimum_coherence": 0.90,
        "minimum_signed_speed_rad_s": 1.0e-9,
        "near_pi_margin_rad": 1.0e-6,
    }


def _pll_config() -> Mapping[str, object]:
    return {
        "schema": "redred.mc_wtb_predictor_stage3.so3_pll_config/v2",
        "candidate_id": PLL_CANDIDATE_ID,
        "pll": {
            "proportional_gain": 0.25,
            "integral_gain": 0.02,
            "lock_residual_max_rad": 0.03490658503988659,
            "phase_jump_max_rad": 0.5235987755982988,
            "near_pi_margin_rad": 1.0e-6,
            "max_proportional_correction_rad_s": 34.90658503988659,
            "max_integral_correction_rad_s": 8.726646259971648,
            "max_angular_rate_rad_s": 69.81317007977319,
            "limit_cycle_min_residual_rad": 0.0008726646259971648,
            "limit_cycle_cosine_max": -0.95,
            "max_gap_ns": 20_000_000,
            "max_prediction_horizon_ns": 5_000_000,
            "cav_max_horizon_ns": 5_000_000,
            "zoh_max_age_ns": 1_000_000,
            "lock_count": 2,
        },
        "pre_roll_ns": 50_000_000,
        "reset": "new_native_model_at_each_warmup_start",
        "event_edge": "occurrence_equals_decision_minus_one",
        "same_edge_priority": "events_before_pose_commits",
        "candidate_gate": "exact_causal_cav_only",
        "fallback_routes": ["CURRENT_CAV", "FRESH_ZOH", "SENSOR_FIXED"],
    }


_RG3_CORE = _COMMON_CORE_FILES + (
    ("benchmarks/redred_mc_wtb_predictor_stage3/rg3.py", "de1cb82ac902064dc5875f34807028554f62815ec626b91f1fac68e07a41d865"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream_core.py", "0d45125581700450e34013a1dcb6c4ed057249c518fc71acc887b7da0a087307"),
) + _CONTRACT_FILES
_DSPB_CORE = _COMMON_CORE_FILES + (
    ("benchmarks/redred_mc_wtb_predictor_stage3/dspb.py", "c63f4d5f1ac6b28f5ba2665af133a2de5c5e39a51297bfbeceee240571026302"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream_core.py", "80c0f58cbc94f0c233f5f57bf761e7bd537100e61cc4ce54a370ca85b06f2f1d"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/framework.py", "882a8fe035b1499161abc6313095157aa39855c2328d886d656073df0e61e9b7"),
    ("benchmarks/redred_mc_wtb_so3_axis_audit/__init__.py", "a543dd82af6621ff0714975326f75e2e8a2dd150561a97907fa26a25dca3539b"),
    ("benchmarks/redred_mc_wtb_so3_axis_audit/analyzer.py", "093a80285f79f97f9a73090ed7a9c5cbbe3112a89a9a99a923d1e68556a573c3"),
) + _CONTRACT_FILES
_PLL_CORE = _COMMON_CORE_FILES + (
    ("benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream_core.py", "b3220db288029d35640f4fb04c920b269e558c8d43d7b2828524a8a746d6e487"),
    ("benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py", "dc1f0433f7ae4b16828ba51100a5587ae70c42f86c3ef1dc26960bc23058ed58"),
) + _CONTRACT_FILES


def _coordinator(candidate_path: str, digest: str) -> Tuple[Tuple[str, str], ...]:
    return tuple(sorted(_COMMON_COORDINATOR_FILES + ((candidate_path, digest),)))


_PROFILES = {
    RG3_CANDIDATE_ID: {
        "candidate_schema": "redred.mc_wtb_predictor_stage3.rg3_query_stream/v1",
        "configuration": _rg3_config(),
        "native_config_sha256": "048701fe1726752f7e75b2284c2a28dcbe671de5bc156a07d72f88cafcee4f08",
        "core_schema": "redred.mc_wtb_predictor_stage3.rg3_query_stream_core_manifest/v1",
        "core_entrypoint": "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream_core.py",
        "core_files": tuple(sorted(_RG3_CORE)),
        "core_manifest_sha256": "65f62f1d72f65d0915f5c89a6954e65e07758ba769b504cd419d36cf90aff173",
        "coordinator_schema": "redred.mc_wtb_predictor_stage3.rg3_query_stream_coordinator_manifest/v1",
        "coordinator_entrypoint": "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream.py",
        "coordinator_files": _coordinator("benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream.py", "370a94c4f4d33bafccd96c0545791c7ceff0920e64903f9b9a1920983bcbeae3"),
        "coordinator_manifest_sha256": "ef9a6b358718b0794dd0f25188a0af0b5b11d40e89cc8749b9321b7ef4bb0cc9",
        "domain_hold": {
            "status": "HOLD",
            "complexity": "O(N)",
            "reason": "the slice consumes the fully verified native current-CAV trace and does not claim an independently streaming native-transition authority",
        },
    },
    DSPB_CANDIDATE_ID: {
        "candidate_schema": "redred.mc_wtb_predictor_stage3.dspb_query_stream/v1",
        "configuration": _dspb_config(),
        "native_config_sha256": "94cdfdbbb532baf02fc836b43677cbdacffeeeaedb42584718d6875e3358b9f5",
        "core_schema": "redred.mc_wtb_predictor_stage3.dspb_query_stream_core_manifest/v1",
        "core_entrypoint": "benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream_core.py",
        "core_files": tuple(sorted(_DSPB_CORE)),
        "core_manifest_sha256": "a06e0381bc8caa26c444c49cc7affac2d28e2bece95c4ba289310366bff4beab",
        "coordinator_schema": "redred.mc_wtb_predictor_stage3.dspb_query_stream_coordinator_manifest/v1",
        "coordinator_entrypoint": "benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream.py",
        "coordinator_files": _coordinator("benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream.py", "8e366589fce3bc2d1c0ced6bd99110a5550b2d9a56cfe1a71bf4bd1c31b0b878"),
        "coordinator_manifest_sha256": "bf509e116fe13021101ea0b372f8e27e743cfc22a393ac40395165ad790b7923",
        "domain_hold": {
            "status": "HOLD",
            "maximum_window_pose_occurrences": 256,
            "maximum_equal_time_cluster_events": 8,
            "valid_v3_inputs_beyond_fixed_caps": "fail_closed",
            "caps_are_execution_input_v3_guarantees": False,
            "post_reset_pose_commit_cycles": "strictly_unique",
            "unique_pose_commit_cycles_are_execution_input_v3_guaranteed": False,
            "reason": "the 256-pose and 8-event-cluster caps are development policy, not execution_input/v3 guarantees; v3 also permits multiple poses on one commit cycle while native DSPB does not; inputs outside this narrower development domain fail closed",
        },
    },
    PLL_CANDIDATE_ID: {
        "candidate_schema": "redred.mc_wtb_predictor_stage3.pll_query_stream/v1",
        "configuration": _pll_config(),
        "native_config_sha256": "74bc10905c1ce633f6231cf284f1af7a09d237aaf5fb5cd83b64fe44a5f3410e",
        "core_schema": "redred.mc_wtb_predictor_stage3.pll_query_stream_core_manifest/v1",
        "core_entrypoint": "benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream_core.py",
        "core_files": tuple(sorted(_PLL_CORE)),
        "core_manifest_sha256": "20de2f3e0f003d5fd929fd6b76f3aa9b2d1c0304af02bfeb6b68805980bd5c18",
        "coordinator_schema": "redred.mc_wtb_predictor_stage3.pll_query_stream_coordinator_manifest/v1",
        "coordinator_entrypoint": "benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream.py",
        "coordinator_files": _coordinator("benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream.py", "c92003d0d6d071eb2be1a246a688364af44adf14ce3b95ad02c23f67de10040a"),
        "coordinator_manifest_sha256": "2318c90e5b58a7b1603e56d854d93bf06a919f664bedc71a3c5423bd82b419c8",
        "domain_hold": {
            "status": "HOLD",
            "post_reset_pose_commit_cycles": "strictly_unique",
            "unique_pose_commit_cycles_are_execution_input_v3_guaranteed": False,
            "reason": "execution_input/v3 permits multiple poses on one post-reset commit cycle, while this native PLL development slice requires one pose publication per edge and fails closed outside that narrower domain",
        },
    },
}


def _snapshot(value: object, where: str) -> Mapping[str, object]:
    try:
        result = json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RefreezeV4ContractError("%s is not canonical JSON" % where) from exc
    if not isinstance(result, dict):
        raise RefreezeV4ContractError("%s must be an object" % where)
    return result


def _file_rows(rows: Sequence[Tuple[str, str]]) -> Sequence[Mapping[str, object]]:
    result = []
    for path, expected in rows:
        resolved = (_REPOSITORY_ROOT / path).resolve()
        try:
            resolved.relative_to(_REPOSITORY_ROOT)
        except ValueError as exc:  # pragma: no cover - literal pins only
            raise RefreezeV4ContractError("pinned dependency escapes root") from exc
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != expected:
            raise RefreezeV4ContractError("pinned dependency differs: %s" % path)
        result.append({"path": path, "sha256": expected})
    return result


def _manifest(
    schema: str,
    candidate_id: str,
    entrypoint: str,
    rows: Sequence[Tuple[str, str]],
    expected_sha256: str,
    core_sha256: str = "",
) -> Mapping[str, object]:
    body = {
        "schema": schema,
        "candidate_id": candidate_id,
        "entrypoint": entrypoint,
        "files": _file_rows(rows),
    }
    if core_sha256:
        body["candidate_safe_core_manifest_sha256"] = core_sha256
    result = dict(body, manifest_sha256=canonical_sha256(body))
    if result["manifest_sha256"] != expected_sha256:
        raise RefreezeV4ContractError("pinned manifest seal differs")
    return result


def _candidate_manifest(candidate_id: str) -> Mapping[str, object]:
    profile = _PROFILES.get(candidate_id)
    if profile is None:
        raise RefreezeV4ContractError("candidate ID is not preauthorized")
    configuration = _snapshot(profile["configuration"], "pinned configuration")
    config_body = {
        "schema": CONFIG_MANIFEST_SCHEMA,
        "candidate_id": candidate_id,
        "configuration": configuration,
        "configuration_canonical_sha256": canonical_sha256(configuration),
        "candidate_native_config_sha256": profile["native_config_sha256"],
    }
    config_manifest = dict(
        config_body, manifest_sha256=canonical_sha256(config_body)
    )
    core = _manifest(
        profile["core_schema"], candidate_id, profile["core_entrypoint"],
        profile["core_files"], profile["core_manifest_sha256"],
    )
    coordinator = _manifest(
        profile["coordinator_schema"], candidate_id,
        profile["coordinator_entrypoint"], profile["coordinator_files"],
        profile["coordinator_manifest_sha256"], core["manifest_sha256"],
    )
    domain_hold = _snapshot(profile["domain_hold"], "candidate domain HOLD")
    body = {
        "schema": CANDIDATE_MANIFEST_SCHEMA,
        "candidate_id": candidate_id,
        "candidate_schema": profile["candidate_schema"],
        "config_manifest": config_manifest,
        "config_manifest_sha256": config_manifest["manifest_sha256"],
        "candidate_safe_core_manifest": core,
        "candidate_safe_core_manifest_sha256": core["manifest_sha256"],
        "coordinator_manifest": coordinator,
        "coordinator_manifest_sha256": coordinator["manifest_sha256"],
        "candidate_domain_hold": domain_hold,
        "candidate_domain_hold_sha256": canonical_sha256(domain_hold),
    }
    return dict(body, manifest_sha256=canonical_sha256(body))


def build_refreeze_v4_contract(
    execution_input: object,
    candidate_id: object,
) -> Mapping[str, object]:
    """Cross-bind verified v3 input to one fixed candidate without executing it."""

    if not isinstance(candidate_id, str):
        raise RefreezeV4ContractError("candidate ID must be text")
    execution = _snapshot(execution_input, "execution_input")
    try:
        execution_digest = verify_stage3_execution_input(
            execution,
            expected_aggregate_sha256=execution.get("aggregate_sha256"),
            repo_root=_REPOSITORY_ROOT,
        )
        candidate = _candidate_manifest(candidate_id)
    except RefreezeV4ContractError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RefreezeV4ContractError("v4 contract failed: %s" % exc) from exc
    body = {
        "schema": REFREEZE_V4_CONTRACT_SCHEMA,
        "status": "DEVELOPMENT_HOLD",
        "authority_decision": "HOLD",
        "execution_input_schema": EXECUTION_INPUT_SCHEMA,
        "execution_input_aggregate_sha256": execution_digest,
        "neutral_input_sha256": execution["neutral_input_sha256"],
        "ordered_query_event_ids_sha256": execution[
            "ordered_query_event_ids_sha256"
        ],
        "candidate_manifest": candidate,
        "candidate_manifest_sha256": candidate["manifest_sha256"],
        "callable_runtime_isolation_hold": dict(
            CALLABLE_RUNTIME_ISOLATION_HOLD
        ),
        "capabilities": dict(CONTRACT_CAPABILITIES),
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


_CONTRACT_FIELDS = frozenset((
    "schema", "status", "authority_decision", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "candidate_manifest",
    "candidate_manifest_sha256", "callable_runtime_isolation_hold",
    "capabilities", "aggregate_sha256",
))


def verify_refreeze_v4_contract(
    contract: object,
    execution_input: object,
    expected_candidate_id: object,
) -> str:
    """Rebuild one expected candidate's closed HOLD contract from pins."""

    if not isinstance(expected_candidate_id, str):
        raise RefreezeV4ContractError("expected candidate ID must be text")
    checked = _snapshot(contract, "refreeze_v4_contract")
    if frozenset(checked) != _CONTRACT_FIELDS:
        raise RefreezeV4ContractError("v4 contract fields are not exact")
    candidate = checked.get("candidate_manifest")
    if not isinstance(candidate, dict):
        raise RefreezeV4ContractError("candidate manifest must be an object")
    candidate_id = candidate.get("candidate_id")
    if candidate_id != expected_candidate_id:
        raise RefreezeV4ContractError("contract candidate does not match expectation")
    rebuilt = build_refreeze_v4_contract(execution_input, expected_candidate_id)
    if canonical_json_bytes(checked) != canonical_json_bytes(rebuilt):
        raise RefreezeV4ContractError("v4 contract differs from fixed reconstruction")
    return checked["aggregate_sha256"]


def verify_rg3_refreeze_v4_contract(
    contract: object, execution_input: object
) -> str:
    checked = _snapshot(contract, "RG3 v4 contract")
    candidate = checked.get("candidate_manifest", {})
    if candidate.get("candidate_id") != RG3_CANDIDATE_ID:
        raise RefreezeV4ContractError("contract is not RG3")
    return verify_refreeze_v4_contract(checked, execution_input, RG3_CANDIDATE_ID)


def verify_dspb_refreeze_v4_contract(
    contract: object, execution_input: object
) -> str:
    checked = _snapshot(contract, "DSPB v4 contract")
    candidate = checked.get("candidate_manifest", {})
    if candidate.get("candidate_id") != DSPB_CANDIDATE_ID:
        raise RefreezeV4ContractError("contract is not DSPB")
    return verify_refreeze_v4_contract(checked, execution_input, DSPB_CANDIDATE_ID)


def verify_pll_refreeze_v4_contract(
    contract: object, execution_input: object
) -> str:
    checked = _snapshot(contract, "PLL v4 contract")
    candidate = checked.get("candidate_manifest", {})
    if candidate.get("candidate_id") != PLL_CANDIDATE_ID:
        raise RefreezeV4ContractError("contract is not PLL")
    return verify_refreeze_v4_contract(checked, execution_input, PLL_CANDIDATE_ID)


__all__ = (
    "CALLABLE_RUNTIME_ISOLATION_HOLD",
    "CANDIDATE_MANIFEST_SCHEMA",
    "CONFIG_MANIFEST_SCHEMA",
    "CONTRACT_CAPABILITIES",
    "DSPB_CANDIDATE_ID",
    "PLL_CANDIDATE_ID",
    "REFREEZE_V4_CONTRACT_SCHEMA",
    "RG3_CANDIDATE_ID",
    "RefreezeV4ContractError",
    "build_refreeze_v4_contract",
    "verify_dspb_refreeze_v4_contract",
    "verify_pll_refreeze_v4_contract",
    "verify_refreeze_v4_contract",
    "verify_rg3_refreeze_v4_contract",
)
