"""Candidate adapter protocol used only by the independent synthetic oracle.

The candidate receives physical event fields, the causally visible pose prefix,
and an opaque state snapshot.  It never receives the oracle truth function,
future records, scenario labels, window membership, or score information.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Tuple


Quaternion = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PoseRecord:
    pose_id: str
    measurement_timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: Quaternion
    valid: bool = True


@dataclass(frozen=True)
class EventRecord:
    """Oracle-ledger event; ``event_id`` is never exposed to the candidate."""

    event_id: str
    timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int
    x: int = 0
    y: int = 0
    polarity: int = 1


@dataclass(frozen=True)
class PredictorEvent:
    """Neutral physical event projection visible to a candidate adapter."""

    timestamp_ns: int
    x: int
    y: int
    polarity: int


@dataclass(frozen=True)
class CausalView:
    decision_cycle: int
    visible_poses: Tuple[PoseRecord, ...]
    state_version: int
    state_effective_cycle: int


@dataclass(frozen=True)
class FallbackDecision:
    mode: str
    quaternion_xyzw: Optional[Quaternion]
    used_pose_ids: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ForecastReceipt:
    """Forecast made from the immutable state preceding a new pose commit."""

    source_state_version: int
    generation_cycle: int
    target_timestamp_ns: int
    quaternion_xyzw: Optional[Quaternion]


@dataclass(frozen=True)
class PoseFeedback:
    pose: PoseRecord
    forecast: ForecastReceipt


@dataclass(frozen=True)
class AdapterDecision:
    """Candidate response for one event.

    ``use_fallback`` asks the harness to copy the frozen fallback decision
    exactly.  A candidate prediction must cite the state version it consumed
    and may cite only pose IDs present in ``CausalView.visible_poses``.
    """

    use_fallback: bool
    quaternion_xyzw: Optional[Quaternion]
    used_pose_ids: Tuple[str, ...]
    source_state_version: int
    reason: str = ""


class CandidateNumericError(ArithmeticError):
    """Explicit fail-open signal from a candidate adapter."""


class CandidateAdapter(Protocol):
    """Minimal causal boundary for a Stage-3 predictor candidate.

    Adapter state must be deepcopy-able and equality comparable.  The harness
    supplies copies and rejects in-call mutation.  ``forecast_pose`` and
    ``accept_pose`` are invoked only for valid pose commits after all event
    decisions on that edge.  Returned state becomes effective at commit+1.
    """

    candidate_id: str

    def initial_state(self) -> Any:
        ...

    def forecast_pose(
        self,
        state: Any,
        target_timestamp_ns: int,
        visible_poses: Tuple[PoseRecord, ...],
    ) -> Optional[Quaternion]:
        ...

    def accept_pose(self, state: Any, feedback: PoseFeedback) -> Any:
        ...

    def decide(
        self,
        state: Any,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        ...
