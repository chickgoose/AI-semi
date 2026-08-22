# MC-WTB Stage 3 prewarm incident/remediation checkpoint — 2026-08-22

Status: **HOLD; documentation checkpoint only; no execution, scoring,
candidate retry, promotion, RTL, or PPA authority**

## Incident boundary

The frozen selector registry, SHA-256
`4d022cfde62c609c19c275add2e374d656babde3d4e1e6e1a849c5f384bb7e0d`,
contains a 1 ms diagnostic warmup around the already-frozen NEW108 queries.
Stage 3 candidate contracts require an independently reset 50 ms causal
pre-roll. Treating the selector registry's 1 ms rows as if they were the
Stage 3 50 ms input is a common pre-score infrastructure mismatch, not a
candidate result.

The remediation boundary is exact:

| Frozen selector authority | Stage 3 reconstructed input |
|---|---|
| exact ordered 108 queries, bounds, query IDs, and label lineage | exact same queries and labels, with `[query_start - 50 ms, query_start)` reconstructed from the locked source |
| 1 ms diagnostic warmup remains immutable lineage evidence | fresh candidate state reset independently at the 50 ms start |
| query event IDs are globally exact-once | overlapping pre-roll may reuse a source event or pose under a distinct `window_id` and `reset_generation` |
| diagnostic warmup is not rescored | no reconstructed pre-roll record enters `Q`, receives a query label, or contributes to a score or denominator |

The locked-source authority is SHA-256
`0e0dbb17db4d170de650729fe9ad1cd3f18d20c1bddcd577c84999fcde045a4c`.
Source reconstruction may change only the common initialization input and its
derived seals. It may not change query IDs, query order or bounds, labels,
candidates, configs, scorer, metrics, thresholds, gates, or fallback rules.

## Conditional epoch2 rule

An `epoch2` run is authorized only after an append-only receipt proves all of
the following:

- the prior epoch failed on this common infrastructure seam before scoring;
- no candidate output, loss, group statistic, rank, plot, score, or other
  outcome was computed, read, logged, or used;
- predecessor commits and artifact hashes, the source lock, ordered query-ID
  digest, and label-sidecar digest are recorded;
- the exact failure and remediation diff are recorded, with replacement
  neutral-input, adapter, and verification hashes;
- the unchanged candidates/configs and unchanged scoring policy are recorded;
- every candidate restarts through the same remediated infrastructure.

A candidate-specific failure or any visible outcome does not qualify. If the
no-outcome claim cannot be proved, `epoch2` is forbidden under this exception
and a new explicitly authorized development freeze is required.

## Remediation checkpoint

Before execution, independent verification must reject a copied 1 ms reset,
missing 50 ms support, pre-roll scoring, query substitution, cross-window
candidate-state carry, or global de-duplication of overlapping pre-roll. It
must accept overlap only when each occurrence is bound to its own window/reset
generation and the ordered query population remains byte-exact.

This document does not rewrite the original Checkpoint A hashes or itself
attest that no outcome was visible. Execution and scoring remain HOLD until a
committed lineage receipt binds the original authorities, this remediation
documentation, the reconstructed artifacts, and the required no-outcome
attestation.
