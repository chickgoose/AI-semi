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

`pose_version` is an explicit `pose_id` in the supplied known-motion stream.
The event timestamp must be at or after that pose timestamp and no older than
the inclusive `--max-pose-age-ns` bound. Counts, IDs, sequence order, timestamp
order, coordinates, polarity, and all object fields are checked exactly.
Unknown/future/stale pose versions and any sensor-to-reference out-of-FOV result
abort before an output is written. There is no fallback, clipping, or silent
drop.

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

- exact input event: x16 + y16 + polarity1 + timestamp64 + pose_version16 =
  113 logical bits;
- occupancy packet: tile_x16 + tile_y16 + polarity1 + time-bin-start64 +
  pose-version16 + multiplicity-count16 = 129 logical bits.

The 16-bit pose version is a dictionary index into the timestamp-sorted
supplied pose stream; both the original string ID and deterministic numeric
code are disclosed in the exact ledger. It is not the UTF-8 size of the ID.

The occupancy projection emits one packet per fixed time/pose/polarity/tile
key. It preserves multiplicity count but omits individual event identity,
intra-bin timestamp, and intra-tile coordinate, so it remains explicitly
lossy. Reported bit counts are neither JSON byte size nor an implemented link.
The complete event ledger remains present so coalescing cannot be described as
lossless compression.

## Metrics

- bottleneck 1: fixed logical occupancy bits and reduction against the
  sensor-fixed projection;
- bottleneck 5: exact timestamp/pose binding count, binding/reorder errors, and
  maximum pose age. No transport timestamp error is claimed;
- bottleneck 6: within-polarity RMS tile spread, same-tile adjacency, and
  polarity/tile concentration (HHI). These are locality proxies, not a measured
  world reconstruction or downstream task score.

All lists and JSON keys have deterministic order, floats are rounded to twelve
decimal places, and the output is atomically replaced only after full
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
