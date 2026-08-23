"""Single-attempt, source-bound Stage-3 campaign orchestration.

The campaign executes one native output adapter twice over the same immutable,
label-free view.  It persists the first rich native receipt, requires the
verifier replay to serialize byte-for-byte identically, and only then invokes
the candidate-neutral screen projection.  Selector labels remain reachable
only by ``screen108`` after every candidate and projection artifact is sealed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_predictor_stage3.logical_cav_evaluator import (
    CAVRegistryEvaluation,
    evaluate_current_cav_registry_bounded,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterBundle,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.stage3_new108_adapter import (
    build_locked_stage3_new108_adapter,
    verify_stage3_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256

from . import candidate_authority, dspb_output, pll_output, rg3_output, screen108

try:  # The projector is integrated independently of this campaign worker.
    from . import screen_projection as _screen_projection
except ImportError:  # pragma: no cover - exercised through the fail-closed API.
    _screen_projection = None


CAMPAIGN_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_receipt/v5"
ATTEMPT_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_attempt/v5"
FAILURE_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_failure/v5"
GENERATOR_EVIDENCE_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.campaign108_adapter_dispatch/v5"
)
REPLAY_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_replay/v3"

RG3_ID = candidate_authority.candidate_native_id("RG3")
DSPB_ID = candidate_authority.candidate_native_id("DSPB")
SO3_PLL_ID = candidate_authority.candidate_native_id("PLL")
FROZEN_CANDIDATE_IDS = (RG3_ID, DSPB_ID, SO3_PLL_ID)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_FAILURE_STAGES = frozenset((
    "CAMPAIGN_AUTHORITY_SEAL",
    "INPUT_BUILD",
    "BASELINE_BUILD",
    "NEUTRAL_BINDING",
    "PRODUCTION_ADAPTER",
    "NATIVE_OUTPUT_VALIDATE",
    "NATIVE_OUTPUT_SEAL",
    "PRE_REPLAY_INTEGRITY",
    "VERIFICATION_REPLAY",
    "REPLAY_SEAL",
    "POST_REPLAY_INTEGRITY",
    "PROJECTION",
    "PROJECTION_SEAL",
    "GENERATOR_EVIDENCE_SEAL",
    "PRE_SCREEN_INTEGRITY",
    "SCREEN",
    "SCREEN_RESULT_VALIDATE",
    "SCREEN_RESULT_SEAL",
    "FINAL_INTEGRITY",
    "CAMPAIGN_RECEIPT_SEAL",
))
_PRE_SCORE_INFRASTRUCTURE_STAGES = frozenset((
    "CAMPAIGN_AUTHORITY_SEAL",
    "INPUT_BUILD",
    "BASELINE_BUILD",
    "NEUTRAL_BINDING",
))


class Campaign108Error(ValueError):
    """A source authority, artifact, replay, or projection invariant failed."""


@dataclass
class _CampaignProgress:
    failure_stage: str = "CAMPAIGN_AUTHORITY_SEAL"
    attempt_sealed: bool = False
    native_output_sealed: bool = False
    screen_started: bool = False
    score_computed: bool = False
    labels_accessed: bool = False
    attempt: Optional[Mapping[str, object]] = None
    paths: Optional[Mapping[str, Path]] = None


@dataclass(frozen=True)
class NeutralAdapterView:
    """The only NEW108 projection visible to a native adapter."""

    neutral_registry: Tuple[object, ...]
    event_streams: Mapping[str, Tuple[object, ...]]
    pose_streams: Mapping[str, Tuple[object, ...]]
    provenance_seal: Mapping[str, str]


@dataclass(frozen=True)
class NeutralCycleView:
    records: Tuple[object, ...]


@dataclass(frozen=True)
class NeutralBaselineWindow:
    registry: object
    input_events: Tuple[object, ...]
    input_poses: Tuple[object, ...]
    simulation: NeutralCycleView


@dataclass(frozen=True)
class NeutralBaselineView:
    windows: Tuple[NeutralBaselineWindow, ...]
    neutral_input_sha256: str


Adapter = Callable[[NeutralAdapterView, NeutralBaselineView], Mapping[str, object]]


@dataclass(frozen=True)
class FrozenCandidate:
    authority_name: str
    artifact_stem: str
    candidate_id: str
    native_schema: str
    adapter: Adapter
    config_bytes: bytes
    executable_sha256: Callable[[], str]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_bytes(path: Path, where: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Campaign108Error("cannot read %s" % where) from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise Campaign108Error("%s must be lowercase SHA-256" % where)
    return value


def _read_json_bytes(payload: bytes, where: str) -> Mapping[str, object]:
    def reject_constant(value: str) -> None:
        raise Campaign108Error("%s contains a non-finite JSON value" % where)

    def reject_duplicates(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
        result = {}  # type: Dict[str, object]
        for key, value in pairs:
            if key in result:
                raise Campaign108Error("%s contains a duplicate JSON key" % where)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Campaign108Error("cannot parse %s" % where) from exc
    if not isinstance(value, Mapping):
        raise Campaign108Error("%s must be a JSON object" % where)
    return value


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Campaign108Error("campaign artifact is not finite JSON") from exc


def _exclusive_write(path: Path, payload: bytes, where: str) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(path), flags, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise Campaign108Error("cannot append-only create %s" % where) from exc


def _neutral_view(bundle: New108AdapterBundle) -> NeutralAdapterView:
    if type(bundle) is not New108AdapterBundle:
        raise Campaign108Error("locked adapter returned the wrong bundle type")
    registries = tuple(bundle.neutral_registry)
    identifiers = tuple(row.window_id for row in registries)
    if (
        not registries
        or len(set(identifiers)) != len(identifiers)
        or set(bundle.event_streams) != set(identifiers)
        or set(bundle.pose_streams) != set(identifiers)
    ):
        raise Campaign108Error("neutral adapter projection identities differ")
    aggregate = bundle.provenance_seal.get("aggregate_sha256")
    _sha256(aggregate, "adapter aggregate digest")
    return NeutralAdapterView(
        registries,
        MappingProxyType({
            identifier: tuple(bundle.event_streams[identifier])
            for identifier in identifiers
        }),
        MappingProxyType({
            identifier: tuple(bundle.pose_streams[identifier])
            for identifier in identifiers
        }),
        MappingProxyType({"aggregate_sha256": str(aggregate)}),
    )


def _build_verified_stage3_adapter(dataset_directory: Path) -> New108AdapterBundle:
    """Build and authenticate the locked 50 ms Stage3 cohort fail-closed."""

    if not callable(build_locked_stage3_new108_adapter) or not callable(
        verify_stage3_new108_adapter
    ):
        raise Campaign108Error("locked Stage3 NEW108 adapter API is unavailable")
    root = Path(dataset_directory)
    bundle = build_locked_stage3_new108_adapter(root)
    if type(bundle) is not New108AdapterBundle:
        raise Campaign108Error("locked Stage3 NEW108 adapter returned wrong type")
    adapter_digest = verify_stage3_new108_adapter(bundle, root)
    if adapter_digest != bundle.provenance_seal.get("aggregate_sha256"):
        raise Campaign108Error("locked Stage3 NEW108 adapter authority differs")
    if any(
        row.query_start_ns_inclusive - row.warmup_start_ns_inclusive
        != screen108.PREROLL_NS
        for row in bundle.neutral_registry
    ):
        raise Campaign108Error("Stage3 NEW108 cohort does not retain 50 ms pre-roll")
    return bundle


def _neutral_projection_sha256(view: NeutralAdapterView) -> str:
    return canonical_sha256({
        "schema": "redred.mc_wtb.current_cav_neutral_inputs/v1",
        "registry": [row.to_mapping() for row in view.neutral_registry],
        "windows": [
            {
                "window_id": row.window_id,
                "events": [
                    event.to_content_mapping()
                    for event in view.event_streams[row.window_id]
                ],
                "poses": [
                    pose.to_content_mapping()
                    for pose in view.pose_streams[row.window_id]
                ],
            }
            for row in view.neutral_registry
        ],
    })


def _neutral_baseline_view(
    baseline: CAVRegistryEvaluation, neutral: NeutralAdapterView
) -> NeutralBaselineView:
    try:
        windows = tuple(baseline.windows)
        digest = baseline.neutral_input_sha256
    except AttributeError as exc:
        raise Campaign108Error("baseline neutral authority is missing") from exc
    if len(windows) != len(neutral.neutral_registry):
        raise Campaign108Error("baseline neutral window cardinality differs")
    projected = []
    for row, registry in zip(windows, neutral.neutral_registry):
        identifier = registry.window_id
        if (
            row.registry.to_mapping() != registry.to_mapping()
            or tuple(event.to_content_mapping() for event in row.input_events)
            != tuple(
                event.to_content_mapping()
                for event in neutral.event_streams[identifier]
            )
            or tuple(pose.to_content_mapping() for pose in row.input_poses)
            != tuple(
                pose.to_content_mapping()
                for pose in neutral.pose_streams[identifier]
            )
        ):
            raise Campaign108Error("baseline neutral window binding differs")
        projected.append(NeutralBaselineWindow(
            registry,
            neutral.event_streams[identifier],
            neutral.pose_streams[identifier],
            NeutralCycleView(tuple(row.simulation.records)),
        ))
    return NeutralBaselineView(tuple(projected), _sha256(digest, "neutral input digest"))


def _compact_neutral_baseline_view(
    neutral: NeutralAdapterView,
) -> NeutralBaselineView:
    """Evaluate and compact one frozen baseline window at a time."""

    compact = evaluate_current_cav_registry_bounded(
        neutral.neutral_registry,
        neutral.event_streams,
        neutral.pose_streams,
    )
    try:
        return _neutral_baseline_view(compact, neutral)
    finally:
        del compact
        gc.collect()


def _dispatch_rg3(
    bundle: NeutralAdapterView, baseline: NeutralBaselineView
) -> Mapping[str, object]:
    del baseline
    return rg3_output.generate_locked_rg3_output(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        str(bundle.provenance_seal["aggregate_sha256"]),
    )


def _dispatch_dspb(
    bundle: NeutralAdapterView, baseline: NeutralBaselineView
) -> Mapping[str, object]:
    del baseline
    return dspb_output.generate_dspb_candidate_output(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        str(bundle.provenance_seal["aggregate_sha256"]),
    )


def _dispatch_pll(
    bundle: NeutralAdapterView, baseline: NeutralBaselineView
) -> Mapping[str, object]:
    return pll_output.generate_locked_pll_output(bundle, baseline)


def _candidate_registry() -> Mapping[str, FrozenCandidate]:
    rows = (
        FrozenCandidate(
            "RG3", "rg3", RG3_ID, rg3_output.CANDIDATE_OUTPUT_SCHEMA,
            _dispatch_rg3, bytes(rg3_output.RG3_CONFIG_BYTES),
            lambda: rg3_output.RG3_EXECUTABLE_SHA256,
        ),
        FrozenCandidate(
            "DSPB", "dspb", DSPB_ID, dspb_output.CANDIDATE_OUTPUT_SCHEMA,
            _dispatch_dspb, bytes(dspb_output.locked_dspb_config_bytes()),
            dspb_output.locked_dspb_executable_sha256,
        ),
        FrozenCandidate(
            "PLL", "pll", SO3_PLL_ID, pll_output.CANDIDATE_OUTPUT_SCHEMA,
            _dispatch_pll, bytes(pll_output.locked_config_bytes()),
            pll_output.generator_executable_sha256,
        ),
    )
    if tuple(row.candidate_id for row in rows) != FROZEN_CANDIDATE_IDS:
        raise Campaign108Error("native candidate IDs differ from authority")
    return MappingProxyType({row.candidate_id: row for row in rows})


_CANDIDATES = _candidate_registry()


def _candidate(candidate_id: object) -> FrozenCandidate:
    if type(candidate_id) is not str or candidate_id not in _CANDIDATES:
        raise Campaign108Error("candidate ID is not in the frozen Stage3 registry")
    return _CANDIDATES[candidate_id]


def frozen_candidate_config_bytes(candidate_id: str) -> bytes:
    """Return the adapter config expected from the candidate-neutral projector."""

    return bytes(_candidate(candidate_id).config_bytes)


def frozen_candidate_config(candidate_id: str) -> Mapping[str, object]:
    return _read_json_bytes(
        frozen_candidate_config_bytes(candidate_id), "locked candidate config"
    )


def _campaign_authority(
    spec: FrozenCandidate,
) -> Tuple[Mapping[str, object], Mapping[str, object], Tuple[Tuple[Path, bytes], ...]]:
    try:
        campaign = candidate_authority.build_campaign_authority(_repo_root())
        candidate_authority.verify_campaign_authority(campaign, _repo_root())
    except candidate_authority.CandidateAuthorityError as exc:
        raise Campaign108Error("candidate source authority differs") from exc
    manifests = campaign.get("candidates")
    if not isinstance(manifests, list):
        raise Campaign108Error("campaign authority candidate schema differs")
    selected = next(
        (
            row for row in manifests
            if isinstance(row, Mapping) and row.get("candidate") == spec.authority_name
        ),
        None,
    )
    if not isinstance(selected, Mapping):
        raise Campaign108Error("candidate authority is missing")
    if selected.get("native_candidate_id") != spec.candidate_id:
        raise Campaign108Error("candidate authority native ID differs")
    try:
        candidate_authority.verify_candidate_authority(selected, _repo_root())
    except candidate_authority.CandidateAuthorityError as exc:
        raise Campaign108Error("candidate source authority differs") from exc
    snapshots = []
    dependencies = selected.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise Campaign108Error("candidate source closure is empty")
    for row in dependencies:
        if not isinstance(row, Mapping):
            raise Campaign108Error("candidate source closure schema differs")
        relative = row.get("path")
        if type(relative) is not str:
            raise Campaign108Error("candidate source path differs")
        path = _repo_root() / relative
        payload = _read_bytes(path, "candidate source")
        if _sha256_bytes(payload) != row.get("sha256"):
            raise Campaign108Error("candidate source digest differs")
        snapshots.append((path, payload))
    return campaign, selected, tuple(snapshots)


def _check_authority_unchanged(
    campaign: Mapping[str, object], snapshots: Sequence[Tuple[Path, bytes]]
) -> None:
    for path, payload in snapshots:
        if _read_bytes(path, "candidate source") != payload:
            raise Campaign108Error("candidate source changed during the single attempt")
    try:
        candidate_authority.verify_campaign_authority(campaign, _repo_root())
    except candidate_authority.CandidateAuthorityError as exc:
        raise Campaign108Error("candidate source authority changed") from exc


def _validate_native_output(
    value: object,
    spec: FrozenCandidate,
    neutral: NeutralAdapterView,
    baseline: NeutralBaselineView,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise Campaign108Error("native adapter output must be an object")
    output = dict(value)
    required = {
        "schema", "candidate_id", "adapter_aggregate_sha256",
        "neutral_input_sha256", "candidate_executable_sha256",
        "candidate_config_sha256", "windows", "aggregate_sha256",
    }
    if not required.issubset(output):
        raise Campaign108Error("native adapter output field schema differs")
    unsigned = dict(output)
    supplied = unsigned.pop("aggregate_sha256", None)
    if (
        output.get("schema") != spec.native_schema
        or output.get("candidate_id") != spec.candidate_id
        or output.get("adapter_aggregate_sha256")
        != neutral.provenance_seal.get("aggregate_sha256")
        or output.get("neutral_input_sha256") != baseline.neutral_input_sha256
        or output.get("candidate_executable_sha256") != spec.executable_sha256()
        or output.get("candidate_config_sha256")
        != _sha256_bytes(spec.config_bytes)
        or supplied != canonical_sha256(unsigned)
    ):
        raise Campaign108Error("native adapter seal or frozen binding differs")
    windows = output.get("windows")
    expected_ids = [row.window_id for row in neutral.neutral_registry]
    if not isinstance(windows, list) or [
        row.get("window_id") if isinstance(row, Mapping) else None for row in windows
    ] != expected_ids:
        raise Campaign108Error("native adapter changed window IDs")
    return output


def _project_native_output(native_output: Mapping[str, object]) -> object:
    if _screen_projection is None:
        raise Campaign108Error("screen projection implementation is unavailable")
    try:
        return _screen_projection.project_native_output(native_output)
    except Exception as exc:
        raise Campaign108Error("screen projection failed") from exc


def _projection_parts(value: object) -> Tuple[Mapping[str, object], Mapping[str, object], bytes, bytes]:
    try:
        screen_output = value.screen_output  # type: ignore[attr-defined]
        receipt = value.projection_receipt  # type: ignore[attr-defined]
        executable = value.executable_artifact_bytes  # type: ignore[attr-defined]
        config = value.config_bytes  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise Campaign108Error("screen projection result schema differs") from exc
    if not isinstance(screen_output, Mapping) or not isinstance(receipt, Mapping):
        raise Campaign108Error("screen projection mappings differ")
    if type(executable) is not bytes or not executable:
        raise Campaign108Error("screen executable artifact bytes differ")
    if type(config) is not bytes or not config:
        raise Campaign108Error("screen config bytes differ")
    return dict(screen_output), dict(receipt), executable, config


def _expected_screen_event(
    native_event: Mapping[str, object], candidate_id: str
) -> Mapping[str, object]:
    route_map = {
        "CANDIDATE": "candidate",
        "CURRENT_CAV": "current_cav",
        "FRESH_ZOH": "fresh_zoh",
        "SENSOR_FIXED": "sensor_fixed",
    }
    route = route_map.get(native_event.get("route"))
    if route is None:
        raise Campaign108Error("native route cannot be projected")
    used = native_event.get("candidate_used")
    if type(used) is not bool:
        raise Campaign108Error("native candidate-use evidence differs")
    fallback_reason = native_event.get("fallback_reason")
    if not used and route == "current_cav":
        fallback_reason = "candidate_failure"
    return {
        "event_id": native_event.get("event_id"),
        "event_content_sha256": native_event.get("event_content_sha256"),
        "occurrence_cycle": native_event.get("occurrence_cycle"),
        "decision_cycle": native_event.get("decision_cycle"),
        "model_id": candidate_id if used else "CURRENT_CAV",
        "predictor_state_version": native_event.get("predictor_state_version"),
        "used_pose_ids": native_event.get("used_pose_ids"),
        "route": route,
        "candidate_attempted": native_event.get("candidate_attempted"),
        "candidate_used": used,
        "fallback_reason": fallback_reason,
        "world_ray": native_event.get("world_ray") if used else None,
    }


def _validate_projection(
    native_output: Mapping[str, object],
    projection: object,
    spec: FrozenCandidate,
) -> Tuple[Mapping[str, object], Mapping[str, object], bytes, bytes]:
    screen_output, receipt, executable, config = _projection_parts(projection)
    required = {
        "schema", "candidate_id", "adapter_aggregate_sha256",
        "neutral_input_sha256", "candidate_executable_sha256",
        "candidate_config_sha256", "windows", "aggregate_sha256",
    }
    if set(screen_output) != required:
        raise Campaign108Error("projected screen output field schema differs")
    unsigned = dict(screen_output)
    supplied = unsigned.pop("aggregate_sha256", None)
    native_windows = native_output["windows"]
    native_ids = [
        row.get("window_id") if isinstance(row, Mapping) else None
        for row in native_windows  # type: ignore[union-attr]
    ]
    screen_windows = screen_output.get("windows")
    screen_ids = [
        row.get("window_id") if isinstance(row, Mapping) else None
        for row in screen_windows
    ] if isinstance(screen_windows, list) else None
    if (
        screen_output.get("schema") != screen108.CANDIDATE_OUTPUT_SCHEMA
        or screen_output.get("candidate_id") != spec.candidate_id
        or screen_output.get("adapter_aggregate_sha256")
        != native_output.get("adapter_aggregate_sha256")
        or screen_output.get("neutral_input_sha256")
        != native_output.get("neutral_input_sha256")
        or screen_output.get("candidate_executable_sha256")
        != _sha256_bytes(executable)
        or screen_output.get("candidate_config_sha256") != _sha256_bytes(config)
        or supplied != canonical_sha256(unsigned)
        or screen_ids != native_ids
    ):
        raise Campaign108Error("screen projection binding differs")

    receipt_windows = receipt.get("windows")
    if not isinstance(receipt_windows, list) or len(receipt_windows) != len(native_ids):
        raise Campaign108Error("screen projection receipt windows differ")
    for native_window, projected_window, receipt_window in zip(
        native_windows, screen_windows, receipt_windows  # type: ignore[arg-type]
    ):
        if (
            not isinstance(native_window, Mapping)
            or not isinstance(projected_window, Mapping)
            or not isinstance(receipt_window, Mapping)
            or set(projected_window) != {"window_id", "events", "events_sha256"}
        ):
            raise Campaign108Error("screen projection window schema differs")
        native_events = native_window.get("events")
        projected_events = projected_window.get("events")
        if (
            not isinstance(native_events, list)
            or not isinstance(projected_events, list)
            or len(native_events) != len(projected_events)
            or projected_window.get("events_sha256")
            != canonical_sha256(projected_events)
        ):
            raise Campaign108Error("screen projection event population differs")
        event_bindings = []
        for native_event, projected_event in zip(native_events, projected_events):
            if not isinstance(native_event, Mapping) or not isinstance(projected_event, Mapping):
                raise Campaign108Error("screen projection event schema differs")
            projected_body = dict(projected_event)
            projected_digest = projected_body.pop("decision_sha256", None)
            if (
                projected_body != _expected_screen_event(native_event, spec.candidate_id)
                or projected_digest != canonical_sha256(projected_body)
            ):
                raise Campaign108Error("screen projection decision substitution differs")
            event_bindings.append({
                "event_id": native_event.get("event_id"),
                "source_decision_sha256": native_event.get("decision_sha256"),
                "projected_decision_sha256": projected_digest,
            })
        source_window_sha = native_window.get("window_sha256")
        if source_window_sha is None:
            source_window_sha = canonical_sha256(native_window)
        receipt_window_body = {
            "window_id": native_window.get("window_id"),
            "source_window_sha256": source_window_sha,
            "source_events_sha256": native_window.get("events_sha256"),
            "projected_events_sha256": projected_window.get("events_sha256"),
            "event_bindings": event_bindings,
            "event_bindings_sha256": canonical_sha256(event_bindings),
        }
        expected_receipt_window = dict(
            receipt_window_body,
            window_projection_sha256=canonical_sha256(receipt_window_body),
        )
        if dict(receipt_window) != expected_receipt_window:
            raise Campaign108Error("screen projection receipt window differs")
    receipt_fields = {
        "schema", "candidate_id", "native_schema", "native_aggregate_sha256",
        "projected_aggregate_sha256", "candidate_executable_sha256",
        "candidate_config_sha256", "windows", "projection_receipt_sha256",
    }
    receipt_unsigned = dict(receipt)
    receipt_digest = receipt_unsigned.pop("projection_receipt_sha256", None)
    if (
        set(receipt) != receipt_fields
        or type(receipt.get("schema")) is not str
        or receipt.get("candidate_id") != spec.candidate_id
        or receipt.get("native_schema") != native_output.get("schema")
        or receipt.get("native_aggregate_sha256")
        != native_output.get("aggregate_sha256")
        or receipt.get("projected_aggregate_sha256") != supplied
        or receipt.get("candidate_executable_sha256") != _sha256_bytes(executable)
        or receipt.get("candidate_config_sha256") != _sha256_bytes(config)
        or receipt_digest != canonical_sha256(receipt_unsigned)
    ):
        raise Campaign108Error("screen projection receipt seal differs")
    return screen_output, receipt, executable, config


def _artifact(path: Path, payload: bytes, semantic_sha256: str) -> Mapping[str, object]:
    return {
        "path": path.name,
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "semantic_sha256": _sha256(semantic_sha256, "artifact semantic digest"),
    }


def _raw_artifact(path: Path, payload: bytes) -> Mapping[str, object]:
    return {
        "path": path.name,
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }


def _check_unchanged(path: Path, expected: bytes, where: str) -> None:
    if _read_bytes(path, where) != expected:
        raise Campaign108Error("%s changed during the single attempt" % where)


def _artifact_paths(campaign_directory: Path, candidate_id: str) -> Mapping[str, Path]:
    stem = _candidate(candidate_id).artifact_stem
    return {
        "attempt": campaign_directory / (stem + ".attempt.json"),
        "campaign_authority": campaign_directory / (stem + ".campaign-authority.json"),
        "native_output": campaign_directory / (stem + ".native-output.json"),
        "projection_receipt": campaign_directory / (stem + ".projection-receipt.json"),
        "screen_output": campaign_directory / (stem + ".screen-output.json"),
        "executable_artifact": campaign_directory / (stem + ".executable-authority"),
        "generator_evidence": campaign_directory / (stem + ".generator-evidence.json"),
        "replay": campaign_directory / (stem + ".replay.json"),
        "screen_result": campaign_directory / (stem + ".screen108-result.json"),
        "campaign_receipt": campaign_directory / (stem + ".campaign-receipt.json"),
        "failure_receipt": campaign_directory / (stem + ".failure-receipt.json"),
    }


def _campaign_epoch(value: object) -> int:
    if type(value) is not int or value < 1:
        raise Campaign108Error("campaign epoch must be a positive integer")
    return value


def _validate_predecessor_bindings(
    value: object,
    aggregate: object,
    campaign_epoch: int,
    candidate_id: str,
) -> Tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise Campaign108Error("failure predecessor bindings differ")
    _sha256(aggregate, "predecessor failure aggregate")
    if canonical_sha256(value) != aggregate:
        raise Campaign108Error("failure predecessor aggregate differs")
    rows = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {
            "candidate_id", "campaign_epoch", "attempt_sha256",
            "failure_receipt_sha256", "artifact_sha256",
        }:
            raise Campaign108Error("failure predecessor binding schema differs")
        if (
            type(row.get("candidate_id")) is not str
            or row.get("campaign_epoch") != campaign_epoch - 1
        ):
            raise Campaign108Error("failure predecessor epoch or candidate differs")
        for field in (
            "attempt_sha256", "failure_receipt_sha256", "artifact_sha256"
        ):
            _sha256(row.get(field), "failure predecessor %s" % field)
        rows.append(dict(row))
    expected = sorted(
        rows,
        key=lambda row: (str(row["candidate_id"]), str(row["failure_receipt_sha256"])),
    )
    if rows != expected or len({row["candidate_id"] for row in rows}) != len(rows):
        raise Campaign108Error("failure predecessor order or uniqueness differs")
    if campaign_epoch == 1:
        if rows:
            raise Campaign108Error("campaign epoch 1 cannot have predecessors")
    elif not rows or candidate_id not in {row["candidate_id"] for row in rows}:
        raise Campaign108Error("candidate predecessor failure is missing")
    return tuple(rows)


def verify_campaign108_failure_receipt(
    receipt: Mapping[str, object],
    campaign_directory: Optional[Path] = None,
) -> str:
    """Verify a sealed failed attempt and, when supplied, its artifact bytes."""

    required = {
        "schema", "status", "candidate_id", "authority_name",
        "campaign_epoch", "attempt_index", "attempt_sha256",
        "predecessor_failures", "predecessor_failures_sha256",
        "failure_stage", "exception_type", "exception_message",
        "exception_message_sha256", "native_output_sealed", "screen_started",
        "score_computed", "labels_accessed", "retry_allowed",
        "tuning_allowed", "artifacts", "failure_receipt_sha256",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        raise Campaign108Error("failure receipt field schema differs")
    if (
        receipt.get("schema") != FAILURE_SCHEMA
        or receipt.get("status") != "CAMPAIGN_SINGLE_ATTEMPT_FAILED"
        or type(receipt.get("candidate_id")) is not str
        or type(receipt.get("authority_name")) is not str
        or _campaign_epoch(receipt.get("campaign_epoch")) < 1
        or receipt.get("attempt_index") != 1
        or receipt.get("failure_stage") not in _FAILURE_STAGES
        or type(receipt.get("exception_type")) is not str
        or not receipt.get("exception_type")
        or type(receipt.get("exception_message")) is not str
        or _sha256_bytes(
            str(receipt.get("exception_message")).encode("utf-8")
        ) != receipt.get("exception_message_sha256")
        or any(
            type(receipt.get(field)) is not bool
            for field in (
                "native_output_sealed", "screen_started", "score_computed",
                "labels_accessed", "retry_allowed", "tuning_allowed",
            )
        )
        or receipt.get("retry_allowed") is not False
        or receipt.get("tuning_allowed") is not False
        or (receipt.get("score_computed") is True and receipt.get("screen_started") is not True)
        or (receipt.get("labels_accessed") is True and receipt.get("screen_started") is not True)
    ):
        raise Campaign108Error("failure receipt policy boundary differs")
    spec = _candidate(receipt.get("candidate_id"))
    if receipt.get("authority_name") != spec.authority_name:
        raise Campaign108Error("failure receipt candidate authority differs")
    for field in (
        "attempt_sha256", "predecessor_failures_sha256",
        "exception_message_sha256", "failure_receipt_sha256",
    ):
        _sha256(receipt.get(field), "failure receipt %s" % field)
    _validate_predecessor_bindings(
        receipt.get("predecessor_failures"),
        receipt.get("predecessor_failures_sha256"),
        int(receipt["campaign_epoch"]),
        str(receipt["candidate_id"]),
    )
    unsigned = dict(receipt)
    supplied = unsigned.pop("failure_receipt_sha256", None)
    if supplied != canonical_sha256(unsigned):
        raise Campaign108Error("failure receipt aggregate seal differs")

    artifacts = receipt.get("artifacts")
    allowed_artifacts = {
        "attempt", "campaign_authority", "native_output", "projection_receipt",
        "screen_output", "executable_artifact", "generator_evidence", "replay",
        "screen_result",
    }
    if (
        not isinstance(artifacts, Mapping)
        or not set(artifacts).issubset(allowed_artifacts)
    ):
        raise Campaign108Error("failure artifact index differs")
    if "attempt" not in artifacts:
        raise Campaign108Error("failure attempt artifact is missing")
    for name, identity in artifacts.items():
        if not isinstance(identity, Mapping) or set(identity) != {
            "path", "size_bytes", "sha256"
        }:
            raise Campaign108Error("failure artifact identity differs")
        if (
            type(identity.get("path")) is not str
            or Path(str(identity["path"])).name != identity["path"]
        ):
            raise Campaign108Error("failure artifact path differs")
        if type(identity.get("size_bytes")) is not int or identity["size_bytes"] < 0:
            raise Campaign108Error("failure artifact size differs")
        _sha256(identity.get("sha256"), "failure artifact digest")
        if campaign_directory is not None:
            path = Path(campaign_directory) / str(identity["path"])
            if path.is_symlink() or not path.is_file():
                raise Campaign108Error("failure artifact is missing or aliased")
            payload = _read_bytes(path, "%s failure artifact" % name)
            if (
                len(payload) != identity["size_bytes"]
                or _sha256_bytes(payload) != identity["sha256"]
            ):
                raise Campaign108Error("failure artifact bytes differ")
    if (
        receipt.get("native_output_sealed") is True
        and "native_output" not in artifacts
    ):
        raise Campaign108Error("failure native-output state differs")
    if campaign_directory is not None:
        attempt_identity = artifacts["attempt"]
        attempt_path = Path(campaign_directory) / str(attempt_identity["path"])
        attempt = _read_json_bytes(
            _read_bytes(attempt_path, "failed attempt marker"),
            "failed attempt marker",
        )
        attempt_unsigned = dict(attempt)
        attempt_digest = attempt_unsigned.pop("attempt_sha256", None)
        if (
            set(attempt) != {
                "schema", "candidate_id", "authority_name", "campaign_epoch",
                "attempt_index", "predecessor_failures",
                "predecessor_failures_sha256", "campaign_authority_sha256",
                "candidate_authority_sha256", "authority_config_sha256",
                "caller_config_sha256", "caller_config_semantic_sha256",
                "cncp_sha256", "cncp_semantic_sha256",
                "campaign_runner_sha256", "adapter_execution_count",
                "verification_replay_count", "verification_replay_is_tuning",
                "retry_allowed", "tuning_allowed", "attempt_sha256",
            }
            or attempt_digest != canonical_sha256(attempt_unsigned)
            or attempt_digest != receipt.get("attempt_sha256")
            or attempt.get("schema") != ATTEMPT_SCHEMA
            or attempt.get("candidate_id") != spec.candidate_id
            or attempt.get("authority_name") != spec.authority_name
            or attempt.get("campaign_epoch") != receipt.get("campaign_epoch")
            or attempt.get("attempt_index") != 1
            or attempt.get("predecessor_failures")
            != receipt.get("predecessor_failures")
            or attempt.get("predecessor_failures_sha256")
            != receipt.get("predecessor_failures_sha256")
            or attempt.get("adapter_execution_count") != 2
            or attempt.get("verification_replay_count") != 1
            or attempt.get("verification_replay_is_tuning") is not False
            or attempt.get("retry_allowed") is not False
            or attempt.get("tuning_allowed") is not False
        ):
            raise Campaign108Error("failed attempt marker seal differs")
        if "campaign_authority" in artifacts:
            authority_identity = artifacts["campaign_authority"]
            authority_path = Path(campaign_directory) / str(
                authority_identity["path"]
            )
            authority = _read_json_bytes(
                _read_bytes(authority_path, "failed campaign authority"),
                "failed campaign authority",
            )
            authority_unsigned = dict(authority)
            authority_digest = authority_unsigned.pop("aggregate_sha256", None)
            if (
                authority_digest != canonical_sha256(authority_unsigned)
                or authority_digest != attempt.get("campaign_authority_sha256")
            ):
                raise Campaign108Error("failed campaign authority seal differs")
    return str(supplied)


def _predecessor_failure_bindings(
    candidate_id: str,
    campaign_epoch: int,
    receipt_paths: Sequence[Path],
) -> Tuple[Tuple[Mapping[str, object], ...], str]:
    epoch = _campaign_epoch(campaign_epoch)
    paths = tuple(Path(path) for path in receipt_paths)
    if epoch == 1:
        if paths:
            raise Campaign108Error("campaign epoch 1 cannot have predecessors")
        return (), canonical_sha256([])
    if not paths:
        raise Campaign108Error("later campaign epoch requires predecessor failures")

    resolved = []
    bindings = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise Campaign108Error("predecessor failure receipt is missing or aliased")
        identity = path.resolve()
        if identity in resolved:
            raise Campaign108Error("duplicate predecessor failure receipt")
        resolved.append(identity)
        payload = _read_bytes(path, "predecessor failure receipt")
        receipt = _read_json_bytes(payload, "predecessor failure receipt")
        semantic = verify_campaign108_failure_receipt(receipt, path.parent)
        if (
            receipt.get("campaign_epoch") != epoch - 1
            or receipt.get("failure_stage") not in _PRE_SCORE_INFRASTRUCTURE_STAGES
            or receipt.get("native_output_sealed") is not False
            or receipt.get("screen_started") is not False
            or receipt.get("score_computed") is not False
            or receipt.get("labels_accessed") is not False
            or any(
                name in receipt.get("artifacts", {})
                for name in (
                    "native_output", "replay", "projection_receipt",
                    "screen_output", "executable_artifact",
                    "generator_evidence", "screen_result",
                )
            )
        ):
            raise Campaign108Error(
                "predecessor is not an eligible pre-score infrastructure failure"
            )
        bindings.append({
            "candidate_id": receipt["candidate_id"],
            "campaign_epoch": receipt["campaign_epoch"],
            "attempt_sha256": receipt["attempt_sha256"],
            "failure_receipt_sha256": semantic,
            "artifact_sha256": _sha256_bytes(payload),
        })
    if len({row["candidate_id"] for row in bindings}) != len(bindings):
        raise Campaign108Error("duplicate predecessor candidate failure")
    if candidate_id not in {row["candidate_id"] for row in bindings}:
        raise Campaign108Error("candidate predecessor failure is missing")
    ordered = tuple(sorted(
        bindings,
        key=lambda row: (str(row["candidate_id"]), str(row["failure_receipt_sha256"])),
    ))
    return ordered, canonical_sha256(list(ordered))


def _seal_failure_receipt(
    spec: FrozenCandidate,
    campaign_epoch: int,
    predecessors: Sequence[Mapping[str, object]],
    predecessor_digest: str,
    progress: _CampaignProgress,
    error: Exception,
) -> None:
    if not progress.attempt_sealed or progress.attempt is None or progress.paths is None:
        return
    paths = progress.paths
    artifacts = {}
    for name in (
        "attempt", "campaign_authority", "native_output", "projection_receipt",
        "screen_output", "executable_artifact", "generator_evidence", "replay",
        "screen_result",
    ):
        path = paths[name]
        if path.is_symlink():
            raise Campaign108Error("cannot seal failure with aliased artifact")
        if path.is_file():
            payload = _read_bytes(path, "%s failed-attempt artifact" % name)
            artifacts[name] = _raw_artifact(path, payload)
    message = str(error)
    body = {
        "schema": FAILURE_SCHEMA,
        "status": "CAMPAIGN_SINGLE_ATTEMPT_FAILED",
        "candidate_id": spec.candidate_id,
        "authority_name": spec.authority_name,
        "campaign_epoch": campaign_epoch,
        "attempt_index": 1,
        "attempt_sha256": progress.attempt["attempt_sha256"],
        "predecessor_failures": list(predecessors),
        "predecessor_failures_sha256": predecessor_digest,
        "failure_stage": progress.failure_stage,
        "exception_type": "%s.%s" % (
            type(error).__module__, type(error).__qualname__
        ),
        "exception_message": message,
        "exception_message_sha256": _sha256_bytes(message.encode("utf-8")),
        "native_output_sealed": progress.native_output_sealed,
        "screen_started": progress.screen_started,
        "score_computed": progress.score_computed,
        "labels_accessed": progress.labels_accessed,
        "retry_allowed": False,
        "tuning_allowed": False,
        "artifacts": artifacts,
    }
    receipt = dict(body, failure_receipt_sha256=canonical_sha256(body))
    _exclusive_write(
        paths["failure_receipt"], _json_bytes(receipt), "campaign failure receipt"
    )


def _screen_result_cncp(result: Mapping[str, object]) -> object:
    value = result.get("cncp")
    if isinstance(value, Mapping) and "declared_values" in value:
        return value["declared_values"]
    return value


def _run_campaign108_attempt(
    candidate_id: str,
    dataset_directory: Path,
    config_path: Path,
    cncp_path: Path,
    campaign_directory: Path,
    campaign_epoch: int,
    predecessor_failures: Sequence[Mapping[str, object]],
    predecessor_failures_sha256: str,
    progress: _CampaignProgress,
) -> Mapping[str, object]:
    """Execute one production attempt and one explicitly non-tuning replay."""

    spec = _candidate(candidate_id)
    config_file = Path(config_path)
    cncp_file = Path(cncp_path)
    caller_config_bytes = _read_bytes(config_file, "candidate config")
    caller_config = _read_json_bytes(caller_config_bytes, "candidate config")
    if caller_config_bytes != spec.config_bytes:
        raise Campaign108Error("caller config bytes differ from native adapter")
    cncp_bytes = _read_bytes(cncp_file, "CNCP")
    cncp = _read_json_bytes(cncp_bytes, "CNCP")
    screen108.validate_cncp(cncp)

    authority, selected_authority, source_snapshots = _campaign_authority(spec)
    authority_bytes = _json_bytes(authority)
    authority_digest = _sha256(
        authority.get("aggregate_sha256"), "campaign authority digest"
    )
    selected_manifest_digest = _sha256(
        selected_authority.get("manifest_sha256"), "candidate authority digest"
    )
    campaign_bytes = _read_bytes(Path(__file__), "campaign runner")
    campaign_digest = _sha256_bytes(campaign_bytes)

    root = Path(campaign_directory)
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise Campaign108Error("campaign directory must be a real directory")
    try:
        root.mkdir(mode=0o755, parents=False, exist_ok=True)
    except OSError as exc:
        raise Campaign108Error("cannot create campaign directory") from exc
    paths = _artifact_paths(root, candidate_id)
    progress.paths = paths

    attempt_body = {
        "schema": ATTEMPT_SCHEMA,
        "candidate_id": candidate_id,
        "authority_name": spec.authority_name,
        "campaign_epoch": campaign_epoch,
        "attempt_index": 1,
        "predecessor_failures": list(predecessor_failures),
        "predecessor_failures_sha256": predecessor_failures_sha256,
        "campaign_authority_sha256": authority_digest,
        "candidate_authority_sha256": selected_manifest_digest,
        "authority_config_sha256": selected_authority["config_sha256"],
        "caller_config_sha256": _sha256_bytes(caller_config_bytes),
        "caller_config_semantic_sha256": canonical_sha256(caller_config),
        "cncp_sha256": _sha256_bytes(cncp_bytes),
        "cncp_semantic_sha256": canonical_sha256(cncp),
        "campaign_runner_sha256": campaign_digest,
        "adapter_execution_count": 2,
        "verification_replay_count": 1,
        "verification_replay_is_tuning": False,
        "retry_allowed": False,
        "tuning_allowed": False,
    }
    attempt = dict(attempt_body, attempt_sha256=canonical_sha256(attempt_body))
    progress.attempt = attempt
    attempt_bytes = _json_bytes(attempt)
    _exclusive_write(paths["attempt"], attempt_bytes, "campaign attempt marker")
    progress.attempt_sealed = True
    progress.failure_stage = "CAMPAIGN_AUTHORITY_SEAL"
    _exclusive_write(paths["campaign_authority"], authority_bytes, "campaign authority")

    progress.failure_stage = "INPUT_BUILD"
    bundle = _build_verified_stage3_adapter(Path(dataset_directory))
    neutral = _neutral_view(bundle)
    progress.failure_stage = "BASELINE_BUILD"
    baseline = _compact_neutral_baseline_view(neutral)
    progress.failure_stage = "NEUTRAL_BINDING"
    neutral_digest = _neutral_projection_sha256(neutral)
    if neutral_digest != baseline.neutral_input_sha256:
        raise Campaign108Error("neutral source binding differs from baseline")

    progress.failure_stage = "PRODUCTION_ADAPTER"
    produced = spec.adapter(neutral, baseline)
    progress.failure_stage = "NATIVE_OUTPUT_VALIDATE"
    native_output = _validate_native_output(produced, spec, neutral, baseline)
    native_bytes = _json_bytes(native_output)
    native_digest = _sha256(
        native_output.get("aggregate_sha256"), "native output digest"
    )
    progress.failure_stage = "NATIVE_OUTPUT_SEAL"
    _exclusive_write(paths["native_output"], native_bytes, "rich native output")
    progress.native_output_sealed = True

    progress.failure_stage = "PRE_REPLAY_INTEGRITY"
    _check_unchanged(config_file, caller_config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(Path(__file__), campaign_bytes, "campaign runner")
    _check_authority_unchanged(authority, source_snapshots)
    if _neutral_projection_sha256(_neutral_view(bundle)) != neutral_digest:
        raise Campaign108Error("neutral source changed after production execution")

    progress.failure_stage = "VERIFICATION_REPLAY"
    replayed = spec.adapter(neutral, baseline)
    replay_output = _validate_native_output(replayed, spec, neutral, baseline)
    replay_bytes = _json_bytes(replay_output)
    if replay_bytes != native_bytes:
        raise Campaign108Error("verifier replay rich output is not byte-identical")
    replay_body = {
        "schema": REPLAY_SCHEMA,
        "candidate_id": candidate_id,
        "campaign_epoch": campaign_epoch,
        "attempt_sha256": attempt["attempt_sha256"],
        "mode": "verifier_replay",
        "neutral_input_sha256": neutral_digest,
        "adapter_execution_count": 2,
        "production_native_bytes_sha256": _sha256_bytes(native_bytes),
        "replay_native_bytes_sha256": _sha256_bytes(replay_bytes),
        "native_output_byte_identical": True,
        "replay_used_for_tuning": False,
        "replay_output_used_for_screen": False,
    }
    replay = dict(replay_body, replay_sha256=canonical_sha256(replay_body))
    replay_bytes_artifact = _json_bytes(replay)
    progress.failure_stage = "REPLAY_SEAL"
    _exclusive_write(paths["replay"], replay_bytes_artifact, "verifier replay receipt")

    progress.failure_stage = "POST_REPLAY_INTEGRITY"
    _check_unchanged(config_file, caller_config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(Path(__file__), campaign_bytes, "campaign runner")
    _check_authority_unchanged(authority, source_snapshots)
    _check_unchanged(paths["native_output"], native_bytes, "rich native output")
    if _neutral_projection_sha256(_neutral_view(bundle)) != neutral_digest:
        raise Campaign108Error("neutral source changed during verifier replay")

    progress.failure_stage = "PROJECTION"
    projected = _project_native_output(native_output)
    screen_output, projection_receipt, executable_bytes, projection_config = (
        _validate_projection(native_output, projected, spec)
    )
    if caller_config_bytes != projection_config:
        raise Campaign108Error("caller config bytes differ from screen projection")
    projection_bytes = _json_bytes(projection_receipt)
    screen_output_bytes = _json_bytes(screen_output)
    projection_digest = _sha256(
        projection_receipt.get("projection_receipt_sha256"),
        "projection receipt digest",
    )
    screen_output_digest = _sha256(
        screen_output.get("aggregate_sha256"), "screen output digest"
    )
    progress.failure_stage = "PROJECTION_SEAL"
    _exclusive_write(
        paths["projection_receipt"], projection_bytes, "projection receipt"
    )
    _exclusive_write(paths["screen_output"], screen_output_bytes, "screen output")
    _exclusive_write(
        paths["executable_artifact"], executable_bytes, "executable authority"
    )

    evidence_body = {
        "schema": GENERATOR_EVIDENCE_SCHEMA,
        "candidate_id": candidate_id,
        "authority_name": spec.authority_name,
        "campaign_epoch": campaign_epoch,
        "attempt_sha256": attempt["attempt_sha256"],
        "predecessor_failures_sha256": predecessor_failures_sha256,
        "campaign_authority_sha256": authority_digest,
        "candidate_authority_sha256": selected_manifest_digest,
        "native_output_sha256": native_digest,
        "projection_receipt_sha256": projection_digest,
        "screen_output_sha256": screen_output_digest,
        "executable_artifact_sha256": _sha256_bytes(executable_bytes),
        "candidate_config_sha256": _sha256_bytes(projection_config),
        "campaign_runner_sha256": campaign_digest,
        "ordered_window_ids": [row.window_id for row in neutral.neutral_registry],
    }
    evidence = dict(evidence_body, aggregate_sha256=canonical_sha256(evidence_body))
    evidence_bytes = _json_bytes(evidence)
    progress.failure_stage = "GENERATOR_EVIDENCE_SEAL"
    _exclusive_write(paths["generator_evidence"], evidence_bytes, "generator evidence")

    progress.failure_stage = "PRE_SCREEN_INTEGRITY"
    for path, payload, where in (
        (config_file, caller_config_bytes, "candidate config"),
        (cncp_file, cncp_bytes, "CNCP"),
        (Path(__file__), campaign_bytes, "campaign runner"),
        (paths["native_output"], native_bytes, "rich native output"),
        (paths["projection_receipt"], projection_bytes, "projection receipt"),
        (paths["screen_output"], screen_output_bytes, "screen output"),
        (paths["executable_artifact"], executable_bytes, "executable authority"),
    ):
        _check_unchanged(path, payload, where)
    _check_authority_unchanged(authority, source_snapshots)

    progress.failure_stage = "SCREEN"
    progress.screen_started = True
    progress.labels_accessed = True
    screen_result = screen108.run_locked_screen108(
        Path(dataset_directory),
        paths["screen_output"],
        paths["executable_artifact"],
        config_file,
        cncp,
    )
    progress.score_computed = True
    progress.failure_stage = "SCREEN_RESULT_VALIDATE"
    screen_digest = screen108.verify_screen108_result_envelope(screen_result)
    screen_result_bytes = _json_bytes(screen_result)
    progress.failure_stage = "SCREEN_RESULT_SEAL"
    _exclusive_write(paths["screen_result"], screen_result_bytes, "screen108 result")

    immutable = (
        ("attempt", attempt_bytes),
        ("campaign_authority", authority_bytes),
        ("native_output", native_bytes),
        ("projection_receipt", projection_bytes),
        ("screen_output", screen_output_bytes),
        ("executable_artifact", executable_bytes),
        ("generator_evidence", evidence_bytes),
        ("replay", replay_bytes_artifact),
        ("screen_result", screen_result_bytes),
    )
    progress.failure_stage = "FINAL_INTEGRITY"
    _check_unchanged(config_file, caller_config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(Path(__file__), campaign_bytes, "campaign runner")
    _check_authority_unchanged(authority, source_snapshots)
    for name, payload in immutable:
        _check_unchanged(paths[name], payload, "%s artifact" % name)

    artifacts = {
        "attempt": _artifact(paths["attempt"], attempt_bytes, attempt["attempt_sha256"]),
        "campaign_authority": _artifact(
            paths["campaign_authority"], authority_bytes, authority_digest
        ),
        "native_output": _artifact(paths["native_output"], native_bytes, native_digest),
        "projection_receipt": _artifact(
            paths["projection_receipt"], projection_bytes, projection_digest
        ),
        "screen_output": _artifact(
            paths["screen_output"], screen_output_bytes, screen_output_digest
        ),
        "executable_artifact": _artifact(
            paths["executable_artifact"], executable_bytes,
            _sha256_bytes(executable_bytes),
        ),
        "generator_evidence": _artifact(
            paths["generator_evidence"], evidence_bytes, evidence["aggregate_sha256"]
        ),
        "replay": _artifact(paths["replay"], replay_bytes_artifact, replay["replay_sha256"]),
        "screen_result": _artifact(paths["screen_result"], screen_result_bytes, screen_digest),
    }
    bindings = {
        "predecessor_failures_sha256": predecessor_failures_sha256,
        "campaign_authority_sha256": authority_digest,
        "candidate_authority_sha256": selected_manifest_digest,
        "authority_config_sha256": selected_authority["config_sha256"],
        "caller_config_sha256": _sha256_bytes(caller_config_bytes),
        "caller_config_semantic_sha256": canonical_sha256(caller_config),
        "cncp_sha256": _sha256_bytes(cncp_bytes),
        "cncp_semantic_sha256": canonical_sha256(cncp),
        "campaign_runner_sha256": campaign_digest,
        "native_output_sha256": native_digest,
        "projection_receipt_sha256": projection_digest,
        "screen_output_sha256": screen_output_digest,
        "executable_artifact_sha256": _sha256_bytes(executable_bytes),
        "candidate_config_sha256": _sha256_bytes(projection_config),
        "generator_evidence_sha256": evidence["aggregate_sha256"],
        "replay_sha256": replay["replay_sha256"],
        "screen_result_sha256": screen_digest,
    }
    receipt_body = {
        "schema": CAMPAIGN_SCHEMA,
        "status": "SCREEN108_SINGLE_ATTEMPT_REPLAY_VERIFIED",
        "candidate_id": candidate_id,
        "authority_name": spec.authority_name,
        "campaign_epoch": campaign_epoch,
        "attempt_sha256": attempt["attempt_sha256"],
        "predecessor_failures": list(predecessor_failures),
        "bindings": bindings,
        "artifacts": artifacts,
        "policy": {
            "attempt_count": 1,
            "adapter_execution_count": 2,
            "verification_replay_count": 1,
            "verification_replay_is_tuning": False,
            "verification_replay_output_scored": False,
            "retry_performed": False,
            "tuning_performed": False,
            "labels_accessed_before_screen_output_seal": False,
            "source_selection_changed": False,
            "external_data_accessed": False,
            "rtl_or_ppa_evaluated": False,
        },
    }
    receipt = dict(receipt_body, receipt_sha256=canonical_sha256(receipt_body))
    progress.failure_stage = "CAMPAIGN_RECEIPT_SEAL"
    _exclusive_write(paths["campaign_receipt"], _json_bytes(receipt), "campaign receipt")
    return receipt


def run_campaign108(
    candidate_id: str,
    dataset_directory: Path,
    config_path: Path,
    cncp_path: Path,
    campaign_directory: Path,
    campaign_epoch: int = 1,
    predecessor_failure_receipts: Sequence[Path] = (),
) -> Mapping[str, object]:
    """Execute one attempt in one epoch and seal every post-marker failure."""

    spec = _candidate(candidate_id)
    epoch = _campaign_epoch(campaign_epoch)
    predecessors, predecessor_digest = _predecessor_failure_bindings(
        candidate_id, epoch, predecessor_failure_receipts
    )
    progress = _CampaignProgress()
    try:
        return _run_campaign108_attempt(
            candidate_id,
            dataset_directory,
            config_path,
            cncp_path,
            campaign_directory,
            epoch,
            predecessors,
            predecessor_digest,
            progress,
        )
    except Exception as error:
        try:
            _seal_failure_receipt(
                spec, epoch, predecessors, predecessor_digest, progress, error
            )
        except Exception as receipt_error:
            raise Campaign108Error(
                "campaign failed and append-only failure receipt could not be sealed"
            ) from receipt_error
        raise


def verify_campaign108_receipt(
    receipt: Mapping[str, object],
    campaign_directory: Path,
    predecessor_failure_receipts: Sequence[Path] = (),
) -> str:
    """Verify source authority and every append-only campaign cross-binding."""

    required = {
        "schema", "status", "candidate_id", "authority_name", "attempt_sha256",
        "campaign_epoch", "predecessor_failures", "bindings", "artifacts",
        "policy", "receipt_sha256",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        raise Campaign108Error("campaign receipt field schema differs")
    if (
        receipt.get("schema") != CAMPAIGN_SCHEMA
        or receipt.get("status") != "SCREEN108_SINGLE_ATTEMPT_REPLAY_VERIFIED"
    ):
        raise Campaign108Error("campaign receipt schema or status differs")
    spec = _candidate(receipt.get("candidate_id"))
    epoch = _campaign_epoch(receipt.get("campaign_epoch"))
    if receipt.get("authority_name") != spec.authority_name:
        raise Campaign108Error("campaign authority name differs")
    expected_policy = {
        "attempt_count": 1,
        "adapter_execution_count": 2,
        "verification_replay_count": 1,
        "verification_replay_is_tuning": False,
        "verification_replay_output_scored": False,
        "retry_performed": False,
        "tuning_performed": False,
        "labels_accessed_before_screen_output_seal": False,
        "source_selection_changed": False,
        "external_data_accessed": False,
        "rtl_or_ppa_evaluated": False,
    }
    if receipt.get("policy") != expected_policy:
        raise Campaign108Error("campaign receipt policy boundary differs")
    predecessors = _validate_predecessor_bindings(
        receipt.get("predecessor_failures"),
        receipt.get("bindings", {}).get("predecessor_failures_sha256")
        if isinstance(receipt.get("bindings"), Mapping) else None,
        epoch,
        spec.candidate_id,
    )
    reopened_predecessors, reopened_predecessor_digest = (
        _predecessor_failure_bindings(
            spec.candidate_id,
            epoch,
            predecessor_failure_receipts,
        )
    )
    if (
        reopened_predecessors != predecessors
        or reopened_predecessor_digest
        != receipt.get("bindings", {}).get("predecessor_failures_sha256")
    ):
        raise Campaign108Error("campaign predecessor failure binding differs")
    unsigned = dict(receipt)
    receipt_digest = unsigned.pop("receipt_sha256", None)
    if receipt_digest != canonical_sha256(unsigned):
        raise Campaign108Error("campaign receipt aggregate seal differs")

    authority, selected, unused = _campaign_authority(spec)
    del unused
    bindings = receipt.get("bindings")
    expected_binding_fields = {
        "predecessor_failures_sha256",
        "campaign_authority_sha256", "candidate_authority_sha256",
        "authority_config_sha256", "caller_config_sha256",
        "caller_config_semantic_sha256", "cncp_sha256", "cncp_semantic_sha256",
        "campaign_runner_sha256", "native_output_sha256",
        "projection_receipt_sha256", "screen_output_sha256",
        "executable_artifact_sha256", "candidate_config_sha256",
        "generator_evidence_sha256", "replay_sha256", "screen_result_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != expected_binding_fields:
        raise Campaign108Error("campaign digest bindings differ")
    for field in expected_binding_fields:
        _sha256(bindings[field], "campaign %s" % field)
    if (
        bindings["predecessor_failures_sha256"] != canonical_sha256(list(predecessors))
        or
        bindings["campaign_authority_sha256"] != authority["aggregate_sha256"]
        or bindings["candidate_authority_sha256"] != selected["manifest_sha256"]
        or bindings["authority_config_sha256"] != selected["config_sha256"]
        or bindings["caller_config_sha256"] != bindings["candidate_config_sha256"]
        or bindings["campaign_runner_sha256"] != _sha256_bytes(_read_bytes(Path(__file__), "campaign runner"))
    ):
        raise Campaign108Error("campaign authority binding differs")

    artifact_names = {
        "attempt", "campaign_authority", "native_output", "projection_receipt",
        "screen_output", "executable_artifact", "generator_evidence", "replay",
        "screen_result",
    }
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != artifact_names:
        raise Campaign108Error("campaign artifact index differs")
    paths = _artifact_paths(Path(campaign_directory), spec.candidate_id)
    payloads = {}
    for name in artifact_names:
        identity = artifacts[name]
        if not isinstance(identity, Mapping) or set(identity) != {
            "path", "size_bytes", "sha256", "semantic_sha256"
        }:
            raise Campaign108Error("campaign artifact identity differs")
        if identity["path"] != paths[name].name:
            raise Campaign108Error("campaign artifact path differs")
        payload = _read_bytes(paths[name], "%s artifact" % name)
        if len(payload) != identity["size_bytes"] or _sha256_bytes(payload) != identity["sha256"]:
            raise Campaign108Error("campaign artifact bytes differ")
        payloads[name] = payload

    decoded = {
        name: _read_json_bytes(payloads[name], "%s artifact" % name)
        for name in artifact_names - {"executable_artifact"}
    }
    if decoded["campaign_authority"] != authority:
        raise Campaign108Error("stored campaign authority differs")
    native = decoded["native_output"]
    native_unsigned = dict(native)
    native_digest = native_unsigned.pop("aggregate_sha256", None)
    if (
        native.get("candidate_id") != spec.candidate_id
        or native.get("schema") != spec.native_schema
        or native_digest != canonical_sha256(native_unsigned)
        or native_digest != bindings["native_output_sha256"]
    ):
        raise Campaign108Error("stored native output differs")
    screen_output = decoded["screen_output"]
    screen_unsigned = dict(screen_output)
    screen_digest = screen_unsigned.pop("aggregate_sha256", None)
    if (
        set(screen_output) != {
            "schema", "candidate_id", "adapter_aggregate_sha256",
            "neutral_input_sha256", "candidate_executable_sha256",
            "candidate_config_sha256", "windows", "aggregate_sha256",
        }
        or screen_output.get("schema") != screen108.CANDIDATE_OUTPUT_SCHEMA
        or screen_output.get("candidate_id") != spec.candidate_id
        or screen_output.get("adapter_aggregate_sha256") != native.get("adapter_aggregate_sha256")
        or screen_output.get("neutral_input_sha256") != native.get("neutral_input_sha256")
        or screen_output.get("candidate_executable_sha256") != _sha256_bytes(payloads["executable_artifact"])
        or screen_output.get("candidate_config_sha256") != bindings["candidate_config_sha256"]
        or screen_digest != canonical_sha256(screen_unsigned)
        or screen_digest != bindings["screen_output_sha256"]
    ):
        raise Campaign108Error("stored screen projection differs")
    projection_receipt = decoded["projection_receipt"]
    projection_unsigned = dict(projection_receipt)
    projection_digest = projection_unsigned.pop("projection_receipt_sha256", None)
    if (
        set(projection_receipt) != {
            "schema", "candidate_id", "native_schema", "native_aggregate_sha256",
            "projected_aggregate_sha256", "candidate_executable_sha256",
            "candidate_config_sha256", "windows", "projection_receipt_sha256",
        }
        or projection_receipt.get("candidate_id") != spec.candidate_id
        or projection_receipt.get("native_schema") != native.get("schema")
        or projection_receipt.get("native_aggregate_sha256") != native_digest
        or projection_receipt.get("projected_aggregate_sha256") != screen_digest
        or projection_receipt.get("candidate_executable_sha256")
        != _sha256_bytes(payloads["executable_artifact"])
        or projection_receipt.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or projection_digest != canonical_sha256(projection_unsigned)
        or projection_digest != bindings["projection_receipt_sha256"]
    ):
        raise Campaign108Error("stored projection receipt differs")

    replay = decoded["replay"]
    replay_unsigned = dict(replay)
    replay_digest = replay_unsigned.pop("replay_sha256", None)
    if (
        set(replay) != {
            "schema", "candidate_id", "campaign_epoch", "attempt_sha256",
            "mode", "neutral_input_sha256",
            "adapter_execution_count", "production_native_bytes_sha256",
            "replay_native_bytes_sha256", "native_output_byte_identical",
            "replay_used_for_tuning", "replay_output_used_for_screen",
            "replay_sha256",
        }
        or replay.get("schema") != REPLAY_SCHEMA
        or replay.get("candidate_id") != spec.candidate_id
        or replay.get("campaign_epoch") != epoch
        or replay.get("attempt_sha256") != receipt.get("attempt_sha256")
        or replay.get("mode") != "verifier_replay"
        or replay.get("neutral_input_sha256") != native.get("neutral_input_sha256")
        or replay.get("adapter_execution_count") != 2
        or replay.get("native_output_byte_identical") is not True
        or replay.get("replay_used_for_tuning") is not False
        or replay.get("replay_output_used_for_screen") is not False
        or replay.get("production_native_bytes_sha256") != artifacts["native_output"]["sha256"]
        or replay.get("replay_native_bytes_sha256") != artifacts["native_output"]["sha256"]
        or replay_digest != canonical_sha256(replay_unsigned)
        or replay_digest != bindings["replay_sha256"]
    ):
        raise Campaign108Error("campaign verifier replay seal differs")
    evidence = decoded["generator_evidence"]
    evidence_unsigned = dict(evidence)
    evidence_digest = evidence_unsigned.pop("aggregate_sha256", None)
    native_windows = native.get("windows")
    ordered_window_ids = [
        row.get("window_id") if isinstance(row, Mapping) else None
        for row in native_windows
    ] if isinstance(native_windows, list) else None
    if (
        set(evidence) != {
            "schema", "candidate_id", "authority_name",
            "campaign_epoch", "attempt_sha256", "predecessor_failures_sha256",
            "campaign_authority_sha256", "candidate_authority_sha256",
            "native_output_sha256", "projection_receipt_sha256",
            "screen_output_sha256", "executable_artifact_sha256",
            "candidate_config_sha256", "campaign_runner_sha256",
            "ordered_window_ids", "aggregate_sha256",
        }
        or evidence.get("schema") != GENERATOR_EVIDENCE_SCHEMA
        or evidence.get("candidate_id") != spec.candidate_id
        or evidence.get("authority_name") != spec.authority_name
        or evidence.get("campaign_epoch") != epoch
        or evidence.get("attempt_sha256") != receipt.get("attempt_sha256")
        or evidence.get("predecessor_failures_sha256")
        != bindings["predecessor_failures_sha256"]
        or evidence.get("campaign_authority_sha256") != bindings["campaign_authority_sha256"]
        or evidence.get("candidate_authority_sha256")
        != bindings["candidate_authority_sha256"]
        or evidence.get("native_output_sha256") != native_digest
        or evidence.get("projection_receipt_sha256") != bindings["projection_receipt_sha256"]
        or evidence.get("screen_output_sha256") != screen_digest
        or evidence.get("executable_artifact_sha256") != bindings["executable_artifact_sha256"]
        or evidence.get("candidate_config_sha256") != bindings["candidate_config_sha256"]
        or evidence.get("campaign_runner_sha256") != bindings["campaign_runner_sha256"]
        or evidence.get("ordered_window_ids") != ordered_window_ids
        or evidence_digest != canonical_sha256(evidence_unsigned)
        or evidence_digest != bindings["generator_evidence_sha256"]
    ):
        raise Campaign108Error("campaign generator evidence differs")
    attempt = decoded["attempt"]
    attempt_unsigned = dict(attempt)
    attempt_digest = attempt_unsigned.pop("attempt_sha256", None)
    if (
        set(attempt) != {
            "schema", "candidate_id", "authority_name", "campaign_epoch",
            "attempt_index", "predecessor_failures",
            "predecessor_failures_sha256",
            "campaign_authority_sha256", "candidate_authority_sha256",
            "authority_config_sha256", "caller_config_sha256",
            "caller_config_semantic_sha256", "cncp_sha256",
            "cncp_semantic_sha256", "campaign_runner_sha256",
            "adapter_execution_count", "verification_replay_count",
            "verification_replay_is_tuning", "retry_allowed", "tuning_allowed",
            "attempt_sha256",
        }
        or attempt.get("schema") != ATTEMPT_SCHEMA
        or attempt.get("candidate_id") != spec.candidate_id
        or attempt.get("authority_name") != spec.authority_name
        or attempt.get("campaign_epoch") != epoch
        or attempt.get("attempt_index") != 1
        or attempt.get("predecessor_failures") != list(predecessors)
        or attempt.get("predecessor_failures_sha256")
        != bindings["predecessor_failures_sha256"]
        or attempt.get("adapter_execution_count") != 2
        or attempt.get("verification_replay_count") != 1
        or attempt.get("verification_replay_is_tuning") is not False
        or attempt.get("retry_allowed") is not False
        or attempt.get("tuning_allowed") is not False
        or any(
            attempt.get(field) != bindings[field]
            for field in (
                "campaign_authority_sha256", "candidate_authority_sha256",
                "authority_config_sha256", "caller_config_sha256",
                "caller_config_semantic_sha256", "cncp_sha256",
                "cncp_semantic_sha256", "campaign_runner_sha256",
            )
        )
        or attempt_digest != canonical_sha256(attempt_unsigned)
        or attempt_digest != receipt.get("attempt_sha256")
    ):
        raise Campaign108Error("campaign attempt seal differs")
    result = decoded["screen_result"]
    result_digest = screen108.verify_screen108_result_envelope(result)
    provenance = result.get("provenance")
    if (
        result.get("candidate_id") != spec.candidate_id
        or canonical_sha256(_screen_result_cncp(result)) != bindings["cncp_semantic_sha256"]
        or not isinstance(provenance, Mapping)
        or provenance.get("candidate_output_sha256") != screen_digest
        or provenance.get("candidate_executable_sha256") != bindings["executable_artifact_sha256"]
        or provenance.get("candidate_config_sha256") != bindings["candidate_config_sha256"]
        or result_digest != bindings["screen_result_sha256"]
    ):
        raise Campaign108Error("campaign screen result binding differs")
    expected_semantic = {
        "attempt": attempt_digest,
        "campaign_authority": bindings["campaign_authority_sha256"],
        "native_output": native_digest,
        "projection_receipt": projection_digest,
        "screen_output": screen_digest,
        "executable_artifact": _sha256_bytes(payloads["executable_artifact"]),
        "generator_evidence": evidence_digest,
        "replay": replay_digest,
        "screen_result": result_digest,
    }
    if any(
        artifacts[name].get("semantic_sha256") != digest
        for name, digest in expected_semantic.items()
    ):
        raise Campaign108Error("campaign artifact semantic binding differs")
    return str(receipt_digest)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch exactly one source-bound Stage3 adapter to locked NEW108"
    )
    parser.add_argument("--candidate-id", choices=FROZEN_CANDIDATE_IDS, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cncp", type=Path, required=True)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--campaign-epoch", type=int, default=1)
    parser.add_argument(
        "--predecessor-failure-receipt",
        type=Path,
        action="append",
        default=[],
    )
    args = parser.parse_args(argv)
    receipt = run_campaign108(
        args.candidate_id,
        args.dataset_dir,
        args.config,
        args.cncp,
        args.campaign_dir,
        args.campaign_epoch,
        args.predecessor_failure_receipt,
    )
    print("receipt_sha256=%s" % receipt["receipt_sha256"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Campaign108Error as exc:
        print("campaign108: %s" % exc, file=sys.stderr)
        sys.exit(2)


__all__ = (
    "ATTEMPT_SCHEMA", "CAMPAIGN_SCHEMA", "DSPB_ID", "FAILURE_SCHEMA",
    "FROZEN_CANDIDATE_IDS", "GENERATOR_EVIDENCE_SCHEMA", "NeutralAdapterView",
    "NeutralBaselineView",
    "RG3_ID", "REPLAY_SCHEMA", "SO3_PLL_ID", "Campaign108Error",
    "frozen_candidate_config", "frozen_candidate_config_bytes", "run_campaign108",
    "verify_campaign108_failure_receipt", "verify_campaign108_receipt",
)
