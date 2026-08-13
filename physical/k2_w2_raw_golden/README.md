# K2 W2 authoritative raw core-only boundary

The raw Fovea and Cluster2 physical cohort is frozen exclusively by:

```text
/tmp/ganghee-pnr-raw-golden-20260813.tar.gz
SHA256 7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3
```

The extracted authoritative root is
`/tmp/ganghee-pnr-raw-golden-20260813`. Its synthesis tops are the unbuffered
cores `aer_tx16_trad_rowcol_fovea` and
`aer_tx16_trad_rowcol_fovea_cluster2`. No `lane_buffer2`, A7, or P6 logic is
part of this cohort.

[`raw_golden.json`](raw_golden.json) binds the archive, exact RTL sources,
report periods, report counts, and sorted report-inventory digests. The
absolute-path `.f` files elaborate those extracted authoritative bytes only.

This cohort is distinct from the buffered server-golden wrappers in
[`../k2_w2_server_golden`](../k2_w2_server_golden/README.md) and the complete
endpoint wrappers in [`../k2_w2_tops`](../k2_w2_tops/README.md). Their area and
power results are different accounting boundaries and must never be combined
into one ranking.
