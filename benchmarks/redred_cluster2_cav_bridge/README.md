# Cluster2/CAV two-stream bridge

This standard-library-only package keeps source sidecars and AER transport
outcomes separate. A caller supplies the full lowercase SHA-256 of a canonical
manifest; the manifest hash-binds both JSONL streams, one mapping artifact, all
RTL artifacts (including `ganghee_cluster2_top`), the clock period, cycle-zero
timestamp, exact ceil mapping rule, and four projection names. There is no
embedded or inferred latest Ganghee digest.

`source_event` contains only raw identity and CAV sidecar fields.
`transport_outcome` contains only event/source identity, occurrence cycle,
`DELIVERED|OVERRUN`, and discriminated retire fields. The loader rejects
unknown keys, so timestamp, polarity, ray, pose, and window data cannot enter
the transport stream.

The bundle loader verifies canonical bytes and authority hashes, performs an
exact event-ID partition, checks source coordinates, the bound ceil timestamp
mapping, unique bitmap occurrence slots, `occurrence_cycle <= retire_cycle`,
unique lane/cycle retirement, per-source FIFO retirement, and integer-nanosecond
CAV retire timestamps. It imports no scorer or evaluator.

Projection produces exactly:

- `RAW4X4_ALL`: every source event;
- `RAW4X4_MATCHED`: source events whose outcome is `DELIVERED`;
- `AER_OCC`: delivered event ID, source coordinate, and occurrence cycle;
- `AER_RET`: delivered retirement plus CAV sidecar and integer retire timestamp.

The implementation asserts exact `(event_id, source_index)` equivalence between
`RAW4X4_MATCHED` and `AER_OCC` before returning the views.
