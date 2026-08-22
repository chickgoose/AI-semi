"""Independent exact-output gate for the three production Stage-3 adapters.

The gate deliberately does not regenerate candidate geometry.  A pristine
production replay is captured once, then this module independently validates
the public envelope, every nested seal, neutral identity/order/cardinality,
pose visibility, fallback shape, and finally byte-exact equality with that
replay.  Mutation tests can therefore reseal dishonest output without gaining
authority to alter the expected result.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Dict, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_predictor_stage3.dspb_output import (
    generate_dspb_candidate_output,
    locked_dspb_config_sha256,
    locked_dspb_executable_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_output import (
    CANDIDATE_ID as PLL_CANDIDATE_ID,
    generate_locked_pll_output,
    generator_executable_sha256,
    locked_config_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3_output import (
    RG3_CONFIG_SHA256,
    RG3_EXECUTABLE_SHA256,
    RG3_OUTPUT_CANDIDATE_ID,
    generate_locked_rg3_output,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterBundle,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ADAPTER_SHA256 = "6" * 64
CANDIDATE_NAMES = ("RG3", "DSPB", "PLL")
OUTPUT_FIELDS = frozenset((
    "schema",
    "candidate_id",
    "adapter_aggregate_sha256",
    "neutral_input_sha256",
    "candidate_executable_sha256",
    "candidate_config_sha256",
    "windows",
    "aggregate_sha256",
))
WINDOW_FIELDS = frozenset(("window_id", "events", "events_sha256"))
EVENT_FIELDS = frozenset((
    "event_id",
    "event_content_sha256",
    "decision_cycle",
    "model_id",
    "predictor_state_version",
    "used_pose_ids",
    "candidate_used",
    "fallback_reason",
    "world_ray",
    "decision_sha256",
))


class GateViolation(AssertionError):
    """A sealed candidate output failed the independent test-only gate."""


@dataclass(frozen=True)
class SyntheticFixture:
    registry: Tuple[NeutralRegistryWindow, ...]
    event_streams: Mapping[str, Tuple[NeutralEventInput, ...]]
    pose_streams: Mapping[str, Tuple[NeutralPoseInput, ...]]
    baseline: object
    bundle: New108AdapterBundle


@dataclass(frozen=True)
class CandidateAuthority:
    candidate_id: str
    executable_sha256: str
    config_sha256: str


def _multiply(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _rotation_vector(vector):
    angle = math.sqrt(math.fsum(value * value for value in vector))
    if angle == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    scale = math.sin(0.5 * angle) / angle
    return (
        vector[0] * scale,
        vector[1] * scale,
        vector[2] * scale,
        math.cos(0.5 * angle),
    )


def _rotation_z(angle):
    return (0.0, 0.0, math.sin(0.5 * angle), math.cos(0.5 * angle))


def _ray(angle):
    return (math.cos(angle), math.sin(angle), 0.0)


def _pose(pose_id, timestamp_ns, start_ns, quaternion, valid=True):
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, start_ns),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
        valid,
        valid,
    )


def _event(event_id, timestamp_ns, query_ns, ray_angle, causal_pose_id):
    sensor_ray = _ray(ray_angle)
    is_query = timestamp_ns >= query_ns
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        0,
        is_query,
        sensor_ray,
        causal_pose_id,
        canonical_event_content_sha256(
            event_id,
            timestamp_ns,
            0,
            is_query,
            sensor_ray,
            causal_pose_id,
        ),
    )


def make_motion_fixture(window_count=2):
    """Make score-free windows with fallback, same-edge, and locked rows."""

    registries = []
    event_streams = {}
    pose_streams = {}
    angles = tuple(
        math.radians(value) for value in (0, 1, 3, 6, 10, 15, 21, 28, 36, 45)
    )
    for window_index in range(window_count):
        start = window_index * 100_000_000
        query = start + 50_000_000
        end = query + 500_000
        window_id = "production-motion-%d" % window_index
        pose_base = window_index * 100
        event_base = window_index * 1000
        registries.append(NeutralRegistryWindow(window_id, start, query, end))
        pose_streams[window_id] = tuple(
            _pose(
                pose_base + index,
                start + index * 5_000_000,
                start,
                _rotation_z(angle),
            )
            for index, angle in enumerate(angles)
        )
        event_streams[window_id] = (
            _event(event_base, start + 2_500_000, query, 0.00, pose_base),
            # Pose 9 commits on this exact edge and must be excluded.
            _event(event_base + 1, start + 45_000_000, query, 0.01, pose_base + 8),
            _event(event_base + 2, start + 45_000_000, query, 0.02, pose_base + 8),
            _event(event_base + 3, start + 49_200_000, query, 0.03, pose_base + 9),
            _event(event_base + 4, start + 49_800_000, query, 0.04, pose_base + 9),
            _event(event_base + 5, query, query, 0.05, pose_base + 9),
            _event(event_base + 6, query, query, 0.06, pose_base + 9),
        )
    registry = tuple(registries)
    baseline = evaluate_current_cav_registry(registry, event_streams, pose_streams)
    bundle = New108AdapterBundle(
        {},
        registry,
        event_streams,
        pose_streams,
        {},
        {"aggregate_sha256": ADAPTER_SHA256},
    )
    return SyntheticFixture(registry, event_streams, pose_streams, baseline, bundle)


def make_noncommuting_rg3_fixture():
    """Make a smooth multi-axis history whose transported acceleration matters."""

    registry = (NeutralRegistryWindow(
        "rg3-noncommuting", 0, 50_000_000, 50_500_000
    ),)
    quaternions = [(0.0, 0.0, 0.0, 1.0)]
    for index in range(1, 10):
        increment = _rotation_vector((
            0.035 + 0.001 * index,
            0.003 * index,
            0.001 * (index % 3),
        ))
        quaternions.append(_multiply(quaternions[-1], increment))
    poses = tuple(
        _pose(index, index * 5_000_000, 0, quaternion)
        for index, quaternion in enumerate(quaternions)
    )
    # A nearby warm-up event supplies the evaluator's same-frame causal
    # reference; both events still exercise the multi-axis RG3 forecast.
    events = (
        _event(89, 49_000_000, 50_000_000, 0.0, 9),
        _event(90, 50_000_000, 50_000_000, 0.1, 9),
    )
    event_streams = {"rg3-noncommuting": events}
    pose_streams = {"rg3-noncommuting": poses}
    baseline = evaluate_current_cav_registry(registry, event_streams, pose_streams)
    bundle = New108AdapterBundle(
        {}, registry, event_streams, pose_streams, {},
        {"aggregate_sha256": ADAPTER_SHA256},
    )
    return SyntheticFixture(registry, event_streams, pose_streams, baseline, bundle)


def authority(candidate_name):
    if candidate_name == "RG3":
        return CandidateAuthority(
            RG3_OUTPUT_CANDIDATE_ID, RG3_EXECUTABLE_SHA256, RG3_CONFIG_SHA256
        )
    if candidate_name == "DSPB":
        return CandidateAuthority(
            DSPBConfig().candidate_id,
            locked_dspb_executable_sha256(),
            locked_dspb_config_sha256(),
        )
    if candidate_name == "PLL":
        return CandidateAuthority(
            PLL_CANDIDATE_ID,
            generator_executable_sha256(),
            locked_config_sha256(),
        )
    raise GateViolation("unknown candidate name")


def generate_production_output(candidate_name, fixture):
    """Invoke the selected actual candidate through its production adapter."""

    if candidate_name == "RG3":
        return generate_locked_rg3_output(
            fixture.registry,
            fixture.event_streams,
            fixture.pose_streams,
            ADAPTER_SHA256,
        )
    if candidate_name == "DSPB":
        return generate_dspb_candidate_output(
            fixture.registry,
            fixture.event_streams,
            fixture.pose_streams,
            ADAPTER_SHA256,
        )
    if candidate_name == "PLL":
        return generate_locked_pll_output(fixture.bundle, fixture.baseline)
    raise GateViolation("unknown candidate name")


def reseal(value):
    """Reseal a test mutation so digest-only checks cannot kill it."""

    output = deepcopy(value)
    for window in output["windows"]:
        for event in window["events"]:
            body = dict(event)
            body.pop("decision_sha256", None)
            event["decision_sha256"] = canonical_sha256(body)
        window["events_sha256"] = canonical_sha256(window["events"])
    body = dict(output)
    body.pop("aggregate_sha256", None)
    output["aggregate_sha256"] = canonical_sha256(body)
    return output


class ExactProductionGate:
    """Validate one adapter's public output without calling that adapter."""

    def __init__(self, candidate_name, fixture, pristine):
        self.candidate_name = candidate_name
        self.fixture = fixture
        self.expected = deepcopy(pristine)
        self.authority = authority(candidate_name)

    @staticmethod
    def _mapping(value, fields, where):
        if not isinstance(value, Mapping) or frozenset(value) != fields:
            raise GateViolation("%s field schema differs" % where)
        return value

    @staticmethod
    def _digest(value, where):
        if type(value) is not str or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise GateViolation("%s is not lowercase SHA-256" % where)

    @staticmethod
    def _unit_ray(value):
        if not isinstance(value, list) or len(value) != 3:
            raise GateViolation("candidate world ray shape differs")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
            raise GateViolation("candidate world ray is not numeric")
        norm = math.sqrt(math.fsum(float(item) * float(item) for item in value))
        if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
            raise GateViolation("candidate world ray is not unit length")

    def validate(self, value):
        output = self._mapping(value, OUTPUT_FIELDS, "candidate output")
        if output["schema"] != "redred.mc_wtb_predictor_stage3.candidate_output/v1":
            raise GateViolation("candidate output schema differs")
        if output["candidate_id"] != self.authority.candidate_id:
            raise GateViolation("candidate identity differs")
        if output["adapter_aggregate_sha256"] != ADAPTER_SHA256:
            raise GateViolation("adapter identity differs")
        if output["neutral_input_sha256"] != self.fixture.baseline.neutral_input_sha256:
            raise GateViolation("neutral input identity differs")
        if output["candidate_executable_sha256"] != self.authority.executable_sha256:
            raise GateViolation("candidate executable identity differs")
        if output["candidate_config_sha256"] != self.authority.config_sha256:
            raise GateViolation("candidate config identity differs")
        for field in (
            "adapter_aggregate_sha256",
            "neutral_input_sha256",
            "candidate_executable_sha256",
            "candidate_config_sha256",
            "aggregate_sha256",
        ):
            self._digest(output[field], field)
        unsigned = dict(output)
        supplied = unsigned.pop("aggregate_sha256")
        if supplied != canonical_sha256(unsigned):
            raise GateViolation("aggregate seal differs")

        windows = output["windows"]
        if not isinstance(windows, list):
            raise GateViolation("candidate windows are not a list")
        expected_ids = [row.window_id for row in self.fixture.registry]
        if [row.get("window_id") if isinstance(row, Mapping) else None for row in windows] != expected_ids:
            raise GateViolation("window identity/order differs")

        for supplied_window, baseline_window in zip(
            windows, self.fixture.baseline.windows
        ):
            window = self._mapping(supplied_window, WINDOW_FIELDS, "candidate window")
            events = window["events"]
            if not isinstance(events, list):
                raise GateViolation("candidate events are not a list")
            if window["events_sha256"] != canonical_sha256(events):
                raise GateViolation("window seal differs")
            expected_events = tuple(baseline_window.input_events)
            baseline_rows = tuple(baseline_window.simulation.records)
            if len(events) != len(expected_events):
                raise GateViolation("event cardinality differs")
            poses = {pose.pose_id: pose for pose in baseline_window.input_poses}
            prior_state = None
            state_by_edge = {}  # type: Dict[int, int]
            for event, expected_event, baseline_row in zip(
                events, expected_events, baseline_rows
            ):
                row = self._mapping(event, EVENT_FIELDS, "candidate event")
                body = dict(row)
                row_digest = body.pop("decision_sha256")
                if row_digest != canonical_sha256(body):
                    raise GateViolation("event decision seal differs")
                if (
                    row["event_id"] != expected_event.event_id
                    or row["event_content_sha256"] != expected_event.event_content_sha256
                ):
                    raise GateViolation("event identity/order differs")
                if row["decision_cycle"] != baseline_row.occurrence_cycle:
                    raise GateViolation("decision edge differs from locked edge")
                state = row["predictor_state_version"]
                if isinstance(state, bool) or not isinstance(state, int) or state < 0:
                    raise GateViolation("predictor state version differs")
                if prior_state is not None and state < prior_state:
                    raise GateViolation("predictor state moved backwards")
                edge_state = state_by_edge.setdefault(row["decision_cycle"], state)
                if edge_state != state:
                    raise GateViolation("one decision edge observed multiple states")
                prior_state = state
                pose_ids = row["used_pose_ids"]
                if (
                    not isinstance(pose_ids, list)
                    or pose_ids != sorted(set(pose_ids))
                ):
                    raise GateViolation("used pose identity/order differs")
                for pose_id in pose_ids:
                    pose = poses.get(pose_id)
                    if (
                        pose is None
                        or pose.commit_cycle >= row["decision_cycle"]
                        or pose.timestamp_ns > expected_event.timestamp_ns
                        or not pose.value_valid
                        or not pose.arithmetic_valid
                    ):
                        raise GateViolation("same-edge, future, or invalid pose was used")
                if type(row["candidate_used"]) is not bool:
                    raise GateViolation("candidate_used type differs")
                if row["candidate_used"]:
                    if row["model_id"] != self.authority.candidate_id:
                        raise GateViolation("candidate row model identity differs")
                    if not pose_ids or row["fallback_reason"] is not None:
                        raise GateViolation("candidate row provenance differs")
                    self._unit_ray(row["world_ray"])
                else:
                    if row["model_id"] != "CURRENT_CAV":
                        raise GateViolation("fallback route was relabeled")
                    if row["world_ray"] is not None:
                        raise GateViolation("fallback supplied replacement geometry")
                    if type(row["fallback_reason"]) is not str or not row["fallback_reason"]:
                        raise GateViolation("fallback reason differs")

        if value != self.expected:
            raise GateViolation("output differs from pristine locked replay")
        return output["aggregate_sha256"]


__all__ = (
    "ADAPTER_SHA256",
    "CANDIDATE_NAMES",
    "ExactProductionGate",
    "GateViolation",
    "SyntheticFixture",
    "authority",
    "generate_production_output",
    "make_motion_fixture",
    "make_noncommuting_rg3_fixture",
    "reseal",
)
