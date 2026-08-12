# f786982 DDR hold root cause (HOLD)

Evidence is from the preserved run root
`/tmp/a6-w7-physical-f786982-full-20260812T171655Z-23034`.  This note does
not qualify that run and does not relax its 0.500 ns uncertainty or reset
coverage.

## reg2cgate: -0.413 ns

The violated clock-gating hold path is
`endpoint_tx_clock_boundary/enable_latched_q_reg/Q` to
`endpoint_tx_clock_boundary/g54__6260/A`, checked against the sample-clock
input `g54__6260/B` on the trailing edge.  The synthesized boundary is a
`TLATXL` plus `AND3X1`; Innovus did not identify a characterized ICG.  The
enable path arrives at 11.854 ns, while the required time is 12.267 ns after
the 0.500 ns uncertainty.  The clock side reaches the AND through two CTS
buffers at 11.767 ns.

The structural correction is to replace the generic latch-and-AND physical
implementation with a library-characterized ICG boundary that Genus and
Innovus both recognize, or to arm a registered enable early enough in the
sample-low phase before feeding that ICG.  The selected ICG must retain the
asynchronous-reset release contract and its recovery/removal coverage.  It
must also pass the existing cycle/edge-exact owner-versus-staged smoke before
another physical run.  A false path, multicycle exception, smaller
uncertainty, or deleted reset arc is not an acceptable correction.

## reg2reg: -0.027 ns and -0.004 ns

The worst path is the ref-clock self-feedback path from
`canonical_fovea_col_arb_pair_lo_last_gnt_reg/Q` through `CLKBUFX2`,
`NAND2XL`, and `NAND3BXL` back to that register's D pin.  Arrival is 0.394 ns
and required time is 0.421 ns.  The second violation runs from
`endpoint_retire_observer_seen_toggle_o_reg/Q` through `CLKXOR2X1` and
`CLKBUFX2` to `endpoint_retire_observer_retire_valid_o_reg/D`; its slack is
-0.004 ns.

Post-route hold optimization already inserted delay/clock buffers and reduced
the residual reg2reg failure, but did not close it.  The structural correction
is a targeted hold ECO using characterized minimum-delay cells on these data
paths (or an equivalent placement/routing ECO), followed by extraction and
full setup/hold/recovery/removal re-analysis.  It must preserve Boolean and
handshake behavior.  Clock uncertainty and reset coverage remain unchanged.
