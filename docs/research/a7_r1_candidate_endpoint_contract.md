# A7 W5 R1 candidate endpoint contract

Status: implementation contract frozen before W5 RTL. Physical status remains
**HOLD**; this document is digital behavior, not STA/PVT/CDC closure.

## R1 upstream launch semantics

The endpoint uses standard synchronous ready-valid at `ref_clk_i`. One launch
occurs on every rising edge satisfying:

```text
launch_fire = event_valid_i && event_ready_o
```

At R1 the link can launch one frame per reference period. `event_ready_o` is low
during reset and remains low through the first safe reference rising edge after
release; a charged one-bit arming register raises ready after that edge.
Continuous valid with a
new address on every accepted edge is legal and must produce consecutive frames.
There is no valid-edge detector, consumed-level bit, rearm cycle, retry state,
or queue. A transaction is held only while ready is low: valid and address must
remain stable until the first handshake. One-shot means exactly one frame per
posedge handshake, not one frame per valid assertion. Cross-rate R>1 and
level-request protocols would require different explicitly charged machinery
and are outside this candidate.

The synthesized launch qualifier contains that one-bit reset-release arming
register plus the ready/fire logic. TX contains the charged four-bit address
register and one-bit frame-enable register.
Back-to-back handshakes reuse these registers but never overwrite a symbol before
its falling-edge commit.

## Clock and DDR semantics

`ref_clk_i` and `sample_clk_i` have equal frequency. `sample_clk_i` rises one
quarter period after `ref_clk_i`. A handshake at a reference rising edge captures
the address while the sample clock is low. The ICG technology boundary forwards
exactly the next sample-clock rise/fall pair:

- forwarded rise samples address `[1:0]`;
- forwarded fall samples address `[3:2]`, reconstructs the address, and commits;
- consecutive handshakes keep the ICG enabled and form a continuous clock while
  retaining one rise/fall pair per occurrence.

The generic latch-and-gate ICG remains a technology contract. Characterized ICG
and DDR endpoint mapping, both half-cycle timing, pulse width, skew and PVT are
**HOLD**.

## Reset contract

Reset assertion is asynchronous in the RTL but is legal only when
`drain_idle_o==1` and the forwarded clock is low. Legal release occurs while
both source clocks are low, after a sample-clock falling edge and at least the
quarter-cycle phase interval before the next reference rising edge. Reset clears
TX state, the RX partial symbol, retirement address, and retirement toggle.
`drain_idle_o` is fail-closed across the whole digital endpoint: it is low for
a same-cycle `launch_fire`, an active frame/clock, an unobserved raw toggle, or
a registered `retire_valid_o` that the downstream consumer has not yet sampled.

Mid-frame reset is invalid input behavior. It can truncate the forwarded clock;
the in-flight occurrence has no delivery guarantee. The endpoint exposes
`drain_idle_o` so reset control can enforce the contract, but contains no runtime
fault queue or resynchronizer. The candidate-only negative test must observe
`drain_idle_o==0`, identify the invalid assertion, ensure reset creates no
phantom retirement, then perform a legal reset before subsequent traffic.
Recovery/removal and RDC remain physical **HOLD**.

## Downstream retirement contract

The RX raw address/toggle commits on the forwarded burst-clock falling edge.
Because R1 freezes `ref_clk_i` and `sample_clk_i` to one source with known phase,
a charged `seen_toggle` register observes that raw toggle at the next reference
rising edge and makes `retire_valid_o` plus the complete registered address
available one reference cycle after admission. An always-ready synchronous
consumer samples those registered outputs in the pre-NBA region of the next
reference edge, so architectural consumer retirement is two cycles after
admission. `drain_idle_o` remains low during the intervening valid cycle.
This is a phase-related synchronous half-cycle path, not a 2FF CDC synchronizer.
There is no retire ready/backpressure and no queue; the primary sink is always
ready. Backpressure or unrelated consumer clocks require a future explicitly
charged handshake/FIFO variant.

The consumer reset epoch matches endpoint reset and cannot interpret the
reset-forced toggle as retirement. Half-cycle setup/hold, recovery/removal and
physical phase closure remain **HOLD**.

## Parallel reference boundary

The parallel reference uses the identical ready-valid launch qualifier, reset
contract, TX/RX endpoint service, and ref-domain `retire_valid_o`/address
observer boundary. Only the link encoding changes: four address wires plus one
forwarded strobe instead of two address wires plus one DDR clock. Both tops
include the arming register, TX, link-clock boundary, RX raw toggle/address, and
the charged `seen_toggle` consumer observer. Neither includes a queue,
backpressure, or downstream CDC adapter. Generic synthesis comparisons count
all of that digital state; they do not count a characterized ICG, clock tree,
ODDR/IDDR hard cells, routed pin capacitance, or physical phase closure and
therefore are not physical PPA evidence.

## Digital evidence and charged scope

Run `scripts/run_a7_r1_candidate_endpoint.sh`. The candidate-only lockstep test
covers nominal, gapped, 16 continuously-valid changing-address handshakes,
stalled/held valid across reset, legal drain-reset, and an explicitly invalid
mid-frame reset. It requires exact once/order/address equality at the common
ref-domain observer boundary. It also checks every DDR rise/fall symbol and the
parallel link occurrence.

The local generic Yosys proxy includes reset-release arming, TX storage, generic
ICG latch, RX state, raw address/toggle, and ref-domain observer for both tops:

| link | pins | pre-guard functional | drain guard | charged total | state bits | charged depth |
|---|---:|---:|---:|---:|---:|---:|
| DDR2 | 3 | 25 | 4 | 29 | 20 | 7 / 7 |
| parallel4 | 5 | 23 | 4 | 27 | 18 | 7 / 7 |

These counts demonstrate that the two-wire saving is not free: this model adds
two state bits and two functional generic cells versus the same-boundary
parallel top. The reset-safety fix adds and charges four common drain-guard
cells to each endpoint; it is not folded out of the charged total. Yosys
`$scopeinfo` bookkeeping cells alone are excluded (five for DDR2 and three for
parallel4); registers and latches remain counted. The pre-guard functional
column records the corrected ca1a209 core comparison (25 versus 23), while the
charged total is the current synthesizable RTL (29 versus 27).
The numbers are structural proxies only. Because characterized ICG/DDR cells,
clock-tree cost, routing/load activity, half-cycle STA, recovery/removal, PVT,
and power are absent, the full endpoint remains physical **HOLD**.
