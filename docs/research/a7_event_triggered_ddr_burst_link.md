# A7 Event-Triggered 2-bit DDR Burst-Clock AER Link

## Decision

The candidate is a frozen address-only N=16 link: one four-bit event identity is
sent over two data wires plus a source-synchronous burst clock. It is a digital
functional model of the complete TX, framing link, and RX. It contains no
arbiter, queue/asynchronous FIFO, LEDR encoding, compression, payload recovery,
or ready/backpressure compensation.

**Physical sign-off: HOLD.** Ordinary RTL simulation proves the logical edge
contract only. It does not prove minimum pulse width, clock/data skew, duty-cycle
distortion, metastability MTBF, an integrated clock-gating implementation, DDR
I/O-cell mapping, or PVT closure. A characterized ICG/ODDR/IDDR implementation
and post-layout timing/noise analysis are required before a silicon GO.

## Frozen edge and framing contract

`ref_clk_i` defines event admission. `sample_clk_i` has the same nominal
frequency and is phase shifted by one quarter reference period.

1. At a rising `ref_clk_i`, a valid address is captured. There is no internal
   queue, so upstream must present no more than one event per reference period.
2. During the high half of `ref_clk_i`, data wires carry `address[1:0]`.
   The gated burst-clock rising edge samples that low symbol.
3. During the low half, the wires carry `address[3:2]`. The burst-clock falling
   edge samples that high symbol, reconstructs `{high, low}`, and toggles the
   native retirement marker exactly once.
4. With no event, the burst clock stays low. Consecutive events retain the clock
   enable, merging their bursts into a continuous DDR clock without losing the
   fixed rising/falling pair that delimits each address.

The ordering invariant is therefore `retire[j] == accept[j]`; each accepted
event causes exactly one rising and one falling burst edge. The falling edge is
the sole commit point. The output toggle, rather than a sticky valid level,
distinguishes successive retirements and remains quiescent during idle.

Reset asynchronously forces the burst clock low and clears the TX frame state,
RX partial symbol, address, and retirement toggle. The regression applies reset
only after full drain. Mid-frame reset/flush is not an advertised delivery
contract.

## Fault boundary

The candidate-only protocol checker observes the raw burst clock. It rejects a
runt high pulse, a falling edge without a prior rise, and an opened frame without
a timely fall. These negative tests prove faults are visible and are not hidden
by adapter state. The synthesizable ideal-edge RX itself cannot measure an
analog runt pulse: it may capture both simulator edges. Missing fall produces no
retirement; a raw unmatched fall remains visible as a phantom commit. Detection
and containment at physical PVT therefore remain **HOLD**, not a claimed RTL
feature.

## Link comparison and accounting

The comparison fixes the same four address bits and a link/core frequency ratio
`R`. A pin means one data or forwarded event/clock wire; reset and supplies are
excluded equally. Clock/strobe transitions are included in toggle/event. Mean
data transitions exhaustively average all 16 previous addresses by all 16 next
addresses.

| Link | Pins | Clock edges/event | Mean total toggles/event | Max logical events/core-cycle proxy |
|---|---:|---:|---:|---:|
| parallel 4-bit + event strobe | 5 | 2 | 4.0 | R |
| A7 2-bit DDR + burst clock | 3 | 2 | 4.0 | R |
| 1-bit DDR serializer + burst clock | 2 | 4 | 6.0 | R/2 |

The A7 link removes two signal pins versus the parallel form without adding an
edge or reducing ideal event rate. It adds TX/RX registers, a two-bit symbol
mux, forwarded-clock gating, and phase/skew constraints. Against the one-bit
serializer it spends one pin to halve clock activity and double ideal logical
throughput. These are switching and capacity proxies, not extracted full-link
power or area; clock tree, pad/route capacitance, ICG and DDR-cell cost must all
be included in a later PPA decision.

At tested ratios R=1, 2, and 4, the continuous stream observed 1, 2, and 4
retirements per core cycle respectively. `full50/cap22` is not rerun through a
common scorer because this candidate intentionally does not alter frozen common
TB/manifests. Analytically, R=1 has capacity one event/core-cycle and therefore
cannot sustain a demand of two; R>=2 reaches the cap22 rate proxy, subject to the
unproved physical clock and CDC constraints.

## Verification and reproduction

The dedicated SV test performs idle-stop, 16-event back-to-back burst merge,
edge-by-edge symbol lockstep, 96-event continuous ratio stress, full-drain reset,
post-reset identity traffic, and the three malformed-clock negatives. The
Python test independently locks the exhaustive pin/edge/toggle/capacity table.
All build products and logs default to `/tmp`.

```sh
scripts/run_a7_event_triggered_ddr_burst_link.sh
git diff --exit-code 1d2c786 -- \
  tb/clean benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor
git diff --check
```

GO is limited to digital architectural exploration if all dedicated tests pass.
Silicon/full-link PPA and any claim that R=2 or R=4 closes timing remain HOLD.
