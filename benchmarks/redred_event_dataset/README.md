# Dataset-neutral local event import

This standard-library-only package transforms one captured byte snapshot of an
event-camera file into the existing REDRED logical AER occurrence form. Its
exact evidence class is:

```text
LOCAL_CAPTURED_BYTE_SNAPSHOT_TRANSFORMATION
```

A successful conversion is `LOCAL_IMPORT_COMPLETE_UNQUALIFIED`. Inspection of
any intact package returns `HOLD_LOCAL_IMPORT_NOT_CANONICAL`. Neither outcome
is canonical, official, release-qualified, competition evidence, or a campaign
provenance bridge. The campaign bridge remains explicitly `HOLD`.

`canonical_jsonl` below is only the name of a strict local input syntax. It
does not mean the source or output is a canonical benchmark.

## Supported local syntax

A supported specification declares provider/dataset/release/version labels,
original artifact, one provenance URI or acquisition ID, SPDX license fields,
source snapshot SHA-256, sensor geometry, explicit address width, time/polarity
mapping, cycle mapping, and clipping policy. These are importer inputs copied
into a local receipt; the importer does not independently establish their
truth or elevate them into release provenance.

`canonical_jsonl` accepts exactly one four-key object per nonblank line:

```json
{"timestamp":"12.500","x":10,"y":7,"polarity":1}
```

Coordinates and polarity are bounded JSON integers. Timestamp and cycle period
are exact-decimal strings. Runtime and schema use the same end-anchored
language, without `$` final-newline ambiguity:

```text
^-?(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,64})?(?:[eE][+-]?(?:0|[1-9]|[1-9][0-9]|1[01][0-9]|12[0-8]))?(?![\s\S])
```

The lexical form is limited to 136 characters, at most 64 integer and 64
fractional digits, and exponent magnitude 128. Bounds are checked before
`int()` or powers of ten. Conversion uses `fractions.Fraction`, never the
process decimal context or binary floating point. Whitespace, underscores,
leading `+`, `.5`, `1.`, leading zeros, and noncanonical exponent integers are
rejected.

`generic_delimited` accepts a declared one-character CSV delimiter or the
special value `whitespace`. CR, LF, and NUL delimiters are forbidden. Header
handling and the `timestamp`, `x`, `y`, and `polarity` columns are explicit;
the importer guesses no source convention.

`samsung_official` remains an unsupported-format HOLD. Its minimal
specification contains only the schema, source snapshot hash, and reserved
format name. No Samsung columns, sensor, time unit, polarity, provider,
license, or record structure are invented.

Machine-readable contracts:

- [import_spec.schema.json](import_spec.schema.json)
- [import_receipt.schema.json](import_receipt.schema.json)
- [completion.schema.json](completion.schema.json)

## Captured-byte scope

Source and specification paths reject symlink components. Each file is opened
and read into bytes once. File type, device, inode, mode, size, nanosecond
mtime, and nanosecond ctime are compared before opening, on the descriptor
before and after reading, and on the path after reading. Parsing uses only the
captured bytes. Duplicate JSON keys, unknown keys, oversized numbers, floats,
non-finite values, and parser errors fail as `ImportFailure`.

Receipts record snapshot SHA-256, semantic SHA-256 where parsing is possible,
and the captured stat fields. They also state:

```text
path_scope = CAPTURE_ONLY_NOT_REVALIDATED
```

The importer does not assert that a pathname still denotes those bytes after
capture. Result-directory or path replacement cannot promote evidence because
the inspector always returns `HOLD_LOCAL_IMPORT_NOT_CANONICAL`. This package
does not claim authenticity or transactional safety against hostile concurrent
filesystem replacement.

## Conversion and counters

Events sort by exact timestamp and source-record index. Equal timestamps keep
source order. Cycles are:

```text
floor((timestamp - first_timestamp) * time_unit_ns / period_ns)
```

Every parsed event becomes one output occurrence. Ties and duplicate
source/cycle occurrences are preserved. Polarity is normalized according to
the declared encoding. The local conservation check is:

```text
input_event_records == events_emitted + events_dropped
events_dropped == 0
```

Clipping is separate from downstream AER transport loss. Bounds policy is
either whole-import rejection or coordinate clipping with explicit counters.
Clipping, out-of-range, blank/comment, and source-parse counters that cannot be
reconstructed from the output trace are marked `IMPORTER_ATTESTED`; they are
never canonical or official. Every counter is bounded by input event
cardinality, except clipped-coordinate and aggregate axis bounds, which are at
most twice that cardinality.

The logical address is checked locally and again during package inspection:

```text
logical_source == y * sensor.width + x
0 <= logical_source < 2**address_width
```

The committed fixtures explicitly declare the current common address width of
4, and tests feed the output to the existing AER preparer at that width.

## Local package protocol

The requested result directory must not exist. The importer exclusively
creates it and never overwrites existing paths. A completed local conversion
contains `events.jsonl`, `receipt.json`, and `COMPLETE.json`. An unsupported
format HOLD contains only the receipt and sentinel. The receipt is fully
validated before publication and re-read afterward. The completion sentinel
is written last.

A write failure can leave an orphan directory without a completion sentinel.
The inspector rejects it. This sentinel protocol detects incomplete or changed
captured package bytes; it is not a hostile-filesystem authenticity guarantee.

## CLI

Convert into a new local directory:

```bash
python3 benchmarks/redred_event_dataset/import_events.py \
  --source benchmarks/redred_event_dataset/fixtures/generic_events.csv \
  --spec benchmarks/redred_event_dataset/fixtures/generic_import.json \
  --result-dir /tmp/redred-event-import-local
```

Expected conversion class:

```text
LOCAL_IMPORT_COMPLETE_UNQUALIFIED
```

Inspect captured package integrity:

```bash
python3 benchmarks/redred_event_dataset/import_events.py \
  --result-dir /tmp/redred-event-import-local \
  --qualify
```

The inspector deliberately exits 3 and reports:

```text
HOLD_LOCAL_IMPORT_NOT_CANONICAL
```

Exit code 0 means only that a local unqualified transformation was written;
exit code 2 is failure; exit code 3 is an explicit HOLD. Run tests with:

```bash
bash tests/redred_event_dataset/run_all.sh
```
