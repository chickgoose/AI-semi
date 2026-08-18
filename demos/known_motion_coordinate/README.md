# Known-motion world/sensor coordinate demo

This standard-library-only demo applies a supplied camera rotation to retired
AER pixel events.  It is deliberately bounded: the `world_reference_image`
coordinates represent rays in a fixed reference camera, not metric world
points.  The model includes no translation, depth, lens distortion, optical
flow, or pose estimation.

It is a software system demo.  It is not candidate RTL/TB and its work or cost
is **not included in any endpoint PPA result**.

## Coordinate and rotation convention

Both image planes use pixel coordinates `x` right and `y` down.  Camera rays
are `(X right, Y down, Z forward)`.  Intrinsics are
`x = fx*X/Z + cx`, `y = fy*Y/Z + cy`.

Every pose is an active `world_to_sensor` rotation.  Euler records apply pan
about `+Y`, then tilt about `+X`, then roll about `+Z`:

```text
R_world_to_sensor = R_roll @ R_tilt @ R_pan
```

Positive pan moves the forward-axis projection right; positive tilt moves it
up; positive roll moves a ray on the right of the principal point down.
`sensor-to-world` uses the transpose of the supplied rotation.  In-FOV floating
coordinates are quantized with `floor(value + 0.5)`.  A ray with non-positive
Z or a projected center outside `[0,width-1] x [0,height-1]` is reported as
coordinate `out_of_fov`, never as AER transport loss.

## Provenance-bound interface

The three inputs have explicit versioned schemas:

- intrinsics JSON: `redred.known_motion.intrinsics/v1`
- pose JSONL header/records: `redred.known_motion.pose_stream/v1` and
  `redred.known_motion.pose/v1`
- retired-event JSONL header/records:
  `redred.aer.retired_event_stream/v1` and `redred.aer.retired_event/v1`

The JSONL header must be first.  Camera, intrinsics, and pose-stream IDs must
match across files.  Event provenance binds the stream to a run, candidate,
workload, and lowercase manifest SHA-256.  The output header and summary add
SHA-256 hashes of the exact three input files, so a result cannot silently be
reattributed to different inputs.

A pose record contains either all of `pan_deg`, `tilt_deg`, `roll_deg`, or an
orthonormal determinant-`+1` `rotation_matrix`.  An event can select a pose by
`pose_id`; otherwise the latest pose at or before its timestamp is selected
with deterministic zero-order hold.  Future, absent, duplicate, malformed, or
optionally stale poses fail closed.

The event header carries independent AER transport counters satisfying:

```text
generated = accepted + source_overrun
accepted_missing = accepted - retired
```

The summary preserves these counters separately from coordinate out-of-FOV
events and checks `retired = in_fov + coordinate_out_of_fov`.

## Run the fixture

From the repository root:

```bash
python3 -m demos.known_motion_coordinate.cli \
  --events demos/known_motion_coordinate/fixtures/retired_events.jsonl \
  --intrinsics demos/known_motion_coordinate/fixtures/intrinsics.json \
  --poses demos/known_motion_coordinate/fixtures/poses.jsonl \
  --mode world-to-sensor \
  --output /tmp/known_motion_transformed.jsonl \
  --summary /tmp/known_motion_summary.json

python3 -m unittest discover -s tests/known_motion_coordinate -v
```
