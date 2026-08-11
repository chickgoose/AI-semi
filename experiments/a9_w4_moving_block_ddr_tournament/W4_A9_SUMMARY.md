# W4_A9_SUMMARY

- Inputs: read-only exact A4 `850fbcf` moving-block core, A7 `31947a7`
  event-triggered DDR link, generator-v4 full50/cap22 (50/22 exact hashes).
- Result: moving core changes full50 accepted `83,514→83,555` and cap22
  `42,948→42,983`; throughput rises only 0.108%/0.111%, while p95/p99 regress
  one cycle and child-control touches double.
- Link: DDR changes no accepted/delivered/overrun/throughput at R=1.  It changes
  5→3 pins, 1,162→1,174 state bits, adds 0.75 core cycle and register toggles.
- Bottleneck/buffer: both cores peak at one event/core-cycle; link capacity is
  R=1/2/4, so the legal event-token envelope needs zero boundary buffer and is
  core/ingress-limited for every tested R.
- Blocker: exact A4 level-valid directly sampled by faster A7 ref clocks is
  valid only at R=1.  R=2/4 creates extra captures without an unimplemented
  one-link-period launch qualifier; those rows are capacity envelopes and HOLD.
- Verdict: simple serial composition, not a novel architecture.  R=1 retains
  four tradeoff points; overall adoption/PPA is HOLD, with no queue or free
  boundary functionality added.
