# A6 W4: A7 event-triggered DDR fixed-pin/full-endpoint audit

## Result

**HOLD_PHYSICAL_AND_FULL_ENDPOINT_PPA.** The bound A7 digital link is an exact
two-edge transport for one N16 address, and the replay preserves every event in
all 648 suite/link/ratio cases. It does not yet establish a full-endpoint PPA or
energy win. The native A7 endpoint has no queue, five runs in each official run
set still need backlog storage at a 4x link clock, and the required physical
clock/DDR cells have neither been included nor characterized.

The 2-bit DDR alternative does reduce external pins from five to three without
reducing its ideal logical service rate relative to parallel4. That is the useful
result. It is not sufficient by itself to claim energy or endpoint efficiency:
the bound TX mux toggles retained low/high address halves during idle, and its
reference/sample clock infrastructure is additional activity.

## Frozen evidence and provenance

- A7 source is read only and byte-bound to commit
  `31947a71ddfcf678f6cd593954df34b27806a63d` (`31947a7`) in
  `a6_w4_fixed_pin_registry.json`. The three bound source blobs are checked with
  `git show 31947a7:<path>` before any metric is produced.
- The A1 generator is frozen at version 4.0 and SHA-256
  `59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50`.
- full50 manifest SHA-256 is
  `9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9`;
  capacity22 is
  `99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62`.
  The registry freezes the ordered run sets and all 72 generated trace hashes.
- Registry SHA-256 is
  `fb781dfc76b7325d5ca27157d03542cdb55e0724b7ab37c3b63ee19e37b0c5a0`.
- The committed machine report contains 648 rows: 72 runs x three links x three
  clock ratios. Report SHA-256 is
  `7be6aeb9b9f7144d3b548872efa6b5db43f8db47d5f65a383fe1318bd9044772`.
  (This digest is verified again after the final diff check.)

The A7 facts used by the model are directly visible at the bound commit. TX
lines 18--23 declare five state bits and no queue; lines 25--33 retain the last
address; lines 39--49 mux address halves on `ref_clk_i` and gate the forwarded
clock. RX lines 13--33 contain two low-symbol bits, four retired-address bits,
and one retirement-toggle bit, committing once on the falling burst edge.

## Normalized contract

`R` is the number of link reference periods per core cycle and is evaluated at
1, 2, and 4. The exact same `(occurrence_cycle, sequence, logical_source)`
stream is presented to every link. `logical_source` is the complete four-bit
address-only event; there is no free payload or reconstructed metadata.

At each core boundary, all occurrences enter an ideal ordered staging FIFO. A
work-conserving serializer drains it, and replay must produce the identical
ordered occurrence list after drain. This FIFO makes lossless comparison
possible but is **not** present in A7 RTL. Its address bits plus minimal read,
write, and count state are charged as a storage/control lower bound. Collector,
same-cycle ordering logic, FIFO implementation rounding, ownership/control
combinational cells, CDC, and integration are unknown and not free.

Definitions:

- `pin-cycle` = one externally reserved pin for one link reference period.
  Events/pin-cycle uses all elapsed stimulus-plus-drain periods, including idle.
- Physical link toggles = data-wire transitions plus forwarded framing-clock or
  strobe edges. Reset activity and pads are excluded uniformly.
- Internal clock-source edges count clocks that continue to toggle even when the
  forwarded framing clock is gated. ICG-input edges are reported separately.
- Latency is from the event occurrence core boundary to RX-visible frame
  completion. Backlog changes capacity/latency, never correctness.
- The max logical event/core proxy is `R`, `R`, and `R/2` for parallel4, DDR2,
  and serial1 respectively. It is an ideal serializer ceiling, not a common-DUT
  acceptance claim.

## TX + link + RX accounting

| Link | Pins (data+frame) | Periods/event | Forwarded edges/event | Fixed link state | Ideal events/core |
|---|---:|---:|---:|---:|---:|
| parallel4 | 5 (4+1) | 1 | 2 | 10-bit model | R |
| DDR2 | 3 (2+1) | 1 | 2 | 12-bit bound A7 RTL | R |
| serial1 | 2 (1+1) | 2 | 4 | 16-bit model | R/2 |

The parallel4 state model is TX address 4 + strobe enable 1 + RX address 4 +
retire toggle 1. A7 DDR2 is TX address 4 + enable 1 + RX low symbol 2 + RX
address 4 + toggle 1. Serial1 is TX shift address 4 + enable 1 + phase 1 + RX
partial 3 + edge count 2 + address 4 + toggle 1. Parallel4 and serial1 are
explicit architectural comparison models, not synthesized candidates.

Physical items deliberately kept outside digital-state totals but explicitly
inventoried as non-free are:

- parallel4: one characterized glitch-free strobe gate/ICG and one forwarded
  strobe output buffer;
- DDR2: one characterized ICG, two ODDR data cells, two IDDR data cells, and one
  forwarded-clock output buffer;
- serial1: one characterized ICG, one ODDR, one IDDR, and one forwarded-clock
  output buffer;
- all three: clock tree, pads, skew closure, CDC/integration, PVT timing, and
  physical cell area/power.

## Link-level replay

The tables report aggregate events/pin-cycle, in-stimulus delivered event/core,
physical link toggles/event, internal clock-source edges/event, event-weighted
mean latency, the worst per-run event latency, and the maximum suite FIFO depth.
`State LB` is fixed link state plus exact-depth four-bit FIFO payload and minimal
pointer/count bits; it is a lower bound, not full endpoint area.

