# Cluster2/CAV two-stream bridge

This standard-library-only package keeps source sidecars and AER transport
outcomes separate. A caller supplies the full lowercase SHA-256 of a canonical
manifest; the manifest hash-binds both JSONL streams, mapping, source-registry,
pose-stream, native transport/simulator receipt, and all RTL artifacts
(including `ganghee_cluster2_top`). Each authority is an opaque unique relative
path plus full SHA-256. It also binds `aer_clock_period_ps`,
`aer_cycle_zero_timestamp_ns`, the exact ceil mapping rule, and four projection
names. There is no embedded or inferred latest Ganghee digest.

The three new opaque authorities are byte-integrity bindings only. Semantic
verification of real source-registry, pose-stream, and native
transport/simulator receipt artifacts is **HOLD pending Ganghee refresh**.

`source_event` contains only raw identity and CAV sidecar fields. Its sensor ray
must have unit norm within `1e-9`; `transform_guard_valid` is mandatory; and
`event_content_sha256` is recomputed from the exact current-CAV neutral preimage
before the source row is accepted.
`transport_outcome` contains only event/source identity, occurrence cycle,
`DELIVERED|OVERRUN`, and discriminated retire fields. The loader rejects
unknown keys, so timestamp, polarity, ray, pose, and window data cannot enter
the transport stream.

The authoritative `cluster2_steal_buf` native boundary has two registered
row/bitmap outputs: `valid0,row0,col_mask0` and
`valid1,row1,col_mask1`. A delivered row records `retire_native_lane` (0 or 1)
and `retire_col` (0 through 3), never a normalized scoreboard slot. Native lane
0 may emit only source rows 1 or 2; native lane 1 may emit only rows 0 or 3;
`retire_col == source_index % 4`. All events in one `(cycle,native_lane)` bitmap
share one row, while `(cycle,native_lane,col)` is unique. Thus the two native
bitmaps can represent up to eight delivered events per cycle after expansion.

The bundle loader verifies canonical bytes and authority hashes, performs an
exact event-ID partition, checks source coordinates, the bound ceil timestamp
mapping, unique bitmap occurrence slots, `occurrence_cycle <= retire_cycle`,
same-row native lane/cycle bitmaps, unique native lane/cycle/column retirement,
per-source FIFO retirement, and integer-nanosecond CAV retire timestamps. It
imports no scorer or evaluator.

Projection produces exactly:

- `RAW4X4_ALL`: every source event;
- `RAW4X4_MATCHED`: source events whose outcome is `DELIVERED`;
- `AER_OCC`: occurrence cycle plus original timestamp and source sidecars;
- `AER_RET`: occurrence timestamp retained separately from a labeled derived
  integer-nanosecond retirement timestamp, plus the same source sidecars.

The implementation asserts exact `(event_id, source_index)` equivalence between
`RAW4X4_MATCHED` and `AER_OCC` before returning the views.

`AER_OCC` and `AER_RET` are observational joins for a later adapter. Every row
and the manifest projection contract carries the exact label
`SOURCE_EVENT_OBSERVATIONAL_JOIN_NOT_AER_PAYLOAD`. They are not asserted to be
wire-complete AER payloads or direct CAV inputs.
