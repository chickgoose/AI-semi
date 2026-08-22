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
