# Strict external post-retire known-motion demo

This standard-library-only program transforms already-retired AER pixel events
using supplied camera poses. It is intentionally outside the transport
endpoint: it is not candidate RTL/TB, adds no transport capability, and is not
included in endpoint PPA.

The `world_reference_image` is a fixed reference-camera ray frame, not a
metric 3-D world reconstruction. The bounded model has rotation only—no
translation, depth, lens distortion, optical flow, or pose estimation.

## Machine-bound coordinate contract

The intrinsics file contains the complete convention object and the pose/event
headers bind its immutable `convention_id`. Validation requires the object to
match exactly:

- right-handed camera axes: `+X` right, `+Y` down, `+Z` forward;
- pixel `x` right, `y` down, origin at the center of top-left pixel `(0,0)`;
- row-major, active `world_to_sensor` rotation;
- pan about `+Y`, then tilt about `+X`, then roll about `+Z`;
- `R_world_to_sensor = R_roll @ R_tilt @ R_pan`;
- input Euler angles in degrees, with explicit `degrees*pi/180` conversion to
  radians for trigonometry;
- `ray_sensor = R_world_to_sensor @ ray_world` and
  `pixel = K * (ray/ray_z)`;
- `sensor-to-world` applies the transpose of the supplied rotation matrix.

Matrix records must declare `matrix_direction: world_to_sensor`; Euler records
must declare `angle_unit: degrees`. An in-FOV floating result is quantized with
`floor(value + 0.5)`. Non-positive ray Z and projections outside
`[0,width-1] x [0,height-1]` are geometry `out_of_fov`, not transport loss.

## Strict synthetic-only input interface

All objects reject unknown fields and duplicate JSON keys. Pose IDs and pose
timestamps are unique. Each `tb_only_event_id` is a preserved, unique TB
identity and may have noncontiguous gaps. The separate required
`retire_sequence_index` is exactly `0..record_count-1` in JSONL retirement
order. It never renumbers or replaces the original event ID. AER events may
legitimately share timestamps when lanes operate in parallel.

The schemas are:

- intrinsics JSON: `redred.known_motion.intrinsics/v2`;
- pose JSONL: `redred.known_motion.pose_stream/v2` header followed by
  `redred.known_motion.pose/v2` records;
- retired-event JSONL: `redred.aer.retired_event_stream/v3` header followed by
  `redred.aer.retired_event/v3` records.

Every retired event carries:

- `tb_only_event_id`, `retire_sequence_index`, `logical_source`, and `address`;
- `occurrence_time` and `capture_time`;
- `accept_time` and independently preserved `retire_time`;
- pixel coordinates and polarity.

For `SYNTHETIC_DEMO`, the event header declares one absolute timebase and pins
the clock-domain label for the pose and every event stage. Every pose,
occurrence, capture, accept, and retire timestamp must have that identical
clock domain, epoch, and `ns` unit. Each event must satisfy
`occurrence <= capture <= accept <= retire`; retire timestamps must also be
nondecreasing in `retire_sequence_index` order. In the bounded fixture,
`address` must equal `logical_source`, so an address/order swap fails closed.

The header selects exactly one of `occurrence_time` or `capture_time` as
`pose_lookup_time`; accept and retire timestamps never affect pose lookup, and
retire time remains independently preserved in the result.

Pose selection is either an explicit `pose_id` or deterministic zero-order
hold of the latest pose satisfying `pose_timestamp <= selected_lookup_time`.
The CLI requires `--max-pose-age-ns`. The bound is inclusive: age equal to the
limit passes; age one nanosecond greater fails. Before-first poses, explicit
future poses, unknown IDs, timebase mismatches, and stale poses fail before an
output is written.

## Provenance and transport precondition

Each of the three primary inputs is read exactly once as bytes. SHA-256 is
computed over those exact bytes before decoding, and the output records those
independently computed identities as `events_input_sha256`,
`intrinsics_input_sha256`, and `poses_input_sha256`.

In contrast, embedded `provenance.content_sha256` values and the retired-event
`provenance.manifest_sha256` are source-supplied assertions. This demo checks
only that they are lowercase, nonzero SHA-256-shaped values; it does not define
their hash scope or recompute them. The event assertions are copied to
`source_content_sha256` and `manifest_sha256` for disclosure, never substituted
for the independently computed input-byte identities.

The integrated deliverable supports exactly one evidence class:

- `SYNTHETIC_DEMO`: no receipt or sidecar may be claimed. The committed fixture
  demonstrates synthetic scenario behavior only and is never canonical
  coordinate evidence.

`CANONICAL_COMMON_SUITE` is explicitly `HOLD/unsupported` and is rejected
before any claimed receipt or sidecar can be inspected. A3 canonical
coordinate join/export is HOLD until a trusted post-retire exporter and
receipt exist. A sidecar, including a symlinked or changed sidecar, cannot
promote this demo to canonical evidence.

Transformation has a hard precondition:

```text
generated = source_overrun + accepted
accepted = retired = event-record count
```

An accepted-missing stream cannot produce successful output. After that gate,
the geometry result separately satisfies:

```text
retired = transformed_in_fov + coordinate_out_of_fov
```

Coordinate out-of-FOV is never added to AER transport loss.

## Run the synthetic fixture

From the repository root:

```bash
python3 -m demos.known_motion_coordinate.cli \
  --events demos/known_motion_coordinate/fixtures/retired_events.jsonl \
  --intrinsics demos/known_motion_coordinate/fixtures/intrinsics.json \
  --poses demos/known_motion_coordinate/fixtures/poses.jsonl \
  --mode world-to-sensor \
  --max-pose-age-ns 800 \
  --output /tmp/known_motion_transformed.jsonl \
  --summary /tmp/known_motion_summary.json

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/known_motion_coordinate -v
```
