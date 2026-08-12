# A7 W7 weighted-Fovea DDR digital submission contract

Status: **DIGITAL GO** for the mandatory address-only, sink-always-ready,
phase-related R1 scope. **PHYSICAL HOLD** and optional backpressure/CDC SKIP.

## Frozen competition boundary

The synthesizable candidate remains `a7_weighted_fovea_ddr`: the SHA-pinned
canonical N=16 weighted fovea feeds the charged A7 R1 TX, generic ICG boundary,
DDR RX, and ref-domain retire observer. The wrapper has zero sequential state
and no queue. Address identity is the canonical four-bit address; no payload or
TB metadata is reconstructed.

The mandatory common cohort is sink-always-ready. The top therefore exposes a
one-cycle `retire_valid_o` at the ref-domain observation boundary and has no
retire-ready input. Output backpressure is `SKIP_UNSUPPORTED`; a TB FIFO or
adapter must not convert it to RUN. Likewise, unrelated clocks are unsupported:
`ref_clk_i` and `sample_clk_i` are equal-frequency clocks from the same frozen
source with sample at `+T/4`. A backpressured or unrelated-clock product needs
an explicitly synthesized and charged handshake/FIFO variant and is not W7.

The source side is normal ready-valid by source bit. A live source remains
asserted until its one-hot `source_ready` handshake. Stateless current-result
masking prevents the registered level result from acknowledging the same
occurrence twice while leaving all other live requests under canonical weighted
arbitration. There is no valid-edge detector, retry history, source queue,
output queue, or free backpressure compensation.

Reset assertion is legal only after `drain_idle_o` and while the forwarded burst
clock is low. It cannot abort accepted traffic and mid-traffic flush is not a
supported capability. Release occurs with both source clocks low; R0 is the
charged endpoint arm edge and the first acceptance is R2. The W6 directed test
already checks pending output, same-cycle launch, raw endpoint work, disjoint
reset epochs, stale results, and final consumer timing.

## Timing and physical boundary

`constraints/a7_weighted_fovea_ddr_w7.sdc` freezes 16 ns clocks, sample phase
`+4 ns`, 8/8 ns nominal duty, 7 ns minimum high/low pulse, 0.5 ns uncertainty,
the generated burst clock, both DDR output edges, and ref-domain source/retire
I/O delays. Reset is deliberately not blanket false-pathed, so recovery/removal
and RDC evidence cannot be hidden.

This makes the interface and digital test reproducible; it does not prove
mapped ICG/ODDR/IDDR availability, half-cycle STA, recovery/removal, CDC/RDC,
PVT, CTS, routing, pin load, or energy/event. Server Genus/Innovus evidence is
still absent, so physical qualification and physical PPA remain **HOLD**.

## Fail-closed evidence

Run:

```sh
scripts/run_a7_weighted_fovea_ddr_w7_submission.sh
```

The runner requires the three canonical SHA-256 pins, tracked/clean W7 inputs,
the prior exact W6 directed/Yosys gate, and ten expected-fail mutations. Five
RTL mutants cover continuous-throughput bubbles, early/late reset arming,
endpoint-drain escape, and same-address second-grant suppression. Five contract
mutants reject false backpressure, unrelated-CDC, free-queue, mid-traffic-reset,
and phase-drift claims with unique diagnostics.

The new canonical exhaustive TB evaluates all 65,536 N16 live bitmaps. Empty is
required quiet; every non-empty bitmap must produce one one-hot live-source
acceptance and exactly one identical output/consumer retirement at the frozen
+1/+2 ref-cycle boundaries, with full drain before the next bitmap. This proves
all static request sets, not every possible temporal sequence.

The only final marker is `A7_W7_DIGITAL_SUBMISSION_PASS`; it is not a full50 or
physical qualification receipt. Unsupported optional suites remain explicit
SKIP, while failure of any baseline, exhaustive, structural, provenance, or
mutation check exits nonzero.
