# MC-WTB Stage-1 analysis model

This standard-library-only model tests one narrow hypothesis: events generated
by a static reference scene under known camera rotation should occupy fewer
tiles after a rotation-only sensor-to-reference warp than in sensor-fixed
coordinates. It imports the validated pinhole convention, intrinsics/pose
loaders, and `sensor-to-world` pixel warp from
`demos.known_motion_coordinate`; that existing demo is not modified.

Stage-1 is an analysis program, not RTL or a transport codec. Its reference
frame is a fixed reference-camera image, not metric 3-D world geometry.
Translation, depth, pose estimation, RTL, and reversible encoding are explicitly
unsupported.

## Strict input and fail-closed behavior

The event JSONL header uses `redred.mc_wtb.event_stream/v1`. Each following
`redred.mc_wtb.event/v1` record atomically contains:

```text
event_id, sequence_index, timestamp_ns, pose_version, x, y, polarity
```

The header carries a complete `timebase` object whose `clock_domain`, `epoch`,
and `unit` must exactly equal the pose-stream timebase. `pose_version` is an
explicit `pose_id` in the supplied known-motion stream. It must be the
deterministic latest pose at or before the event timestamp, and its age must not
exceed the inclusive `--max-pose-age-ns` bound. Polarity must have JSON integer
type (booleans and floats are rejected) and value `-1` or `1`. Counts, IDs,
sequence order, timestamp order, coordinates, and all object fields are checked
exactly. Unknown/future/old/non-latest pose versions and any
sensor-to-reference out-of-FOV result abort before output replacement. There is
no fallback, clipping, truncation, or silent drop.

The output path is rejected before processing if its resolved path equals any
input or, when it already exists, if it shares an inode with an input (including
a hardlink). This prevents output replacement from overwriting an input.

The committed synthetic fixture deliberately uses only a new event file. It
references the existing known-motion intrinsics and pose fixtures. This keeps
the coordinate convention and supplied rotations shared rather than copied.

## Two representations and event ledger

Every valid input event appears once in `exact_event_ledger`, preserving its
ID, timestamp, pose version, polarity, sensor coordinate, reference coordinate,
and both tile assignments. `exact_input_count_ledger` requires the declared,
parsed, atomic-bound, sensor-assigned, and reference-assigned counts to agree.

The two compared views are:

- `sensor_fixed`: tile the unwarped sensor `(x,y)`;
- `pose_compensated_reference`: apply the supplied rotation with
  `warp_pixel(..., "sensor-to-world")`, then tile the reference coordinate.

Spatial-locality bins are keyed by `(polarity,tile_x,tile_y)` over the complete
input and publish event counts, member IDs, unique pixel counts, and
`same_tile_extra_events`. Per-polarity occupancy is reported separately.
Logical bit accounting is deliberately stricter: its packets are keyed by
`(fixed_time_bin,pose_version,polarity,tile_x,tile_y)`, so events cannot be
amortized across an unbounded run or across poses.

## Fixed logical bit accounting

`redred.mc_wtb.logical_bits/fixed-v1` is a data-independent accounting rule:

- raw sensor payload: x16 + y16 + polarity1 + timestamp64 + pose_version16 =
  113 logical bits;
- occupancy packet: tile_x16 + tile_y16 + polarity1 + time-bin-start64 +
  pose-version16 + multiplicity-count16 = 129 logical bits.

`event_id` and `sequence_index` are analysis-provenance fields used for exact
traceability; they are deliberately excluded from the raw sensor payload
accounting convention. The 16-bit pose version is a dictionary index into the
timestamp-sorted supplied pose stream; both the original string ID and
deterministic numeric code are disclosed in the exact ledger. It is not the
UTF-8 size of the ID.
Every declared unsigned field is range-checked. Timestamp and time-bin start
must fit 64 bits; x/y, tile x/y, pose code, and multiplicity count must fit their
declared 16-bit fields. Stage-1 fails closed on overflow instead of wrapping,
saturating, or truncating.

The fixed-v1 width table is a nested immutable mapping and is the single source
used by both validators and accounting. Each result receives a new plain-JSON
copy of the table and a new unsupported-feature list, so caller mutation cannot
change module state or a later run.

The occupancy projection emits one packet per fixed time/pose/polarity/tile
key. It preserves multiplicity count but omits individual event identity,
intra-bin timestamp, and intra-tile coordinate, so it remains explicitly
lossy. Reported bit counts are neither JSON byte size nor an implemented link.
The complete event ledger remains present so coalescing cannot be described as
lossless compression.

## Exact-byte provenance and output semantics

Each primary input is opened once with no symlink following and read from that
one pinned regular-file descriptor into an immutable public known-motion input
blob. The parser and published SHA-256 consume the exact same blob bytes; paths
are not reopened for parsing or provenance hashing. Each file is independently
snapshotted, so this does not claim an atomic three-file snapshot or a writer's
coherent multi-file transaction. It is also not a canonical-evidence claim;
the fixture remains `SYNTHETIC_DEMO`.

On supported POSIX platforms, output publication pins the final parent directory
with `O_DIRECTORY|O_NOFOLLOW`, creates a mode-0600 temporary regular file in
that dirfd, checks the current parent path and no-follow target inode, and uses
a same-dirfd atomic `rename`. Parent-path redirection and target input-inode or
symlink aliases fail closed. Required dirfd features have no weak path-based
fallback. The implementation does not call file or directory `fsync`, so this
atomic visibility makes no crash-durability guarantee.

`analysis_contract` binds the validated tile dimensions, time-bin size,
inclusive maximum pose age, fixed-v1 format ID, latest-at-or-before ZOH rule,
semantic implementation ID, result-contract revision, and public known-motion
blob API ID. The semantic ID is source-controlled contract identity, not a
cryptographic attestation of a binary or repository commit.
The result schema remains `redred.mc_wtb.stage1_analysis/v1` under its additive
consumer policy: existing keys retain their meanings and Hardening 2 adds the
contract object plus stronger provenance/output-semantics fields.

## Metrics

- bottleneck 1: fixed packet-key projection totals and their delta against the
  sensor-fixed projection. This is not measured wire traffic, a bandwidth
  reduction result, or an implemented codec;
- bottleneck 5: exact timestamp/pose binding count, binding/reorder errors, and
  maximum pose age. No transport timestamp error is claimed;
- bottleneck 6: within-polarity RMS tile spread, same-tile adjacency, and
  polarity/tile concentration (HHI). These are locality proxies, not a measured
  world reconstruction or downstream task score.

All lists and JSON keys have deterministic order, floats are rounded to twelve
decimal places, and the output becomes atomically visible only after full
validation.

## Run

From the repository root:

```bash
python3 -m demos.mc_wtb.cli \
  --events demos/mc_wtb/fixtures/events.jsonl \
  --intrinsics demos/known_motion_coordinate/fixtures/intrinsics.json \
  --poses demos/known_motion_coordinate/fixtures/poses.jsonl \
  --tile-width 8 --tile-height 8 --time-bin-ns 1000 \
  --max-pose-age-ns 0 \
  --output /tmp/mc_wtb_stage1.json

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/mc_wtb -p 'test_*.py' -v
```
