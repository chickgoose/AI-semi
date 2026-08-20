# UZH MC-WTB control evaluator

This standard-library-only package evaluates one frozen event cohort across
exactly six controls: `RAW`, `SENSOR_FIXED`, `MC_CORRECT`, `MC_WRONG`,
`MC_DELAYED`, and `RETIRE_WARP`.

It reports only world-ray geometry gates and two kinds of tile-locality
opportunity. It does not serialize anything and makes no bandwidth,
compression, codec, benefit, RTL, or PPA claim. A geometry-only PASS is named
`PASS_GEOMETRY_CONTROLS_ONLY`; the enclosing evidence status always remains
`CONTROL_EVALUATION_ONLY_NO_BANDWIDTH_OR_BENEFIT_CLAIM`.

## Adapter record boundary

`evaluate_records()` consumes JSON-like mappings. One mapping owns one event
ID and must contain all six arm outputs, so an adapter cannot supply a
different admitted set per arm. Event IDs must be unique and strictly
increasing; timestamps must be nondecreasing. The exact record shape is:

```json
{
  "schema": "redred.uzh_mc_wtb_controls.adapter_record/v2",
  "dataset_event_index": 13856250,
  "join_sequence_index": 0,
  "timestamp_ns": 41321000000,
  "x_raw": 120,
  "y_raw": 90,
  "polarity_01": 1,
  "oracle_status": "in_fov",
  "oracle_reference_ray": [0.0, 0.0, 1.0],
  "arms": {
    "RAW": {
      "geometry_status": "in_fov",
      "reference_ray": [0.0, 0.0, 1.0],
      "locality_x": 120.0,
      "locality_y": 90.0,
      "pose_lookup_timestamp_ns": null
    },
    "SENSOR_FIXED": {
      "geometry_status": "in_fov",
      "reference_ray": [0.0, 0.0, 1.0],
      "locality_x": 120.0,
      "locality_y": 90.0,
      "pose_lookup_timestamp_ns": 41321000000
    },
    "MC_CORRECT": {},
    "MC_WRONG": {},
    "MC_DELAYED": {},
    "RETIRE_WARP": {}
  }
}
```

The four omitted arm objects have the same five required fields as
`SENSOR_FIXED`. The oracle ray must come from an independently checked
occurrence-time supplied-pose path; this evaluator deliberately does not
import, interpolate, or warp production records itself.

The normalized boundary deliberately preserves the A2 native adapter's
`dataset_event_index`, `join_sequence_index`, `timestamp_ns`, `x_raw`,
`y_raw`, and polarity meanings. Join sequence must be exactly `0..N-1`, and
RAW/SENSOR_FIXED locality coordinates must equal the common top-level raw
coordinates. The evaluator remains a separate package and does not inspect,
produce, or relabel the native adapter package.

The result binds the exact ordered cohort as SHA-256 over one ASCII decimal
dataset event index plus LF per record; count and endpoint anchors are not
treated as a substitute for complete identity.

`RAW` is not an alias for `SENSOR_FIXED`. RAW applies no pose transform: its
ray is the undistorted source sensor ray interpreted directly in the
evaluation/reference axes, its locality coordinates are the original raw
sensor coordinates, and its pose lookup timestamp is null. SENSOR_FIXED has
the same raw sensor locality coordinates, but its `reference_ray` is the
receiver-side occurrence-pose world/reference ray. The evaluator requires
RAW and SENSOR_FIXED locality coordinates to be exactly equal per event while
explicitly not requiring their geometry rays to be equal. They coincide only
when the relevant pose transform is identity or happens to leave that ray
unchanged.

RAW status is `in_fov` for a valid unwarped source ray and
`invalid_distortion` when that ray cannot be formed; without a pose transform
it cannot become `outside_reference_image` or `behind_reference`. RAW remains
a no-warp geometry diagnostic and is not required to pass the correct-pose
gate.

MC arms provide their projected continuous coordinates. Valid OOF records
retain their ray and outside coordinate, so they participate normally in
angular and tile metrics. `behind_reference` retains a ray but has null
locality coordinates. `invalid_distortion` has a null ray and null projected
coordinates. RAW and sensor-fixed always retain their raw locality coordinates.

No pose lookup is allowed for RAW. Occurrence lookup is mandatory for
sensor-fixed, correct, and wrong arms;
delayed lookup must be strictly earlier; retire lookup cannot precede the
event. These timestamps are evidence fields, not a substitute for the
adapter's pose-bracket validation.

## Frozen parameters and denominators

The committed `preregistered.json` fixes 8x8 half-open tiles at origin (0,0)
and 1 ms occurrence-time bins at 41.321 s. There is no runtime parameter
override in v2. The former five-arm v1 record is rejected rather than silently
reinterpreted as this six-arm contract.

All geometry percentiles and RMS values use the admitted count. Missing or
invalid rays receive a predeclared 180-degree penalty. Valid OOF rays keep
their actual angular error. Nonprojectable locality records receive an
event-unique escape key: they remain in HHI and pair denominators without
being pooled into an artificial high-concentration failure tile.

An invalid negative-control ray cannot pass merely because its 180-degree
penalty is large: wrong, delayed, and retire identification gates additionally
require zero invalid or missing rays.

Persistent-map keys exclude time. Packet keys add the frozen occurrence-time
bin. Neither is a packet format or wire-size estimate; they only describe
potential grouping locality at this predeclared resolution.

The committed tests use synthetic normalized records, including a 1,100-row
denominator regression. They are not an execution or PASS claim for the real
UZH native adapter, its 1,094/6/0 acceptance vector, or the pinned archive.
