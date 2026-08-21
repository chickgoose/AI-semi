# MC-WTB Stage-4 pose-recovery comparison contract

Status: **REFROZEN AFTER PRE-SCORE RED TEAM; NO ARM SCORE OPENED**. This is a development-only contract,
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
  `causal_cav`, and `oracle_resampled_groundtruth_1khz` must transform from this snapshot even
  when queueing delays execution. Re-reading a newer pose at dequeue/transform
  time is a causality violation. Only `delayed_exact` may bind a later right
  bracket, and its receipt must label that use noncausal-at-occurrence.
- Accounting boundary: two event lanes, one-cycle transform pipeline, 102-bit
  event records, 192-bit pose packets, and at most 1,024 buffered events.
  These are cycle/state accounting constants, not measured physical results.
- The source presents at most two records per cycle in stable event-ID order.
  An equal-timestamp cluster has one occurrence-cycle pose snapshot even when
  service spans cycles. Any source serialization/storage before this boundary
  is an explicit external system cost and cannot change the occurrence
  snapshot.
- The comparison sink is always ready. Each lane has initiation interval one;
  up to two ingress admissions and two ordered retirements may occur in the
  same cycle. Visible pose state is sampled first, ready heads retire second,
  then new records are admitted. A one-cycle transform may not reorder lanes.
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
   and has disposition `DIAGNOSTIC_UPPER_BOUND`; it can never receive
   `GO_TO_RTL`. A right bracket is eligible only if visible no later than
   `occurrence_cycle + ceil(6_000_000*1000/6500)`; a pose committing on that
   deadline edge is not yet visible and is too late.
3. `causal_cav`: use the two latest committed poses only. Apply shortest-arc
   constant-angular-velocity extrapolation only when
   `age <= min(5,000,000 ns, latest_pose_time-previous_pose_time)` and all
   validity/arithmetic guards pass. Otherwise use valid fresh ZOH, or bypass if
   ZOH age exceeds 1,000,000 ns. There is no clipping, future bracket, or
   retrospective replacement.
4. `oracle_resampled_groundtruth_1khz`: counterfactual poses are generated by
   shortest-arc SLERP from pose truth on the global sequence phase `t=0 mod
   1,000,000 ns`, only where both source brackets exist. They are serialized
   and hashed before scoring, delivered atomically on a modeled 192-bit path,
   committed one cycle after their effective timestamp, and visible to events
   the following cycle. Use the same 1 ms ZOH gate. Its disposition is
   `INTERFACE_VALUE_ONLY`, never unqualified `GO_TO_RTL`; it evaluates the value
   of an oracle-fed upstream interface, not an existing UZH/on-chip estimator.

Quaternion interpolation/extrapolation uses shortest-arc sign alignment and a
normalized result. Translation is excluded because the current world-ray
metric depends only on rotation. Negate the second endpoint only when the dot
product is strictly less than zero. At exactly zero, retain the original sign;
this is the canonical tie rule.

The 1,024 entries include the complete delayed holding queue and its bypass
state. When full and new records arrive, up to two oldest heads are forced to
ordered raw bypass and retired before the new records are admitted. Newer raw
records may never bypass an older head. With the frozen always-ready sink this
requires at most two reads and two writes per cycle and loses no event. Output
stall/backpressure, extra skid storage, producer serialization, and CDC remain
outside this comparison and must be charged before hardware integration.

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

The quality loss remains past-only and same-polarity, with every complete
equal-timestamp cluster scored before insertion. Raw sensor rays and world rays
must never share a bank. For every arm, construct two score-only shadow banks:

- a sensor bank updated by every event's sensor ray; and
- an arm-world bank updated by every event's deterministic arm transform,
  regardless of the arm's enable/bypass decision.

Thus gate decisions cannot reduce reference density. The shadow transforms are
not runtime decisions and cannot feed the decision receipt. A bypassed event
uses its sensor-bank loss; an enabled event uses its arm-world-bank loss. Every
query event must have both losses available or the arm is a protocol failure;
no unavailable event is removed from the denominator. Each bank retains the
same polarity separation, age, capacity, ordering, and equal-timestamp rule.

All loss sums use Python binary64 `math.fsum` in increasing event-ID order. A
zero or non-finite sensor denominator is a protocol failure. A window counts
as positive only when `R_window > 1e-6` as a fraction; equality and smaller
values are not positive. The 18/24 rule is an engineering consistency
heuristic, not a statistical-confidence claim.

All rates are aggregate event-weighted quantities. Enable uses accepted query
events; freshness/invalid bypass uses accepted query events; operational waste
uses attempted corrections; quality waste uses enabled events after loss join,
with ties counted as waste. Zero attempted/enabled denominators fail their
corresponding GO coverage test rather than producing a favorable zero.

Latency percentiles use nearest rank on per-event integer cycle deltas, sorted
by `(latency_cycles,event_id)`. Added latency is each policy retirement cycle
minus that event's always-bypass baseline retirement cycle, not a difference
of percentile summaries. The aggregate and every-window p99 must satisfy the
1 ms GO limit.

## Frozen Stage-4 disposition rules

The causal arms may receive `GO_TO_EPOCH_INTEGRATION` only when all conditions
hold. This is not a direct RTL GO because the production LOW/MID/HIGH profile
and its epoch interlock schedule are not part of this pose-delivery assay:

- zero causality, blacklist, identity, duplicate, silent-loss, source-overrun,
  accepted-event-loss, conservation,
  or receipt violation;
- `R_all >= 1.0%` and positive all-event effect in at least 18/24 windows;
- enable rate at least 10%, quality waste at most 50%, operational waste at
  most 1%, and zero accepted-event loss;
- aggregate and every-window added p99 latency at most 1 ms;
- peak buffer at most 1,024 x 102 bits, pose bandwidth at most 0.25 Mbit/s,
  and total incremental modeled state at most 128 Kibit;
- enabled-only and all-event effects agree in direction.

`STOP` applies to any undeclared causality/conservation/leakage violation,
`R_all <= 0`,
or fixed cost-bound violation. `HOLD` applies when benefit is positive but
below 1%, positive-window consistency is insufficient, or enable coverage is
below 10%. Intentional future access inside `delayed_exact` is declared fixed-
lag diagnostic behavior rather than a protocol violation, but it remains
`DIAGNOSTIC_UPPER_BOUND`. `oracle_resampled_groundtruth_1khz` remains
`INTERFACE_VALUE_ONLY` even when its numeric result passes.

## Leakage controls

The protocol, registry, arm parameters, generator, cycle model, scorer,
sources, and runtime are hashed before scoring. A score-free decision receipt
containing event ID, arm, occurrence/retire cycles, available pose IDs and
timestamps, pose age, disposition reason, and queue cycles is serialized and
hashed before it is joined with losses. All four arms are scored exactly once
and all outcomes are reported. Once opened, this registry is development-
selected forever and cannot serve as later confirmatory evidence. This
Markdown file is normative. The machine-readable JSON is a subset and must
record the exact SHA-256 of this normative file before decision generation.
