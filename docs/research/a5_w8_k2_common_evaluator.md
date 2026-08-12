# A5 W8 independent common digital K2 evaluator

Status: **fail-closed v2 package implemented and self-falsified; three owner
evidence bundles are not supplied, so no candidate comparison is published**.

The isolated implementation is under
`tests/a5_k2_common_evaluator/`. It adds no RTL and changes no candidate branch,
frozen common TB, manifest, or team design. The evaluator requires exactly
three independently identified N16/K2 evidence files, real regular-file paths
for source/binding/runner, and separate hashed run artifacts. It refuses
unattached hashes, inline observations, malformed output envelopes, and rebound
identity.

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

Before ranking, all candidates must have the same canonical SHA-256 of their
exact policy-class, indexed-edge, and latency-definition contract. Different
fingerprints produce `INCOMPARABLE`, a null frontier, and no Pareto claim. After
same-contract hard gates, comparison is tolerance-aware unweighted Pareto. Fixed
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
exercise a clearly labelled synthetic reference only to qualify the checker.
Three committed fixture-owner triplets provide real regular files, while each
run is materialized as a separately SHA-256-bound artifact. The mutation gate
kills seven semantic attacks plus unattached-hash, fabricated-output, and
rebound-binding attacks, and retains the future-arrival divergence witness.

No non-fixture owner source, binding, runner, or transaction evidence is
registered. Consequently there is no candidate PASS, ranking, or claimed
performance result.
