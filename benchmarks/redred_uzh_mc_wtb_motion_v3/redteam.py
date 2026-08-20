"""Architecture-neutral, fail-closed audits for MC-WTB metric-v3 candidates.

This module deliberately knows nothing about the metric-v3 evaluator or its
preregistration schema. Callers provide parsed identities, scores, references,
windows, or a candidate energy function. Every audit returns immutable
findings, so checks compose before a caller rejects with require_clean().
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real


EventId = Hashable
Point = tuple[float, float]


@dataclass(frozen=True, order=True)
class Finding:
    """One deterministic red-team contract violation."""

    code: str
    subject: str
    message: str


@dataclass(frozen=True)
class ReferenceIdentity:
    """Identity of the common evaluation reference used by every arm."""

    frame_id: str
    epoch_ns: int
    anchor_sha256: str
    projection_model_id: str


@dataclass(frozen=True)
class PhaseEnergy:
    """Energy observed after applying one common subpixel translation."""

    dx: float
    dy: float
    energy: float


@dataclass(frozen=True)
class PhaseAudit:
    """Measurements and violations from a fractional-phase invariance probe."""

    measurements: tuple[PhaseEnergy, ...]
    findings: tuple[Finding, ...]


class RedTeamContractError(ValueError):
    """Raised when one or more red-team findings are promoted to rejection."""

    def __init__(self, findings: Iterable[Finding]):
        self.findings = tuple(sorted(findings))
        if not self.findings:
            raise ValueError("RedTeamContractError requires at least one finding")
        summary = "; ".join(
            f"{item.code}[{item.subject}]: {item.message}" for item in self.findings
        )
        super().__init__(summary)


def _duplicates(values: Sequence[EventId]) -> tuple[EventId, ...]:
    counts = Counter(values)
    seen: set[EventId] = set()
    output: list[EventId] = []
    for value in values:
        if counts[value] > 1 and value not in seen:
            output.append(value)
            seen.add(value)
    return tuple(output)


def _display(values: Iterable[object], *, limit: int = 8) -> str:
    items = list(values)
    visible = ", ".join(repr(value) for value in items[:limit])
    if len(items) > limit:
        visible += f", ... (+{len(items) - limit})"
    return visible


def audit_event_denominators(
    expected_event_ids: Sequence[EventId],
    arm_event_ids: Mapping[str, Sequence[EventId]],
    *,
    oof_event_ids: Iterable[EventId] = (),
) -> tuple[Finding, ...]:
    """Reject deletion, duplication, reordering, and arm-local denominators.

    expected_event_ids is the sole cohort authority. OOF identities remain
    members of that cohort; naming them separately only permits a more precise
    OOF_DELETION finding. It never permits filtering.
    """

    expected = tuple(expected_event_ids)
    findings: list[Finding] = []
    if not expected:
        findings.append(
            Finding("EMPTY_COHORT", "source", "expected event cohort is empty")
        )
        return tuple(findings)

    source_duplicates = _duplicates(expected)
    if source_duplicates:
        findings.append(
            Finding(
                "SOURCE_EVENT_DUPLICATE",
                "source",
                f"cohort authority contains duplicate IDs: {_display(source_duplicates)}",
            )
        )

    expected_set = set(expected)
    oof = frozenset(oof_event_ids)
    unknown_oof = tuple(value for value in oof if value not in expected_set)
    if unknown_oof:
        findings.append(
            Finding(
                "OOF_NOT_IN_COHORT",
                "source",
                f"OOF authority names IDs outside the cohort: {_display(unknown_oof)}",
            )
        )

    if not arm_event_ids:
        findings.append(
            Finding("NO_ARMS", "arms", "no arm denominator was supplied")
        )
        return tuple(sorted(findings))

    for arm in sorted(arm_event_ids):
        observed = tuple(arm_event_ids[arm])
        duplicates = _duplicates(observed)
        if duplicates:
            findings.append(
                Finding(
                    "EVENT_DUPLICATE",
                    arm,
                    f"arm contains duplicate IDs: {_display(duplicates)}",
                )
            )

        observed_set = set(observed)
        missing = tuple(value for value in expected if value not in observed_set)
        unexpected = tuple(value for value in observed if value not in expected_set)
        missing_oof = tuple(value for value in missing if value in oof)

        if missing_oof:
            findings.append(
                Finding(
                    "OOF_DELETION",
                    arm,
                    f"arm deleted OOF IDs: {_display(missing_oof)}",
                )
            )
        if missing:
            findings.append(
                Finding(
                    "EVENT_DROP",
                    arm,
                    f"arm is missing cohort IDs: {_display(missing)}",
                )
            )
        if unexpected:
            findings.append(
                Finding(
                    "UNEXPECTED_EVENT",
                    arm,
                    f"arm added IDs outside the cohort: {_display(unexpected)}",
                )
            )
        if len(observed) != len(expected) or observed_set != expected_set:
            findings.append(
                Finding(
                    "ARM_LOCAL_DENOMINATOR",
                    arm,
                    f"arm denominator {len(observed)} differs from cohort {len(expected)}",
                )
            )
        elif observed != expected:
            findings.append(
                Finding(
                    "EVENT_ORDER_MISMATCH",
                    arm,
                    "arm contains the cohort but changes its canonical order",
                )
            )

    return tuple(sorted(findings))


def audit_negative_control_order(
    arm_scores: Mapping[str, Real],
    *,
    correct_arm: str = "MC_CORRECT",
    control_arms: Sequence[str] = ("MC_WRONG", "MC_DELAYED"),
    lower_is_better: bool = True,
    minimum_gap: float = 0.0,
) -> tuple[Finding, ...]:
    """Require the correct-pose arm to strictly beat each negative control."""

    if not math.isfinite(minimum_gap) or minimum_gap < 0.0:
        raise ValueError("minimum_gap must be finite and nonnegative")

    findings: list[Finding] = []
    required = (correct_arm, *control_arms)
    parsed: dict[str, float] = {}
    for arm in required:
        value = arm_scores.get(arm)
        if isinstance(value, bool) or not isinstance(value, Real):
            findings.append(
                Finding("ARM_SCORE_MISSING_OR_INVALID", arm, "score is not a real number")
            )
            continue
        number = float(value)
        if not math.isfinite(number):
            findings.append(
                Finding("ARM_SCORE_NONFINITE", arm, f"score is {number!r}")
            )
            continue
        parsed[arm] = number

    if correct_arm not in parsed:
        return tuple(sorted(findings))

    correct = parsed[correct_arm]
    for control in control_arms:
        if control not in parsed:
            continue
        control_value = parsed[control]
        advantage = (
            control_value - correct
            if lower_is_better
            else correct - control_value
        )
        if advantage < 0.0:
            findings.append(
                Finding(
                    "NEGATIVE_CONTROL_FAVORED",
                    control,
                    f"control score {control_value} beats correct score {correct}",
                )
            )
        elif advantage <= minimum_gap:
            findings.append(
                Finding(
                    "NEGATIVE_CONTROL_NOT_SEPARATED",
                    control,
                    f"correct/control advantage {advantage} is not > {minimum_gap}",
                )
            )
    return tuple(sorted(findings))


def audit_fractional_phase_bias(
    points: Sequence[Point],
    energy: Callable[[tuple[Point, ...]], Real],
    *,
    phases: Sequence[Point] = (
        (0.0, 0.0),
        (0.25, 0.25),
        (0.5, 0.5),
        (0.75, 0.75),
    ),
    absolute_tolerance: float = 1e-12,
    relative_tolerance: float = 1e-12,
) -> PhaseAudit:
    """Probe whether common subpixel translation changes candidate self-energy.

    All pairwise geometry is unchanged by the translation. A metric may opt
    into a looser, preregistered tolerance, but an integer-coordinate phase
    cannot receive a hidden advantage merely because fractional samples are
    splatted differently.
    """

    if not points:
        raise ValueError("points must be nonempty")
    if not phases:
        raise ValueError("phases must be nonempty")
    if (
        not math.isfinite(absolute_tolerance)
        or not math.isfinite(relative_tolerance)
        or absolute_tolerance < 0.0
        or relative_tolerance < 0.0
    ):
        raise ValueError("phase tolerances must be finite and nonnegative")

    canonical_points: list[Point] = []
    for point in points:
        if len(point) != 2:
            raise ValueError("every point must have exactly two coordinates")
        x, y = float(point[0]), float(point[1])
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("point coordinates must be finite")
        canonical_points.append((x, y))

    measurements: list[PhaseEnergy] = []
    findings: list[Finding] = []
    for ordinal, phase in enumerate(phases):
        if len(phase) != 2:
            raise ValueError("every phase must have exactly two coordinates")
        dx, dy = float(phase[0]), float(phase[1])
        if not math.isfinite(dx) or not math.isfinite(dy):
            raise ValueError("phase coordinates must be finite")
        translated = tuple((x + dx, y + dy) for x, y in canonical_points)
        try:
            raw_value = energy(translated)
        except Exception as error:  # A crashing metric fails closed.
            findings.append(
                Finding(
                    "PHASE_ENERGY_EXCEPTION",
                    f"phase[{ordinal}]",
                    f"energy callable raised {type(error).__name__}: {error}",
                )
            )
            continue
        if isinstance(raw_value, bool) or not isinstance(raw_value, Real):
            findings.append(
                Finding(
                    "PHASE_ENERGY_INVALID",
                    f"phase[{ordinal}]",
                    "energy callable did not return a real number",
                )
            )
            continue
        value = float(raw_value)
        if not math.isfinite(value):
            findings.append(
                Finding(
                    "PHASE_ENERGY_NONFINITE",
                    f"phase[{ordinal}]",
                    f"energy is {value!r}",
                )
            )
            continue
        measurements.append(PhaseEnergy(dx, dy, value))

    if len(measurements) == len(phases):
        values = [item.energy for item in measurements]
        spread = max(values) - min(values)
        scale = max(1.0, *(abs(value) for value in values))
        allowance = absolute_tolerance + relative_tolerance * scale
        if spread > allowance:
            rendered = ", ".join(
                f"({item.dx:g},{item.dy:g})={item.energy:.17g}"
                for item in measurements
            )
            findings.append(
                Finding(
                    "FRACTIONAL_PHASE_SELF_ENERGY_BIAS",
                    "energy",
                    f"phase spread {spread:.17g} exceeds {allowance:.17g}: {rendered}",
                )
            )

    return PhaseAudit(tuple(measurements), tuple(sorted(findings)))


def audit_window_selection(
    frozen_window_ids: Sequence[str],
    evaluated_window_ids: Sequence[str],
    reported_window_ids: Sequence[str],
    *,
    frozen_primary_window: str | None = None,
    reported_primary_window: str | None = None,
    frozen_selection_rule: str = "ALL_WINDOWS",
    reported_selection_rule: str = "ALL_WINDOWS",
) -> tuple[Finding, ...]:
    """Reject omitted windows and primary-window selection not frozen in advance."""

    frozen = tuple(frozen_window_ids)
    evaluated = tuple(evaluated_window_ids)
    reported = tuple(reported_window_ids)
    findings: list[Finding] = []

    for label, values in (
        ("frozen", frozen),
        ("evaluated", evaluated),
        ("reported", reported),
    ):
        duplicates = _duplicates(values)
        if duplicates:
            findings.append(
                Finding(
                    "WINDOW_DUPLICATE",
                    label,
                    f"window list contains duplicates: {_display(duplicates)}",
                )
            )

    if not frozen:
        findings.append(
            Finding("NO_FROZEN_WINDOWS", "frozen", "no preregistered windows exist")
        )
        return tuple(sorted(findings))

    frozen_set = set(frozen)
    for label, values in (("evaluated", evaluated), ("reported", reported)):
        value_set = set(values)
        missing = tuple(item for item in frozen if item not in value_set)
        unexpected = tuple(item for item in values if item not in frozen_set)
        if missing or unexpected or len(values) != len(frozen):
            findings.append(
                Finding(
                    "POST_HOC_WINDOW_SUBSET",
                    label,
                    f"missing [{_display(missing)}], unexpected [{_display(unexpected)}]",
                )
            )

    if frozen_primary_window is not None and frozen_primary_window not in frozen_set:
        findings.append(
            Finding(
                "INVALID_FROZEN_PRIMARY_WINDOW",
                "frozen",
                f"primary {frozen_primary_window!r} is not in frozen windows",
            )
        )
    if frozen_primary_window is None:
        if reported_primary_window is not None:
            findings.append(
                Finding(
                    "POST_HOC_PRIMARY_WINDOW",
                    "reported",
                    f"reported primary {reported_primary_window!r} was not preregistered",
                )
            )
    elif reported_primary_window != frozen_primary_window:
        findings.append(
            Finding(
                "POST_HOC_PRIMARY_WINDOW",
                "reported",
                f"reported {reported_primary_window!r}, frozen {frozen_primary_window!r}",
            )
        )
    if not frozen_selection_rule or not reported_selection_rule:
        findings.append(
            Finding(
                "WINDOW_SELECTION_RULE_INVALID",
                "selection_rule",
                "frozen and reported selection rules must be nonempty",
            )
        )
    elif reported_selection_rule != frozen_selection_rule:
        findings.append(
            Finding(
                "POST_HOC_WINDOW_SELECTION_RULE",
                "reported",
                f"reported {reported_selection_rule!r}, frozen {frozen_selection_rule!r}",
            )
        )

    return tuple(sorted(findings))


def audit_reference_identity(
    arm_references: Mapping[str, ReferenceIdentity],
    *,
    required_arms: Sequence[str] | None = None,
) -> tuple[Finding, ...]:
    """Require all compared arms to use the exact same reference identity."""

    findings: list[Finding] = []
    required = (
        tuple(required_arms)
        if required_arms is not None
        else tuple(sorted(arm_references))
    )
    if not required:
        return (
            Finding("NO_REFERENCE_ARMS", "references", "no arm references supplied"),
        )

    for arm in required:
        if arm not in arm_references:
            findings.append(
                Finding("REFERENCE_MISSING", arm, "arm has no reference identity")
            )

    present = [arm for arm in required if arm in arm_references]
    if not present:
        return tuple(sorted(findings))

    for arm in present:
        identity = arm_references[arm]
        if (
            not identity.frame_id
            or not identity.projection_model_id
            or len(identity.anchor_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in identity.anchor_sha256
            )
            or isinstance(identity.epoch_ns, bool)
            or not isinstance(identity.epoch_ns, int)
        ):
            findings.append(
                Finding(
                    "REFERENCE_IDENTITY_INVALID",
                    arm,
                    "reference identity fields are not canonical",
                )
            )

    baseline_arm = present[0]
    baseline = arm_references[baseline_arm]
    for arm in present[1:]:
        reference = arm_references[arm]
        if reference != baseline:
            differing = [
                field
                for field in (
                    "frame_id",
                    "epoch_ns",
                    "anchor_sha256",
                    "projection_model_id",
                )
                if getattr(reference, field) != getattr(baseline, field)
            ]
            findings.append(
                Finding(
                    "REFERENCE_MISMATCH",
                    arm,
                    f"differs from {baseline_arm} in {', '.join(differing)}",
                )
            )
    return tuple(sorted(findings))


def merge_findings(*groups: Iterable[Finding]) -> tuple[Finding, ...]:
    """Flatten, de-duplicate, and deterministically order audit findings."""

    return tuple(sorted({finding for group in groups for finding in group}))


def require_clean(findings: Iterable[Finding]) -> None:
    """Promote any finding to a single fail-closed exception."""

    materialized = tuple(findings)
    if materialized:
        raise RedTeamContractError(materialized)
