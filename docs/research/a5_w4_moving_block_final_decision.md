# A5 W4 Final Moving-Block Statistical Adoption Decision

Decision: **REJECT_AS_DEFAULT_REPLACEMENT**

This decision does not invalidate A4 functional correctness or its local
STYLE2 optimization. It rejects the moving two-step semantics as the default
replacement for the fixed one-step reference under an unpriced Pareto gate.
An application that explicitly prices mean/fill latency above tail latency and
logic cost would need to declare a different gate before rerunning the study.

## Pinned evidence

The calculation reads named Git objects rather than foreign worktrees or
current `HEAD`:

- A5 matched-cohort audit from `d4818c5`, result SHA
  `1be66e390590593bb63afc41dc7964f8e417ca1851cad37c37a4d27bb7c1674f`;
- A4 `41f239dad4a342277f33d94bb3ed3db53e3497e0`, local summary SHA
  `b3124911730c9d634a3708d3bda3ea96833f2468538d627bbc90a6babca4bf1a`
  and functional follow-up SHA
  `40d81275ebee63380508d12dad240836f0e5ef84ae6c7f83a7ef6b601f41fbd4`;
- A4 predeclared gate `0d024152be37846a4fae73c65bcc2cfa73393844`, SHA
  `f123ab43e2e203b7a4eb9a0e8612b5d2f9dcd14890718697bca6b319f51b7618`;
- A3 `2696aef01b1df455e19a84cae800719941d2df66`, true six-way
  selected/MAX1/MAX2 same-flow receipt SHA
  `77ebf3cea5abe0edf13619c01c2081786166e9237da4391fe221744e1577f550`;
- A9 `3450ddf09a590e7e66d9f35dff91efad831dfa87`, audited read-only below.

The machine-readable gate is
[`a5_w4_moving_block_final_gate.json`](results/a5_w4_moving_block_final_gate.json).
Its byte receipt is
[`a5_w4_moving_block_final_gate.receipt.json`](results/a5_w4_moving_block_final_gate.receipt.json);
the result SHA is
`337b44a6db3cc8c4a3a3cc7c796beb23141b7339848b520e9afcffb047f5017b`.

## Gate contract

The default replacement must satisfy all of these without a workload-specific
utility weight:

1. exact functional equivalence, conservation, order, and drain;
2. statistically detected generated-event capacity direction;
3. no p95, p99, or maximum regression on the occurrence-ID matched cohort;
4. measured throughput ratio at least equal to selected/MAX1 same-flow
   total-cell cost ratio,
   so throughput per mapped cell does not fall;
5. common qualification and physical evidence complete before adoption.

This is deliberately a break-even gate, not an arbitrary minimum percentage.
The matched-tail rule prevents a large survivor swap from purchasing an
unpriced tail regression. Physical/common incompleteness would yield HOLD if
the utility gates passed; here the tail and efficiency gates already fail, so
the statistical decision is REJECT rather than merely waiting for PPA.

## Functional and statistical gates

A4 `41f239d` closes the local stalled-root, midstream-reset, no-reset recovery,
and N64 equivalence checks. All four RTL variants match for 2,982 cycles;
conservation, source order, and drain pass. Functional correctness therefore
passes and is not the rejection reason.

| suite | net accepted, 95% CI | discordant accepted IDs | net/churn | loss reduction | accepted Jaccard |
| --- | ---: | ---: | ---: | ---: | ---: |
| full50 | +41, [23, 61] | 11,023 | 0.372% | 0.179% | 0.87621 |
| capacity22 | +35, [21, 49] | 10,841 | 0.323% | 0.154% | 0.77595 |

The direction is statistically stable, but `+41/+35` is the small net residue
after roughly 11k IDs change membership. Moving is not the fixed accepted set
plus 41 or 35 events.

| suite | matched mean fixed→moving | matched p95 | matched p99 | matched max | tail gate |
| --- | ---: | ---: | ---: | ---: | --- |
| full50 | 13.455→12.370 | 42→43 | **46→46** | 46→47 | FAIL |
| capacity22 | 20.799→20.315 | 45→45 | **46→47** | 46→47 | FAIL |

Mean latency improves, but there is no Pareto tail win. Full50 raw p99 +1 is a
survivor-composition effect because matched p99 remains 46. Capacity22 retains
the +1 inside the matched cohort, so the tail regression cannot be dismissed as
survivorship alone.

## True selected/MAX1 same-flow cost and break-even

A3 `2696aef` maps MAX1, frozen MAX2, and selected STYLE2 at N16/N64 with the
same boundary, tool, canonical top, pass sequence, and generic cell
interpretation. Only selected STYLE2 versus MAX1 is the formal adoption-cost
input. The older `d1e979e` MAX1/MAX2 result is excluded from the decision and
retained only as an external historical diagnostic.

