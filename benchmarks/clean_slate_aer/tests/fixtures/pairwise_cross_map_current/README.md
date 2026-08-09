# Current A1 pairwise-report integration fixture

These four files exercise the complete/isolated form of the report emitted by
the current A1 `pairwise_contention_metrics.py`, including measurement state,
capacity counts, overlap provenance, worst-pair records, canonical aggregates,
and per-trial result fields. The audited producer SHA-256 was
`d3fef3ab758f0d98cace32626d147e681bbb69262472330401cc00a987aa81db` and the
generator SHA-256 was
`59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50`.

Adversarial partial, combined drop-and-censor, no-evaluable, and overlap cases
are derived from the same schema in `test_pairwise_cross_map_compare.py`.
