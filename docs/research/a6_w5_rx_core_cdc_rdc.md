# A6 W5: production R1 RX-to-reference boundary audit

## Final binding and recommendation

The final endpoint is the A7 production snapshot at
`ca1a20971ee7bc32520aef47a3a97c89747c7fa5`. Digital status is
**GO_PRODUCTION_PHASE_RELATED_R1_DIGITAL_ONLY**. Physical status remains
**HOLD**, and unrelated-clock CDC remains
**HOLD_REQUIRES_END_TO_END_BACKPRESSURE**.

This is not a 2FF CDC synchronizer. `ref_clk_i` and `sample_clk_i` share the
frozen source and phase. RX commits its raw four-bit address/toggle on burst fall;
the charged six-bit observer samples both at the next reference rise. The
production endpoint additionally charges a one-bit reset-release arming register
and provides a complete same-boundary parallel4 reference.

A6 commit `ee590cc` contained a standalone synchronous-reset observer used to
establish the architecture. It is historical, not final qualification. Those
duplicate RTL files are removed at HEAD; all final RTL/TB/synthesis evidence is
read directly from the bound A7 production snapshot.

## Production timing and occurrence invariant

For the frozen 16 ns clock contract:

1. a reference rise at 0 ns performs one ready-valid handshake when armed;
2. sample/burst rise at 4 ns captures address `[1:0]`;
3. sample/burst fall at 12 ns captures `[3:2]`, publishes the complete raw
   address, and changes the raw retirement toggle exactly once;
4. the next reference rise at 16 ns observes the stable address/toggle and emits
   one-cycle `retire_valid_o`.

Commit-to-observer latency is nominally 4 ns or 0.25 reference cycles;
admission-to-observer latency is one cycle. Continuous changing-address valid is
legal and sustains one occurrence/reference cycle. There is no valid-edge
detector or rearm bubble. Exactness requires no more than one raw RX retirement
between reference edges. R=2/R=4 can make toggle transitions cancel and are
outside this endpoint even if an individual sparse trace happens to pass.

## Exact charged state and fair parallel boundary

The common retire observer contains six bits:

| State | Bits |
|---|---:|
| seen raw toggle | 1 |
| registered retirement address | 4 |
| registered retirement valid | 1 |

The production launch qualifier contributes a seventh common control bit:
`reset_release_armed_q`. It holds `event_ready_o` low through the first safe
reference edge after reset release, so that edge is charged and cannot handshake.

The complete flattened generic endpoints are:

| Link | Pins | Generic cells | State bits | Operator/gate depth |
|---|---:|---:|---:|---:|
| parallel4 production reference | 5 | 26 | 18 | 5 / 5 |
| DDR2 production endpoint | 3 | 30 | 20 | 5 / 5 |

Both include the same launch arming, ICG boundary, raw RX address/toggle, and
six-bit reference-domain observer. DDR2 therefore costs two state bits and four
generic cells while removing two physical link pins, with the same one-event/R1
digital ceiling. These are generic structural proxies, not physical area, timing,
energy, or maximum-frequency results.

The 20 DDR bits are: arming 1 + TX address/frame 5 + ICG latch 1 + RX low/raw
address/toggle 7 + observer 6. Parallel uses arming 1 + TX data/frame 5 + ICG
latch 1 + raw address/toggle 5 + observer 6 = 18.

Upstream same-cycle collection/FIFO, characterized ICG, ODDR/IDDR, clock tree,
pads, routing, downstream logic and physical STA/CDC/RDC remain excluded and
non-free.

## Alternative comparison

| Boundary | Incremental state lower bound | First visibility | Sustainable throughput | Lossless condition | Production A7 |
|---|---:|---|---|---|---|
| phase-related R1 observer | observer 6; production common control adds arming 1 | 0.25 cycle after RX commit | 1 event/ref cycle | frozen phase, STA, at most one commit/ref edge, drained reset | implemented |
| bundled-data two-phase handshake | 15 | 2--3 destination cycles; reuse after return sync | conservatively <=0.25 event/cycle at equal clocks | TX waits for synchronized acknowledge | not implemented |
| Gray-pointer async FIFO depth 2 | 31 | normally 2--3 destination cycles | min(write, read) | full backpressure or proved finite backlog | not implemented |
| Gray-pointer async FIFO depth 4 | 47 | normally 2--3 destination cycles | min(write, read) | same, with four slots | not implemented |

