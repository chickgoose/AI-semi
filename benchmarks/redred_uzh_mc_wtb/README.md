# UZH MC-WTB geometry core

This package contains standard-library-only, I/O-free geometry primitives for
the UZH DAVIS path. UZH poses are interpreted as xyzw camera-to-world `T_WC`.
For reference camera `C0` and current camera `Ct`, it derives:

```text
R_Ct_C0 = R_WCt.T @ R_WC0       # reference-to-current / world-to-sensor
R_C0_Ct = R_WC0.T @ R_WCt       # current sensor-to-reference
```

Translation is interpolated and retained in `RelativeGeometry`, but is never
applied by `warp_raw_sensor_to_reference`. The warp is explicitly
orientation-only: raw OpenCV radial-tangential inverse, `R_C0_Ct`, forward
radial-tangential projection onto the same raw reference lattice, then
`floor(value+0.5)` for an in-range continuous coordinate.

Statuses are disjoint:

- `in_fov`
- `outside_reference_image` (valid geometry; raw escape required)
- `behind_reference` (valid geometry; raw escape required)
- `invalid_distortion` (invalid geometry; not an OOF count)

SLERP normalizes xyzw inputs, takes the shortest arc by flipping the second
quaternion when the dot product is negative, and uses a fixed normalized-linear
fallback above dot product `0.9995`. Radtan inversion uses at most 50 fixed-point
iterations with a `1e-15` step and forward-residual tolerance.

This package does not parse files, implement a raw packet or decoder, apply
translation/depth, or claim MC-WTB compression, wire, accuracy, RTL, or PPA
benefit.
