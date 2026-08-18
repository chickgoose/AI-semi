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

## Strict v2 input interface

All objects reject unknown fields and duplicate JSON keys. Pose IDs and pose
timestamps are unique, and `tb_only_event_id` is unique and contiguous. AER
events may legitimately share timestamps when lanes operate in parallel; the
event ID disambiguates them.

The schemas are:

- intrinsics JSON: `redred.known_motion.intrinsics/v2`;
- pose JSONL: `redred.known_motion.pose_stream/v2` header followed by
  `redred.known_motion.pose/v2` records;
- retired-event JSONL: `redred.aer.retired_event_stream/v2` header followed by
  `redred.aer.retired_event/v2` records.

Every retired event carries:

- `tb_only_event_id`, `logical_source`, and `address`;
- `occurrence_time` and `capture_time`;
- `accept_time` and independently preserved `retire_time`;
- pixel coordinates and polarity.

Each timestamp explicitly contains `value`, `clock_domain`, `epoch`, and
`unit`. The header selects exactly one of `occurrence_time` or `capture_time`
as `pose_lookup_time`; accept and retire timestamps never affect pose lookup.
The selected timestamp must share the pose stream's timebase.

Pose selection is either an explicit `pose_id` or deterministic zero-order
hold of the latest pose satisfying `pose_timestamp <= selected_lookup_time`.
The CLI requires `--max-pose-age-ns`. The bound is inclusive: age equal to the
limit passes; age one nanosecond greater fails. Before-first poses, explicit
future poses, unknown IDs, timebase mismatches, and stale poses fail before an
output is written.

## Provenance and transport precondition

Each of the three primary inputs is read exactly once as bytes. SHA-256 is
computed over those exact bytes before decoding, and the output records the
three hashes. Manifest/content/receipt digests must be lowercase, nonzero
SHA-256 values.

The event header has one evidence class:

- `SYNTHETIC_DEMO`: no receipt may be claimed. The committed small fixture is
  only this class and is never canonical evidence.
- `CANONICAL_COMMON_SUITE`: `provenance.transport_receipt` must contain a path
  relative to the event stream and the exact SHA-256 of a strict
  `redred.known_motion.transport_receipt_sidecar/v1` JSON file. The sidecar is
  read once and must bind run, candidate, workload, manifest/content digests,
  and transport accounting byte-for-byte.

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
