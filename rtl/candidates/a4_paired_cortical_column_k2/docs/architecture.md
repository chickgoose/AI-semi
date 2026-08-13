# Architecture, evidence, and limits

## Datapath and state

There are exactly four row-local 4-way round-robin column arbiters. Their
outputs are four `{valid,column[1:0]}` summaries. The global portion selects at
most two distinct rows, then forms each 4-bit address by concatenating row and
column. No 16-way payload mux or event queue is present in the scheduler.

Default scheduler state is 49 bits:

| State | Bits |
|---|---:|
| phase and token | 4 |
| four column pointers | 8 |
| four 4-bit bounded debt counters | 16 |
| debt and fallback rotating starts | 4 |
| atomic held-request valid and snapshot | 17 |
| total | 49 |

The optional ordered link adds 10 synthesizable bits (2-bit occupancy and two
4-bit addresses). It is deliberately absent from scheduler PPA measurements.

## Fallback/debt invariant

A borrowed committed token increments only its nominal row's saturating debt.
An eligible indebted row is serviced before calendar tokens and decremented
only by a real committed event. If the debt counter is full, fallback remains
work-conserving but the nominal token is not consumed. This produces a visible
stall in calendar progress instead of debt wrap or silent weight erasure.

The debt is not A8's signed equal-and-opposite formulation. It records owed
nominal service, while fallback fairness is maintained by a separate rotating
row start. That distinction is material and tested/documented as aggregate-only.

## Boundary and reset

An uncommitted offer snapshots the complete 16-bit request cohort. Changing
live inputs while blocked cannot alter the offered count or addresses. Policy
next state is combinational speculation only; registers update iff
`bundle_ready && grant_count!=0`. Reset asynchronously clears the offer and
all fairness state. It also overrides live request and ready pins so
`grant_count=0`, both addresses are zero, `source_ready=0`, and `drain_idle=1`.
No partial lane-drain state exists in the scheduler.

The optional link can retire one or two FIFO-prefix events. Lane 1 valid is
gated by lane 0 ready; therefore a `retire_ready=2'b10` observation exposes
only lane 0 valid and cannot handshake the younger entry. With `01`, only the
head retires and the younger entry compacts to lane 0 before any refill. The
scheduler `bundle_ready` is asserted only when the whole offered bundle fits
after that edge's ordered retirement.

## Evaluator cross-check

- A5 commit `41c425bec79aca6c84f5856ca7dee2a4865a6447` requires the scalar wheel
  `0,1,1,1,1,1,2,2,2,2,2,3` and a separately stalled ordered output link.
  The optional link aligns transport, but the scheduler is not a contiguous
  scalar prefix, so no A5 `FULL`/PASS claim is made.
- A8 commit `1248a19e1f3bea4c519645460cb810b19fab4c5d` uses the same atomic bundle
  handshake but its paired proposal calendar begins
  `0,1,2,1,2,1,2,1,2,1,2,3` and uses signed equal-and-opposite fallback debt.
  This candidate has the same epoch aggregate only and remains unbound.

The semantic grade is therefore **AGGREGATE_ONLY**. Transport compatibility
does not improve that grade.

## Physical evidence limits

The structural runner uses warning-free Verilator lint and two Yosys views:
pre-techmap mux cells/select/data-bit load, then `techmap; abc -g simple` for
flop bits, generic cells, logic depth, fanout distribution, high-fanout nets,
and sink-pin wire load. Architectural pin counts and local routing estimates
are also reported.

These measurements do not establish clock frequency, area, power, buffering,
route crossings, congestion, or pin placement. The debt-first/fallback choice
chain is the likely critical path. Actual PPA and route locality require the
target Liberty set, synthesis constraints, floorplan, placement, clock tree,
and routed extraction. The generic depth is a comparison proxy, not a timing
number.

Frozen-v4 replay is local candidate evidence. Common qualification, A5/A8
owner binding, release qualification, and physical PPA all remain HOLD.
