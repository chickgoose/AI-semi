# A5 W4 Independent Audit of A4 Moving-Block Results

Status: **HOLD unchanged**

## 1. Scope and provenance

This is an independent statistical/fairness audit of A4 commit
`850fbcfa4ad168b1250223610780f11378f6c391`. It does not modify or qualify the
A4 RTL, common testbench, generator, manifests, or traces. The audit replays
the exact address-only generator-v4 occurrences and retains
`tb_only_event_id` only in the non-synthesizable observer. The fixed and moving
models see identical generated occurrences and an always-ready single retire
lane.

Repository arguments are object-database locators only. The audit never reads
the current A4/A1 checkout, index, branch, or `HEAD`. It resolves the explicit
commit objects and obtains only named blobs with
`git cat-file blob <commit>:<path>`. The A4 model, W3 replay tool, and frozen W3
result plus the A1 generator-v4, official policy, and two official manifests
are materialized under a fresh mode-`0700` temporary directory. Snapshot files
are created mode `0600` with exclusive/no-follow creation and fsync. Exact
trace bytes are then regenerated inside that snapshot and checked against all
official per-trace SHA values before analysis; the snapshot is removed on
exit. The traces were not committed as A4 blobs, so claiming otherwise would
be inaccurate.

The analysis fails closed unless all of the following match:

- A4 model SHA
  `fc0d57cbb66c94c1b903ce3e328f962b9ef5345400bab74dbd95fe657116a8bc`;
- A4 frozen summary SHA
  `b96ceb25f1b01b8bb8c6de3e0ede25cce97764928cf5b576d21cfed005093f39`;
- A4 frozen W3 replay-tool SHA
  `489710451649975b8abfec05e13ee10e7f38822fec3524c3fc189d9d5ecb8f86`;
- A1 common commit `47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be` and official-policy SHA
  `7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33`;
- exact official run names/order/cardinality, manifest SHA, and every trace SHA;
- the previously published offered, accepted, overrun, retired, cycles,
  bubbles, p95, and p99 aggregates for both models and both suites.

The fixed W4 integration replay succeeded while the A4 owner worktree was at
`8918829b777f4167dd6bb7d8c8195c5d1cf63610`, not `850fbcf`. A separate
two-commit repository mutation test moves `HEAD` after the pinned commit and
proves that snapshot materialization still returns the pinned blob. Neither
test permits the analysis path to query `HEAD`.

The machine-readable result is
[`a5_w4_moving_block_audit.json`](results/a5_w4_moving_block_audit.json). The
analysis source and unit tests are candidate-owned under
`tests/a5_w4_moving_block_audit/`.

## 2. Why the original aggregate is not a matched latency comparison

Under overload, a generated event occupies its source latch only if that latch
is empty. The fixed and moving trees clear leaves at different times, change
local RR phase at different times, and therefore accept different occurrence
IDs. Comparing latency across all events accepted by each model conditions on
two different survivor sets.

For each run this audit therefore defines:

- generated cohort: every exact trace occurrence;
- fixed/moving accepted sets: exact `tb_only_event_id` sets after complete
  drain;
- matched cohort: intersection of those two accepted sets within the same
  exact run;
- exclusive cohorts: fixed-only and moving-only accepted IDs.

Matching removes event-membership survivorship bias. It does not make the
surrounding queue history counterfactual-identical: other accepted events can
still change branch phase and interference. A matched result is consequently
stronger than the raw comparison but is not a proof of isolated per-event
causality.

## 3. Generated-event capacity and accepted-set churn

| suite | generated | fixed accepted/loss | moving accepted/loss | net accepted | acceptance-rate delta | loss reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full50 | 106,416 | 83,514 / 22,902 | 83,555 / 22,861 | **+41** | +0.0385 percentage points | 0.1790% |
| capacity22 | 65,616 | 42,948 / 22,668 | 42,983 / 22,633 | **+35** | +0.0533 percentage points | 0.1544% |

These are real generated-event accounting differences: the additional events
fully retire, so they are neither internal occupancy nor phantom throughput.
They are nevertheless very small relative to both generated demand and source
capacity loss.

