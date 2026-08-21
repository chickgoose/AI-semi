"""Normative, score-free Stage-4 two-lane cycle model.

The model operates only on event identity/timing and hash-bound pose packet
metadata.  It deliberately has no quality, loss, reference-bank, or scorer
interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


CLOCK_PERIOD_PS = 6_500
PICOSECONDS_PER_NANOSECOND = 1_000
RAW_INGRESS_LANES = 6
INGRESS_STAGING_ENTRIES = 6
EVENT_LANES = 2
TRANSFORM_PIPELINE_CYCLES = 1
BUFFER_ENTRIES = 1_024
EVENT_RECORD_BITS = 102
CAUSAL_POSE_INDEX_BITS = 14
CAUSAL_POSE_INDEX_LIMIT = 1 << CAUSAL_POSE_INDEX_BITS
POSE_ID_GAPS_ALLOWED = True
POSE_PACKET_BITS = 192
POSE_RING_ENTRIES = 16
POSE_RING_STATE_BITS = POSE_RING_ENTRIES * POSE_PACKET_BITS
ZOH_MAX_AGE_NS = 1_000_000
CAV_MAX_HORIZON_NS = 5_000_000
DELAYED_DEADLINE_NS = 6_000_000
ORACLE_CADENCE_NS = 1_000_000
DELAYED_DEADLINE_CYCLES = (
    DELAYED_DEADLINE_NS * PICOSECONDS_PER_NANOSECOND + CLOCK_PERIOD_PS - 1
) // CLOCK_PERIOD_PS
DATASET_POSE_ARRIVAL_ASSUMPTION = "arrival_equals_recorded_timestamp"
DELAYED_UNBOUNDED_DIAGNOSTIC_SCHEMA = (
    "redred.mc_wtb.stage4_delayed_unbounded_depth_diagnostic/v1"
)
DELAYED_UNBOUNDED_CONFIG_SCHEMA = (
    "redred.mc_wtb.stage4_delayed_unbounded_depth_config/v1"
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CycleModelError(ValueError):
    """An input or a frozen cycle-model invariant failed."""


class Arm(str, Enum):
    ZOH_FRESHNESS = "zoh_freshness"
    DELAYED_EXACT = "delayed_exact"
    CAUSAL_CAV = "causal_cav"
    ORACLE_1KHZ = "oracle_resampled_groundtruth_1khz"


ARM_LABELS = {
    Arm.ZOH_FRESHNESS.value: "CAUSAL_CANDIDATE",
    Arm.CAUSAL_CAV.value: "CAUSAL_CANDIDATE",
    Arm.DELAYED_EXACT.value: "DIAGNOSTIC_UPPER_BOUND",
    Arm.ORACLE_1KHZ.value: "INTERFACE_VALUE_ONLY",
}
DELAYED_RAW_REASONS = frozenset(
    ("deadline_timeout", "fifo_full_forced_bypass", "invalid_pose", "missing_bracket")
)
INVALID_POSE_FAILURE_CAUSE_ORDER = (
    "left_value_invalid",
    "right_value_invalid",
    "left_arithmetic_invalid",
    "right_arithmetic_invalid",
    "transform_guard_invalid",
)
INVALID_POSE_FAILURE_CAUSES = frozenset(INVALID_POSE_FAILURE_CAUSE_ORDER)


class PoseSource(str, Enum):
    DATASET = "dataset"
    ORACLE_1KHZ = "oracle_resampled_groundtruth_1khz"


def _nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CycleModelError("%s must be a non-negative integer" % where)
    return value


def _integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CycleModelError("%s must be an integer" % where)
    return value


def _nonempty_text(value: Any, where: str) -> str:
    if type(value) is not str or not value:
        raise CycleModelError("%s must be a non-empty string" % where)
    return value


def _sha256(value: Any, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CycleModelError("%s must be a lowercase SHA-256" % where)
    return value


def ceil_div(numerator: int, denominator: int) -> int:
    """Return exact mathematical ceiling division for non-negative integers."""

    numerator = _nonnegative_int(numerator, "numerator")
    denominator = _nonnegative_int(denominator, "denominator")
    if denominator == 0:
        raise CycleModelError("denominator must be positive")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder else 0)


def signed_ceil_div(numerator: int, denominator: int) -> int:
    """Return exact mathematical ceiling division with a signed numerator."""

    numerator = _integer(numerator, "numerator")
    denominator = _nonnegative_int(denominator, "denominator")
    if denominator == 0:
        raise CycleModelError("denominator must be positive")
    return -((-numerator) // denominator)


def timestamp_to_cycle(timestamp_ns: int, window_start_ns: int) -> int:
    """Map an integer timestamp with the frozen 6.5 ns ceiling rule."""

    timestamp_ns = _nonnegative_int(timestamp_ns, "timestamp_ns")
    window_start_ns = _nonnegative_int(window_start_ns, "window_start_ns")
    if timestamp_ns < window_start_ns:
        raise CycleModelError("timestamp precedes window_start_ns")
    return ceil_div(
        (timestamp_ns - window_start_ns) * PICOSECONDS_PER_NANOSECOND,
        CLOCK_PERIOD_PS,
    )


def pose_timestamp_to_cycle(timestamp_ns: int, window_start_ns: int) -> int:
    """Map a pose timestamp to a signed cycle relative to the event window."""

    timestamp_ns = _nonnegative_int(timestamp_ns, "timestamp_ns")
    window_start_ns = _nonnegative_int(window_start_ns, "window_start_ns")
    return signed_ceil_div(
        (timestamp_ns - window_start_ns) * PICOSECONDS_PER_NANOSECOND,
        CLOCK_PERIOD_PS,
    )


def pose_ring_slot(pose_id: int) -> int:
    """Return the deterministic ring slot for a nonnegative pose identity."""

    pose_id = _nonnegative_int(pose_id, "pose_id")
    return pose_id % POSE_RING_ENTRIES


@dataclass(frozen=True)
class Event:
    event_id: int
    timestamp_ns: int
    transform_guard_valid: bool = True
    causal_pose_index: Optional[int] = None

    def __post_init__(self) -> None:
        _nonnegative_int(self.event_id, "event_id")
        _nonnegative_int(self.timestamp_ns, "event timestamp_ns")
        if type(self.transform_guard_valid) is not bool:
            raise CycleModelError("transform_guard_valid must be bool")
        if self.causal_pose_index is not None:
            _nonnegative_int(self.causal_pose_index, "causal_pose_index")
            if self.causal_pose_index >= CAUSAL_POSE_INDEX_LIMIT:
                raise CycleModelError("causal_pose_index exceeds 14 bits")


@dataclass(frozen=True)
class PosePacket:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    source: PoseSource
    pose_sha256: str
    value_valid: bool = True
    arithmetic_valid: bool = True

    def __post_init__(self) -> None:
        pose_ring_slot(self.pose_id)
        _nonnegative_int(self.timestamp_ns, "pose timestamp_ns")
        _integer(self.commit_cycle, "pose commit_cycle")
        if not isinstance(self.source, PoseSource):
            raise CycleModelError("pose source must be PoseSource")
        _sha256(self.pose_sha256, "pose_sha256")
        if type(self.value_valid) is not bool:
            raise CycleModelError("pose value_valid must be bool")
        if type(self.arithmetic_valid) is not bool:
            raise CycleModelError("pose arithmetic_valid must be bool")

    @classmethod
    def dataset(
        cls,
        pose_id: int,
        timestamp_ns: int,
        window_start_ns: int,
        pose_sha256: str,
        value_valid: bool = True,
        arithmetic_valid: bool = True,
    ) -> "PosePacket":
        return cls(
            pose_id=pose_id,
            timestamp_ns=timestamp_ns,
            commit_cycle=pose_timestamp_to_cycle(timestamp_ns, window_start_ns),
            source=PoseSource.DATASET,
            pose_sha256=pose_sha256,
            value_valid=value_valid,
            arithmetic_valid=arithmetic_valid,
        )

    @classmethod
    def oracle_1khz(
        cls,
        pose_id: int,
        timestamp_ns: int,
        window_start_ns: int,
        pose_sha256: str,
        value_valid: bool = True,
        arithmetic_valid: bool = True,
    ) -> "PosePacket":
        return cls(
            pose_id=pose_id,
            timestamp_ns=timestamp_ns,
            commit_cycle=pose_timestamp_to_cycle(timestamp_ns, window_start_ns) + 1,
            source=PoseSource.ORACLE_1KHZ,
            pose_sha256=pose_sha256,
            value_valid=value_valid,
            arithmetic_valid=arithmetic_valid,
        )


@dataclass(frozen=True)
class DecisionRecord:
    window_id: str
    event_id: int
    event_timestamp_ns: int
    arm: str
    arm_semantic_label: str
    occurrence_cycle: int
    retire_cycle: int
    occurrence_pose_ids: Tuple[int, ...]
    occurrence_pose_timestamps_ns: Tuple[int, ...]
    occurrence_pose_commit_cycles: Tuple[int, ...]
    occurrence_pose_sha256: Tuple[str, ...]
    used_pose_ids: Tuple[int, ...]
    used_pose_timestamps_ns: Tuple[int, ...]
    used_pose_commit_cycles: Tuple[int, ...]
    used_pose_sha256: Tuple[str, ...]
    intentional_future_pose_use: bool
    pose_age_ns: Optional[int]
    disposition: str
    disposition_reason: str
    queue_cycles: int

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "event_timestamp_ns": self.event_timestamp_ns,
            "arm": self.arm,
            "arm_semantic_label": self.arm_semantic_label,
            "occurrence_cycle": self.occurrence_cycle,
            "retire_cycle": self.retire_cycle,
            "occurrence_pose_ids": list(self.occurrence_pose_ids),
            "occurrence_pose_timestamps_ns": list(
                self.occurrence_pose_timestamps_ns
            ),
            "occurrence_pose_commit_cycles": list(
                self.occurrence_pose_commit_cycles
            ),
            "occurrence_pose_sha256": list(self.occurrence_pose_sha256),
            "used_pose_ids": list(self.used_pose_ids),
            "used_pose_timestamps_ns": list(self.used_pose_timestamps_ns),
            "used_pose_commit_cycles": list(self.used_pose_commit_cycles),
            "used_pose_sha256": list(self.used_pose_sha256),
            "intentional_future_pose_use": self.intentional_future_pose_use,
            "pose_age_ns": self.pose_age_ns,
            "disposition": self.disposition,
            "disposition_reason": self.disposition_reason,
            "queue_cycles": self.queue_cycles,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class CycleReceipt:
    window_id: str
    event_id: int
    arm: str
    arm_semantic_label: str
    event_causal_pose_index: Optional[int]
    causal_pose_index_applicable: bool
    causal_pose_index_verified: bool
    occurrence_cycle: int
    admission_cycle: int
    admission_lane: int
    launch_cycle: Optional[int]
    launch_lane: Optional[int]
    retire_cycle: int
    retire_lane: int
    fifo_occupancy_before_admission: int
    fifo_occupancy_after_admission: int
    fifo_occupancy_before_retire: int
    fifo_occupancy_after_retire: int
    disposition: str
    disposition_reason: str
    inspection_cycle: Optional[int]
    inspected_pose_ids: Tuple[int, ...]
    inspected_pose_timestamps_ns: Tuple[int, ...]
    inspected_pose_commit_cycles: Tuple[int, ...]
    inspected_pose_sha256: Tuple[str, ...]
    inspection_failure_causes: Tuple[str, ...]
    decision_record_sha256: str

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "arm": self.arm,
            "arm_semantic_label": self.arm_semantic_label,
            "event_causal_pose_index": self.event_causal_pose_index,
            "causal_pose_index_applicable": self.causal_pose_index_applicable,
            "causal_pose_index_verified": self.causal_pose_index_verified,
            "occurrence_cycle": self.occurrence_cycle,
            "admission_cycle": self.admission_cycle,
            "admission_lane": self.admission_lane,
            "launch_cycle": self.launch_cycle,
            "launch_lane": self.launch_lane,
            "retire_cycle": self.retire_cycle,
            "retire_lane": self.retire_lane,
            "fifo_occupancy_before_admission": self.fifo_occupancy_before_admission,
            "fifo_occupancy_after_admission": self.fifo_occupancy_after_admission,
            "fifo_occupancy_before_retire": self.fifo_occupancy_before_retire,
            "fifo_occupancy_after_retire": self.fifo_occupancy_after_retire,
            "disposition": self.disposition,
            "disposition_reason": self.disposition_reason,
            "inspection_cycle": self.inspection_cycle,
            "inspected_pose_ids": list(self.inspected_pose_ids),
            "inspected_pose_timestamps_ns": list(
                self.inspected_pose_timestamps_ns
            ),
            "inspected_pose_commit_cycles": list(
                self.inspected_pose_commit_cycles
            ),
            "inspected_pose_sha256": list(self.inspected_pose_sha256),
            "inspection_failure_causes": list(self.inspection_failure_causes),
            "decision_record_sha256": self.decision_record_sha256,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class SimulationResult:
    window_id: str
    arm: Arm
    records: Tuple[DecisionRecord, ...]
    decision_records_sha256: str
    cycle_receipts: Tuple[CycleReceipt, ...]
    cycle_receipts_sha256: str
    common_serializer_cycles: Tuple[int, ...]
    always_bypass_retire_cycles: Tuple[int, ...]
    policy_added_latency_cycles: Tuple[int, ...]
    peak_ingress_staging_occupancy: int
    peak_buffer_occupancy: int
    raw_ingress_lanes: int
    ingress_staging_entries: int
    buffer_entries: int
    event_record_bits: int
    causal_pose_index_bits_in_event_record: int
    pose_packet_bits: int
    event_lanes: int
    transform_pipeline_cycles: int
    dataset_pose_arrival_assumption: str
    arm_disposition_label: str
    synthetic_test_mode: bool
    all_event_pose_indices_verified: bool
    pose_ring_entries: int
    pose_ring_state_bits: int
    pose_ring_accounting: "PoseRingAccounting"
    pose_ring_accounting_sha256: str


@dataclass(frozen=True)
class PoseRingAccounting:
    entries: int
    entry_bits: int
    state_bits: int
    writes: int
    safe_overwrites: int
    peak_occupied_entries: int
    peak_live_references: int
    live_reference_checks: int
    failures: int

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "entries": self.entries,
            "entry_bits": self.entry_bits,
            "state_bits": self.state_bits,
            "writes": self.writes,
            "safe_overwrites": self.safe_overwrites,
            "peak_occupied_entries": self.peak_occupied_entries,
            "peak_live_references": self.peak_live_references,
            "live_reference_checks": self.live_reference_checks,
            "failures": self.failures,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class DelayedUnboundedDiagnosticConfig:
    """Frozen identity of the one permitted unbounded diagnostic variant."""

    schema: str
    arm: str
    arm_semantic_label: str
    clock_period_ps: int
    timestamp_to_cycle_rule: str
    raw_ingress_lanes: int
    ingress_staging_entries: int
    ingress_order: str
    event_lanes: int
    transform_pipeline_cycles: int
    delayed_deadline_ns: int
    delayed_deadline_cycles: int
    pose_visibility_rule: str
    cycle_priority: str
    fifo_policy: str
    removed_bounded_fifo_entries: int
    removed_pressure_reason: str
    event_record_bits: int
    pose_ring_entries: int
    pose_ring_state_bits: int

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "arm": self.arm,
            "arm_semantic_label": self.arm_semantic_label,
            "clock_period_ps": self.clock_period_ps,
            "timestamp_to_cycle_rule": self.timestamp_to_cycle_rule,
            "raw_ingress_lanes": self.raw_ingress_lanes,
            "ingress_staging_entries": self.ingress_staging_entries,
            "ingress_order": self.ingress_order,
            "event_lanes": self.event_lanes,
            "transform_pipeline_cycles": self.transform_pipeline_cycles,
            "delayed_deadline_ns": self.delayed_deadline_ns,
            "delayed_deadline_cycles": self.delayed_deadline_cycles,
            "pose_visibility_rule": self.pose_visibility_rule,
            "cycle_priority": self.cycle_priority,
            "fifo_policy": self.fifo_policy,
            "removed_bounded_fifo_entries": self.removed_bounded_fifo_entries,
            "removed_pressure_reason": self.removed_pressure_reason,
            "event_record_bits": self.event_record_bits,
            "pose_ring_entries": self.pose_ring_entries,
            "pose_ring_state_bits": self.pose_ring_state_bits,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


def _delayed_unbounded_config() -> DelayedUnboundedDiagnosticConfig:
    return DelayedUnboundedDiagnosticConfig(
        schema=DELAYED_UNBOUNDED_CONFIG_SCHEMA,
        arm=Arm.DELAYED_EXACT.value,
        arm_semantic_label=ARM_LABELS[Arm.DELAYED_EXACT.value],
        clock_period_ps=CLOCK_PERIOD_PS,
        timestamp_to_cycle_rule="ceil((timestamp_ns-window_start_ns)*1000/6500)",
        raw_ingress_lanes=RAW_INGRESS_LANES,
        ingress_staging_entries=INGRESS_STAGING_ENTRIES,
        ingress_order="atomic_capture_then_stable_event_id_two_per_cycle",
        event_lanes=EVENT_LANES,
        transform_pipeline_cycles=TRANSFORM_PIPELINE_CYCLES,
        delayed_deadline_ns=DELAYED_DEADLINE_NS,
        delayed_deadline_cycles=DELAYED_DEADLINE_CYCLES,
        pose_visibility_rule="commit_cycle_strictly_less_than_observation_cycle",
        cycle_priority=(
            "visible_pose_then_ordered_retire_then_atomic_capture_then_"
            "stable_admit_then_consecutive_ready_head_launch"
        ),
        fifo_policy="unbounded_remove_only_fifo_full_pressure_action",
        removed_bounded_fifo_entries=BUFFER_ENTRIES,
        removed_pressure_reason="fifo_full_forced_bypass",
        event_record_bits=EVENT_RECORD_BITS,
        pose_ring_entries=POSE_RING_ENTRIES,
        pose_ring_state_bits=POSE_RING_STATE_BITS,
    )


@dataclass(frozen=True)
class DelayedUnboundedDiagnosticEvidence:
    """Immutable, self-validating score-free unbounded-depth evidence."""

    schema: str
    window_id: str
    arm: Arm
    arm_semantic_label: str
    config: DelayedUnboundedDiagnosticConfig
    config_identity_sha256: str
    input_event_ids: Tuple[int, ...]
    retired_event_ids: Tuple[int, ...]
    input_event_ids_sha256: str
    retired_event_ids_sha256: str
    input_count: int
    retired_count: int
    exact_once_ordered_conservation: bool
    no_full_pressure_reasons: bool
    peak_fifo_depth: int
    peak_ingress_staging_occupancy: int
    records: Tuple[DecisionRecord, ...]
    decision_records_sha256: str
    cycle_receipts: Tuple[CycleReceipt, ...]
    cycle_receipts_sha256: str
    common_serializer_cycles: Tuple[int, ...]
    always_bypass_retire_cycles: Tuple[int, ...]
    policy_added_latency_cycles: Tuple[int, ...]
    synthetic_test_mode: bool
    all_event_pose_indices_verified: bool
    pose_ring_accounting: PoseRingAccounting
    pose_ring_accounting_sha256: str

    def _body_mapping(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "window_id": self.window_id,
            "arm": self.arm.value,
            "arm_semantic_label": self.arm_semantic_label,
            "config": self.config.to_mapping(),
            "config_identity_sha256": self.config_identity_sha256,
            "input_event_ids": list(self.input_event_ids),
            "retired_event_ids": list(self.retired_event_ids),
            "input_event_ids_sha256": self.input_event_ids_sha256,
            "retired_event_ids_sha256": self.retired_event_ids_sha256,
            "input_count": self.input_count,
            "retired_count": self.retired_count,
            "exact_once_ordered_conservation": (
                self.exact_once_ordered_conservation
            ),
            "no_full_pressure_reasons": self.no_full_pressure_reasons,
            "peak_fifo_depth": self.peak_fifo_depth,
            "peak_ingress_staging_occupancy": (
                self.peak_ingress_staging_occupancy
            ),
            "records": [record.to_mapping() for record in self.records],
            "decision_records_sha256": self.decision_records_sha256,
            "cycle_receipts": [
                receipt.to_mapping() for receipt in self.cycle_receipts
            ],
            "cycle_receipts_sha256": self.cycle_receipts_sha256,
            "common_serializer_cycles": list(self.common_serializer_cycles),
            "always_bypass_retire_cycles": list(
                self.always_bypass_retire_cycles
            ),
            "policy_added_latency_cycles": list(
                self.policy_added_latency_cycles
            ),
            "synthetic_test_mode": self.synthetic_test_mode,
            "all_event_pose_indices_verified": (
                self.all_event_pose_indices_verified
            ),
            "pose_ring_accounting": self.pose_ring_accounting.to_mapping(),
            "pose_ring_accounting_sha256": self.pose_ring_accounting_sha256,
        }

    @property
    def evidence_sha256(self) -> str:
        return _canonical_sha256(self._body_mapping())

    def to_mapping(self) -> Dict[str, Any]:
        mapping = self._body_mapping()
        mapping["evidence_sha256"] = self.evidence_sha256
        return mapping

    def validate(self) -> None:
        _nonempty_text(self.window_id, "unbounded diagnostic window_id")
        if self.schema != DELAYED_UNBOUNDED_DIAGNOSTIC_SCHEMA:
            raise CycleModelError("unbounded diagnostic schema differs")
        if self.arm is not Arm.DELAYED_EXACT or self.arm_semantic_label != ARM_LABELS[
            Arm.DELAYED_EXACT.value
        ]:
            raise CycleModelError("unbounded diagnostic arm identity differs")
        expected_config = _delayed_unbounded_config()
        if self.config != expected_config:
            raise CycleModelError("unbounded diagnostic config differs")
        if self.config_identity_sha256 != expected_config.canonical_sha256():
            raise CycleModelError("unbounded diagnostic config identity differs")
        if self.input_event_ids_sha256 != _canonical_sha256(
            list(self.input_event_ids)
        ):
            raise CycleModelError("unbounded diagnostic input ID hash differs")
        if self.retired_event_ids_sha256 != _canonical_sha256(
            list(self.retired_event_ids)
        ):
            raise CycleModelError("unbounded diagnostic retired ID hash differs")
        record_ids = tuple(record.event_id for record in self.records)
        receipt_ids = tuple(receipt.event_id for receipt in self.cycle_receipts)
        if (
            self.input_count != len(self.input_event_ids)
            or self.retired_count != len(self.retired_event_ids)
            or record_ids != self.retired_event_ids
            or receipt_ids != self.retired_event_ids
            or self.input_event_ids != self.retired_event_ids
            or len(set(self.retired_event_ids)) != len(self.retired_event_ids)
            or any(
                right <= left
                for left, right in zip(
                    self.retired_event_ids, self.retired_event_ids[1:]
                )
            )
            or not self.exact_once_ordered_conservation
        ):
            raise CycleModelError("unbounded diagnostic conservation differs")
        expected_decisions_sha256 = _canonical_sha256(
            [record.to_mapping() for record in self.records]
        )
        expected_receipts_sha256 = _canonical_sha256(
            [receipt.to_mapping() for receipt in self.cycle_receipts]
        )
        if self.decision_records_sha256 != expected_decisions_sha256:
            raise CycleModelError("unbounded diagnostic decision hash differs")
        if self.cycle_receipts_sha256 != expected_receipts_sha256:
            raise CycleModelError("unbounded diagnostic receipt hash differs")
        for record, receipt in zip(self.records, self.cycle_receipts):
            if (
                record.window_id != self.window_id
                or receipt.window_id != self.window_id
                or record.arm != Arm.DELAYED_EXACT.value
                or receipt.arm != Arm.DELAYED_EXACT.value
                or record.arm_semantic_label != self.arm_semantic_label
                or receipt.arm_semantic_label != self.arm_semantic_label
                or receipt.decision_record_sha256 != record.canonical_sha256()
                or receipt.disposition != record.disposition
                or receipt.disposition_reason != record.disposition_reason
                or receipt.occurrence_cycle != record.occurrence_cycle
                or receipt.retire_cycle != record.retire_cycle
            ):
                raise CycleModelError("unbounded diagnostic receipt binding differs")
        if any(
            right.retire_cycle < left.retire_cycle
            for left, right in zip(self.records, self.records[1:])
        ) or any(
            record.retire_cycle < record.occurrence_cycle
            for record in self.records
        ):
            raise CycleModelError("unbounded diagnostic retirement order differs")
        full_pressure_present = any(
            record.disposition_reason == "fifo_full_forced_bypass"
            for record in self.records
        ) or any(
            receipt.disposition_reason == "fifo_full_forced_bypass"
            for receipt in self.cycle_receipts
        )
        if full_pressure_present or not self.no_full_pressure_reasons:
            raise CycleModelError("unbounded diagnostic contains full-pressure reason")
        derived_peak = max(
            (
                max(
                    receipt.fifo_occupancy_after_admission,
                    receipt.fifo_occupancy_before_retire,
                )
                for receipt in self.cycle_receipts
            ),
            default=0,
        )
        if self.peak_fifo_depth != derived_peak:
            raise CycleModelError("unbounded diagnostic peak FIFO depth differs")
        count = len(self.records)
        if not (
            len(self.common_serializer_cycles) == count
            and len(self.always_bypass_retire_cycles) == count
            and len(self.policy_added_latency_cycles) == count
        ):
            raise CycleModelError("unbounded diagnostic latency accounting differs")
        expected_serializer_cycles = tuple(
            receipt.admission_cycle - receipt.occurrence_cycle
            for receipt in self.cycle_receipts
        )
        expected_baseline_cycles = tuple(
            receipt.admission_cycle + TRANSFORM_PIPELINE_CYCLES
            for receipt in self.cycle_receipts
        )
        expected_policy_cycles = tuple(
            record.retire_cycle - baseline
            for record, baseline in zip(self.records, expected_baseline_cycles)
        )
        if (
            self.common_serializer_cycles != expected_serializer_cycles
            or self.always_bypass_retire_cycles != expected_baseline_cycles
            or self.policy_added_latency_cycles != expected_policy_cycles
        ):
            raise CycleModelError("unbounded diagnostic latency values differ")
        derived_pose_index_verification = all(
            not receipt.causal_pose_index_applicable
            or receipt.causal_pose_index_verified
            for receipt in self.cycle_receipts
        )
        if (
            type(self.synthetic_test_mode) is not bool
            or type(self.all_event_pose_indices_verified) is not bool
            or self.all_event_pose_indices_verified
            != derived_pose_index_verification
            or type(self.exact_once_ordered_conservation) is not bool
            or type(self.no_full_pressure_reasons) is not bool
            or not 0
            <= self.peak_ingress_staging_occupancy
            <= INGRESS_STAGING_ENTRIES
        ):
            raise CycleModelError("unbounded diagnostic status evidence differs")
        if (
            self.pose_ring_accounting_sha256
            != self.pose_ring_accounting.canonical_sha256()
            or self.pose_ring_accounting.entries != POSE_RING_ENTRIES
            or self.pose_ring_accounting.state_bits != POSE_RING_STATE_BITS
            or self.pose_ring_accounting.failures != 0
        ):
            raise CycleModelError("unbounded diagnostic pose-ring hash differs")


@dataclass(frozen=True)
class PoseRingFailureEvidence:
    reason: str
    cycle: int
    ring_slot: int
    incoming_pose_id: Optional[int]
    resident_pose_id: Optional[int]
    referenced_pose_id: Optional[int]
    live_event_ids: Tuple[int, ...]
    writes_completed: int
    safe_overwrites: int
    occupied_entries: int
    live_reference_count: int

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "schema": "redred.mc_wtb.stage4_pose_ring_failure/v1",
            "reason": self.reason,
            "cycle": self.cycle,
            "ring_slot": self.ring_slot,
            "incoming_pose_id": self.incoming_pose_id,
            "resident_pose_id": self.resident_pose_id,
            "referenced_pose_id": self.referenced_pose_id,
            "live_event_ids": list(self.live_event_ids),
            "writes_completed": self.writes_completed,
            "safe_overwrites": self.safe_overwrites,
            "occupied_entries": self.occupied_entries,
            "live_reference_count": self.live_reference_count,
            "pose_ring_entries": POSE_RING_ENTRIES,
            "pose_ring_entry_bits": POSE_PACKET_BITS,
            "pose_ring_state_bits": POSE_RING_STATE_BITS,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


class PoseRingSafetyError(CycleModelError):
    """A ring access would overwrite or resolve a live reference incorrectly."""

    def __init__(self, evidence: PoseRingFailureEvidence) -> None:
        self.evidence = evidence
        super().__init__(
            "%s at cycle %d ring slot %d (evidence %s)"
            % (
                evidence.reason,
                evidence.cycle,
                evidence.ring_slot,
                evidence.canonical_sha256(),
            )
        )


@dataclass
class _EventState:
    event: Event
    occurrence_cycle: int
    occurrence_snapshot: Tuple[PosePacket, ...]
    pose_index_applicable: bool = True
    pose_index_verified: bool = False
    accept_cycle: Optional[int] = None
    admission_lane: Optional[int] = None
    deadline_cycle: Optional[int] = None
    inflight: bool = False
    launch_cycle: Optional[int] = None
    launch_lane: Optional[int] = None
    retire_lane: Optional[int] = None
    fifo_occupancy_before_admission: Optional[int] = None
    fifo_occupancy_after_admission: Optional[int] = None
    fifo_occupancy_before_retire: Optional[int] = None
    fifo_occupancy_after_retire: Optional[int] = None
    selected: Tuple[PosePacket, ...] = ()
    disposition: Optional[str] = None
    reason: Optional[str] = None
    inspection_cycle: Optional[int] = None
    inspected: Tuple[PosePacket, ...] = ()
    inspection_failure_causes: Tuple[str, ...] = ()


def _canonical_sha256(value: Any) -> str:
    encoded = (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_and_prepare(
    window_start_ns: int,
    arm: Arm,
    events: Sequence[Event],
    poses: Sequence[PosePacket],
    synthetic_test_mode: bool,
) -> Tuple[List[_EventState], Tuple[PosePacket, ...]]:
    _nonnegative_int(window_start_ns, "window_start_ns")
    if not isinstance(arm, Arm):
        raise CycleModelError("arm must be Arm")
    if type(synthetic_test_mode) is not bool:
        raise CycleModelError("synthetic_test_mode must be bool")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise CycleModelError("events must be an ordered sequence")
    if isinstance(poses, (str, bytes)) or not isinstance(poses, Sequence):
        raise CycleModelError("poses must be an ordered sequence")
    if any(not isinstance(event, Event) for event in events):
        raise CycleModelError("events must contain Event values")
    if any(not isinstance(pose, PosePacket) for pose in poses):
        raise CycleModelError("poses must contain PosePacket values")

    event_values = tuple(events)
    pose_values = tuple(poses)
    if any(
        right.event_id <= left.event_id
        for left, right in zip(event_values, event_values[1:])
    ):
        raise CycleModelError("event IDs must be strictly increasing")
    if any(
        right.timestamp_ns < left.timestamp_ns
        for left, right in zip(event_values, event_values[1:])
    ):
        raise CycleModelError("event timestamps must be nondecreasing")
    pose_ids = [pose.pose_id for pose in pose_values]
    if len(set(pose_ids)) != len(pose_ids):
        raise CycleModelError("duplicate pose IDs are forbidden")
    if any(
        right.pose_id < left.pose_id
        for left, right in zip(pose_values, pose_values[1:])
    ):
        raise CycleModelError("pose IDs must be presented in increasing order")
    if any(
        right.timestamp_ns <= left.timestamp_ns
        for left, right in zip(pose_values, pose_values[1:])
    ):
        raise CycleModelError("pose timestamps must be strictly increasing")

    expected_source = (
        PoseSource.ORACLE_1KHZ
        if arm is Arm.ORACLE_1KHZ
        else PoseSource.DATASET
    )
    for pose in pose_values:
        if pose.source is not expected_source:
            raise CycleModelError("pose source does not match the selected arm")
        timestamp_cycle = pose_timestamp_to_cycle(
            pose.timestamp_ns, window_start_ns
        )
        expected_commit = timestamp_cycle + (
            1 if pose.source is PoseSource.ORACLE_1KHZ else 0
        )
        if pose.commit_cycle != expected_commit:
            raise CycleModelError("pose commit cycle violates delivery timing")
        if (
            pose.source is PoseSource.ORACLE_1KHZ
            and pose.timestamp_ns % ORACLE_CADENCE_NS != 0
        ):
            raise CycleModelError("oracle pose timestamp violates global 1 kHz phase")
        if (
            pose.source is PoseSource.ORACLE_1KHZ
            and pose.pose_id != pose.timestamp_ns // ORACLE_CADENCE_NS
        ):
            raise CycleModelError("oracle pose ID violates the global phase schedule")

    occurrence_cycles = [
        timestamp_to_cycle(event.timestamp_ns, window_start_ns)
        for event in event_values
    ]
    cycle_groups = {}  # type: Dict[int, List[Event]]
    for event, cycle in zip(event_values, occurrence_cycles):
        cycle_groups.setdefault(cycle, []).append(event)
    if any(len(group) > RAW_INGRESS_LANES for group in cycle_groups.values()):
        raise CycleModelError("more than six source records map to one occurrence cycle")

    prepared = []  # type: List[_EventState]
    for event, occurrence_cycle in zip(event_values, occurrence_cycles):
        visible = tuple(
            pose
            for pose in pose_values
            if pose.commit_cycle < occurrence_cycle
            and pose.timestamp_ns <= event.timestamp_ns
        )
        expected_pose_index = visible[-1].pose_id if visible else None
        pose_index_applicable = arm is not Arm.ORACLE_1KHZ
        if not pose_index_applicable:
            if event.causal_pose_index is not None:
                raise CycleModelError(
                    "oracle event causal_pose_index must be None"
                )
            pose_index_verified = False
        elif event.causal_pose_index is None:
            if not synthetic_test_mode:
                raise CycleModelError(
                    "integration event is missing causal_pose_index"
                )
            pose_index_verified = False
        else:
            if expected_pose_index is None:
                raise CycleModelError(
                    "causal_pose_index has no visible occurrence pose"
                )
            if event.causal_pose_index != expected_pose_index:
                raise CycleModelError(
                    "causal_pose_index differs from the latest occurrence pose"
                )
            pose_index_verified = True
        prepared.append(
            _EventState(
                event=event,
                occurrence_cycle=occurrence_cycle,
                occurrence_snapshot=visible[-2:],
                pose_index_applicable=pose_index_applicable,
                pose_index_verified=pose_index_verified,
                deadline_cycle=(
                    occurrence_cycle + DELAYED_DEADLINE_CYCLES
                    if arm is Arm.DELAYED_EXACT
                    else None
                ),
            )
        )
    return prepared, pose_values


def _capture_occurrences(
    states: List[_EventState],
    next_event: int,
    cycle: int,
    staging: List[_EventState],
) -> Tuple[int, int]:
    if next_event < len(states) and states[next_event].occurrence_cycle < cycle:
        raise CycleModelError("an occurrence was skipped before ingress capture")
    captured = []  # type: List[_EventState]
    while (
        next_event < len(states)
        and states[next_event].occurrence_cycle == cycle
    ):
        captured.append(states[next_event])
        next_event += 1
    if len(captured) > RAW_INGRESS_LANES:
        raise CycleModelError("raw ingress exceeded six lanes")
    if len(staging) + len(captured) > INGRESS_STAGING_ENTRIES:
        raise CycleModelError("six-entry ingress staging overflow")
    staging.extend(captured)
    return next_event, len(staging)


def _provenance(
    packets: Tuple[PosePacket, ...]
) -> Tuple[Tuple[int, ...], Tuple[int, ...], Tuple[int, ...], Tuple[str, ...]]:
    return (
        tuple(packet.pose_id for packet in packets),
        tuple(packet.timestamp_ns for packet in packets),
        tuple(packet.commit_cycle for packet in packets),
        tuple(packet.pose_sha256 for packet in packets),
    )


def _make_record(
    window_id: str,
    arm: Arm,
    state: _EventState,
    retire_cycle: int,
    selected: Tuple[PosePacket, ...],
    disposition: str,
    reason: str,
    queue_cycles: int,
) -> DecisionRecord:
    occurrence = _provenance(state.occurrence_snapshot)
    used = _provenance(selected)
    pose_age = (
        state.event.timestamp_ns - selected[-1].timestamp_ns if selected else None
    )
    return DecisionRecord(
        window_id=window_id,
        event_id=state.event.event_id,
        event_timestamp_ns=state.event.timestamp_ns,
        arm=arm.value,
        arm_semantic_label=ARM_LABELS[arm.value],
        occurrence_cycle=state.occurrence_cycle,
        retire_cycle=retire_cycle,
        occurrence_pose_ids=occurrence[0],
        occurrence_pose_timestamps_ns=occurrence[1],
        occurrence_pose_commit_cycles=occurrence[2],
        occurrence_pose_sha256=occurrence[3],
        used_pose_ids=used[0],
        used_pose_timestamps_ns=used[1],
        used_pose_commit_cycles=used[2],
        used_pose_sha256=used[3],
        intentional_future_pose_use=any(
            packet.timestamp_ns > state.event.timestamp_ns for packet in selected
        ),
        pose_age_ns=pose_age,
        disposition=disposition,
        disposition_reason=reason,
        queue_cycles=queue_cycles,
    )


def _select_causal(
    arm: Arm, state: _EventState
) -> Tuple[Tuple[PosePacket, ...], str, str]:
    snapshot = state.occurrence_snapshot
    if not snapshot:
        return (), "raw_bypass", "no_occurrence_pose"
    latest = snapshot[-1]
    age = state.event.timestamp_ns - latest.timestamp_ns
    if arm is Arm.CAUSAL_CAV and len(snapshot) == 2:
        previous = snapshot[0]
        interval = latest.timestamp_ns - previous.timestamp_ns
        horizon = min(CAV_MAX_HORIZON_NS, interval)
        if (
            age <= horizon
            and previous.value_valid
            and latest.value_valid
            and previous.arithmetic_valid
            and latest.arithmetic_valid
            and state.event.transform_guard_valid
        ):
            return snapshot, "corrected_world_ray", "causal_cav"
    if latest.value_valid and latest.arithmetic_valid and age <= ZOH_MAX_AGE_NS:
        reason = (
            "fresh_zoh_fallback"
            if arm is Arm.CAUSAL_CAV
            else "oracle_fresh_zoh"
            if arm is Arm.ORACLE_1KHZ
            else "fresh_zoh"
        )
        return (latest,), "corrected_world_ray", reason
    if not latest.value_valid or not latest.arithmetic_valid:
        return (latest,), "raw_bypass", "invalid_pose"
    return (latest,), "raw_bypass", "stale_pose"


def _run_causal(
    window_id: str,
    arm: Arm,
    states: List[_EventState],
) -> Tuple[List[DecisionRecord], int, int]:
    if not states:
        return [], 0, 0
    records = []  # type: List[DecisionRecord]
    staging = []  # type: List[_EventState]
    inflight = []  # type: List[_EventState]
    next_event = 0
    peak_staging = 0
    cycle = states[0].occurrence_cycle
    while len(records) < len(states):
        if inflight:
            for retire_lane, state in enumerate(inflight):
                if state.launch_cycle is None or state.disposition is None or state.reason is None:
                    raise CycleModelError("internal transform state is incomplete")
                state.retire_lane = retire_lane
                state.fifo_occupancy_before_retire = 0
                state.fifo_occupancy_after_retire = 0
                records.append(
                    _make_record(
                        window_id,
                        arm,
                        state,
                        cycle,
                        state.selected,
                        state.disposition,
                        state.reason,
                        0,
                    )
                )
            inflight = []

        next_event, staging_occupancy = _capture_occurrences(
            states, next_event, cycle, staging
        )
        peak_staging = max(peak_staging, staging_occupancy)
        admitted = staging[:EVENT_LANES]
        del staging[: len(admitted)]
        for admission_lane, state in enumerate(admitted):
            state.accept_cycle = cycle
            state.admission_lane = admission_lane
            state.fifo_occupancy_before_admission = 0
            state.fifo_occupancy_after_admission = 0
            selected, disposition, reason = _select_causal(arm, state)
            state.selected = selected
            state.disposition = disposition
            state.reason = reason
            state.launch_cycle = cycle
            state.launch_lane = admission_lane
        inflight = admitted

        if len(records) == len(states):
            break
        if inflight or staging:
            cycle += 1
        elif next_event < len(states):
            cycle = states[next_event].occurrence_cycle
        else:
            raise CycleModelError("causal simulation stopped before exact retirement")
    return records, 0, peak_staging


def _first_right_pose(
    state: _EventState, poses: Tuple[PosePacket, ...]
) -> Optional[PosePacket]:
    for pose in poses:
        if pose.timestamp_ns > state.event.timestamp_ns:
            return pose
    return None


def _invalid_pose_causes(
    state: _EventState, left: PosePacket, right: PosePacket
) -> Tuple[str, ...]:
    causes = []  # type: List[str]
    if not left.value_valid:
        causes.append("left_value_invalid")
    if not right.value_valid:
        causes.append("right_value_invalid")
    if not left.arithmetic_valid:
        causes.append("left_arithmetic_invalid")
    if not right.arithmetic_valid:
        causes.append("right_arithmetic_invalid")
    if not state.event.transform_guard_valid:
        causes.append("transform_guard_invalid")
    return tuple(causes)


def _delayed_status(
    state: _EventState,
    poses: Tuple[PosePacket, ...],
    cycle: int,
) -> Tuple[
    str,
    Tuple[PosePacket, ...],
    str,
    Tuple[PosePacket, ...],
    Tuple[str, ...],
]:
    left = state.occurrence_snapshot[-1:] if state.occurrence_snapshot else ()
    if not left:
        return "raw", (), "missing_bracket", (), ()
    right = _first_right_pose(state, poses)
    if right is not None and right.commit_cycle < cycle:
        selected = (left[0], right)
        if (
            left[0].value_valid
            and right.value_valid
            and left[0].arithmetic_valid
            and right.arithmetic_valid
            and state.event.transform_guard_valid
        ):
            return "correct", selected, "bracket_interpolation", (), ()
        causes = _invalid_pose_causes(state, left[0], right)
        if not causes:
            raise CycleModelError("invalid delayed pose has no failing guard")
        return "raw", left, "invalid_pose", (right,), causes
    if state.deadline_cycle is None:
        raise CycleModelError("delayed event has no deadline")
    if cycle >= state.deadline_cycle:
        return "raw", left, "deadline_timeout", (), ()
    return "wait", left, "waiting_for_right_bracket", (), ()


def _pop_raw_head(
    queue: List[_EventState],
    records: List[DecisionRecord],
    window_id: str,
    cycle: int,
    selected: Tuple[PosePacket, ...],
    reason: str,
    retire_lane: int,
) -> None:
    occupancy_before = len(queue)
    state = queue.pop(0)
    if state.accept_cycle is None:
        raise CycleModelError("delayed FIFO head was never admitted")
    state.retire_lane = retire_lane
    state.fifo_occupancy_before_retire = occupancy_before
    state.fifo_occupancy_after_retire = len(queue)
    records.append(
        _make_record(
            window_id,
            Arm.DELAYED_EXACT,
            state,
            cycle,
            selected,
            "raw_bypass",
            reason,
            cycle - state.accept_cycle,
        )
    )


def _run_delayed(
    window_id: str,
    states: List[_EventState],
    poses: Tuple[PosePacket, ...],
) -> Tuple[List[DecisionRecord], int, int]:
    if not states:
        return [], 0, 0
    records = []  # type: List[DecisionRecord]
    queue = []  # type: List[_EventState]
    staging = []  # type: List[_EventState]
    inflight = []  # type: List[_EventState]
    next_event = 0
    peak = 0
    peak_staging = 0
    cycle = states[0].occurrence_cycle

    while len(records) < len(states):
        retirements = 0
        if inflight:
            for expected in inflight:
                if not queue or queue[0] is not expected:
                    raise CycleModelError("transform pipeline reordered the FIFO")
                occupancy_before = len(queue)
                state = queue.pop(0)
                if state.launch_cycle is None:
                    raise CycleModelError("launched delayed event has no cycle")
                if state.accept_cycle is None:
                    raise CycleModelError("launched delayed event was never admitted")
                state.retire_lane = retirements
                state.fifo_occupancy_before_retire = occupancy_before
                state.fifo_occupancy_after_retire = len(queue)
                records.append(
                    _make_record(
                        window_id,
                        Arm.DELAYED_EXACT,
                        state,
                        cycle,
                        state.selected,
                        "corrected_world_ray",
                        "bracket_interpolation",
                        state.launch_cycle - state.accept_cycle,
                    )
                )
                retirements += 1
            inflight = []

        while queue and retirements < EVENT_LANES:
            status, selected, reason, inspected, failure_causes = _delayed_status(
                queue[0], poses, cycle
            )
            if status != "raw":
                break
            if inspected:
                queue[0].inspection_cycle = cycle
                queue[0].inspected = inspected
                queue[0].inspection_failure_causes = failure_causes
            _pop_raw_head(
                queue,
                records,
                window_id,
                cycle,
                selected,
                reason,
                retirements,
            )
            retirements += 1

        next_event, staging_occupancy = _capture_occurrences(
            states, next_event, cycle, staging
        )
        peak_staging = max(peak_staging, staging_occupancy)
        incoming = staging[:EVENT_LANES]
        needed = max(0, len(queue) + len(incoming) - BUFFER_ENTRIES)
        if needed > EVENT_LANES - retirements:
            raise CycleModelError("full-pressure retirement exceeded two lanes")
        for _ in range(needed):
            if not queue or queue[0].inflight:
                raise CycleModelError("full-pressure bypass cannot select the oldest head")
            left = queue[0].occurrence_snapshot[-1:] if queue[0].occurrence_snapshot else ()
            _pop_raw_head(
                queue,
                records,
                window_id,
                cycle,
                left,
                "fifo_full_forced_bypass",
                retirements,
            )
            retirements += 1

        del staging[: len(incoming)]
        for admission_lane, state in enumerate(incoming):
            state.accept_cycle = cycle
            state.admission_lane = admission_lane
            state.fifo_occupancy_before_admission = len(queue)
            queue.append(state)
            state.fifo_occupancy_after_admission = len(queue)
        if len(queue) > BUFFER_ENTRIES:
            raise CycleModelError("delayed FIFO exceeded 1,024 entries")
        peak = max(peak, len(queue))

        launch = []  # type: List[_EventState]
        for launch_lane, state in enumerate(queue[:EVENT_LANES]):
            if state.inflight:
                break
            status, selected, reason, _inspected, _failure_causes = _delayed_status(
                state, poses, cycle
            )
            if status != "correct":
                break
            state.inflight = True
            state.launch_cycle = cycle
            state.launch_lane = launch_lane
            state.selected = selected
            state.disposition = "corrected_world_ray"
            state.reason = reason
            launch.append(state)
        inflight = launch

        if len(records) == len(states):
            break

        force_next_cycle = bool(inflight or staging or incoming)
        if retirements == EVENT_LANES and queue:
            force_next_cycle = True
        if force_next_cycle:
            cycle += 1
            continue

        candidates = []  # type: List[int]
        if next_event < len(states):
            candidates.append(states[next_event].occurrence_cycle)
        if queue:
            head = queue[0]
            if head.deadline_cycle is None:
                raise CycleModelError("queued delayed event has no deadline")
            candidates.append(head.deadline_cycle)
            right = _first_right_pose(head, poses)
            if right is not None:
                candidates.append(right.commit_cycle + 1)
        future = [candidate for candidate in candidates if candidate > cycle]
        if not future:
            cycle += 1
        else:
            cycle = min(future)
    return records, peak, peak_staging


def _run_delayed_unbounded(
    window_id: str,
    states: List[_EventState],
    poses: Tuple[PosePacket, ...],
) -> Tuple[List[DecisionRecord], int, int]:
    """Replay delayed_exact with only the bounded FIFO pressure action removed."""

    if not states:
        return [], 0, 0
    records = []  # type: List[DecisionRecord]
    queue = []  # type: List[_EventState]
    staging = []  # type: List[_EventState]
    inflight = []  # type: List[_EventState]
    next_event = 0
    peak = 0
    peak_staging = 0
    cycle = states[0].occurrence_cycle

    while len(records) < len(states):
        retirements = 0
        if inflight:
            for expected in inflight:
                if not queue or queue[0] is not expected:
                    raise CycleModelError("transform pipeline reordered the FIFO")
                occupancy_before = len(queue)
                state = queue.pop(0)
                if state.launch_cycle is None:
                    raise CycleModelError("launched delayed event has no cycle")
                if state.accept_cycle is None:
                    raise CycleModelError("launched delayed event was never admitted")
                state.retire_lane = retirements
                state.fifo_occupancy_before_retire = occupancy_before
                state.fifo_occupancy_after_retire = len(queue)
                records.append(
                    _make_record(
                        window_id,
                        Arm.DELAYED_EXACT,
                        state,
                        cycle,
                        state.selected,
                        "corrected_world_ray",
                        "bracket_interpolation",
                        state.launch_cycle - state.accept_cycle,
                    )
                )
                retirements += 1
            inflight = []

        while queue and retirements < EVENT_LANES:
            status, selected, reason, inspected, failure_causes = _delayed_status(
                queue[0], poses, cycle
            )
            if status != "raw":
                break
            if inspected:
                queue[0].inspection_cycle = cycle
                queue[0].inspected = inspected
                queue[0].inspection_failure_causes = failure_causes
            _pop_raw_head(
                queue,
                records,
                window_id,
                cycle,
                selected,
                reason,
                retirements,
            )
            retirements += 1

        next_event, staging_occupancy = _capture_occurrences(
            states, next_event, cycle, staging
        )
        peak_staging = max(peak_staging, staging_occupancy)
        incoming = staging[:EVENT_LANES]

        # This is the sole semantic difference from _run_delayed: do not
        # synthesize fifo_full_forced_bypass retirements and do not cap queue
        # occupancy at BUFFER_ENTRIES. Admission order and rate are unchanged.
        del staging[: len(incoming)]
        for admission_lane, state in enumerate(incoming):
            state.accept_cycle = cycle
            state.admission_lane = admission_lane
            state.fifo_occupancy_before_admission = len(queue)
            queue.append(state)
            state.fifo_occupancy_after_admission = len(queue)
        peak = max(peak, len(queue))

        launch = []  # type: List[_EventState]
        for launch_lane, state in enumerate(queue[:EVENT_LANES]):
            if state.inflight:
                break
            status, selected, reason, _inspected, _failure_causes = _delayed_status(
                state, poses, cycle
            )
            if status != "correct":
                break
            state.inflight = True
            state.launch_cycle = cycle
            state.launch_lane = launch_lane
            state.selected = selected
            state.disposition = "corrected_world_ray"
            state.reason = reason
            launch.append(state)
        inflight = launch

        if len(records) == len(states):
            break

        force_next_cycle = bool(inflight or staging or incoming)
        if retirements == EVENT_LANES and queue:
            force_next_cycle = True
        if force_next_cycle:
            cycle += 1
            continue

        candidates = []  # type: List[int]
        if next_event < len(states):
            candidates.append(states[next_event].occurrence_cycle)
        if queue:
            head = queue[0]
            if head.deadline_cycle is None:
                raise CycleModelError("queued delayed event has no deadline")
            candidates.append(head.deadline_cycle)
            right = _first_right_pose(head, poses)
            if right is not None:
                candidates.append(right.commit_cycle + 1)
        future = [candidate for candidate in candidates if candidate > cycle]
        if not future:
            cycle += 1
        else:
            cycle = min(future)
    return records, peak, peak_staging


def _validate_conservation(
    states: List[_EventState], records: List[DecisionRecord]
) -> None:
    expected = [state.event.event_id for state in states]
    actual = [record.event_id for record in records]
    if actual != expected or len(set(actual)) != len(actual):
        raise CycleModelError("exact-once ordered retirement failed")
    if any(
        right.retire_cycle < left.retire_cycle
        for left, right in zip(records, records[1:])
    ):
        raise CycleModelError("retirement cycles moved backwards")
    if any(record.retire_cycle < record.occurrence_cycle for record in records):
        raise CycleModelError("an event retired before occurrence")


def _validate_delayed_dispositions(
    states: List[_EventState],
    poses: Tuple[PosePacket, ...],
    records: List[DecisionRecord],
) -> None:
    for state, record in zip(states, records):
        if record.arm != Arm.DELAYED_EXACT.value:
            raise CycleModelError("delayed record lost its diagnostic arm identity")
        if record.disposition == "corrected_world_ray":
            left = state.occurrence_snapshot[-1:] if state.occurrence_snapshot else ()
            right = _first_right_pose(state, poses)
            expected = left + ((right,) if right is not None else ())
            if (
                len(expected) != 2
                or record.used_pose_ids != tuple(pose.pose_id for pose in expected)
                or record.used_pose_timestamps_ns[0] > state.event.timestamp_ns
                or record.used_pose_timestamps_ns[1] <= state.event.timestamp_ns
                or not record.intentional_future_pose_use
                or record.disposition_reason != "bracket_interpolation"
            ):
                raise CycleModelError(
                    "corrected delayed record lacks the first strict right bracket"
                )
        else:
            if record.disposition_reason not in DELAYED_RAW_REASONS:
                raise CycleModelError("delayed raw bypass lacks an explicit reason")
            if record.intentional_future_pose_use or any(
                timestamp > record.event_timestamp_ns
                for timestamp in record.used_pose_timestamps_ns
            ):
                raise CycleModelError("delayed raw bypass recorded a future used pose")


def _make_cycle_receipts(
    window_id: str,
    arm: Arm,
    states: List[_EventState],
    records: List[DecisionRecord],
) -> Tuple[CycleReceipt, ...]:
    receipts = []  # type: List[CycleReceipt]
    for state, record in zip(states, records):
        inspected = _provenance(state.inspected)
        required = (
            state.accept_cycle,
            state.admission_lane,
            state.retire_lane,
            state.fifo_occupancy_before_admission,
            state.fifo_occupancy_after_admission,
            state.fifo_occupancy_before_retire,
            state.fifo_occupancy_after_retire,
        )
        if any(value is None for value in required):
            raise CycleModelError("cycle receipt scheduling metadata is incomplete")
        if (state.launch_cycle is None) != (state.launch_lane is None):
            raise CycleModelError("cycle receipt launch cycle/lane pairing differs")
        if not 0 <= state.admission_lane < EVENT_LANES:
            raise CycleModelError("admission lane is outside the two-lane service")
        if not 0 <= state.retire_lane < EVENT_LANES:
            raise CycleModelError("retire lane is outside the two-lane service")
        if state.launch_lane is not None and not 0 <= state.launch_lane < EVENT_LANES:
            raise CycleModelError("launch lane is outside the two-lane pipeline")
        if state.inspected:
            if not state.occurrence_snapshot:
                raise CycleModelError("inspected right pose has no causal left pose")
            left = state.occurrence_snapshot[-1]
            expected_causes = _invalid_pose_causes(
                state, left, state.inspected[0]
            )
            if (
                state.inspection_cycle is None
                or record.disposition != "raw_bypass"
                or record.disposition_reason != "invalid_pose"
                or len(state.inspected) != 1
                or state.inspected[0].commit_cycle >= state.inspection_cycle
                or state.inspection_cycle > record.retire_cycle
                or not state.inspection_failure_causes
                or state.inspection_failure_causes != expected_causes
                or any(
                    cause not in INVALID_POSE_FAILURE_CAUSES
                    for cause in state.inspection_failure_causes
                )
            ):
                raise CycleModelError("invalid-pose inspection evidence is inconsistent")
        elif (
            state.inspection_cycle is not None
            or state.inspection_failure_causes
        ):
            raise CycleModelError("inspection metadata has no inspected pose")
        receipts.append(
            CycleReceipt(
                window_id=window_id,
                event_id=state.event.event_id,
                arm=arm.value,
                arm_semantic_label=ARM_LABELS[arm.value],
                event_causal_pose_index=state.event.causal_pose_index,
                causal_pose_index_applicable=state.pose_index_applicable,
                causal_pose_index_verified=state.pose_index_verified,
                occurrence_cycle=state.occurrence_cycle,
                admission_cycle=state.accept_cycle,
                admission_lane=state.admission_lane,
                launch_cycle=state.launch_cycle,
                launch_lane=state.launch_lane,
                retire_cycle=record.retire_cycle,
                retire_lane=state.retire_lane,
                fifo_occupancy_before_admission=state.fifo_occupancy_before_admission,
                fifo_occupancy_after_admission=state.fifo_occupancy_after_admission,
                fifo_occupancy_before_retire=state.fifo_occupancy_before_retire,
                fifo_occupancy_after_retire=state.fifo_occupancy_after_retire,
                disposition=record.disposition,
                disposition_reason=record.disposition_reason,
                inspection_cycle=state.inspection_cycle,
                inspected_pose_ids=inspected[0],
                inspected_pose_timestamps_ns=inspected[1],
                inspected_pose_commit_cycles=inspected[2],
                inspected_pose_sha256=inspected[3],
                inspection_failure_causes=state.inspection_failure_causes,
                decision_record_sha256=record.canonical_sha256(),
            )
        )
    return tuple(receipts)


def _verify_pose_ring(
    states: List[_EventState],
    poses: Tuple[PosePacket, ...],
    records: List[DecisionRecord],
) -> PoseRingAccounting:
    """Replay the charged ring and fail before any live entry can be replaced.

    Slot phase is the authoritative nonnegative pose identity modulo 16;
    packet gaps never renumber later slots. References begin before writes on
    their start cycle and remain live through the retire phase of their end
    cycle.
    """

    pose_by_id = {pose.pose_id: pose for pose in poses}
    slot_by_pose_id = {
        pose.pose_id: pose_ring_slot(pose.pose_id) for pose in poses
    }
    writes_by_cycle = {}  # type: Dict[int, List[PosePacket]]
    for pose in poses:
        writes_by_cycle.setdefault(pose.commit_cycle, []).append(pose)

    starts_by_cycle = {}  # type: Dict[int, List[Tuple[int, int, int]]]
    releases_by_cycle = {}  # type: Dict[int, List[Tuple[int, int]]]
    for state, record in zip(states, records):
        references = {}  # type: Dict[int, int]
        for pose in state.occurrence_snapshot:
            references[pose.pose_id] = state.occurrence_cycle
        occurrence_ids = set(references)
        for pose_id in record.used_pose_ids:
            if pose_id not in occurrence_ids:
                if state.launch_cycle is None:
                    raise CycleModelError(
                        "a non-occurrence pose has no transform launch cycle"
                    )
                references[pose_id] = state.launch_cycle
        for pose in state.inspected:
            if state.inspection_cycle is None:
                raise CycleModelError("inspected pose has no inspection cycle")
            references[pose.pose_id] = state.inspection_cycle
        for pose_id, start_cycle in references.items():
            if pose_id not in pose_by_id:
                raise CycleModelError("event references an unknown pose ID")
            starts_by_cycle.setdefault(start_cycle, []).append(
                (state.event.event_id, pose_id, record.retire_cycle)
            )
            releases_by_cycle.setdefault(record.retire_cycle, []).append(
                (state.event.event_id, pose_id)
            )

    resident = [None] * POSE_RING_ENTRIES  # type: List[Optional[int]]
    active = {}  # type: Dict[int, List[int]]
    writes_completed = 0
    safe_overwrites = 0
    peak_occupied = 0
    peak_live = 0
    reference_checks = 0

    def fail(
        reason: str,
        cycle: int,
        slot: int,
        incoming_pose_id: Optional[int],
        referenced_pose_id: Optional[int],
    ) -> None:
        resident_pose_id = resident[slot]
        live_ids = tuple(sorted(active.get(resident_pose_id, ())))
        raise PoseRingSafetyError(
            PoseRingFailureEvidence(
                reason=reason,
                cycle=cycle,
                ring_slot=slot,
                incoming_pose_id=incoming_pose_id,
                resident_pose_id=resident_pose_id,
                referenced_pose_id=referenced_pose_id,
                live_event_ids=live_ids,
                writes_completed=writes_completed,
                safe_overwrites=safe_overwrites,
                occupied_entries=sum(value is not None for value in resident),
                live_reference_count=sum(len(values) for values in active.values()),
            )
        )

    cycles = sorted(
        set(writes_by_cycle) | set(starts_by_cycle) | set(releases_by_cycle)
    )
    for cycle in cycles:
        for event_id, pose_id, _end_cycle in sorted(starts_by_cycle.get(cycle, ())):
            slot = slot_by_pose_id[pose_id]
            reference_checks += 1
            if resident[slot] != pose_id:
                fail(
                    "referenced_pose_not_resident",
                    cycle,
                    slot,
                    None,
                    pose_id,
                )
            active.setdefault(pose_id, []).append(event_id)
        peak_live = max(peak_live, sum(len(values) for values in active.values()))

        for pose in writes_by_cycle.get(cycle, ()):
            slot = pose_ring_slot(pose.pose_id)
            resident_pose_id = resident[slot]
            if resident_pose_id is not None and active.get(resident_pose_id):
                fail(
                    "live_reference_overwrite",
                    cycle,
                    slot,
                    pose.pose_id,
                    None,
                )
            if resident_pose_id is not None:
                safe_overwrites += 1
            resident[slot] = pose.pose_id
            writes_completed += 1
            peak_occupied = max(
                peak_occupied,
                sum(value is not None for value in resident),
            )

        for event_id, pose_id in sorted(releases_by_cycle.get(cycle, ())):
            live_ids = active.get(pose_id)
            if live_ids is None or event_id not in live_ids:
                raise CycleModelError("pose-ring reference release was not live")
            live_ids.remove(event_id)
            if not live_ids:
                del active[pose_id]

    if active:
        raise CycleModelError("pose-ring references remained live after retirement")
    return PoseRingAccounting(
        entries=POSE_RING_ENTRIES,
        entry_bits=POSE_PACKET_BITS,
        state_bits=POSE_RING_STATE_BITS,
        writes=writes_completed,
        safe_overwrites=safe_overwrites,
        peak_occupied_entries=peak_occupied,
        peak_live_references=peak_live,
        live_reference_checks=reference_checks,
        failures=0,
    )


def run_cycle_model(
    *,
    window_id: str,
    window_start_ns: int,
    arm: Arm,
    events: Sequence[Event],
    poses: Sequence[PosePacket],
    synthetic_test_mode: bool = False,
) -> SimulationResult:
    """Simulate one arm/window and return exact-once score-free decisions."""

    _nonempty_text(window_id, "window_id")
    states, checked_poses = _validate_and_prepare(
        window_start_ns, arm, events, poses, synthetic_test_mode
    )
    if arm is Arm.DELAYED_EXACT:
        records, peak, peak_staging = _run_delayed(
            window_id, states, checked_poses
        )
    else:
        records, peak, peak_staging = _run_causal(window_id, arm, states)
    _validate_conservation(states, records)
    if arm is Arm.DELAYED_EXACT:
        _validate_delayed_dispositions(states, checked_poses, records)
    cycle_receipts = _make_cycle_receipts(window_id, arm, states, records)
    pose_ring_accounting = _verify_pose_ring(states, checked_poses, records)
    serializer_cycles = []  # type: List[int]
    baseline_retire_cycles = []  # type: List[int]
    policy_added_cycles = []  # type: List[int]
    for state, record in zip(states, records):
        if state.accept_cycle is None:
            raise CycleModelError("retired event has no serializer exit cycle")
        serializer_cycle_count = state.accept_cycle - state.occurrence_cycle
        baseline_retire_cycle = state.accept_cycle + TRANSFORM_PIPELINE_CYCLES
        serializer_cycles.append(serializer_cycle_count)
        baseline_retire_cycles.append(baseline_retire_cycle)
        policy_added_cycles.append(record.retire_cycle - baseline_retire_cycle)
    mapping = [record.to_mapping() for record in records]
    label = ARM_LABELS[arm.value]
    return SimulationResult(
        window_id=window_id,
        arm=arm,
        records=tuple(records),
        decision_records_sha256=_canonical_sha256(mapping),
        cycle_receipts=cycle_receipts,
        cycle_receipts_sha256=_canonical_sha256(
            [receipt.to_mapping() for receipt in cycle_receipts]
        ),
        common_serializer_cycles=tuple(serializer_cycles),
        always_bypass_retire_cycles=tuple(baseline_retire_cycles),
        policy_added_latency_cycles=tuple(policy_added_cycles),
        peak_ingress_staging_occupancy=peak_staging,
        peak_buffer_occupancy=peak,
        raw_ingress_lanes=RAW_INGRESS_LANES,
        ingress_staging_entries=INGRESS_STAGING_ENTRIES,
        buffer_entries=BUFFER_ENTRIES,
        event_record_bits=EVENT_RECORD_BITS,
        causal_pose_index_bits_in_event_record=CAUSAL_POSE_INDEX_BITS,
        pose_packet_bits=POSE_PACKET_BITS,
        event_lanes=EVENT_LANES,
        transform_pipeline_cycles=TRANSFORM_PIPELINE_CYCLES,
        dataset_pose_arrival_assumption=DATASET_POSE_ARRIVAL_ASSUMPTION,
        arm_disposition_label=label,
        synthetic_test_mode=synthetic_test_mode,
        all_event_pose_indices_verified=all(
            not state.pose_index_applicable or state.pose_index_verified
            for state in states
        ),
        pose_ring_entries=POSE_RING_ENTRIES,
        pose_ring_state_bits=POSE_RING_STATE_BITS,
        pose_ring_accounting=pose_ring_accounting,
        pose_ring_accounting_sha256=pose_ring_accounting.canonical_sha256(),
    )


def run_delayed_unbounded_diagnostic(
    *,
    window_id: str,
    window_start_ns: int,
    events: Sequence[Event],
    poses: Sequence[PosePacket],
    synthetic_test_mode: bool = False,
) -> DelayedUnboundedDiagnosticEvidence:
    """Run the delayed arm without its 1,024-entry full-pressure action.

    This entry point is fixed to ``delayed_exact`` and is diagnostic evidence
    only. It shares input validation, occurrence capture, two-lane service,
    deadlines, pose visibility, receipts, and pose-ring safety with the
    bounded model.
    """

    _nonempty_text(window_id, "window_id")
    states, checked_poses = _validate_and_prepare(
        window_start_ns,
        Arm.DELAYED_EXACT,
        events,
        poses,
        synthetic_test_mode,
    )
    records, peak, peak_staging = _run_delayed_unbounded(
        window_id, states, checked_poses
    )
    _validate_conservation(states, records)
    _validate_delayed_dispositions(states, checked_poses, records)
    cycle_receipts = _make_cycle_receipts(
        window_id, Arm.DELAYED_EXACT, states, records
    )
    pose_ring_accounting = _verify_pose_ring(states, checked_poses, records)

    serializer_cycles = []  # type: List[int]
    baseline_retire_cycles = []  # type: List[int]
    policy_added_cycles = []  # type: List[int]
    for state, record in zip(states, records):
        if state.accept_cycle is None:
            raise CycleModelError(
                "unbounded diagnostic retired event has no serializer exit cycle"
            )
        serializer_cycle_count = state.accept_cycle - state.occurrence_cycle
        baseline_retire_cycle = state.accept_cycle + TRANSFORM_PIPELINE_CYCLES
        serializer_cycles.append(serializer_cycle_count)
        baseline_retire_cycles.append(baseline_retire_cycle)
        policy_added_cycles.append(record.retire_cycle - baseline_retire_cycle)

    input_event_ids = tuple(state.event.event_id for state in states)
    retired_event_ids = tuple(record.event_id for record in records)
    full_pressure_present = any(
        record.disposition_reason == "fifo_full_forced_bypass"
        for record in records
    ) or any(
        receipt.disposition_reason == "fifo_full_forced_bypass"
        for receipt in cycle_receipts
    )
    if full_pressure_present:
        raise CycleModelError(
            "unbounded diagnostic generated a forbidden full-pressure reason"
        )
    config = _delayed_unbounded_config()
    evidence = DelayedUnboundedDiagnosticEvidence(
        schema=DELAYED_UNBOUNDED_DIAGNOSTIC_SCHEMA,
        window_id=window_id,
        arm=Arm.DELAYED_EXACT,
        arm_semantic_label=ARM_LABELS[Arm.DELAYED_EXACT.value],
        config=config,
        config_identity_sha256=config.canonical_sha256(),
        input_event_ids=input_event_ids,
        retired_event_ids=retired_event_ids,
        input_event_ids_sha256=_canonical_sha256(list(input_event_ids)),
        retired_event_ids_sha256=_canonical_sha256(list(retired_event_ids)),
        input_count=len(input_event_ids),
        retired_count=len(retired_event_ids),
        exact_once_ordered_conservation=input_event_ids == retired_event_ids,
        no_full_pressure_reasons=True,
        peak_fifo_depth=peak,
        peak_ingress_staging_occupancy=peak_staging,
        records=tuple(records),
        decision_records_sha256=_canonical_sha256(
            [record.to_mapping() for record in records]
        ),
        cycle_receipts=cycle_receipts,
        cycle_receipts_sha256=_canonical_sha256(
            [receipt.to_mapping() for receipt in cycle_receipts]
        ),
        common_serializer_cycles=tuple(serializer_cycles),
        always_bypass_retire_cycles=tuple(baseline_retire_cycles),
        policy_added_latency_cycles=tuple(policy_added_cycles),
        synthetic_test_mode=synthetic_test_mode,
        all_event_pose_indices_verified=all(
            not state.pose_index_applicable or state.pose_index_verified
            for state in states
        ),
        pose_ring_accounting=pose_ring_accounting,
        pose_ring_accounting_sha256=pose_ring_accounting.canonical_sha256(),
    )
    evidence.validate()
    return evidence
