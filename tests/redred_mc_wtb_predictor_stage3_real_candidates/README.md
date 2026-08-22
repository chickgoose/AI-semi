# Stage-3 real-candidate cross-integration tests

This directory independently drives the actual public APIs of:

- `recover_rg3_cav()` from RG3;
- `DSPBModel.commit_pose()` and `predict_event_cluster()`; and
- `SO3PLLModel.commit_pose()` and `predict()`.

No candidate module is wrapped by a replacement predictor.  `harness.py` only
projects one synthetic ledger into each native input type and normalizes the
resulting receipts for common invariants.  Candidate geometry remains owned by
the imported implementation.  Exact fallback checks call the frozen
`recover_causal_cav()` authority directly.

Run from any directory with:

```sh
tests/redred_mc_wtb_predictor_stage3_real_candidates/run_all.sh
```

The runner disables site-packages and bytecode writes.

## Adversarial scheduling

At a cycle containing both a pose commit and event decisions, the harness
intentionally calls the pose API first.  This is the stronger mutation: each
candidate must still exclude that pose because visibility is strictly
`commit_cycle < decision_cycle`.  Equal-timestamp events are then presented as
one DSPB cluster and repeated read-only calls to the RG3 and PLL APIs.  Every
member must consume identical state and geometry.

Changing a same-edge pose from 2 degrees to 40 degrees must leave the already
sealed cluster unchanged and may affect only the following edge.  Normalized
pose receipts bind the commit and effective cycles; DSPB and PLL additionally
bind source and published state versions.

## Scope and coverage

The shared analytic ledgers cover:

- strict same-edge pose exclusion and equal-timestamp atomicity;
- future-only supplied-pose feedback publication;
- exact CAV, fresh-ZOH, and sensor-fixed bypass fallback;
- constant-rate acquisition, stop, restart, reversal, long dropout, and
  near-pi discontinuity;
- deterministic replay digests;
- exact identity/order/cardinality and missing/duplicate/reorder/identity
  mutants; and
- public-signature, static-import, and fresh-import checks forbidding scorer,
  label, selector, and evaluator runtime inputs.

All trajectories are analytic SO(3) rotations generated in memory.  The tests
read no dataset and exercise no event-quality filter, scorer, RTL, PPA, or
external service.

## Production-output mutation gate

`test_production_mutation_gate.py` additionally invokes RG3, DSPB, and PLL
through `rg3_output.py`, `dspb_output.py`, and `pll_output.py`.  Its test-only
`ExactProductionGate` first invokes the candidate-specific exact native replay
verifier (a second deterministic production replay for RG3), then calls
`screen_projection.project_native_output()`, and finally submits only the
returned `ScreenProjection.screen_output` to the exact screen108-v2 validator.
The native evidence and candidate-neutral scoring receipt are deliberately
kept distinct.

Common-row mutations operate only on the projected screen receipt and reseal
every decision, window, and aggregate digest.  Candidate-specific state,
reset, transition, forecast, dependency, and configuration evidence remains
native and must be rejected by the candidate verifier or deterministic replay.
The gate accepts only classified candidate-output, screen-contract, or exact
replay rejections; an arbitrary producer or projection crash is a test error,
not a mutation kill.  Projected fallback taxonomy is exact: a current-CAV
candidate failure is normalized to `candidate_failure`, fresh ZOH to
`fresh_zoh_fallback`, and sensor-fixed bypass to the baseline
`no_occurrence_pose`, `invalid_pose`, or `stale_pose` reason.  Candidate-native
failure detail belongs only to the projection receipt.

The mutations cover noncommuting multi-axis RG3 transport removal,
occurrence/decision-cycle substitution, same-edge pose citation, cross-window
PLL state carry, PLL commit-time anchoring, DSPB hindcast and stale-winner
selection, unrelated unit rays, candidate/executable/config substitution,
fallback relabeling, event deletion/reordering/duplication, and retrospective
outcome-retry rewrite/append behavior.

This remains a synthetic, in-process mutation gate.  It uses no real dataset,
selector label, scorer, filter, RTL, or PPA path.
