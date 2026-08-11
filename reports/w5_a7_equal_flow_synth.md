# W5 A3 equal-flow A7 endpoint synthesis

Decision: **PHYSICAL_HOLD**. The generic mapping is a structural economics
screen, not timing, route, CDC, or power evidence.

## Frozen boundary

- Production W5 endpoint commit: `42377ca81340951bfcd453b3bd664e673091f9f3`
- Same synthesis top: `a3_w5_r1_endpoint_top`, STYLE=0 parallel and STYLE=1 DDR
- R1 load: one frame at every `valid && ready` ref-clock rising edge; continuous
  valid with a new address each accepted cycle is supported.
- Both production tops charge the same one-bit reset-release arming qualifier;
  the first safe ref edge arms ready and does not accept a transaction.
- Valid-edge one-shot suppression: rejected (0 additional state bits). The witness has
  four legal handshakes but an edge detector would launch only two.
- Both production styles include the identical six-bit consumer observer: one
  seen-toggle bit, one valid register, and four address registers.
- Frozen clocks are phase-related: 16 ns period,
  4 ns sample phase. RX commits at burst fall and
  endpoint retire output becomes available 4
  ns later at the next ref rise (1 ref cycle after launch). A distinct synchronous
  sink consumes that registered output at the following ref rise (2 cycles after
  launch). This is **not a 2FF CDC claim**.
- Consumer backpressure, unrelated clocks, and handshake/FIFO variants are out
  of scope.

The production endpoint already contains reset arming, the phase-related
seen-toggle observer, the complete parallel reference, and its digital
regression. Its fail-closed drain guard covers same-cycle launch, active frame,
unobserved raw commit, and registered valid pending synchronous consumption.
The local A3 wrapper only selects one production top and pads the
two-bit DDR link observation port; it adds no state or functional behavior.
The runner compiles and executes the pinned production TB and requires every
named PASS marker exactly once before synthesis results can be published.

## Equal-flow generic results

| Metric | Complete parallel4 | A7 DDR2 | DDR - parallel |
|---|---:|---:|---:|
| Charged functional cells, scopeinfo removed | 27 | 29 | +2 |
| ABC generic mapped cells | 30 | 34 | +4 |
| Combinational cells | 12 | 14 | +2 |
| Sequential bits | 18 | 20 | +2 |
| DFF bits | 17 | 19 | +2 |
| ICG latch bits | 1 | 1 | +0 |
| Comb depth proxy | 5 | 5 | +0 |
| Nets / net bits | 26 / 38 | 28 / 44 | +2 / +6 |
| Max data fanout | 7 | 9 | +2 |
| Data sink-pin proxy | 49 | 57 | +8 |
| Logical unpadded link signals | 5 | 3 | -2 |

The 5-versus-3 count means logical unpadded data signals plus the forwarded
clock/strobe; it is not a physical pad or package-pin count. DDR saves two such
signals but costs two sequential bits, two charged functional cells, and four
ABC-mapped cells in this exact boundary. The final totals independently reproduce
27/29; their common four-cell drain-guard decomposition is inherited owner
accounting, not an A3 base-blob subtraction. Generic latch/flop logic does not prove a
characterized ICG/ODDR/IDDR implementation, timing closure, routed wire savings,
or energy benefit; all physical claims remain HOLD.

## Reproduction

```sh
python3 scripts/w5_a7_equal_flow_synth/run.py \
  --output reports/w5_a7_equal_flow_synth.json
python3 -m unittest scripts.w5_a7_equal_flow_synth.test_run
```

The runner receipts its own bytes, the vendored W5 helper, and Python identity,
and SHA-checks every pinned A7 git object, the frozen SDC, the independent
wrapper, Verilator, Yosys, ABC, and its Tcl runtime. It observes and explicitly
allows only Verilator `DECLFILENAME` diagnostics and ABC's exact combinational-
network warning; this is not a warning-free claim. Missing/duplicate digital
PASS markers, unexpected warnings, unresolved objects, residual
processes/memories, scopeinfo-contaminated functional counts, state-count changes,
drain-contract changes, or Yosys check failures fail closed.
The JSON and this Markdown file are atomically replaced and byte-deterministic.
