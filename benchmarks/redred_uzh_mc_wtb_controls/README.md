# UZH MC-WTB control evaluator

This standard-library-only package evaluates one frozen event cohort across
exactly five controls: `SENSOR_FIXED`, `MC_CORRECT`, `MC_WRONG`,
`MC_DELAYED`, and `RETIRE_WARP`.

It reports only world-ray geometry gates and two kinds of tile-locality
opportunity. It does not serialize anything and makes no bandwidth,
compression, codec, benefit, RTL, or PPA claim. A geometry-only PASS is named
`PASS_GEOMETRY_CONTROLS_ONLY`; the enclosing evidence status always remains
`CONTROL_EVALUATION_ONLY_NO_BANDWIDTH_OR_BENEFIT_CLAIM`.

## Adapter record boundary

`evaluate_records()` consumes JSON-like mappings. One mapping owns one event
ID and must contain all five arm outputs, so an adapter cannot supply a
different admitted set per arm. Event IDs must be unique and strictly
increasing; timestamps must be nondecreasing. The exact record shape is:

```json
{
  "schema": "redred.uzh_mc_wtb_controls.adapter_record/v1",
  "event_id": 13856250,
  "timestamp_ns": 41321000000,
  "polarity_01": 1,
  "oracle_status": "in_fov",
  "oracle_reference_ray": [0.0, 0.0, 1.0],
  "arms": {
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

The result binds the exact ordered cohort as SHA-256 over one ASCII decimal
event ID plus LF per record; count and endpoint anchors are not treated as a
substitute for complete identity.

`SENSOR_FIXED.locality_{x,y}` are raw sensor coordinates, while its
`reference_ray` is the receiver-side occurrence-pose world/reference ray.
MC arms provide their projected continuous coordinates. Valid OOF records
retain their ray and outside coordinate, so they participate normally in
angular and tile metrics. `behind_reference` retains a ray but has null
locality coordinates. `invalid_distortion` has a null ray and null projected
coordinates. Sensor-fixed always retains its raw locality coordinates.

Occurrence lookup is mandatory for sensor-fixed, correct, and wrong arms;
delayed lookup must be strictly earlier; retire lookup cannot precede the
event. These timestamps are evidence fields, not a substitute for the
adapter's pose-bracket validation.

## Frozen parameters and denominators

The committed `preregistered.json` fixes 8x8 half-open tiles at origin (0,0)
and 1 ms occurrence-time bins at 41.321 s. There is no runtime parameter
override in v1.

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
