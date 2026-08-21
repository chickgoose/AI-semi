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
from typing import Any, Dict, Mapping, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_integration import (
    build_window_cycle_inputs,
    load_assay_bundle,
)

from .evaluator import (
    CAVRegistryEvaluation,
    CurrentCAVEvaluationError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
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


class Original24CompatibilityError(CurrentCAVEvaluationError):
    """The original Stage-4 compatibility cross-check failed."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Original24CompatibilityError("cannot read compatibility JSON") from exc


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

    bundle = load_assay_bundle(
        Path(assay_dir), expected_manifest_sha256=expected_manifest_sha256
    )
    summaries = bundle.manifest.get("windows")
    if not isinstance(summaries, list) or len(summaries) != 24:
        raise Original24CompatibilityError("official assay is not the original 24")
    registry = tuple(
        NeutralRegistryWindow(
            row["window_id"],
            row["warmup_start_ns_inclusive"],
            row["query_start_ns_inclusive"],
            row["query_end_ns_exclusive"],
        )
        for row in summaries
    )
    event_streams: Dict[str, Tuple[NeutralEventInput, ...]] = {}
    pose_streams: Dict[str, Tuple[NeutralPoseInput, ...]] = {}
    for window in registry:
        inputs = build_window_cycle_inputs(bundle, window.window_id)
        event_streams[window.window_id] = tuple(
            NeutralEventInput(
                event.event_id,
                event.timestamp_ns,
                int(row["polarity"]),
                row["is_query"] is True,
                ray,
                event.causal_pose_index,
                event.transform_guard_valid,
            )
            for row, event, ray in zip(
                inputs.event_rows, inputs.events, inputs.sensor_rays
            )
        )
        pose_streams[window.window_id] = tuple(
            NeutralPoseInput(
                pose.pose_id,
                pose.timestamp_ns,
                pose.commit_cycle,
                inputs.dataset_quaternions[pose.pose_id],
                pose.pose_sha256,
                pose.value_valid,
                pose.arithmetic_valid,
            )
            for pose in inputs.dataset_poses
        )
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
    float_rel_tol: float = 1.0e-12,
    float_abs_tol: float = 1.0e-12,
) -> Original24CompatibilityReport:
    """Compare neutral CAV output with frozen decisions and official losses.

    Identity, decision, provenance, cycle, and reference fields require exact
    equality.  Tolerances apply only to binary64 loss and summary values.
    """

    if not isinstance(evaluation, CAVRegistryEvaluation):
        raise Original24CompatibilityError("evaluation type differs")
    if len(evaluation.windows) != 24:
        raise Original24CompatibilityError("evaluation is not the original 24")
    seal_root = Path(seal_dir)
    seal_manifest_path = seal_root / "stage4-score-free-seal-manifest.json"
    result_file = Path(result_path)
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

    decisions_checked = 0
    references_checked = 0
    losses_checked = 0
    for window in evaluation.windows:
        window_id = window.registry.window_id
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
