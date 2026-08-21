# MC-WTB Stage-4 pose-recovery geometry

This package is a pure-Python, score-free implementation of the Stage-4 pose
recovery primitives. It does not read datasets, compute event-quality scores,
or access any holdout.

Each `PoseSample` binds an orientation measurement to two distinct times:

- `measurement_timestamp_ns` is when the orientation applies;
- `commit_cycle` (also exposed as `availability_cycle`) is the first edge on
  which the packet commits.

An event on cycle `c` sees only poses with `commit_cycle < c`, matching the
frozen pre-edge-read rule. Measurement timestamps after the event are also
excluded from causal constant-angular-velocity recovery.

## Geometry and policies

- Quaternion inputs use xyzw order and are normalized.
- Interpolation aligns the second quaternion sign and follows the shortest arc,
  with deterministic normalized-linear fallback above dot `0.9995`.
- Delayed bracket interpolation requires `left <= event < right` and requires
  both bracket poses to have committed before the decision cycle.
- CAV uses quaternion relative-rotation exponentiation. It is constant angular
  velocity, not extrapolated NLERP.
- CAV is enabled only when
  `age <= min(5_000_000 ns, latest_timestamp - previous_timestamp)`.
- A failed CAV guard selects ZOH only when the latest committed pose is at most
  1 ms old; otherwise it selects sensor-fixed bypass.

`resample_counterfactual_1khz` creates the frozen hypothetical upstream 1 kHz
interface. It samples an offline truth series on an exact integer-nanosecond
grid and derives commit cycles with integer picosecond ceiling arithmetic plus
the requested commit delay. Original truth-packet availability is deliberately
irrelevant to this counterfactual arm; generated packets contain their new
commit cycles. This utility must not be used as a causal CAV input generator or
represented as an existing sensor/on-chip capability.

Run the focused tests from the repository root:

```sh
python3 -m unittest discover -s tests/redred_mc_wtb_pose_recovery -p 'test_*.py' -v
```
