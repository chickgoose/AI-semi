# K2 W2 authoritative buffered Fovea/Cluster2 server boundary

The authoritative **buffered** Fovea/Cluster2 physical boundary is the
read-only server snapshot at:

```text
/tmp/ganghee-pnr-golden-20260813
```

It uses `aer_fovea_buffered` and `aer_cluster2_buffered`, not the raw core-only
tops. Both wrappers include the exact `lane_buffer2.sv` from that
snapshot: one instance for scalar Fovea and two independent instances for
Cluster2. The associated P&R evidence is likewise taken only from the snapshot's
`synth/pnr/resynth_fovea_buffered` and
`synth/pnr/resynth_cluster2_buffered` report directories.

[`server_golden.json`](server_golden.json) binds every RTL source by SHA-256,
the exact report periods, report counts, and an aggregate digest over the
sorted report inventory. The `.f` files contain absolute paths deliberately:
they elaborate the authoritative bytes in place and do not silently fall back
to similarly named repository fixtures.

This single-clock buffered boundary is separate from the raw core-only cohort
in [`../k2_w2_raw_golden`](../k2_w2_raw_golden/README.md) and from
[`../k2_w2_tops`](../k2_w2_tops/README.md), whose Fovea+A7, A2+P6, and A3+P6
tops are complete, two-clock endpoint compositions. The three-cohort registry
[`../k2_w2_boundaries.json`](../k2_w2_boundaries.json) prohibits combining
their area or power rankings.
