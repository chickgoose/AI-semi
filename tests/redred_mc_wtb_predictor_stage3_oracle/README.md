# Stage-3 independent predictor oracle

This directory is a standard-library-only, synthetic correctness and causality
oracle.  It does not import a candidate implementation, Stage-4 selection or
scoring code, dataset files, or any real-data artifact.  It deliberately does
not test event-quality filtering, RTL, synthesis, timing, power, or area.

Run it from any directory with:

```sh
tests/redred_mc_wtb_predictor_stage3_oracle/run_all.sh
```

## Candidate adapter boundary

`protocol.py` defines the candidate-facing protocol without changing any
candidate file.  A future test wrapper may implement `CandidateAdapter` and
pass it to `OracleHarness.run()`.

The candidate sees only:

- one physical `PredictorEvent` at a time (no oracle event identity, role,
  window membership, scenario label, score, or future record);
- poses with `commit_cycle < decision_cycle` and pose measurement time no later
  than the event time;
- one deepcopy-isolated state snapshot and its version/effective cycle; and
- the frozen CAV -> fresh-ZOH -> RAW fallback result.

For each valid pose commit, the harness evaluates `forecast_pose()` from the
immutable pre-pose state.  It binds that forecast state version, generation
cycle, target pose timestamp, pose commit cycle, and publication effective
cycle in `FeedbackAudit`.  Only `accept_pose()` may create a new state, and that
state is unavailable until `pose.commit_cycle + 1`.  Invalid poses never update
state.  Events on the pose commit edge are sealed first, so feedback cannot
rewrite the past.

Candidate exceptions classified as numeric failures fail open by copying the
reference fallback output exactly.  A candidate pose citation must be a valid
member of the causal view, its state-version citation must match the supplied
snapshot, and its quaternion must be finite and unit length.  Input-state
mutation is rejected.

## Oracle coverage

The synthetic suite covers:

- stationary, constant-rate, constant-angular-acceleration, stop, and reversal
  motion;
- unequal pose/event cadence;
- delayed, dropped, and invalid poses;
- exclusion of a pose committed on the event decision edge;
- equal-timestamp cluster atomicity;
- shortest-arc near-pi rejection;
- candidate numeric failure and exact RAW/ZOH/CAV fallback equivalence;
- event identity, order, cardinality, exact-once output, sealed digest, and
  immutable snapshot checks; and
- Python 3.8 grammar parsing of every Python source in this directory.

The tests use analytic SO(3) trajectories and an independently implemented
quaternion/CAV reference.  Stop, reversal, and acceleration deliberately show
nonzero causal CAV error: a correct predictor is not granted future knowledge.
