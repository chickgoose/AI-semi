# MC-WTB predictor Stage 1–2 contract — 2026-08-22

Status: **Checkpoint A content — freeze becomes effective only when a separate
committed receipt binds these file hashes and their content commit; no predictor
candidate implemented and no new sequence archive body opened or evaluated by
this checkpoint work**

Baseline authority: commit `2d7d3d128da2436b257ea1ce759bf8cb6c0b2466`.
The current descriptive checkpoint is
`results/redred_mc_wtb_so3_axis_audit/current_cav_axis_motion_checkpoint_20260822.json`.

## 1. Why this checkpoint exists

The current always-on two-pose constant-angular-velocity (CAV) reference shows
a 4.9083% pooled angular-loss reduction against `SENSOR_FIXED` on 108 selected
`shapes_rotation` windows. Its quality-waste rate is 38.2521%. The HIGH bins
are stronger than LOW bins, but this is one already-consumed recording and an
offline descriptive evaluation. It is not cross-scene generalization, causal
predictor proof, pan/tilt/roll proof, RTL, PPA, or P&R evidence.

The next question is deliberately narrow:

> Can a strictly causal pose predictor improve the always-on rotation CAV
> itself, especially in retrospective MID/HIGH diagnostic groups, before an
> event-quality selector is allowed to hide unfavorable events?

The later event-quality selector, world-tile compression, depth, translation,
parallax, and PPA are not merged into that question.

## 2. Data roles to be bound by the freeze receipt

The machine-readable plan to be bound is
`benchmarks/redred_mc_wtb_predictor_stage12/source_split_plan.json`.

| Role | Sequence | Rule |
|---|---|---|
| Development consumed | all `shapes_rotation`, including NEW108 and 43.321 s | may develop predictors; never confirmatory again |
| One-shot noncompensatory challenge | `dynamic_rotation` | tests independently moving content; cannot improve the primary result |
| Final confirmatory holdout | `poster_rotation` and `boxes_rotation` | identical frozen executable/parameters; both must pass independently |
| Future 6-DoF/translation/parallax development | `shapes_6dof` | no official depth ground truth is claimed; not a rotation-predictor selection or confirmation source |

This preserves the earlier unopened `poster_rotation` plus `boxes_rotation`
promise. It also prevents a same-scene sibling from being promoted after its
family has been inspected.

“Unopened” here reflects repository records and HTTP-header-only access by this
checkpoint work; it is not proof about every user, cache, worktree, machine, or
prior session. Before any final-family acquisition, a custodian must inventory
poster/boxes sibling sequences, local/shared caches and receipts, identify who
had access, and commit a negative-access or consumption receipt. Any prior
member, plot, image, derived statistic, or outcome access consumes that family.
Only an explicit `NEGATIVE_ACCESS_PASS` authorizes later acquisition.
`CONSUMED` permanently demotes that scene family to development and stops the
external evaluation; unknown or incomplete custody is HOLD.

### Continuous feedback development

The 108 selected windows are not sufficient by themselves to finish an audit of
a stateful feedback loop. They contain 456 selected pose packets and are
disconnected diagnostic windows. However, the complete source contains
23,126,288 events—about 260 times the 88,028 selected events—so replaying it for
every weak candidate would be wasteful.

The frozen selector registry and the Stage 3 predictor input have different
roles. The selector registry identified by SHA-256
`4d022cfde62c609c19c275add2e374d656babde3d4e1e6e1a849c5f384bb7e0d`
contains a 1 ms diagnostic warmup for each selected query. That 1 ms interval
is frozen evidence about the already-consumed selector cohort; it is not the
Stage 3 stateful-predictor pre-roll and must not be relabeled as 50 ms.

