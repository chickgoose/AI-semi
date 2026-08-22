# MC/WTB Stage 3 Causal Red-Team Review — 2026-08-22

## Status and authority

This is an independent, read-only design review of the Stage 3 implementation
boundary. It neither implements a predictor nor authorizes retuning, CAV
changes, external data, or a 108-query evaluation.

The reviewed Stage 3 boundary is governed by:

- baseline authority commit
  `2d7d3d128da2436b257ea1ce759bf8cb6c0b2466`;
- Checkpoint A content commit
  `4add865a1a3e46fbeb11bcfa49ffa48f0821712e`;
- frozen source-split-plan SHA-256
  `654582131fe0d44ea047268163e928d53fd7120493292eda957b8c3180e14a6e`;
- frozen contract SHA-256
  `3b151649404b39557acc57b665d19e28e787368b46d0797ac7d37fad5d60409f`;
- frozen architecture-candidates SHA-256
  `86be810c63a3e4817af9611c24b6d02283f763c0616e02c12628a92cf4de1178`.

Those identities, not a similarly named mutable file, are the authority. A
hash mismatch is a protocol failure and must stop Stage 3 before compilation or
evaluation.

## Exact Stage 3 implementation boundary

Stage 3 may implement only the already specified causal wrappers and frozen
candidate definitions for RG3-CAV, DSPB, and SO3-PLL, plus deterministic
logging, replay verification, mutation testing, and exact fallback plumbing.
It may not change the baseline, scorer, selector sidecar, query set, metric
definition, candidate topology, thresholds, gains, bounds, history length,
expert composition, reset rules, fallback order, or numeric profile without a
new candidate identity and a new pre-evaluation freeze.

For an event with immutable occurrence-decision edge `d`, a predictor input is
eligible only when all of the following are true:

1. the event record is visible before `d` and its mapped
   `occurrence_cycle < d`;
2. each consumed pose has `commit_cycle < d`;
3. each consumed pose measurement timestamp is no later than the event
   occurrence timestamp; and
4. the consumed state version was effective before `d`.

The decision edge is the occurrence-decision edge. Dequeue, retirement,
logging, scoring, and query completion are not substitutes for it. A pose that
commits on edge `d` is unavailable to an event decided on edge `d`.

Equal-timestamp event clusters are atomic: every event in the cluster consumes
one identical pre-cluster predictor state. No candidate or feedback update may
become visible until the whole cluster has committed. Prior event decisions are
append-only and must remain byte-identical under replay.

Only past committed poses, past visible events, frozen constants, and the
candidate's own causally effective state may enter the predictor. The following
are forbidden predictor inputs even if available elsewhere in the process:

- sequence, window, block, query, event, source, archive, role, or rank IDs;
- source paths or archive/content digests;
- absolute query start, selector labels, scenario labels, or split membership;
- scorer fields, evaluator loss, waste, harm, oracle-best route, or validation
  statistics;
- the right bracket, query endpoint, future queue contents, or any future pose;
- retrospective axis, sign, motion, stopping, reversal, or quality labels.

The evaluator may observe sealed decisions, frozen neutral inputs, and frozen
sidecars, but no evaluator- or sidecar-derived value may flow back into a
candidate. This separation must be structural and independently auditable, not
merely a convention at a call site.

At each 108 query, candidate state resets exactly at the frozen 50 ms pre-roll
start. Pre-roll poses and events may initialize state but are never scored.
State may not carry across queries. Candidate-specific history shortage does
not alter the ordered scored event set `Q`; it invokes and counts the exact
fallback.

Every event for which baseline A is valid is an always-on candidate attempt.
The universal fail-closed chain is:

1. candidate output when valid;
2. exact frozen current CAV when candidate fails;
3. fresh ZOH only when its age is at most 1 ms if CAV is unavailable; and
4. sensor-fixed bypass otherwise.

Fallback events remain in every applicable denominator. Overflow, invalid
quaternion, near-pi ambiguity, insufficient history, pose gap, expert
disagreement, saturation, loss of lock, and state corruption must produce an
explicit reason and the prescribed fallback. Silent clipping, wrapping,
event deletion, filtering, or retrying with knowledge of the outcome is
forbidden.

Candidate-specific boundaries are also fixed:

