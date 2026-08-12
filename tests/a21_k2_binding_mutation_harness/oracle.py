#!/usr/bin/env python3
"""Independent black-box oracle for the normalized N16/K2 binding seam.

The oracle deliberately keeps one global FIFO of accepted event identities.
It never uses a per-source scoreboard to establish retirement order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


SCHEMA = "a21_k2_binding_trace_v1"
SOURCE_COUNT = 16
LANES = 2


class TraceContractError(ValueError):
    """The stimulus or observation document is malformed."""


class BindingViolation(AssertionError):
    """A black-box observation violates the normalized binding contract."""

    def __init__(self, code: str, cycle: int, detail: str) -> None:
        self.code = code
        self.cycle = cycle
        self.detail = detail
        super().__init__(f"{code} cycle={cycle} {detail}")


@dataclass(frozen=True)
class Event:
    source: int
    event_id: str
    payload: int

    @classmethod
    def parse(cls, value: Any, context: str) -> "Event":
        if not isinstance(value, dict) or set(value) != {
            "source", "event_id", "payload"
        }:
            raise TraceContractError(f"{context}: malformed event")
        source = value["source"]
        event_id = value["event_id"]
        payload = value["payload"]
        if not isinstance(source, int) or not 0 <= source < SOURCE_COUNT:
            raise TraceContractError(f"{context}: source outside N16")
        if not isinstance(event_id, str) or not event_id:
            raise TraceContractError(f"{context}: event_id must be nonempty")
        if not isinstance(payload, int) or not 0 <= payload < (1 << 16):
            raise TraceContractError(f"{context}: payload outside 16 bits")
        return cls(source, event_id, payload)

    def document(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "event_id": self.event_id,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ValidationReport:
    binding: str
    cycles: int
    accepted: int
    retired: int
    flattened_accept_order: tuple[str, ...]
    flattened_retire_order: tuple[str, ...]


def invalid_outputs() -> list[dict[str, Any]]:
    return [{"lane": 0, "valid": False}, {"lane": 1, "valid": False}]


def output_record(lane: int, event: Event | None) -> dict[str, Any]:
    if event is None:
        return {"lane": lane, "valid": False}
    return {"lane": lane, "valid": True, **event.document()}


def _parse_ready(value: Any, context: str) -> tuple[bool, bool]:
    if (
        not isinstance(value, list)
        or len(value) != LANES
        or any(not isinstance(item, bool) for item in value)
    ):
        raise TraceContractError(f"{context}: retire_ready must be two booleans")
    return value[0], value[1]


def _parse_source_ready(value: Any, context: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(not isinstance(item, int) for item in value):
        raise TraceContractError(f"{context}: source_ready must be an integer list")
    if any(not 0 <= item < SOURCE_COUNT for item in value) or len(set(value)) != len(value):
        raise TraceContractError(f"{context}: invalid source_ready set")
    return tuple(sorted(value))


def _parse_outputs(value: Any, context: str) -> tuple[Event | None, Event | None]:
    if not isinstance(value, list) or len(value) != LANES:
        raise TraceContractError(f"{context}: outputs must contain two lanes")
    parsed: list[Event | None] = []
    for lane, record in enumerate(value):
        if not isinstance(record, dict) or record.get("lane") != lane:
            raise TraceContractError(f"{context}: output lane sequence mismatch")
        if not isinstance(record.get("valid"), bool):
            raise TraceContractError(f"{context}: output valid must be boolean")
        if record["valid"]:
            if set(record) != {"lane", "valid", "source", "event_id", "payload"}:
                raise TraceContractError(f"{context}: valid lane fields mismatch")
            event_fields = {key: record[key] for key in ("source", "event_id", "payload") if key in record}
            parsed.append(Event.parse(event_fields, f"{context}.lane{lane}"))
        else:
            if set(record) != {"lane", "valid"}:
                raise TraceContractError(f"{context}: invalid lane carries event fields")
            parsed.append(None)
    return parsed[0], parsed[1]


def validate_stimulus(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise TraceContractError(f"stimulus schema must be {SCHEMA}")
    if set(document) != {"schema", "name", "source_count", "retire_lanes", "cycles"}:
        raise TraceContractError("stimulus document fields mismatch")
    if not isinstance(document["name"], str) or not document["name"]:
        raise TraceContractError("stimulus name must be nonempty")
    if document.get("source_count") != SOURCE_COUNT or document.get("retire_lanes") != LANES:
        raise TraceContractError("stimulus must use N16/K2")
    cycles = document.get("cycles")
    if not isinstance(cycles, list) or not cycles:
        raise TraceContractError("stimulus requires cycles")
    seen_ids: dict[str, Event] = {}
    normalized: list[dict[str, Any]] = []
    for index, cycle in enumerate(cycles):
        context = f"stimulus cycle {index}"
        if not isinstance(cycle, dict) or cycle.get("cycle") != index:
            raise TraceContractError(f"{context}: noncontiguous cycle index")
        if set(cycle) != {"cycle", "reset_n", "retire_ready", "offer", "source_live"}:
            raise TraceContractError(f"{context}: fields mismatch")
        if not isinstance(cycle.get("reset_n"), bool):
            raise TraceContractError(f"{context}: reset_n must be boolean")
        ready = _parse_ready(cycle.get("retire_ready"), context)
        offer_value = cycle.get("offer")
        live_value = cycle.get("source_live")
        if not isinstance(offer_value, list) or len(offer_value) > LANES:
            raise TraceContractError(f"{context}: offer count outside K2")
        if not isinstance(live_value, list):
            raise TraceContractError(f"{context}: source_live must be a list")
        offer = tuple(Event.parse(item, f"{context}.offer") for item in offer_value)
        live = tuple(Event.parse(item, f"{context}.source_live") for item in live_value)
        if len({item.source for item in offer}) != len(offer):
            raise TraceContractError(f"{context}: duplicate source in atomic offer")
        if len({item.source for item in live}) != len(live):
            raise TraceContractError(f"{context}: duplicate live source")
        live_by_source = {item.source: item for item in live}
        if any(live_by_source.get(item.source) != item for item in offer):
            raise TraceContractError(f"{context}: offer is not an ordered subset of source_live")
        if not cycle["reset_n"] and (offer or live):
            raise TraceContractError(f"{context}: reset cycle must not carry live work")
        for item in live:
            previous = seen_ids.get(item.event_id)
            if previous is not None and previous != item:
                raise TraceContractError(
                    f"{context}: event_id {item.event_id} changed source or payload"
                )
            # A stable event may remain live until it is accepted.
            seen_ids[item.event_id] = item
        normalized.append({
            "cycle": index,
            "reset_n": cycle["reset_n"],
            "retire_ready": ready,
            "offer": offer,
            "source_live": live,
        })
    return normalized


def validate_observations(document: Any, binding: str, cycles: int) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise TraceContractError(f"observation schema must be {SCHEMA}")
    if set(document) != {"schema", "binding", "cycles"}:
        raise TraceContractError("observation document fields mismatch")
    if document.get("binding") != binding:
        raise TraceContractError("observation binding identity mismatch")
    rows = document.get("cycles")
    if not isinstance(rows, list) or len(rows) != cycles:
        raise TraceContractError("observation cycle count mismatch")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        context = f"observation cycle {index}"
        if not isinstance(row, dict) or row.get("cycle") != index:
            raise TraceContractError(f"{context}: noncontiguous cycle index")
        if set(row) != {"cycle", "offer_ready", "source_ready", "outputs", "drain_idle"}:
            raise TraceContractError(f"{context}: fields mismatch")
        if not isinstance(row.get("offer_ready"), bool):
            raise TraceContractError(f"{context}: offer_ready must be boolean")
        if not isinstance(row.get("drain_idle"), bool):
            raise TraceContractError(f"{context}: drain_idle must be boolean")
        normalized.append({
            "cycle": index,
            "offer_ready": row["offer_ready"],
            "source_ready": _parse_source_ready(row.get("source_ready"), context),
            "outputs": _parse_outputs(row.get("outputs"), context),
            "drain_idle": row["drain_idle"],
        })
    return normalized


def _expected_outputs(queue: list[Event], ready: tuple[bool, bool]) -> tuple[Event | None, Event | None]:
    lane0 = queue[0] if queue else None
    lane1 = queue[1] if len(queue) == 2 and ready[0] and ready[1] else None
    return lane0, lane1


def _retire_count(queue: list[Event], ready: tuple[bool, bool]) -> int:
    if not queue or not ready[0]:
        return 0
    return 2 if len(queue) == 2 and ready[1] else 1


def _event_ids(events: Iterable[Event | None]) -> tuple[str | None, ...]:
    return tuple(event.event_id if event is not None else None for event in events)


def validate_trace(stimulus_document: Any, observation_document: Any, binding: str) -> ValidationReport:
    """Validate a complete pin trace using a candidate-independent link model."""

    stimulus = validate_stimulus(stimulus_document)
    observed = validate_observations(observation_document, binding, len(stimulus))
    queue: list[Event] = []
    accepted_flat: list[Event] = []
    retired_flat: list[Event] = []
    retired_ids: set[str] = set()
    reset_aborted_ids: set[str] = set()
    previous_outputs: tuple[Event | None, Event | None] = (None, None)
    previous_ready = (True, True)

    for cycle, (inputs, actual) in enumerate(zip(stimulus, observed, strict=True)):
        if not inputs["reset_n"]:
            reset_aborted_ids.update(item.event_id for item in queue)
            queue.clear()
            if actual["source_ready"] or any(actual["outputs"]):
                raise BindingViolation("RESET_NOT_QUIET", cycle, "ready/valid asserted during reset")
            previous_outputs = actual["outputs"]
            previous_ready = inputs["retire_ready"]
            continue

        expected_outputs = _expected_outputs(queue, inputs["retire_ready"])
        retire_count = _retire_count(queue, inputs["retire_ready"])
        remaining = queue[retire_count:]
        offer = inputs["offer"]
        expected_offer_ready = len(offer) <= LANES - len(remaining)
        expected_sources = tuple(sorted(item.source for item in offer)) if offer and expected_offer_ready else ()

        if len(offer) == 2 and not expected_offer_ready and (
            actual["offer_ready"] or actual["source_ready"]
        ):
            raise BindingViolation(
                "OVERFLOW_DROP", cycle,
                f"count2 accepted with capacity={LANES - len(remaining)}",
            )
        if actual["offer_ready"] != expected_offer_ready:
            raise BindingViolation(
                "OFFER_READY_MISMATCH", cycle,
                f"expected={expected_offer_ready} actual={actual['offer_ready']}",
            )
        if len(offer) == 2 and actual["offer_ready"] and len(actual["source_ready"]) == 1:
            raise BindingViolation(
                "PARTIAL_COUNT2_ACCEPT", cycle,
                f"offer={[item.source for item in offer]} ready={actual['source_ready']}",
            )
        if actual["source_ready"] != expected_sources:
            raise BindingViolation(
                "WRONG_SOURCE_READY", cycle,
                f"expected={expected_sources} actual={actual['source_ready']}",
            )

        # A held valid lane is checked before content/order comparisons so a
        # mutation cannot hide as a generic wrong-event failure.
        for lane in range(LANES):
            held = previous_outputs[lane]
            if held is not None and not previous_ready[lane] and actual["outputs"][lane] != held:
                raise BindingViolation(
                    "UNSTABLE_STALL", cycle,
                    f"lane={lane} held={held.event_id} actual={_event_ids(actual['outputs'])[lane]}",
                )

        if (
            actual["outputs"][1] is not None
            and queue
            and not inputs["retire_ready"][0]
        ):
            raise BindingViolation(
                "YOUNGER_BYPASS", cycle,
                f"head={queue[0].event_id} younger={actual['outputs'][1].event_id}",
            )

        actual_ids = _event_ids(actual["outputs"])
        expected_ids = _event_ids(expected_outputs)
        for item in actual["outputs"]:
            if item is not None and item.event_id in reset_aborted_ids:
                raise BindingViolation("STALE_RESET", cycle, f"event={item.event_id}")
        presented_ids = [item.event_id for item in actual["outputs"] if item is not None]
        if len(presented_ids) != len(set(presented_ids)):
            raise BindingViolation("DUPLICATE_RETIRE", cycle, f"events={presented_ids}")
        for item in actual["outputs"]:
            if item is not None and item.event_id in retired_ids:
                raise BindingViolation("DUPLICATE_RETIRE", cycle, f"event={item.event_id}")
        if actual_ids != expected_ids:
            if sorted(item for item in actual_ids if item is not None) == sorted(
                item for item in expected_ids if item is not None
            ) and actual_ids != expected_ids:
                raise BindingViolation(
                    "GLOBAL_ORDER_MISMATCH", cycle,
                    f"expected={expected_ids} actual={actual_ids}",
                )
            if any(
                expected is not None and actual is None
                for expected, actual in zip(expected_ids, actual_ids, strict=True)
            ):
                raise BindingViolation(
                    "LATENCY_SHIFT", cycle,
                    f"expected={expected_ids} actual={actual_ids}",
                )
            raise BindingViolation(
                "GLOBAL_ORDER_MISMATCH", cycle,
                f"expected={expected_ids} actual={actual_ids}",
            )
        if actual["outputs"] != expected_outputs:
            raise BindingViolation(
                "RETIRE_CONTENT_MISMATCH", cycle,
                f"expected={expected_outputs} actual={actual['outputs']}",
            )

        expected_drain = not queue and not inputs["source_live"] and not offer
        if actual["drain_idle"] and not expected_drain:
            raise BindingViolation(
                "EARLY_DRAIN", cycle,
                f"buffered={len(queue)} live={len(inputs['source_live'])}",
            )
        if actual["drain_idle"] != expected_drain:
            raise BindingViolation(
                "DRAIN_IDLE_MISMATCH", cycle,
                f"expected={expected_drain} actual={actual['drain_idle']}",
            )

        for lane, item in enumerate(actual["outputs"]):
            if item is not None and inputs["retire_ready"][lane]:
                retired_flat.append(item)
                retired_ids.add(item.event_id)

        queue = remaining
        if offer and expected_offer_ready:
            queue.extend(offer)
            accepted_flat.extend(offer)
        previous_outputs = actual["outputs"]
        previous_ready = inputs["retire_ready"]

    live_accepted = [item.event_id for item in accepted_flat if item.event_id not in reset_aborted_ids]
    actual_retired = [item.event_id for item in retired_flat]
    if actual_retired != live_accepted:
        raise BindingViolation(
            "FINAL_GLOBAL_ORDER", len(stimulus),
            f"accepted={live_accepted} retired={actual_retired}",
        )
    if queue:
        raise BindingViolation("NOT_DRAINED", len(stimulus), f"queued={[item.event_id for item in queue]}")
    return ValidationReport(
        binding=binding,
        cycles=len(stimulus),
        accepted=len(accepted_flat),
        retired=len(retired_flat),
        flattened_accept_order=tuple(item.event_id for item in accepted_flat),
        flattened_retire_order=tuple(item.event_id for item in retired_flat),
    )


def legacy_per_source_scoreboard_passes(
    accepted: Iterable[Event], retired: Iterable[Event]
) -> bool:
    """Model the blind spot that motivated this harness.

    Each source is compared independently, so swaps between different sources
    are invisible even though the flattened global transaction order changed.
    """

    accepted_by_source: dict[int, list[str]] = {source: [] for source in range(SOURCE_COUNT)}
    retired_by_source: dict[int, list[str]] = {source: [] for source in range(SOURCE_COUNT)}
    for item in accepted:
        accepted_by_source[item.source].append(item.event_id)
    for item in retired:
        retired_by_source[item.source].append(item.event_id)
    return accepted_by_source == retired_by_source
