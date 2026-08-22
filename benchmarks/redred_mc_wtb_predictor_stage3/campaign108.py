"""Single-attempt, candidate-neutral orchestration for the locked NEW108 screen.

The campaign chooses one exact model-specific generator from a closed frozen-ID
registry.  A generator receives neutral adapter values only; the selector label
sidecar is neither passed nor read.  Its candidate output is sealed and written
append-only before the locked screen is invoked.  There is no candidate loop,
parameter search, retry, outcome-dependent branch, or external-data path.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample as RecoveryPoseSample,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterBundle,
    build_locked_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import timestamp_to_cycle

from . import dspb, rg3, screen108, so3_pll


CAMPAIGN_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_receipt/v1"
ATTEMPT_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_attempt/v1"
GENERATOR_EVIDENCE_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.campaign108_generator_evidence/v1"
)
CONFIG_SCHEMA = "redred.mc_wtb_predictor_stage3.frozen_candidate_config/v1"

RG3_ID = "RG3-CAV-A3-V1"
DSPB_ID = "DSPB-A4-E0E1E2E3-V1"
SO3_PLL_ID = "SO3-PLL-A5-V1"
FROZEN_CANDIDATE_IDS = (RG3_ID, DSPB_ID, SO3_PLL_ID)

_EXECUTABLE_SHA256 = {
    RG3_ID: "de1cb82ac902064dc5875f34807028554f62815ec626b91f1fac68e07a41d865",
    DSPB_ID: "c63f4d5f1ac6b28f5ba2665af133a2de5c5e39a51297bfbeceee240571026302",
    SO3_PLL_ID: "dc1f0433f7ae4b16828ba51100a5587ae70c42f86c3ef1dc26960bc23058ed58",
}


class Campaign108Error(ValueError):
    """The frozen campaign identity, artifact, or single-attempt rule failed."""


@dataclass(frozen=True)
class GenerationWindow:
    """The complete label-free input passed to model-specific generators."""

    registry: NeutralRegistryWindow
    events: Tuple[NeutralEventInput, ...]
    poses: Tuple[NeutralPoseInput, ...]


@dataclass(frozen=True)
class GenerationInput:
    windows: Tuple[GenerationWindow, ...]


@dataclass(frozen=True)
class GeneratedCandidate:
    """Unsealed screen rows plus full model-specific causal evidence."""

    candidate_id: str
    windows: Tuple[Mapping[str, object], ...]
    evidence: Mapping[str, object]


Generator = Callable[[GenerationInput], GeneratedCandidate]


@dataclass(frozen=True)
class FrozenCandidate:
    candidate_id: str
    model_candidate_id: str
    executable_path: Path
    executable_sha256: str
    expected_config: Mapping[str, object]
    generator: Generator


def _read_bytes(path: Path, where: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Campaign108Error("cannot read %s" % where) from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_json_bytes(payload: bytes, where: str) -> Mapping[str, object]:
    def reject_constant(value: str) -> None:
        raise Campaign108Error("%s contains a non-finite JSON value" % where)

    def reject_duplicates(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
        output = {}  # type: Dict[str, object]
        for key, value in pairs:
            if key in output:
                raise Campaign108Error("%s contains a duplicate JSON key" % where)
            output[key] = value
        return output

    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Campaign108Error("cannot parse %s" % where) from exc
    if not isinstance(value, Mapping):
        raise Campaign108Error("%s must be a JSON object" % where)
    return value


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Campaign108Error("campaign artifact is not finite canonical JSON") from exc


def _exclusive_write(path: Path, payload: bytes, where: str) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(path), flags, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise Campaign108Error("cannot append-only create %s" % where) from exc


def _frozen_config(
    candidate_id: str,
    model_candidate_id: str,
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "schema": CONFIG_SCHEMA,
        "candidate_id": candidate_id,
        "model_candidate_id": model_candidate_id,
        "parameters": dict(parameters),
    }


def _rg3_config() -> Mapping[str, object]:
    return _frozen_config(RG3_ID, rg3.RG3_POLICY.candidate_id, asdict(rg3.RG3_POLICY))


def _dspb_config() -> Mapping[str, object]:
    config = dspb.DSPBConfig()
    return _frozen_config(DSPB_ID, config.candidate_id, config.to_mapping())


def _pll_config() -> Mapping[str, object]:
    config = so3_pll.SO3PLLConfig()
    return _frozen_config(SO3_PLL_ID, config.candidate_id, asdict(config))


def frozen_candidate_config(candidate_id: str) -> Mapping[str, object]:
    """Return a detached copy of the one accepted config for a frozen ID."""

    spec = _candidate(candidate_id)
    return json.loads(json.dumps(spec.expected_config, allow_nan=False))


def _decision_cycle(window: GenerationWindow, event: NeutralEventInput) -> int:
    return timestamp_to_cycle(
        event.timestamp_ns, window.registry.warmup_start_ns_inclusive
    )


def _pose_ids_for_recovery(
    poses: Sequence[NeutralPoseInput],
    timestamps: Sequence[int],
    cycles: Sequence[int],
) -> Tuple[int, ...]:
    by_key = {(pose.timestamp_ns, pose.commit_cycle): pose.pose_id for pose in poses}
    identifiers = []
    for timestamp, cycle in zip(timestamps, cycles):
        pose_id = by_key.get((timestamp, cycle))
        if pose_id is None:
            raise Campaign108Error("model receipt cannot resolve a neutral pose")
        identifiers.append(pose_id)
    return tuple(sorted(set(identifiers)))


def _output_row(
    candidate_id: str,
    event: NeutralEventInput,
    decision_cycle: int,
    state_version: int,
    used_pose_ids: Sequence[int],
    candidate_used: bool,
    quaternion_xyzw: Optional[Sequence[float]],
    fallback_reason: Optional[str],
) -> Mapping[str, object]:
    if candidate_used:
        if quaternion_xyzw is None:
            raise Campaign108Error("candidate-use decision lacks a quaternion")
        world_ray = list(
            rotate_sensor_ray_to_world(quaternion_xyzw, event.sensor_ray)
        )
        model_id = candidate_id
        reason = None
    else:
        world_ray = None
        model_id = "CURRENT_CAV"
        reason = fallback_reason or "candidate_fallback"
    return {
        "event_id": event.event_id,
        "event_content_sha256": event.event_content_sha256,
        "decision_cycle": decision_cycle,
        "model_id": model_id,
        "predictor_state_version": state_version,
        "used_pose_ids": list(sorted(set(used_pose_ids))),
        "candidate_used": candidate_used,
        "fallback_reason": reason,
        "world_ray": world_ray,
    }


def _seal_generator_evidence(
    candidate_id: str, model_candidate_id: str, windows: Sequence[Mapping[str, object]]
) -> Mapping[str, object]:
    body = {
        "schema": GENERATOR_EVIDENCE_SCHEMA,
        "candidate_id": candidate_id,
        "model_candidate_id": model_candidate_id,
        "windows": list(windows),
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def _generate_rg3(source: GenerationInput) -> GeneratedCandidate:
    windows = []
    evidence_windows = []
    for window in source.windows:
        samples = tuple(
            RecoveryPoseSample(
                pose.timestamp_ns, pose.commit_cycle, pose.quaternion_xyzw
            )
            for pose in window.poses if pose.value_valid and pose.arithmetic_valid
        )
        rows = []
        decisions = []
        for event in window.events:
            cycle = _decision_cycle(window, event)
            decision = rg3.recover_rg3_cav(samples, event.timestamp_ns, cycle)
            used_ids = _pose_ids_for_recovery(
                window.poses,
                decision.used_measurement_timestamps_ns,
                decision.used_commit_cycles,
            )
            rows.append(_output_row(
                RG3_ID, event, cycle, 0, used_ids, decision.candidate_used,
                decision.quaternion_xyzw, decision.reason,
            ))
            decisions.append({
                "event_id": event.event_id,
                "candidate_used": decision.candidate_used,
                "reason": decision.reason,
                "used_pose_ids": list(used_ids),
                "quaternion_xyzw": (
                    None if decision.quaternion_xyzw is None
                    else list(decision.quaternion_xyzw)
                ),
                "baseline_mode": decision.baseline_decision.mode.value,
            })
        windows.append({"window_id": window.registry.window_id, "events": rows})
        evidence_windows.append({
            "window_id": window.registry.window_id,
            "decision_count": len(decisions),
            "decisions": decisions,
            "decisions_sha256": canonical_sha256(decisions),
        })
    evidence = _seal_generator_evidence(
        RG3_ID, rg3.RG3_POLICY.candidate_id, evidence_windows
    )
    return GeneratedCandidate(RG3_ID, tuple(windows), evidence)


def _events_by_cycle(window: GenerationWindow) -> Mapping[int, Tuple[NeutralEventInput, ...]]:
    grouped = {}  # type: Dict[int, list]
    for event in window.events:
        grouped.setdefault(_decision_cycle(window, event), []).append(event)
    return {cycle: tuple(events) for cycle, events in grouped.items()}


def _generate_dspb(source: GenerationInput) -> GeneratedCandidate:
    windows = []
    evidence_windows = []
    config = dspb.DSPBConfig()
    for window in source.windows:
        model = dspb.DSPBModel(config)
        events_by_cycle = _events_by_cycle(window)
        poses_by_cycle = {}  # type: Dict[int, list]
        maximum_event_cycle = max(events_by_cycle)
        for pose in window.poses:
            if pose.commit_cycle <= maximum_event_cycle:
                poses_by_cycle.setdefault(pose.commit_cycle, []).append(pose)
        output_by_id = {}
        for cycle in sorted(set(events_by_cycle) | set(poses_by_cycle)):
            for pose in poses_by_cycle.get(cycle, ()):
                model.commit_pose(dspb.SuppliedPose(
                    pose.pose_id, pose.timestamp_ns, pose.commit_cycle,
                    pose.quaternion_xyzw, pose.value_valid, pose.arithmetic_valid,
                ))
            cycle_events = events_by_cycle.get(cycle, ())
            index = 0
            while index < len(cycle_events):
                stop = index + 1
                while stop < len(cycle_events) and cycle_events[stop].timestamp_ns == cycle_events[index].timestamp_ns:
                    stop += 1
                cluster = cycle_events[index:stop]
                model_events = tuple(dspb.EventRecord(
                    event.event_id, event.timestamp_ns, cycle - 1, cycle
                ) for event in cluster)
                for event, decision in zip(cluster, model.predict_event_cluster(model_events)):
                    output_by_id[event.event_id] = _output_row(
                        DSPB_ID, event, cycle, decision.state_version,
                        decision.used_pose_ids, decision.candidate_used,
                        decision.output_quaternion_xyzw, decision.fallback_reason,
                    )
                index = stop
        rows = [output_by_id[event.event_id] for event in window.events]
        windows.append({"window_id": window.registry.window_id, "events": rows})
        pose_receipts = [row.to_mapping() for row in model.pose_receipts]
        event_receipts = [row.to_mapping() for row in model.event_decisions]
        evidence_windows.append({
            "window_id": window.registry.window_id,
            "pose_receipts": pose_receipts,
            "event_decisions": event_receipts,
            "pose_receipts_sha256": canonical_sha256(pose_receipts),
            "event_decisions_sha256": canonical_sha256(event_receipts),
        })
    evidence = _seal_generator_evidence(
        DSPB_ID, config.candidate_id, evidence_windows
    )
    return GeneratedCandidate(DSPB_ID, tuple(windows), evidence)


def _pll_recovery_pose_ids(
    poses: Sequence[NeutralPoseInput], decision: so3_pll.SO3PLLDecision
) -> Tuple[int, ...]:
    if decision.candidate_used:
        return () if decision.anchor_pose_id is None else (decision.anchor_pose_id,)
    recovery = decision.fallback_decision
    if recovery is None:
        return ()
    return _pose_ids_for_recovery(
        poses,
        recovery.used_measurement_timestamps_ns,
        recovery.used_commit_cycles,
    )


def _generate_pll(source: GenerationInput) -> GeneratedCandidate:
    windows = []
    evidence_windows = []
    config = so3_pll.SO3PLLConfig()
    for window in source.windows:
        model = so3_pll.SO3PLLModel(config)
        events_by_cycle = _events_by_cycle(window)
        poses_by_cycle = {}  # type: Dict[int, list]
        maximum_event_cycle = max(events_by_cycle)
        for pose in window.poses:
            if pose.commit_cycle <= maximum_event_cycle:
                poses_by_cycle.setdefault(pose.commit_cycle, []).append(pose)
        output_by_id = {}
        model_decisions = []
        for cycle in sorted(set(events_by_cycle) | set(poses_by_cycle)):
            for pose in poses_by_cycle.get(cycle, ()):
                model.commit_pose(
                    pose.pose_id, pose.timestamp_ns, pose.commit_cycle,
                    pose.quaternion_xyzw,
                    valid=pose.value_valid and pose.arithmetic_valid,
                )
            for event in events_by_cycle.get(cycle, ()):
                decision = model.predict(event.timestamp_ns, cycle)
                used_ids = _pll_recovery_pose_ids(window.poses, decision)
                state_version = 0 if decision.state_version is None else decision.state_version
                output_by_id[event.event_id] = _output_row(
                    SO3_PLL_ID, event, cycle, state_version, used_ids,
                    decision.candidate_used, decision.quaternion_xyzw, decision.reason,
                )
                model_decisions.append({
                    "event_id": event.event_id,
                    "mode": decision.mode.value,
                    "candidate_used": decision.candidate_used,
                    "state_version": decision.state_version,
                    "anchor_pose_id": decision.anchor_pose_id,
                    "age_ns": decision.age_ns,
                    "reason": decision.reason,
                    "quaternion_xyzw": (
                        None if decision.quaternion_xyzw is None
                        else list(decision.quaternion_xyzw)
                    ),
                })
        rows = [output_by_id[event.event_id] for event in window.events]
        windows.append({"window_id": window.registry.window_id, "events": rows})
        pose_receipts = [asdict(row) for row in model.update_receipts]
        states = [asdict(row) for row in model.state_versions]
        evidence_windows.append({
            "window_id": window.registry.window_id,
            "pose_receipts": pose_receipts,
            "state_versions": states,
            "event_decisions": model_decisions,
            "pose_receipts_sha256": canonical_sha256(pose_receipts),
            "state_versions_sha256": canonical_sha256(states),
            "event_decisions_sha256": canonical_sha256(model_decisions),
        })
    evidence = _seal_generator_evidence(
        SO3_PLL_ID, config.candidate_id, evidence_windows
    )
    return GeneratedCandidate(SO3_PLL_ID, tuple(windows), evidence)


def _candidate_registry() -> Mapping[str, FrozenCandidate]:
    return {
        RG3_ID: FrozenCandidate(
            RG3_ID, rg3.RG3_POLICY.candidate_id, Path(rg3.__file__).resolve(),
            _EXECUTABLE_SHA256[RG3_ID], _rg3_config(), _generate_rg3,
        ),
        DSPB_ID: FrozenCandidate(
            DSPB_ID, dspb.DSPBConfig().candidate_id, Path(dspb.__file__).resolve(),
            _EXECUTABLE_SHA256[DSPB_ID], _dspb_config(), _generate_dspb,
        ),
        SO3_PLL_ID: FrozenCandidate(
            SO3_PLL_ID, so3_pll.SO3PLLConfig().candidate_id,
            Path(so3_pll.__file__).resolve(), _EXECUTABLE_SHA256[SO3_PLL_ID],
            _pll_config(), _generate_pll,
        ),
    }


_CANDIDATES = _candidate_registry()


def _candidate(candidate_id: object) -> FrozenCandidate:
    if type(candidate_id) is not str or candidate_id not in _CANDIDATES:
        raise Campaign108Error("candidate ID is not in the frozen Stage3 registry")
    return _CANDIDATES[candidate_id]


def _generation_input(bundle: New108AdapterBundle) -> GenerationInput:
    """Project only neutral values; deliberately never touch selector_labels."""

    if type(bundle) is not New108AdapterBundle:
        raise Campaign108Error("locked adapter returned the wrong bundle type")
    windows = []
    expected = {row.window_id for row in bundle.neutral_registry}
    if set(bundle.event_streams) != expected or set(bundle.pose_streams) != expected:
        raise Campaign108Error("neutral adapter stream identities differ")
    for registry in bundle.neutral_registry:
        windows.append(GenerationWindow(
            registry,
            tuple(bundle.event_streams[registry.window_id]),
            tuple(bundle.pose_streams[registry.window_id]),
        ))
    return GenerationInput(tuple(windows))


def _artifact(path: Path, payload: bytes, semantic_sha256: str) -> Mapping[str, object]:
    return {
        "path": path.name,
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "semantic_sha256": semantic_sha256,
    }


def _check_unchanged(path: Path, expected: bytes, where: str) -> None:
    if _read_bytes(path, where) != expected:
        raise Campaign108Error("%s changed during the single attempt" % where)


def _artifact_paths(campaign_directory: Path, candidate_id: str) -> Mapping[str, Path]:
    prefix = candidate_id.lower()
    return {
        "attempt": campaign_directory / (prefix + ".attempt.json"),
        "generator_evidence": campaign_directory / (prefix + ".generator-evidence.json"),
        "candidate_output": campaign_directory / (prefix + ".candidate-output.json"),
        "screen_result": campaign_directory / (prefix + ".screen108-result.json"),
        "campaign_receipt": campaign_directory / (prefix + ".campaign-receipt.json"),
    }


def run_campaign108(
    candidate_id: str,
    dataset_directory: Path,
    config_path: Path,
    cncp_path: Path,
    campaign_directory: Path,
) -> Mapping[str, object]:
    """Run one frozen candidate exactly once and emit append-only artifacts."""

    spec = _candidate(candidate_id)
    config_file = Path(config_path)
    cncp_file = Path(cncp_path)
    config_bytes = _read_bytes(config_file, "candidate config")
    cncp_bytes = _read_bytes(cncp_file, "CNCP")
    config = _read_json_bytes(config_bytes, "candidate config")
    cncp = _read_json_bytes(cncp_bytes, "CNCP")
    if config != spec.expected_config:
        raise Campaign108Error("candidate config differs from the frozen ID")
    screen108.validate_cncp(cncp)
    executable_bytes = _read_bytes(spec.executable_path, "candidate executable")
    executable_sha256 = _sha256_bytes(executable_bytes)
    if executable_sha256 != spec.executable_sha256:
        raise Campaign108Error("candidate executable differs from the frozen ID")

    campaign_root = Path(campaign_directory)
    if campaign_root.exists() and (not campaign_root.is_dir() or campaign_root.is_symlink()):
        raise Campaign108Error("campaign directory must be a real directory")
    try:
        campaign_root.mkdir(mode=0o755, parents=False, exist_ok=True)
    except OSError as exc:
        raise Campaign108Error("cannot create campaign directory") from exc
    paths = _artifact_paths(campaign_root, candidate_id)
    config_sha256 = _sha256_bytes(config_bytes)
    cncp_sha256 = _sha256_bytes(cncp_bytes)
    cncp_semantic_sha256 = canonical_sha256(cncp)
    attempt_body = {
        "schema": ATTEMPT_SCHEMA,
        "candidate_id": candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "attempt_index": 1,
        "candidate_executable_sha256": executable_sha256,
        "candidate_config_sha256": config_sha256,
        "candidate_config_semantic_sha256": canonical_sha256(config),
        "cncp_sha256": cncp_sha256,
        "cncp_semantic_sha256": cncp_semantic_sha256,
        "campaign_runner_sha256": _sha256_bytes(_read_bytes(Path(__file__), "campaign runner")),
        "retry_allowed": False,
        "tuning_allowed": False,
    }
    attempt = dict(attempt_body, attempt_sha256=canonical_sha256(attempt_body))
    attempt_bytes = _json_bytes(attempt)
    _exclusive_write(paths["attempt"], attempt_bytes, "campaign attempt marker")

    # Exactly one generator call.  No labels, scores, or outcomes are present
    # in GenerationInput, and no exception path calls the generator again.
    bundle = build_locked_new108_adapter(Path(dataset_directory))
    neutral = _generation_input(bundle)
    generated = spec.generator(neutral)
    if type(generated) is not GeneratedCandidate or generated.candidate_id != candidate_id:
        raise Campaign108Error("model-specific generator returned the wrong candidate")
    evidence = generated.evidence
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("schema") != GENERATOR_EVIDENCE_SCHEMA
        or evidence.get("candidate_id") != candidate_id
        or evidence.get("model_candidate_id") != spec.model_candidate_id
    ):
        raise Campaign108Error("model-specific generator evidence differs")
    evidence_unsigned = dict(evidence)
    evidence_digest = evidence_unsigned.pop("aggregate_sha256", None)
    if evidence_digest != canonical_sha256(evidence_unsigned):
        raise Campaign108Error("model-specific generator evidence seal differs")
    evidence_bytes = _json_bytes(evidence)
    _exclusive_write(paths["generator_evidence"], evidence_bytes, "generator evidence")

    baseline = evaluate_current_cav_registry(
        bundle.neutral_registry, bundle.event_streams, bundle.pose_streams
    )
    candidate_output = screen108.seal_candidate_output(
        candidate_id,
        str(bundle.provenance_seal["aggregate_sha256"]),
        baseline.neutral_input_sha256,
        executable_sha256,
        config_sha256,
        generated.windows,
    )
    candidate_output_bytes = _json_bytes(candidate_output)
    _exclusive_write(paths["candidate_output"], candidate_output_bytes, "candidate output")

    # Detect source races before scoring.  The locked screen independently
    # re-reads executable/config and reconstructs the source-bound adapter.
    _check_unchanged(config_file, config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(spec.executable_path, executable_bytes, "candidate executable")
    screen_result = screen108.run_locked_screen108(
        Path(dataset_directory), paths["candidate_output"], spec.executable_path,
        config_file, cncp,
    )
    screen_digest = screen108.verify_screen108_result_envelope(screen_result)
    screen_bytes = _json_bytes(screen_result)
    _exclusive_write(paths["screen_result"], screen_bytes, "screen108 result")

    artifacts = {
        "attempt": _artifact(paths["attempt"], attempt_bytes, attempt["attempt_sha256"]),
        "generator_evidence": _artifact(
            paths["generator_evidence"], evidence_bytes, str(evidence_digest)
        ),
        "candidate_output": _artifact(
            paths["candidate_output"], candidate_output_bytes,
            str(candidate_output["aggregate_sha256"]),
        ),
        "screen_result": _artifact(
            paths["screen_result"], screen_bytes, screen_digest
        ),
    }
    receipt_body = {
        "schema": CAMPAIGN_SCHEMA,
        "status": "SCREEN108_SINGLE_ATTEMPT_COMPLETE",
        "candidate_id": candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "attempt_sha256": attempt["attempt_sha256"],
        "bindings": {
            "candidate_executable_sha256": executable_sha256,
            "candidate_config_sha256": config_sha256,
            "candidate_config_semantic_sha256": canonical_sha256(config),
            "cncp_sha256": cncp_sha256,
            "cncp_semantic_sha256": cncp_semantic_sha256,
            "generator_evidence_sha256": evidence_digest,
            "candidate_output_sha256": candidate_output["aggregate_sha256"],
            "screen_result_sha256": screen_digest,
        },
        "artifacts": artifacts,
        "policy": {
            "attempt_count": 1,
            "retry_performed": False,
            "tuning_performed": False,
            "labels_accessed_before_candidate_output_seal": False,
            "source_selection_changed": False,
            "external_data_accessed": False,
            "rtl_or_ppa_evaluated": False,
        },
    }
    receipt = dict(receipt_body, receipt_sha256=canonical_sha256(receipt_body))
    receipt_bytes = _json_bytes(receipt)
    _exclusive_write(paths["campaign_receipt"], receipt_bytes, "campaign receipt")
    return receipt


def verify_campaign108_receipt(
    receipt: Mapping[str, object], campaign_directory: Path
) -> str:
    """Verify a completed receipt and every append-only artifact binding."""

    required = {
        "schema", "status", "candidate_id", "model_candidate_id",
        "attempt_sha256", "bindings", "artifacts", "policy", "receipt_sha256",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        raise Campaign108Error("campaign receipt field schema differs")
    if (
        receipt["schema"] != CAMPAIGN_SCHEMA
        or receipt["status"] != "SCREEN108_SINGLE_ATTEMPT_COMPLETE"
    ):
        raise Campaign108Error("campaign receipt schema or status differs")
    spec = _candidate(receipt["candidate_id"])
    if receipt["model_candidate_id"] != spec.model_candidate_id:
        raise Campaign108Error("campaign model identity differs")
    policy = receipt["policy"]
    expected_policy = {
        "attempt_count": 1,
        "retry_performed": False,
        "tuning_performed": False,
        "labels_accessed_before_candidate_output_seal": False,
        "source_selection_changed": False,
        "external_data_accessed": False,
        "rtl_or_ppa_evaluated": False,
    }
    if not isinstance(policy, Mapping) or policy != expected_policy:
        raise Campaign108Error("campaign receipt policy boundary differs")
    unsigned = dict(receipt)
    supplied = unsigned.pop("receipt_sha256", None)
    if supplied != canonical_sha256(unsigned):
        raise Campaign108Error("campaign receipt aggregate seal differs")
    bindings = receipt["bindings"]
    binding_fields = {
        "candidate_executable_sha256", "candidate_config_sha256",
        "candidate_config_semantic_sha256", "cncp_sha256",
        "cncp_semantic_sha256", "generator_evidence_sha256",
        "candidate_output_sha256", "screen_result_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != binding_fields:
        raise Campaign108Error("campaign digest bindings differ")
    if any(
        type(bindings[field]) is not str
        or len(bindings[field]) != 64
        or any(character not in "0123456789abcdef" for character in bindings[field])
        for field in binding_fields
    ):
        raise Campaign108Error("campaign digest binding is not SHA-256")
    if bindings["candidate_executable_sha256"] != spec.executable_sha256:
        raise Campaign108Error("campaign executable binding differs")
    artifacts = receipt["artifacts"]
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "attempt", "generator_evidence", "candidate_output", "screen_result"
    }:
        raise Campaign108Error("campaign artifact index differs")
    root = Path(campaign_directory)
    expected_paths = _artifact_paths(root, str(receipt["candidate_id"]))
    decoded = {}
    for name, identity in artifacts.items():
        if not isinstance(identity, Mapping) or set(identity) != {
            "path", "size_bytes", "sha256", "semantic_sha256"
        }:
            raise Campaign108Error("campaign artifact identity differs")
        relative = identity["path"]
        if (
            type(relative) is not str
            or Path(relative).name != relative
            or relative != expected_paths[name].name
        ):
            raise Campaign108Error("campaign artifact path differs")
        payload = _read_bytes(root / relative, "%s artifact" % name)
        if len(payload) != identity["size_bytes"] or _sha256_bytes(payload) != identity["sha256"]:
            raise Campaign108Error("campaign artifact bytes differ")
        decoded[name] = _read_json_bytes(payload, "%s artifact" % name)

    attempt = decoded["attempt"]
    attempt_fields = {
        "schema", "candidate_id", "model_candidate_id", "attempt_index",
        "candidate_executable_sha256", "candidate_config_sha256",
        "candidate_config_semantic_sha256", "cncp_sha256",
        "cncp_semantic_sha256", "campaign_runner_sha256", "retry_allowed",
        "tuning_allowed", "attempt_sha256",
    }
    attempt_unsigned = dict(attempt)
    attempt_digest = attempt_unsigned.pop("attempt_sha256", None)
    if (
        set(attempt) != attempt_fields
        or attempt.get("schema") != ATTEMPT_SCHEMA
        or attempt.get("candidate_id") != receipt["candidate_id"]
        or attempt.get("model_candidate_id") != spec.model_candidate_id
        or type(attempt.get("attempt_index")) is not int
        or attempt.get("attempt_index") != 1
        or attempt.get("retry_allowed") is not False
        or attempt.get("tuning_allowed") is not False
        or attempt_digest != canonical_sha256(attempt_unsigned)
        or attempt_digest != receipt["attempt_sha256"]
        or attempt_digest != artifacts["attempt"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign attempt seal differs")
    for field in (
        "candidate_executable_sha256", "candidate_config_sha256",
        "candidate_config_semantic_sha256", "cncp_sha256",
        "cncp_semantic_sha256",
    ):
        if attempt.get(field) != bindings[field]:
            raise Campaign108Error("campaign attempt digest binding differs")

    evidence = decoded["generator_evidence"]
    evidence_unsigned = dict(evidence)
    evidence_digest = evidence_unsigned.pop("aggregate_sha256", None)
    if (
        set(evidence) != {
            "schema", "candidate_id", "model_candidate_id", "windows",
            "aggregate_sha256",
        }
        or evidence.get("schema") != GENERATOR_EVIDENCE_SCHEMA
        or evidence.get("candidate_id") != receipt["candidate_id"]
        or evidence.get("model_candidate_id") != spec.model_candidate_id
        or evidence_digest != canonical_sha256(evidence_unsigned)
        or evidence_digest != bindings["generator_evidence_sha256"]
        or evidence_digest != artifacts["generator_evidence"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign generator evidence seal differs")

    candidate_output = decoded["candidate_output"]
    output_unsigned = dict(candidate_output)
    output_digest = output_unsigned.pop("aggregate_sha256", None)
    if (
        set(candidate_output) != {
            "schema", "candidate_id", "adapter_aggregate_sha256",
            "neutral_input_sha256", "candidate_executable_sha256",
            "candidate_config_sha256", "windows", "aggregate_sha256",
        }
        or candidate_output.get("schema") != screen108.CANDIDATE_OUTPUT_SCHEMA
        or candidate_output.get("candidate_id") != receipt["candidate_id"]
        or candidate_output.get("candidate_executable_sha256")
        != bindings["candidate_executable_sha256"]
        or candidate_output.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or output_digest != canonical_sha256(output_unsigned)
        or output_digest != bindings["candidate_output_sha256"]
        or output_digest != artifacts["candidate_output"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign candidate output seal differs")

    screen_result = decoded["screen_result"]
    screen_digest = screen108.verify_screen108_result_envelope(screen_result)
    provenance = screen_result.get("provenance")
    if (
        screen_result.get("candidate_id") != receipt["candidate_id"]
        or canonical_sha256(screen_result.get("cncp"))
        != bindings["cncp_semantic_sha256"]
        or not isinstance(provenance, Mapping)
        or provenance.get("candidate_output_sha256")
        != bindings["candidate_output_sha256"]
        or provenance.get("candidate_executable_sha256")
        != bindings["candidate_executable_sha256"]
        or provenance.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or screen_digest != bindings["screen_result_sha256"]
        or screen_digest != artifacts["screen_result"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign screen result binding differs")
    return str(supplied)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run exactly one frozen Stage3 candidate on locked NEW108"
    )
    parser.add_argument("--candidate-id", choices=FROZEN_CANDIDATE_IDS, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cncp", type=Path, required=True)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = run_campaign108(
        args.candidate_id, args.dataset_dir, args.config, args.cncp,
        args.campaign_dir,
    )
    print("receipt_sha256=%s" % receipt["receipt_sha256"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Campaign108Error as exc:
        print("campaign108: %s" % exc, file=sys.stderr)
        sys.exit(2)


__all__ = [
    "CAMPAIGN_SCHEMA", "CONFIG_SCHEMA", "DSPB_ID", "FROZEN_CANDIDATE_IDS",
    "GeneratedCandidate", "GenerationInput", "GenerationWindow", "RG3_ID",
    "SO3_PLL_ID", "Campaign108Error", "frozen_candidate_config",
    "run_campaign108", "verify_campaign108_receipt",
]
