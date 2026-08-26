"""Hardware-polarity extension around the sealed legacy functional assay.

This module does not alter the address-only ``functional_assay`` API or its
result bytes.  It accepts an explicitly versioned projection of a delivered
``transport_outcome/v2`` row, checks hardware-observed polarity against the
source event before invoking legacy geometry, and returns the untouched legacy
result with a separate hardware-carried polarity sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple

from . import functional_assay as legacy
from .functional_source import FunctionalSourceBundle
from .native_outcome_bundle import NativeOutcome


TRANSPORT_OUTCOME_POLARITY_SCHEMA = (
    "redred.cluster2_cav_bridge.transport_outcome/v2"
)
HARDWARE_CARRIED_POLARITY = "HARDWARE_CARRIED_POLARITY"

_ADAPTER_FIELDS = frozenset((
    "schema", "event_id", "source", "occurrence_cycle", "retire_cycle",
    "latency", "retire_polarity",
))
_V2_ROW_FIELDS = frozenset((
    "schema", "event_id", "source_index", "occurrence_cycle", "outcome",
    "retire_cycle", "retire_native_lane", "retire_row", "retire_col",
    "retire_polarity",
))
_OBSERVATION_FIELDS = frozenset((
    "event_id", "source_index", "native_occurrence_cycle",
    "hardware_observed_polarity", "semantics_label",
))
_RESULT_FIELDS = frozenset(("legacy_result", "hardware_polarity_sidecar"))


class PolarityFunctionalAssayError(ValueError):
    """A v2 adapter, polarity join, or wrapped result is inconsistent."""


def _fail(message: str) -> None:
    raise PolarityFunctionalAssayError(message)


def _exact_fields(
    value: object, expected_type: type, expected_fields: frozenset, where: str
) -> None:
    if type(value) is not expected_type:
        _fail("%s must have its exact polarity-assay type" % where)
    try:
        fields = frozenset(vars(value))
    except TypeError as error:
        raise PolarityFunctionalAssayError(
            "%s has no field mapping" % where
        ) from error
    if fields != expected_fields:
        _fail("%s field schema differs" % where)


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        _fail("%s must be a non-negative integer" % where)
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class HardwarePolarityOutcomeV2:
    """Functional projection of one delivered transport_outcome/v2 row."""

    schema: str
    event_id: int
    source: int
    occurrence_cycle: int
    retire_cycle: int
    latency: int
    retire_polarity: int

    def __post_init__(self) -> None:
        _exact_fields(
            self, HardwarePolarityOutcomeV2, _ADAPTER_FIELDS,
            "hardware polarity outcome",
        )
        if (
            type(self.schema) is not str
            or self.schema != TRANSPORT_OUTCOME_POLARITY_SCHEMA
        ):
            _fail("hardware polarity outcome schema differs from transport_outcome/v2")
        for name in (
            "event_id", "source", "occurrence_cycle", "retire_cycle",
            "latency", "retire_polarity",
        ):
            _nonnegative_int(
                getattr(self, name), "hardware polarity outcome %s" % name
            )
        if self.source > 15:
            _fail("hardware polarity outcome source exceeds 15")
        if self.retire_cycle <= self.occurrence_cycle:
            _fail("hardware polarity outcome retirement must follow occurrence")
        if self.latency != self.retire_cycle - self.occurrence_cycle:
            _fail("hardware polarity outcome latency differs from retire-occurrence")
        if self.retire_polarity > 1:
            _fail("hardware polarity outcome retire_polarity must be 0 or 1")

    @classmethod
    def from_transport_outcome_v2(
        cls, value: Mapping[str, object]
    ) -> "HardwarePolarityOutcomeV2":
        """Adapt an exact delivered v2 contract row without source inference."""

        if not isinstance(value, Mapping) or frozenset(value) != _V2_ROW_FIELDS:
            _fail("transport_outcome/v2 adapter input fields differ")
        if value["schema"] != TRANSPORT_OUTCOME_POLARITY_SCHEMA:
            _fail("transport_outcome/v2 adapter schema differs")
        if value["outcome"] != "DELIVERED":
            _fail("polarity assay accepts only delivered transport outcomes")
        retire_cycle = _nonnegative_int(value["retire_cycle"], "retire_cycle")
        occurrence_cycle = _nonnegative_int(
            value["occurrence_cycle"], "occurrence_cycle"
        )
        return cls(
            value["schema"],  # type: ignore[arg-type]
            _nonnegative_int(value["event_id"], "event_id"),
            _nonnegative_int(value["source_index"], "source_index"),
            occurrence_cycle,
            retire_cycle,
            retire_cycle - occurrence_cycle,
            _nonnegative_int(value["retire_polarity"], "retire_polarity"),
        )

    def legacy_outcome(self) -> NativeOutcome:
        """Project v1 transport fields, deliberately excluding polarity."""

        return NativeOutcome(
            self.event_id,
            self.source,
            self.occurrence_cycle,
            self.retire_cycle,
            self.latency,
        )


@dataclass(frozen=True)
class HardwarePolarityObservation:
    """One source-checked, hardware-carried polarity observation."""

    event_id: int
    source_index: int
    native_occurrence_cycle: int
    hardware_observed_polarity: int
    semantics_label: str

    def __post_init__(self) -> None:
        _exact_fields(
            self, HardwarePolarityObservation, _OBSERVATION_FIELDS,
            "hardware polarity observation",
        )
        _nonnegative_int(self.event_id, "hardware polarity event_id")
        source = _nonnegative_int(self.source_index, "hardware polarity source")
        if source > 15:
            _fail("hardware polarity source exceeds 15")
        _nonnegative_int(
            self.native_occurrence_cycle, "hardware polarity occurrence cycle"
        )
        polarity = _nonnegative_int(
            self.hardware_observed_polarity, "hardware-observed polarity"
        )
        if polarity > 1:
            _fail("hardware-observed polarity must be 0 or 1")
        if (
            type(self.semantics_label) is not str
            or self.semantics_label != HARDWARE_CARRIED_POLARITY
        ):
            _fail("hardware polarity semantics differ")


@dataclass(frozen=True)
class HardwarePolarityAssayResult:
    """Untouched legacy result plus separately proven hardware polarity."""

    legacy_result: legacy.FunctionalAssayResult
    hardware_polarity_sidecar: Tuple[HardwarePolarityObservation, ...]

    def __post_init__(self) -> None:
        _exact_fields(
            self, HardwarePolarityAssayResult, _RESULT_FIELDS,
            "hardware polarity assay result",
        )
        if type(self.legacy_result) is not legacy.FunctionalAssayResult:
            _fail("hardware polarity result has an invalid legacy result")
        if (
            type(self.hardware_polarity_sidecar) is not tuple
            or any(
                type(row) is not HardwarePolarityObservation
                for row in self.hardware_polarity_sidecar
            )
        ):
            _fail("hardware polarity sidecar must be an exact observation tuple")
        for row in self.hardware_polarity_sidecar:
            HardwarePolarityObservation(**vars(row))
        legacy_keys = tuple(
            (row.event_id, row.source_index, row.native_occurrence_cycle)
            for row in self.legacy_result.retire_sidecar
        )
        polarity_keys = tuple(
            (row.event_id, row.source_index, row.native_occurrence_cycle)
            for row in self.hardware_polarity_sidecar
        )
        if polarity_keys != legacy_keys:
            _fail("hardware polarity sidecar differs from legacy retirement order")

    @property
    def geometry(self):
        return self.legacy_result.geometry

    @property
    def retire_sidecar(self):
        return self.legacy_result.retire_sidecar

    @property
    def statistics(self):
        return self.legacy_result.statistics

    @property
    def views(self):
        return self.legacy_result.views


def run_hardware_polarity_functional_assay(
    source: FunctionalSourceBundle,
    hardware_outcomes: Sequence[HardwarePolarityOutcomeV2],
) -> HardwarePolarityAssayResult:
    """Check v2 polarity, then call the byte-sealed legacy assay unchanged."""

    if type(source) is not FunctionalSourceBundle:
        _fail("source must be an exact FunctionalSourceBundle")
    if (
        type(hardware_outcomes) is not tuple
        or len(hardware_outcomes) != legacy.EXPECTED_EVENT_COUNT
    ):
        _fail(
            "hardware polarity outcomes must be the exact %d-row tuple"
            % legacy.EXPECTED_EVENT_COUNT
        )
    if any(type(row) is not HardwarePolarityOutcomeV2 for row in hardware_outcomes):
        _fail("hardware polarity outcomes contain an invalid adapter type")
    for row in hardware_outcomes:
        HardwarePolarityOutcomeV2(**vars(row))
    if tuple(row.event_id for row in hardware_outcomes) != tuple(
        range(legacy.EXPECTED_EVENT_COUNT)
    ):
        _fail("hardware polarity outcome IDs/order are not exactly contiguous")
    by_id = dict((row.event_id, row) for row in hardware_outcomes)
    if type(source.events) is not tuple or len(source.events) != len(by_id):
        _fail("source event population differs from hardware polarity outcomes")
    for event in source.events:
        outcome = by_id.get(event.event_id)
        if outcome is None:
            _fail("source event lacks a hardware polarity outcome")
        if event.polarity != outcome.retire_polarity:
            _fail("hardware-observed polarity differs from source polarity")

    legacy_outcomes = tuple(row.legacy_outcome() for row in hardware_outcomes)
    try:
        legacy_result = legacy.run_functional_assay(source, legacy_outcomes)
    except legacy.FunctionalAssayError as error:
        raise PolarityFunctionalAssayError(
            "legacy functional assay rejected v2 transport projection"
        ) from error
    hardware_sidecar = tuple(
        HardwarePolarityObservation(
            row.event_id,
            row.source_index,
            row.native_occurrence_cycle,
            by_id[row.event_id].retire_polarity,
            HARDWARE_CARRIED_POLARITY,
        )
        for row in legacy_result.retire_sidecar
    )
    return HardwarePolarityAssayResult(legacy_result, hardware_sidecar)


def validate_hardware_polarity_assay_result(
    result: HardwarePolarityAssayResult,
    source: FunctionalSourceBundle,
    hardware_outcomes: Sequence[HardwarePolarityOutcomeV2],
) -> HardwarePolarityAssayResult:
    """Bind a wrapper result to an exact replay of its v2 adapter inputs."""

    if type(result) is not HardwarePolarityAssayResult:
        _fail("result must be an exact HardwarePolarityAssayResult")
    expected = run_hardware_polarity_functional_assay(source, hardware_outcomes)
    if result != expected:
        _fail("hardware polarity assay result differs from exact input replay")
    return result


__all__ = (
    "HARDWARE_CARRIED_POLARITY",
    "HardwarePolarityAssayResult",
    "HardwarePolarityObservation",
    "HardwarePolarityOutcomeV2",
    "PolarityFunctionalAssayError",
    "TRANSPORT_OUTCOME_POLARITY_SCHEMA",
    "run_hardware_polarity_functional_assay",
    "validate_hardware_polarity_assay_result",
)
