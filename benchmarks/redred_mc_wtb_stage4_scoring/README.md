# MC-WTB Stage-4 frame-safe scoring

This pure-Python package implements only the frozen Stage-4 offline loss join,
metrics, and disposition rules. It does not generate decisions, transform
poses, read UZH data, score a holdout, or authorize RTL/PPA claims.

## Score-after-receipt boundary

`score_window` accepts frozen `DecisionRecord` objects and a validated
`DecisionReceipt`. It also requires an immutable `ScoreInputManifest` and the
manifest digest frozen by the caller before scoring. Before inspecting a ray
or computing a loss, it verifies:

- the caller-supplied canonical score-input manifest SHA-256;
- the caller-supplied canonical receipt SHA-256;
- the receipt's contract and registry digests;
- the canonical digest of the exact immutable decision records;
- a canonical digest of `ScoreFreeAccounting`.

Manifest schema v2 additionally binds the authoritative assay input manifest,
the complete warmup-plus-query cycle result, the complete cycle-receipt stream,
and the query projection. The independently supplied `ScoreBoundaryEvidence`
must match all four values, and the query projection must equal the decision
receipt's record digest. Consequently, retaining the same query receipt while
substituting warmup inputs or cycle history changes a required pre-score hash.

The manifest binds the decision-receipt digest, accounting digest, exact
ray/provenance stream, and the protocol, registry, arm-parameter, generator,
cycle-model, scorer, source, and runtime artifact classes. A changed ray or
shadow provenance after the manifest is frozen is rejected before either
causal bank runs.

Pose provenance commit cycles are signed relative-window cycles. Negative
cycles are allowed for pre-window history; causal visibility comparisons remain
strict and reject same-edge or later commits.

The accounting object classifies attempted corrections, freshness vetoes,
invalid-pose bypasses, operational waste, baseline retirement cycles, and
modeled costs without referring to a loss. Raw classifications are disjoint
and exhaustive. This avoids inventing a reason-string taxonomy after scores
are visible.

## Frame and density safety

Every `RayEvent` contains a sensor-frame ray and exactly one deterministic
`ShadowRay` for every frozen arm. Each shadow carries its transform kind and
pose IDs, measurement timestamps, commit cycles, and hashes. One causal bank
is built solely from all sensor rays and a separate bank solely from all
selected-arm world shadows.
Both banks:

- are separated by polarity;
- score a complete equal-timestamp cluster before inserting any member;
- receive every warm-up and query event regardless of runtime enable/bypass;
- use the same capacity, age, and event ordering.

A bypassed event takes its sensor loss, while an enabled event takes its world
loss. The bypassed event's world shadow still enters the world bank, preventing
the gate from changing later reference density. Missing query loss in either
frame fails closed; it never removes an event from the denominator.

For `zoh_freshness` and any runtime-bypassed `causal_cav` event, the scorer
requires the shadow provenance to be exactly the latest pose in the immutable
occurrence snapshot and the transform to be `occurrence_zoh`, without applying
the runtime age gate. An enabled CAV shadow is accepted only from the two
latest occurrence-snapshot poses and only inside the frozen horizon. Delayed
and oracle shadows are similarly bound to their declared bracket and
serialized packet prefix. A `delayed_exact` raw bypass may carry a later,
score-only `delayed_slerp` bracket that differs from the runtime `used_pose`:
it must contain exactly two pre-frozen authoritative rows, its left row must
equal the latest occurrence-snapshot pose, and it must satisfy
`left_timestamp <= event_timestamp < right_timestamp`, strictly increasing
pose IDs/timestamps, and distinct aligned pose hashes. Corrected delayed events
still require the shadow bracket to equal runtime `used_pose` exactly. Missing
or unordered bracket provenance is a protocol failure. These score-only
shadows cannot change the receipt decision and are inserted for every event
regardless of its gate outcome.

Loss totals use binary64 `math.fsum` in increasing event-ID order. Window
positivity is strictly `R_window > 1e-6`. Enabled world-loss ties count as
quality waste. Latency percentiles use nearest rank after sorting by
`(latency_cycles, event_id)`, and added latency is computed per event against
its always-bypass retirement cycle.

`aggregate_arm` requires the frozen 24 windows and 8,914-event denominator.
`delayed_exact` remains `DIAGNOSTIC_UPPER_BOUND`; the oracle-fed 1 kHz arm
remains `INTERFACE_VALUE_ONLY`; only causal arms can return
`GO_TO_EPOCH_INTEGRATION`. `validate_complete_comparison` requires all four
arms exactly once on the identical ordered event denominator.

Run the synthetic tests from the repository root:

```sh
python3 -m unittest discover -s tests/redred_mc_wtb_stage4_scoring -p 'test_*.py' -v
```
