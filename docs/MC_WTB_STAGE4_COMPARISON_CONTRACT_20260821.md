# MC-WTB Stage-4 pose-recovery comparison contract

Status: **FROZEN BEFORE ARM SCORING**. This is a development-only contract,
not organizer criteria, holdout evidence, RTL/PPA evidence, or a novelty
claim. The Stage-1--3 negative result remains preserved at commit `7791e21`.

## Purpose and non-negotiable boundary

Stage 3 showed that a latest-at-or-before UZH pose is commonly 3.63--4.07 ms
old at the query boundary and that blindly applying that ZOH orientation gives
practically no all-window benefit. Stage 4 compares four ways of handling that
pose-delivery problem. A policy may inspect pose values, pose validity,
timestamps, queue state, and cycle time. It may never inspect an event-quality
loss or an arm score when deciding whether or how to correct an event.

The comparison does not replace the existing LOW/MID/HIGH representation
qualifier. It first determines whether a usable occurrence-time orientation
can be produced. A later integration must place this result before the
qualifier and preserve its exact-once epoch drain/commit behavior.

The comparison arms are event-level pose-delivery models, not permission to
change the committed representation route per event. In the hardware wrapper,
an epoch-start freshness decision conservatively covers the full epoch and is
combined with the motion qualifier before the route request is committed.
Per-event pose snapshots may update corrected coordinates within that frozen
route. Any per-event recovery failure takes the existing explicit raw-escape
disposition in order; it does not silently change the epoch route or discard
the event.

## Frozen source and timing

- Source: hash-pinned UZH `shapes_rotation` events, ground truth, and camera
  calibration already used by the causal development model.
- Registry: the existing 24 development windows, registry SHA-256
  `19df5788d3300ef9e6169165ed1dc68806a08f4e4af73eb4a52aebc9b642f62f`.
- Denominator: all 8,914 query events; each window has 1 ms warm-up and 1 ms
  query and is simulated independently.
- Forbidden interval: `[43_320_750_000, 43_322_000_000)` ns. It may be read
  only as part of whole-file hashing/parsing; it may not enter selection,
  decisions, an arm, or scoring.
- Clock period: 6.5 ns. With integer-nanosecond source timestamps,
  `cycle(t) = ceil((t-window_start)*1000/6500)` using integer arithmetic.
- Same-edge ordering: an event reads pre-edge pose state; a pose committed on
  that edge becomes usable on the next cycle.
- Causal-pose snapshot: at each event occurrence edge, copy the IDs,
  timestamps, commit cycles, values, and hashes of the eligible pre-edge pose
  state into that event's score-free decision record. `zoh_freshness`,
  `causal_cav`, and `supplied_pose_1khz` must transform from this snapshot even
  when queueing delays execution. Re-reading a newer pose at dequeue/transform
  time is a causality violation. Only `delayed_exact` may bind a later right
  bracket, and its receipt must label that use noncausal-at-occurrence.
- Accounting boundary: two event lanes, one-cycle transform pipeline, 102-bit
  event records, 192-bit pose packets, and at most 1,024 buffered events.
  These are cycle/state accounting constants, not measured physical results.
- Dataset poses are assumed to arrive at their recorded timestamp for this
  development model. The assumption must remain explicit in every receipt.

## Frozen arms

The common baseline is sensor-fixed all-bypass.

1. `zoh_freshness`: use the latest committed pose only when its age is at most
   1,000,000 ns; otherwise retire the unchanged sensor event as raw bypass.
2. `delayed_exact`: retain an event in ordered storage until the first
   committed pose strictly after the event permits bracket interpolation.
   Deadline is 6,000,000 ns. Timeout or full-buffer pressure produces ordered
   raw bypass, never discard. This arm is explicitly noncausal at occurrence
   and is primarily a diagnostic upper bound.
3. `causal_cav`: use the two latest committed poses only. Apply shortest-arc
   constant-angular-velocity extrapolation only when
   `age <= min(5,000,000 ns, latest_pose_time-previous_pose_time)` and all
   validity/arithmetic guards pass. Otherwise use valid fresh ZOH, or bypass if
   ZOH age exceeds 1,000,000 ns. There is no clipping, future bracket, or
   retrospective replacement.
