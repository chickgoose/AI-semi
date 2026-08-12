#!/usr/bin/env python3
"""Independent atomic-bundle oracles for three proposed weighted K2 schedulers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Iterable, Optional, Sequence


WEIGHTS = (1, 5, 5, 1)
CENTER_MASK = 0b0110
PERIPH_MASK = 0b1001

CONTRACT_SCALAR_PREFIX = "exact_weighted_scalar_prefix_k2"
CONTRACT_BATCHED_IWRR = "batched_iwrr_k2"
# This deliberately does not claim A4's unbound cortical-column relationship.
CONTRACT_PAIRED_ROW_PROPOSAL = "paired_row_calendar_proposal_k2"
CONTRACTS = (
    CONTRACT_SCALAR_PREFIX,
    CONTRACT_BATCHED_IWRR,
    CONTRACT_PAIRED_ROW_PROPOSAL,
)

# Exact A2 owner calendar, paired into six atomic phases.
BATCHED_IWRR_ROWS = (1, 2, 0, 1, 2, 3, 1, 2, 1, 2, 1, 2)
# Engineering proposal only: fixed row-token pairing, with no column-pair claim.
PAIRED_ROW_PROPOSAL_ROWS = (0, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 3)


class ContractViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class Arbiter2State:
    last_grant: int = 1


@dataclass(frozen=True)
class Arbiter4State:
    low: Arbiter2State = Arbiter2State()
    high: Arbiter2State = Arbiter2State()
    top: Arbiter2State = Arbiter2State()


@dataclass(frozen=True)
class FoveaState:
    center: Arbiter4State = Arbiter4State()
    peripheral: Arbiter4State = Arbiter4State()
    column: Arbiter4State = Arbiter4State()
    round_index: int = 0


@dataclass(frozen=True)
class CalendarState:
    phase: int = 0
    column_rr: tuple[int, int, int, int] = (0, 0, 0, 0)


PolicyState = FoveaState | CalendarState


@dataclass(frozen=True)
class Bundle:
    grant_count: int
    addresses: tuple[Optional[int], Optional[int]]
    request_snapshot: int
    next_policy: PolicyState
    nominal_rows: tuple[Optional[int], Optional[int]]
    extra_sources: tuple[int, ...] = ()


@dataclass(frozen=True)
class CycleInput:
    request: int = 0
    bundle_ready: bool = True
    reset: bool = False
    # Not visible to the correct oracle; used to falsify future-arrival sampling.
    future_request: int = 0


@dataclass(frozen=True)
class Observation:
    contract: str
    cycle: int
    reset: bool
    request_snapshot: int
    bundle_ready: bool
    grant_count: int
    addresses: tuple[Optional[int], Optional[int]]
    committed: tuple[Optional[int], Optional[int]]
    held_after: tuple[Optional[int], Optional[int]]
    nominal_rows: tuple[Optional[int], Optional[int]]
    extra_sources: tuple[int, ...]
    policy_before: dict
    policy_after: dict

    def json_dict(self) -> dict:
        return asdict(self)


def _arbiter2(req: int, state: Arbiter2State) -> tuple[int, Arbiter2State]:
    req &= 0b11
    prefer_one = state.last_grant == 0
    grant_one = bool(req & 0b10) and (prefer_one or not bool(req & 0b01))
    grant_zero = bool(req & 0b01) and not grant_one
    grant = int(grant_zero) | (int(grant_one) << 1)
    return grant, state if req == 0 else Arbiter2State((grant >> 1) & 1)


def _arbiter4(req: int, state: Arbiter4State) -> tuple[int, Arbiter4State]:
    req &= 0xF
    low_grant, low_next = _arbiter2(req & 3, state.low)
    high_grant, high_next = _arbiter2((req >> 2) & 3, state.high)
    groups = int(bool(req & 3)) | (int(bool(req & 0xC)) << 1)
    group_grant, top_next = _arbiter2(groups, state.top)
    grant = low_grant if group_grant & 1 else 0
    if group_grant & 2:
        grant |= high_grant << 2
    return grant, Arbiter4State(low_next, high_next, top_next)


def _onehot_index(bits: int) -> int:
    if bits == 0 or bits & (bits - 1):
        raise ContractViolation(f"ONEHOT_REQUIRED value=0x{bits:x}")
    return (bits & -bits).bit_length() - 1


def _row_request(request: int) -> int:
    return sum(
        int(bool(request & (0xF << (4 * row)))) << row for row in range(4)
    )


def canonical_fovea_step(
    request: int, state: FoveaState
) -> tuple[Optional[int], FoveaState]:
    """One canonical WEIGHT=5 scalar microstep."""

    request &= 0xFFFF
    rows = _row_request(request)
    center_available = bool(rows & CENTER_MASK)
    peripheral_available = bool(rows & PERIPH_MASK)
    prefer_center = state.round_index != 5
    use_center = (prefer_center and center_available) or (
        not prefer_center and not peripheral_available and center_available
    )
    use_peripheral = (not prefer_center and peripheral_available) or (
        prefer_center and not center_available and peripheral_available
    )
    center_grant, center_next = _arbiter4(
        rows & CENTER_MASK if use_center else 0, state.center
    )
    peripheral_grant, peripheral_next = _arbiter4(
        rows & PERIPH_MASK if use_peripheral else 0, state.peripheral
    )
    row_grant = center_grant if use_center else (
        peripheral_grant if use_peripheral else 0
    )
    if not row_grant:
        return None, replace(
            state, center=center_next, peripheral=peripheral_next
        )
    row = _onehot_index(row_grant)
    column_grant, column_next = _arbiter4(
        (request >> (4 * row)) & 0xF, state.column
    )
    source = 4 * row + _onehot_index(column_grant)
    return source, FoveaState(
        center=center_next,
        peripheral=peripheral_next,
        column=column_next,
        round_index=0 if state.round_index == 5 else state.round_index + 1,
    )


def _tokens_for_contract(contract: str) -> tuple[int, ...]:
    if contract == CONTRACT_BATCHED_IWRR:
        return BATCHED_IWRR_ROWS
    if contract == CONTRACT_PAIRED_ROW_PROPOSAL:
        return PAIRED_ROW_PROPOSAL_ROWS
    raise ContractViolation(f"NO_CALENDAR_FOR_CONTRACT contract={contract}")


def aggregate_rows(tokens: Sequence[int]) -> tuple[int, int, int, int]:
    counts = [0, 0, 0, 0]
    for row in tokens:
        if row not in range(4):
            raise ContractViolation("ROW_RANGE")
        counts[row] += 1
    return tuple(counts)  # type: ignore[return-value]


def check_weight_schedule(tokens: Sequence[int]) -> None:
    actual = aggregate_rows(tokens)
    if actual != WEIGHTS:
        raise ContractViolation(
            f"FALSE_AGGREGATE_1551 expected={WEIGHTS} actual={actual}"
        )


def check_batched_iwrr_contract(tokens: Sequence[int]) -> None:
    if tuple(tokens) != BATCHED_IWRR_ROWS:
        raise ContractViolation(
            "BATCHED_IWRR_CALENDAR_MISMATCH "
            f"expected={BATCHED_IWRR_ROWS} actual={tuple(tokens)}"
        )
    check_weight_schedule(tokens)


def _pick_column(request: int, row: int, start: int) -> int:
    columns = (request >> (4 * row)) & 0xF
    for offset in range(4):
        column = (start + offset) & 3
        if columns & (1 << column):
            return column
    raise ContractViolation(f"EMPTY_SELECTED_ROW row={row}")


def _calendar_source(
    request: int, row: int, column_rr: list[int]
) -> Optional[int]:
    if not request & (0xF << (4 * row)):
        return None
    column = _pick_column(request, row, column_rr[row])
    column_rr[row] = (column + 1) & 3
    return 4 * row + column


def _state_dict(state: PolicyState) -> dict:
    return asdict(state)


class K2Scheduler:
    """One atomic offer: count/addresses/state hold until bundle_ready."""

    def __init__(self, contract: str, fault: Optional[str] = None) -> None:
        if contract not in CONTRACTS:
            raise ValueError(f"unknown contract: {contract}")
        self.contract = contract
        self.fault = fault
        self.policy: PolicyState = (
            FoveaState() if contract == CONTRACT_SCALAR_PREFIX else CalendarState()
        )
        self.bundle: Optional[Bundle] = None
        self.cycle_index = 0
        self._previous_correct_g1: Optional[int] = None

    def _make_scalar_bundle(self, request: int, future_request: int) -> Bundle:
        assert isinstance(self.policy, FoveaState)
        g0, state1 = canonical_fovea_step(request, self.policy)
        remaining = request if g0 is None else request & ~(1 << g0)
        correct_g1, correct_state2 = canonical_fovea_step(remaining, state1)
        g1_request, g1_state = remaining, state1
        if self.fault == "duplicate_source":
            g1_request = request
        elif self.fault == "wrong_rr_state_after_g0":
            g1_state = self.policy
        elif self.fault == "future_arrival_overclaim":
            g1_request |= future_request
        g1, state2 = canonical_fovea_step(g1_request, g1_state)
        if self.fault == "stale_g1":
            g1, state2 = self._previous_correct_g1, correct_state2
        self._previous_correct_g1 = correct_g1
        if g0 is None:
            g1, state2 = None, state1
        sources = (g0, g1)
        count = sum(source is not None for source in sources)
        extra: tuple[int, ...] = ()
        if self.fault == "bitmap_popcount_confusion" and g0 is not None:
            row = g0 // 4
            extra = tuple(
                4 * row + column
                for column in range(4)
                if request & (1 << (4 * row + column))
                and 4 * row + column not in sources
            )
        return Bundle(
            count,
            sources,
            request,
            state2,
            tuple(None if s is None else s // 4 for s in sources),  # type: ignore[arg-type]
            extra,
        )

    def _make_calendar_bundle(self, request: int) -> Bundle:
        assert isinstance(self.policy, CalendarState)
        tokens = list(_tokens_for_contract(self.contract))
        if self.fault == "false_aggregate_1551":
            tokens[5] = 2
        pair = tokens[2 * self.policy.phase : 2 * self.policy.phase + 2]
        rr = list(self.policy.column_rr)
        sources: list[Optional[int]] = []
        nominal: list[Optional[int]] = []
        for row in pair:
            nominal.append(row)
            source = _calendar_source(request, row, rr)
            if source is None and self.fault == "sparse_fallback_debt":
                # Incorrect debt/borrowing model: an absent entitlement is
                # replaced by an unrelated eligible row instead of waived.
                for borrower in range(4):
                    source = _calendar_source(request, borrower, rr)
                    if source is not None and source not in sources:
                        break
                if source in sources:
                    source = None
            sources.append(source)
        compact = [source for source in sources if source is not None]
        source_tuple = tuple(compact + [None] * (2 - len(compact)))
        next_state = CalendarState(
            phase=(self.policy.phase + 1) % 6,
            column_rr=tuple(rr),  # type: ignore[arg-type]
        )
        extra: tuple[int, ...] = ()
        if self.fault == "bitmap_popcount_confusion":
            extra = tuple(
                source for source in range(16)
                if request & (1 << source) and source not in source_tuple
            )
        return Bundle(
            sum(source is not None for source in source_tuple),
            source_tuple,  # type: ignore[arg-type]
            request,
            next_state,
            tuple(nominal),  # type: ignore[arg-type]
            extra,
        )

    def _make_bundle(self, cycle_input: CycleInput) -> Bundle:
        request = cycle_input.request & 0xFFFF
        if self.contract == CONTRACT_SCALAR_PREFIX:
            return self._make_scalar_bundle(
                request, cycle_input.future_request & 0xFFFF
            )
        return self._make_calendar_bundle(request)

    def step(self, cycle_input: CycleInput) -> Observation:
        before = _state_dict(self.policy)
        if cycle_input.reset:
            old = self.bundle
            self.policy = (
                FoveaState()
                if self.contract == CONTRACT_SCALAR_PREFIX
                else CalendarState()
            )
            self.bundle = old if self.fault == "reset_phantom" else None
            addresses = old.addresses if self.bundle is not None else (None, None)
            observation = Observation(
                self.contract, self.cycle_index, True,
                old.request_snapshot if self.bundle is not None else cycle_input.request & 0xFFFF,
                cycle_input.bundle_ready,
                old.grant_count if self.bundle is not None else 0,
                addresses, (None, None), addresses,
                old.nominal_rows if self.bundle is not None else (None, None),
                old.extra_sources if self.bundle is not None else (),
                before, _state_dict(self.policy),
            )
            self.cycle_index += 1
            return observation

        if self.bundle is None:
            self.bundle = self._make_bundle(cycle_input)
        bundle = self.bundle
        committed = bundle.addresses if cycle_input.bundle_ready else (None, None)
        held = (None, None) if cycle_input.bundle_ready else bundle.addresses
        # A2's exact contract waives an all-empty phase automatically.  A
        # nonempty offer is atomic and advances only when bundle_ready.
        automatic_empty_waive = bundle.grant_count == 0 and (
            self.contract == CONTRACT_BATCHED_IWRR
        )
        if cycle_input.bundle_ready or automatic_empty_waive:
            self.policy = bundle.next_policy
            self.bundle = None
        elif self.fault == "calendar_advance_uncommitted_lane":
            self.policy = bundle.next_policy
        observation = Observation(
            self.contract, self.cycle_index, False, bundle.request_snapshot,
            cycle_input.bundle_ready, bundle.grant_count, bundle.addresses,
            committed, held, bundle.nominal_rows, bundle.extra_sources,
            before, _state_dict(self.policy),
        )
        self.cycle_index += 1
        return observation


@dataclass(frozen=True)
class LinkObservation:
    outputs: tuple[Optional[int], Optional[int]]
    held_after: tuple[Optional[int], Optional[int]]
    scheduler_policy_touched: bool


class TwoLaneBufferedLink:
    """Post-scheduler adapter; lane stalls are deliberately outside policy."""

    def __init__(self, fault: Optional[str] = None) -> None:
        self.lanes: tuple[Optional[int], Optional[int]] = (None, None)
        self.fault = fault

    def accept_atomic(self, addresses: tuple[Optional[int], Optional[int]]) -> None:
        if any(source is not None for source in self.lanes):
            raise ContractViolation("LINK_ACCEPT_WHILE_FULL")
        self.lanes = addresses

    def step(
        self,
        lane_ready: tuple[bool, bool],
        corrupting_arrival: tuple[Optional[int], Optional[int]] = (None, None),
    ) -> LinkObservation:
        outputs = tuple(
            source if lane_ready[lane] else None
            for lane, source in enumerate(self.lanes)
        )
        held = tuple(
            None if lane_ready[lane] else source
            for lane, source in enumerate(self.lanes)
        )
        touched = False
        if self.fault == "independent_lane_stall_corruption":
            held = tuple(
                corrupting_arrival[lane] if held[lane] is not None else held[lane]
                for lane in range(2)
            )
            touched = True
        self.lanes = held  # type: ignore[assignment]
        return LinkObservation(outputs, held, touched)


def run_trace(
    contract: str, trace: Iterable[CycleInput], fault: Optional[str] = None
) -> list[Observation]:
    scheduler = K2Scheduler(contract, fault=fault)
    return [scheduler.step(cycle_input) for cycle_input in trace]


def flatten_committed(observations: Iterable[Observation]) -> list[int]:
    return [
        source
        for observation in observations
        for source in observation.committed
        if source is not None
    ]


def validate_observation(observation: Observation) -> None:
    sources = [s for s in observation.addresses if s is not None]
    if observation.grant_count != len(sources) or sources != list(
        observation.addresses[: observation.grant_count]
    ):
        raise ContractViolation("K2_COUNT_ADDRESS_ENCODING")
    if len(sources) != len(set(sources)):
        raise ContractViolation("DUPLICATE_SOURCE")
    for source in sources:
        if not observation.request_snapshot & (1 << source):
            raise ContractViolation(f"FUTURE_ARRIVAL_OVERCLAIM source={source}")
    if observation.extra_sources:
        raise ContractViolation(
            f"BITMAP_POPCOUNT_CONFUSION extras={observation.extra_sources}"
        )
    if observation.reset and observation.grant_count:
        raise ContractViolation("RESET_PHANTOM")
    if not observation.bundle_ready and observation.grant_count:
        if observation.committed != (None, None):
            raise ContractViolation("ATOMIC_BUNDLE_PARTIAL_COMMIT")
        if observation.policy_after != observation.policy_before:
            raise ContractViolation("CALENDAR_ADVANCE_ON_UNCOMMITTED_LANE")


def compare_observations(
    expected: Sequence[Observation], actual: Sequence[Observation]
) -> None:
    if len(expected) != len(actual):
        raise ContractViolation("TRACE_LENGTH")
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            raise ContractViolation(
                f"REFERENCE_DIVERGENCE cycle={index} expected={left.json_dict()} "
                f"actual={right.json_dict()}"
            )
