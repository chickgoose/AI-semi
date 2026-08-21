"""Score-after-receipt Stage-4 metrics with frame-isolated causal banks."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference.reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    ReferenceObservation,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    ComparisonContract,
    DecisionReceipt,
    DecisionRecord,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract.receipt import DECISION_ARMS


Ray = Tuple[float, float, float]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NUMERIC_DISPOSITIONS = frozenset(("GO_NUMERIC", "HOLD", "STOP"))
_SHADOW_TRANSFORMS = frozenset(
    ("occurrence_zoh", "occurrence_cav", "delayed_slerp", "oracle_prefix")
)
_MANIFEST_ARTIFACTS = frozenset(
    (
        "protocol",
        "registry",
        "arm_parameters",
        "generator",
        "cycle_model",
        "scorer",
        "sources",
        "runtime",
    )
)


class ScoringError(ValueError):
    """The score-after-receipt or metric contract was violated."""


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScoringError("%s must be a non-negative integer" % name)
    return value


def _finite_nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoringError("%s must be a finite non-negative number" % name)
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ScoringError("%s must be a finite non-negative number" % name)
    return result


def _checked_ray(value: object, name: str) -> Ray:
    if type(value) is not tuple or len(value) != 3:
        raise ScoringError("%s must be an immutable three-component tuple" % name)
    if any(isinstance(component, bool) for component in value):
        raise ScoringError("%s must contain finite numeric components" % name)
    try:
        checked = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScoringError("%s must contain finite numeric components" % name) from exc
    if not all(math.isfinite(component) for component in checked):
        raise ScoringError("%s must contain finite components" % name)
    norm = math.sqrt(math.fsum(component * component for component in checked))
    if abs(norm - 1.0) > 1.0e-9:
        raise ScoringError("%s must be a normalized ray" % name)
    return checked  # type: ignore[return-value]


def _checked_id_tuple(value: object, name: str) -> Tuple[int, ...]:
    if type(value) is not tuple:
        raise ScoringError("%s must be an immutable tuple" % name)
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in value
    ):
        raise ScoringError("%s contains an invalid event ID" % name)
    if len(set(value)) != len(value):
        raise ScoringError("%s contains duplicate event IDs" % name)
    return value


@dataclass(frozen=True)
class ShadowRay:
    """A deterministic arm shadow and its score-free pose provenance."""

    arm: str
    ray: Ray
    transform: str
    pose_ids: Tuple[int, ...]
    pose_timestamps_ns: Tuple[int, ...]
    pose_commit_cycles: Tuple[int, ...]
    pose_sha256: Tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.arm) is not str or self.arm not in DECISION_ARMS:
            raise ScoringError("shadow arm is not frozen")
        object.__setattr__(self, "ray", _checked_ray(self.ray, "shadow ray"))
        if type(self.transform) is not str or self.transform not in _SHADOW_TRANSFORMS:
            raise ScoringError("shadow transform is not frozen")
        groups = (
            self.pose_ids,
            self.pose_timestamps_ns,
            self.pose_commit_cycles,
            self.pose_sha256,
        )
        if not all(type(group) is tuple for group in groups):
            raise ScoringError("shadow pose provenance must use immutable tuples")
        if len(set(len(group) for group in groups)) != 1 or not self.pose_ids:
            raise ScoringError("shadow pose provenance is missing or misaligned")
        if len(self.pose_ids) > 2:
            raise ScoringError("shadow pose provenance exceeds two poses")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for group in groups[:3]
            for value in group
        ):
            raise ScoringError("shadow pose provenance contains an invalid integer")
        if any(
            type(value) is not str or _SHA256.fullmatch(value) is None
            for value in self.pose_sha256
        ):
            raise ScoringError("shadow pose provenance contains an invalid digest")
        if any(right <= left for left, right in zip(self.pose_ids, self.pose_ids[1:])):
            raise ScoringError("shadow pose IDs must be strictly increasing")
        if any(
            right <= left
            for left, right in zip(
                self.pose_timestamps_ns, self.pose_timestamps_ns[1:]
            )
        ):
            raise ScoringError("shadow pose timestamps must be strictly increasing")
        required_poses = (
            2 if self.transform in ("occurrence_cav", "delayed_slerp") else 1
        )
        if len(self.pose_ids) != required_poses:
            raise ScoringError("shadow transform has the wrong pose arity")

    def provenance_rows(self) -> Tuple[Tuple[int, int, int, str], ...]:
        return tuple(zip(
            self.pose_ids,
            self.pose_timestamps_ns,
            self.pose_commit_cycles,
            self.pose_sha256,
        ))

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "arm": self.arm,
            "ray": list(self.ray),
            "transform": self.transform,
            "pose_ids": list(self.pose_ids),
            "pose_timestamps_ns": list(self.pose_timestamps_ns),
            "pose_commit_cycles": list(self.pose_commit_cycles),
            "pose_sha256": list(self.pose_sha256),
        }


@dataclass(frozen=True)
class RayEvent:
    """One sensor-frame ray plus a deterministic world shadow for every arm."""

    window_id: str
    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: Ray
    world_shadow_rays: Tuple[ShadowRay, ...]

    def __post_init__(self) -> None:
        if type(self.window_id) is not str or not self.window_id:
            raise ScoringError("window_id must be a non-empty string")
        object.__setattr__(self, "event_id", _nonnegative_int(self.event_id, "event_id"))
        object.__setattr__(
            self, "timestamp_ns", _nonnegative_int(self.timestamp_ns, "timestamp_ns")
        )
        if isinstance(self.polarity, bool) or self.polarity not in (0, 1):
            raise ScoringError("polarity must be integer zero or one")
        if type(self.is_query) is not bool:
            raise ScoringError("is_query must be bool")
        object.__setattr__(self, "sensor_ray", _checked_ray(self.sensor_ray, "sensor_ray"))
        if type(self.world_shadow_rays) is not tuple:
            raise ScoringError("world_shadow_rays must be an immutable tuple")
        checked = []
        names = []
        for index, row in enumerate(self.world_shadow_rays):
            if not isinstance(row, ShadowRay):
                raise ScoringError(
                    "world shadow row %d is not immutable ShadowRay" % index
                )
            names.append(row.arm)
            checked.append(row)
        if len(set(names)) != len(names) or set(names) != set(DECISION_ARMS):
            raise ScoringError("every event requires exactly one shadow ray for every arm")
        object.__setattr__(
            self,
            "world_shadow_rays",
            tuple(sorted(checked, key=lambda row: row.arm)),
        )

    def world_shadow(self, arm: str) -> ShadowRay:
        for shadow in self.world_shadow_rays:
            if shadow.arm == arm:
                return shadow
        raise ScoringError("arm world shadow is missing")

    def world_ray(self, arm: str) -> Ray:
        return self.world_shadow(arm).ray

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "timestamp_ns": self.timestamp_ns,
            "polarity": self.polarity,
            "is_query": self.is_query,
            "sensor_ray": list(self.sensor_ray),
            "world_shadow_rays": [
                shadow.to_mapping() for shadow in self.world_shadow_rays
            ],
        }


@dataclass(frozen=True)
class ScoreInputManifest:
    """Pre-frozen binding of score-free inputs and execution artifacts."""

    window_id: str
    arm: str
    decision_receipt_sha256: str
    score_free_accounting_sha256: str
    ray_events_sha256: str
    artifact_sha256: Tuple[Tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.window_id) is not str or not self.window_id:
            raise ScoringError("manifest window_id must be a non-empty string")
        if type(self.arm) is not str or self.arm not in DECISION_ARMS:
            raise ScoringError("manifest arm is not frozen")
        for value in (
            self.decision_receipt_sha256,
            self.score_free_accounting_sha256,
            self.ray_events_sha256,
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ScoringError("manifest input digest is invalid")
        if type(self.artifact_sha256) is not tuple:
            raise ScoringError("manifest artifacts must be an immutable tuple")
        checked = []
        names = []
        for index, row in enumerate(self.artifact_sha256):
            if (
                type(row) is not tuple
                or len(row) != 2
                or type(row[0]) is not str
                or type(row[1]) is not str
                or _SHA256.fullmatch(row[1]) is None
            ):
                raise ScoringError("manifest artifact row %d is invalid" % index)
            names.append(row[0])
            checked.append(row)
        if len(set(names)) != len(names) or set(names) != set(_MANIFEST_ARTIFACTS):
            raise ScoringError("manifest does not bind every frozen artifact class")
        object.__setattr__(self, "artifact_sha256", tuple(sorted(checked)))

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "schema": "redred.mc_wtb.stage4_score_input_manifest/v1",
            "window_id": self.window_id,
            "arm": self.arm,
            "decision_receipt_sha256": self.decision_receipt_sha256,
            "score_free_accounting_sha256": self.score_free_accounting_sha256,
            "ray_events_sha256": self.ray_events_sha256,
            "artifact_sha256": dict(self.artifact_sha256),
        }

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class ScoreFreeAccounting:
    """Pre-score rate/cost classification bound independently of losses."""

    window_id: str
    arm: str
    baseline_retire_cycles: Tuple[Tuple[int, int], ...]
    attempted_correction_event_ids: Tuple[int, ...]
    freshness_veto_event_ids: Tuple[int, ...]
    invalid_pose_bypass_event_ids: Tuple[int, ...]
    operational_waste_event_ids: Tuple[int, ...]
    peak_buffer_entries: int
    minimum_zero_loss_buffer_entries: int
    buffer_bit_cycles: int
    pose_bandwidth_bits_per_second: int
    event_bandwidth_bits_per_second: int
    incremental_state_bits: int
    source_overrun_events: int = 0
    accepted_event_loss: int = 0
    causality_violations: int = 0
    leakage_violations: int = 0

    def __post_init__(self) -> None:
        if type(self.window_id) is not str or not self.window_id:
            raise ScoringError("accounting window_id must be a non-empty string")
        if type(self.arm) is not str or self.arm not in DECISION_ARMS:
            raise ScoringError("accounting arm is not frozen")
        if type(self.baseline_retire_cycles) is not tuple:
            raise ScoringError("baseline_retire_cycles must be an immutable tuple")
        baseline_ids = []
        for row in self.baseline_retire_cycles:
            if type(row) is not tuple or len(row) != 2:
                raise ScoringError("baseline retirement row is invalid")
            baseline_ids.append(_nonnegative_int(row[0], "baseline event ID"))
            _nonnegative_int(row[1], "baseline retirement cycle")
        if len(set(baseline_ids)) != len(baseline_ids):
            raise ScoringError("baseline retirement IDs are duplicated")
        for field in (
            "attempted_correction_event_ids",
            "freshness_veto_event_ids",
            "invalid_pose_bypass_event_ids",
            "operational_waste_event_ids",
        ):
            _checked_id_tuple(getattr(self, field), field)
        for field in (
            "peak_buffer_entries",
            "minimum_zero_loss_buffer_entries",
            "buffer_bit_cycles",
            "pose_bandwidth_bits_per_second",
            "event_bandwidth_bits_per_second",
            "incremental_state_bits",
            "source_overrun_events",
            "accepted_event_loss",
            "causality_violations",
            "leakage_violations",
        ):
            object.__setattr__(self, field, _nonnegative_int(getattr(self, field), field))

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "arm": self.arm,
            "baseline_retire_cycles": [list(row) for row in self.baseline_retire_cycles],
            "attempted_correction_event_ids": list(self.attempted_correction_event_ids),
            "freshness_veto_event_ids": list(self.freshness_veto_event_ids),
            "invalid_pose_bypass_event_ids": list(self.invalid_pose_bypass_event_ids),
            "operational_waste_event_ids": list(self.operational_waste_event_ids),
            "peak_buffer_entries": self.peak_buffer_entries,
            "minimum_zero_loss_buffer_entries": self.minimum_zero_loss_buffer_entries,
            "buffer_bit_cycles": self.buffer_bit_cycles,
            "pose_bandwidth_bits_per_second": self.pose_bandwidth_bits_per_second,
            "event_bandwidth_bits_per_second": self.event_bandwidth_bits_per_second,
            "incremental_state_bits": self.incremental_state_bits,
            "source_overrun_events": self.source_overrun_events,
            "accepted_event_loss": self.accepted_event_loss,
            "causality_violations": self.causality_violations,
            "leakage_violations": self.leakage_violations,
        }

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class LatencySummary:
    count: int
    mean_cycles: float
    p50_cycles: int
    p95_cycles: int
    p99_cycles: int
    max_cycles: int


def nearest_rank_latency(values: Iterable[Tuple[int, int]]) -> LatencySummary:
    """Summarize ``(event_id, cycles)`` using the frozen nearest-rank rule."""

    checked = []
    for event_id, cycles in values:
        checked.append((
            _nonnegative_int(cycles, "latency_cycles"),
            _nonnegative_int(event_id, "latency event_id"),
        ))
    if not checked:
        raise ScoringError("latency population must not be empty")
    checked.sort()  # (latency_cycles, event_id)
    count = len(checked)

    def rank(fraction: float) -> int:
        return checked[int(math.ceil(fraction * count)) - 1][0]

    return LatencySummary(
        count,
        math.fsum(float(row[0]) for row in checked) / count,
        rank(0.50),
        rank(0.95),
        rank(0.99),
        checked[-1][0],
    )


@dataclass(frozen=True)
class EventLoss:
    event_id: int
    sensor_loss: float
    world_shadow_loss: float
    policy_loss: float
    enabled: bool
    quality_waste: bool
    sensor_reference_event_id: int
    world_reference_event_id: int
    occurrence_latency_cycles: int
    added_latency_cycles: int

    def __post_init__(self) -> None:
        _nonnegative_int(self.event_id, "event loss ID")
        for name in ("sensor_loss", "world_shadow_loss", "policy_loss"):
            _finite_nonnegative(getattr(self, name), name)
        if type(self.enabled) is not bool or type(self.quality_waste) is not bool:
            raise ScoringError("enabled and quality_waste must be bool")
        expected_policy_loss = self.world_shadow_loss if self.enabled else self.sensor_loss
        if self.policy_loss != expected_policy_loss:
            raise ScoringError("policy loss does not match the sealed enable decision")
        expected_quality_waste = self.enabled and self.world_shadow_loss >= self.sensor_loss
        if self.quality_waste != expected_quality_waste:
            raise ScoringError("quality-waste flag differs; ties must count as waste")
        _nonnegative_int(self.sensor_reference_event_id, "sensor reference event ID")
        _nonnegative_int(self.world_reference_event_id, "world reference event ID")
        _nonnegative_int(self.occurrence_latency_cycles, "occurrence latency")
        _nonnegative_int(self.added_latency_cycles, "added latency")


def _ordered_fsum(events: Sequence[EventLoss], field: str) -> float:
    return math.fsum(
        float(getattr(event, field)) for event in sorted(events, key=lambda row: row.event_id)
    )


def _effect(events: Sequence[EventLoss], policy_field: str) -> float:
    sensor = _ordered_fsum(events, "sensor_loss")
    if not math.isfinite(sensor) or sensor <= 0.0:
        raise ScoringError("sensor loss denominator must be finite and positive")
    policy = _ordered_fsum(events, policy_field)
    result = 1.0 - policy / sensor
    if not math.isfinite(result):
        raise ScoringError("effect is non-finite")
    return result


def is_positive_window(effect: float) -> bool:
    """Apply the frozen exclusive ``R_window > 1e-6`` rule."""

    checked = float(effect)
    if not math.isfinite(checked):
        raise ScoringError("window effect must be finite")
    return checked > 1.0e-6


@dataclass(frozen=True)
class WindowMetrics:
    window_id: str
    arm: str
    manifest_sha256: str
    receipt_sha256: str
    accounting_sha256: str
    event_losses: Tuple[EventLoss, ...]
    freshness_veto_events: int
    invalid_pose_bypass_events: int
    attempted_corrections: int
    operational_waste_events: int
    peak_buffer_entries: int
    minimum_zero_loss_buffer_entries: int
    buffer_bit_cycles: int
    pose_bandwidth_bits_per_second: int
    event_bandwidth_bits_per_second: int
    incremental_state_bits: int
    source_overrun_events: int
    accepted_event_loss: int
    causality_violations: int
    leakage_violations: int

    def __post_init__(self) -> None:
        if type(self.window_id) is not str or not self.window_id:
            raise ScoringError("metrics window_id is invalid")
        if self.arm not in DECISION_ARMS:
            raise ScoringError("metrics arm is invalid")
        if any(
            _SHA256.fullmatch(value) is None
            for value in (
                self.manifest_sha256,
                self.receipt_sha256,
                self.accounting_sha256,
            )
        ):
            raise ScoringError("metrics digest is invalid")
        if type(self.event_losses) is not tuple or not self.event_losses:
            raise ScoringError("window must contain immutable event losses")
        if any(not isinstance(event, EventLoss) for event in self.event_losses):
            raise ScoringError("window losses must contain EventLoss values")
        ids = tuple(event.event_id for event in self.event_losses)
        if len(set(ids)) != len(ids):
            raise ScoringError("window event losses contain duplicate IDs")
        for field in (
            "freshness_veto_events",
            "invalid_pose_bypass_events",
            "attempted_corrections",
            "operational_waste_events",
            "peak_buffer_entries",
            "minimum_zero_loss_buffer_entries",
            "buffer_bit_cycles",
            "pose_bandwidth_bits_per_second",
            "event_bandwidth_bits_per_second",
            "incremental_state_bits",
            "source_overrun_events",
            "accepted_event_loss",
            "causality_violations",
            "leakage_violations",
        ):
            _nonnegative_int(getattr(self, field), field)
        enabled = self.enabled_events
        if self.attempted_corrections != enabled + self.operational_waste_events:
            raise ScoringError("attempted corrections do not partition into success/waste")
        raw = self.accepted_events - enabled
        if raw != (
            self.freshness_veto_events
            + self.invalid_pose_bypass_events
            + self.operational_waste_events
        ):
            raise ScoringError("raw bypass events are not exhaustively classified")

    @property
    def accepted_events(self) -> int:
        return len(self.event_losses)

    @property
    def enabled_events(self) -> int:
        return sum(1 for event in self.event_losses if event.enabled)

    @property
    def quality_waste_events(self) -> int:
        return sum(1 for event in self.event_losses if event.quality_waste)

    @property
    def sensor_loss_sum(self) -> float:
        return _ordered_fsum(self.event_losses, "sensor_loss")

    @property
    def policy_loss_sum(self) -> float:
        return _ordered_fsum(self.event_losses, "policy_loss")

    @property
    def all_event_effect(self) -> float:
        return _effect(self.event_losses, "policy_loss")

    @property
    def enabled_only_effect(self) -> Optional[float]:
        enabled = tuple(event for event in self.event_losses if event.enabled)
        return None if not enabled else _effect(enabled, "world_shadow_loss")

    @property
    def positive_window(self) -> bool:
        return is_positive_window(self.all_event_effect)

    @property
    def enable_rate(self) -> float:
        return float(self.enabled_events) / self.accepted_events

    @property
    def freshness_veto_rate(self) -> float:
        return float(self.freshness_veto_events) / self.accepted_events

    @property
    def invalid_pose_bypass_rate(self) -> float:
        return float(self.invalid_pose_bypass_events) / self.accepted_events

    @property
    def operational_waste_rate(self) -> Optional[float]:
        if self.attempted_corrections == 0:
            return None
        return float(self.operational_waste_events) / self.attempted_corrections

    @property
    def quality_waste_rate(self) -> Optional[float]:
        if self.enabled_events == 0:
            return None
        return float(self.quality_waste_events) / self.enabled_events

    @property
    def occurrence_latency(self) -> LatencySummary:
        return nearest_rank_latency(
            (event.event_id, event.occurrence_latency_cycles)
            for event in self.event_losses
        )

    @property
    def added_latency(self) -> LatencySummary:
        return nearest_rank_latency(
            (event.event_id, event.added_latency_cycles) for event in self.event_losses
        )


def _verify_prescore_binding(
    contract: ComparisonContract,
    receipt: DecisionReceipt,
    records: Tuple[DecisionRecord, ...],
    accounting: ScoreFreeAccounting,
    manifest: ScoreInputManifest,
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
    expected_accounting_sha256: str,
) -> None:
    """Verify all score-free digests before any ray is inspected or loss joined."""

    if not isinstance(contract, ComparisonContract):
        raise ScoringError("contract must be a validated ComparisonContract")
    if not isinstance(receipt, DecisionReceipt):
        raise ScoringError("receipt must be a DecisionReceipt")
    if not isinstance(manifest, ScoreInputManifest):
        raise ScoringError("manifest must be immutable ScoreInputManifest")
    if _SHA256.fullmatch(expected_manifest_sha256 or "") is None or (
        manifest.canonical_sha256() != expected_manifest_sha256
    ):
        raise ScoringError("score input manifest digest differs before scoring")
    if _SHA256.fullmatch(expected_receipt_sha256 or "") is None or (
        receipt.canonical_sha256() != expected_receipt_sha256
    ):
        raise ScoringError("decision receipt digest differs before scoring")
    if not isinstance(accounting, ScoreFreeAccounting):
        raise ScoringError("accounting must be immutable ScoreFreeAccounting")
    if _SHA256.fullmatch(expected_accounting_sha256 or "") is None or (
        accounting.canonical_sha256() != expected_accounting_sha256
    ):
        raise ScoringError("score-free accounting digest differs before scoring")
    if receipt.comparison_contract_sha256 != contract.canonical_sha256:
        raise ScoringError("receipt contract digest differs")
    if receipt.registry_sha256 != contract.registry["sha256"]:
        raise ScoringError("receipt registry digest differs")
    artifacts = dict(manifest.artifact_sha256)
    if artifacts["protocol"] != contract.canonical_sha256:
        raise ScoringError("manifest protocol digest differs")
    if artifacts["registry"] != contract.registry["sha256"]:
        raise ScoringError("manifest registry digest differs")
    if (
        manifest.window_id != receipt.window_id
        or manifest.arm != receipt.arm
        or manifest.decision_receipt_sha256 != expected_receipt_sha256
        or manifest.score_free_accounting_sha256 != expected_accounting_sha256
    ):
        raise ScoringError("manifest identity/input binding differs")
    if any(not isinstance(record, DecisionRecord) for record in records):
        raise ScoringError("scoring accepts immutable DecisionRecord values only")
    record_digest = canonical_sha256([record.to_mapping() for record in records])
    if record_digest != receipt.decision_records_sha256:
        raise ScoringError("decision records differ from the sealed receipt")
    if len(records) != receipt.expected_events or len(records) != receipt.retired_records:
        raise ScoringError("receipt decision conservation differs")
    if any(record.window_id != receipt.window_id for record in records):
        raise ScoringError("decision window differs from receipt")
    if any(record.arm != receipt.arm for record in records):
        raise ScoringError("decision arm differs from receipt")
    if accounting.window_id != receipt.window_id or accounting.arm != receipt.arm:
        raise ScoringError("score-free accounting identity differs from receipt")


def _validate_rays(
    events: Sequence[RayEvent], window_id: str
) -> Tuple[RayEvent, ...]:
    if type(events) is not tuple or not events:
        raise ScoringError("ray events must be a non-empty immutable tuple")
    if any(not isinstance(event, RayEvent) for event in events):
        raise ScoringError("ray events must contain only RayEvent values")
    if any(event.window_id != window_id for event in events):
        raise ScoringError("ray event window differs")
    if any(right.timestamp_ns < left.timestamp_ns for left, right in zip(events, events[1:])):
        raise ScoringError("ray event timestamps move backwards")
    ids = tuple(event.event_id for event in events)
    if len(set(ids)) != len(ids):
        raise ScoringError("ray event IDs are duplicated")
    return tuple(events)


def _pose_rows(
    ids: Tuple[int, ...],
    timestamps: Tuple[int, ...],
    commits: Tuple[int, ...],
    hashes: Tuple[str, ...],
) -> Tuple[Tuple[int, int, int, str], ...]:
    return tuple(zip(ids, timestamps, commits, hashes))


def _latest_occurrence_zoh(
    event: RayEvent, record: DecisionRecord, shadow: ShadowRay
) -> None:
    occurrence = _pose_rows(
        record.occurrence_pose_ids,
        record.occurrence_pose_timestamps_ns,
        record.occurrence_pose_commit_cycles,
        record.occurrence_pose_sha256,
    )
    if not occurrence:
        raise ScoringError("causal shadow lacks an occurrence-snapshot pose")
    if (
        shadow.transform != "occurrence_zoh"
        or shadow.provenance_rows() != occurrence[-1:]
    ):
        raise ScoringError(
            "causal bypass shadow must use latest occurrence-snapshot ZOH"
        )
    if shadow.pose_timestamps_ns[-1] > event.timestamp_ns:
        raise ScoringError("causal shadow pose is future at occurrence")


def _validate_shadow_provenance(
    event: RayEvent, record: DecisionRecord
) -> None:
    shadow = event.world_shadow(record.arm)
    occurrence = _pose_rows(
        record.occurrence_pose_ids,
        record.occurrence_pose_timestamps_ns,
        record.occurrence_pose_commit_cycles,
        record.occurrence_pose_sha256,
    )
    used = _pose_rows(
        record.used_pose_ids,
        record.used_pose_timestamps_ns,
        record.used_pose_commit_cycles,
        record.used_pose_sha256,
    )

    if record.arm == "zoh_freshness":
        _latest_occurrence_zoh(event, record, shadow)
        if (
            record.disposition == "corrected_world_ray"
            and used != shadow.provenance_rows()
        ):
            raise ScoringError("enabled ZOH shadow differs from the used occurrence pose")
        return
    if record.arm == "causal_cav":
        if record.disposition == "raw_bypass" or shadow.transform == "occurrence_zoh":
            _latest_occurrence_zoh(event, record, shadow)
            if (
                record.disposition == "corrected_world_ray"
                and used != shadow.provenance_rows()
            ):
                raise ScoringError("enabled ZOH fallback differs from its used pose")
            return
        if shadow.transform != "occurrence_cav" or len(occurrence) < 2:
            raise ScoringError("causal CAV shadow lacks its occurrence-snapshot pair")
        if shadow.provenance_rows() != occurrence[-2:]:
            raise ScoringError("causal CAV shadow rereads pose state after occurrence")
        if used != shadow.provenance_rows():
            raise ScoringError("enabled CAV shadow differs from its used pose pair")
        age_ns = event.timestamp_ns - shadow.pose_timestamps_ns[-1]
        interval_ns = shadow.pose_timestamps_ns[-1] - shadow.pose_timestamps_ns[-2]
        if age_ns < 0 or age_ns > min(5_000_000, interval_ns):
            raise ScoringError("causal CAV shadow used CAV outside the frozen horizon")
        return
    if record.arm == "delayed_exact":
        if shadow.transform != "delayed_slerp" or shadow.provenance_rows() != used:
            raise ScoringError("delayed shadow differs from its declared right bracket")
        return
    if record.arm == "oracle_resampled_groundtruth_1khz":
        if not occurrence:
            raise ScoringError("oracle shadow lacks a serialized packet prefix")
        if (
            shadow.transform != "oracle_prefix"
            or shadow.provenance_rows() != occurrence[-1:]
        ):
            raise ScoringError("oracle shadow differs from its serialized packet prefix")
        if (
            record.disposition == "corrected_world_ray"
            and used != shadow.provenance_rows()
        ):
            raise ScoringError("enabled oracle shadow differs from its used packet")
        return
    raise ScoringError("shadow arm is not frozen")


def _validate_warmup_shadow_shapes(events: Sequence[RayEvent]) -> None:
    allowed = {
        "zoh_freshness": frozenset(("occurrence_zoh",)),
        "causal_cav": frozenset(("occurrence_zoh", "occurrence_cav")),
        "delayed_exact": frozenset(("delayed_slerp",)),
        "oracle_resampled_groundtruth_1khz": frozenset(("oracle_prefix",)),
    }
    for event in events:
        if event.is_query:
            continue
        for shadow in event.world_shadow_rays:
            if shadow.transform not in allowed[shadow.arm]:
                raise ScoringError("warm-up shadow transform does not match its arm")


def _validate_accounting_partition(
    accounting: ScoreFreeAccounting,
    query_ids: Tuple[int, ...],
    decisions: Mapping[int, DecisionRecord],
) -> Dict[int, int]:
    query_set = set(query_ids)
    baseline = dict(accounting.baseline_retire_cycles)
    if set(baseline) != query_set or len(baseline) != len(query_ids):
        raise ScoringError("baseline retirement cycles do not cover query events exactly")
    categories = (
        set(accounting.freshness_veto_event_ids),
        set(accounting.invalid_pose_bypass_event_ids),
        set(accounting.operational_waste_event_ids),
    )
    if any(not category <= query_set for category in categories):
        raise ScoringError("accounting category contains a non-query event")
    if any(left & right for index, left in enumerate(categories) for right in categories[index + 1:]):
        raise ScoringError("raw bypass accounting categories overlap")
    enabled = {
        event_id for event_id, record in decisions.items()
        if record.disposition == "corrected_world_ray"
    }
    raw = query_set - enabled
    operational = categories[2]
    attempted = set(accounting.attempted_correction_event_ids)
    if attempted != enabled | operational:
        raise ScoringError("attempted corrections are not enabled plus operational waste")
    if set().union(*categories) != raw:
        raise ScoringError("raw bypass accounting is not exhaustive")
    if any(decisions[event_id].disposition != "raw_bypass" for event_id in raw):
        raise ScoringError("accounting raw partition contradicts decision disposition")
    return baseline


def score_window(
    contract: ComparisonContract,
    receipt: DecisionReceipt,
    decision_records: Sequence[DecisionRecord],
    ray_events: Sequence[RayEvent],
    accounting: ScoreFreeAccounting,
    manifest: ScoreInputManifest,
    *,
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
    expected_accounting_sha256: str,
    bank_capacity_per_polarity: int = 256,
    bank_max_age_ns: int = 2_000_000,
) -> WindowMetrics:
    """Join losses only after validating immutable score-free receipt digests."""

    records = tuple(decision_records)
    _verify_prescore_binding(
        contract,
        receipt,
        records,
        accounting,
        manifest,
        expected_manifest_sha256,
        expected_receipt_sha256,
        expected_accounting_sha256,
    )
    events = _validate_rays(ray_events, receipt.window_id)
    if (
        canonical_sha256([event.to_mapping() for event in events])
        != manifest.ray_events_sha256
    ):
        raise ScoringError("ray/provenance inputs differ from pre-frozen manifest")
    _validate_warmup_shadow_shapes(events)
    query = tuple(event for event in events if event.is_query)
    if not query:
        raise ScoringError("window contains no query events")
    query_ids = tuple(event.event_id for event in query)
    if canonical_sha256(list(query_ids)) != receipt.ordered_event_ids_sha256:
        raise ScoringError("query ray identity/order differs from sealed receipt")
    if tuple(record.event_id for record in records) != query_ids:
        raise ScoringError("query rays and decisions are not in the same order")
    decisions = dict((record.event_id, record) for record in records)
    for event in query:
        if decisions[event.event_id].event_timestamp_ns != event.timestamp_ns:
            raise ScoringError("decision and ray timestamps differ")
        _validate_shadow_provenance(event, decisions[event.event_id])
    baseline = _validate_accounting_partition(accounting, query_ids, decisions)

    config = CausalReferenceConfig(bank_capacity_per_polarity, bank_max_age_ns)
    sensor_bank = CausalReferenceBank(config)
    world_bank = CausalReferenceBank(config)
    sensor_scores = sensor_bank.process(
        ReferenceObservation(
            event.event_id, event.timestamp_ns, event.polarity, event.sensor_ray
        )
        for event in events
    )
    world_scores = world_bank.process(
        ReferenceObservation(
            event.event_id,
            event.timestamp_ns,
            event.polarity,
            event.world_ray(receipt.arm),
        )
        for event in events
    )
    sensor_by_id = dict((score.event_id, score) for score in sensor_scores)
    world_by_id = dict((score.event_id, score) for score in world_scores)
    event_ids = tuple(event.event_id for event in events)
    if (
        len(sensor_scores) != len(events)
        or len(world_scores) != len(events)
        or set(sensor_by_id) != set(event_ids)
        or set(world_by_id) != set(event_ids)
    ):
        raise ScoringError("missing event loss is a protocol failure")

    joined = []
    for event in query:
        sensor = sensor_by_id[event.event_id]
        world = world_by_id[event.event_id]
        if not sensor.reference_available or sensor.angular_cost_rad is None:
            raise ScoringError("query event lacks a same-frame sensor reference")
        if not world.reference_available or world.angular_cost_rad is None:
            raise ScoringError("query event lacks a same-frame world-shadow reference")
        if sensor.reference_event_id is None or world.reference_event_id is None:
            raise ScoringError("query reference identity is unavailable")
        record = decisions[event.event_id]
        enabled = record.disposition == "corrected_world_ray"
        sensor_loss = float(sensor.angular_cost_rad)
        world_loss = float(world.angular_cost_rad)
        baseline_cycle = baseline[event.event_id]
        if record.retire_cycle < baseline_cycle:
            raise ScoringError("policy retires before the always-bypass baseline")
        joined.append(EventLoss(
            event.event_id,
            sensor_loss,
            world_loss,
            world_loss if enabled else sensor_loss,
            enabled,
            enabled and world_loss >= sensor_loss,
            sensor.reference_event_id,
            world.reference_event_id,
            record.retire_cycle - record.occurrence_cycle,
            record.retire_cycle - baseline_cycle,
        ))

    # Force the zero/non-finite denominator check at window construction time.
    _effect(tuple(joined), "policy_loss")
    return WindowMetrics(
        receipt.window_id,
        receipt.arm,
        expected_manifest_sha256,
        expected_receipt_sha256,
        expected_accounting_sha256,
        tuple(joined),
        len(accounting.freshness_veto_event_ids),
        len(accounting.invalid_pose_bypass_event_ids),
        len(accounting.attempted_correction_event_ids),
        len(accounting.operational_waste_event_ids),
        accounting.peak_buffer_entries,
        accounting.minimum_zero_loss_buffer_entries,
        accounting.buffer_bit_cycles,
        accounting.pose_bandwidth_bits_per_second,
        accounting.event_bandwidth_bits_per_second,
        accounting.incremental_state_bits,
        accounting.source_overrun_events,
        accounting.accepted_event_loss,
        accounting.causality_violations,
        accounting.leakage_violations,
    )


@dataclass(frozen=True)
class ArmAggregate:
    arm: str
    windows: Tuple[WindowMetrics, ...]
    accepted_events: int
    enabled_events: int
    attempted_corrections: int
    freshness_veto_events: int
    invalid_pose_bypass_events: int
    operational_waste_events: int
    quality_waste_events: int
    all_event_effect: float
    enabled_only_effect: Optional[float]
    positive_windows: int
    enable_rate: float
    freshness_veto_rate: float
    invalid_pose_bypass_rate: float
    operational_waste_rate: Optional[float]
    quality_waste_rate: Optional[float]
    occurrence_latency: LatencySummary
    added_latency: LatencySummary
    peak_buffer_entries: int
    minimum_zero_loss_buffer_entries: int
    buffer_bit_cycles: int
    pose_bandwidth_bits_per_second: int
    event_bandwidth_bits_per_second: int
    incremental_state_bits: int
    source_overrun_events: int
    accepted_event_loss: int
    causality_violations: int
    leakage_violations: int
    numeric_disposition: str
    final_disposition: str


def finalize_disposition(arm: str, numeric_disposition: str) -> str:
    """Apply diagnostic/interface labels without promoting them to epoch GO."""

    if arm not in DECISION_ARMS:
        raise ScoringError("arm is not frozen")
    if numeric_disposition not in _NUMERIC_DISPOSITIONS:
        raise ScoringError("numeric disposition is invalid")
    if numeric_disposition == "STOP":
        return "STOP"
    if arm == "delayed_exact":
        return "DIAGNOSTIC_UPPER_BOUND"
    if arm == "oracle_resampled_groundtruth_1khz":
        return "INTERFACE_VALUE_ONLY"
    if numeric_disposition == "GO_NUMERIC":
        return "GO_TO_EPOCH_INTEGRATION"
    return "HOLD"


def aggregate_arm(
    contract: ComparisonContract, windows: Sequence[WindowMetrics]
) -> ArmAggregate:
    """Aggregate exactly 24 frozen windows and apply Stage-4 disposition rules."""

    if not isinstance(contract, ComparisonContract):
        raise ScoringError("contract must be a validated ComparisonContract")
    source = tuple(windows)
    expected_windows = int(contract.registry["window_count"])
    if len(source) != expected_windows:
        raise ScoringError("aggregate window count differs from frozen registry")
    if any(not isinstance(window, WindowMetrics) for window in source):
        raise ScoringError("aggregate requires WindowMetrics values")
    arm = source[0].arm
    if any(window.arm != arm for window in source):
        raise ScoringError("aggregate mixes arms")
    window_ids = tuple(window.window_id for window in source)
    if len(set(window_ids)) != len(window_ids):
        raise ScoringError("aggregate window IDs are duplicated")
    events = tuple(event for window in source for event in window.event_losses)
    if len(events) != int(contract.registry["query_event_count"]):
        raise ScoringError("aggregate query denominator differs from frozen contract")
    event_ids = tuple(event.event_id for event in events)
    if len(set(event_ids)) != len(event_ids):
        raise ScoringError("aggregate event IDs are duplicated across windows")

    accepted = len(events)
    enabled_events = tuple(event for event in events if event.enabled)
    enabled = len(enabled_events)
    attempted = sum(window.attempted_corrections for window in source)
    freshness = sum(window.freshness_veto_events for window in source)
    invalid = sum(window.invalid_pose_bypass_events for window in source)
    operational = sum(window.operational_waste_events for window in source)
    quality = sum(window.quality_waste_events for window in source)
    all_effect = _effect(events, "policy_loss")
    enabled_effect = None if not enabled_events else _effect(enabled_events, "world_shadow_loss")
    enable_rate = float(enabled) / accepted
    operational_rate = None if attempted == 0 else float(operational) / attempted
    quality_rate = None if enabled == 0 else float(quality) / enabled
    occurrence_latency = nearest_rank_latency(
        (event.event_id, event.occurrence_latency_cycles) for event in events
    )
    added_latency = nearest_rank_latency(
        (event.event_id, event.added_latency_cycles) for event in events
    )
    peak_buffer = max(window.peak_buffer_entries for window in source)
    minimum_buffer = max(window.minimum_zero_loss_buffer_entries for window in source)
    buffer_bit_cycles = sum(window.buffer_bit_cycles for window in source)
    pose_bandwidth = max(window.pose_bandwidth_bits_per_second for window in source)
    event_bandwidth = max(window.event_bandwidth_bits_per_second for window in source)
    state_bits = max(window.incremental_state_bits for window in source)
    overrun = sum(window.source_overrun_events for window in source)
    accepted_loss = sum(window.accepted_event_loss for window in source)
    causality = sum(window.causality_violations for window in source)
    leakage = sum(window.leakage_violations for window in source)
    positive_windows = sum(1 for window in source if window.positive_window)

    gates = contract.as_dict()["go_to_epoch_integration"]
    period_ps = int(contract.timing["clock_period_ps"])
    latency_limit_ps = int(gates["maximum_added_p99_latency_ns"]) * 1_000
    latency_violation = (
        added_latency.p99_cycles * period_ps > latency_limit_ps
        or any(window.added_latency.p99_cycles * period_ps > latency_limit_ps for window in source)
    )
    hard_stop = (
        all_effect <= 0.0
        or overrun != 0
        or accepted_loss != 0
        or causality != 0
        or leakage != 0
        or latency_violation
        or peak_buffer > int(gates["maximum_buffer_entries"])
        or pose_bandwidth > int(gates["maximum_pose_bandwidth_bits_per_second"])
        or state_bits > int(gates["maximum_incremental_state_bits"])
        or (operational_rate is not None and operational_rate > float(gates["maximum_operational_waste_rate"]))
    )
    direction_agrees = enabled_effect is not None and all_effect * enabled_effect > 0.0
    go = (
        not hard_stop
        and all_effect >= float(gates["minimum_r_all_fraction"])
        and positive_windows >= int(gates["minimum_positive_windows"])
        and enable_rate >= float(gates["minimum_enable_rate"])
        and quality_rate is not None
        and quality_rate <= float(gates["maximum_quality_waste_rate"])
        and operational_rate is not None
        and operational_rate <= float(gates["maximum_operational_waste_rate"])
        and direction_agrees
    )
    numeric = "STOP" if hard_stop else ("GO_NUMERIC" if go else "HOLD")
    final = finalize_disposition(arm, numeric)
    return ArmAggregate(
        arm,
        source,
        accepted,
        enabled,
        attempted,
        freshness,
        invalid,
        operational,
        quality,
        all_effect,
        enabled_effect,
        positive_windows,
        enable_rate,
        float(freshness) / accepted,
        float(invalid) / accepted,
        operational_rate,
        quality_rate,
        occurrence_latency,
        added_latency,
        peak_buffer,
        minimum_buffer,
        buffer_bit_cycles,
        pose_bandwidth,
        event_bandwidth,
        state_bits,
        overrun,
        accepted_loss,
        causality,
        leakage,
        numeric,
        final,
    )


def validate_complete_comparison(
    aggregates: Sequence[ArmAggregate],
) -> Tuple[ArmAggregate, ...]:
    """Require every frozen arm exactly once on an identical event denominator."""

    source = tuple(aggregates)
    if any(not isinstance(item, ArmAggregate) for item in source):
        raise ScoringError("comparison requires ArmAggregate values")
    by_arm = dict((item.arm, item) for item in source)
    if len(by_arm) != len(source) or set(by_arm) != set(DECISION_ARMS):
        raise ScoringError("comparison must contain every frozen arm exactly once")
    identity = None
    for item in source:
        current = tuple(
            (window.window_id, event.event_id)
            for window in item.windows
            for event in window.event_losses
        )
        if identity is None:
            identity = current
        elif current != identity:
            raise ScoringError("arm event denominators differ")
    return tuple(by_arm[arm] for arm in sorted(by_arm))
