# Dataset-neutral event import

This package converts provenance-bound event-camera records into the existing
REDRED logical AER occurrence JSONL form. It is an input adapter, not an
official workload, dataset endorsement, transport model, or candidate score.
It uses only the Python standard library.

## Supported input contracts

`canonical_jsonl` accepts exactly one JSON object per nonblank line with these
fields:

```json
{"timestamp":"12.500", "x":10, "y":7, "polarity":1}
```

Coordinates and polarity are JSON integers. Timestamps are integers or decimal
strings; JSON floating-point values are rejected so conversion cannot depend
on binary floating-point rounding.

`generic_delimited` accepts CSV, another declared one-character delimiter, or
whitespace-separated text. The import specification must declare whether a
header exists and map all four semantic columns: `timestamp`, `x`, `y`, and
`polarity`. Header mappings use names; headerless mappings use zero-based
column indices. The time unit, polarity encoding, and sensor width/height are
also mandatory. No column order or polarity convention is guessed.

`samsung_official` is a reserved, unsupported identifier. The CLI verifies the
source hash, emits a receipt with `status: HOLD`, returns exit code 3, and does
not parse or emit a trace. The real Samsung schema must be supplied and frozen
before support is implemented; this package deliberately contains no imagined
Samsung columns, timestamp unit, polarity encoding, or record layout.

The complete machine-readable contract is
[`import_spec.schema.json`](import_spec.schema.json). Runtime validation is
implemented directly as well, so the CLI does not require a JSON Schema
package. Completed and HOLD receipts are described by
[`import_receipt.schema.json`](import_receipt.schema.json).

## Provenance and deterministic mapping

Every import specification contains the exact SHA-256 of the source bytes. A
mismatch fails before parsing. The receipt records that hash, a canonical hash
of the import specification, the output hash, and all conversion parameters.

Events are sorted by exact decimal timestamp and then their zero-based source
record index. Equal timestamps retain source-file order. A cycle is:

```text
floor((timestamp - first_timestamp) * declared_time_unit_ns / period_ns)
```

The conversion does not spread tied events across invented cycles. Every
parsed input event becomes exactly one output occurrence, with contiguous
`tb_only_event_id` values. The receipt enforces and records:

```text
input_event_records == events_emitted + events_dropped
events_dropped == 0
```

Timestamp ties, same-cycle events, and same-source/same-cycle retriggers are
counted explicitly. A downstream common AER driver may classify a retrigger at
an occupied one-entry source latch as `source_overrun`; the importer neither
hides it nor introduces an unbounded queue.

For an in-range or clipped coordinate, the mandatory address relation is:

```text
logical_source == y * sensor.width + x
```

The output fields match the clean-slate address-only JSONL form. Polarity
remains trace metadata and is not claimed to traverse an address-only DUT.

## Bounds policy

`bounds_policy: reject` fails the entire import at the first out-of-range
event. It does not create a partial trace. `bounds_policy: clip` clamps each
coordinate to the nearest sensor edge and emits every event. The receipt
separately reports out-of-range events, low/high violations by axis, clipped
events, and clipped coordinate components. There is no drop policy.

## CLI

From the repository root:

```bash
python3 benchmarks/redred_event_dataset/import_events.py \
  --source benchmarks/redred_event_dataset/fixtures/generic_events.csv \
  --spec benchmarks/redred_event_dataset/fixtures/generic_import.json \
  --output /tmp/redred-events.jsonl \
  --receipt /tmp/redred-events.receipt.json
```

Exit codes are 0 for a completed import, 2 for invalid input/provenance, and 3
for the explicit Samsung-format HOLD. The committed fixtures are synthetic and
must not be represented as Samsung or competition-provided data.

Run the focused tests with:

```bash
python3 -m unittest discover -s tests/redred_event_dataset -v
```
