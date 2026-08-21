"""Score-free Stage-4 decision records and exact-once receipts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import ComparisonContract, canonical_json_bytes, canonical_sha256


class ReceiptError(ValueError):
    """A decision record or exact-once receipt invariant failed."""


DECISION_ARMS = frozenset(
    ("zoh_freshness", "delayed_exact", "causal_cav", "supplied_pose_1khz")
)
DISPOSITIONS = frozenset(("corrected_world_ray", "raw_bypass"))
_REASON = re.compile(r"[a-z][a-z0-9_]*\Z")
_DECISION_FIELDS = frozenset(
    (
        "window_id",
        "event_id",
        "arm",
        "occurrence_cycle",
        "retire_cycle",
        "available_pose_ids",
        "available_pose_timestamps_ns",
        "pose_age_ns",
        "disposition",
        "disposition_reason",
        "queue_cycles",
    )
)


def _is_nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


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
    arm: str
    occurrence_cycle: int
    retire_cycle: int
    available_pose_ids: Tuple[int, ...]
    available_pose_timestamps_ns: Tuple[int, ...]
    pose_age_ns: Optional[int]
    disposition: str
    disposition_reason: str
    queue_cycles: int

    def __post_init__(self) -> None:
        if type(self.window_id) is not str or not self.window_id:
            raise ReceiptError("window_id must be a non-empty string")
        if not _is_nonnegative_int(self.event_id):
            raise ReceiptError("event_id must be a non-negative integer")
        if type(self.arm) is not str or self.arm not in DECISION_ARMS:
            raise ReceiptError("arm is not frozen by the comparison contract")
        if not _is_nonnegative_int(self.occurrence_cycle):
            raise ReceiptError("occurrence_cycle must be a non-negative integer")
        if not _is_nonnegative_int(self.retire_cycle):
            raise ReceiptError("retire_cycle must be a non-negative integer")
        if self.retire_cycle < self.occurrence_cycle:
            raise ReceiptError("retirement precedes occurrence")
        if type(self.available_pose_ids) is not tuple:
            raise ReceiptError("available_pose_ids must be a tuple")
        if type(self.available_pose_timestamps_ns) is not tuple:
            raise ReceiptError("available_pose_timestamps_ns must be a tuple")
        if len(self.available_pose_ids) != len(self.available_pose_timestamps_ns):
            raise ReceiptError("available pose IDs/timestamps have different lengths")
        if any(not _is_nonnegative_int(value) for value in self.available_pose_ids):
            raise ReceiptError("available pose IDs must be non-negative integers")
        if any(
            not _is_nonnegative_int(value)
            for value in self.available_pose_timestamps_ns
        ):
            raise ReceiptError("available pose timestamps must be non-negative integers")
        if any(
            right <= left
            for left, right in zip(self.available_pose_ids, self.available_pose_ids[1:])
        ):
            raise ReceiptError("available pose IDs must be strictly increasing")
        if any(
            right <= left
            for left, right in zip(
                self.available_pose_timestamps_ns,
                self.available_pose_timestamps_ns[1:],
            )
        ):
            raise ReceiptError("available pose timestamps must be strictly increasing")
        if self.pose_age_ns is not None and (
            isinstance(self.pose_age_ns, bool) or not isinstance(self.pose_age_ns, int)
        ):
            raise ReceiptError("pose_age_ns must be an integer or null")
        if self.available_pose_ids and self.pose_age_ns is None:
            raise ReceiptError("pose_age_ns is required when a pose is available")
        if not self.available_pose_ids and self.pose_age_ns is not None:
            raise ReceiptError("pose_age_ns requires an available pose")
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
        pose_ids = value["available_pose_ids"]
        pose_times = value["available_pose_timestamps_ns"]
        if not isinstance(pose_ids, (list, tuple)) or isinstance(pose_ids, (str, bytes)):
            raise ReceiptError("available_pose_ids must be an array")
        if not isinstance(pose_times, (list, tuple)) or isinstance(pose_times, (str, bytes)):
            raise ReceiptError("available_pose_timestamps_ns must be an array")
        return cls(
            window_id=value["window_id"],
            event_id=value["event_id"],
            arm=value["arm"],
            occurrence_cycle=value["occurrence_cycle"],
            retire_cycle=value["retire_cycle"],
            available_pose_ids=tuple(pose_ids),
            available_pose_timestamps_ns=tuple(pose_times),
            pose_age_ns=value["pose_age_ns"],
            disposition=value["disposition"],
            disposition_reason=value["disposition_reason"],
            queue_cycles=value["queue_cycles"],
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "arm": self.arm,
            "occurrence_cycle": self.occurrence_cycle,
            "retire_cycle": self.retire_cycle,
            "available_pose_ids": list(self.available_pose_ids),
            "available_pose_timestamps_ns": list(
                self.available_pose_timestamps_ns
            ),
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

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "schema": "redred.mc_wtb.stage4_decision_receipt/v1",
            "comparison_contract_sha256": self.comparison_contract_sha256,
            "registry_sha256": self.registry_sha256,
            "dataset_pose_arrival_assumption": self.dataset_pose_arrival_assumption,
            "window_id": self.window_id,
            "arm": self.arm,
            "expected_events": self.expected_events,
            "retired_records": self.retired_records,
            "ordered_event_ids_sha256": self.ordered_event_ids_sha256,
            "decision_records_sha256": self.decision_records_sha256,
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
