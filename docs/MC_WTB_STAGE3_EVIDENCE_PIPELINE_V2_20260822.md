# MC-WTB Stage 3 evidence pipeline v2 — 2026-08-22

Status: **normative implementation note; no implementation, candidate retune,
CAV change, data execution, model result, CNCP feasibility result, RTL, or PPA
is authorized by this document**

This note freezes the Stage 3 v2 evidence boundary for the unchanged native
RG3-CAV, DSPB, and SO3-PLL candidates. It supersedes v1 candidate-output and
screen evidence semantics where they conflict. It does not change the frozen
Stage12 source roles, ordered query population `Q`, candidate mathematics,
baseline/fallback mathematics, scorer, metrics, or promotion thresholds.
Changing any field, enum, mapping, identity, reset rule, or verification order
below requires a v3 schema and a new pre-evaluation freeze.

## 1. Frozen authority and identity

Every v2 receipt binds these Checkpoint A authorities:

- baseline commit `2d7d3d128da2436b257ea1ce759bf8cb6c0b2466`;
- content commit `4add865a1a3e46fbeb11bcfa49ffa48f0821712e`;
- source-plan SHA-256
  `654582131fe0d44ea047268163e928d53fd7120493292eda957b8c3180e14a6e`;
- Stage12 contract SHA-256
  `3b151649404b39557acc57b665d19e28e787368b46d0797ac7d37fad5d60409f`;
- architecture SHA-256
  `86be810c63a3e4817af9611c24b6d02283f763c0616e02c12628a92cf4de1178`.

These hashes identify the original Checkpoint A bytes and remain historical
authorities. This clarification does not silently replace them with the hashes
of edited documentation. A permitted pre-score remediation epoch must bind the
original authorities, this documentation lineage, and the remediated artifacts
in a separate append-only checkpoint before execution or scoring.

`candidate_id` is the exact UTF-8 byte string returned by the native frozen
model: `RG3_POLICY.candidate_id`, `DSPBConfig().candidate_id`, or
`SO3PLLConfig().candidate_id`. Replacement, shortening, escaping,
case-folding, slash-to-dot conversion, aliases such as `RG3`/`SO3_PLL`, and
output-only IDs are forbidden. The same bytes must appear in the registry,
configuration, execution receipt, every candidate-use decision, verifier
receipt, mutation receipt, and result. A material configuration change creates
a new native ID before any protected outcome is read.

Canonical serialization is UTF-8 JSON with sorted object keys, no insignificant
whitespace, exact integers, finite decimal numbers only, and SHA-256 over the
serialized bytes with the object's own digest field omitted. Unknown or extra
fields fail closed.

## 2. Frozen signed edge mapping

All cycle and edge values in v2 are signed 64-bit integers. Unsigned coercion,
clamping, wrap, or rejection solely because a valid pre-roll value is negative
is forbidden. For every event:

```text
decision_edge = signed_ceil_div(
    (event_timestamp_ns - warmup_start_ns_inclusive) * 1000, 6500)
occurrence_cycle = decision_edge - 1
```

Thus `occurrence_cycle == decision_edge - 1` and
`occurrence_cycle < decision_edge` are both mandatory. The current cycle-model
field historically named `occurrence_cycle` is the v2 `decision_edge`; it may
not be copied into both fields. Equal-timestamp events have identical
occurrence and decision edges. A pose is eligible only when both
`pose.commit_cycle < decision_edge` and
`pose.measurement_timestamp_ns <= event_timestamp_ns`. Predictor state is
eligible when `state_effective_edge <= decision_edge`; an update produced on
edge `p` has `state_effective_edge == p + 1`. Dequeue, callback,
retirement, logging, and scoring edges never affect a decision.

The Stage 3 source-reconstructed 50 ms window reset occurs at signed edge zero.
It is distinct from the frozen selector registry's 1 ms diagnostic warmup.
Pose commits before reset may remain baseline source evidence when the frozen
baseline permits them, but they may not initialize post-reset candidate state.
The exact distinction is verified rather than silently dropping or
unsigned-wrapping records.

### 2.1 Frozen query authority and reconstructed pre-roll

The frozen selector registry, SHA-256
`4d022cfde62c609c19c275add2e374d656babde3d4e1e6e1a849c5f384bb7e0d`,
is the authority for the ordered 108 query windows, exact query bounds, query
event IDs, and label lineage. Its 1 ms warmup is a diagnostic registry field,
not the predictor initialization interval. Stage 3 reconstructs
`[query_start - 50 ms, query_start)` from the locked source, SHA-256
`0e0dbb17db4d170de650729fe9ad1cd3f18d20c1bddcd577c84999fcde045a4c`,
without changing any query ID, query event, bound, order, or label.

