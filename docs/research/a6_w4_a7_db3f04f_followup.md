# A6 W4 follow-up: A7 `db3f04f` conservative fixed-pin audit

## Decision

**HOLD_PHYSICAL_AND_FULL_ENDPOINT_PPA.** Commit
`db3f04fe0e01699e63c596145fe71effc601e57c` materially improves the digital
clock-gate boundary and test-only fault evidence. It does not demonstrate a
physical PPA, throughput, or power victory. The updated 648 replay cases remain
lossless and retain the historical external service schedule; the changes are
state/control accounting and stronger qualification boundaries, not extra link
capacity.

The historical audit and machine result at A6 commit `8fed980` are preserved
byte-for-byte. This document and `replay-db3f04f.json` supersede only their A7
implementation assumptions.

## Exact historical-assumption diff

| Boundary | `8fed980` assumption bound to `31947a7` | Latest `db3f04f` evidence | Metric consequence |
|---|---|---|---|
| DDR2 fixed state | 12 bits: TX 5 + RX 7 | 13 bits: TX 5 + explicit low-phase ICG latch 1 + RX 7 | every DDR2 storage lower bound increases by 1 bit |
| parallel4 fixed state | 10-bit architectural model | 11-bit same-top generic RTL: TX data 4 + enable 1 + ICG latch 1 + RX address 4 + toggle 1 | every parallel4 storage lower bound increases by 1 bit |
| serial1 fixed state | 16-bit architectural model | 16-bit same-top generic RTL with a now-pinned state breakdown | total unchanged |
| generic cells | not available as a bound A7 result | parallel4 11, DDR2 13, serial1 26 after generic Yosys flatten/opt | structural proxy only; no library area implication |
| clock gate | combinational digital expression; physical ICG required | separate low-transparent enable latch plus phase-preserving gate | high-phase enable changes cannot create/truncate a pulse in the RTL model; characterized ICG still required |
| parallel clock accounting | only one continuously running source clock charged | same-top parallel reference uses both `ref_clk_i` and `sample_clk_i` | internal source-clock edge proxy doubles relative to the old parallel model |
| idle forwarded clock | stopped when enable is low | stopped by the generic ICG boundary | unchanged external clock-edge count |
| idle DDR data | retained address halves alternate with `ref_clk_i` | TX data mux is unchanged and still alternates them | idle data cost remains and is now reported separately |
| strict fault checking | not part of the A6 replay | independent ten-mutation action/edge/symbol/reset oracle | test-only evidence; zero synthesized state and no live detection/containment |
| timing/CDC | phase relationship and missing physical cells noted | candidate SDC/manifest freeze 16 ns period, 4 ns phase, 7 ns minimum high/low, 0.5 ns uncertainty; RX remains burst-clock domain | constraints are a specification, not STA/CDC/PVT closure |

The ICG RTL at `db3f04f` lines 13--25 contains the added state bit and gate. TX
lines 16--50 retain the four-bit address, one frame-enable bit, the reference-
clock-selected data mux, and instantiate that boundary. RX lines 13--33 remain
seven state bits. The same-top reference reports state 11/13/16 and generic cells
11/13/26; the A7 documentation explicitly classifies these as generic structural
proxies rather than physical PPA.

## Strict oracle scope

The pinned oracle rejects missing/extra edges, a rise over an open frame, high or
low duty distortion, runt pulses, unstable/unknown symbols, symbol or
reconstruction errors, removed merged boundaries, bad schedules, and reset with
traffic in flight. The follow-up runner executes its ten mutation tests directly
from a `git archive` of `db3f04f`, not from the mutable A7 checkout.

It is not instantiated by TX or RX. It neither consumes 13-bit endpoint state nor
detects, contains, retries, or resynchronizes a live fault. Therefore it improves
test qualification only. Reset remains supported only after drain with the burst
clock low; downstream core CDC synchronization is still excluded.

## Conservative activity model

The external link definitions remain five pins for parallel4, three for DDR2,
and two for serial1. At `R` link periods/core cycle, ideal serializer ceilings
remain `R`, `R`, and `R/2`. Delivery-window events/pin-cycle and latency therefore
equal the historical replay. The conservative headline below additionally
charges a terminal quiescence period when needed. No throughput is attributed to
the latch or oracle.

New accounting separates:

- active versus idle DDR data transitions;
- low-phase ICG latch transitions, including merged bursts;
- a terminal quiescence period when the last drained event still leaves frame
  enable asserted;
- continuously running ref/sample source-clock and ICG-input edges;
- generic fixed state from FIFO/storage lower bounds;
- characterized ICG, DDR cells, CTS, pads, CDC, and physical interconnect as
  `unknown_not_free`.

### full50, 106,416 events

