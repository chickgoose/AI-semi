"""Original-24 compatibility adapter for the selector-neutral CAV evaluator.

The adapter reads already-frozen Stage-4 assay, seal, and result artifacts.  It
never invokes the SO(3) selector/analyzer or any Stage-4 scoring entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_assay.source import (
    Calibration,
    EventSample,
    SourceInputError,
    sensor_ray,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    CycleModelError,
    Event,
    PosePacket,
    PoseSource,
    pose_timestamp_to_cycle,
)

from .evaluator import (
    CAVRegistryEvaluation,
    CurrentCAVEvaluationError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)


OFFICIAL_ASSAY_MANIFEST_SHA256 = (
    "90b5286d42c8d85d88b14148c7150b8b9d1be252bc3465c90a4f80dc1f87d7f2"
)
OFFICIAL_SEAL_MANIFEST_SHA256 = (
    "a9dc53799242e6bd92e3df3213ddc67f9f11d58325f50617d7f30a497f1d72ed"
)
OFFICIAL_RESULT_FILE_SHA256 = (
    "08ed4cc7a8a80616003fad7061e33ce2ee47fd4b7ac5fbd1e738f102c5d2da16"
)
OFFICIAL_RESULT_BODY_SHA256 = (
    "20f9af927039fecad7e5e79e7ae01cfd46501236b85be2bf10816c21fee13b67"
)
_FLOAT_REL_TOL_CAP = 1.0e-12
_FLOAT_ABS_TOL_CAP = 1.0e-12
_MANIFEST = "stage4_input_manifest.json"
_EVENTS = "stage4_events.jsonl"
_DATASET_POSES = "stage4_dataset_pose_packets.jsonl"
_ASSAY_FILES = (
    _EVENTS,
    "stage4_occurrence_batches.jsonl",
    "stage4_occurrence_pose_snapshots.jsonl",
    _DATASET_POSES,
    "oracle_resampled_groundtruth_1khz.jsonl",
    "stage4_oracle_window_schedule.jsonl",
)
_CALIBRATION_FIELDS = (
    "width",
    "height",
    "fx",
    "fy",
    "cx",
    "cy",
    "k1",
    "k2",
    "p1",
    "p2",
    "k3",
)
_CALIBRATION_RULE = "radtan_inverse_newton_then_normalized_sensor_ray"
_EVENT_RECORD_FIELDS = frozenset((
    "window_id",
    "event_id",
    "event_sequence_tag",
    "timestamp_ns",
    "x",
    "y",
    "polarity",
    "sensor_ray",
    "is_query",
    "window_event_ordinal",
    "occurrence_cycle",
    "equal_timestamp_cluster_id",
    "equal_timestamp_cluster_size",
    "occurrence_batch_id",
    "occurrence_lane",
    "occurrence_batch_size",
    "occurrence_pose_snapshot_sha256",
    "causal_pose_source_index",
    "payload_hex",
    "presentation_cycle",
    "presentation_lane",
    "serializer_queue_cycles",
))
_DATASET_POSE_FIELDS = frozenset((
    "arrival_cycle",
    "commit_cycle",
    "packet_sha256",
    "pose_value_sha256",
    "quaternion_xyzw",
    "source_pose_id",
    "timestamp_ns",
    "visible_at_window_start",
    "visible_cycle",
    "window_id",
))


class Original24CompatibilityError(CurrentCAVEvaluationError):
    """The original Stage-4 compatibility cross-check failed."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise Original24CompatibilityError("cannot read compatibility file") from exc
    return digest.hexdigest()


def _duplicate_rejecting_object(
    pairs: Sequence[Tuple[str, Any]],
) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise Original24CompatibilityError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _decode_json(raw: bytes, where: str) -> Any:
    try:
        return json.loads(
            raw.decode("ascii"), object_pairs_hook=_duplicate_rejecting_object
        )
    except Original24CompatibilityError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise Original24CompatibilityError("%s is not strict ASCII JSON" % where) from exc


def _read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Original24CompatibilityError("cannot read compatibility JSON") from exc
    value = _decode_json(raw, path.name)
    if canonical_json_bytes(value) != raw:
        raise Original24CompatibilityError("compatibility JSON is not canonical")
    return value