4. `supplied_pose_1khz`: counterfactual poses are resampled from pose truth at
   an exact 1,000,000 ns cadence, serialized and hashed before scoring, then
   committed one cycle after their effective timestamp. Use the same 1 ms ZOH
   gate. This evaluates an upstream supplied-pose interface, not an existing
   UZH or on-chip estimator capability.

Quaternion interpolation/extrapolation uses shortest-arc sign alignment and a
normalized result. Translation is excluded because the current world-ray
metric depends only on rotation.

## Freshness safety profile for later hardware integration

Every eventual arm must also expose a score-independent, fail-closed
freshness decision covering the entire epoch:

```text
A_cover = epoch_end_ns - latest_pose_timestamp_ns
delta_pose = latest_pose_timestamp_ns - previous_pose_timestamp_ns
D_recent_q = ceil(pixel_gain_q_per_rad * relative_angle_upper_urad / 1_000_000)
E_recent_q = ceil(rate_growth_num * D_recent_q * A_cover /
                  (rate_growth_den * delta_pose))
E_floor_q = ceil(pixel_rate_floor_q_per_second * A_cover / 1_000_000_000)
E_total_q = max(E_recent_q, E_floor_q) + static_error_margin_q
```

Pass requires immediate-predecessor, latest-available, past-only poses; matching
timebase/calibration/profile hashes; authorized rate bounds; no arithmetic
overflow; the hard cover-age and rate-sample-interval limits; and
`E_total_q <= max_pixel_error_q`. Thresholds must come from interface,
geometric-error, and physical-dynamics contracts, never Stage-4 quality scores.
Failure selects `UNRELIABLE/SENSOR_FIXED_BYPASS` without dropping or flushing
events.

## Frozen metrics

For every window, arm, and aggregate, report:

- enable, freshness-veto, invalid-pose bypass, operational-waste, and
  quality-waste rates;
- primary all-event effect
  `R_all = 1 - sum(loss_policy)/sum(loss_sensor)`, with bypassed events charged
  their sensor-fixed loss;
- enabled-only effect as a diagnostic, never as an independent GO reason;
- accepted-event loss categories and exact conservation/identity checks;
- occurrence-to-retire and policy-added latency mean/p50/p95/p99/max;
- peak/time-weighted buffer occupancy, peak age, overflow, minimum zero-loss
  depth, and bit-cycles;
- pose/event bandwidth and all incremental on-chip state.

The quality loss remains the past-only, same-polarity causal-reference loss
with every equal-timestamp cluster scored before insertion.

## Frozen Stage-4 disposition rules

An arm is `GO_TO_RTL` only when all conditions hold:

- zero causality, blacklist, identity, duplicate, silent-loss, conservation,
  or receipt violation;
- `R_all >= 1.0%` and positive all-event effect in at least 18/24 windows;
- enable rate at least 10%, quality waste at most 50%, operational waste at
  most 1%, and zero accepted-event loss;
- added p99 latency at most 1 ms;
- peak buffer at most 1,024 x 102 bits, pose bandwidth at most 0.25 Mbit/s,
  and total incremental modeled state at most 128 Kibit;
- enabled-only and all-event effects agree in direction.

`STOP` applies to any causality/conservation/leakage violation, `R_all <= 0`,
or fixed cost-bound violation. `HOLD` applies when benefit is positive but
below 1%, positive-window consistency is insufficient, or enable coverage is
below 10%. `delayed_exact` and `supplied_pose_1khz` retain their explicit
semantic/system-boundary qualifications even when their numeric result passes.

## Leakage controls

The protocol, registry, arm parameters, generator, cycle model, scorer,
sources, and runtime are hashed before scoring. A score-free decision receipt
containing event ID, arm, occurrence/retire cycles, available pose IDs and
timestamps, pose age, disposition reason, and queue cycles is serialized and
hashed before it is joined with losses. All four arms are scored exactly once
and all outcomes are reported. Once opened, this registry is development-
selected forever and cannot serve as later confirmatory evidence.
