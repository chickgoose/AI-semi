# W4 A7 Physicalizable-Digital DDR Burst Link

## Outcome and boundary

W4 preserves commit `31947a7` and adds a separate implementation. The framing
contract is unchanged: address-only N16 identity uses two data wires; the
forwarded burst-clock rising edge captures address bits `[1:0]`, and its falling
edge captures `[3:2]` and commits exactly once. Idle stops the forwarded clock,
and back-to-back frames merge into a continuous edge stream.

W3's direct combinational clock expression is replaced by the explicit
`a7_w4_icg_boundary`. Its generic synthesizable model uses a low-transparent
enable latch followed by a phase-preserving gate. Enable changes during clock
high cannot produce a late edge or truncate the active pulse. ASIC integration
must map the complete module to one characterized ICG; decomposing it into an
unqualified fabric AND is prohibited. The data path remains an ordinary mux
whose transitions occur a nominal quarter cycle before the associated sampling
edge. An ODDR/IDDR macro may replace the TX/RX boundary if it preserves those
edge identities and timing relationships.

## Frozen timing and CDC assumptions

The candidate-only SDC and JSON manifest freeze a 16 ns nominal period, 50%
duty cycle, and a 4 ns sample-clock phase offset from the reference clock.
Minimum legal high and low pulse widths are each 7 ns, leaving a nominal 1 ns
duty-distortion budget. Clock uncertainty/skew is capped at 0.5 ns in the
constraint model. The burst clock is declared as a phase-preserving generated
clock from `sample_clk_i`.

TX admission and frame state use `ref_clk_i`. RX capture and retirement use the
forwarded `burst_clk_o`; `retire_addr_o` and `retire_toggle_o` remain in that
domain. W4 intentionally does not add an asynchronous FIFO or pretend that the
retirement output is core-clock synchronous. Receiver integration must provide
a separately verified toggle synchronizer and coherent address capture if the
consumer clock is unrelated. Reset is asynchronous but supported only after
full drain while the burst clock is low. Mid-frame reset can truncate a clock
pulse and has no delivery guarantee.

The SDC deliberately does **not** apply a blanket false path from `rst_n`.
Doing so could suppress recovery/removal analysis while creating the appearance
of closure. Target-library recovery/removal constraints, reset-release timing,
and RDC analysis remain required and are **HOLD**; the candidate SDC alone does
not close them.

## Lockstep and observer-only fault evidence

W4 reuses the exact committed W3 testbench through test-only module aliases; W3
RTL and TB are not edited. Ratios 1, 2, and 4 pass idle stop, 16-event continuous
burst merge, edge-by-edge symbol comparison, 96-event sustained traffic,
full-drain reset, and post-reset identity. The observed maxima remain 1, 2, and
4 logical events per core cycle.

Independent audit `f92196b` showed that the inherited manual checker is not a
general fault detector: a new rise can overwrite an open frame, and extra normal
edge pairs, high/low duty distortion, a removed merged boundary, and unknown or
unstable symbols can false-pass that checker. Its runt/missing-rise/missing-fall
markers are therefore retained only as legacy directed observations.

W4 now has an independent, test-only action/edge/symbol/reset schedule oracle.
It accepts the exact legal two-event merged schedule and rejects missing-fall
before a next rise, an extra edge pair, high and low duty distortion, a removed
back-to-back boundary, unstable and unknown symbols, runt, missing rise, and
reset with traffic in flight. The oracle is not connected to the RTL and cannot
contain or resynchronize a live fault. There is no synthesizable runtime fault
monitor in W4; fault detection and containment are explicitly **not claimed**.

The separate ICG boundary test still establishes one narrower digital property:
changing enable during clock high does not shorten the modeled pulse. This is
event-driven RTL evidence, not cell-delay or analog glitch simulation.

## Same-top local structural comparison

All three references elaborate through `a7_w4_structural_compare_top` with the
same logical event input, ready output, reconstructed identity/toggle output,
raw clock observation, and padded raw-data observation. Each implementation
passes the same 32-event identity test. Physical link pins are counted from the
unpadded boundary semantics: four data plus strobe for parallel, two data plus
clock for DDR2, and one data plus clock for serial1.

Yosys 0.52 runs `proc; flatten; opt`, then reports generic functional cells and
state bits; `$scopeinfo` cells are excluded. Included are generic TX state/data
selection, generic RX rising/falling-edge registers, and the generic ICG
latch-and-gate model. `ltp -noff` reports combinational operator depth before and
after generic `techmap`.

Excluded are characterized ICG, ODDR/IDDR cells, clock-tree buffers/CTS, link
routing, pads, wire/load capacitance, and downstream CDC synchronization. No
standard-cell library or timing-driven mapping is used. The table is a same-top
generic RTL structural proxy and must not be called physical area, physical PPA,
or full-link PPA.

| Link | Physical pins | Events/link cycle | Functional cells | State bits | Operator/generic depth |
|---|---:|---:|---:|---:|---:|
| parallel4 | 5 | 1.0 | 11 | 11 | 2 / 2 |
| W4 DDR2 | 3 | 1.0 | 13 | 13 | 2 / 2 |
| serial1 DDR | 2 | 0.5 | 26 | 16 | 2 / 2 |

Thus DDR2 costs two generic cells and two state bits versus parallel4 while
removing two link pins without changing the ideal link-cycle event rate. It
uses one more pin than serial1 but halves edge demand and, in this fixed model,
uses fewer cells/state. These counts include the generic latch-and-gate ICG
model; a characterized ICG/DDR cell can change the area result.

## Decision

Digital implementation evidence is **GO** only for exact nominal framing and
technology-boundary exploration. The only generated-clock logic is isolated
behind a replaceable ICG contract, and the generic structural comparison is
reproducible. Malformed schedules are rejected by a test-only oracle; W4 has no
synthesizable fault detection or containment.

Physical status remains **HOLD** until a target library maps the ICG and dual-edge
capture boundary, STA validates half-cycle setup/hold and recovery/removal,
CDC/MTBF is closed at the consuming core, and post-route PVT/CTS/extracted power
includes the full clock, link routing, and I/O loads.

```sh
scripts/run_a7_event_triggered_ddr_burst_link_w4.sh
git diff --exit-code 31947a7 -- \
  tb/clean benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor \
  rtl/candidates/a7_event_triggered_ddr_burst_link
git diff --check
```
