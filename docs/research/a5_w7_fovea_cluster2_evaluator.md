# W7-A5 Fovea versus Cluster2 evaluator

Status: **evaluator implementation GO; real paired comparison HOLD**.

Commit-local implementation lives under
`tests/a5_fovea_cluster2_evaluator/`. It changes no common benchmark, manifest,
TB, binding, or candidate RTL. Its purpose is to prevent the historical
18-trace direct-coordinate and Cluster2 numbers, current generator-v4 suites,
and native 1:5:5:1 policy probes from being mixed into one unsupported winner.

## Evidence boundary

The only accepted candidates are the native tops
`aer_tx16_trad_rowcol_fovea` and
`aer_tx16_trad_rowcol_fovea_cluster2`. Fovea is normalized as one scalar
address lane; Cluster2 is normalized as its fixed eight row/column slots. Both
remain address-only and always-ready for these suites. The evidence bundle
binds actual source, binding, runner, and simulator bytes plus exact result
artifacts. The evaluator regenerates the SHA-pinned official full50 and
capacity22 traces itself.

Capacity22 is an exact subset: all 22 overlapping Fovea results and all 22
overlapping Cluster2 results must be byte-identical to their full50 artifacts.
The evaluator reports both suite views but never treats them as 72 independent
workloads.

## Outcome and Pareto dimensions

Correctness and reset are hard gates before ranking. Generated accounting,
per-occurrence identity, source order, fixed-window delivery, full drain,
direct native-valid reset observation, reset-after-drain, quiet outputs,
loss/duplicate/phantom/stale exclusion, and a caught negative control must all
pass.

The unweighted Pareto vector then contains:

- uniform capacity knee and full50/capacity22 fixed-window event/cycle;
- overrun, p99 tail, maximum wait, demand-normalized fairness, and minimum
  source delivery;
- spatial and moving/rotating family throughput, overrun, p99, and fairness;
- exact pairwise identity/affine completion, p99, mapping delta, and matched
  relation churn over 240 relations;
- distance from ideal Fovea row shares `1/12,5/12,5/12,1/12`.

Fovea must pass the independent continuous-all-source 1:5:5:1 control within
one percentage point. Cluster2 is not forced to emulate it: its native row
shares are reported as preserved or transformed and enter the Pareto vector.
No weighted scalar score is produced.

## False-pass closure

The official-generation integration plus nine evidence mutations cover
duplicate IDs, fabricated delivery, forged measurement counts, stale reset,
false Fovea weighting, trace substitution, correctness-to-Pareto leakage,
capacity22 rebound, and source artifact swap. Current regression result:

```text
Ran 11 tests ... OK
A5_W7_FOVEA_CLUSTER2_EVALUATOR_TEST_PASS tests=11 mutations=9
```

## Current decision

| Item | Status | Reason |
| --- | --- | --- |
| Evaluator logic | **GO** | exact official generation, direct recomputation, atomic receipt, mutations pass |
| Historical 18/18 Cluster2 result | **not ranking input** | old suite/manifest and incomplete W7 dimensions |
| Existing Fovea+A7 W6 replay | **not raw-Fovea input** | includes the A7 endpoint and has a different boundary |
| Raw Fovea versus raw Cluster2 W7 outcome | **HOLD** | no current paired full50/cap22 native evidence bundles supplied |
| Physical/PPA winner | **HOLD** | this evaluator contains no characterized or routed evidence |

Promotion requires newly receipted raw-native runs on the same frozen traces,
the two reset artifacts, and the same-boundary full-contention row-policy
probes. Synthetic data used by unit tests is explicitly test-only and is not a
hardware result.
