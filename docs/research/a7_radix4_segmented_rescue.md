# A7 radix-4 segmented-prefix rescue hypothesis

Status: pre-RTL falsification plan, 2026-08-07

## Fixed baseline and scope

This experiment does not revise the second-round result.  Commit `2219040`
fixes the original parallel-prefix and equal-state replicated-selector data;
its `adversarial-structural.csv` SHA-256 is
`101e9c2f1d94478d1fd8d896f75626889dcdff9d526537411a77586769c38021`.
The rescue remains on the A7 scan axis.  It adds no spatial hierarchy,
prediction, codec, calendar, token-ring, or other-track mechanism.

All three candidates retain the same rotation state, source-inflight state,
K registered output lanes, independent ready behavior, same-cycle refill, and
advance-past-last-selected fairness rule.  Only the combinational bitmap-to-rank
primitive differs.

## Radix-4 segmented scan

For segment `q=floor(i/4)` and local offset `t=i mod 4`, compute a narrow
inclusive local count

```text
L[q,t] = sum(request[4q : 4q+t])       // width 3
G[q]   = L[q,3]                        // segment population, width 3
```

Only the segment populations enter the wide shared prefix:

```text
H[q] = sum(G[0:q-1])                   // width ceil(log2(N+1))
P[i] = H[q] + L[q,t]                   // exact inclusive source prefix
```

`P` is bit-for-bit equal to the original scan.  The existing rotation-neutral
transform reuses it to form cyclic ranks, and available-lane rank `s` selects
the unique active source with cyclic rank `s`.  Thus exactly
`min(popcount(eligible), available_lanes)` distinct indices are produced in
cyclic order without K replicated priority encoders.

The local network uses pair sums (`r0+r1`, `r2+r3`) and one second level for
offsets 2/3.  The segment-prefix network uses iterative doubling over N/4
totals.  Compared with a W-bit source-level scan, narrow work moves below the
segment boundary and the wide prefix population falls by four.  The expected
cost is

```text
local narrow adds       = 4 * (N/4), width <= 3
wide segment-prefix adds= (N/4)*log2(N/4) - (N/4-1)
source reconstruction  = N wide adds (segment base + local count)
```

This can reduce area, but reconstruction plus the local/group dependency may
lose depth.  That tradeoff is the point being falsified.

## Verification and structural contract

N=16 exhaustively checks all 65,536 bitmaps for every prefix value, total, and
the first K indices at K=2/4.  N=16/32/64 then run deterministic randomized
cycle equivalence at K=2/4 across original, segmented, and replicated
candidates under independent ready/backpressure.  Every cycle must match
`source_ready`, retire valid/event/source, including stalled-lane stability.

Yosys evaluates N=16/32/64 and K=2/4 with identical top ports and register
boundaries.  It reports operator and generic gate count, `ltp -noff` depth,
register bits, and post-techmap net fanout derived from JSON cell input
connections.  No server or standard-cell PPA is authorized.

## Rescue decision

N=16/K=2 is rescued only if segmented prefix is strictly smaller than both
original prefix and equal-state replicated reference in generic combinational
gates and strictly shallower than both in generic topological depth, with equal
register bits and complete equivalence.  A tie fails the simultaneous
area/depth criterion.

If it fails, the K=2 rescue is rejected regardless of results at larger N.
The allowed A7 application condition remains N=16/K>=4, and K=4 is retained
only where the measured segmented or original prefix beats the equal-state
reference without changing functional behavior.
