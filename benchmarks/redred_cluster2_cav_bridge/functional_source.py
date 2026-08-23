"""Official UZH functional source for the score-free Cluster2 CAV adapter.

Native one-millisecond cycle identities remain an aligned sidecar.  Geometry
events are ordered by their original timestamp and native event ID, and use
the independent frozen 6.5 ns CAV clock for strict-past pose visibility.
"""

from __future__ import annotations

import bisect
import hashlib
import hmac
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_assay.source import (
    OFFICIAL_SOURCE_PINS,
    SourceInputError,
    ValidatedSources,
    load_pose_samples,
    parse_calibration_bytes,
    sensor_ray,
    validate_sources,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    CAUSAL_POSE_INDEX_LIMIT,
    CycleModelError,
    pose_timestamp_to_cycle,
    timestamp_to_cycle,
)

from .cav_adapter import (
    CAVAdapterError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from .contract import canonical_event_content_sha256, canonical_json_bytes
from .source_crosswalk import (
    BIN_NS,
    SourceCrosswalkError,
    SourceCrosswalkEvent,
    derive_official_uzh_source_crosswalk_files,
)


WINDOW_ID = "uzh-shapes-rotation-cluster2-functional-source"
EXPECTED_EVENT_COUNT = 8_503
EXPECTED_POSE_COUNT = 11_883
CAV_MAX_HORIZON_NS = 5_000_000
ZOH_MAX_AGE_NS = 1_000_000


class FunctionalSourceError(ValueError):
    """The official source cannot produce one exact functional CAV input."""


def _fail(message: str) -> None:
    raise FunctionalSourceError(message)


def _pose_sha256(
    pose_id: int, timestamp_ns: int, quaternion_xyzw: Sequence[float]
) -> str:
    return hashlib.sha256(canonical_json_bytes({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion_xyzw),
    })).hexdigest()


