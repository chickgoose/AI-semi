# A9 fourth assignment: final local structural gate

Date: 2026-08-07.  Status: final local decision; no server or physical PPA.
The H1/H2 result is frozen at `855e950` and `d3a386d`.  This phase changes no
common benchmark, TB, trace, golden data, or other track.

## Method

Yosys 0.52 (`fee39a3284c90249e1d9684cf6944ffbbcbb8f90`) performs a
technology-neutral comparison at N=16/L=4 and N=64/L=8.  Static A9, the H2
neighbor-handoff wrapper, and the same-L flat centralized reference are each
placed inside `a9_phase4_synth_top` with identical boundaries:

- registered source-valid and 16-bit source-event inputs;
- registered retire-ready inputs;
- registered source-ready outputs; and
- registered retire-valid, 16-bit event, and source-ID outputs.

The flow is `proc; flatten; opt; memory; opt; techmap; opt; abc -g simple;
clean`.  The generic library contains AND, OR, XOR, MUX, and NOT gates; it is
not a standard-cell library and has no wire delay.  `logic_depth` is the
longest generic combinational-cell chain between a top input/register and a top
output/register D or enable.  Fanout counts mapped data/control consumers and
excludes top clock and reset.  `ready_to_valid_depth` traces only the common
registered retire-ready boundary to the pre-output-register retire-valid net.

Yosys 0.52 cannot parse the simulation interface's unpacked array ports.  The
`A9_YOSYS` preprocessor view changes only those ports to equivalent packed
arrays; indexing and candidate logic are unchanged.  Normal Verilator/Icarus
regressions use the original ports.

The common register-shell bit count is 376 at N=16 and 1,344 at N=64.  Core
state below subtracts that same shell from each mapped result.  Synthesis may
remove constant/redundant payload bits, so mapped core state is intentionally
reported rather than replacing it with the earlier hand formula.

## Generic mapping result

| N/L | Implementation | generic cells | combinational | state bits total / core | logic depth | max data/control fanout | ready→valid depth |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 16/4 | static | 3,660 | 2,355 | 1,305 / 929 | 10 | 42 | 0 |
| 16/4 | H2 | 3,424 | 2,113 | 1,311 / 935 | 10 | 42 | 6 |
| 16/4 | centralized | 1,658 | 918 | 740 / 364 | 20 | 22 | 0 |
| 64/8 | static | 15,272 | 10,059 | 5,213 / 3,869 | 10 | 44 | 0 |
| 64/8 | H2 | 14,174 | 8,947 | 5,227 / 3,883 | 10 | 44 | 8 |
| 64/8 | centralized | 6,580 | 3,942 | 2,638 / 1,294 | 29 | 25 | 0 |

At N=16, static uses 2.21x central generic cells and 2.55x central total state;
at N=64 those ratios are 2.32x and 1.98x total state, or 2.99x after removing
the common shell.  Central logic depth grows from 20 to 29 while static remains
10.  This is the only structural evidence favorable to distributed A9.

H2 declares exactly L new pin bits in RTL.  The mapped state delta is six bits
at N=16 and fourteen at N=64 because the changed ready cone also prevents Yosys
from removing two/six base-state bits.  H2's lower mapped combinational count is
an ABC Boolean-optimization result, not a claim that the wrapper is physically
free.  It still adds paired output mux/control and the only ready-to-valid path.

The mapped maximum fanout proxy does not favor A9: 42/44 for distributed versus
22/25 for central.  It captures gate consumers after ABC duplication, not
physical wire span.  Therefore it cannot validate the original local-wire
hypothesis; only an approved placed physical comparison could do that.

## Asymmetric lane-stall sweep

The simulation sweep offers one event/cycle, rotating across sources 0--3 of
stripe 0 for 1,000 cycles.  Lane 0 is stalled for the first 0/25/50/75/100
cycles of every 100-cycle period; paired lane 1 and all other lanes remain
ready and idle.  All accepted events are drained and checked exactly once.
`measured delivered` counts only the stimulus window.

| lane-0 stall | static delivered / overrun | H2 delivered / overrun | central delivered / overrun | H2 migrations | coverage | H2 delivered gain vs static |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0% | 994 / 0 | 994 / 0 | 997 / 0 | 0 | N/A | 0 |
| 25% | 749 / 241 | 994 / 0 | 749 / 244 | 245 | 100% | 245 |
| 50% | 499 / 491 | 994 / 0 | 499 / 492 | 495 | 100% | 495 |
| 75% | 249 / 741 | 994 / 0 | 249 / 744 | 745 | 100% | 745 |
| 100% | 0 / 984 | 994 / 0 | 0 / 991 | 994 | 100% | 994 |

Here coverage is successful migrations divided by cycles with a valid static
stripe-0 head while its lane is stalled.  The idle neighbor makes every H2
opportunity legal.  This is a deliberately favorable H2 condition; partner
traffic or an already pinned head reduces coverage, as the phase-3 directed
test demonstrates.

The toggle proxy samples H2's added pin bits and migration predicate at clock
edges.  Pins never toggle in this favorable sweep because every fresh stalled
head migrates immediately.  The migration predicate toggles 20 times at
25/50/75% stalls and twice at 100%, giving 0.020121, 0.020121, 0.020121, and
0.002012 added sampled toggles per measured event.  These are reproducible
logical activities, not a power estimate; intra-cycle hazards are not counted.
The more important cost is structural: six/eight generic levels of
ready-to-valid dependency at N=16/64.

## Always-ready imbalance remains negative

At 0% stall, static and H2 both deliver 994 measured events, H2 migrations are
zero, and added toggles/event is zero.  The phase-3 hotspot, moving-hotspot,
alternating-stripe, and all-stripe runs likewise produced zero migrations and
identical static/H2 throughput, overrun, and latency.  H2 therefore does not
repair fixed-stripe imbalance and must not be presented as doing so.

## Final local decision

There is **no unconditional distributed A9 physical shortlist**.

- N=16 always-ready: reject static and H2 in favor of the same-L central
  reference.  Central uses less than half the generic cells and 43% of static
  core state, while the earlier simulation also showed lower latency.
- N=64 always-ready: retain static A9 only as a conditional timing-first
  physical experiment if a future head-approved flow shows that central's
  depth-29/global placement path misses frequency and the roughly 2.3x cell,
  3.0x core-state, and pipeline-latency costs are acceptable.  Generic mapping
  alone is insufficient to promote it.
- H2: shortlist only for a system contract with persistent asymmetric
  independent lane stalls and usually idle paired lanes.  Under the favorable
  25--100% sweep it converts every stalled-head opportunity into one delivered
  event, but it adds a ready-to-valid timing path and provides exactly zero
  always-ready benefit.  It is not the default A9 candidate.

Thus the distributed-token track is rejected for the current always-ready
benchmark/PPA shortlist.  The only remaining conditional cases are N=64
timing-dominated static A9 or backpressure-specific H2.  Neither may advance on
the basis of these generic proxies alone.  No global scan, other-track
mechanism, common-file change, or server execution was used.

## Reproduction

With Yosys 0.52 and Verilator available locally:

```text
YOSYS=/path/to/yosys scripts/run_a9_phase4_yosys.sh
VERILATOR=/path/to/verilator tests/a9/run_phase4_stall_sweep.sh
```

The first command writes six netlist JSON/stat/log directories plus
`summary.csv`; the second writes 15 checked simulation logs plus `results.log`.
Generated outputs remain under `/tmp` by default and are not committed.