Each reconstructed window is an independent reset generation. Its 50 ms
pre-roll may overlap another window's pre-roll, and the same source event or
pose may therefore appear in multiple generations. Each occurrence is bound
to `window_id` and `reset_generation`. Only query event IDs are globally
exact-once in `Q`; pre-roll IDs are exact in source order within a window but
are not required to be globally unique across windows. Pre-roll records are
initialization evidence only: they are never scored, labeled as query events,
included in a metric denominator, or substituted for a frozen query event.

## 3. Exact v2 evidence objects

The schemas below are closed field sets. Arrays retain source order unless an
explicit digest names a canonical order.

### 3.1 Source manifest

`redred.mc_wtb_predictor_stage3.source_manifest/v2` contains exactly:

```text
schema, authority, candidate_id, candidate_config_sha256,
runtime_identity, runtime_executable_sha256, roots, files,
dependency_graph_sha256, manifest_sha256
```

`authority` contains exactly `baseline_commit`, `content_commit`,
`source_plan_sha256`, `stage12_contract_sha256`, and
`architecture_sha256`. Each `roots` row contains exactly `logical_name` and
`phase`.

Each `files` row contains exactly:

```text
logical_name, repo_relative_path, phase, role, size_bytes, sha256,
dependencies
```

`phase` is `EXECUTION`, `INDEPENDENT_VERIFICATION`, or `SCORING`; `role` is
`RUNNER`, `ADAPTER`, `CANDIDATE`, `CYCLE_MAPPING`, `FALLBACK`, `GEOMETRY`,
`SERIALIZER`, `VERIFIER`, `EVALUATOR`, `SCHEMA`, or `CONFIG`.
`dependencies` is an ordered list of `logical_name` values.

The graph is the complete transitive closure from all declared roots. It must
include the runner, identity-free adapter, native candidate, cycle mapping,
current-CAV/ZOH/sensor-fixed authorities, quaternion-to-ray geometry,
serializer, independent verifier, evaluator, schemas, and canonical config.
The bound runtime covers standard-library behavior. Undeclared local imports,
dynamic code loading, generated unsealed code, symlinks escaping the repository,
missing files, hash drift, dirty or unindexed runtime bytes, dependency cycles
not declared as one component, and files reachable across phases outside the
allowlist are protocol failures.

### 3.2 Window row

`redred.mc_wtb_predictor_stage3.window_evidence/v2` contains exactly:

```text
window_id, warmup_start_ns_inclusive, query_start_ns_inclusive,
query_end_ns_exclusive, reset_generation, reset_edge,
initial_state_version, initial_state_sha256,
query_start_state_version, query_start_state_sha256,
query_start_state_valid, query_start_lock_status,
pre_roll_attempt_count, pre_roll_candidate_use_count,
pre_roll_route_counts, ordered_event_ids_sha256,
ordered_query_event_ids_sha256, event_rows_sha256,
feedback_rows_sha256, window_sha256
```

`reset_generation` is unique per window and `reset_edge` is exactly zero. The
reset-state payload is derived only from the native candidate ID and canonical
config; its receipt and state identity also bind `reset_generation`. No prior
window state is permitted. `query_start_lock_status` is one of
`STATELESS`, `UNLOCKED`, `LOCKED`, or `FAULTED`. Route counts use all four
routes below as exact keys, including zero values. The fixed 25/100 ms sensitivity runs
have separate non-ranking receipts and cannot modify this 50 ms row.
Here `warmup_start_ns_inclusive` is the reconstructed Stage 3 boundary exactly
50 ms before `query_start_ns_inclusive`; it is not copied from the selector's
1 ms diagnostic row. `ordered_event_ids_sha256` is per-window and includes the
reconstructed occurrence stream, while `ordered_query_event_ids_sha256` binds
the unchanged scored subset. Cross-window exact-once applies only to the latter.

### 3.3 Event-decision row

`redred.mc_wtb_predictor_stage3.event_decision/v2` contains exactly:

```text
event_id, event_content_sha256, event_timestamp_ns, is_query,
occurrence_cycle, decision_edge, reset_generation,
candidate_id, selected_model_id,
state_version, state_sha256, state_parent_sha256, state_effective_edge,
candidate_attempted, candidate_used, route,
candidate_failure_reason, nonattempt_reason, fallback_reason,
used_pose_receipts, output_quaternion_xyzw, world_ray_xyz,
decision_sha256
```

