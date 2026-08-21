# MC-WTB score-free SO(3) axis audit

This standard-library-only package analyzes timestamped orientation samples.
It is a diagnostic library, not a controller or a performance evaluator.  Its
only inputs are non-negative timestamps, active sensor-to-world xyzw
quaternions, a coordinate-frame choice, and a caller-selected stationary
tolerance.  It neither reads campaign artifacts nor changes pose recovery.

`relative_rotation_vector()` computes the principal SO(3) logarithm between
two orientations. `analyze_axis_motion()` reports:

- one shortest-arc rotation vector and angular velocity per interval;
- cumulative path angle separately from endpoint net rotation;
- time-weighted mean/RMS speed and peak interval speed;
- a path-angle-weighted dominant axis and axial coherence;
- positive/negative travel and direction reversals on that dominant axis; and
- absolute x/y/z rotation-vector accumulation.

The frame is explicit. `BODY` uses `q_before^-1 * q_after`, while `WORLD` uses
`q_after * q_before^-1`, assuming the input is active sensor-to-world
orientation. Quaternion normalization and deterministic projective sign
selection make results invariant to input scale and `q` versus `-q`.

The dominant axis is obtained from the largest eigenvector of
`sum(angle * axis * axis^T)`, so forward and reverse travel identify the same
geometric axis. Coherence is the largest eigenvalue divided by moving path
angle. Equal largest eigenvalues are reported as no unique dominant axis.
Because SO(3) is noncommutative, per-step vector sums are not presented as net
rotation; net rotation is always recomputed from the first and last poses.

Run the focused tests from the repository root:

```bash
python3 -m unittest discover \
  -s tests/redred_mc_wtb_so3_axis_audit -p 'test_*.py' -v
```
