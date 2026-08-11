# W3_HEAD_SUMMARY — A9 online primal-dual lane-price matcher

Date: 2026-08-11.  Decision: **HOLD; no SV**.

## Contract and provenance

- candidate path only; common TB/manifests and existing RTL are unchanged;
- canonical suite HEAD `47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be`;
- full50 SHA `9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9`;
- cap22 SHA `99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62`;
- generator v4 SHA `59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50`.

Each source has exactly two fixed legal lanes and emits one proposal per cycle.
Each lane resolves only its fixed adjacency.  A 3-bit lane price changes by at
most one from actual full occupancy or output stall and is never a function of
source age, deficit, request pressure, or an N-wide vector.  A 2-bit rejection
counter enters a pinned escape.  The local cyclic tie serves that escape within
the lane adjacency degree under always-ready service.  Same-source ordering is
protected by a lane route lock until outstanding count returns to zero.

## Cycle-model result

| cap22 aggregate | lane price | price off | exact K | flat RR |
| --- | ---: | ---: | ---: | ---: |
| generated | 65,616 | 65,616 | 65,616 | 65,616 |
| delivered | 64,557 | 64,557 | 65,603 | 42,685 |
| source overrun | 1,059 | 1,059 | 13 | 22,931 |
| sum of per-trace measured event/cycle | 24.694825 | 24.694825 | 25.035888 | 16.818118 |
| worst p99 | 4 | 4 | 4 | 16 |
| worst demand-normalized Jain fairness | 0.998358 | 0.998358 | 0.999998 | 0.991218 |
| price toggles/event | 0 | 0 | N/A | N/A |

The candidate reaches 98.64% of exact-K aggregate throughput and exceeds the
single-grant flat RR.  That is not a price result: cap22 price-on and price-off
are bit-for-bit metric-identical.  The five full50 moving-hotspot controls also
match across all four models at 9,111/9,111 delivered, zero overrun, p99=1.

Affine pairwise contention is invariant (480 delivered, zero overrun, p99=2),
but mixed-phase address placement remains visible.  Identity produces 437
overruns and 2.145508 event/cycle; bit-reverse produces 192 and 2.205566.  Exact
K has only 8/5 overruns, so the fixed degree-two proposal graph leaves a real
matching gap.

Alternating lane stalls produce 38 price updates and 0.107692 price bit
toggles/event, yet price-on and price-off both deliver 520 events with 504
overruns and p99=5.  Thus price adds activity with zero delivered or tail gain.
The all-same-cheapest directed state drains eight contenders, enters four
escapes, and observes max escape wait one ready opportunity; this supports the
bounded collision escape but not a pricing benefit.

## Counterexamples and proof boundary

- N=4 exhaustive exploration covers every pending mask, both FIFO occupancies,
  all 2-bit lane prices, and tie states; all always-ready states conserve and
  drain.
- With `R=2^reject_bits-1`, a rejected source pins to its other legal lane.  A
  continuously serviceable lane's cyclic tie accepts it within at most its
  fixed adjacency degree after escape entry.
- The bound counts ready service opportunities.  If the chosen route-locked
  lane is stalled indefinitely while the source's other legal lane is ready,
  an accepted event starves.  The price saturates but cannot migrate the event
  without either breaking source ordering or adding route acknowledgment/state.
- Alternating stalls give a bounded price oscillation with no performance
  benefit.  No livelock occurs after all lanes become ready and injection stops.

## PPA proxies and decision

| Point/model | max lane adjacency | comparator-depth proxy | control-state bits |
| --- | ---: | ---: | ---: |
| N16/L4 lane price | 8 | 4 | 184 |
| N16/L4 exact K | global N16 | 16 | 4 |
| N16/L4 flat RR | global N16 | 16 | 4 |
| N64/L8 lane price | 16 | 5 | 760 |
| N64/L8 exact K | global N64 | 48 | 6 |
| N64/L8 flat RR | global N64 | 64 | 6 |

The control path is one two-price compare plus a local adjacency selection;
there is no flat N-wide request/grant path.  State includes prices, local tie
cursors, rejection/escape, route lock, and outstanding counts, excluding common
source latches and event FIFO payload.  The central references' small state is
only a cursor; their proxy exposes the global matching/scan logic and omits the
data crossbar.  These are structural estimates, not synthesized PPA.

**HOLD reason:** fixed legal matching is promising, but the assigned online
primal-dual price mechanism provides exactly zero cap22/moving-hotspot gain and
adds toggles under stalls.  The arbitrary-lane-stall ordering counterexample is
also unresolved.  Because the GO gate failed, creating SV or a lockstep TB
would incorrectly promote a non-contributing mechanism and is prohibited.
