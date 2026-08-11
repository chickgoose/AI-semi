# A8 Wave 3: Non-Crossing Frontier Fabric

Status: **HOLD after cycle-model gate; SystemVerilog and lockstep TB were not
created**.

## Structure and invariant

For N=16 and K=4, three strictly ordered frontier registers partition sources
into four nonempty contiguous half-open territories.  A source is owned by
exactly one lane, and each lane performs local RR.  Internal frontiers move by
at most one address per cycle.  A live-neighbor check requires
`frontier[j-1] < frontier[j] < frontier[j+1]` after every proposed move.

Normal movement compares the K-lane request mass on the left of each frontier
with that frontier's `j/K` share of total request mass.  This is an occupancy
reduction across the four lanes; it does not compact source requests.  A
bounded emergency move is enabled by consecutive lane overload plus an empty
neighbor.  Reverse-direction pressure must persist before a frontier can undo
its last direction.  There is no request age, timestamp, calendar, quadtree,
stealing, source splitting, or predictor state.

The first implementation exposed a static `request=16'h0808` counterexample:
45 direction reversals in 32 cycles.  Reversal debounce plus a full-occurrence
quantile hysteresis removed the sustained oscillation without adding request
age.

## Cycle-model results

Both schedulers have four logical retire lanes, identical one-pending-source
latches, 512 stimulus cycles, and complete drain.  Throughput is delivered
events per stimulus cycle.  All accepted events were delivered exactly once.

| Workload | Flat delivered / overrun | Frontier delivered / overrun | Flat / frontier throughput | Frontier p99 / max wait | Fairness | Frontier distance / reversals |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| local | 1008 / 0 | 1004 / 4 | 1.9688 / 1.9609 | 1 / 2 | 1.0000 | 18 / 0 |
| dispersed | 1008 / 0 | 1008 / 0 | 1.9688 / 1.9688 | 0 / 0 | 1.0000 | 0 / 0 |
| mirror | 1008 / 0 | 1005 / 3 | 1.9688 / 1.9629 | 1 / 3 | 1.0000 | 18 / 0 |
| moving row hotspot | 1536 / 0 | 1306 / 230 | 3.0000 / 2.5508 | 2 / 2 | 0.9744 | 47 / 2 |
| moving column hotspot | 1536 / 0 | 1536 / 0 | 3.0000 / 3.0000 | 0 / 0 | 1.0000 | 0 / 0 |
| moving dispersed hotspot | 1536 / 0 | 1536 / 0 | 3.0000 / 3.0000 | 0 / 0 | 1.0000 | 0 / 0 |
| elephant-mouse | 576 / 0 | 573 / 3 | 1.1250 / 1.1191 | 0 / 1 | 1.0000 | 9 / 0 |
| global fan-in | 1008 / 0 | 1008 / 0 | 1.9688 / 1.9688 | 3 / 3 | 1.0000 | 0 / 0 |

The demand-normalized fairness column is rounded; the exact minimum delta from
flat RR is -0.025627.  The candidate's largest p99 penalty is two cycles.
Moving-column and moving-dispersed traffic already occupies several territories
and therefore needs no frontier movement.  Moving-row exposes the architectural
limit: three rotating requests among four contiguous sources cannot always be
spread over four indivisible territories from instantaneous pending occupancy.

## Exhaustive N16 checks

- all 65,536 request masks drain without starvation or crossing;
- all 455 strictly ordered four-territory partitions elaborate in the model;
- the request-mask enumeration includes empty-demand territories and every
  single-overloaded-initial-territory subset;
- every static N16 request mask is held for 32 cycles and checked for sustained
  frontier reversal;
- every transition preserves one owner per source and minimum territory width
  one; and
- emergency movement is separately forced with normal hysteresis disabled.

The exact worst drain and reversal witnesses are printed by the committed test
runner, rather than being silently inferred from a sampled workload.

## Flat-RR logical-operation and toggle proxy

| Proxy | Flat K-lane RR | Frontier fabric | Ratio |
| --- | ---: | ---: | ---: |
| request comparators | 64 | 22 | 0.34375 |
| global request-wire fanout | 64 | 24 | 0.37500 |
| select-depth proxy | 16 | 6 | 0.37500 |
| policy state bits | 4 | 54 | 13.5x |

These are incomplete cycle-model operation counts, not physical topology or
synthesis results.  The nominal comparator proxy counts 16 local source tests
plus six abstract frontier pressure comparisons.  It does **not** expand the
population-count/addition logic needed to form lane pressure, the comparison
bit width, moving-boundary request routing/muxes, ownership decoders, frontier
fanout, grant merge wiring, or their buffers.  Likewise, the nominal fanout
count treats each request as one abstract connection and can materially
undercount physical sinks when a moving frontier must route a request toward
different lane selectors.  Therefore `22/64`, `24/64`, and the depth ratio are
model-screening numbers only; they do not establish comparator, wire, area,
Fmax, or power savings over flat RR.  Policy state includes frontier positions,
four local RR pointers, overload/reversal streaks,
cooldowns, and direction.  Common source pending latches are excluded from both.

Toggle results are also behavioral state/Hamming proxies, not VCD power, and do
not include the unexpanded pressure or moving-routing combinational activity.
The frontier candidate is lower than flat RR on moving-column (0.6908 versus 0.8464
toggles/delivered) and moving-dispersed (0.6868 versus 0.9310), but worse on
local, mirror, elephant-mouse, moving-row, and global fan-in.  Global fan-in is
5.0 versus 2.375 toggles/delivered because all four local RR pointers update.

## Gate decision

The predeclared gate required minimum per-workload delivered ratio >=0.90, mean
ratio >=0.97, fairness delta >=-0.05, p99 penalty <=16 cycles, comparator ratio
<=0.50, and wire ratio <=0.50.  Results were:

```text
minimum delivered ratio   0.8502604167  FAIL
mean delivered ratio      0.9797634549  PASS
minimum fairness delta   -0.0256270223  PASS
maximum p99 delta          2             PASS
nominal comparator ratio   0.34375       MODEL-ONLY PASS
nominal wire-fanout ratio  0.375         MODEL-ONLY PASS
```

The decision is **HOLD**.  Fixing the moving-row failure requires persistent
hotspot history/prediction or allowing multiple lanes to share one source,
which changes the assigned architecture.  Per the Wave-3 rule, no SV RTL,
lockstep TB, Yosys proxy, or Verilator proxy was created after this failed gate.

## Automation semantics

`run_model_tests.sh` without arguments means that the research run and its
invariant tests completed; it prints separate machine-decision and completion
sentinel lines and returns zero for this completed HOLD study.  Qualification
automation must invoke `run_model_tests.sh --require-go`, which validates that
`go_gate.go`, `decision`, and `completion_sentinel` agree and returns nonzero for
the frozen HOLD decision.  Unit tests explicitly assert HOLD and reject a
rebound or contradictory sentinel.
