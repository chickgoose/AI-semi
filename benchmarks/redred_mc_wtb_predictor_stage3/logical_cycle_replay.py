"""Stage3-only logical ingress replay over frozen Stage4 implementation bytes.

The canonical Stage4 module remains the immutable physical 6x6 authority.  A
private module instance executes the same hash-pinned source with only its raw
ingress and staging constants fixed to eight.  The public API is deliberately
closed to the current-CAV arm and cannot select another profile.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType
from typing import Any, Dict, Mapping, Sequence

from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import model as _canonical_model
from benchmarks.redred_mc_wtb_stage4_cyclemodel import CycleModelError


FROZEN_STAGE4_MODEL_SHA256 = (
    "ac69d2e5e35f100cca1385be728814fec5e873ce0cf81a2ca4f38880100167ee"
)
FROZEN_STAGE4_API_SHA256 = (
    "8357e0b4d5579dcd1d96ade00060ecce7c7e9a254aac54f094d286b851ea46af"
)
LOGICAL_PROFILE_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.logical_ingress_profile/v1"
)
LOGICAL_REPLAY_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.logical_cycle_replay/v1"
)
_PRIVATE_MODULE_NAME = (
    "benchmarks.redred_mc_wtb_predictor_stage3._stage3_private_stage4_cyclemodel"
)
_RAW_INGRESS_LANES = 8
_INGRESS_STAGING_ENTRIES = 8
_EVENT_SERVICE_LANES = 2
_FROZEN_RAW_INGRESS_LANES = 6
_FROZEN_INGRESS_STAGING_ENTRIES = 6
_EVENT_ORDER_RULE = (
    "preserve_source_array_order_with_nondecreasing_occurrence_timestamps_and_cycles"
)
_EVENT_ID_TRANSPORT = (
    "unique_source_ids_may_decrease_private_ordinal_then_exact_restore"
)
_FROZEN_MODEL_PATH = Path(_canonical_model.__file__).resolve()
_FROZEN_API_PATH = _FROZEN_MODEL_PATH.with_name("__init__.py")
_LOAD_LOCK = threading.RLock()
_PRIVATE_MODEL = None  # type: Any


class LogicalCycleReplayError(ValueError):
    """Frozen-source identity or the closed Stage3 replay contract failed."""


@dataclass(frozen=True)
class LogicalIngressProfile:
    profile_id: str = "STAGE3_CANDIDATE_NEUTRAL_LOGICAL_REPLAY_8X8_V1"
    raw_ingress_lanes: int = _RAW_INGRESS_LANES
    ingress_staging_entries: int = _INGRESS_STAGING_ENTRIES
    scope: str = "MODEL_ONLY_LOGICAL_REPLAY_NO_RTL_OR_PPA_CLAIM"

    def to_mapping(self) -> Dict[str, object]:
        return {
            "schema": LOGICAL_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "raw_ingress_lanes": self.raw_ingress_lanes,
            "ingress_staging_entries": self.ingress_staging_entries,
            "event_service_lanes": _EVENT_SERVICE_LANES,
            "scope": self.scope,
        }

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE = LogicalIngressProfile()


def _file_sha256(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise LogicalCycleReplayError("frozen Stage4 source is unavailable") from exc
    return hashlib.sha256(payload).hexdigest()


def _verify_frozen_authority() -> None:
    if _file_sha256(_FROZEN_MODEL_PATH) != FROZEN_STAGE4_MODEL_SHA256:
        raise LogicalCycleReplayError("frozen Stage4 model hash differs")
    if _file_sha256(_FROZEN_API_PATH) != FROZEN_STAGE4_API_SHA256:
        raise LogicalCycleReplayError("frozen Stage4 API hash differs")
    if (
        _canonical_model.RAW_INGRESS_LANES != _FROZEN_RAW_INGRESS_LANES
        or _canonical_model.INGRESS_STAGING_ENTRIES
        != _FROZEN_INGRESS_STAGING_ENTRIES
        or _canonical_model.EVENT_LANES != _EVENT_SERVICE_LANES
    ):
        raise LogicalCycleReplayError("canonical Stage4 ingress globals changed")


def _load_private_model() -> ModuleType:
    global _PRIVATE_MODEL
    with _LOAD_LOCK:
        _verify_frozen_authority()
        if _PRIVATE_MODEL is None:
            spec = importlib.util.spec_from_file_location(
                _PRIVATE_MODULE_NAME, str(_FROZEN_MODEL_PATH)
            )
            if spec is None or spec.loader is None:
                raise LogicalCycleReplayError("private Stage4 loader is unavailable")
            module = importlib.util.module_from_spec(spec)
            prior = sys.modules.get(_PRIVATE_MODULE_NAME)
            if prior is not None:
                raise LogicalCycleReplayError("private Stage4 namespace is occupied")
            sys.modules[_PRIVATE_MODULE_NAME] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(_PRIVATE_MODULE_NAME, None)
                raise
            module.RAW_INGRESS_LANES = _RAW_INGRESS_LANES
            module.INGRESS_STAGING_ENTRIES = _INGRESS_STAGING_ENTRIES
            module.__stage3_logical_private__ = True
            _PRIVATE_MODEL = module
        if (
            _PRIVATE_MODEL is _canonical_model
            or _PRIVATE_MODEL.RAW_INGRESS_LANES != _RAW_INGRESS_LANES
            or _PRIVATE_MODEL.INGRESS_STAGING_ENTRIES
            != _INGRESS_STAGING_ENTRIES
            or _PRIVATE_MODEL.EVENT_LANES != _EVENT_SERVICE_LANES
            or getattr(_PRIVATE_MODEL, "__stage3_logical_private__", False) is not True
        ):
            raise LogicalCycleReplayError("private Stage3 ingress authority changed")
        return _PRIVATE_MODEL


def logical_replay_authority() -> Mapping[str, object]:
    """Return the complete non-result-bearing derivation authority."""

    _load_private_model()
    body = {
        "schema": LOGICAL_REPLAY_SCHEMA,
        "frozen_stage4_model_sha256": FROZEN_STAGE4_MODEL_SHA256,
        "frozen_stage4_api_sha256": FROZEN_STAGE4_API_SHA256,
        "private_module_namespace": _PRIVATE_MODULE_NAME,
        "profile": STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.to_mapping(),
        "profile_sha256": STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.canonical_sha256(),
        "overrides": {
            "RAW_INGRESS_LANES": _RAW_INGRESS_LANES,
            "INGRESS_STAGING_ENTRIES": _INGRESS_STAGING_ENTRIES,
        },
        "retained_event_service_lanes": _EVENT_SERVICE_LANES,
        "exposed_arm": "causal_cav",
        "event_order_rule": _EVENT_ORDER_RULE,
        "event_id_transport": _EVENT_ID_TRANSPORT,
        "canonical_module_mutation": False,
    }
    return dict(body, authority_sha256=canonical_sha256(body))


def run_stage3_logical_cycle_model(
    *,
    window_id: str,
    window_start_ns: int,
    arm: object,
    events: Sequence[object],
    poses: Sequence[object],
    synthetic_test_mode: bool = False,
) -> object:
    """Run the fixed logical 8x8 current-CAV replay in a private namespace."""

    module = _load_private_model()
    arm_value = getattr(arm, "value", arm)
    if arm_value != "causal_cav":
        raise LogicalCycleReplayError("Stage3 logical replay arm is not causal_cav")
    try:
        source_events = tuple(module.Event(
            event.event_id,
            event.timestamp_ns,
            event.transform_guard_valid,
            event.causal_pose_index,
        ) for event in events)
        source_event_ids = tuple(event.event_id for event in source_events)
        if len(set(source_event_ids)) != len(source_event_ids):
            raise CycleModelError("duplicate event IDs are forbidden")
        source_occurrence_cycles = tuple(
            module.timestamp_to_cycle(event.timestamp_ns, window_start_ns)
            for event in source_events
        )
        if any(
            right.timestamp_ns < left.timestamp_ns
            for left, right in zip(source_events, source_events[1:])
        ):
            raise CycleModelError("event timestamps must be nondecreasing")
        if any(
            right < left
            for left, right in zip(
                source_occurrence_cycles, source_occurrence_cycles[1:]
            )
        ):
            raise CycleModelError("event occurrence cycles must be nondecreasing")
        requires_id_transport = any(
            right < left
            for left, right in zip(source_event_ids, source_event_ids[1:])
        )
        private_events = source_events
        if requires_id_transport:
            private_events = tuple(
                module.Event(
                    ordinal,
                    event.timestamp_ns,
                    event.transform_guard_valid,
                    event.causal_pose_index,
                )
                for ordinal, event in enumerate(source_events)
            )
        private_poses = tuple(module.PosePacket(
            pose.pose_id,
            pose.timestamp_ns,
            pose.commit_cycle,
            module.PoseSource(getattr(pose.source, "value", pose.source)),
            pose.pose_sha256,
            pose.value_valid,
            pose.arithmetic_valid,
        ) for pose in poses)
        try:
            result = module.run_cycle_model(
                window_id=window_id,
                window_start_ns=window_start_ns,
                arm=module.Arm.CAUSAL_CAV,
                events=private_events,
                poses=private_poses,
                synthetic_test_mode=synthetic_test_mode,
            )
        except module.CycleModelError as exc:
            message = str(exc)
            if message == "more than six source records map to one occurrence cycle":
                message = "more than eight source records map to one occurrence cycle"
            raise CycleModelError(message) from None
    except AttributeError as exc:
        raise LogicalCycleReplayError("logical replay input interface differs") from exc
    if requires_id_transport:
        if (
            len(result.records) != len(source_event_ids)
            or len(result.cycle_receipts) != len(source_event_ids)
        ):
            raise LogicalCycleReplayError("logical replay transport cardinality differs")
        records = tuple(
            replace(record, event_id=event_id)
            for event_id, record in zip(source_event_ids, result.records)
        )
        receipts = tuple(
            replace(
                receipt,
                event_id=event_id,
                decision_record_sha256=record.canonical_sha256(),
            )
            for event_id, record, receipt in zip(
                source_event_ids, records, result.cycle_receipts
            )
        )
        result = replace(
            result,
            records=records,
            decision_records_sha256=module._canonical_sha256(
                [record.to_mapping() for record in records]
            ),
            cycle_receipts=receipts,
            cycle_receipts_sha256=module._canonical_sha256(
                [receipt.to_mapping() for receipt in receipts]
            ),
        )
    if (
        result.raw_ingress_lanes != _RAW_INGRESS_LANES
        or result.ingress_staging_entries != _INGRESS_STAGING_ENTRIES
        or result.event_lanes != _EVENT_SERVICE_LANES
    ):
        raise LogicalCycleReplayError("logical replay result profile differs")
    _verify_frozen_authority()
    return result


__all__ = (
    "FROZEN_STAGE4_API_SHA256",
    "FROZEN_STAGE4_MODEL_SHA256",
    "LOGICAL_PROFILE_SCHEMA",
    "LOGICAL_REPLAY_SCHEMA",
    "LogicalCycleReplayError",
    "LogicalIngressProfile",
    "STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE",
    "logical_replay_authority",
    "run_stage3_logical_cycle_model",
)
