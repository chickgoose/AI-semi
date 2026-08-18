# Dataset-neutral event import v2

This standard-library-only package converts provenance-bound event-camera
records into the existing REDRED logical AER occurrence form. It is an input
adapter, not an official workload, dataset endorsement, transport model, or
candidate score.

## Input contracts

Supported specifications use `schema: redred-event-import-v2` and must declare:

- provider, dataset, release, version, and original artifact;
- exactly one absolute provenance URI or acquisition ID;
- SPDX license ID, license-text SHA-256, and redistribution status;
- source-file raw SHA-256;
- sensor width/height and an explicit `address_width` (the committed common
  fixtures explicitly use 4; there is no implicit default);
- input format, time unit, polarity encoding, cycle period/origin/deadline,
  and reject-or-clip bounds policy.

`canonical_jsonl` accepts exactly one object per nonblank line:

```json
{"timestamp":"12.500","x":10,"y":7,"polarity":1}
```

Objects have exactly those four keys. Coordinates and polarity are JSON
integers. Timestamp and `cycle_mapping.period_ns` are exact-decimal strings
matching:

```text
-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?(0|[1-9][0-9]*))?
```

Leading/trailing whitespace, underscores, leading `+`, `.5`, `1.`, leading
zeros, and noncanonical exponent integers are rejected. Conversion parses an
integer coefficient and exponent into `fractions.Fraction`; it never uses the
process `Decimal` context or binary floating point.

`generic_delimited` accepts a declared one-character CSV delimiter or
`whitespace`. Its specification declares header handling and maps
`timestamp`, `x`, `y`, and `polarity` by header name or zero-based position.
No column order, time unit, or polarity convention is guessed.

`samsung_official` has a separate minimal HOLD specification containing only
the schema, raw source hash, and reserved format name. It publishes no trace
and encodes no imagined Samsung columns, timestamp unit, sensor, polarity,
license, provider, or record layout. Actual Samsung support remains HOLD until
the real format and provenance are supplied.

See [import_spec.schema.json](import_spec.schema.json),
[import_receipt.schema.json](import_receipt.schema.json), and
[completion.schema.json](completion.schema.json).

## Stable provenance reads

The specification and source are each opened and read into bytes exactly once.
The importer compares file type, device, inode, size, and nanosecond mtime
before opening, on the descriptor before/after reading, and on the path after
reading. Symlinks and files that change during the read fail closed. Parsing
uses only the captured bytes. JSON duplicate keys and unknown keys are errors.

PASS receipts record raw and canonical-semantic SHA-256 values for both source
events and the specification. The source semantic hash covers exact rational
timestamps, coordinates, normalized polarity, and source-record order. A HOLD
receipt records no semantic source hash because the format was not parsed.

## Deterministic, lossless conversion

Events sort by exact timestamp and then source record index, so timestamp ties
retain source-file order. Cycles use exact floor division:

```text
floor((timestamp - first_timestamp) * time_unit_ns / period_ns)
```

Ties are never spread across invented cycles. Every parsed event becomes one
occurrence with a contiguous `tb_only_event_id`. Timestamp ties, same-cycle
events, and same-source/same-cycle retriggers are counted. The invariant is:

```text
input_event_records == events_emitted + events_dropped
events_dropped == 0
```

A downstream one-entry AER source latch can classify a preserved retrigger as
`source_overrun`; the importer does not hide it in a queue or call clipping a
transport loss. `bounds_policy: reject` fails before publication.
`bounds_policy: clip` clamps coordinates but emits every event and reports
out-of-range events, axis/direction violations, clipped events, and clipped
coordinate components separately.

The output relation is checked during import and independent qualification:

```text
logical_source == y * sensor.width + x
0 <= logical_source < 2**address_width
```

Polarity remains trace metadata for the current address-only DUT contract.

## Exclusive result-directory protocol

The CLI accepts one result directory that must not already exist. It creates
the directory exclusively and never overwrites a directory, file, trace,
receipt, or sentinel. A PASS package contains exactly:

```text
events.jsonl
receipt.json
COMPLETE.json
```

A HOLD package contains only `receipt.json` and `COMPLETE.json`. The completion
sentinel is written last, after the trace/receipt and directory sync. An I/O
failure can leave an orphan directory, but without a valid sentinel it is not a
result and the qualifier rejects it. The importer never reuses or cleans an
orphan; choose a new result path after investigating it. This prevents a HOLD
or failed run from coexisting with stale current-looking output.

The qualifier checks exact directory membership, PASS/HOLD-exclusive receipt
shapes, sentinel hashes, trace hash/cardinality, all event fields, declared
address width, ordering, deadlines, collision counts, clipping relationships,
and zero-drop conservation.

## CLI

Import into a new directory:

```bash
python3 benchmarks/redred_event_dataset/import_events.py \
  --source benchmarks/redred_event_dataset/fixtures/generic_events.csv \
  --spec benchmarks/redred_event_dataset/fixtures/generic_import.json \
  --result-dir /tmp/redred-event-import-v2
```

Independently qualify it:

```bash
python3 benchmarks/redred_event_dataset/import_events.py \
  --result-dir /tmp/redred-event-import-v2 \
  --qualify
```

Exit codes are 0 for PASS, 2 for failure, and 3 for a qualified HOLD. The
committed fixtures and license are synthetic and are not Samsung or
competition-provided data.

Run the focused tests:

```bash
bash tests/redred_event_dataset/run_all.sh
```
