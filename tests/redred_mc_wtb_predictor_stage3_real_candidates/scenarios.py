"""Shared analytic ledgers used unchanged by RG3, DSPB, and SO3-PLL."""

from __future__ import annotations

from harness import (
    AnalyticStream,
    CommonEvent,
    CommonPose,
    EventCluster,
    z_rotation_degrees,
)


def _pose(pose_id: int, timestamp_ns: int, commit_cycle: int, degrees: float) -> CommonPose:
    return CommonPose(
        pose_id,
        timestamp_ns,
        commit_cycle,
        z_rotation_degrees(degrees),
    )


def _event(event_id: int, timestamp_ns: int, occurrence_cycle: int, decision_cycle: int) -> CommonEvent:
    return CommonEvent(event_id, timestamp_ns, occurrence_cycle, decision_cycle)


def _cluster(*events: CommonEvent) -> EventCluster:
    return EventCluster(tuple(events))


def same_edge_stream(latest_degrees: float = 2.0) -> AnalyticStream:
    """Two same-edge events followed by the first edge allowed to see p2."""

    return AnalyticStream(
        (
            _pose(0, 0, 1, 0.0),
            _pose(1, 1_000_000, 11, 1.0),
            _pose(2, 2_000_000, 21, latest_degrees),
        ),
        (
            _cluster(
                _event(100, 2_000_000, 20, 21),
                _event(101, 2_000_000, 20, 21),
            ),
            _cluster(_event(102, 2_100_000, 21, 22)),
        ),
    )


def fallback_stream() -> AnalyticStream:
    """One ledger that visits bypass, one-pose ZOH, then two-pose CAV."""

    return AnalyticStream(
        (
            _pose(0, 0, 2, 0.0),
            _pose(1, 1_000_000, 10, 1.0),
        ),
        (
            _cluster(_event(200, 0, 0, 1)),
            _cluster(_event(201, 500_000, 2, 3)),
            _cluster(_event(202, 1_500_000, 10, 11)),
        ),
    )


def stop_reversal_stream() -> AnalyticStream:
    """Constant rate, one stopped interval, restart, and signed reversal."""

    angles = (0.0, 1.0, 2.0, 3.0, 3.0, 4.0, 3.0)
    poses = tuple(
        _pose(index, index * 1_000_000, 1 + index * 10, angle)
        for index, angle in enumerate(angles)
    )
    clusters = tuple(
        _cluster(_event(
            300 + index,
            index * 1_000_000 + 500_000,
            pose.commit_cycle,
            pose.commit_cycle + 1,
        ))
        for index, pose in enumerate(poses)
    )
    return AnalyticStream(poses, clusters)


def dropout_stream() -> AnalyticStream:
    """A 28 ms pose dropout with pre-commit, same-edge, and future events."""

    return AnalyticStream(
        (
            _pose(0, 0, 1, 0.0),
            _pose(1, 1_000_000, 11, 1.0),
            _pose(2, 2_000_000, 21, 2.0),
            _pose(3, 30_000_000, 301, 30.0),
        ),
        (
            _cluster(_event(400, 25_000_000, 250, 251)),
            _cluster(_event(401, 30_000_000, 300, 301)),
            _cluster(_event(402, 30_100_000, 301, 302)),
        ),
    )


def near_pi_stream() -> AnalyticStream:
    """A frozen 180-degree supplied-pose discontinuity."""

    return AnalyticStream(
        (
            _pose(0, 0, 1, 0.0),
            _pose(1, 1_000_000, 11, 0.0),
            _pose(2, 2_000_000, 21, 180.0),
        ),
        (
            _cluster(_event(500, 2_000_000, 20, 21)),
            _cluster(_event(501, 2_100_000, 21, 22)),
        ),
    )


__all__ = (
    "dropout_stream",
    "fallback_stream",
    "near_pi_stream",
    "same_edge_stream",
    "stop_reversal_stream",
)