def _read_jsonl(raw: bytes, where: str) -> Tuple[Mapping[str, Any], ...]:
    if raw and not raw.endswith(b"\n"):
        raise Original24CompatibilityError("%s lacks its final newline" % where)
    rows = []  # type: List[Mapping[str, Any]]
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        value = _decode_json(line, "%s:%d" % (where, line_number))
        if not isinstance(value, Mapping):
            raise Original24CompatibilityError("%s row must be an object" % where)
        if canonical_json_bytes(value) != line:
            raise Original24CompatibilityError("%s row is not canonical" % where)
        rows.append(value)
    return tuple(rows)


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Original24CompatibilityError("%s must be an object" % where)
    return value


def _integer(value: object, where: str, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Original24CompatibilityError("%s must be an integer" % where)
    if minimum is not None and value < minimum:
        raise Original24CompatibilityError("%s is below its minimum" % where)
    return value


def _digest(value: object, where: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise Original24CompatibilityError("%s must be lowercase SHA-256" % where)
    return value


def _quaternion(value: object, where: str) -> Tuple[float, float, float, float]:
    if type(value) is not list or len(value) != 4:  # type: ignore[arg-type]
        raise Original24CompatibilityError("%s must contain four floats" % where)
    if any(
        type(component) is not float or not math.isfinite(component)
        for component in value  # type: ignore[union-attr]
    ):
        raise Original24CompatibilityError("%s must contain exact finite floats" % where)
    return tuple(value)  # type: ignore[arg-type,return-value]


def _load_artifacts(
    root: Path, manifest: Mapping[str, Any]
) -> Mapping[str, Tuple[Mapping[str, Any], ...]]:
    identities = _mapping(manifest.get("artifacts"), "manifest artifacts")
    if frozenset(identities) != frozenset(_ASSAY_FILES):
        raise Original24CompatibilityError("frozen assay artifact set differs")
    loaded = {}  # type: Dict[str, Tuple[Mapping[str, Any], ...]]
    for name in _ASSAY_FILES:
        identity = _mapping(identities.get(name), "artifact %s" % name)
        if identity.get("path") != name:
            raise Original24CompatibilityError("artifact path differs: %s" % name)
        try:
            raw = (root / name).read_bytes()
        except OSError as exc:
            raise Original24CompatibilityError("assay artifact is missing") from exc
        if hashlib.sha256(raw).hexdigest() != _digest(
            identity.get("sha256"), "artifact digest"
        ):
            raise Original24CompatibilityError("assay artifact digest differs")
        if len(raw) != _integer(identity.get("size_bytes"), "artifact size", 0):
            raise Original24CompatibilityError("assay artifact size differs")
        rows = _read_jsonl(raw, name)
        if len(rows) != _integer(identity.get("record_count"), "record count", 0):
            raise Original24CompatibilityError("assay artifact count differs")
        loaded[name] = rows
    return loaded


def _load_calibration(manifest: Mapping[str, Any]) -> Calibration:
    authority = _mapping(
        manifest.get("authoritative_input_binding"), "input authority"
    )
    calibration = _mapping(
        authority.get("calibration_model"), "calibration authority"
    )
    body = dict(calibration)
    supplied = _digest(body.pop("authority_sha256", None), "calibration digest")
    if canonical_sha256(body) != supplied:
        raise Original24CompatibilityError("calibration authority digest differs")
    if frozenset(body) != frozenset((
        "schema",
        "source_path",
        "source_sha256",
        "sensor_ray_generator_rule",
        "model",
    )):
        raise Original24CompatibilityError("calibration authority fields differ")
    source = _mapping(manifest.get("source"), "manifest source")
    if (
        body.get("schema") != "redred.mc_wtb.stage4_calibration_authority/v1"
        or body.get("source_path") != "calib.txt"
        or body.get("source_sha256") != source.get("calibration_sha256")
        or body.get("sensor_ray_generator_rule") != _CALIBRATION_RULE
    ):
        raise Original24CompatibilityError("calibration authority differs")
    model = _mapping(body.get("model"), "calibration model")
    if frozenset(model) != frozenset(_CALIBRATION_FIELDS):
        raise Original24CompatibilityError("calibration model fields differ")
    for field in ("width", "height"):
        _integer(model.get(field), "calibration %s" % field, 1)
    for field in _CALIBRATION_FIELDS[2:]:
        value = model.get(field)
        if type(value) is not float or not math.isfinite(value):
            raise Original24CompatibilityError(
                "calibration %s must be an exact finite float" % field
            )
    try:
        return Calibration(**{field: model[field] for field in _CALIBRATION_FIELDS})
    except SourceInputError as exc:
        raise Original24CompatibilityError("calibration model is invalid") from exc


def _recompute_sensor_ray(
    row: Mapping[str, Any], calibration: Calibration
) -> Tuple[float, float, float]:
    try:
        recovered = sensor_ray(
            EventSample(
                _integer(row.get("event_id"), "event ID", 0),
                _integer(row.get("timestamp_ns"), "event timestamp", 0),
                _integer(row.get("x"), "event x", 0),
                _integer(row.get("y"), "event y", 0),
                _integer(row.get("polarity"), "event polarity", 0),
            ),
            calibration,
        )
    except SourceInputError as exc:
        raise Original24CompatibilityError("sensor-ray recovery failed") from exc
    serialized = row.get("sensor_ray")
    if (
        type(serialized) is not list
        or len(serialized) != 3
        or any(
            type(component) is not float or not math.isfinite(component)
            for component in serialized
        )
        or list(recovered) != serialized
    ):
        raise Original24CompatibilityError("serialized sensor ray differs")
    return recovered


def _load_frozen_assay(
    assay_dir: Path, expected_manifest_sha256: str
) -> Tuple[
    Mapping[str, Any],
    Mapping[str, Tuple[Mapping[str, Any], ...]],
    Calibration,
]:
    if expected_manifest_sha256 != OFFICIAL_ASSAY_MANIFEST_SHA256:
        raise Original24CompatibilityError("official assay digest cannot be overridden")
    root = Path(assay_dir)
    manifest = _read_json(root / _MANIFEST)
    if not isinstance(manifest, Mapping):
        raise Original24CompatibilityError("assay manifest must be an object")
    if _file_sha256(root / _MANIFEST) != OFFICIAL_ASSAY_MANIFEST_SHA256:
        raise Original24CompatibilityError("official assay manifest digest differs")
    if manifest.get("schema") != "redred.mc_wtb.stage4_score_free_inputs/v2":
        raise Original24CompatibilityError("official assay schema differs")
    return manifest, _load_artifacts(root, manifest), _load_calibration(manifest)


def load_original_24_neutral_inputs(
    assay_dir: Path,
    *,
    expected_manifest_sha256: str = OFFICIAL_ASSAY_MANIFEST_SHA256,
) -> Tuple[
    Tuple[NeutralRegistryWindow, ...],
    Mapping[str, Tuple[NeutralEventInput, ...]],
    Mapping[str, Tuple[NeutralPoseInput, ...]],
]:
    """Adapt the official assay into the strict neutral evaluator interface."""

    manifest, artifacts, calibration = _load_frozen_assay(
        assay_dir, expected_manifest_sha256
    )
    summaries = manifest.get("windows")
    if not isinstance(summaries, list) or len(summaries) != 24:
        raise Original24CompatibilityError("official assay is not the original 24")
    registry_rows = []  # type: List[Mapping[str, object]]
    for summary in summaries:
        row = _mapping(summary, "window summary")
        registry_rows.append({
            "window_id": row.get("window_id"),
            "warmup_start_ns_inclusive": row.get("warmup_start_ns_inclusive"),
            "query_start_ns_inclusive": row.get("query_start_ns_inclusive"),
            "query_end_ns_exclusive": row.get("query_end_ns_exclusive"),
        })
    registry = tuple(
        NeutralRegistryWindow(
            row["window_id"],  # type: ignore[arg-type]
            row["warmup_start_ns_inclusive"],  # type: ignore[arg-type]
            row["query_start_ns_inclusive"],  # type: ignore[arg-type]
            row["query_end_ns_exclusive"],  # type: ignore[arg-type]
        )
        for row in registry_rows
    )
    registry_authority = _mapping(manifest.get("registry"), "manifest registry")
    if (
        registry_authority.get("window_count") != 24
        or registry_authority.get("sha256") != canonical_sha256(registry_rows)
    ):
        raise Original24CompatibilityError("manifest registry binding differs")

    all_events = artifacts[_EVENTS]
    all_poses = artifacts[_DATASET_POSES]
    event_streams: Dict[str, Tuple[NeutralEventInput, ...]] = {}
    pose_streams: Dict[str, Tuple[NeutralPoseInput, ...]] = {}
    prior_event_id = -1
    query_count = 0
    for window, summary_value in zip(registry, summaries):
        summary = _mapping(summary_value, "window summary")
        event_values = []  # type: List[NeutralEventInput]
        event_rows = tuple(
            row for row in all_events if row.get("window_id") == window.window_id
        )
        if len(event_rows) != _integer(
            summary.get("selected_event_count"), "selected event count", 1
        ):
            raise Original24CompatibilityError("window event count differs")
        for row in event_rows:
            if frozenset(row) != _EVENT_RECORD_FIELDS:
                raise Original24CompatibilityError("assay event field set differs")
            event_id = _integer(row.get("event_id"), "event ID", 0)
            timestamp_ns = _integer(row.get("timestamp_ns"), "event timestamp", 0)
            polarity = _integer(row.get("polarity"), "event polarity", 0)
            causal_index = _integer(
                row.get("causal_pose_source_index"), "causal pose index", 0
            )
            is_query = row.get("is_query")
            if type(is_query) is not bool:
                raise Original24CompatibilityError("event query flag is not bool")
            if event_id <= prior_event_id:
                raise Original24CompatibilityError("event IDs are not globally increasing")
            prior_event_id = event_id
            if not (
                window.warmup_start_ns_inclusive
                <= timestamp_ns
                < window.query_end_ns_exclusive
            ):
                raise Original24CompatibilityError("event lies outside registry bounds")
            if is_query != (window.query_start_ns_inclusive <= timestamp_ns):
                raise Original24CompatibilityError("event query flag differs from bounds")
            ray = _recompute_sensor_ray(row, calibration)
            try:
                cycle_event = Event(
                    event_id,
                    timestamp_ns,
                    transform_guard_valid=True,
                    causal_pose_index=causal_index,
                )
            except CycleModelError as exc:
                raise Original24CompatibilityError("event cannot enter cycle model") from exc
            content_sha256 = canonical_event_content_sha256(
                cycle_event.event_id,
                cycle_event.timestamp_ns,
                polarity,
                is_query,
                ray,
                causal_index,
                cycle_event.transform_guard_valid,
            )
            event_values.append(NeutralEventInput(
                cycle_event.event_id,
                cycle_event.timestamp_ns,
                polarity,
                is_query,
                ray,
                causal_index,
                content_sha256,
                cycle_event.transform_guard_valid,
            ))
        window_query_ids = [event.event_id for event in event_values if event.is_query]
        if (
            len(window_query_ids)
            != _integer(summary.get("query_event_count"), "query event count", 1)
            or canonical_sha256(window_query_ids)
            != summary.get("ordered_query_event_ids_sha256")
        ):
            raise Original24CompatibilityError("window query identity differs")
        query_count += len(window_query_ids)
        event_streams[window.window_id] = tuple(event_values)

        pose_values = []  # type: List[NeutralPoseInput]
        pose_rows = tuple(
            row for row in all_poses if row.get("window_id") == window.window_id
        )
        if not pose_rows:
            raise Original24CompatibilityError("window has no dataset poses")
        for row in pose_rows:
            if frozenset(row) != _DATASET_POSE_FIELDS:
                raise Original24CompatibilityError("dataset pose field set differs")
            pose_id = _integer(row.get("source_pose_id"), "pose ID", 0)
            timestamp_ns = _integer(row.get("timestamp_ns"), "pose timestamp", 0)
            commit_cycle = _integer(row.get("commit_cycle"), "pose commit cycle")
            quaternion = _quaternion(row.get("quaternion_xyzw"), "pose quaternion")
            pose_sha256 = _digest(row.get("pose_value_sha256"), "pose value digest")
            if pose_sha256 != canonical_pose_value_sha256(
                pose_id, timestamp_ns, quaternion
            ):
                raise Original24CompatibilityError("dataset pose value digest differs")
            packet_body = dict(row)
            packet_sha256 = _digest(
                packet_body.pop("packet_sha256", None), "pose packet digest"
            )
            if canonical_sha256(packet_body) != packet_sha256:
                raise Original24CompatibilityError("dataset pose packet digest differs")
            if (
                commit_cycle
                != pose_timestamp_to_cycle(
                    timestamp_ns, window.warmup_start_ns_inclusive
                )
                or row.get("arrival_cycle") != commit_cycle
                or row.get("visible_cycle") != commit_cycle + 1
            ):
                raise Original24CompatibilityError("dataset pose timing differs")
            try:
                packet = PosePacket(
                    pose_id,
                    timestamp_ns,
                    commit_cycle,
                    PoseSource.DATASET,
                    pose_sha256,
                )
            except CycleModelError as exc:
                raise Original24CompatibilityError("pose cannot enter cycle model") from exc
            pose_values.append(NeutralPoseInput(
                packet.pose_id,
                packet.timestamp_ns,
                packet.commit_cycle,
                quaternion,
                packet.pose_sha256,
                packet.value_valid,
                packet.arithmetic_valid,
            ))
        pose_streams[window.window_id] = tuple(pose_values)
    if query_count != registry_authority.get("query_event_count"):
        raise Original24CompatibilityError("manifest query population differs")
    return registry, event_streams, pose_streams


def evaluate_original_24(
    assay_dir: Path,
    *,
    expected_manifest_sha256: str = OFFICIAL_ASSAY_MANIFEST_SHA256,
) -> CAVRegistryEvaluation:
    """Run only current CAV on the original 24 through the neutral evaluator."""

    registry, events, poses = load_original_24_neutral_inputs(
        assay_dir, expected_manifest_sha256=expected_manifest_sha256
    )
    return evaluate_current_cav_registry(registry, events, poses)


def _close_float(
    observed: object,
    expected: object,
    where: str,
    *,
    rel_tol: float,
    abs_tol: float,
) -> None:
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        raise Original24CompatibilityError("%s observed value is not numeric" % where)
    if isinstance(expected, bool) or not isinstance(expected, (int, float)):
        raise Original24CompatibilityError("%s expected value is not numeric" % where)
    if not math.isclose(
        float(observed), float(expected), rel_tol=rel_tol, abs_tol=abs_tol
    ):
        raise Original24CompatibilityError("%s float differs" % where)


def _exact(observed: object, expected: object, where: str) -> None:
    if observed != expected:
        raise Original24CompatibilityError("%s differs" % where)


def _bounded_tolerance(value: object, cap: float, where: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
        or float(value) > cap
    ):
        raise Original24CompatibilityError("%s exceeds its frozen finite cap" % where)
    return float(value)


def _latency_compatible(
    observed: Mapping[str, object],
    expected: object,
    where: str,
    *,
    rel_tol: float,
    abs_tol: float,
) -> None:
    if not isinstance(expected, Mapping):
        raise Original24CompatibilityError("%s is not an object" % where)
    for field in ("count", "p50_cycles", "p95_cycles", "p99_cycles", "max_cycles"):
        _exact(observed[field], expected.get(field), "%s %s" % (where, field))
    _close_float(
        observed["mean_cycles"],
        expected.get("mean_cycles"),
        "%s mean_cycles" % where,
        rel_tol=rel_tol,
        abs_tol=abs_tol,
    )


@dataclass(frozen=True)
class Original24CompatibilityReport:
    window_count: int
    event_count: int
    exact_decision_count: int
    exact_reference_identity_count: int
    float_loss_count: int
    all_event_effect: float
    positive_windows: int
    result_body_sha256: str


def verify_original_24_compatibility(
    evaluation: CAVRegistryEvaluation,
    *,
    seal_dir: Path,
    result_path: Path,
    expected_seal_sha256: str = OFFICIAL_SEAL_MANIFEST_SHA256,
    expected_result_file_sha256: str = OFFICIAL_RESULT_FILE_SHA256,
    expected_result_body_sha256: str = OFFICIAL_RESULT_BODY_SHA256,
    float_rel_tol: float = _FLOAT_REL_TOL_CAP,
    float_abs_tol: float = _FLOAT_ABS_TOL_CAP,
) -> Original24CompatibilityReport:
    """Compare neutral CAV output with frozen decisions and official losses.

    Identity, decision, provenance, cycle, and reference fields require exact
    equality.  Tolerances apply only to binary64 loss and summary values.
    """

    float_rel_tol = _bounded_tolerance(
        float_rel_tol, _FLOAT_REL_TOL_CAP, "relative tolerance"
    )
    float_abs_tol = _bounded_tolerance(
        float_abs_tol, _FLOAT_ABS_TOL_CAP, "absolute tolerance"
    )
    if type(evaluation) is not CAVRegistryEvaluation:
        raise Original24CompatibilityError("evaluation type differs")
    if len(evaluation.windows) != 24:
        raise Original24CompatibilityError("evaluation is not the original 24")
    seal_root = Path(seal_dir)
    seal_manifest_path = seal_root / "stage4-score-free-seal-manifest.json"
    result_file = Path(result_path)
    _exact(
        expected_seal_sha256,
        OFFICIAL_SEAL_MANIFEST_SHA256,
        "official seal expectation",
    )
    _exact(
        expected_result_file_sha256,
        OFFICIAL_RESULT_FILE_SHA256,
        "official result expectation",
    )
    _exact(
        expected_result_body_sha256,
        OFFICIAL_RESULT_BODY_SHA256,
        "official result-body expectation",
    )
    _exact(_file_sha256(seal_manifest_path), expected_seal_sha256, "seal root")
    _exact(_file_sha256(result_file), expected_result_file_sha256, "result file")
    campaign = _read_json(seal_manifest_path)
    result = _read_json(result_file)
    if not isinstance(campaign, Mapping) or not isinstance(result, Mapping):
        raise Original24CompatibilityError("official compatibility roots must be objects")
    body = dict(result)
    result_seal = body.pop("result_seal", None)
    if not isinstance(result_seal, Mapping):
        raise Original24CompatibilityError("official result seal is missing")
    body_sha = canonical_sha256(body)
    _exact(body_sha, expected_result_body_sha256, "official result body")
    _exact(result_seal.get("sha256"), body_sha, "embedded result body seal")
    _exact(
        campaign.get("assay_manifest_sha256"),
        OFFICIAL_ASSAY_MANIFEST_SHA256,
        "campaign assay manifest",
    )
    _exact(
        campaign.get("registry_sha256"),
        evaluation.registry_sha256,
        "campaign registry digest",
    )
    _exact(
        campaign.get("window_order"),
        [window.registry.window_id for window in evaluation.windows],
        "window order",
    )

    files = campaign.get("files")
    leaves = result.get("leaves")
    aggregates = result.get("aggregates")
    if not (
        isinstance(files, Mapping)
        and isinstance(leaves, list)
        and isinstance(aggregates, Mapping)
    ):
        raise Original24CompatibilityError("official result structure differs")
    official_cav_leaves = {
        row.get("window_id"): row
        for row in leaves
        if isinstance(row, Mapping) and row.get("arm") == "causal_cav"
    }
    if len(official_cav_leaves) != 24:
        raise Original24CompatibilityError("official result lacks 24 CAV leaves")

    campaign_windows = campaign.get("windows")
    if not isinstance(campaign_windows, list) or len(campaign_windows) != 24:
        raise Original24CompatibilityError("campaign window index differs")
    campaign_window_index = {
        row.get("window_id"): row
        for row in campaign_windows
        if isinstance(row, Mapping)
    }
    if len(campaign_window_index) != 24:
        raise Original24CompatibilityError("campaign window identity differs")

    decisions_checked = 0
    references_checked = 0
    losses_checked = 0
    for window in evaluation.windows:
        window_id = window.registry.window_id
        window_identity = campaign_window_index.get(window_id)
        if not isinstance(window_identity, Mapping):
            raise Original24CompatibilityError("campaign window seal is missing")
        window_seal_relative = window_identity.get("path")
        if type(window_seal_relative) is not str:
            raise Original24CompatibilityError("campaign window path differs")
        window_seal_path = seal_root / window_seal_relative
        _exact(
            _file_sha256(window_seal_path),
            window_identity.get("sha256"),
            "%s window seal hash" % window_id,
        )
        window_file_identity = files.get(window_seal_relative)
        if not isinstance(window_file_identity, Mapping):
            raise Original24CompatibilityError("window seal is absent from file index")
        _exact(
            window_file_identity.get("sha256"),
            window_identity.get("sha256"),
            "%s indexed window seal hash" % window_id,
        )
        window_seal = _read_json(window_seal_path)
        if not isinstance(window_seal, Mapping):
            raise Original24CompatibilityError("window seal must be an object")
        _exact(
            window.registry.to_mapping(),
            {
                "window_id": window_seal.get("window_id"),
                "warmup_start_ns_inclusive": window_seal.get(
                    "warmup_start_ns_inclusive"
                ),
                "query_start_ns_inclusive": window_seal.get(
                    "query_start_ns_inclusive"
                ),
                "query_end_ns_exclusive": window_seal.get(
                    "query_end_ns_exclusive"
                ),
            },
            "%s registry bounds" % window_id,
        )
        leaf = official_cav_leaves.get(window_id)
        if not isinstance(leaf, Mapping):
            raise Original24CompatibilityError("official CAV leaf is missing")
        relative = "windows/%s/arms/causal_cav/query-decision-records.json" % window_id
        identity = files.get(relative)
        if not isinstance(identity, Mapping):
            raise Original24CompatibilityError("decision file is absent from seal index")
        decision_path = seal_root / relative
        _exact(_file_sha256(decision_path), identity.get("sha256"), "decision file hash")
        expected_decisions = _read_json(decision_path)
        if not isinstance(expected_decisions, list):
            raise Original24CompatibilityError("decision file must be an array")
        observed_decisions = [event.decision.to_mapping() for event in window.query_events]
        _exact(observed_decisions, expected_decisions, "%s decisions" % window_id)
        _exact(
            window.query_decisions_sha256,
            canonical_sha256(expected_decisions),
            "%s decision projection" % window_id,
        )
        decisions_checked += len(observed_decisions)

        expected_losses = leaf.get("event_losses")
        if (
            not isinstance(expected_losses, list)
            or len(expected_losses) != len(window.query_events)
        ):
            raise Original24CompatibilityError("official event loss cardinality differs")
        for observed, expected in zip(window.query_events, expected_losses):
            if not isinstance(expected, Mapping):
                raise Original24CompatibilityError("official event loss row differs")
            mapping = observed.to_loss_mapping()
            for field in (
                "event_id",
                "enabled",
                "quality_waste",
                "sensor_reference_event_id",
                "world_reference_event_id",
                "occurrence_latency_cycles",
                "added_latency_cycles",
            ):
                _exact(mapping[field], expected.get(field), "%s %s" % (window_id, field))
            references_checked += 2
            for field in ("sensor_loss", "world_shadow_loss", "policy_loss"):
                _close_float(
                    mapping[field],
                    expected.get(field),
                    "%s %s" % (window_id, field),
                    rel_tol=float_rel_tol,
                    abs_tol=float_abs_tol,
                )
                losses_checked += 1
        for field, observed in (
            ("accepted_events", window.accepted_events),
            ("enabled_events", window.enabled_events),
            ("quality_waste_events", window.quality_waste_events),
            ("positive_window", window.positive_window),
        ):
            _exact(observed, leaf.get(field), "%s %s" % (window_id, field))
        for field, observed in (
            ("sensor_loss_sum", window.sensor_loss_sum),
            ("policy_loss_sum", window.policy_loss_sum),
            ("all_event_effect", window.all_event_effect),
            ("enable_rate", window.enable_rate),
            ("quality_waste_rate", window.quality_waste_rate),
        ):
            _close_float(
                observed,
                leaf.get(field),
                "%s %s" % (window_id, field),
                rel_tol=float_rel_tol,
                abs_tol=float_abs_tol,
            )

    aggregate = aggregates.get("causal_cav")
    if not isinstance(aggregate, Mapping):
        raise Original24CompatibilityError("official CAV aggregate is missing")
    for field, observed in (
        ("accepted_events", evaluation.accepted_events),
        ("enabled_events", evaluation.enabled_events),
        ("quality_waste_events", evaluation.quality_waste_events),
        ("positive_windows", evaluation.positive_windows),
    ):
        _exact(observed, aggregate.get(field), "aggregate %s" % field)
    for field, observed in (
        ("all_event_effect", evaluation.all_event_effect),
        ("enable_rate", evaluation.enable_rate),
        ("quality_waste_rate", evaluation.quality_waste_rate),
    ):
        _close_float(
            observed,
            aggregate.get(field),
            "aggregate %s" % field,
            rel_tol=float_rel_tol,
            abs_tol=float_abs_tol,
        )
    _latency_compatible(
        evaluation.occurrence_latency.to_mapping(),
        aggregate.get("occurrence_latency"),
        "aggregate occurrence latency",
        rel_tol=float_rel_tol,
        abs_tol=float_abs_tol,
    )
    _latency_compatible(
        evaluation.added_latency.to_mapping(),
        aggregate.get("added_latency"),
        "aggregate added latency",
        rel_tol=float_rel_tol,
        abs_tol=float_abs_tol,
    )
    return Original24CompatibilityReport(
        len(evaluation.windows),
        evaluation.accepted_events,
        decisions_checked,
        references_checked,
        losses_checked,
        evaluation.all_event_effect,
        evaluation.positive_windows,
        body_sha,
    )