| suite | matched IDs | fixed-only | moving-only | discordant total | accepted-set Jaccard |
| --- | ---: | ---: | ---: | ---: | ---: |
| full50 | 78,023 | 5,491 | 5,532 | 11,023 | 0.87621 |
| capacity22 | 37,545 | 5,403 | 5,438 | 10,841 | 0.77595 |

Thus `+41` and `+35` are the small net residues of thousands of ID swaps, not
41/35 strictly additional events layered on an otherwise identical accepted
population. Any latency/fairness claim that ignores this churn is unsafe.

## 4. Raw versus occurrence-ID matched latency

Latency is occurrence-to-retirement with the same edge convention as A4.

| suite/cohort | model | count | mean | p95 | p99 | max |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| full50, all accepted | fixed | 83,514 | 14.943 | 44 | 46 | 46 |
| full50, all accepted | moving | 83,555 | 14.073 | 45 | 47 | 47 |
| full50, matched | fixed | 78,023 | 13.455 | 42 | 46 | 46 |
| full50, matched | moving | 78,023 | 12.370 | 43 | 46 | 47 |
| capacity22, all accepted | fixed | 42,948 | 22.742 | 45 | 46 | 46 |
| capacity22, all accepted | moving | 42,983 | 22.586 | 46 | 47 | 47 |
| capacity22, matched | fixed | 37,545 | 20.799 | 45 | 46 | 46 |
| capacity22, matched | moving | 37,545 | 20.315 | 45 | 47 | 47 |

Moving improves matched mean latency, but it does not dominate the tail. On a
paired-event basis, moving-minus-fixed has mean/p50/p95/p99/max
`-1.085/-2/+3/+11/+27` cycles for full50 and
`-0.484/-1/+7/+14/+23` for capacity22. Fast fill helps most events while a
minority waits materially longer under changed contention phase.

The exclusive cohorts expose the original survivor effect. Full50 fixed-only
has mean/p95/p99 `36.089/46/46`; moving-only has `38.103/47/47`.
Capacity22 is similar: `36.238/46/46` versus `38.265/47/47`.

## 5. Cause of the reported p99 +1

The cause is suite-dependent rather than one universal extra pipeline stage.
Sparse and pairwise traffic are exactly two cycles faster in the moving model,
which rules out a static +1 delivery-stage explanation.

- **full50:** raw p99 is 46 versus 47, but the matched cohort is 46 versus 46.
  The +1 is therefore a **survivor-set quantile-composition effect**. Moving's
  exclusive accepted events are later-tailed and move the pooled order
  statistic across 47.
- **capacity22:** matched p99 remains 46 versus 47, with max 46 versus 47.
  There are 496 matched moving events at latency 47; those same IDs have fixed
  p50/p95/p99 `44/46/46`. Their workload attribution is 300 uniform-overload,
  148 mixed-phase, and 48 phase-transition events. This is a real
  **matched-cohort tail shift caused by overload arbitration/admission
  history**, not by selecting only different target IDs. Different co-accepted
  events still alter local branch phase, so the audit does not overclaim an
  isolated combinational cause.

The different result occurs because full50 adds many low-latency observations
that change the pooled p99 rank; full50 and capacity22 must not be combined as
independent samples because capacity22 substantially overlaps full50.

## 6. Demand-normalized fairness

For each active source the service ratio is `accepted/generated`; Jain's index
is then computed over those ratios. This does not reward a source merely for
having more offered demand.

| suite | pooled Jain fixed/moving | pooled min ratio fixed/moving | macro run-mean Jain fixed/moving | worst run Jain fixed/moving |
| --- | ---: | ---: | ---: | ---: |
| full50 | 0.998805 / 0.998772 | 0.733932 / 0.733932 | 0.998770 / 0.998760 | 0.986125 / 0.985783 |
| capacity22 | 0.995858 / 0.995774 | 0.577318 / 0.577318 | 0.997218 / 0.997198 | 0.986125 / 0.985783 |