### full50 (106,416 exact events)

| Link | R | ev/pin-cycle | ev/core stim | toggles/event | internal edges/event | mean latency | worst latency | FIFO depth | State LB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| parallel4 | 1 | 0.15504 | 0.7339 | 3.946 | 2.58 | 661.94 | 5133 | 5132 | 20577 |
| DDR2 | 1 | 0.25840 | 0.7339 | 4.495 | 5.16 | 661.94 | 5133 | 5132 | 20579 |
| serial1 | 1 | 0.23580 | 0.4447 | 6.043 | 8.48 | 2399.55 | 14361 | 7180 | 28775 |
| parallel4 | 2 | 0.09094 | 0.8996 | 3.946 | 4.40 | 56.95 | 524 | 1047 | 4231 |
| DDR2 | 2 | 0.15157 | 0.8996 | 6.200 | 8.80 | 56.95 | 524 | 1047 | 4233 |
| serial1 | 2 | 0.19380 | 0.7339 | 6.043 | 10.32 | 661.94 | 5133 | 5132 | 20583 |
| parallel4 | 4 | 0.04588 | 0.9176 | 3.946 | 8.72 | 0.39 | 4 | 15 | 82 |
| DDR2 | 4 | 0.07647 | 0.9176 | 10.267 | 17.44 | 0.39 | 4 | 15 | 84 |
| serial1 | 4 | 0.11368 | 0.8996 | 6.043 | 17.59 | 56.95 | 524 | 1047 | 4237 |

### capacity22 (65,616 exact events)

| Link | R | ev/pin-cycle | ev/core stim | toggles/event | internal edges/event | mean latency | worst latency | FIFO depth | State LB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| parallel4 | 1 | 0.17129 | 0.8020 | 4.041 | 2.34 | 1065.12 | 5133 | 5132 | 20577 |
| DDR2 | 1 | 0.28548 | 0.8020 | 4.237 | 4.67 | 1065.12 | 5133 | 5132 | 20579 |
| serial1 | 1 | 0.24084 | 0.4550 | 6.066 | 8.30 | 3338.36 | 14361 | 7180 | 28775 |
| parallel4 | 2 | 0.11593 | 1.1435 | 4.041 | 3.45 | 91.98 | 524 | 1047 | 4231 |
| DDR2 | 2 | 0.19322 | 1.1435 | 5.240 | 6.90 | 91.98 | 524 | 1047 | 4233 |
| serial1 | 2 | 0.21411 | 0.8020 | 6.066 | 9.34 | 1065.12 | 5133 | 5132 | 20583 |
| parallel4 | 4 | 0.05906 | 1.1812 | 4.041 | 6.77 | 0.45 | 4 | 15 | 82 |
| DDR2 | 4 | 0.09843 | 1.1812 | 8.295 | 13.55 | 0.45 | 4 | 15 | 84 |
| serial1 | 4 | 0.14492 | 1.1435 | 6.066 | 13.80 | 91.98 | 524 | 1047 | 4237 |

All 648 decoded sequences equal their inputs. At R=4 the five traces still
incompatible with a no-cross-core-backlog schedule in both run sets are
`core_simultaneous_identity`, `shape_b16`, `global_fanin_identity`, and the two
`mixed_phase_always_ready` mappings. Each requires a 15-entry FIFO lower bound
and reaches four core cycles of event latency. At R=1 the worst trace requires
5132 queued addresses for parallel4/DDR2 and 7180 for serial1.

DDR2 has the best pin count at equal one-period service and beats parallel4 in
events/pin-cycle. It does not beat parallel4 in toggle or clock activity. Its
data toggle/event rises with `R` because A7 TX lines 39--43 continuously select
the retained low/high halves as `ref_clk_i` changes even when the burst clock is
off. This is why DDR2 data toggles are 8.267/event in full50 R=4, rather than the
address-transition-only value near two.

Serial1 can score well in events/pin-cycle because it reserves only two pins,
but it halves logical service, creates much larger backlog/latency at equal R,
and needs four framing edges/event. It is not a simultaneous throughput win.

## Decision boundary

The digital representation and RX framing are executable and exact, so this is
not a correctness HOLD. It is a qualification HOLD:

1. No option has a measured full TX/link/RX endpoint area, timing, or energy.
2. A7's 12 state bits do not include the storage needed by the official
   multi-occurrence streams. Adding the lower-bound FIFO changes endpoint cost.
3. Collector/sorter/control and actual FIFO cells remain unknown; the reported
   state lower bound must not be relabeled as full endpoint PPA.
4. Clock tree, ICG, ODDR/IDDR, forwarded-clock buffer, pad, and skew costs are
   inventoried but uncharacterized. They are not zero.
5. DDR2's idle data switching must be removed with a physically safe hold/isolate
   mechanism or paid in post-layout activity before an energy GO is possible.

## Reproduction

Run `scripts/run_a6_w4_fixed_pin_audit.sh`. It regenerates fresh full50 and
capacity22 traces, verifies every pinned input and A7 source blob, runs eight
unit/mutation tests, produces 648 exact replay rows, and checks the explicit
HOLD decision. No A7, common runner, common TB, manifest, or owned RTL file is
modified.
