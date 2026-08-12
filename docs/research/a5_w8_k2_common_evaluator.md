# A5 W8 independent common digital K2 evaluator

Status: **package implemented and self-falsified; three owner evidence bundles
not yet supplied, therefore candidate comparison is HOLD**.

The isolated implementation is under
`tests/a5_k2_common_evaluator/`. It adds no RTL and changes no candidate branch,
frozen common TB, manifest, or team design. The evaluator requires exactly
three independently identified N16/K2 evidence files and refuses to manufacture
a result when they are absent.

## Frozen decision boundary

The authoritative scalar policy is committed-event weighted row service with
wheel `[0,1,1,1,1,1,2,2,2,2,2,3]` and round-robin columns. A legal two-event
acceptance is the first two recursive scalar selections from the same pre-edge
pending set. Adaptive width may shorten this prefix but cannot replace either
winner. The resulting grade is distinct from the aggregate row-share test:

- aggregate `[1,5,5,1]` checks the first 120 committed-event rows;
- scalar-prefix grade checks every ordered acceptance vector; and
- full future-trace equivalence is explicitly rejected because early clearing
  changes later arbitration and source-overrun behavior.

The transaction boundary retains TB-only event identities so stale source
generations, duplication, accepted-set churn, and matched-event latency can be
measured without adding IDs to candidate RTL.

## Frozen gates and comparison rule

Hard gates cover conservation/drain, exact accepted-retired order, distinct
winners, full prefix equivalence, persistent `10:50:50:10`, sparse maximum
occurrence-to-accept of one cycle, stable ordered lane stalls, and reset phantom
exclusion. Latency is split into occurrence-to-accept and accept-to-retire.

After all hard gates, comparison is tolerance-aware unweighted Pareto. Fixed
window event/cycle uses a 0.005 absolute or 1% relative tie band. Both p99
latencies use a one-cycle or 5% band. Overrun ratio uses 0.005 absolute. Latency
is compared on the all-three common accepted-event cohort; left-only/right-only events
and symmetric difference remain visible. The reported frontier excludes an
eligible candidate only when it is pairwise dominated across all available
per-run dimensions. No single weighted score is produced.

## Evidence status

The deterministic adversarial generator has seven SHA-locked runs. The exact
generator-v4 adapter binds the committed 50/22 manifest identities and all
per-trace hashes; capacity22 remains an exact full50 subset view. Local tests
exercise a clearly labelled synthetic reference only to qualify the checker and
kill seven semantic mutations plus the future-arrival divergence witness.

No owner source SHA, binding, runner, or transaction evidence is presently
registered in this package. Consequently there is no candidate PASS, ranking,
or claimed performance result.