Stage 3 keeps the selector's ordered 108 query windows, query bounds, query
event IDs, and post-seal labels exactly unchanged. It reconstructs a separate
50 ms causal pre-roll for each window from the locked source identified by
source-lock SHA-256
`0e0dbb17db4d170de650729fe9ad1cd3f18d20c1bddcd577c84999fcde045a4c`.
Every candidate receives the same reconstruction. State resets independently
at `query_start - 50 ms`; only earlier events and poses committed under the
frozen edge rule may initialize that reset generation. Nearby windows may have
overlapping 50 ms pre-rolls, so one source event or pose may legitimately occur
in more than one independently reset window. Such occurrences are bound to
their `window_id` and `reset_generation` and do not represent duplicated query
outputs.

Only the ordered query event IDs in `Q` are globally exact-once. Within each
window, pre-roll and query source order and cardinality are exact, but global
ID uniqueness is not imposed on overlapping pre-roll occurrences. No pre-roll
event or pose is scored, enters `Q`, receives a query label, changes a metric
denominator, or is promoted into the query interval. The 1 ms selector
diagnostic rows remain immutable lineage evidence and are not scored again as
Stage 3 pre-roll.

Development is staged. All candidates first run synthetic causality/fallback
tests and the common 108-window screen using that source-reconstructed 50 ms
causal pre-roll. A query without the full pre-roll or common source/scorer
timestamp support is invalid for every candidate and is not replaced or
extended. Candidate-specific history shortage never changes `Q`; it takes the
exact fallback and is counted.
Only candidates that improve the current CAV overall and in MID/HIGH without
worsening waste advance. At most two advancing stateful candidates may replay
the complete locked `shapes_rotation` stream chronologically. That second gate
measures convergence, lock time, stopping/reversal response, dropout
reacquisition, and long-run drift. The present source lock records 11,883 pose
samples for that full replay.

This full-stream replay is still development-only: it increases the number of
causal state transitions, not the number of independent scenes or sensors.
Feedback state resets at the declared recording boundary. Validation or final
holdout state, residual statistics, gains, or normalization may not flow back
into another run.

### Pre-score remediation epoch

An `epoch2` execution is permitted only when the preceding epoch stopped on a
common pre-score infrastructure failure before any candidate outcome became
visible. The qualifying case is common plumbing such as supplying the frozen
1 ms diagnostic bundle to an interface that requires the frozen 50 ms
reconstruction; a candidate-specific rejection, unfavorable output, score,
loss, rank, plot, or grouped statistic is not a qualifying failure.

Before `epoch2`, an append-only lineage checkpoint must bind the failed epoch,
its commits and artifact hashes, the unchanged ordered query-ID and label
digests, the source lock, the precise pre-score failure, the remediation diff,
and the replacement neutral-input and adapter hashes. It must attest that no
candidate or scoring outcome was computed, read, logged, or used and that the
candidates, configs, query IDs/order/bounds, labels, scorer, metrics, gates,
and thresholds are unchanged. The remediation must be common to every
candidate and every candidate must restart through it. If outcome invisibility
cannot be proved, `epoch2` is forbidden under this exception and a separately
authorized development freeze is required.

