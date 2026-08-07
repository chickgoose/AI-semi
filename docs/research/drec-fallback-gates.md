# DREC physical-failure fallback gates

Date: 2026-08-07

This file prevents an open-ended attempt to rescue DREC if standard-cell or
post-route evidence removes its generic N=16/K=4 crossover.  A fallback is
started only after DREC reaches a declared STOP condition.

## Fallback 1: N=64 radix-4 elastic quadtree scaling study

This is not a revival of the rejected A4 N=16 point.  N=16 remains
`HOLD_FLAT`.  The separate N=64 hypothesis previously showed, against its flat
reference, generic cells -37.3%, combinational logic -44.8%, depth 114 to 27,
maximum fanout 1,074 to 127, and full-wire proxy 8,192 to 3,584.  Its costs are
58.3% more state and two sparse-latency cycles.

Minimum qualification:

1. exact parameterized N=64 RTL;
2. randomized conservation, source-local order, and independent-stall checks
   against the flat reference;
3. one identical-flow generic mapping replay;
4. GO only with zero functional errors and all five declared structural gates
   still passing.

Without a frozen N=64 common trace suite this remains a scaling hypothesis, not
an N=16 submission winner or routed PPA claim.

## Fallback 2: hierarchical K-grant merge compactor

This is a new Wave-2 hypothesis rather than a patch to the rejected segmented
K=2 scan.  Radix-4 leaves produce ordered lists of at most K requests and a
bounded-fanin tree merges those lists into K independently available retire
lanes.  It removes the global rank-reconstruction cone that made the segmented
rescue deeper, while preserving the DREC target of shared multi-event service.

Minimum qualification:

1. exhaustive all 65,536 N=16 request bitmaps at K=4;
2. cycle lockstep under independent ready/refill against a golden selector;
3. identical Yosys comparison against original DREC K=4 and replicated K=4;
4. GO only if it preserves the replicated-reference crossover, reduces at
   least one of depth, maximum fanout, or wire proxy versus original DREC, and
   increases cells by no more than 5%.

If neither gate passes, do not reopen A2/A3/A5/A6/A8/A9 merely to fill time.
Their Wave-1 failures require a new capability contract or substantial redesign.
