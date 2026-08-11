# W4_A9_SUMMARY

- Inputs: read-only exact A4 `850fbcf` moving-block core, A7 `31947a7`
  event-triggered DDR link, generator-v4 full50/cap22 (50/22 exact hashes).
- Semantics: every DUT-visible 32-bit word is zero-extended source/address;
  occurrence IDs and timing remain only in source-local TB scoreboard deques.
- Result: moving core changes full50 accepted `83,514→83,555` and cap22
  `42,948→42,983`; throughput rises only 0.108%/0.111%, while p95/p99 regress
  one cycle and child-control touches double.
- Link: DDR changes no accepted/delivered/overrun/throughput at R=1.  Frozen
  `31947a7` changes 5→3 pins, 1,162→1,174 state bits, adds 0.75 core cycle, and
  its toggle proxy now includes idle low/high-symbol mux activity.
- Bottleneck/buffer: both cores peak at one event/core-cycle; link capacity is
  R=1/2/4, so the legal event-token envelope needs zero **added A4→A7 FIFO** and
  is core/ingress-limited.  A4 still has 31 slots and ingress has 16 latches.
- Evidence boundary: R=1 is an analytical, rate-compatible legal-launch model,
  not executed A4-RTL→A7-RTL composition; its reset path is absent.  R=2/4
  creates extra captures without an unimplemented
  one-link-period launch qualifier of unknown cost; those rows are envelopes
  and HOLD.
- Version boundary: latest A7 is `db3f04f`; `a349d64` is its structural-evidence
  ancestor with a separate ICG latch and 13 state bits.  Both are explicitly
  excluded rather than mixed into the frozen `31947a7` tournament.
- Verdict: simple serial composition, not a novel architecture.  R=1 retains
  four tradeoff points; overall adoption/PPA is HOLD, with no queue or free
  boundary functionality added.
