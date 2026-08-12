# A6 W6 weighted-fovea + A7 integration and novelty contract

Status: **normative proposal; qualification HOLD**.  This contract does not
change either owner's RTL and does not claim a qualified integrated product.

## Event and weighting semantics

The only functional event is one 4-bit address `addr = {row[1:0], col[1:0]}`.
Every admitted occurrence shall retire exactly once with the same address.  No
payload, source metadata, event type, duplicate suppression, coalescing, or
reconstruction is part of the hardware contract.

For rows 0 through 3, the fovea arbitration weights are exactly
`[1, 5, 5, 1]`.  A weight is a relative service opportunity among simultaneously
eligible rows.  It does not create five occurrences, reserve five output slots,
change address identity, or guarantee a 5:1 measured rate when offered traffic
or column contention prevents it.  Rows 1 and 2 are the favored center class;
rows 0 and 3 are the peripheral class.  Within-row selection and the precise
credit/round update remain properties of the frozen fovea RTL and shall be
tested, not reinterpreted by integration logic.

## Exact R1 integration boundary

The direct composition is:

```text
fovea: clk=ref_clk_i, rst=~rst_n, req_i[15:0]
        -> fovea_valid, fovea_addr[3:0]
A7:    event_valid_i=fovea_valid, event_addr_i=fovea_addr
        -> event_ready_o, burst_clk_o, burst_data_o[1:0]
        -> retire_valid_o, retire_addr_o[3:0], drain_idle_o
```

There is no adapter FIFO, retry, arbitration, address decoder, metadata lookup,
or rate converter.  The fovea can emit at most one address per `ref_clk_i`
cycle; A7 R1 can accept one frame per reference period after arming.  Continuous
one-event-per-cycle operation is required.

The native fovea has no ready input and therefore cannot satisfy A7's held-valid
rule while `event_ready_o==0`.  Integration is legal only under this sequence:

1. keep fovea reset asserted and `req_i==0`;
2. release A7 reset under its legal clock-phase conditions;
3. observe A7 armed with `event_ready_o==1`;
4. release fovea reset and then permit requests;
5. until the next drain/reset sequence, A7 ready shall remain asserted.

Any fovea `valid` while A7 is not ready is a protocol failure, not a dropped or
retried event.  A production top shall implement and charge the release/request
isolation needed for this sequence; it is not present for free in either block.

`ref_clk_i` and `sample_clk_i` have equal frequency and a known quarter-period
phase relation.  A7 commits the reconstructed address on the forwarded-clock
falling edge; its charged ref-domain observer presents it one cycle after
admission, and an always-ready synchronous consumer retires it two cycles after
admission.  This is the strict phase-related R1 contract, not unrelated-clock
CDC.  Downstream backpressure, clock-ratio changes, or unrelated clocks require
an explicitly charged handshake or asynchronous FIFO and are excluded.

Reset may be asserted only after requests are quiesced and the complete path is
drained.  Normatively, system drain requires all of:

```text
req_i == 16'b0
fovea_valid == 1'b0
A7 drain_idle_o == 1'b1
burst_clk_o == 1'b0
```

Reset release shall occur with both source clocks low, after a sample-clock
falling edge and at least one quarter period before the next reference rise.
Mid-frame reset is invalid and provides no delivery guarantee.  The integrated
drain indication shall fail closed until the fovea is quiescent and A7 has no
admission, active frame, unobserved commit, or unconsumed retire indication.

## Claim boundary

The safe novelty statement is:

> A scoped cross-layer composition of `[1,5,5,1]` foveation-inspired exact-address
> arbitration with an activity-triggered two-data-wire DDR link, evaluated at a
> common synchronous consumer boundary with TX, framing, RX, reset, and observer
> state charged.

The weights are an engineering abstraction of center-biased resource allocation,
not a model of retinal anatomy, receptor density, receptive fields, saliency, or
neural coding.  AER, weighted arbitration, forwarded-clock links, and DDR are not
individually claimed as new.  Without a literature and patent review this work
shall not claim “first,” biological fidelity, or general novelty beyond the
specific composition and its measured trade-off.

A7's current digital proxy is three link pins, 20 state bits, and 29 charged
generic functional cells versus five pins, 18 bits, and 27 cells for its
same-boundary parallel reference.  These are not integrated fovea totals and not
physical PPA evidence.  Characterized ICG/ODDR/IDDR cells, clock tree, routed pin
load, half-cycle STA, reset recovery/removal, PVT, and measured power remain
excluded and therefore physical status remains **HOLD**.

## Required qualification

No integration GO or performance/energy superiority claim is permitted until:

- cycle-exact lockstep proves accepted, link, reconstructed, and retired address
  equality, exact-once occurrence count, and source-local order;
- reset arming, legal drain/reset, illegal mid-frame reset, continuous traffic,
  retrigger, simultaneous rows, partial activity, and idle gaps are tested;
- full50 and capacity22 are replayed with generated/accepted/retired conservation,
  per-row acceptance, latency percentiles, service gaps, overrun, and matched
  identity/affine controls;
- observed center/periphery service is reported as a workload-dependent outcome,
  not inferred from `[1,5,5,1]` alone;
- synthesis includes the unmodified weighted fovea, release/isolation and drain
  glue, complete A7 TX/link/RX/observer, and reset control;
- a five-pin parallel endpoint uses the same admission, reset, drain, and consumer
  observation boundary; and
- physical pin, clock, toggle, timing, area, and power costs are closed with the
  omitted technology cells included.

Capacity loss with zero scoreboard errors is a capacity result; it is not a
correctness failure.  Any loss, duplicate, reorder, stale address, censored drain,
or uncharged integration state is a qualification failure.

## Why cluster2 is excluded

Cluster2 emits two native lanes of `{valid, row[1:0], col_mask[3:0]}` and can
represent up to eight occurrences in one cycle.  A7 R1 accepts one scalar 4-bit
address per cycle.  Connecting them losslessly requires bitmap enumeration,
ordering rules, arbitration, burst buffering, retry/backpressure, and ownership
state.  None belongs to the direct A7 endpoint, and even two scalar A7 links cap
service at two rather than eight events per cycle.  Consequently cluster2+A7
may not inherit both cluster2 peak throughput and A7's three-pin/count results;
it is outside this contract.

## Future six-pin bitmap DDR

A future candidate may dedicate one three-pin DDR link to each cluster2 lane,
for six pins total: two data wires plus one forwarded clock per lane.  This is a
new bitmap protocol, not two unchanged A7 scalar endpoints.  Each lane must frame
the full six-bit `{row, col_mask}` symbol, define empty/partial masks and lane
ordering, preserve every set-bit occurrence, and provide sufficient buffering or
backpressure for consecutive multi-hot masks.  TX, both clocks, framing, RX,
bitmap-to-occurrence enumeration, reset/drain, storage, and consumer control must
all be implemented and charged.  Until its worst-case service rate, latency,
resynchronization, full50/capacity22 conservation, and physical endpoint are
qualified, the six-pin form is **future HOLD**, not evidence for this candidate.