The official UZH dataset page specifies the DAVIS text formats and describes
the rotation/6-DoF scenes. It releases the data under CC BY-NC-SA 3.0 for
non-commercial use including research. Raw archives remain outside Git;
source SHA-256 is computed before extraction. HTTP `ETag` is metadata, not a
cryptographic content identity. See the [official UZH dataset
authority](https://rpg.ifi.uzh.ch/davis_data.html) and the [dataset
paper](https://doi.org/10.1177/0278364917691115).

### Final-holdout selection

Each final sequence is evaluated in its own timestamp/event-ID namespace and
divided into non-overlapping 1 s blocks; only result receipts are aggregated.
Block zero starts at the smallest integer second boundary no earlier than the
source minimum event timestamp plus the frozen 50 ms pre-roll. The last partial
block is discarded. Every valid block enumerates 1 ms query windows on a 10 ms
grid. Exactly one is selected by the minimum SHA-256 rank over the frozen
domain prefix and NUL-delimited canonical archive hash, sequence ID, block
index, and query start; digest ties choose the earliest query. Eligibility can
use only byte/member integrity, strictly causal pose timestamp/commit
availability, scorer-truth timestamp bracketing availability, and nonempty
event support. Availability checks may read timestamps/record presence but not
pose values. Query starts are `block_start + n*10 ms` and must end inside the
block. A selected window that later proves unscorable makes the whole sequence
`PROTOCOL_FAIL`; it is never dropped or replaced.

Axis, sign, motion magnitude, image content, predictor output, event loss, and
future/query-end pose are forbidden selection inputs. BODY X/Y/Z and
LOW/MID/HIGH labels are joined only after neutral predictor outputs are sealed
and are secondary diagnostics, never selection controls. At least 40 valid
blocks are required per final sequence; rejected blocks are not replaced.
Burst density and ingress capacity never select or reject predictor windows.
They are reported later on the already-selected cohort as separate system
evidence. The selector receives sequence ID, exact source pins, captured
archive digest, acquisition-receipt digest, and frozen policy ID because
archive identity participates in ranking. Scene/motion/role descriptions
remain a non-executable sidecar.

Before external evaluation, each child registry/adapter/result seal binds a
unique sequence ID, sequence-namespaced window IDs, source lock, captured
archive, acquisition receipt, child registry, neutral input, label sidecar,
adapter seal, and result receipt. The aggregate is an ordered
`sequence_id -> child_seal` mapping and rejects duplicate, missing, relabeled,
or out-of-catalog children. Each child additionally binds candidate executable
and config, selector, evaluator, bootstrap implementation, and exact stress
bundle digests. Existing single-`shapes_rotation` seals do not authorize
multi-sequence confirmation.

## 3. Strict causal and no-oracle boundary

The predictor decision edge `d` is the event's immutable occurrence-decision
edge, not a later dequeue or retirement edge. A candidate may consume only:

- an event record visible before `d`, with its separately mapped
  `occurrence_cycle < d`;
- poses whose `commit_cycle < d` **and** whose measurement
  timestamp is no later than the event occurrence timestamp;
- earlier events whose records and occurrence cycles are visible before `d`;
- queue, validity, and registered predictor state visible before `d`;
- fixed configuration frozen before validation.

The predictor's neutral input projection contains only physical/calibrated
event fields (`x,y,polarity` or sensor ray), causal relative time to the latest
visible pose, visible pose quaternions and inter-pose time deltas, and declared
validity/transport-state flags. Sequence/window/block/query/event IDs, role,
rank, source/archive digests, absolute query start, selector labels, and scorer
fields are wrapper/sealer-only and forbidden predictor inputs.

A pose committing on the same decision edge is unavailable. Raw sensor time,
occurrence cycle, and decision cycle are separate domains; the later adapter
freeze binds their mapping, CDC, and commit semantics. Equal-timestamp event
clusters are atomic and consume one predictor-state version. An update derived
from a cluster becomes effective only after the entire cluster commits.
Forbidden inputs include future/right-bracket pose, query endpoint,
retrospective axis/sign/motion/reversal labels, evaluator loss, oracle-best
route, validation statistics, and future queue state.

Pose residual feedback is legal only as follows: when a new pose commits, an
earlier forecast or immutable pre-pose state version may be evaluated at that
pose's measurement timestamp before any state update. Its receipt binds the
forecast-state version, generation cycle, target timestamp, pose commit cycle,
and later effective cycle. The residual may update **future** predictor state.
It cannot change, rescore, or reroute any earlier event. Evaluator ground truth
may score a sealed neutral output, but it is not hardware input.

Every event decision is append-only and sealed before feedback publication with
event ID, occurrence/decision cycle, predictor model and state version, used
pose IDs, output, fallback reason, and digest. Replay must reproduce every
decision earlier than an update's effective cycle byte-for-byte.

## 4. Baselines and exact metrics

For identical ordered query IDs `Q`, let `S` be `SENSOR_FIXED`, `A` the frozen
current always-CAV reference, and `P` a candidate. Missing, extra, duplicated,
reordered, or arm-local filtered events are protocol failures.

For lower-is-better angular loss:

```text
E_X:S      = 1 - sum(loss_X) / sum(loss_S)
I_P:A      = 1 - sum(loss_P) / sum(loss_A)
Delta_P-A  = E_P:S - E_A:S = (sum(loss_A)-sum(loss_P))/sum(loss_S)
Ieq_P:A    = mean_w(1 - sum_e(loss_P,w,e) / sum_e(loss_A,w,e))
```

Every denominator is over the identical complete ordered `Q`; fallback events
remain included. A zero or non-finite per-window or pooled denominator is a
protocol failure. `I_P:A` and `Delta_P-A` have the same sign for positive
denominators and are reported as two scales, not treated as independent gates.
Report pooled and equal-window values independently. Also report:

- positive-window count for `P` versus both `S` and `A`;
- candidate-use and fallback rate;
- all-event sensor-relative waste
  `count(loss_P >= loss_S)/|Q|`, with ties counted as waste;
- all-event incremental waste `count(loss_P >= loss_A)/|Q|`;
- candidate-use sensor-relative waste
  `count(candidate_used and loss_P >= loss_S)/count(candidate_used)`;
- quality-harm mass `sum(max(0, loss_P-loss_S))/sum(loss_S)`;
- pose age, predictor residual, acceleration/reversal, BODY axis/sign, and the
  frozen LOW/MID/HIGH bins as post-seal diagnostics;
- added latency and a candidate-neutral state/operator/pipeline cost vector.

`candidate_used` means a non-baseline predictor model produced the geometry;
selection of exact current CAV is recorded as baseline-model fallback. Every
fallback reason is reported. Residual gates and DSPB model choice are part of
the composite predictor, never grounds for removing an event from `Q`.

This predictor stage has no bandwidth-saving requirement. A predictor does not
become a codec merely because better alignment might later improve world-tile
compression. Wire bits and compression are evaluated only after the predictor
winner is integrated with a representation/transport candidate.

### Candidate-neutral hardware boundary

The eventual endpoint target remains 6.5 ns and two events per cycle with
`II_event=1`; a pose-rate predictor must not reduce that throughput. Report:

```text
CNCP = (B_ff, B_sram, read_ports, write_ports, O_pose, O_event,
        II_event, critical_depth, pipeline_bits, max_wire_width, numeric_risk)
```

Candidate-added state classes are `S0 <=256 bit`, `S1 <=1 Kibit`, `S2 <=4
Kibit`, and `S3 >4 Kibit`. Compute classes are `C0` add/compare, `C1` fixed
multiply, `C2` shared pose-rate nonlinear, `C3` event-rate nonlinear, and `C4`
replicated two-lane nonlinear. Pipeline classes are `P0 <=1`, `P1 2..3`, `P2
4..8`, and `P3 >8 or variable`. Numeric classes are `N0` exact integer, `N1`
bounded fixed point, `N2` normalization/division, and `N3`
acceleration/adaptive feedback.

The common 108,799-bit logical envelope is reported separately and gives no
free credit for candidate state. Candidate buffers, nonlinear pipeline
registers, memory banking/replication, two ray-rotation lanes, mux/fanout,
fallback storage, and clock power are all charged. Model-only selection can
retain an `N3` research candidate, but `GO_TO_RTL` later requires at most
`S2/C2/P2/N2`; an `N3` winner remains RTL HOLD until a bounded bit-true
realization is separately approved.

`MODEL_ACCURACY_GO` and `IMPLEMENTATION_FEASIBILITY_GO` are separate verdicts.
Checkpoint A grants neither. A model can win the software comparison while
remaining hardware HOLD; hardware cost cannot be reported as predictor loss or
used to rewrite the scientific endpoint.

## 5. Always-on development and promotion rule

The candidate wrapper attempts prediction for every event on which `A` is
valid. An internal arithmetic/validity failure may fall back to exact `A` and
must record the reason. This is not the later event-quality selector. A
candidate must first demonstrate that its model, not selective removal, creates
the gain.

Development ranking on the consumed 108-window cohort requires:

1. zero causality, identity, order, loss, duplicate, seal, or denominator
   violation;
2. positive `I_P:A` overall and separately in retrospective MID and HIGH;
3. no LOW subgroup with pooled `I_P:A < -0.25%`;
4. all-event sensor-relative waste no worse than `A`, and positive-window
   counts versus both `S` and `A` reported without dropping fallback events;
5. deterministic exact fallback and no policy-added event buffering;
6. a complete CNCP estimate; no candidate above `S2/C2/P2` advances beyond
   model study, and every acceleration/adaptive candidate is explicitly `N3`
   and remains RTL HOLD.

The gate order is fixed:

1. `SYNTHETIC_PASS`: every candidate passes causality, fallback,
   timestamp/cycle, equal-timestamp, invalid-pose, overflow, and immutable-past
   mutations.
2. `SCREEN_PASS`: rank common 108-window results by larger MID/HIGH `I_P:A`,
   lower quality-waste, smaller actual state, then frozen candidate ID. At most
   the top two passing stateful candidates advance.
3. `FULL_STREAM_STABILITY_PASS`: replay those finalists chronologically. A
   failure is noncompensatory. The higher screen-ranked stable candidate is the
   sole winner. If it fails, the unchanged runner-up may win only by
   independently passing. If neither passes, STOP.
4. The sole winner may proceed to the dynamic challenge and later final
   holdout. Only after predictor confirmation is the separate event-quality
   selector studied.

Every material formula, expert composition, history length, gain, gate, bound,
hysteresis, fallback, reset policy, and numeric profile receives a distinct
candidate ID. Development may compare those IDs, but every tried variant and
outcome is retained. The exact winner is frozen before the one-shot dynamic
challenge; dynamic or final outcomes can never tune or replace it.

The common 108-window ranking denominator is never replaced by full-stream
outcomes. Development results choose a candidate but never establish
generalization. At query start, report state-valid/lock status, candidate use,
and every pre-roll fallback. Selection uses 50 ms only; fixed 25 ms and 100 ms
pre-roll replays are non-ranking initialization-sensitivity diagnostics.

Full-stream stability requires zero correctness failure and zero unhandled
non-finite/overflow/saturation (handled cases must take the recorded fallback),
initial lock and post-fault relock within 10 subsequent valid pose
commits, at most 10% operational fallback after first lock on the clean stream,
no more than 10 consecutive clean-stream pose intervals without a valid
candidate, pooled `I_P:A > 0`, and no 1 s block with `I_P:A < -1%`. Accuracy
cannot compensate for any stability failure.

## 6. One-shot challenge and final confirmation

`dynamic_rotation` is a noncompensatory challenge using the same deterministic
one-window-per-valid-block rule as the final holdout. The sole winner resets
once at the recording boundary and replays the entire recording
chronologically; selected windows control scoring only, not state updates. The
same single-reset chronological rule applies to each final sequence.

Dynamic dispositions are exact. A correctness/causality/identity failure is
`PROTOCOL_FAIL`. Pooled `I_P:A < -1%`, any 1 s block `I_P:A < -5%`, or
quality-harm mass above 1% is catastrophic `STOP` and forbids final execution.
For `-1% <= I_P:A < 1%` with no hard failure, record
`SAFE_NO_BENEFIT`: moving-scene benefit remains HOLD but static-scene final
confirmation may proceed. `I_P:A >= 1%` is only a scoped dynamic-sequence
benefit; it still does not establish moving-object correction. The challenge is
never rerun on a runner-up.

Before acquiring `poster_rotation` or `boxes_rotation`, freeze the executable,
candidate ID, fixed-point policy, weights/gains, fallback, input/source locks,
window selector, metrics, bootstrap, runtime, and dirty-tree rejection. Each
final sequence must independently satisfy:

- zero correctness, causality, provenance, seal, or silent-loss violation;
- pooled `E_P:S >= 1.0%` with a one-sided 95% block-bootstrap lower bound above
  0; the 1% floor is inherited as the practical-effect floor;
- pooled `I_P:A >= 1.0%` with a one-sided 95% block-bootstrap lower bound above
  0;
- `Ieq_P:A > 0`;
- pooled `Delta_P-A > 0` and no statistically clear harm in an adequately
  supported signed-axis subgroup;
- at least 75% positive windows versus `S` and 60% versus `A`;
- all-event sensor-relative waste at least 1 percentage point below `A`, with
  a one-sided 95% upper confidence bound on `waste_P-waste_A` below 0;
- exact fallback under frozen pose-delay/dropout/noise stresses;
- no uncharged predictor buffer, metadata, or variable-latency path.

An adequately supported signed-axis subgroup has at least five selected windows
and 500 query events. Apply Holm correction across the six BODY-axis/sign
subgroups; unsupported subgroups are reported as HOLD for that subgroup claim
and cannot be called noninferior.

Confidence intervals use 10,000 deterministic paired bootstrap replicates over
fixed contiguous 5 s superblocks, identified by `floor(block_index/5)`. A
sequence needs at least eight nonempty superblocks. Resample complete
superblocks with replacement and keep every selected window and
equal-timestamp cluster inside its superblock intact. The seed is the first 64
big-endian bits of `SHA256("REDRED-MCWTB-STAGE12-BOOTSTRAP-V1\\0" ||
contract_sha256 || "\\0" || archive_sha256 || "\\0" || sequence_id ||
"\\0" || metric_id)`, with lowercase ASCII hashes and UTF-8 IDs. Sort all
10,000 replicates and use element 499 (zero-based) as the one-sided 95% lower
bound and element 9499 as the one-sided 95% upper bound.

A hard correctness/authority failure is `PROTOCOL_FAIL`; a nonpositive point
effect or statistically supported harm is `STOP`; positive but insufficient
effect, confidence, or subgroup support is `HOLD`; only all gates produce
`GO_CONFIRMATORY`. Pooled performance cannot rescue one failed sequence. The
dynamic challenge is run on the sole frozen winner once; its failure consumes
that challenge and does not authorize trying the runner-up. Any post-unblind model,
gain, threshold, selector, metric, or implementation change consumes the
holdout and requires a genuinely new untouched source for confirmation.

## 7. Robustness and failure receipts

Pose perturbations affect predictor-visible inputs only; scorer truth stays
unchanged. The later executable freeze binds their exact generated bytes before
any candidate outcome exists. At minimum it contains dropout bursts of 1/2/4/8
poses, retain-every-2nd and retain-every-4th pose, 0.1/0.5/1.0 degree SO(3)
noise, 0.5 degree fixed bias, and 0.1 degree/s drift, plus deterministic cadence
jitter, acceleration, stopping, and reversal cases. Seeds derive from contract
and captured archive hashes. A scenario identifier is not presented to a
predictor unless equivalent fault metadata exists in the hardware interface.

Overflow, invalid quaternion, near-pi ambiguity, insufficient history, pose
gap, predictor disagreement, and fixed-point saturation must produce an exact
fallback reason. Wrapping, silent clipping, event deletion, and outcome-aware
retry are forbidden.

## 8. Claim ceiling

Even a complete PASS supports only orientation-only improvement on two unseen
scenes from the same UZH DAVIS240C family. It does not establish universal
pan/tilt/roll semantics, translation/parallax/depth correction, dynamic-object
correction, other-sensor generalization, codec benefit, RTL correctness, or
45 nm PPA/P&R.