| Link | R | fixed state | State LB | ev/pin-cycle incl. quiesce | link toggles/event incl. quiesce | idle data/event | ICG latch/event | internal edges/event incl. quiesce |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| parallel4 | 1 | 11 | 20578 | 0.15501 | 3.946 | 0 | 0.189 | 5.16 |
| DDR2 | 1 | 13 | 20580 | 0.25835 | 4.496 | 0.524 | 0.189 | 5.16 |
| serial1 | 1 | 16 | 28775 | 0.23576 | 6.043 | 0 | 0.031 | 8.48 |
| parallel4 | 2 | 11 | 4232 | 0.09094 | 3.946 | 0 | 0.986 | 8.80 |
| DDR2 | 2 | 13 | 4234 | 0.15157 | 6.200 | 2.229 | 0.986 | 8.80 |
| serial1 | 2 | 16 | 20583 | 0.19378 | 6.043 | 0 | 0.189 | 10.32 |
| parallel4 | 4 | 11 | 83 | 0.04588 | 3.946 | 0 | 1.367 | 17.44 |
| DDR2 | 4 | 13 | 85 | 0.07647 | 10.267 | 6.296 | 1.367 | 17.44 |
| serial1 | 4 | 16 | 4237 | 0.11368 | 6.043 | 0 | 0.986 | 17.59 |

### capacity22, 65,616 events

| Link | R | fixed state | State LB | ev/pin-cycle incl. quiesce | link toggles/event incl. quiesce | idle data/event | ICG latch/event | internal edges/event incl. quiesce |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| parallel4 | 1 | 11 | 20578 | 0.17125 | 4.041 | 0 | 0.048 | 4.67 |
| DDR2 | 1 | 13 | 20580 | 0.28542 | 4.238 | 0.269 | 0.048 | 4.67 |
| serial1 | 1 | 16 | 28775 | 0.24081 | 6.066 | 0 | 0.022 | 8.31 |
| parallel4 | 2 | 11 | 4232 | 0.11593 | 4.041 | 0 | 0.526 | 6.90 |
| DDR2 | 2 | 13 | 4234 | 0.19321 | 5.240 | 1.272 | 0.526 | 6.90 |
| serial1 | 2 | 16 | 20583 | 0.21408 | 6.066 | 0 | 0.048 | 9.34 |
| parallel4 | 4 | 11 | 83 | 0.05906 | 4.041 | 0 | 1.114 | 13.55 |
| DDR2 | 4 | 13 | 85 | 0.09843 | 8.295 | 4.327 | 1.114 | 13.55 |
| serial1 | 4 | 16 | 4237 | 0.14491 | 6.066 | 0 | 0.526 | 13.80 |

`State LB` is fixed generic state plus the exact-depth four-bit staging FIFO and
minimal pointer/count state. It is not full endpoint area. The worst FIFO depths
remain 5132 entries for parallel4/DDR2 at R=1, 1047 at R=2, and 15 at R=4.
Collector/sorter, same-cycle ordering, implementation-rounded FIFO cells,
ownership/control, CDC, and integration remain excluded and non-free.

The DDR link still improves events/pin-cycle over parallel4 because it reserves
three instead of five pins at the same ideal service rate. It does not win the
reported toggle proxy: full50 DDR2 reaches 10.267 link toggles/event at R=4,
including 6.296 idle data transitions/event. Serial1 can lead events/pin-cycle
while retaining half the logical service ceiling and higher backlog/latency.
These are trade-offs, not a Pareto or throughput victory.

## Physical and qualification boundary

The generic ICG latch is real synthesized RTL state and is included in 13/11.
It is not a substitute for the characterized ICG required by the manifest.
ODDR/IDDR mapping, CTS, forwarded-clock buffer, clock/data skew, recovery/removal,
reset-domain analysis, pads, routing capacitance, target-library timing/area, PVT,
and extracted full-link power remain missing. Neither the generic cell ordering
11 < 13 < 26 nor the fixed-pin replay qualifies physical area, energy, or maximum
frequency.

## Provenance and reproduction

- Latest registry SHA-256:
  `41f5a800660d2c08d770dae4c5db1fb67bd650de9536c3ff7fbb04399b417da4`.
- Latest machine report SHA-256:
  `8c89876de1efa7d4f82c9b8885a370a9e302ee422de6d18a4e73bed478a8227b`.
- Historical registry/report remain
  `fb781dfc76b7325d5ca27157d03542cdb55e0724b7ab37c3b63ee19e37b0c5a0`
  and `7be6aeb9b9f7144d3b548872efa6b5db43f8db47d5f65a383fe1318bd9044772`.

Run `scripts/run_a6_w4_db3f04f_followup.sh`. It regenerates and SHA-validates
full50/capacity22, checks all pinned A7 blobs with `git show`, executes the A6
models and the bound strict-oracle mutations, and requires 648 exact replay rows
with an explicit HOLD. It does not modify A7 or common files.
