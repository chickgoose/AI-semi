"""Independent native-replay and screen108-v2 gate for production candidates.

The production adapters emit candidate-specific native receipts.  This gate
first authenticates an exact native replay, then invokes the production
``screen_projection`` boundary, then validates the projected, exact-field
screen108-v2 receipt.  Common-row mutation tests operate only on that projected
receipt; candidate-specific evidence mutations remain on the native side.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Mapping, Tuple

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    dspb_output,
    pll_output,
    rg3_output,
    screen108,
    screen_projection,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
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


class GateViolation(AssertionError):
    """Base class for a classified test-gate rejection."""


class NativeReplayViolation(GateViolation):
    """A candidate without a public verifier differed from deterministic replay."""


class ProjectionViolation(GateViolation):
    """The production projection did not honor its exact public protocol."""


class ScreenContractViolation(GateViolation):
    """The projected receipt was rejected by the exact screen108-v2 validator."""


class ScreenReplayViolation(GateViolation):
    """A validly resealed screen receipt differed from the locked projection."""


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


@dataclass(frozen=True)
class VerifiedProductionOutput:
    """The distinct native and projected artifacts from one exact replay."""

    native_output: Mapping[str, object]
    native_aggregate_sha256: str
    projection: object
    screen_output: Mapping[str, object]


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


def make_fallback_taxonomy_fixture():
    """Force fresh-ZOH and candidate-failure current-CAV projection rows."""

    registry = (NeutralRegistryWindow(
        "fallback-taxonomy", 0, 50_000_000, 50_500_000
    ),)
    poses = tuple(
        _pose(index, index * 5_000_000, 0, _rotation_z(index * 0.02))
        for index in range(10)
    )
    events = (
        _event(0, 500_000, 50_000_000, 0.00, 0),
        _event(1, 5_500_000, 50_000_000, 0.01, 1),
        _event(2, 10_500_000, 50_000_000, 0.02, 2),
        _event(3, 49_000_000, 50_000_000, 0.03, 9),
        _event(4, 50_000_000, 50_000_000, 0.04, 9),
    )
    event_streams = {"fallback-taxonomy": events}
    pose_streams = {"fallback-taxonomy": poses}
    baseline = evaluate_current_cav_registry(
        registry, event_streams, pose_streams
    )
    bundle = New108AdapterBundle(
        {}, registry, event_streams, pose_streams, {},
        {"aggregate_sha256": ADAPTER_SHA256},
    )
    return SyntheticFixture(registry, event_streams, pose_streams, baseline, bundle)


def authority(candidate_name):
    if candidate_name == "RG3":
        return CandidateAuthority(
            rg3_output.RG3_OUTPUT_CANDIDATE_ID,
            rg3_output.RG3_EXECUTABLE_SHA256,
            rg3_output.RG3_CONFIG_SHA256,
        )
    if candidate_name == "DSPB":
        return CandidateAuthority(
            DSPBConfig().candidate_id,
            dspb_output.locked_dspb_executable_sha256(),
            dspb_output.locked_dspb_config_sha256(),
        )
    if candidate_name == "PLL":
        return CandidateAuthority(
            pll_output.CANDIDATE_ID,
            pll_output.generator_executable_sha256(),
            pll_output.locked_config_sha256(),
        )
    raise GateViolation("unknown candidate name")


def generate_production_output(candidate_name, fixture):
    """Invoke the selected actual candidate through its production adapter."""

    if candidate_name == "RG3":
        return rg3_output.generate_locked_rg3_output(
            fixture.registry,
            fixture.event_streams,
            fixture.pose_streams,
            ADAPTER_SHA256,
        )
    if candidate_name == "DSPB":
        return dspb_output.generate_dspb_candidate_output(
            fixture.registry,
            fixture.event_streams,
            fixture.pose_streams,
            ADAPTER_SHA256,
        )
    if candidate_name == "PLL":
        return pll_output.generate_locked_pll_output(fixture.bundle, fixture.baseline)
    raise GateViolation("unknown candidate name")


def verify_native_output(candidate_name, fixture, value):
    """Run the candidate-specific exact verifier before any projection."""

    if candidate_name == "RG3":
        # RG3 has no separate public verifier.  A fresh call through its locked
        # production adapter is therefore its deterministic exact replay.
        expected = generate_production_output("RG3", fixture)
        if value != expected:
            raise NativeReplayViolation(
                "RG3 native output differs from deterministic exact replay"
            )
        return expected["aggregate_sha256"]
    if candidate_name == "DSPB":
        return dspb_output.verify_dspb_candidate_output(
            value,
            fixture.registry,
            fixture.event_streams,
            fixture.pose_streams,
            ADAPTER_SHA256,
        )
    if candidate_name == "PLL":
        return pll_output.verify_locked_pll_output(
            value, fixture.bundle, fixture.baseline
        )
    raise GateViolation("unknown candidate name")


def _screen_output(projection):
    if type(projection) is not screen_projection.ScreenProjection:
        raise ProjectionViolation("project_native_output returned the wrong type")
    output = projection.screen_output
    if not isinstance(output, Mapping):
        raise ProjectionViolation("ScreenProjection.screen_output is not a mapping")
    return output


def project_verified_native(candidate_name, fixture, native_output):
    """Authenticate native replay, then project and validate screen108 v2."""

    native_digest = verify_native_output(candidate_name, fixture, native_output)
    projection = screen_projection.project_native_output(native_output)
    screen_output = _screen_output(projection)
    candidate_authority = authority(candidate_name)
    try:
        screen108._validate_candidate_output(
            screen_output,
            fixture.bundle,
            fixture.baseline,
            candidate_authority.executable_sha256,
            candidate_authority.config_sha256,
        )
    except screen108.Screen108Error as exc:
        raise ScreenContractViolation(
            "screen108 v2 rejected production projection: %s" % exc
        ) from exc
    return VerifiedProductionOutput(
        deepcopy(native_output),
        native_digest,
        projection,
        deepcopy(screen_output),
    )


def reseal_screen_output(value):
    """Reseal every event, window, and aggregate seal in a screen mutation."""

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


def reseal_native_envelope(value):
    """Reseal the native aggregate after a top-level evidence mutation."""

    output = deepcopy(value)
    body = dict(output)
    body.pop("aggregate_sha256", None)
    output["aggregate_sha256"] = canonical_sha256(body)
    return output


def reseal_pll_transitions(value):
    """Reseal PLL transition, window, and aggregate evidence layers."""

    output = deepcopy(value)
    for window in output["windows"]:
        for transition in window["state_transitions"]:
            body = dict(transition)
            body.pop("transition_sha256", None)
            transition["transition_sha256"] = canonical_sha256(body)
        window["state_transitions_sha256"] = canonical_sha256(
            window["state_transitions"]
        )
        body = dict(window)
        body.pop("window_sha256", None)
        window["window_sha256"] = canonical_sha256(body)
    return reseal_native_envelope(output)


class ExactProductionGate:
    """Require exact native replay followed by exact screen108-v2 projection."""

    def __init__(self, candidate_name, fixture, pristine_native):
        self.candidate_name = candidate_name
        self.fixture = fixture
        self.expected = project_verified_native(
            candidate_name, fixture, pristine_native
        )

    def validate_native(self, value):
        """Verify native evidence before projection and exact screen replay."""

        verified = project_verified_native(self.candidate_name, self.fixture, value)
        if verified.native_output != self.expected.native_output:
            raise NativeReplayViolation("native output differs from pristine replay")
        if verified.screen_output != self.expected.screen_output:
            raise ScreenReplayViolation("projection differs from pristine locked replay")
        return verified

    def validate_screen(self, value):
        """Validate a separately supplied, fully resealed projected mutation."""

        candidate_authority = authority(self.candidate_name)
        try:
            digest, _events = screen108._validate_candidate_output(
                value,
                self.fixture.bundle,
                self.fixture.baseline,
                candidate_authority.executable_sha256,
                candidate_authority.config_sha256,
            )
        except screen108.Screen108Error as exc:
            raise ScreenContractViolation(
                "screen108 v2 rejected projected mutation: %s" % exc
            ) from exc
        if value != self.expected.screen_output:
            raise ScreenReplayViolation(
                "screen output differs from pristine locked projection"
            )
        return digest


__all__ = (
    "ADAPTER_SHA256",
    "CANDIDATE_NAMES",
    "ExactProductionGate",
    "GateViolation",
    "NativeReplayViolation",
    "ProjectionViolation",
    "ScreenContractViolation",
    "ScreenReplayViolation",
    "SyntheticFixture",
    "VerifiedProductionOutput",
    "authority",
    "generate_production_output",
    "make_fallback_taxonomy_fixture",
    "make_motion_fixture",
    "make_noncommuting_rg3_fixture",
    "project_verified_native",
    "reseal_native_envelope",
    "reseal_pll_transitions",
    "reseal_screen_output",
    "verify_native_output",
)
