# UZH MC-WTB geometry tests

These standard-library tests define the public numerical contract expected
from `benchmarks.redred_uzh_mc_wtb.geometry`.  The expected values are literal
axis-rotation matrices or are computed by small analytical oracles in the test
module; the production helpers are never used to construct their own expected
values.

The deliberately small public surface exercised here is:

- `GeometryError` and `RadtanCalibration`
- `quaternion_xyzw_to_world_camera_matrix`, `slerp_xyzw`,
  `WorldCameraPose`, and `relative_geometry`
- `distort_normalized` and `undistort_normalized`
- `deterministic_pixel_round` and `warp_raw_sensor_to_reference`

The warp consumes `RelativeGeometry`, uses its `R_C0_Ct`, and returns one of
the disjoint statuses `in_fov`, `outside_reference_image`,
`behind_reference`, or `invalid_distortion`.  The latter is not OOF.

Run the always-on unit tests with:

```sh
bash tests/redred_uzh_mc_wtb/run_all.sh
```

The exact local UZH integration is deliberately opt-in because it reads the
509 MB `events.txt`.  It never downloads data.  Point it at an extracted copy
of the pinned `shapes_rotation` text archive:

```sh
REDRED_UZH_SHAPES_ROTATION_ROOT=/tmp/uzh-shapes_rotation \
  bash tests/redred_uzh_mc_wtb/run_all.sh
```

Once enabled, missing files or mismatched hashes are failures.  A successful
external run must conserve all 1,100 events in `[41.321,41.322)` and classify
exactly 1,094 in the raw-reference FOV and six outside it.
