# MC-WTB Stage-4 contract and decision receipts

This package is the score-blind foundation for the frozen Stage-4 pose-recovery
comparison. It performs four jobs only:

1. strictly loads the exact frozen comparison contract, rejecting duplicate
   JSON keys, non-finite numbers, wrong types, changed values, or extra fields;
2. validates the existing 24-window development registry, its canonical hash,
   ordering, interval shape, and nonoverlap with the forbidden interval;
3. defines deterministic per-event decision records and canonical JSON hashes;
4. fails closed unless every expected event has one retirement record in the
   original order with exact accepted/retired conservation.

It does not load arm outputs, compute losses, score an arm, select a winner, or
authorize holdout, RTL/PPA, or novelty claims.

## Canonical encoding

Canonical JSON uses sorted object keys, compact separators, ASCII escaping, no
NaN/Infinity, and exactly one final newline. Arrays remain order-sensitive.
The registry encoding therefore matches the existing frozen registry hash.

Decision records contain only:

- window and event identity;
- frozen arm name;
- occurrence and retirement cycles;
- pose IDs and timestamps available to the decision;
- pose age, disposition/reason, and queue cycles.

Any mapping field whose name contains `score` or `loss` is rejected before the
record schema is interpreted. No extension field is accepted.

`available_pose_ids` and `available_pose_timestamps_ns` are ordered oldest to
newest and must have equal lengths. `pose_age_ns` is signed because the frozen
`delayed_exact` diagnostic may bind a pose strictly after event occurrence.
`queue_cycles` is non-negative and cannot exceed occurrence-to-retirement
latency. Multiple records may retire on one cycle, but retirement cycles and
event order may never move backwards.

Use `load_comparison_contract()` followed by `validate_existing_registry()`
before generating decisions. Then call `validate_decision_records()` once per
window and arm before any separate scoring process sees the receipt hash.
