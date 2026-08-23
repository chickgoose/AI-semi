"""Fail-closed dual-time records for observational transport latency.

The source event timestamp and the transport cycles belong to different time
domains.  This module preserves all three inputs verbatim and derives a fourth
timestamp by adding only the observed transport latency.  It does not replay a
workload cycle number as physical time and does not invoke a CAV evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass


TRANSPORT_TIME_SEMANTICS = "TRANSPORT_LATENCY_INJECTION_NOT_PHYSICAL_REPLAY"


class TransportTimeValidationError(ValueError):
    """A dual-time value is ambiguous, mutated, or uses a fractional-ns clock."""


def _nonnegative_integer(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        raise TransportTimeValidationError(
            "%s must be a non-negative integer" % where
        )
    return value


def _whole_ns_clock(clock_period_ps: object) -> int:
    period_ps = _nonnegative_integer(clock_period_ps, "clock_period_ps")
    if period_ps == 0:
        raise TransportTimeValidationError("clock_period_ps must be positive")
    if period_ps % 1000 != 0:
        raise TransportTimeValidationError(
            "clock_period_ps must be a whole number of nanoseconds"
        )
    return period_ps // 1000


@dataclass(frozen=True)
class DualTimeEvent:
    """One source timestamp plus an independently observed transport interval."""

    event_timestamp_ns: int
    occurrence_cycle: int
    retire_cycle: int
    clock_period_ps: int
    latency_cycles: int
    latency_ns: int
    derived_retire_timestamp_ns: int
    semantics_label: str = TRANSPORT_TIME_SEMANTICS

    def __post_init__(self) -> None:
        validate_dual_time_event(self)

    @property
    def clock_ns(self) -> int:
        """Return the already-validated integral clock period in nanoseconds."""

        return self.clock_period_ps // 1000


def validate_dual_time_event(value: object) -> DualTimeEvent:
    """Validate exact preservation and derivation of one dual-time record."""

    if type(value) is not DualTimeEvent:
        raise TransportTimeValidationError("value must be an exact DualTimeEvent")
    event_timestamp_ns = _nonnegative_integer(
        value.event_timestamp_ns, "event_timestamp_ns"
    )
    occurrence_cycle = _nonnegative_integer(
        value.occurrence_cycle, "occurrence_cycle"
    )
    retire_cycle = _nonnegative_integer(value.retire_cycle, "retire_cycle")
    clock_ns = _whole_ns_clock(value.clock_period_ps)
    latency_cycles = _nonnegative_integer(value.latency_cycles, "latency_cycles")
    latency_ns = _nonnegative_integer(value.latency_ns, "latency_ns")
    derived = _nonnegative_integer(
        value.derived_retire_timestamp_ns, "derived_retire_timestamp_ns"
    )
    if retire_cycle < occurrence_cycle:
        raise TransportTimeValidationError(
            "retire_cycle must not precede occurrence_cycle"
        )
    expected_latency = retire_cycle - occurrence_cycle
    if latency_cycles != expected_latency:
        raise TransportTimeValidationError(
            "latency_cycles must equal retire_cycle - occurrence_cycle"
        )
    expected_latency_ns = latency_cycles * clock_ns
    if latency_ns != expected_latency_ns:
        raise TransportTimeValidationError(
            "latency_ns must equal latency_cycles * clock_ns"
        )
    expected_derived = event_timestamp_ns + latency_ns
    if derived != expected_derived:
        raise TransportTimeValidationError(
            "derived_retire_timestamp_ns must inject only transport latency"
        )
    if value.semantics_label != TRANSPORT_TIME_SEMANTICS:
        raise TransportTimeValidationError("transport-time semantics label differs")
    return value


def build_dual_time_event(
    event_timestamp_ns: int,
    occurrence_cycle: int,
    retire_cycle: int,
    clock_period_ps: int,
) -> DualTimeEvent:
    """Build a record without interpreting absolute workload cycles as time."""

    timestamp = _nonnegative_integer(event_timestamp_ns, "event_timestamp_ns")
    occurrence = _nonnegative_integer(occurrence_cycle, "occurrence_cycle")
    retire = _nonnegative_integer(retire_cycle, "retire_cycle")
    clock_ns = _whole_ns_clock(clock_period_ps)
    if retire < occurrence:
        raise TransportTimeValidationError(
            "retire_cycle must not precede occurrence_cycle"
        )
    latency = retire - occurrence
    return DualTimeEvent(
        event_timestamp_ns=timestamp,
        occurrence_cycle=occurrence,
        retire_cycle=retire,
        clock_period_ps=clock_period_ps,
        latency_cycles=latency,
        latency_ns=latency * clock_ns,
        derived_retire_timestamp_ns=timestamp + latency * clock_ns,
    )


__all__ = (
    "DualTimeEvent",
    "TRANSPORT_TIME_SEMANTICS",
    "TransportTimeValidationError",
    "build_dual_time_event",
    "validate_dual_time_event",
)