Each `used_pose_receipts` row contains exactly `pose_id`, `pose_sha256`,
`measurement_timestamp_ns`, and signed `commit_cycle`. It lists every pose
whose value influenced the selected output, with no hidden dependency.
`route` is exactly one of:

```text
CANDIDATE | CURRENT_CAV | FRESH_ZOH | SENSOR_FIXED
```

The accounting semantics are fixed:

| Condition | attempted | used | route | selected_model_id |
|---|---:|---:|---|---|
| Baseline A valid and candidate valid | true | true | `CANDIDATE` | exact native `candidate_id` |
| Baseline A valid and candidate fails | true | false | `CURRENT_CAV` | `CURRENT_CAV` |
| A unavailable and ZOH age <= 1 ms | false | false | `FRESH_ZOH` | `FRESH_ZOH` |
| A and fresh ZOH unavailable | false | false | `SENSOR_FIXED` | `SENSOR_FIXED` |

`candidate_used` implies `candidate_attempted`. Every A-valid event is an
attempt. `candidate_failure_reason` is non-null exactly for an attempted
candidate failure; `nonattempt_reason` is non-null exactly when no attempt was
allowed; `fallback_reason` is null only for `CANDIDATE` and otherwise is the
exact independently reconstructed baseline reason. The four route counts
partition all event rows; attempts equal candidate uses plus attempted
`CURRENT_CAV` rows. No event is deleted, retried, relabeled, or removed from a
denominator.

Every reason is a member of the closed reason registry inside the canonical
candidate/baseline configuration; arbitrary free text is forbidden. Digests
are lowercase 64-hex strings, flags are exact booleans, identifiers and state
versions are nonnegative integers, cycle/edge fields are signed int64, and
quaternion/ray arrays contain finite unit-norm values of lengths four/three.
Only the null cases explicitly specified here are permitted.

For `CANDIDATE`, `CURRENT_CAV`, and `FRESH_ZOH`, the quaternion and world ray
are present and the verifier independently rotates the frozen sensor ray. For
`SENSOR_FIXED`, both are null and the evaluator uses the exact sensor-fixed
authority. Candidate geometry is permitted only when frozen current CAV is
valid. The runner, not the candidate, owns IDs and `is_query`; those fields are
never part of the candidate-visible projection.

### 3.4 Feedback row

`redred.mc_wtb_predictor_stage3.feedback_evidence/v2` contains exactly:

```text
feedback_id, candidate_id, reset_generation,
forecast_state_version, forecast_state_sha256,
forecast_generation_edge, target_measurement_timestamp_ns,
forecast_quaternion_xyzw, forecast_sha256,
pose_id, pose_sha256, pose_commit_edge,
prior_state_version, prior_state_sha256,
next_state_version, next_state_sha256,
publication_edge, status, reason, feedback_sha256
```

The forecast is immutable and exists before the target pose is revealed.
`publication_edge == pose_commit_edge + 1`; the next state may affect only
decisions with `decision_edge >= publication_edge`. Invalid, duplicate, stale,
out-of-generation, or mismatched feedback cannot update state and records an
exact fail-closed status/reason. Equal-timestamp clusters share one pre-cluster
state and publish only after the complete cluster.

### 3.5 Mutation row

Each append-only
`redred.mc_wtb_predictor_stage3.mutation_evidence/v2` row contains exactly:

```text
mutation_id, candidate_id, authority_source_manifest_sha256,
mutant_source_manifest_sha256, expected_disposition,
observed_disposition, verifier_reason, killed, mutation_receipt_sha256
```

`killed` is true only when the independent verifier returns the exact frozen
fail-closed disposition. A crash, empty output, timeout, or unclassified error
does not kill a mutant.

### 3.6 Execution and scoring receipts

The execution envelope
`redred.mc_wtb_predictor_stage3.execution_receipt/v2` contains exactly:

```text
schema, authority, candidate_id, candidate_config_sha256,
source_manifest_sha256, neutral_input_sha256,
ordered_query_event_ids_sha256, window_rows,
canonical_run_sha256, replay_run_sha256, deterministic_match,
independent_verifier_manifest_sha256, verifier_receipt_sha256,
decision_stream_sha256, execution_receipt_sha256
```

The scorer accepts this receipt only when `deterministic_match` is exactly
true, both fresh-process runs are byte-identical after canonical serialization,
the independent verifier has accepted every decision/state/feedback row, and
the ordered `Q` digest matches the neutral authority. A self-sealed external
candidate-output file is never scoreable evidence.