The differences are small, but none supports a fairness improvement. Moving is
slightly lower by both pooled and macro definitions; its net capacity gain is
not a fairness gain.

## 7. Phase recovery

The official phase-transition ranges are frozen eighths of the 4,096-cycle
stimulus. Results below use occurrence-phase matched cohorts.

| trace/phase | fixed/moving accepted | fixed p95/p99/max | moving p95/p99/max |
| --- | ---: | ---: | ---: |
| s3501 overload | 1,057 / 1,061 | 46 / 46 / 46 | 45 / 47 / 47 |
| s3501 post-sparse | 51 / 51 | 7 / 21 / 21 | 10 / 23 / 23 |
| s3502 overload | 1,055 / 1,058 | 45 / 46 / 46 | 46 / 47 / 47 |
| s3502 post-sparse | 54 / 54 | 8 / 35 / 35 | 9 / 36 / 36 |

Sparse and near-saturation matched events improve uniformly from five to three
cycles. At the overload-to-post-sparse transition, however, moving has worse
post-sparse p95 and max because early probe events encounter a different
residual arbitration state. Recovery-to-zero measured from the no-injection
drain boundary is fixed/moving `1/0` cycles for s3501 and `0/0` for s3502.
Therefore there is no sustained or repeatable phase-recovery advantage beyond
at most one boundary cycle.

## 8. Pairwise mapping delta

The identity and affine traces are joined exactly by canonical source pair and
repeat index. All `240/240` pair trials complete for fixed and moving under
both mappings, so there is no drop censor or partial-pair survivorship.

- moving-minus-fixed pair completion latency is exactly `-2` cycles for every
  joined trial under both identity and affine;
- affine-minus-identity completion latency is exactly zero for every joined
  trial for both models;
- accepted counts are identical.

The official pairwise trace therefore demonstrates fill-latency improvement
and no mapping delta, but it is low enough load that it supplies no evidence
for the overload capacity claim.

## 9. Bootstrap/CI and significance interpretation

The resampling plan was fixed in the analysis source before interpreting the
result: seed `20260811`, 10,000 paired trace-cluster replicates; capacity22 uses
the deterministic seed expansion `20260812`. A whole exact run is resampled as
one cluster, preserving within-trace event dependence.

| suite | accepted delta | paired cluster 95% CI | positive/negative/zero runs | two-sided run sign p | matched p95 delta CI | matched p99 delta CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full50 | +41 | **[23, 61]** | 14 / 0 / 36 | 0.000122 | [0, 1] | [0, 1] |
| capacity22 | +35 | **[21, 49]** | 12 / 0 / 10 | 0.000488 | [0, 1] | [0, 1] |

Under this fixed suite-as-clusters robustness model, the direction of the
accepted-count delta is statistically stable: no run has a negative net delta.
The CI is not a population claim because the official workloads are curated,
not IID draws. More importantly, statistical detectability does not make the
effect practically large. The measured gain recovers only 0.15--0.18% of
source capacity loss while accepted membership churns by roughly 11k events
and tail/fairness do not improve.

## 10. Verdict

`+41/+35` is **real and statistically directional, but not practically
material on the present evidence**. It is correctly described as a tiny
generated-event capacity-loss reduction, not a broad throughput, latency-tail,
or fairness win. The moving model's doubled local control-touch proxy remains
unpaid by the observed capacity delta, and capacity22 retains a matched p99/max
regression of one cycle.

The A4 moving-block candidate therefore remains **HOLD**. Promotion would need
physical timing/power evidence plus a predeclared system value for a
0.04--0.05 percentage-point acceptance-rate increase that outweighs the tail
and control-cost regressions. This W4 audit supplies neither RTL qualification
nor such a value judgment.

## 11. Reproduction

```bash
python3 tests/a5_w4_moving_block_audit/analyze_moving_block.py \
  --a1-repo /home/chickgoose/projects/a1 \
  --a4-repo /home/chickgoose/projects/a4 \
  --output /tmp/a5-w4-audit.json
python3 -m unittest tests/a5_w4_moving_block_audit/test_analysis.py
```