- **RG3-CAV:** exactly three eligible committed sensor-to-world poses; the two
  relative rotations and the prescribed tangent-space transports; frozen
  cadence, residual, direction, and magnitude gates; otherwise exact CAV.
- **DSPB:** exactly four frozen experts—current CAV, EWMA body-rate CAV,
  bounded RG3, and past-only axis-coherent signed-speed—selected only from
  prior effective credit state. Credit evaluates an immutable pre-pose
  forecast at the newly committed pose's measurement timestamp before the
  pose updates candidate state. The update is visible only in the next epoch.
- **SO3-PLL:** state is anchored to pose measurement time, never commit time.
  A newly committed pose evaluates the immutable forecast at that measurement
  time, then applies bounded P/I correction after the commit edge. It starts
  unlocked and uses CAV until the frozen lock count; specified anomalies clear
  lock and reset state.

## Red-team findings and mandatory closures

The following are not claims that a bug presently exists. They are attack
classes that the Stage 3 implementation and its independent verifier must
close before any 108 result is admissible.

### 1. Oracle leakage

**Threat.** A predictor can appear causal while using the right-bracket pose,
query endpoint, selector/scorer fields, future queue occupancy, scenario ID, or
retrospective labels through a helper, cache, trace object, expert teacher, or
debug field. DSPB is especially exposed if expert credit is recomputed after
the actual pose is known instead of scoring an immutable stored forecast.

**Required invariant.** The predictor API accepts a positive allowlist of
causal fields. Runtime candidate memory cannot reference evaluator or sidecar
objects. Every output receipt identifies the exact input records and prior
state version used. Diagnostic oracles may exist only in a disconnected
post-processing process.

**Fail condition.** Any forbidden-field injection that changes an output,
state, fallback, or gate—or is silently accepted by the predictor schema—is a
causal gate failure. Rejecting it only in a release build is insufficient.

### 2. Timestamp/commit confusion

**Threat.** Measurement timestamp, arrival/commit cycle, occurrence timestamp,
decision edge, dequeue edge, and retirement edge can be conflated. A pose may
have an old measurement timestamp yet commit too late, or commit early while
its measurement timestamp lies after the event. Rounding nanoseconds to cycles
can also turn strict `< d` into `<= d`. SO3-PLL can accidentally integrate from
commit time and thereby encode transport latency.

**Required invariant.** Eligibility checks both axes independently:
`pose.commit_cycle < d` and
`pose.measurement_timestamp <= event.occurrence_timestamp`. The adapter records
the raw time, mapped cycle, rounding rule, decision edge, and state effective
edge. PLL propagation is keyed to measurement time. Negative/pre-roll times and
nonmonotone inputs are handled by an explicit frozen rule, not unsigned wrap.

**Fail condition.** Changing dequeue or retirement timing changes a sealed
decision; a same-edge commit is consumed; a future measurement is consumed;
or PLL output changes solely because commit latency changes while the eligible
measurement history is identical.

### 3. Same-edge and atomic-cluster bugs

**Threat.** Software callback order can make a pose/feedback update visible to
an event on the same hardware edge. Parallel lanes can expose updates between
events with one timestamp. Stable-sort order can become an unintended causal
input.

**Required invariant.** Reads use the state snapshot effective strictly before
the decision edge. Writes publish at a separately recorded effective edge.
Equal-timestamp events bind one state version and publish candidate updates only
after the entire cluster. Lane or iteration order cannot affect output.

**Fail condition.** Reversing callback, container, lane, or within-cluster
order changes any cluster decision; an event observes a pose or feedback
committed on its decision edge; or replay cannot recover the same state-version
binding.

### 4. Adaptive tuning leakage

**Threat.** Gains, gates, histories, expert credit, hysteresis, fallback rules,
or numeric bounds may be adjusted using 108 outcomes, per-query scorer loss,
dynamic/final labels, or full-stream results. A losing run may be retried under
the same candidate ID. Equal-window and pooled metrics may influence online
state. Cross-query carry can learn query order.

**Required invariant.** All formulae and constants are hashed before the
synthetic gate. Any change creates a new candidate ID and complete retained
outcome. Online adaptation uses only frozen, past-causal signals specified by
the architecture; it resets at the frozen pre-roll boundary. Evaluation is a
one-way consumer. Sensitivity diagnostics do not rank or alter candidates.