The scoring envelope
`redred.mc_wtb_predictor_stage3.scoring_receipt/v2` contains exactly:

```text
schema, execution_receipt_sha256, evaluator_manifest_sha256,
metric_spec_sha256, label_sidecar_sha256,
ordered_query_event_ids_sha256, query_event_count,
groups_sha256, causal_status, model_status,
cncp_manifest_sha256, cncp_status, ppa_status,
promotion_authorized, rtl_ppa_authorized, scoring_receipt_sha256
```

`cncp_manifest_sha256` is null when no sealed CNCP manifest exists; that case
requires a CNCP HOLD. No naked CNCP number is present in this envelope.

## 4. Mandatory execution order and label isolation

The v2 runner performs this order without an override:

1. verify the frozen authorities, clean tree, dependency-closed manifest,
   native candidate ID, canonical config, selector query authority, source
   lock, neutral-input seal, and schemas; reconstruct and seal the common 50 ms
   pre-roll without changing the frozen query population or label lineage;
2. create a fresh process, independently reset each window at its reconstructed
   50 ms pre-roll start, and execute the actual native candidate through the
   identity-free adapter; overlapping source occurrences remain namespaced by
   window/reset generation and no pre-roll row is scoreable;
3. repeat the complete execution in another fresh process, visiting windows in
   reverse registry order while preserving within-window order, restore the
   canonical registry order, and require exact decision/state/feedback bytes;
4. run the separately manifested verifier to reconstruct signed edges, pose
   eligibility, attempt/route partition, fallback, state/feedback ancestry,
   candidate quaternion-to-ray projection, exact-once order, and all seals;
5. atomically seal the v2 execution receipt; only then start the scoring phase;
6. in a separate scoring process, verify the receipt again, reconstruct `S`
   and `A`, score the unchanged ordered `Q`, and only after that join frozen
   BODY X/Y/Z and LOW/MID/HIGH sidecars for grouped reporting.

The execution process has no readable handle, import path, argument, object,
environment channel, or dependency edge to selector labels, axis/sign/motion
labels, scorer truth/loss, evaluator state, source role/rank, or prior outcomes.
The candidate-visible schema is a positive allowlist of physical event fields,
relative causal time, visible pose values/deltas, and declared validity or
transport flags; it excludes all IDs, absolute query start, labels, scores,
digests, roles, ranks, and split membership. Forbidden-field injection is
rejected even when it would not change the output.

### 4.1 Conditional epoch2 remediation

`epoch2` is not a candidate retry. It is allowed only after a common
infrastructure failure stopped the preceding epoch before candidate scoring and
before any candidate output, loss, group result, rank, plot, or other outcome
was visible. A common mismatch between the selector's 1 ms diagnostic bundle
and Stage 3's required 50 ms reconstructed input qualifies; a
candidate-specific verifier failure or an unfavorable result does not.

An append-only remediation checkpoint must precede `epoch2` and bind the
predecessor epoch and commits, failed artifact hashes, source lock, exact
failure disposition, remediation bytes, replacement neutral-input/adapter
hashes, unchanged query-ID/order/bounds and label digests, and a no-outcome-
visibility attestation. It must also attest that candidates and configs,
scorer, metrics, thresholds, gates, and label isolation are unchanged and that
the remediation is applied identically to all candidates. If any outcome was
available or the attestation is incomplete, scoring remains HOLD and this
exception cannot authorize `epoch2`.

## 5. Model evidence and CNCP are separate

The scoring result carries four independent statuses:

```text
causal_status = CAUSAL_PASS | CAUSAL_HOLD | CAUSAL_FAIL
model_status  = MODEL_PASS  | MODEL_HOLD  | MODEL_FAIL
cncp_status   = CNCP_PASS_BOUNDED | CNCP_HOLD_* | CNCP_FAIL_CEILING
ppa_status    = NOT_EVALUATED
```

There is no combined accuracy-and-cost pass bit. Model scoring consumes no
CNCP values; it may carry only a separately sealed CNCP manifest digest and
verdict. Missing or `DECLARED_UNVERIFIED` CNCP is HOLD, never model failure and
never feasibility evidence. CNCP cannot alter `Q`, metrics, model rank, or the
native winner. Model accuracy cannot satisfy CNCP, timing, RTL, or PPA gates.
`promotion_authorized` and `rtl_ppa_authorized` remain false in Stage 3.