def _stream_sha256(path: Path, where: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise FunctionalSourceError("cannot re-read %s" % where) from error
    return digest.hexdigest()


def _identity(value: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@dataclass(frozen=True)
class NativeEventIdentity:
    """Native-only identity aligned one-for-one with the CAV event tuple."""

    event_id: int
    source_index: int
    native_occurrence_cycle: int
    x: int
    y: int

    def __post_init__(self) -> None:
        for name in (
            "event_id", "source_index", "native_occurrence_cycle", "x", "y"
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                _fail("native identity %s must be a non-negative integer" % name)
        if self.source_index > 15:
            _fail("native identity source_index exceeds 15")
        if not 110 <= self.x <= 113 or not 85 <= self.y <= 88:
            _fail("native identity lies outside the pinned 4x4 patch")
        expected_source = (self.y - 85) * 4 + (self.x - 110)
        if self.source_index != expected_source:
            _fail("native identity source differs from raw coordinates")


@dataclass(frozen=True)
class FunctionalSourceBundle:
    """One official event population plus full pose history and side identity."""

    registry: NeutralRegistryWindow
    events: Tuple[NeutralEventInput, ...]
    poses: Tuple[NeutralPoseInput, ...]
    native_identities: Tuple[NativeEventIdentity, ...]
    required_pose_start_id: int
    required_pose_end_id: int
    required_pose_pre_roll_ns: int
    causal_cav_eligible_count: int
    fresh_zoh_fallback_count: int
    stale_pose_count: int

    @property
    def registry_rows(self) -> Tuple[NeutralRegistryWindow, ...]:
        return (self.registry,)

    @property
    def event_streams(self) -> Mapping[str, Tuple[NeutralEventInput, ...]]:
        return {self.registry.window_id: self.events}

    @property
    def pose_streams(self) -> Mapping[str, Tuple[NeutralPoseInput, ...]]:
        return {self.registry.window_id: self.poses}

    @property
    def native_identity_streams(
        self,
    ) -> Mapping[str, Tuple[NativeEventIdentity, ...]]:
        return {self.registry.window_id: self.native_identities}


def _validated_crosswalk(
    dataset_directory: Path, cyclemask_path: Path
) -> Tuple[Tuple[SourceCrosswalkEvent, ...], ValidatedSources]:
    try:
        sources = validate_sources(dataset_directory)
    except SourceInputError as error:
        raise FunctionalSourceError(
            "official UZH source validation failed: %s" % error
        ) from error
    if sources.pins != OFFICIAL_SOURCE_PINS:
        _fail("source validator did not retain the official UZH pins")
    if sources.events_path != dataset_directory / "events.txt":
        _fail("validated events path differs from the official dataset root")
    try:
        values = derive_official_uzh_source_crosswalk_files(
            sources.events_path, cyclemask_path
        )
    except SourceCrosswalkError as error:
        raise FunctionalSourceError(
            "official UZH crosswalk failed: %s" % error
        ) from error
    if len(values) != EXPECTED_EVENT_COUNT:
        _fail("official UZH crosswalk event count differs")
    identifiers = tuple(value.event_id for value in values)
    if identifiers != tuple(range(EXPECTED_EVENT_COUNT)):
        _fail("native crosswalk event IDs are not contiguous")
    slots = tuple(
        (value.occurrence_cycle, value.source_index) for value in values
    )
    if len(set(slots)) != len(slots):
        _fail("native crosswalk contains a cycle/source collision")
    if slots != tuple(sorted(slots)):
        _fail("native crosswalk order is not cycle-then-source")
    return values, sources


def _validated_poses(
    dataset_directory: Path, window_start_ns: int
) -> Tuple[NeutralPoseInput, ...]:
    path = dataset_directory / "groundtruth.txt"
    try:
        before = path.stat()
        source_poses = load_pose_samples(path)
        after = path.stat()
    except (OSError, SourceInputError) as error:
        raise FunctionalSourceError(
            "official UZH groundtruth parsing failed: %s" % error
        ) from error
    if _identity(before) != _identity(after):
        _fail("groundtruth.txt changed during parsing")
    actual = _stream_sha256(path, "groundtruth.txt")
    try:
        final = path.stat()
    except OSError as error:
        raise FunctionalSourceError("cannot re-stat groundtruth.txt") from error
    if _identity(after) != _identity(final):
        _fail("groundtruth.txt changed during authority re-read")
    if not hmac.compare_digest(actual, OFFICIAL_SOURCE_PINS.groundtruth_sha256):
        _fail("consumed groundtruth.txt differs from its official pin")
    if len(source_poses) != EXPECTED_POSE_COUNT:
        _fail("official UZH pose count differs")

    result = []
    previous_timestamp = -1
    previous_commit = None
    for expected_id, pose in enumerate(source_poses):
        if pose.pose_id != expected_id:
            _fail("official pose IDs are not contiguous source indices")
        if pose.timestamp_ns <= previous_timestamp:
            _fail("official pose timestamps are not strictly increasing")
        try:
            commit = pose_timestamp_to_cycle(pose.timestamp_ns, window_start_ns)
        except CycleModelError as error:
            raise FunctionalSourceError("pose cycle conversion failed") from error
        if previous_commit is not None and commit < previous_commit:
            _fail("official pose commit cycles move backwards")
        if pose.pose_id >= CAUSAL_POSE_INDEX_LIMIT:
            _fail("official pose ID exceeds the CAV event payload")
        quaternion = tuple(pose.quaternion_xyzw)
        try:
            result.append(NeutralPoseInput(
                pose.pose_id,
                pose.timestamp_ns,
                commit,
                quaternion,
                _pose_sha256(pose.pose_id, pose.timestamp_ns, quaternion),
                True,
                True,
            ))
        except CAVAdapterError as error:
            raise FunctionalSourceError("neutral pose construction failed") from error
        previous_timestamp = pose.timestamp_ns
        previous_commit = commit
    return tuple(result)


def _visible_pose_end(
    event_timestamp_ns: int,
    event_cycle: int,
    poses: Tuple[NeutralPoseInput, ...],
    pose_commits: Tuple[int, ...],
) -> int:
    end = bisect.bisect_left(pose_commits, event_cycle)
    while end > 0 and poses[end - 1].timestamp_ns > event_timestamp_ns:
        end -= 1
    if end == 0:
        _fail("event has no strictly pre-edge official pose")
    latest = poses[end - 1]
    if not (
        latest.commit_cycle < event_cycle
        and latest.timestamp_ns <= event_timestamp_ns
    ):
        _fail("strict-past causal pose selection failed")
    if end < len(poses):
        following = poses[end]
        if (
            following.commit_cycle < event_cycle
            and following.timestamp_ns <= event_timestamp_ns
        ):
            _fail("causal pose selection did not choose the latest visible pose")
    return end


def build_official_uzh_functional_source(
    dataset_directory: Path, cyclemask_path: Path
) -> FunctionalSourceBundle:
    """Build the pinned 8,503-event functional CAV source without a scorer."""

    if not isinstance(dataset_directory, Path) or not isinstance(cyclemask_path, Path):
        _fail("dataset directory and cyclemask must be pathlib.Path values")
    crosswalk, sources = _validated_crosswalk(dataset_directory, cyclemask_path)
    native_first_cycle = min(value.occurrence_cycle for value in crosswalk)
    native_last_cycle = max(value.occurrence_cycle for value in crosswalk)
    window_start_ns = native_first_cycle * BIN_NS
    window_end_ns = (native_last_cycle + 1) * BIN_NS

    cav_order = tuple(sorted(
        crosswalk, key=lambda value: (value.timestamp_ns, value.event_id)
    ))
    if len({value.event_id for value in cav_order}) != len(cav_order):
        _fail("CAV source event IDs repeat")
    if any(
        (right.timestamp_ns, right.event_id)
        <= (left.timestamp_ns, left.event_id)
        for left, right in zip(cav_order, cav_order[1:])
    ):
        _fail("CAV event order is not strict timestamp-then-event-ID")
    first_timestamp = cav_order[0].timestamp_ns
    last_timestamp = cav_order[-1].timestamp_ns
    if not window_start_ns < first_timestamp < window_end_ns:
        _fail("native registry does not precede the first CAV event")
    if last_timestamp >= window_end_ns:
        _fail("last CAV event lies outside the native registry cover")
    try:
        registry = NeutralRegistryWindow(
            WINDOW_ID, window_start_ns, first_timestamp, window_end_ns
        )
    except CAVAdapterError as error:
        raise FunctionalSourceError("neutral registry construction failed") from error

    poses = _validated_poses(dataset_directory, window_start_ns)
    pose_commits = tuple(pose.commit_cycle for pose in poses)
    try:
        calibration = parse_calibration_bytes(sources.calibration_bytes)
    except SourceInputError as error:
        raise FunctionalSourceError(
            "official UZH calibration revalidation failed: %s" % error
        ) from error
    if not hmac.compare_digest(
        sources.calibration_sha256, OFFICIAL_SOURCE_PINS.calibration_sha256
    ):
        _fail("consumed calibration differs from its official pin")

    events = []
    identities = []
    visible_ends = []
    causal_cav_eligible = 0
    fresh_zoh = 0
    stale = 0
    for value in cav_order:
        try:
            event_cycle = timestamp_to_cycle(value.timestamp_ns, window_start_ns)
        except CycleModelError as error:
            raise FunctionalSourceError("event cycle conversion failed") from error
        visible_end = _visible_pose_end(
            value.timestamp_ns, event_cycle, poses, pose_commits
        )
        visible_ends.append(visible_end)
        latest = poses[visible_end - 1]
        try:
            ray = sensor_ray(value, calibration)
        except SourceInputError as error:
            raise FunctionalSourceError("sensor-ray construction failed") from error
        if not all(math.isfinite(component) for component in ray):
            _fail("sensor ray contains non-finite data")
        norm = math.sqrt(math.fsum(component * component for component in ray))
        if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
            _fail("sensor ray is not normalized")
        digest = canonical_event_content_sha256(
            value.event_id,
            value.timestamp_ns,
            value.polarity,
            True,
            ray,
            latest.pose_id,
            True,
        )
        try:
            events.append(NeutralEventInput(
                value.event_id,
                value.timestamp_ns,
                value.polarity,
                True,
                ray,
                latest.pose_id,
                digest,
                True,
            ))
        except CAVAdapterError as error:
            raise FunctionalSourceError("neutral event construction failed") from error
        identities.append(NativeEventIdentity(
            value.event_id,
            value.source_index,
            value.occurrence_cycle,
            value.x,
            value.y,
        ))

        age = value.timestamp_ns - latest.timestamp_ns
        if age < 0:
            _fail("causal pose age is negative")
        eligible = False
        if visible_end >= 2:
            previous = poses[visible_end - 2]
            interval = latest.timestamp_ns - previous.timestamp_ns
            eligible = age <= min(CAV_MAX_HORIZON_NS, interval)
        if eligible:
            causal_cav_eligible += 1
        elif age <= ZOH_MAX_AGE_NS:
            fresh_zoh += 1
        else:
            stale += 1

    if len(events) != EXPECTED_EVENT_COUNT or len(identities) != len(events):
        _fail("functional source population count differs")
    if causal_cav_eligible + fresh_zoh + stale != len(events):
        _fail("functional source disposition partition differs")
    first_visible_end = visible_ends[0]
    if first_visible_end < 2:
        _fail("first event lacks two-pose pre-roll support")
    required_pose_start = poses[first_visible_end - 2]
    required_pose_end = poses[max(visible_ends) - 1]
    pre_roll_ns = window_start_ns - required_pose_start.timestamp_ns
    if pre_roll_ns <= 0:
        _fail("required pose pre-roll is not strictly before the registry")

    return FunctionalSourceBundle(
        registry=registry,
        events=tuple(events),
        poses=poses,
        native_identities=tuple(identities),
        required_pose_start_id=required_pose_start.pose_id,
        required_pose_end_id=required_pose_end.pose_id,
        required_pose_pre_roll_ns=pre_roll_ns,
        causal_cav_eligible_count=causal_cav_eligible,
        fresh_zoh_fallback_count=fresh_zoh,
        stale_pose_count=stale,
    )


__all__ = [
    "CAV_MAX_HORIZON_NS",
    "EXPECTED_EVENT_COUNT",
    "EXPECTED_POSE_COUNT",
    "FunctionalSourceBundle",
    "FunctionalSourceError",
    "NativeEventIdentity",
    "WINDOW_ID",
    "ZOH_MAX_AGE_NS",
    "build_official_uzh_functional_source",
]