**Fail condition.** A mutation exposes loss, selector, split, or aggregate
metrics and the candidate reads them; two distinct configurations share one
identity; a failed run is overwritten; or query permutation changes results
after results are restored to canonical query order.

### 5. Denominator drift

**Threat.** Candidate fallbacks or difficult events can disappear from `Q`;
candidate-specific validity can invalidate a query; per-window averages can be
substituted for ratio-of-sums; zero denominators can be coerced; or the full
stream can replace the frozen 108 ranking.

**Required invariant.** All candidates consume the identical full ordered `Q`.
Missing, extra, duplicate, reordered, or arm-filtered events fail the protocol.
Only a source/scorer condition common to all candidates may invalidate a query.
Fallback events are included. Pooled event-weighted and equal-window metrics
are separately labeled and computed by their frozen equations. Zero or
nonfinite denominators fail closed. The 25/100 ms sensitivity and full-stream
checks are non-ranking and cannot rewrite the 108 outcome.

**Fail condition.** Any candidate has a different ordered-Q digest or count;
the total event denominator differs from the neutral evaluator's authority;
fallbacks disappear; invalid arithmetic is replaced by zero; or aggregation
method is ambiguous in the artifact.

### 6. Hidden filtering

**Threat.** A candidate can claim always-on behavior while attempting only
favorable motion bins, low-residual samples, convenient event contents, or
well-conditioned histories. It can classify a poor prediction as invalid,
delete it, retry after the truth arrives, or omit its fallback reason. A gate
can indirectly filter using IDs or sidecar-derived categories.

**Required invariant.** The wrapper records one attempt for every A-valid
event before candidate quality is known. Candidate-use and every fallback
reason partition those attempts exactly. Gating uses only frozen causal inputs;
failure routes through the universal fallback without deleting or reclassifying
the event.

**Fail condition.** Attempt count differs from A-valid event count; use plus
fallback counts do not equal attempts; forced numerical failure reduces `Q`;
or permuting forbidden metadata changes an attempt, use decision, or fallback.

### 7. Feedback replay hazards

**Threat.** DSPB or PLL may reconstruct a forecast after seeing the newly
committed pose, evaluate a mutable state object, apply credit/correction before
same-edge events, accept a stale generation, update on an invalid pose, or
retroactively rewrite sealed decisions. Replay may accidentally use final
state rather than the historical version.

**Required invariant.** Each forecast used for feedback is immutable and binds
candidate ID, query/reset generation, state version, generation cycle, target
measurement timestamp, and forecast bytes. On pose commit, feedback evaluates
that stored forecast before the pose update. Its publication edge is recorded
and affects only later epochs. Invalid poses never update credit or PLL state.
Stale, duplicate, missing, or mismatched feedback fails closed and invokes the
frozen clear/reset behavior. All prior decisions remain byte-identical.

**Fail condition.** Regenerating a forecast after truth arrival gives a
different residual; a feedback write changes a same-edge decision; stale or
duplicate feedback changes state; invalid pose changes credit/lock; or replay
changes any decision earlier than the update's effective edge.

## Required pre-108 mutation suite

Every mutation below is mandatory. Tests must prove that a deliberately broken
implementation is detected, not merely that the reference path passes. A
mutation is successful only when the independent verifier produces the stated
fail-closed disposition. Compiler crashes, empty output, or unclassified test
errors do not count as detection.

