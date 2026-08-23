# Cluster2/CAV two-stream bridge

This standard-library-only package keeps source sidecars and AER transport
outcomes separate. A caller supplies the full lowercase SHA-256 of a canonical
manifest; the manifest hash-binds both JSONL streams, mapping, source-registry,
pose-stream, native transport/simulator receipt, and all RTL artifacts
(including `ganghee_cluster2_top`). Each authority is an opaque unique relative
path plus full SHA-256. It also binds `aer_clock_period_ps`,
`aer_cycle_zero_timestamp_ns`, the exact ceil mapping rule, and four projection
names. There is no embedded or inferred latest Ganghee digest.

This scoped existing-CAV bridge accepts only whole-nanosecond AER clocks:
`aer_clock_period_ps % 1000 == 0`. The intended bridge demonstration uses
`aer_clock_period_ps = 2000`. Fractional-nanosecond AER clocks remain **HOLD
pending a picosecond-resolution CAV interface**, and are rejected at manifest
validation before any event is loaded.

The three new opaque authorities are byte-integrity bindings only. Semantic
verification of real source-registry, pose-stream, and native
transport/simulator receipt artifacts is **HOLD pending Ganghee refresh**.

`source_event` contains only raw identity and CAV sidecar fields. Its sensor ray
must have unit norm within `1e-9`; `transform_guard_valid` is mandatory; and
`event_content_sha256` is recomputed from the exact current-CAV neutral preimage
before the source row is accepted. Ray components are converted to JSON floats
for that preimage, matching current-CAV `_unit_tuple` normalization even when
the captured JSON ray uses integer tokens such as `[0,0,1]`.
`transport_outcome` contains only event/source identity, occurrence cycle,
`DELIVERED|OVERRUN`, and discriminated retire fields. The loader rejects
unknown keys, so timestamp, polarity, ray, pose, and window data cannot enter
the transport stream.

The authoritative `cluster2_steal_buf` native boundary has two registered
row/bitmap outputs: `valid0,row0,col_mask0` and
`valid1,row1,col_mask1`. A delivered row records `retire_native_lane` (0 or 1)
plus the observed `retire_row` and `retire_col` (both 0 through 3), never a
normalized scoreboard slot. The identity check is
`source_index == retire_row * 4 + retire_col`. Native lane 0 may emit rows
0, 1, or 2; native lane 1 may emit rows 0, 2, or 3. All events in one
`(cycle,native_lane)` bitmap share one row, the two valid lanes cannot select
the same row in one cycle, and `(cycle,native_lane,col)` is unique. Thus the
two native bitmaps can represent up to eight delivered events per cycle after
expansion.

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

`AER_RET` rows are deterministic native retirement order:
`(retire_cycle, retire_native_lane, retire_col, event_id)`. RAW diagnostics and
`AER_OCC` remain in source ordinal order.

The implementation asserts exact `(event_id, source_index)` equivalence between
`RAW4X4_MATCHED` and `AER_OCC` before returning the views.

`AER_OCC` and `AER_RET` are observational joins for a later adapter. Every row
and the manifest projection contract carries the exact label
`SOURCE_EVENT_OBSERVATIONAL_JOIN_NOT_AER_PAYLOAD`. They are not asserted to be
wire-complete AER payloads or direct CAV inputs.
