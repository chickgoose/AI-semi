# K2 W2 fair physical tops

These three stateless wrappers are the W2 full-composition cohort:

| design | synthesis top | ordered file list | native link pins |
| --- | --- | --- | ---: |
| Fovea + A7 | `k2_w2_fovea_a7_top` | `filelists/fovea_a7.f` | 3 |
| A2 + P6 | `k2_w2_a2_p6_top` | `filelists/a2_p6.f` | 6 |
| A3 + P6 | `k2_w2_a3_p6_top` | `filelists/a3_p6.f` | 6 |

Every top has the same 19-bit input boundary: `ref_clk_i`, `sample_clk_i`,
active-low `rst_n`, and the 16-source level-held `load_i` bitmap. The P6
composition mode gate is tied high inside its wrappers. Outputs remain native:
the two-bit A7 DDR bus is not padded to the five-bit P6 bus, and scalar retire
is not widened to two lanes. Port accounting must therefore compare native
output and link costs rather than a synthetic normalized bus.

The wrappers add no state, queues, decoding, or arbitration. The canonical
scalar Fovea fixture appears here only as part of the previously qualified
Fovea+A7 complete endpoint.

This cohort is separate from both standalone Fovea/Cluster2 physical
boundaries. The raw core-only cohort is recorded in
[`../k2_w2_raw_golden`](../k2_w2_raw_golden/README.md). The buffered cohort is
recorded in [`../k2_w2_server_golden`](../k2_w2_server_golden/README.md) and
uses the exact server files `aer_fovea_buffered`, `aer_cluster2_buffered`, and
`lane_buffer2` from `/tmp/ganghee-pnr-golden-20260813`.

[`../k2_w2_boundaries.json`](../k2_w2_boundaries.json) freezes the three
cohorts and prohibits combining their area or power rankings. Raw cores,
buffered wrappers, and complete endpoints are not interchangeable boundaries.

Run the local gates with:

```sh
tests/k2_w2_tops/run_all.sh
```
