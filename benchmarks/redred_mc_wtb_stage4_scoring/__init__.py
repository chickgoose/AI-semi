"""Frame-safe offline scoring for the frozen MC-WTB Stage-4 comparison."""

from .scoring import (
    ArmAggregate,
    EventLoss,
    LatencySummary,
    RayEvent,
    ScoreInputManifest,
    ScoreFreeAccounting,
    ShadowRay,
    ScoringError,
    WindowMetrics,
    aggregate_arm,
    finalize_disposition,
    is_positive_window,
    nearest_rank_latency,
    score_window,
    validate_complete_comparison,
)

__all__ = (
    "ArmAggregate",
    "EventLoss",
    "LatencySummary",
    "RayEvent",
    "ScoreInputManifest",
    "ScoreFreeAccounting",
    "ShadowRay",
    "ScoringError",
    "WindowMetrics",
    "aggregate_arm",
    "finalize_disposition",
    "is_positive_window",
    "nearest_rank_latency",
    "score_window",
    "validate_complete_comparison",
)
