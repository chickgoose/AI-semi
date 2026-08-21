# MC-WTB Stage-4 frame-safe scoring

This pure-Python package implements only the frozen Stage-4 offline loss join,
metrics, and disposition rules. It does not generate decisions, transform
poses, read UZH data, score a holdout, or authorize RTL/PPA claims.

## Score-after-receipt boundary

`score_window` accepts frozen `DecisionRecord` objects and a validated
`DecisionReceipt`. Before inspecting a ray or computing a loss, it verifies:

- the caller-supplied canonical receipt SHA-256;
- the receipt's contract and registry digests;
- the canonical digest of the exact immutable decision records;
- a canonical digest of `ScoreFreeAccounting`.

The accounting object classifies attempted corrections, freshness vetoes,
invalid-pose bypasses, operational waste, baseline retirement cycles, and
modeled costs without referring to a loss. Raw classifications are disjoint
and exhaustive. This avoids inventing a reason-string taxonomy after scores
are visible.

## Frame and density safety

Every `RayEvent` contains a sensor-frame ray and exactly one deterministic
world-shadow ray for every frozen arm. One causal bank is built solely from all
sensor rays and a separate bank solely from all selected-arm world shadows.
Both banks:

- are separated by polarity;
- score a complete equal-timestamp cluster before inserting any member;
- receive every warm-up and query event regardless of runtime enable/bypass;
- use the same capacity, age, and event ordering.

A bypassed event takes its sensor loss, while an enabled event takes its world
loss. The bypassed event's world shadow still enters the world bank, preventing
the gate from changing later reference density. Missing query loss in either
frame fails closed.

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
