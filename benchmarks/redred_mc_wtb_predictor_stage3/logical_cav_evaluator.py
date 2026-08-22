"""Stage3-only current-CAV evaluator over frozen evaluator implementation bytes."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType
from typing import Dict, Mapping, Sequence

from benchmarks.redred_mc_wtb_so3_axis_audit import evaluator as _canonical_evaluator
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256

from .logical_cycle_replay import (
    logical_replay_authority,
    run_stage3_logical_cycle_model,
)


FROZEN_LOGICAL_REPLAY_SHA256 = (
    "7bd89f3f18aad5ee6101cd95563473f3bcbb3e9e964cf96b37cd2bb5caa7d4da"
)
FROZEN_STAGE4_EVALUATOR_SHA256 = (
    "64cf6d9aff7c4a3dec791469b5e2f010fe80d8930650f8438d80f4659b3302fd"
)
LOGICAL_EVALUATOR_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.logical_cav_evaluator/v1"
)
_PRIVATE_MODULE_NAME = (
    "benchmarks.redred_mc_wtb_predictor_stage3._stage3_private_cav_evaluator"
)
_FROZEN_EVALUATOR_PATH = Path(_canonical_evaluator.__file__).resolve()
_LOGICAL_REPLAY_PATH = Path(
    sys.modules[run_stage3_logical_cycle_model.__module__].__file__
).resolve()
_LOAD_LOCK = threading.RLock()
_PRIVATE_EVALUATOR = None  # type: object


class LogicalCAVEvaluatorError(ValueError):
    """Frozen evaluator identity or Stage3-only injection failed."""


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LogicalCAVEvaluatorError("frozen current-CAV evaluator is unavailable") from exc


def _verify_frozen_evaluator() -> None:
    if _file_sha256(_LOGICAL_REPLAY_PATH) != FROZEN_LOGICAL_REPLAY_SHA256:
        raise LogicalCAVEvaluatorError("Stage3 logical replay hash differs")
    if _file_sha256(_FROZEN_EVALUATOR_PATH) != FROZEN_STAGE4_EVALUATOR_SHA256:
        raise LogicalCAVEvaluatorError("frozen current-CAV evaluator hash differs")


def _load_private_evaluator() -> ModuleType:
    global _PRIVATE_EVALUATOR
    with _LOAD_LOCK:
        _verify_frozen_evaluator()
        if _PRIVATE_EVALUATOR is None:
            spec = importlib.util.spec_from_file_location(
                _PRIVATE_MODULE_NAME, str(_FROZEN_EVALUATOR_PATH)
            )
            if spec is None or spec.loader is None:
                raise LogicalCAVEvaluatorError("private evaluator loader is unavailable")
            module = importlib.util.module_from_spec(spec)
            if sys.modules.get(_PRIVATE_MODULE_NAME) is not None:
                raise LogicalCAVEvaluatorError("private evaluator namespace is occupied")
            sys.modules[_PRIVATE_MODULE_NAME] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(_PRIVATE_MODULE_NAME, None)
                raise
            module.run_cycle_model = run_stage3_logical_cycle_model
            module.__stage3_logical_private__ = True
            _PRIVATE_EVALUATOR = module
        module = _PRIVATE_EVALUATOR
        if (
            module is _canonical_evaluator
            or module.run_cycle_model is not run_stage3_logical_cycle_model
            or getattr(module, "__stage3_logical_private__", False) is not True
        ):
            raise LogicalCAVEvaluatorError("private Stage3 evaluator authority changed")
        return module  # type: ignore[return-value]


_IMPLEMENTATION = _load_private_evaluator()
CAVEventEvaluation = _IMPLEMENTATION.CAVEventEvaluation
CAVRegistryEvaluation = _IMPLEMENTATION.CAVRegistryEvaluation
CAVWindowEvaluation = _IMPLEMENTATION.CAVWindowEvaluation
CurrentCAVEvaluationError = _IMPLEMENTATION.CurrentCAVEvaluationError
LatencySummary = _IMPLEMENTATION.LatencySummary
NeutralEventInput = _IMPLEMENTATION.NeutralEventInput
NeutralPoseInput = _IMPLEMENTATION.NeutralPoseInput
NeutralRegistryWindow = _IMPLEMENTATION.NeutralRegistryWindow


def _registry_value(value: object) -> object:
    return NeutralRegistryWindow(
        value.window_id,  # type: ignore[attr-defined]
        value.warmup_start_ns_inclusive,  # type: ignore[attr-defined]
        value.query_start_ns_inclusive,  # type: ignore[attr-defined]
        value.query_end_ns_exclusive,  # type: ignore[attr-defined]
    )


def _event_value(value: object) -> object:
    return NeutralEventInput(
        value.event_id,  # type: ignore[attr-defined]
        value.timestamp_ns,  # type: ignore[attr-defined]
        value.polarity,  # type: ignore[attr-defined]
        value.is_query,  # type: ignore[attr-defined]
        tuple(value.sensor_ray),  # type: ignore[attr-defined]
        value.causal_pose_source_index,  # type: ignore[attr-defined]
        value.event_content_sha256,  # type: ignore[attr-defined]
        value.transform_guard_valid,  # type: ignore[attr-defined]
    )


def _pose_value(value: object) -> object:
    return NeutralPoseInput(
        value.pose_id,  # type: ignore[attr-defined]
        value.timestamp_ns,  # type: ignore[attr-defined]
        value.commit_cycle,  # type: ignore[attr-defined]
        tuple(value.quaternion_xyzw),  # type: ignore[attr-defined]
        value.pose_sha256,  # type: ignore[attr-defined]
        value.value_valid,  # type: ignore[attr-defined]
        value.arithmetic_valid,  # type: ignore[attr-defined]
    )


def _private_inputs(
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
) -> object:
    private_registry = tuple(_registry_value(value) for value in registry)
    private_events = dict(
        (window_id, tuple(_event_value(value) for value in values))
        for window_id, values in event_streams.items()
    )
    private_poses = dict(
        (window_id, tuple(_pose_value(value) for value in values))
        for window_id, values in pose_streams.items()
    )
    return private_registry, private_events, private_poses


def evaluate_current_cav_registry(
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
) -> object:
    """Evaluate only under the fixed Stage3 logical ingress authority."""

    module = _load_private_evaluator()
    converted = _private_inputs(registry, event_streams, pose_streams)
    result = module.evaluate_current_cav_registry(*converted)
    _verify_frozen_evaluator()
    return result


def verify_current_cav_evaluation_integrity(evaluation: object) -> str:
    """Replay a Stage3 logical evaluation without profile auto-detection."""

    module = _load_private_evaluator()
    result = module.verify_current_cav_evaluation_integrity(evaluation)
    _verify_frozen_evaluator()
    return result


def logical_evaluator_authority() -> Dict[str, object]:
    _load_private_evaluator()
    replay = logical_replay_authority()
    body = {
        "schema": LOGICAL_EVALUATOR_SCHEMA,
        "logical_cycle_replay_sha256": FROZEN_LOGICAL_REPLAY_SHA256,
        "frozen_stage4_evaluator_sha256": FROZEN_STAGE4_EVALUATOR_SHA256,
        "private_module_namespace": _PRIVATE_MODULE_NAME,
        "cycle_replay_authority_sha256": replay["authority_sha256"],
        "canonical_module_mutation": False,
    }
    return dict(body, authority_sha256=canonical_sha256(body))


_world_shadow = _IMPLEMENTATION._world_shadow


__all__ = (
    "CAVEventEvaluation",
    "CAVRegistryEvaluation",
    "CAVWindowEvaluation",
    "CurrentCAVEvaluationError",
    "FROZEN_LOGICAL_REPLAY_SHA256",
    "FROZEN_STAGE4_EVALUATOR_SHA256",
    "LOGICAL_EVALUATOR_SCHEMA",
    "LatencySummary",
    "LogicalCAVEvaluatorError",
    "NeutralEventInput",
    "NeutralPoseInput",
    "NeutralRegistryWindow",
    "evaluate_current_cav_registry",
    "logical_evaluator_authority",
    "verify_current_cav_evaluation_integrity",
)
