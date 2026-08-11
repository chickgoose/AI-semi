#!/usr/bin/env python3
"""Executable state-machine contract for the address-only N16 R1 endpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from collections import deque


N = 16
FINAL_A7_ENDPOINT_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
DDR_FUNCTIONAL_PINS = 3       # forwarded clock + data[1:0]
PARALLEL_FUNCTIONAL_PINS = 5  # forwarded strobe + addr[3:0]
DDR_ENDPOINT_STATE_BITS = 20
PARALLEL_ENDPOINT_STATE_BITS = 18
FINAL_A7_DDR_CHARGED_CELLS = 29
FINAL_A7_PARALLEL_CHARGED_CELLS = 27
FINAL_A7_CHARGED_DEPTH = 7
RAW_DDR_COMMIT_DELAY = Fraction(3, 4)
OUTPUT_AVAILABILITY_DELAY = Fraction(1, 1)
SYNCHRONOUS_CONSUME_DELAY = Fraction(2, 1)


class ContractViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class Faults:
    valid_edge_detector: bool = False
    duplicate_launch: bool = False
    duplicate_raw_commit: bool = False
    duplicate_retire: bool = False
    drop_raw_commit: bool = False
    corrupt_wire_hidden_reconstruction: bool = False
    stale_after_reset: bool = False
    phantom_frame: bool = False
    wrong_launch_phase: bool = False
    wrong_raw_commit_phase: bool = False
    wrong_observer_phase: bool = False
    omit_launch_drain_guard: bool = False
    omit_pending_valid_drain_guard: bool = False


@dataclass(frozen=True)
class Launch:
    low_symbol: int
    time: Fraction


@dataclass(frozen=True)
class RawCommit:
    address: int
    wire_address: int
    toggle: int
    time: Fraction


@dataclass(frozen=True)
class Availability:
    address: int
    time: Fraction


@dataclass(frozen=True)
class Retirement:
    address: int
    time: Fraction


class R1Endpoint:
    """Charged R1 endpoint; ``Faults`` exist only for checker mutation tests."""

    def __init__(self, faults: Faults = Faults()):
        self.faults = faults
        self.reset_release_armed_q = False  # launch qualifier: 1 bit
        self.tx_address_q = 0               # TX: 4 bits
        self.frame_active_q = False         # TX: 1 bit
        self.icg_enable_latched_q = False   # generic ICG boundary: 1 bit
        self.rx_low_symbol_q = 0            # RX: 2 bits
        self.raw_retire_address_q = 0       # RX: 4 bits
        self.raw_retire_toggle_q = 0        # RX: 1 bit
        self.seen_toggle_q = 0              # observer: 1 bit
        self.retire_address_q = 0           # observer: 4 bits
        self.retire_valid_q = False         # observer: 1 bit
        self._rise_seen = False             # schedule checker, not endpoint state
        self._previous_valid = False        # mutation-only edge-detector state
        self._stale_address: int | None = None
        self._launch_fire_current = False

    @property
    def declared_functional_state_bits(self) -> int:
        return DDR_ENDPOINT_STATE_BITS

    def ref_edge(
        self, valid: bool, address: int, time: Fraction
    ) -> tuple[bool, bool, list[Availability], list[Retirement]]:
        if valid and not 0 <= address < N:
            raise ContractViolation("DUT address must be the four-bit N16 source")

        # An always-ready synchronous consumer samples the registered output
        # from the preceding cycle in the pre-NBA region of this edge.
        retirements: list[Retirement] = []
        if self.retire_valid_q:
            retirement = Retirement(self.retire_address_q, time)
            retirements = (
                [retirement, retirement]
                if self.faults.duplicate_retire
                else [retirement]
            )

        # The raw fall-to-ref path is phase-related.  The six-bit charged
        # observer makes registered valid/address available after this edge.
        raw_changed = self.raw_retire_toggle_q != self.seen_toggle_q
        self.retire_valid_q = raw_changed
        availabilities: list[Availability] = []
        if raw_changed:
            self.retire_address_q = self.raw_retire_address_q
            available_time = (
                time + Fraction(1, 8) if self.faults.wrong_observer_phase else time
            )
            availabilities = [Availability(self.retire_address_q, available_time)]
        self.seen_toggle_q = self.raw_retire_toggle_q

        # Ready is low through the first ref edge after reset release.  No
        # valid-edge detector exists: every later valid&&ready edge is new.
        ready = self.reset_release_armed_q
        handshake = bool(valid and ready)
        accepted = handshake
        if self.faults.valid_edge_detector and self._previous_valid:
            accepted = False
        self._previous_valid = bool(valid)
        self.reset_release_armed_q = True
        self._launch_fire_current = accepted

        # R1 phase closure guarantees the preceding frame has already committed
        # before this edge.  Back-to-back handshakes reuse, rather than queue,
        # these registers.
        # launch_fire is combinational at this edge; frame_active_q changes in
        # the sequential TX update.  Keeping this seam explicit lets the drain
        # checker prove that same-cycle admission is guarded before that update.
        self._rise_seen = False
        if accepted:
            self.tx_address_q = address
        return ready, accepted, availabilities, retirements

    def ddr_rise(self, time: Fraction) -> list[Launch]:
        self.frame_active_q = self._launch_fire_current
        self.icg_enable_latched_q = self.frame_active_q
        if not self.frame_active_q:
            return [Launch(0, time)] if self.faults.phantom_frame else []
        self.rx_low_symbol_q = self.tx_address_q & 0b11
        self._rise_seen = True
        launch_time = time + Fraction(1, 8) if self.faults.wrong_launch_phase else time
        launch = Launch(self.rx_low_symbol_q, launch_time)
        return [launch, launch] if self.faults.duplicate_launch else [launch]

    def ddr_fall(self, time: Fraction) -> list[RawCommit]:
        if not self.frame_active_q:
            if self._stale_address is None:
                return []
            address = self._stale_address
            self._stale_address = None
            self.raw_retire_address_q = address
            self.raw_retire_toggle_q ^= 1
            return [RawCommit(address, address, self.raw_retire_toggle_q, time)]
        if not self._rise_seen:
            raise ContractViolation("falling DDR edge without a frame-opening rise")
        high_symbol = (self.tx_address_q >> 2) & 0b11
        if self.faults.corrupt_wire_hidden_reconstruction:
            high_symbol ^= 0b01
        wire_address = (high_symbol << 2) | self.rx_low_symbol_q
        self.raw_retire_address_q = (
            self.tx_address_q
            if self.faults.corrupt_wire_hidden_reconstruction
            else wire_address
        )
        self.raw_retire_toggle_q ^= 1
        commit_time = (
            time + Fraction(1, 8)
            if self.faults.wrong_raw_commit_phase
            else time
        )
        commit = RawCommit(
            self.raw_retire_address_q,
            wire_address,
            self.raw_retire_toggle_q,
            commit_time,
        )
        self._rise_seen = False
        if self.faults.drop_raw_commit:
            return []
        return [commit, commit] if self.faults.duplicate_raw_commit else [commit]

    def reset(self) -> None:
        if self.faults.stale_after_reset and self.frame_active_q:
            self._stale_address = self.tx_address_q
        else:
            self._stale_address = None
        self.reset_release_armed_q = False
        self.tx_address_q = 0
        self.frame_active_q = False
        self.icg_enable_latched_q = False
        self.rx_low_symbol_q = 0
        self.raw_retire_address_q = 0
        self.raw_retire_toggle_q = 0
        self.seen_toggle_q = 0
        self.retire_address_q = 0
        self.retire_valid_q = False
        self._rise_seen = False
        self._previous_valid = False
        self._launch_fire_current = False

    def reset_outputs_quiet(self) -> bool:
        return all((
            not self.frame_active_q,
            not self.icg_enable_latched_q,
            not self._launch_fire_current,
            self.raw_retire_toggle_q == 0,
            not self.retire_valid_q,
        ))

    def drain_idle(self) -> bool:
        launch_idle = (
            True if self.faults.omit_launch_drain_guard
            else not self._launch_fire_current
        )
        pending_valid_idle = (
            True if self.faults.omit_pending_valid_drain_guard
            else not self.retire_valid_q
        )
        return (
            launch_idle
            and not self.frame_active_q
            and not self.icg_enable_latched_q
            and self.raw_retire_toggle_q == self.seen_toggle_q
            and pending_valid_idle
        )


@dataclass
class Credit:
    address: int
    accepted_time: Fraction
    launched: bool = False
    raw_committed: bool = False
    available: bool = False


class ContractMonitor:
    """TB-only causal credits; they check but never reconstruct endpoint data."""

    def __init__(self):
        self.credits: deque[Credit] = deque()
        self.stalled_address: int | None = None
        self.expected_raw_toggle = 0
        self.accepted = 0
        self.launches = 0
        self.raw_commits = 0
        self.available = 0
        self.retired = 0
        self.invalid_midframe_resets = 0

    @property
    def drained(self) -> bool:
        return not self.credits

    def observe_retirement(self, retirement: Retirement) -> None:
        if not self.credits or not self.credits[0].available:
            raise ContractViolation("phantom, duplicate, or pre-availability retirement")
        credit = self.credits[0]
        if retirement.address != credit.address:
            raise ContractViolation("synchronous consumer retirement address mismatch")
        if retirement.time - credit.accepted_time != SYNCHRONOUS_CONSUME_DELAY:
            raise ContractViolation("synchronous consumer latency is not exactly two cycles")
        self.credits.popleft()
        self.retired += 1

    def observe_availability(self, availability: Availability) -> None:
        matches = [credit for credit in self.credits if credit.raw_committed and not credit.available]
        if not matches:
            raise ContractViolation("output availability without one raw commit")
        credit = matches[0]
        if credit is not self.credits[0]:
            raise ContractViolation("output availability reordered causal credits")
        if availability.address != credit.address:
            raise ContractViolation("observer availability address mismatch")
        if availability.time - credit.accepted_time != OUTPUT_AVAILABILITY_DELAY:
            raise ContractViolation("output availability is not exactly one cycle")
        credit.available = True
        self.available += 1

    def sample_core(
        self,
        *,
        valid: bool,
        address: int,
        ready: bool,
        endpoint_accepted: bool,
        time: Fraction,
    ) -> None:
        if valid and not 0 <= address < N:
            raise ContractViolation("logical event is not address-only N16")
        if self.stalled_address is not None:
            if not valid or address != self.stalled_address:
                raise ContractViolation("valid/address changed while ready was low")
        handshake = bool(valid and ready)
        if endpoint_accepted != handshake:
            raise ContractViolation("acceptance must equal valid AND ready at the posedge")
        self.stalled_address = address if valid and not ready else None
        if not handshake:
            return
        self.credits.append(Credit(address, time))
        if len(self.credits) > 2:
            raise ContractViolation("more than charged frame+observer pipeline capacity")
        self.accepted += 1

    def observe_launch(self, launch: Launch) -> None:
        matches = [credit for credit in self.credits if not credit.launched]
        if not matches:
            raise ContractViolation("frame launch without an accepted core event")
        credit = matches[0]
        if launch.low_symbol != (credit.address & 0b11):
            raise ContractViolation("DDR low symbol does not encode accepted address")
        if launch.time - credit.accepted_time != Fraction(1, 4):
            raise ContractViolation("frame launch phase is not one quarter cycle")
        credit.launched = True
        self.launches += 1

    def observe_raw_commit(self, commit: RawCommit) -> None:
        matches = [
            credit for credit in self.credits
            if credit.launched and not credit.raw_committed
        ]
        if not matches:
            raise ContractViolation("raw commit without one launched credit")
        credit = matches[0]
        if commit.wire_address != credit.address:
            raise ContractViolation("wire symbols do not carry the accepted address")
        if commit.address != commit.wire_address:
            raise ContractViolation("raw address was reconstructed outside wire data")
        if commit.time - credit.accepted_time != RAW_DDR_COMMIT_DELAY:
            raise ContractViolation("raw DDR commit is not exactly 3/4 cycle")
        self.expected_raw_toggle ^= 1
        if commit.toggle != self.expected_raw_toggle:
            raise ContractViolation("raw retirement toggle did not change exactly once")
        credit.raw_committed = True
        self.raw_commits += 1

    def end_frame_period(self, accepted_this_edge: bool, time: Fraction) -> None:
        if accepted_this_edge and not any(
            credit.accepted_time == time and credit.raw_committed
            for credit in self.credits
        ):
            raise ContractViolation("accepted R1 event did not complete its raw frame")

    def reset(self, *, require_drained: bool) -> None:
        if require_drained and not self.drained:
            raise ContractViolation("normative reset is allowed only after complete drain")
        if not self.drained:
            self.invalid_midframe_resets += 1
        self.credits.clear()
        self.stalled_address = None
        self.expected_raw_toggle = 0


class ContractHarness:
    def __init__(self, endpoint: R1Endpoint | None = None):
        self.endpoint = endpoint or R1Endpoint()
        self.monitor = ContractMonitor()
        self.cycle = 0

    def ref_edge(self, valid: bool, address: int = 0) -> bool:
        ready, accepted, availabilities, retirements = self.endpoint.ref_edge(
            valid, address, Fraction(self.cycle)
        )
        # The synchronous consumer first samples last cycle's registered output;
        # then the observer may make the next raw commit available.  Both occur
        # before accepting the new R1 event in the abstract edge ordering.
        for retirement in retirements:
            self.monitor.observe_retirement(retirement)
        for availability in availabilities:
            self.monitor.observe_availability(availability)
        self.monitor.sample_core(
            valid=valid,
            address=address,
            ready=ready,
            endpoint_accepted=accepted,
            time=Fraction(self.cycle),
        )
        return accepted

    def frame_rise(self) -> None:
        for launch in self.endpoint.ddr_rise(Fraction(self.cycle) + Fraction(1, 4)):
            self.monitor.observe_launch(launch)

    def frame_fall(self, time: Fraction | None = None) -> None:
        commit_time = time or Fraction(self.cycle) + RAW_DDR_COMMIT_DELAY
        for commit in self.endpoint.ddr_fall(commit_time):
            self.monitor.observe_raw_commit(commit)

    def run_cycle(self, valid: bool, address: int = 0) -> bool:
        accepted = self.ref_edge(valid, address)
        self.frame_rise()
        self.frame_fall()
        self.monitor.end_frame_period(accepted, Fraction(self.cycle))
        self.cycle += 1
        return accepted

    def arm_after_reset(self) -> None:
        if self.run_cycle(False):
            raise ContractViolation("first reset-release edge accepted an event")

    def reset(self, *, require_drained: bool = True) -> None:
        if require_drained and not self.endpoint.drain_idle():
            raise ContractViolation("endpoint drain_idle is false at legal reset")
        self.monitor.reset(require_drained=require_drained)
        self.endpoint.reset()
        if not self.endpoint.reset_outputs_quiet():
            raise ContractViolation("endpoint output is not quiet during reset")

    def assert_drain_consistent(self) -> None:
        if self.endpoint.drain_idle() != self.monitor.drained:
            raise ContractViolation("drain_idle does not cover every causal credit")

    def drain(self, limit: int = 4) -> None:
        for _ in range(limit):
            if self.monitor.drained and self.endpoint.drain_idle():
                return
            self.run_cycle(False)
        raise ContractViolation("full endpoint did not drain through consumer")

    def assert_conserved(self) -> None:
        if self.monitor.invalid_midframe_resets:
            raise ContractViolation("invalid mid-frame reset is not legal conservation")
        if not self.monitor.drained or not self.endpoint.drain_idle():
            raise ContractViolation("full endpoint is not drained")
        counts = (
            self.monitor.accepted,
            self.monitor.launches,
            self.monitor.raw_commits,
            self.monitor.available,
            self.monitor.retired,
        )
        if len(set(counts)) != 1:
            raise ContractViolation("accepted/frame/raw/retirement conservation failed")


@dataclass(frozen=True)
class PpaEvidence:
    mandatory_endpoint_tests_passed: bool
    throughput_ratio_to_parallel: Fraction
    raw_ddr_commit_delay_cycles: Fraction
    output_availability_delay_cycles: Fraction
    synchronous_consume_delay_cycles: Fraction
    latency_delta_to_parallel_cycles: Fraction
    ddr_functional_pins: int
    parallel_functional_pins: int
    ddr_launch_state_bits: int
    ddr_tx_state_bits: int
    ddr_icg_state_bits: int
    ddr_rx_state_bits: int
    ddr_observer_state_bits: int
    ddr_declared_total_state_bits: int
    parallel_declared_total_state_bits: int
    ddr_charged_functional_cells: int
    parallel_charged_functional_cells: int
    drain_guard_charged: bool
    boundary_queue_entries: int
    payload_bits: int
    hidden_reconstruction: bool
    post_route: bool
    setup_wns_ns: float
    hold_wns_ns: float
    forwarded_clock_qualified: bool
    endpoint_area: float
    parallel_area: float
    energy_per_event: float
    parallel_energy_per_event: float
    max_area_penalty_per_pin_saved: float | None = None
    max_energy_penalty_fraction: float | None = None


@dataclass(frozen=True)
class Decision:
    functional: str
    physical: str
    adoption: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


def qualify(evidence: PpaEvidence) -> Decision:
    reasons: list[str] = []
    accounted_ddr_state = sum((
        evidence.ddr_launch_state_bits,
        evidence.ddr_tx_state_bits,
        evidence.ddr_icg_state_bits,
        evidence.ddr_rx_state_bits,
        evidence.ddr_observer_state_bits,
    ))
    functional_ok = all((
        evidence.mandatory_endpoint_tests_passed,
        evidence.throughput_ratio_to_parallel == 1,
        evidence.raw_ddr_commit_delay_cycles == RAW_DDR_COMMIT_DELAY,
        evidence.output_availability_delay_cycles == OUTPUT_AVAILABILITY_DELAY,
        evidence.synchronous_consume_delay_cycles == SYNCHRONOUS_CONSUME_DELAY,
        evidence.latency_delta_to_parallel_cycles == 0,
        evidence.ddr_functional_pins == DDR_FUNCTIONAL_PINS,
        evidence.parallel_functional_pins == PARALLEL_FUNCTIONAL_PINS,
        evidence.ddr_declared_total_state_bits == accounted_ddr_state,
        evidence.ddr_declared_total_state_bits >= DDR_ENDPOINT_STATE_BITS,
        evidence.parallel_declared_total_state_bits >= PARALLEL_ENDPOINT_STATE_BITS,
        evidence.ddr_charged_functional_cells > 0,
        evidence.parallel_charged_functional_cells > 0,
        evidence.drain_guard_charged,
        evidence.boundary_queue_entries == 0,
        evidence.payload_bits == 0,
        not evidence.hidden_reconstruction,
    ))
    if not functional_ok:
        reasons.append("functional/full-endpoint accounting threshold failed")

    physical_ok = functional_ok and all((
        evidence.post_route,
        evidence.setup_wns_ns >= 0,
        evidence.hold_wns_ns >= 0,
        evidence.forwarded_clock_qualified,
    ))
    if functional_ok and not physical_ok:
        reasons.append("post-route timing or forwarded-clock qualification missing")

    budget_present = (
        evidence.max_area_penalty_per_pin_saved is not None
        and evidence.max_energy_penalty_fraction is not None
    )
    economic_ok = False
    if physical_ok and budget_present:
        pins_saved = evidence.parallel_functional_pins - evidence.ddr_functional_pins
        area_penalty = (evidence.endpoint_area - evidence.parallel_area) / pins_saved
        energy_penalty = (
            evidence.energy_per_event / evidence.parallel_energy_per_event - 1
        )
        economic_ok = (
            pins_saved == 2
            and area_penalty <= evidence.max_area_penalty_per_pin_saved + 1e-12
            and energy_penalty <= evidence.max_energy_penalty_fraction + 1e-12
        )
        if not economic_ok:
            reasons.append("predeclared pin-value area/energy budget exceeded")
    elif physical_ok:
        reasons.append("no predeclared pin-value economic budget")

    return Decision(
        functional="GO" if functional_ok else "HOLD",
        physical="GO" if physical_ok else "HOLD",
        adoption="GO" if economic_ok else "HOLD",
        reasons=tuple(reasons),
    )


def main() -> int:
    harness = ContractHarness()
    harness.arm_after_reset()
    for address in range(N):
        harness.run_cycle(True, address)
    harness.drain()
    harness.assert_conserved()
    print(
        "W5_R1_CONTRACT_MODEL_SELFTEST_PASS "
        "continuous_valid=16 accepted=16 launched=16 raw=16 available=16 retired=16 "
        "qualification=NONE"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