| ID | Deliberate mutation | Required disposition |
|---|---|---|
| ORC-01 | Expose the right-bracket/query-end pose as a predictor input. | Schema/verifier rejects before scoring. |
| ORC-02 | Feed selector label, scorer loss, oracle-best route, or retrospective motion/sign label to a gate. | Reject; no 108 run may start. |
| ORC-03 | Feed query/window/source ID, path, digest, role, rank, absolute query start, or split membership. | Reject even if output happens to be unchanged. |
| ORC-04 | Let a candidate inspect future queue occupancy or next-pose availability. | Reject forbidden dependency. |
| ORC-05 | Recompute a DSPB expert forecast after revealing the target pose instead of consuming its immutable pre-pose snapshot. | Feedback binding/replay mismatch; reject. |
| TIME-01 | Set `pose.commit_cycle == d` with an otherwise old eligible measurement timestamp. | Pose is unavailable; pre-edge output/state is exact. |
| TIME-02 | Set `pose.commit_cycle < d` but its measurement timestamp after event occurrence. | Pose is unavailable. |
| TIME-03 | Set an old measurement timestamp with `commit_cycle > d`. | Pose is unavailable. |
| TIME-04 | Move dequeue and retirement edges while holding occurrence, decision, and eligible history fixed. | Decision bytes and state binding do not change. |
| TIME-05 | Mutate cycle conversion at both sides of a 6.5 ns boundary, including exact-edge rounding. | Only the frozen mapping is accepted; strict edge eligibility is preserved. |
| TIME-06 | Supply negative pre-roll time, nonmonotone pose time, duplicate pose time, and signed/unsigned boundary values. | Explicit frozen fallback/reject reason; no wrap or silent reorder. |
| TIME-07 | Anchor SO3-PLL propagation to commit time and separately jitter commit latency with identical measurement history. | Mutant is killed; authority output is latency-invariant. |
| EDGE-01 | Swap pose-commit and event callbacks on the same edge. | Same pre-edge event output under both schedules. |
| EDGE-02 | Publish DSPB credit or PLL correction to an event on its feedback commit edge. | Mutant is killed; event sees the older state version. |
| EDGE-03 | Create equal-timestamp clusters of 2, 3, and 6 events and reverse every within-cluster order. | All events bind one pre-cluster state; canonicalized decisions match. |
| EDGE-04 | Interleave cluster events across two simulated lanes. | Lane scheduling cannot change output or publication edge. |
| EDGE-05 | Update state after the first event of a cluster. | Mutant is killed by state-version and decision digest mismatch. |
| TUNE-01 | Alter one gain, gate, bound, history, hysteresis, expert formula/composition, fallback, reset, or numeric profile without changing candidate ID. | Identity/config hash mismatch; reject. |
| TUNE-02 | Make a runtime threshold depend on evaluator loss, waste, harm, enable, or validation aggregate. | Forbidden read detected; reject. |
| TUNE-03 | Preserve state across query reset or vary the reset point to obtain more history. | Mutant is killed; state/reset receipt mismatch. |
| TUNE-04 | Retry a dynamic/final losing outcome under the same candidate ID or overwrite an earlier variant result. | Append-only registry/receipt violation; reject. |
| TUNE-05 | Let 25/100 ms sensitivity or full-stream outcomes alter ranking/configuration. | Provenance/ranking violation; reject. |
| DEN-01 | Remove one fallback event, add one event, duplicate one event, or reorder two events. | Ordered-Q count/digest mismatch; reject. |
| DEN-02 | Mark a query invalid only for a candidate with insufficient history or internal failure. | Reject candidate-specific query-set drift. |
| DEN-03 | Exclude fallback events from effect, waste, harm, or enable denominators. | Exact-count/equation mismatch; reject. |
| DEN-04 | Substitute mean-of-window-ratios for pooled ratio-of-sums, or the reverse. | Labeled metric cross-check fails. |
| DEN-05 | Coerce a zero or nonfinite denominator/result to zero or omit it. | Protocol failure is mandatory. |
| DEN-06 | Replace frozen 108 ranking with full-stream or sensitivity ordering. | Promotion receipt rejected. |
| FILT-01 | Attempt only when residual, motion bin, event content, or history quality predicts success. | Attempt-count partition fails; reject. |
| FILT-02 | Convert a numerically bad candidate output into a deleted/skipped event. | Event remains in `Q` with explicit fallback; mutant killed. |
| FILT-03 | Retry a failed event after a pose, score, or truth becomes visible. | Append-only decision/replay mismatch; reject. |
| FILT-04 | Omit or merge a fallback reason so use plus reasons no longer exactly partitions attempts. | Accounting invariant fails. |
| FILT-05 | Permute forbidden metadata while preserving causal numeric inputs. | Any output/use/fallback change kills the implementation. |
| FB-01 | Mutate a stored DSPB/PLL forecast after its target pose commits. | Forecast byte/hash binding mismatch; reject. |
| FB-02 | Deliver feedback twice, out of order, to the wrong query generation, or with a stale state version. | No state change; explicit reject/reset reason. |
| FB-03 | Let an invalid/nonfinite pose update DSPB credit or PLL phase/lock. | Mutant killed; prior state remains or frozen reset applies. |
| FB-04 | Replay from final state rather than historical state snapshots. | Prior-decision digest mismatch; reject. |
| FB-05 | Change a decision whose edge precedes the feedback effective edge. | Append-only causal violation; reject. |
| FB-06 | On DSPB tie, corruption, invalid credit, or excess disagreement, retain the stale winner. | Mutant killed; frozen clear/unlock and fallback occur. |
| RG3-01 | Remove either tangent-space transport or reverse its frame direction. | Known-answer synthetic rotation disagrees; mutant killed. |
| RG3-02 | Remove one of three poses, introduce an excessive cadence gap, near-pi rotation, bad quaternion, residual/magnitude/direction failure, or arithmetic saturation. | Exact CAV fallback with the exact reason; event retained. |
| DSPB-01 | Add/drop/reorder an expert or alter an expert formula under the same candidate ID. | Candidate identity violation; reject. |
| DSPB-02 | Credit an expert using the just-committed pose before evaluating its pre-pose forecast. | Mutation killed by immutable-forecast known answer. |
| PLL-01 | Start locked, shorten lock count, or retain lock across gap, near-pi, phase jump, normalization failure, saturation, or limit cycle. | Mutation killed; CAV while unlocked and frozen reset reason observed. |
| FALL-01 | Reorder CAV, age-limited ZOH, and sensor-fixed fallback or admit ZOH older than 1 ms. | Exact fallback-chain mismatch; reject. |
| ROB-01 | Replace overflow failure with wrap, silent clip, NaN coercion, or outcome-dependent retry. | Mutation killed; explicit fallback/reason required. |
| REPLAY-01 | Repeat the same neutral input under allocator, thread, lane, and container-order perturbations. | Ordered decisions, state bindings, fallback counts, and metrics are byte-identical. |

