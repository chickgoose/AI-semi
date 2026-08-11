# A9_W4_FIX_SUMMARY

- Fixed semantic bug: `run_core` now drives only `zero_extend(source)` into the
  exact A4 model.  Unique occurrence IDs and timing are TB-only source-local
  sidecars; node and retirement equality are asserted every cycle.
- Replayed official generator-v4 full50/cap22.  Accepted/delivered, overrun,
  throughput, and latency are unchanged.  Address-only core state-toggle
  proxies are now full50 fixed/moving `2,323,775 / 1,511,352` and capacity22
  `1,215,726 / 847,126`.
- Corrected storage language: zero means only no newly inserted A4→A7 FIFO.
  The model retains 31 A4 internal event slots plus 16 ingress source latches;
  observed maxima are 30/31 fixed, 31/31 moving, and 16/16 source latches.
- Preserved requested A7 commit `31947a7`: DDR state remains 12 bits in this
  tournament.  Its idle ref-clock data-mux activity is now counted.  Latest A7
  is `db3f04f`; structural-evidence ancestor `a349d64` uses a separate ICG latch
  and reports 13 bits.  Both are explicitly excluded rather than blended into
  the old-commit results.
- Both continuously toggling old-A7 clock inputs are exposed as a separate
  unit-edge proxy (not falsely converted to power); full50 moving R=1/2/4 is
  `5.479/10.959/21.918` input-clock edges per delivered event.
- R=1 is analytical/rate-compatible only, not an executed RTL composition, and
  reset is absent from the composition model.  R=2/4 remain capacity envelopes.
  The missing one-link-period qualifier has unknown state, timing, and toggle
  cost, so no free qualifier or queue is modeled and composition remains HOLD.
