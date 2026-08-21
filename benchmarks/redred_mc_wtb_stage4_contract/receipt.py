"""Score-free Stage-4 decision records and exact-once receipts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import (
    EXPECTED_CANONICAL_SHA256,
    ComparisonContract,
    canonical_json_bytes,
    canonical_sha256,
)


class ReceiptError(ValueError):
    """A decision record or exact-once receipt invariant failed."""


DECISION_ARMS = frozenset(
    (
        "zoh_freshness",
        "delayed_exact",
        "causal_cav",
        "oracle_resampled_groundtruth_1khz",
    )
)
DISPOSITIONS = frozenset(("corrected_world_ray", "raw_bypass"))
ARM_LABELS = {
    "zoh_freshness": "CAUSAL_CANDIDATE",
    "causal_cav": "CAUSAL_CANDIDATE",
    "delayed_exact": "DIAGNOSTIC_UPPER_BOUND",
    "oracle_resampled_groundtruth_1khz": "INTERFACE_VALUE_ONLY",
}
DELAYED_RAW_REASONS = frozenset(
    ("deadline_timeout", "fifo_full_forced_bypass", "invalid_pose", "missing_bracket")
)
EXPECTED_REGISTRY_SHA256 = (
    "19df5788d3300ef9e6169165ed1dc68806a08f4e4af73eb4a52aebc9b642f62f"
)
DATASET_POSE_ARRIVAL_ASSUMPTION = "arrival_equals_recorded_timestamp"
SINK_MODE = "always_ready"
_REASON = re.compile(r"[a-z][a-z0-9_]*\Z")
_DECISION_FIELDS = frozenset(
    (
        "window_id",
        "event_id",
        "event_timestamp_ns",
        "arm",
        "arm_semantic_label",
        "occurrence_cycle",
        "retire_cycle",
        "occurrence_pose_ids",
        "occurrence_pose_timestamps_ns",
        "occurrence_pose_commit_cycles",
        "occurrence_pose_sha256",
        "used_pose_ids",
        "used_pose_timestamps_ns",
        "used_pose_commit_cycles",
        "used_pose_sha256",
        "intentional_future_pose_use",
        "pose_age_ns",
        "disposition",
        "disposition_reason",
        "queue_cycles",
    )
)


def _is_nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _is_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int)


def _is_sha256(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _reject_score_or_loss_fields(value: Any, where: str = "decision") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise ReceiptError("%s contains a non-string field" % where)
            lowered = key.lower()
            if "score" in lowered or "loss" in lowered:
                raise ReceiptError("decision records must not contain score/loss fields")
            _reject_score_or_loss_fields(item, "%s.%s" % (where, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_score_or_loss_fields(item, "%s[%d]" % (where, index))


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

    def __post_init__(self) -> None:
        if type(self.window_id) is not str or not self.window_id:
            raise ReceiptError("window_id must be a non-empty string")
        if not _is_nonnegative_int(self.event_id):
            raise ReceiptError("event_id must be a non-negative integer")
        if not _is_nonnegative_int(self.event_timestamp_ns):
            raise ReceiptError("event_timestamp_ns must be a non-negative integer")
        if type(self.arm) is not str or self.arm not in DECISION_ARMS:
            raise ReceiptError("arm is not frozen by the comparison contract")
        if self.arm_semantic_label != ARM_LABELS[self.arm]:
            raise ReceiptError("arm_semantic_label differs from the frozen arm")
        if not _is_nonnegative_int(self.occurrence_cycle):
            raise ReceiptError("occurrence_cycle must be a non-negative integer")
        if not _is_nonnegative_int(self.retire_cycle):
            raise ReceiptError("retire_cycle must be a non-negative integer")
        if self.retire_cycle < self.occurrence_cycle:
            raise ReceiptError("retirement precedes occurrence")
        pose_groups = (
            (
                "occurrence",
                self.occurrence_pose_ids,
                self.occurrence_pose_timestamps_ns,
                self.occurrence_pose_commit_cycles,
                self.occurrence_pose_sha256,
            ),
            (
                "used",
                self.used_pose_ids,
                self.used_pose_timestamps_ns,
                self.used_pose_commit_cycles,
                self.used_pose_sha256,
            ),
        )
        for name, ids, timestamps, commits, hashes in pose_groups:
            if not all(type(value) is tuple for value in (ids, timestamps, commits, hashes)):
                raise ReceiptError("%s pose provenance must use tuples" % name)
            if len({len(ids), len(timestamps), len(commits), len(hashes)}) != 1:
                raise ReceiptError("%s pose provenance arrays have different lengths" % name)
            if len(ids) > 2:
                raise ReceiptError("%s pose snapshot exceeds two poses" % name)
            if any(not _is_nonnegative_int(value) for value in ids):
                raise ReceiptError("%s pose IDs must be non-negative integers" % name)
            if any(not _is_nonnegative_int(value) for value in timestamps):
                raise ReceiptError("%s pose timestamps must be non-negative integers" % name)
            if any(not _is_int(value) for value in commits):
                raise ReceiptError("%s pose commit cycles must be signed integers" % name)
            if any(
                type(value) is not str
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in hashes
            ):
                raise ReceiptError("%s pose hashes must be lowercase SHA-256" % name)
            if any(right <= left for left, right in zip(ids, ids[1:])):
                raise ReceiptError("%s pose IDs must be strictly increasing" % name)
            if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
                raise ReceiptError("%s pose timestamps must be strictly increasing" % name)
        if any(cycle >= self.occurrence_cycle for cycle in self.occurrence_pose_commit_cycles):
            raise ReceiptError("occurrence pose is not visible before the event edge")
        if any(
            timestamp > self.event_timestamp_ns
            for timestamp in self.occurrence_pose_timestamps_ns
        ):
            raise ReceiptError("occurrence pose has a future measurement timestamp")
        if type(self.intentional_future_pose_use) is not bool:
            raise ReceiptError("intentional_future_pose_use must be bool")
        occurrence_rows = set(zip(
            self.occurrence_pose_ids,
            self.occurrence_pose_timestamps_ns,
            self.occurrence_pose_commit_cycles,
            self.occurrence_pose_sha256,
        ))
        used_rows = tuple(zip(
            self.used_pose_ids,
            self.used_pose_timestamps_ns,
            self.used_pose_commit_cycles,
            self.used_pose_sha256,
        ))
        if self.arm == "delayed_exact":
            if self.disposition == "corrected_world_ray":
                if not self.intentional_future_pose_use:
                    raise ReceiptError("corrected delayed_exact must declare future pose use")
                if len(used_rows) != 2 or not (
                    self.used_pose_timestamps_ns[0]
                    <= self.event_timestamp_ns
                    < self.used_pose_timestamps_ns[1]
                ):
                    raise ReceiptError("delayed_exact requires a strict right bracket")
                if any(cycle >= self.retire_cycle for cycle in self.used_pose_commit_cycles):
                    raise ReceiptError("delayed pose is not visible before retirement")
            else:
                if self.intentional_future_pose_use:
                    raise ReceiptError("delayed raw bypass cannot claim future pose use")
                if any(row not in occurrence_rows for row in used_rows):
                    raise ReceiptError("delayed raw bypass used pose outside occurrence snapshot")
                if self.disposition_reason not in DELAYED_RAW_REASONS:
                    raise ReceiptError("delayed raw bypass reason is not frozen")
        else:
            if self.intentional_future_pose_use:
                raise ReceiptError("causal arm cannot declare future pose use")
            if any(row not in occurrence_rows for row in used_rows):
                raise ReceiptError("causal arm used pose outside occurrence snapshot")
        if self.pose_age_ns is not None and (
            isinstance(self.pose_age_ns, bool) or not isinstance(self.pose_age_ns, int)
        ):
            raise ReceiptError("pose_age_ns must be an integer or null")
        if self.used_pose_ids and self.pose_age_ns is None:
            raise ReceiptError("pose_age_ns is required when a pose is available")
        if not self.used_pose_ids and self.pose_age_ns is not None:
            raise ReceiptError("pose_age_ns requires an available pose")
        if self.used_pose_ids and self.pose_age_ns != (
            self.event_timestamp_ns - self.used_pose_timestamps_ns[-1]
        ):
            raise ReceiptError("pose_age_ns differs from the latest used pose")
        if type(self.disposition) is not str or self.disposition not in DISPOSITIONS:
            raise ReceiptError("invalid disposition")
        if type(self.disposition_reason) is not str or _REASON.fullmatch(
            self.disposition_reason
        ) is None:
            raise ReceiptError("disposition_reason must be lower snake case")
        if not _is_nonnegative_int(self.queue_cycles):
            raise ReceiptError("queue_cycles must be a non-negative integer")
        if self.queue_cycles > self.retire_cycle - self.occurrence_cycle:
            raise ReceiptError("queue_cycles exceeds occurrence-to-retire latency")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DecisionRecord":
        if not isinstance(value, Mapping):
            raise ReceiptError("decision record must be an object")
        _reject_score_or_loss_fields(value)
        if set(value) != _DECISION_FIELDS:
            missing = sorted(_DECISION_FIELDS - set(value))
            extra = sorted(set(value) - _DECISION_FIELDS)
            raise ReceiptError(
                "decision fields differ; missing=%r extra=%r" % (missing, extra)
            )
        tuple_fields = (
            "occurrence_pose_ids",
            "occurrence_pose_timestamps_ns",
            "occurrence_pose_commit_cycles",
            "occurrence_pose_sha256",
            "used_pose_ids",
            "used_pose_timestamps_ns",
            "used_pose_commit_cycles",
            "used_pose_sha256",
        )
        for field in tuple_fields:
            field_value = value[field]
            if not isinstance(field_value, (list, tuple)) or isinstance(
                field_value, (str, bytes)
            ):
                raise ReceiptError("%s must be an array" % field)
        return cls(
            window_id=value["window_id"],
            event_id=value["event_id"],
            event_timestamp_ns=value["event_timestamp_ns"],
            arm=value["arm"],
            arm_semantic_label=value["arm_semantic_label"],
            occurrence_cycle=value["occurrence_cycle"],
            retire_cycle=value["retire_cycle"],
            occurrence_pose_ids=tuple(value["occurrence_pose_ids"]),
            occurrence_pose_timestamps_ns=tuple(value["occurrence_pose_timestamps_ns"]),
            occurrence_pose_commit_cycles=tuple(value["occurrence_pose_commit_cycles"]),
            occurrence_pose_sha256=tuple(value["occurrence_pose_sha256"]),
            used_pose_ids=tuple(value["used_pose_ids"]),
            used_pose_timestamps_ns=tuple(value["used_pose_timestamps_ns"]),
            used_pose_commit_cycles=tuple(value["used_pose_commit_cycles"]),
            used_pose_sha256=tuple(value["used_pose_sha256"]),
            intentional_future_pose_use=value["intentional_future_pose_use"],
            pose_age_ns=value["pose_age_ns"],
            disposition=value["disposition"],
            disposition_reason=value["disposition_reason"],
            queue_cycles=value["queue_cycles"],
        )

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
            "occurrence_pose_timestamps_ns": list(self.occurrence_pose_timestamps_ns),
            "occurrence_pose_commit_cycles": list(self.occurrence_pose_commit_cycles),
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


@dataclass(frozen=True)
class DecisionReceipt:
    comparison_contract_sha256: str
    registry_sha256: str
    dataset_pose_arrival_assumption: str
    window_id: str
    arm: str
    expected_events: int
    retired_records: int
    ordered_event_ids_sha256: str
    decision_records_sha256: str
    sink_mode: str = SINK_MODE

    def __post_init__(self) -> None:
        if self.comparison_contract_sha256 != EXPECTED_CANONICAL_SHA256:
            raise ReceiptError("receipt comparison contract hash is not current")
        if self.registry_sha256 != EXPECTED_REGISTRY_SHA256:
            raise ReceiptError("receipt registry hash is not frozen")
        for name in ("ordered_event_ids_sha256", "decision_records_sha256"):
            if not _is_sha256(getattr(self, name)):
                raise ReceiptError("receipt %s is not lowercase SHA-256" % name)
        if self.dataset_pose_arrival_assumption != DATASET_POSE_ARRIVAL_ASSUMPTION:
            raise ReceiptError("receipt pose arrival assumption is not frozen")
        if type(self.window_id) is not str or not self.window_id:
            raise ReceiptError("receipt window_id must be a non-empty string")
        if type(self.arm) is not str or self.arm not in DECISION_ARMS:
            raise ReceiptError("receipt arm is not frozen")
        if not _is_nonnegative_int(self.expected_events):
            raise ReceiptError("receipt expected_events must be a non-negative integer")
        if not _is_nonnegative_int(self.retired_records):
            raise ReceiptError("receipt retired_records must be a non-negative integer")
        if self.expected_events != self.retired_records:
            raise ReceiptError("receipt accepted/retired counts differ")
        if self.sink_mode != SINK_MODE:
            raise ReceiptError("receipt sink_mode must be always_ready")

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "schema": "redred.mc_wtb.stage4_decision_receipt/v2",
            "comparison_contract_sha256": self.comparison_contract_sha256,
            "registry_sha256": self.registry_sha256,
            "dataset_pose_arrival_assumption": self.dataset_pose_arrival_assumption,
            "window_id": self.window_id,
            "arm": self.arm,
            "expected_events": self.expected_events,
            "retired_records": self.retired_records,
            "ordered_event_ids_sha256": self.ordered_event_ids_sha256,
            "decision_records_sha256": self.decision_records_sha256,
            "sink_mode": self.sink_mode,
            "conservation": {
                "missing_events": 0,
                "duplicate_events": 0,
                "unexpected_events": 0,
                "reordered_events": 0,
                "exact_once": True,
                "ordered_retirement": True,
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_mapping())

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


def _coerce_records(records: Iterable[Any]) -> Tuple[DecisionRecord, ...]:
    result = []  # type: List[DecisionRecord]
    for value in records:
        if isinstance(value, DecisionRecord):
            result.append(value)
        elif isinstance(value, Mapping):
            result.append(DecisionRecord.from_mapping(value))
        else:
            raise ReceiptError("decision records must be DecisionRecord values or objects")
    return tuple(result)


def validate_decision_records(
    contract: ComparisonContract,
    expected_event_ids: Sequence[int],
    records: Iterable[Any],
    *,
    expected_window_id: str,
    expected_arm: str,
) -> DecisionReceipt:
    """Fail closed unless records retire expected events exactly once and in order."""

    if not isinstance(contract, ComparisonContract):
        raise ReceiptError("contract must be a validated ComparisonContract")
    if type(expected_window_id) is not str or not expected_window_id:
        raise ReceiptError("expected_window_id must be a non-empty string")
    if (
        type(expected_arm) is not str
        or expected_arm not in DECISION_ARMS
        or expected_arm not in contract.arms
    ):
        raise ReceiptError("expected_arm is not frozen by the comparison contract")
    if isinstance(expected_event_ids, (str, bytes)) or not isinstance(
        expected_event_ids, Sequence
    ):
        raise ReceiptError("expected_event_ids must be an ordered sequence")
    expected = tuple(expected_event_ids)
    if any(not _is_nonnegative_int(event_id) for event_id in expected):
        raise ReceiptError("expected event IDs must be non-negative integers")
    if len(set(expected)) != len(expected):
        raise ReceiptError("expected event IDs contain duplicates")

    checked = _coerce_records(records)
    if any(record.window_id != expected_window_id for record in checked):
        raise ReceiptError("decision record window differs")
    if any(record.arm != expected_arm for record in checked):
        raise ReceiptError("decision record arm differs")

    actual = tuple(record.event_id for record in checked)
    counts = Counter(actual)
    duplicates = sorted(event_id for event_id, count in counts.items() if count > 1)
    if duplicates:
        raise ReceiptError("duplicate retired event IDs: %r" % duplicates)
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    if missing:
        raise ReceiptError("missing retired event IDs: %r" % missing)
    if unexpected:
        raise ReceiptError("unexpected retired event IDs: %r" % unexpected)
    if len(checked) != len(expected):
        raise ReceiptError("accepted/retired conservation differs")
    if actual != expected:
        raise ReceiptError("retired event order differs")
    if any(
        right.occurrence_cycle < left.occurrence_cycle
        for left, right in zip(checked, checked[1:])
    ):
        raise ReceiptError("occurrence cycles move backwards")
    if any(
        right.retire_cycle < left.retire_cycle
        for left, right in zip(checked, checked[1:])
    ):
        raise ReceiptError("retirement cycles move backwards")

    record_values = [record.to_mapping() for record in checked]
    return DecisionReceipt(
        comparison_contract_sha256=contract.canonical_sha256,
        registry_sha256=contract.registry["sha256"],
        dataset_pose_arrival_assumption=contract.timing[
            "dataset_pose_arrival_assumption"
        ],
        window_id=expected_window_id,
        arm=expected_arm,
        expected_events=len(expected),
        retired_records=len(checked),
        ordered_event_ids_sha256=canonical_sha256(list(expected)),
        decision_records_sha256=canonical_sha256(record_values),
    )
