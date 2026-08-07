# A7 replicated-selector falsification and scaling report

Date: 2026-08-07
Scope: candidate-only RTL and tests; no server PPA

## Decision

The broad claim that the shared-prefix implementation is already an
area/depth win at N=16, K=2 is **rejected**.  Against a reference with the same
rotation, fairness, independent output registers, and refill behavior, the
prefix implementation uses 4,299 versus 3,733 generic combinational gates
(+15.2%) and has the same 133-level generic depth.  It has no performance or
lane-utilization advantage on the always-ready suite because both implement
the same arbitration contract.

The narrower scaling claim is retained: N=16, K=4 is the first measured point
where prefix wins both proxies (5,592 versus 6,729 gates, -16.9%; 139 versus
248 levels, -44.0%).  N=16/K=8 and all tested K>=2 points at N=32/64 also win
both generic gate and depth proxies.  K=1 is not a compaction use case and
prefix loses depth at all tested N.  This is a Yosys structural result, not a
standard-cell area/Fmax result.

## Fair reference

`a7_replicated_selector_reference` is deliberately conventional: K identical
rotation-aware fixed-priority selectors are cascaded through winner masks.
It shares the candidate's rotation base, source-inflight state, K one-entry
registered retire lanes, independent ready semantics, same-cycle refill,
source-local ordering guard, and advance-past-last-winner fairness rule.  The
only experimental variable is the selection primitive.  It does not use any
mechanism from another track.

## Structural scaling

The table reports post-`techmap; opt` one-bit generic combinational cells and
`ltp -noff` topological levels.  Register bits are identical within every N/K
pair, so the comparison does not buy a result by changing output state.

| N | K | prefix gates/depth | replicated gates/depth | register bits (both) | verdict |
| ---: | ---: | ---: | ---: | ---: | --- |
| 16 | 1 | 3,689 / 130 | 2,304 / 67 | 41 | reject |
| 16 | 2 | 4,299 / 133 | 3,733 / 133 | 62 | reject |
| 16 | 4 | 5,592 / 139 | 6,729 / 248 | 104 | pass |
| 16 | 8 | 8,328 / 151 | 13,396 / 476 | 188 | pass |
| 32 | 1 | 9,633 / 219 | 7,746 / 119 | 59 | reject |
| 32 | 2 | 10,855 / 222 | 12,111 / 234 | 81 | pass |
| 32 | 4 | 13,436 / 228 | 20,983 / 447 | 125 | pass |
| 32 | 8 | 19,077 / 240 | 39,434 / 871 | 213 | pass |
| 64 | 1 | 25,439 / 389 | 27,993 / 217 | 93 | reject: depth |
| 64 | 2 | 27,914 / 392 | 42,895 / 429 | 116 | pass |
| 64 | 4 | 33,105 / 398 | 72,845 / 836 | 162 | pass |
| 64 | 8 | 44,790 / 410 | 133,489 / 1,648 | 254 | pass |

Pass requires prefix to be no worse on both generic gate count and depth.
Raw operator counts and width-weighted operator-bit proxies are preserved in
`adversarial-structural.csv`; raw operator count alone is misleading because a
wide operator and a one-bit operator each count as one.  The generic mapping
is therefore the primary structural proxy.  No Liberty mapping, placement,
routing, or server PPA was run.

## Performance and lane utilization

The replicated reference ran the same frozen 46 N=16 always-ready traces at
K=1/2/4.  Comparing all 87 same-K aggregate rows after ignoring only the
candidate name found zero differences across generated, overrun, accepted,
delivered, errors, fixed-window throughput, latency/wait tails, and fairness.
Consequently lane utilization (`throughput / K`) is also identical at every
same-K point.  The committed prefix headline remains valid: at uniform 2.0,
K=1/2/4 throughput is 0.9995/1.9990/1.9990 and utilization is
100.0%/100.0%/50.0%.  It is evidence for multi-lane capacity, not evidence
that prefix beats replicated selection.

Sparse overhead is now explicit.  At uniform 0.125 both structures have the
same 0.1209 event/cycle and two-cycle p95/p99, while N=16 prefix pays a gate
penalty at K=1 and K=2.  The prefix structural break-even begins at K=4 for
the frozen N=16 design.

## Independent-ready adversarial result

Prefix and reference each passed K=2/4/8 under three phases: distinct
per-lane periodic ready, alternating all-ready/lane-selective backpressure,
and a permanently stalled lane 0.  Assertions checked stable valid/event/source
on the stalled lane, no source duplicated across lanes, no phantom retirement,
exact accepted-to-delivered conservation after drain, and progress on lanes
other than lane 0.  Observed other-lane retire counts were identical by
implementation: 227/671/1,590 for K=2/4/8.

The bounded-fairness proof still applies to service cycles with available
capacity.  A permanently stalled lane excludes its held source from
readmission, while the remaining lane capacity continues round-robin service;
no arbiter can bound completion of the event held behind an unready sink.

## Reproduction

```bash
PATH=/path/with/verilator:$PATH \
  tests/a7_parallel_event_compactor/run_adversarial.sh

PATH=/path/with/verilator:$PATH AER_SIMULATOR=verilator \
  tests/a7_parallel_event_compactor/run_unit.sh

LD_LIBRARY_PATH=/path/to/yosys-libs \
  tests/a7_parallel_event_compactor/structural_compare.py \
  --yosys /path/to/yosys \
  --output reports/a7-parallel-event-compactor/adversarial-structural.csv

AER_A7_IMPL=replicated AER_CLEAN_OUT=/tmp/a7-reference-46 \
  AER_A7_TRACE_DIR=/tmp/a7-neutrality-n16-traces \
  scripts/run_a7_46_traces.sh

python3 benchmarks/clean_slate_aer/aggregate.py \
  /tmp/a7-reference-46/replicated/k*/*/trace.csv \
  --events /tmp/a7-reference-46/replicated/k*/*/trace.events.csv \
  --output reports/a7-parallel-event-compactor/replicated-aggregate.csv

sed -i 's/\r$//' \
  reports/a7-parallel-event-compactor/replicated-aggregate.csv

tests/a7_parallel_event_compactor/compare_46.py \
  reports/a7-parallel-event-compactor/aggregate.csv \
  reports/a7-parallel-event-compactor/replicated-aggregate.csv

git diff ad96895 -- scripts/run_clean_benchmark.sh tb/clean/aer_clean_tb.sv
```

The unit run again passed all 65,536 N=16 bitmaps at K=1/2/4.  The final
frozen-common diff command produced no output.
