# Official full50 steal-buffer comparison evidence

This directory preserves the upstream Xcelium comparison evidence used only
for the architecture-family loss comparison in presentation slide 6.

- Upstream repository: `GangHeeJo/AI-SEMI`
- Result/TB introduction commit: `cb0cf15e26d0bda7b9c11dcb764affb43ae4a0c2`
- Audited receipt checkout: `f2f93a830414aff2e0a3b7db05154294e1d4b78d`
- Simulator recorded by the upstream audit: Xcelium `23.09-s013`
- Workloads: the official common `full50` set

The rigorous result contains 50 `TRACE=` summaries and 50
`PHANTOM_DEBUG_PASS` witnesses. Direct aggregation gives:

- generated: 106,416
- delivered: 105,914
- dropped overrun: 502
- phantom: 0
- steal-buffer loss: `502 / 106,416 = 0.4717%`, displayed as `0.47%`

The upstream independently recovered base-Cluster2 comparison at the same
106,416-event denominator is 12,259 losses (`11.5199%`, displayed as
`11.52%`). Therefore the loss-count reduction is `12,259 / 502 = 24.42x`, or
95.9% relative reduction.

This comparison is distinct from the final polarity-v1 UZH run. The latter
uses `source/tb/redred_cluster2_polarity_v1_native_observational_tb.sv` and
proves 8,503 generated/delivered events with zero overrun and polarity
mismatch. The two denominators must not be merged.

SHA-256:

- `steal_buf_rigorous_results.txt`:
  `7aa6e6da4b443903337f09e2c9c457723e37f1991523cd3829e30079e1dccde4`
- `tb_steal_buf_trace_phantom_debug.v`:
  `06123cc83e1682e7175c220a762fd3ff75bd40fd7795565e5f381ac55167556e`