In addition, the frozen robustness bundle—pose drops of 1/2/4/8, retention of
every 2nd/4th pose, rotation noise of 0.1/0.5/1 degree, 0.5 degree bias,
0.1 degree/s drift, jitter, acceleration, stopping, and reversal—must exercise
the same causal and accounting assertions. It is not a tuning set.

## Evidence required from the synthetic gate

Before 108 evaluation, one immutable gate receipt must bind at minimum:

- authority commits and all three frozen document hashes above;
- candidate ID and the complete serialized configuration/numeric-profile hash;
- executable, runner, neutral-input adapter, evaluator, mutation bundle, and
  independent-verifier hashes;
- reset/pre-roll mapping and raw-time-to-cycle mapping identity;
- canonical ordered-Q construction/version, even though the synthetic gate
  does not consume the 108 outcomes;
- for every synthetic event: occurrence timestamp/cycle, decision edge,
  attempt, candidate-use/fallback and exact reason, state version, effective
  edge, and identifiers of eligible consumed records;
- for every feedback item: query generation, forecast/state version, generation
  cycle, target measurement timestamp, pose commit edge, publication edge, and
  immutable forecast hash;
- mutation ID, expected disposition, observed disposition, and verifier reason;
- exact attempt/use/fallback partition counts and all invalid/overflow counts;
- canonical decision-stream hash and a second independent replay hash; and
- an explicit `SYNTHETIC_PASS` only when every required mutation is killed and
  every reference-path invariant passes.

Receipts and sealed decision streams must be append-only. A missing field,
unknown fallback reason, hash mismatch, nondeterministic replay, or mutation
that survives is `STAGE3_CAUSAL_GATE_FAIL`.

## Pre-108 gate verdict

Stage 3 is not allowed to consume the frozen 108 evaluation until the complete
mutation suite above has passed against the exact candidate binaries and an
independent verifier. Any surviving mutation blocks all candidates, even if
aggregate metrics look favorable; metrics cannot compensate for a causal or
denominator violation.

`SYNTHETIC_PASS` authorizes only the already frozen 108 screen. It is not
evidence of model quality, PPA closure, generalization, or promotion, and it
does not authorize candidate retuning. The 108 run must still enforce the
identical ordered `Q`, always-on attempt accounting, exact fallback chain,
append-only variant registry, and frozen promotion rules.

This review used no external data and evaluates no candidate results. Its sole
purpose is to define the fail-closed Stage 3 boundary and the causal attacks
that must be demonstrably rejected before evaluation.
