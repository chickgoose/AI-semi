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

## Lockstep and malformed-edge evidence

W4 reuses the exact committed W3 testbench through test-only module aliases; W3
RTL and TB are not edited. Ratios 1, 2, and 4 pass idle stop, 16-event continuous
burst merge, edge-by-edge symbol comparison, 96-event sustained traffic,
full-drain reset, and post-reset identity. The observed maxima remain 1, 2, and
4 logical events per core cycle.

The same raw-clock negative tests detect runt, missing rise, and missing fall.
The additional ICG boundary test deliberately changes enable during clock high:
the current pulse completes, the next complete cycle alone reflects the new
enable, and no shortened high pulse appears. This is event-driven RTL evidence,
not a cell-delay or analog glitch simulation.

## Same-top local structural comparison

All three references elaborate through `a7_w4_structural_compare_top` with the
same logical event input, ready output, reconstructed identity/toggle output,
raw clock observation, and padded raw-data observation. Each implementation
passes the same 32-event identity test. Physical link pins are counted from the
unpadded boundary semantics: four data plus strobe for parallel, two data plus
clock for DDR2, and one data plus clock for serial1.

Yosys 0.52 runs `proc; flatten; opt`, then reports generic functional cells and
state bits; `$scopeinfo` cells are excluded. `ltp -noff` reports combinational
operator depth before and after generic `techmap`. No standard-cell library,
wire estimate, CTS, pad model, or timing-driven mapping is used.

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

Digital implementation evidence is **GO**: exact framing is preserved, the only
generated-clock logic is isolated behind a replaceable ICG contract, malformed
edges remain observable, and the generic structural comparison is reproducible.

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
