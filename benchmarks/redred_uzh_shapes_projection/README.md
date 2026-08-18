# UZH shapes_rotation public projected extension

This standard-library-only package streams the pinned public UZH Event-Camera
Dataset `shapes_rotation` `events.txt`, verifies the source archive, extracted
events, and CC-BY-NC-SA-3.0 legal text, and projects one exact window into 16
logical sources.

Its only successful evidence class is:

```text
PUBLIC_PROJECTED_EXTENSION_UNREPLAYED / HOLD
```

It is not official REDRED traffic, not canonical traffic, not a replay result,
and not P6 evidence. An actual single-edge replay must independently bind the
trace SHA, implementation, tool, counters, and receipt before changing any
replay claim.

## Exact projection

The input format is four single-space-separated fields:

```text
timestamp_seconds x y polarity
```

The sensor is DAVIS240C, 240x180. For every input event in
`[41.321,41.322)` seconds:

```text
bx = floor(x*4/240)
by = floor(y*4/180)
logical_source = 4*by + bx
```

All 1,100 events are retained in source order. Equal timestamps, repeated
projected sources, and same-source/same-cycle collisions are not coalesced.
`projected_events.jsonl` retains the original coordinate and 0/1 polarity.
The three replay-preparer traces use the normalized 4x4 coordinate and -1/+1
polarity while preserving the same contiguous event identity.

At clock period 6.5 ns = 13/2 ns and disclosed compression `C`, the exact
integer-only mapping is:

```text
occurrence_cycle = floor((timestamp_ns - 41321000000) * 2 / (13*C))
C in {1,64,256}
```

No binary floating point or ambient decimal context is used.

## Streaming and publication

The 509 MB input is never loaded as one byte array. Every line is streamed
through SHA-256, strict lexical parsing, monotonicity and full-sensor bounds
checks. The ZIP is streamed through SHA-256 and its `events.txt` member is
independently decompressed and matched to the extracted event SHA. Inputs and
all path components reject symlinks and changes to file identity, size, mtime,
or ctime during a read.

The output directory must not exist. Files are created with exclusive writes;
`COMPLETE.json` is written last. A write/resource failure leaves no valid
completion sentinel and cannot be inspected as complete. Inspection rehashes
all artifacts and reconstructs every bin, event identity, exact cycle, and
collision count, then deliberately returns HOLD.

## Commands

Create a new package from the pinned external bytes:

```bash
python3 benchmarks/redred_uzh_shapes_projection/project.py \
  --archive /tmp/uzh-shapes_rotation.zip \
  --events /tmp/uzh-shapes_rotation/events.txt \
  --license /tmp/CC-BY-NC-SA-3.0.txt \
  --result-dir /tmp/redred-uzh-shapes-projection
```

Inspect it (expected exit code 3 because it remains HOLD):

```bash
python3 benchmarks/redred_uzh_shapes_projection/project.py \
  --result-dir /tmp/redred-uzh-shapes-projection --qualify
```

Run tests:

```bash
bash tests/redred_uzh_shapes_projection/run_all.sh
```

The committed `projection_spec.json` pins all source sizes/hashes, archive
member identity, license, geometry, window anchors, binning, rational clock
mapping, scenarios, resource limits, and no-P6/unreplayed lineage.