The handshake lower bound includes the four-bit source mailbox, request toggle,
two destination request synchronizer bits, destination seen bit, five output
address/valid bits, and two source acknowledgement synchronizer bits. It excludes
reset synchronizers and the required acknowledgement-to-TX admission path.

The FIFO lower bound includes four-bit payload storage, local binary/Gray
pointers, both two-stage crossed-pointer synchronizers, registered output/valid,
full, and empty. A real implementation may require more state. Current RX has no
ready/full input; a finite FIFO can overflow and a mailbox can be overwritten.
Neither is lossless until backpressure propagates to TX admission.

## full50/capacity22 replay

The A6 model pins the production sources, serializes the exact address-only
occurrences, and counts raw RX commits between reference edges. Toggle alias is
modeled exactly: two unseen transitions deliver zero; three expose only the last
occurrence.

| Suite | R | Events | Exact runs | Toggle-alias losses | Worst commits/ref interval | Finite-trace FIFO depth LB | Rounded FIFO/state LB |
|---|---:|---:|---:|---:|---:|---:|---:|
| full50 | 1 | 106416 | 50/50 | 0 | 1 | 3 | 4 / 47 bits |
| full50 | 2 | 106416 | 24/50 | 56452 | 2 | 4612 | 8192 / 32887 bits |
| full50 | 4 | 106416 | 24/50 | 55272 | 4 | 5135 | 8192 / 32887 bits |
| capacity22 | 1 | 65616 | 22/22 | 0 | 1 | 3 | 4 / 47 bits |
| capacity22 | 2 | 65616 | 3/22 | 49512 | 2 | 4612 | 8192 / 32887 bits |
| capacity22 | 4 | 65616 | 3/22 | 48332 | 4 | 5135 | 8192 / 32887 bits |

R1 is exact after serialization. This does not make the upstream collector free:
official simultaneous occurrences still require staging before the one-lane
ready-valid endpoint. The FIFO column charges two reference cycles before a
synchronized write pointer becomes visible and is only a finite-trace capacity
lower bound. Unrelated clocks can drift, and R>1 can sustain write>read, so no
finite number above proves arbitrary losslessness. Lower aggregate loss at some
R=4 cases is toggle parity, not improved correctness.

## Production reset/RDC contract

All production endpoint domains share the same `rst_n` epoch. Assertion is
asynchronous in RTL but legal only after drain with the forwarded clock low.
Release is legal while both source clocks are low, after a sample falling edge
and at least the quarter-cycle interval before the next reference rise.

The required order is:

1. stop admission and wait for `drain_idle_o==1`;
2. assert reset (`rst_n=0`) while the forwarded clock is low;
3. release `rst_n` in the frozen low-phase window;
4. charge the first safe reference edge to `reset_release_armed_q`; ready remains
   unable to handshake on that edge;
5. begin ready-valid admission only after `event_ready_o` rises.

Reset clears TX/RX raw state, raw toggle, observer epoch and output valid. An
in-flight reset can truncate the clock and has no delivery guarantee. The bound
negative test observes that invalid case, checks no phantom retirement, and then
performs a legal reset. Recovery/removal, RDC and physical release timing remain
HOLD.

## Exact inclusion boundary

Included and executed from `git archive ca1a209...`:

- production DDR2 endpoint and complete parallel4 reference;
- reset-release arming, TX, generic ICG, RX and retire observer;
- A7 nominal, continuous 16-event changing-address, gapped, held-valid reset,
  legal drain-reset, invalid mid-frame reset and exact once/order/address tests;
- identical generic Yosys flow yielding 30/20 DDR and 26/18 parallel
  cell/state results;
- A6 full50/capacity22 replay and handshake/FIFO lower-bound model.

Required but excluded/non-free:

- half-cycle related-clock STA, skew, recovery/removal and RDC closure;
- characterized ICG and DDR I/O mapping, CTS, pads, routing, PVT and extracted
  activity/power;
- upstream same-cycle collector/FIFO and common integration;
- unrelated-clock synchronizer, acknowledgement path, async FIFO or full
  backpressure.

Run `scripts/run_a6_w5_cdc_checks.sh`. It regenerates both suites, validates every
trace and production source SHA, extracts the immutable A7 snapshot, runs its
production Verilator regression and structural comparison, and requires the A6
R1 exact/HOLD boundaries. A7 and common files remain read only.

Provenance digests:

- production registry:
  `e6dbd1ede35d7443410953dd910c59905ab875515a4bce29fd91cf9a1da6a6df`;
- machine replay report:
  `b7003ad2d0225a46b93440c1c7c7a31bf94dccf02992fccff34ef96737de63f6`.