| N | cells MAX1→selected | comb | depth | nets/bits | data max fanout / >=16 nets | sink-pin wire |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 6,467→7,469 (+15.49%) | 5,305→6,307 (+18.89%) | **13→23** | 4,107→5,080 / 6,998→8,001 | 42→39 / 91→137 | 11,607→16,081 |
| 64 | 29,830→32,620 (+9.35%) | 24,814→27,604 (+11.24%) | **18→31** | 19,712→22,377 / 31,945→34,736 | 55→66 / 398→566 | 53,674→70,060 |

| suite | measured throughput gain | N16 break-even gain | N16 throughput/cell | N64 break-even gain | N64 throughput/cell |
| --- | ---: | ---: | ---: | ---: | ---: |
| full50 | +0.1077% | +15.49% | 0.867× | +9.35% | 0.915× |
| capacity22 | +0.1110% | +15.49% | 0.867× | +9.35% | 0.915× |

Moving preserves state bits but does not recover its combinational and wire
cost. Its throughput gain is roughly 84--144 times smaller than the total-cell
break-even gain, and throughput per cell falls by about 8.5--13.3%.

### Predeclared A4 `0d02415` gate

The A4 gate was frozen before A3's result. It permits at most two added logic
levels and 10% depth premium at each size, with no cross-metric compensation.
The true receipt reports +10 levels/76.92% at N16 and +13 levels/72.22% at N64,
so depth alone makes the local result **NO-GO**. Other failures include N16
total cells (15.49% versus a 15% ceiling), high-fanout-net counts at both sizes,
and N64 maximum fanout. Conservative DFFE-as-DFF-plus-mux accounting also
exceeds its total/comb ceilings. No cross-flow synthesis count is used to reach
this decision.

## Read-only audit of A9 `3450ddf`

Verdict: **PARTIAL_CAVEAT_INADEQUATE_FOR_MATCHED_COHORT_CLAIM**.

The raw A9 count and aggregate-latency values remain valid descriptive results,
and its tournament correctly stays HOLD. The citation does not accurately carry
the full matched-cohort qualification:

- `REPORT.md:86` says “admits 41 additional events.” This implies a superset,
  whereas the result is net +41 after 5,491 fixed-only and 5,532 moving-only
  events.
- `REPORT.md:88` does acknowledge that survivor sets differ, so the report is
  not wholly unaware of the issue. It gives neither 11,023 churn nor matched
  p99 46→46.
- `REPORT.md:103` similarly calls net +35 “additional”; the exact result is
  5,403 fixed-only versus 5,438 moving-only, with matched p99 46→47.
- `W4_A9_SUMMARY.md:7-9` repeats both count deltas and raw tail regressions but
  omits the survivor and matched-cohort caveat entirely.
- `w4_tournament.py:151-165` stores no accepted-ID set in `CoreRun`;
  lines 243-245 pop and discard the scoreboard ID, and lines 338-367 aggregate
  only counts/latencies. Thus commit `3450ddf` cannot independently calculate
  churn or matched latency from its published result.

Correct replacement wording is:

- full50: **net +41 after 11,023 discordant IDs; matched p99 46→46**;
- capacity22: **net +35 after 10,841 discordant IDs; matched p99 46→47**.

## Final decision

| gate | result |
| --- | --- |
| Exact functional equivalence | PASS |
| Positive capacity direction in both suites | PASS |
| Matched-tail non-regression | **FAIL** |
| True selected/MAX1 throughput/cell break-even | **FAIL** |
| A4 predeclared same-flow local-cost gate | **NO-GO** |
| Common qualification complete | HOLD/open |
| Physical PPA complete | HOLD/open |

The candidate is **REJECTED as the default fixed-one-step replacement**. The
small net capacity effect does not amortize the true same-flow logic/connectivity
cost, and cap22 has a real matched p99/max regression. No server PPA run is
needed to reach this pre-physical decision. A4 STYLE2 may remain a local
conditional implementation artifact, but it is not an adopted system
candidate under this gate.

## Reproduction

```bash
python3 tests/a5_w4_moving_block_audit/compute_final_adoption_gate.py \
  --output /tmp/a5-w4-final-gate.json \
  --receipt /tmp/a5-w4-final-gate.receipt.json
# Expected decision-specific exit: 4 (REJECT_AS_DEFAULT_REPLACEMENT)
python3 -m unittest \
  tests/a5_w4_moving_block_audit/test_analysis.py \
  tests/a5_w4_moving_block_audit/test_final_adoption_gate.py
```
