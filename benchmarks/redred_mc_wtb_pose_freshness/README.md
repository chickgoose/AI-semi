# MC-WTB score-independent pose freshness gate

This package implements the Stage-4 freshness safety slice from
`docs/MC_WTB_STAGE4_COMPARISON_CONTRACT_20260821.md`. It consumes only a
snapshot of pose and interface metadata available at the epoch-start decision
edge. It never imports an event scorer, loss, arm result, dataset selector, or
quality threshold.

Two profiles share the same fail-closed structural checks:

- `age_only/v1` requires the latest pose to cover the complete epoch within an
  inclusive hard age limit.
- `age_times_rate/v1` retains that hard age backstop and additionally applies
  the frozen recent-angle, pixel-rate floor, static margin, and pixel-error
  equations with exact ceiling division.

The rate-aware equations are:

```text
A_cover = epoch_end_ns - latest_pose_timestamp_ns
delta_pose = latest_pose_timestamp_ns - previous_pose_timestamp_ns
D_recent_q = ceil(pixel_gain_q_per_rad * relative_angle_upper_urad / 1_000_000)
E_recent_q = ceil(rate_growth_num * D_recent_q * A_cover /
                  (rate_growth_den * delta_pose))
E_floor_q = ceil(pixel_rate_floor_q_per_second * A_cover / 1_000_000_000)
E_total_q = max(E_recent_q, E_floor_q) + static_error_margin_q
```

The gate requires two strictly ordered immediate-predecessor poses, proof that
the latest member is the latest available at the decision snapshot, past-only
timestamps, exact timebase/stream/calibration/profile identities, valid pose
values, a hash-bound timebase definition, and clock alignment. The rate-aware profile also requires an
authorized rate-bound assumption and an inclusive rate-sample interval limit.

All public numeric metadata is unsigned 64-bit. Products are checked against
an unsigned 128-bit intermediate boundary; overflow selects
`UNRELIABLE_SENSOR_FIXED_BYPASS`. Runtime qualification failure never means
drop, flush, clipping, or reuse of a stale decision. Configuration or typed-API
misuse raises `FreshnessContractError` before qualification.

Thresholds are interface and geometry policy, not learned values:

- hard cover age comes from the pose producer service and epoch duration;
- the rate interval and growth ratio come from a physical-dynamics contract;
- pixel gain comes from a hash-bound calibration-only projection envelope;
- rate floor and static margin cover pose, timestamp, calibration, and numeric
  uncertainty;
- maximum pixel error comes from the downstream coordinate tolerance.

Changing one of those values changes the deterministic configuration digest.
Every decision also binds a deterministic canonical evidence digest.

Run the focused tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/redred_mc_wtb_pose_freshness -p 'test_*.py' -v
```

The package is standard-library-only and avoids syntax introduced after
Python 3.8.
