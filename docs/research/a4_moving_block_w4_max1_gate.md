# A4 W4 STYLE2 versus MAX1 quantitative gate freeze

Status: frozen before review of A3 same-flow comparison; current decision HOLD

## Provenance recheck

The W4 owner rechecked clean HEAD
`41f239dad4a342277f33d94bb3ed3db53e3497e0`.  The tracked worktree was clean,
all seven W4 result-contract tests passed, and the locked RTL, runners,
analyzers, TBs, generators, and two existing result files matched their recorded
SHA-256 values.  No common file or manifest was changed.

The two existing evidence sets remain immutable:

- `w4_local_summary.json`: 50 full50 plus 22 cap22 always-ready exact RTL
  traces and N16/N64 structural proxies;
- `w4_functional_followup.json`: 2,982 stalled, random-ready, reset, shock, and
  N64 exact-equivalence cycles.

This report adds a decision rule only.  It does not import or inspect an A3
MAX1/MAX2/selected result, so its thresholds precede that comparison.

## Comparison contract

The reference must be fixed one-step `MAX_ADVANCE=1`; the candidate must be W4
STYLE2 (`shared_clearance_local_enable`, `MAX_ADVANCE=2`).  A comparison is
eligible only when N16 and N64 use identical output width, ingress/output
register boundaries, reset, external pins, parameters other than the declared
movement/control choice, Yosys version, pass sequence, and generic cell-cost
interpretation.  RTL line count is not evidence.

Missing raw counts, a changed boundary, or a different synthesis flow produces
HOLD rather than an inferred result.

## Locked workload benefit floor

The capacity improvement is small, so it cannot justify an unlimited control
premium.  Before structural cost is considered, MAX2/STYLE2 must meet all of:

| metric | full50 minimum | cap22 minimum |
| --- | ---: | ---: |
| accepted gain | 32 events and 0.030% of offered | 32 events and 0.050% of offered |
| output-bubble reduction | 25% | 40% |
| p99 regression | at most +1 cycle | at most +1 cycle |
| maximum-latency regression | at most +1 cycle | at most +1 cycle |

Additionally, isolated-sparse and `shape_b16` p99 must each improve by at least
two cycles, aggregate throughput must not decrease, and overrun must not
increase.  The already frozen observation passes narrowly: accepted gains are
41/35 events (0.03853%/0.05334%), bubble reductions are 26.99%/47.19%, and the
tail penalty is exactly one cycle.  These are floors, not a claim that a tiny
accepted-event delta alone pays for hardware.

## Same-flow local cost ceiling

Every criterion below must pass independently at both N16 and N64:

| cost metric, selected relative to MAX1 | ceiling |
| --- | ---: |
| mapped state bits | exactly equal |
| mapped total-cell premium | <= 15% |
| mapped combinational-cell premium | <= 20% |
| logic-depth premium | <= 2 levels and <= 10% |
| maximum-fanout premium | <= 15% |
| nets with fanout >=16 premium | <= 15% |
| pipeline-latency delta | 0 cycles |
| external-pin delta | 0 |

The percentage tests use integer raw counts; equality at a ceiling passes.
Passing one size cannot compensate for failing the other, and a cell reduction
cannot compensate for a depth or fanout failure.

STYLE2 relies heavily on integrated-enable flops, so a second conservative
accounting is mandatory: replace each generic DFFE with one DFF plus one
external mux.  Under that accounting, effective total-cell premium must be at
most 25%, effective combinational premium at most 30%, and the estimated depth
premium at most two levels at both sizes.

## Decision semantics

If functional prerequisites, the workload benefit floor, exact comparability,
all local cost ceilings, and conservative enable accounting pass at N16 and
N64, the result is only `GO_TO_COMMON_AND_PHYSICAL`.  It is not physical
promotion.

Any failed, missing, stale, or incomparable field produces `HOLD_STYLE2` and
retains fixed MAX1 as the cost reference.  The current state is
`HOLD_PENDING_A3_SAME_FLOW_AND_COMMON_PHYSICAL_QUALIFICATION`.

For later physical promotion, the same-library/constraint/activity comparison
must additionally show no more than 10% post-synthesis area premium, valid Fmax
at least 98% of MAX1, energy per delivered event no worse than MAX1, and clean
setup/hold/DRC/antenna results.  All are mandatory; no weighted average hides a
failure.

The machine-readable immutable rule and exact provenance are in
`rtl/candidates/a4_moving_block_w4/results/w4_max1_gate_freeze.json`.