Positive-window semantics are exactly `effect > 0.0`; no epsilon is applied.
Pooled ratio-of-sums and equal-window mean-of-ratios remain separately named,
fallback rows remain in every denominator, and zero/nonfinite denominators are
protocol failures.

## 6. P0/P1 closure evidence

The identifiers below refer to
`MC_WTB_STAGE3_IMPLEMENTATION_REDTEAM_20260822.md`. A row is not closed by the
document itself; the listed evidence must be emitted by the implemented v2
pipeline and accepted by the independent verifier before any 108 scoring.

| Finding | Required v2 closure evidence |
|---|---|
| P0-1 disconnected candidate/framework/oracle | One runner receipt per native candidate proving that the same manifested binary traversed the identity-free adapter, actual candidate, independent verifier, mutation gate, and score-input producer. |
| P0-2 unattested supplied geometry | Two runner-executed fresh-process hashes, native quaternion, independently recomputed world ray, complete state/input provenance, and verifier receipt; external supplied rows are rejected. |
| P0-3 occurrence/decision confusion | Signed event rows satisfying the frozen ceiling conversion and `occurrence_cycle == decision_edge - 1`, plus boundary, same-edge, dequeue/retirement, and rounding mutation receipts. |
| P0-4 reset/state/feedback ambiguity | Window reset-generation and initial/query-start state rows, full state ancestry, feedback rows, cluster atomicity proof, window-permutation replay, cross-window-carry mutant rejection, and proof that overlapping reconstructed pre-roll occurrences stay in independent generations. |
| P0-5 incompatible/aliased IDs | Byte-identical native candidate ID in configuration, source manifest, every candidate-use row, execution/verifier/mutation receipts, schema, and result. |
| P0-6 self-referential source/config binding | Independently pinned authorities, clean-tree receipt, canonical config, dependency-closed phased source manifest, runtime hash, and proof that those exact bytes were executed. |
| P0-7 collapsed fallback semantics | Exhaustive attempted/use/route partition, exact model ID and reason for each route, exact used poses/quaternion/ray, independent CAV/ZOH/sensor reconstruction, and fallback-route mutation rejection. |
| P1-1 identity-bearing native input | Hash-bound positive-allowlist projection plus forbidden-ID/label/digest injection mutations showing the candidate never receives wrapper identity. |
| P1-2 signed pre-roll disagreement | Signed-int64 schema and known answers for negative cycles, reset edge, signed/unsigned boundaries, pre-reset baseline-versus-candidate-state treatment, and explicit rejection of copying the selector's 1 ms diagnostic start into the Stage 3 50 ms boundary. |
| P1-3 positive-window epsilon drift | Metric receipt proving positive counts use strict `effect > 0.0`, including zero and sub-`1e-6` positive known answers. |
| P1-4 asserted CNCP as feasibility | Independent model and CNCP statuses; model receipt contains only a sealed CNCP digest/verdict, while unverified numeric declarations force CNCP HOLD and cannot change model output or rank. |
| P1-5 missing actual-candidate mutants | Append-only mutation receipt for every mandatory ORC/TIME/EDGE/TUNE/DEN/FILT/FB/RG3/DSPB/PLL/FALL/ROB/REPLAY case, naming expected/observed disposition and verifier reason; every mutant is killed through the same manifested runner path. |
| P1-6 missing query-start diagnostics | Every window row reports its source-reconstructed 50 ms reset, initial and query-start state/version/hash, validity/lock, pre-roll attempt/use/route counts, overlap-safe generation binding, and no-pre-roll-scoring proof, plus separate sealed non-ranking 25/100 ms sensitivity receipts. |

## 7. Gate

Before scoring any 108 event, every required field must be present, both
runner executions and the independent verifier must agree byte-for-byte, every
mandatory mutation must be killed, ordered `Q` must be exact, and label
isolation must be proven. Any unknown field, missing dependency, alias ID,
edge mismatch, surviving mutation, nondeterminism, state ancestry gap,
fallback ambiguity, external candidate output, pre-seal label access,
1 ms/50 ms boundary substitution, global de-duplication of overlapping
pre-roll, any pre-roll scoring, or an epoch2 without complete pre-score
lineage and no-outcome proof is `STAGE3_EVIDENCE_V2_FAIL`; scoring must not
start.

`STAGE3_EVIDENCE_V2_PASS` authorizes only the already frozen development
screen. It does not authorize retuning, full-stream replay, external sequence
access, generalization claims, implementation feasibility, RTL, or PPA.
