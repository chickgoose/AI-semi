# Independent A5/A8 cross-validation adapters

These adapters translate the candidate's already-qualified atomic scheduler
semantics; neither changes policy or RTL.

`a8_owner_adapter.py` exposes the contract-level atomic transaction view used
by A8 commit `1248a19`.  It produces `grant_count`, ordered addresses, atomic
commit, held offer, and immutable snapshot proof.  It uses the candidate's
independent scalar-fold oracle.  Candidate RTL is separately locked to that
oracle by `run.py`; the A8 adapter is not itself direct RTL simulation.

`a5_trace_exporter.py` exposes the candidate's transaction-level atomic policy
fold and adds an ordered two-entry post-scheduler link solely to express A5
commit `41c425b`'s per-lane retire observations.  It intentionally normalizes
away the candidate output register because A5's reset vector requires a final
idle observation two cycles after a sentinel occurrence.  The link may drain
independently; it never partially commits a scheduler offer or advances
scheduler policy.

A5 freezes a different scalar oracle: a 12-entry row wheel with four row-local
column pointers.  This candidate implements the pinned Ganghee Fovea state:
center/peripheral `arbiter4_tree`, one shared column `arbiter4_tree`, and the
six-state center/peripheral round.  The exporter therefore reports candidate
behavior honestly and is expected to receive A5 prefix mismatches; it must not
rewrite winners to make that evaluator pass.

`compare_a8_oracle.py` loads a materialized A8 oracle under a private module
name and exhaustively compares both ordered grants and complete post-prefix
policy state for all 65,536 initial request masks.
