# A9 third assignment: neighbor-only empty-slot diffusion

Status: protocol gate before RTL, 2026-08-07.  The second-assignment result at
commit `cd6ba55` is frozen.  This experiment does not alter the common
benchmark, TB, traces, golden data, or the static A9 implementation.

## Question and non-overlap boundary

Can an idle adjacent retire stripe absorb work from a congested fixed stripe
without a global request scan, central priority encoder, remapping crossbar, A4
tree, A2 reservoir, or another track's mechanism?

The only candidate admitted for implementation is a one-hop ejection handoff.
Stripe `l` communicates only with its fixed adjacent pair `l xor 1`.  There is
no global idle vector, multi-hop route search, dynamic source remap, or shared
overflow pool.  A local two-input selector at each retire lane chooses its own
stripe head or the single incoming neighbor mailbox.

## Proposed local rule H1

Each paired lane endpoint has:

- one incoming handoff mailbox holding `{valid,event,source,origin}`;
- one outgoing fence holding `{valid,source}`; and
- one local arbitration toggle for native-head versus incoming-mailbox
  contention.

The underlying static stripe is unchanged.  A native head normally retires on
its home lane.  It may instead transfer exactly once into the paired incoming
mailbox when all of the following registered-state predicates hold:

1. the home lane is externally stalled;
2. the paired lane is externally ready;
3. the paired native head and paired mailbox are both empty, so the neighbor is
   actually idle;
4. the origin has no outstanding handoff fence; and
5. the event is not itself an incoming migrant.

Migration dequeues the static stripe and fills the neighbor mailbox on the
same edge.  A migrant can retire but cannot migrate again.  When it retires, a
one-hop acknowledgment clears its origin fence.  If the origin's native head
has the fenced source, that head is held until the acknowledgment; heads from
other sources may continue.  If native and mailbox heads are simultaneously
eligible at one lane, the local toggle alternates them.

This is conservative diffusion of *service opportunity*, not storage pooling.
The mailbox is an endpoint pipeline register and cannot accept arbitrary
mid-stripe traffic.  It is intentionally unable to see or compare non-neighbor
requests.

## Why the fence is mandatory

Without a fence, the following three-cycle counterexample violates the clean
source-local ordering contract:

1. source `s` event `a` migrates from stalled lane 0 to lane 1;
2. lane 1 becomes stalled while lane 0 becomes ready;
3. later source `s` event `b` reaches lane 0 and retires before `a`.

A migration toggle alone cannot distinguish this state.  Either a per-source
route epoch/acknowledgment or a local outstanding fence is necessary.  H1 uses
one fence per origin lane and therefore permits at most one outstanding migrant
from that lane.  This is the minimum state considered implementable without a
global source table.

## Conservation and safety invariants

Let `Q` be occupied static ingress/transport entries, `M` occupied handoff
mailboxes, `A` accepted source events, and `R` retired events.  Reset establishes
`Q=M=0`.  Every transition must preserve:

```text
A - R = Q + M
0 <= M <= RETIRE_LANES
fence[origin] == 1  iff exactly one mailbox holds an unretired event from origin
```

A migration is `Q--, M++`; native retirement is `Q--, R++`; migrant retirement
is `M--, R++` and clears exactly one fence.  No transition may both retire and
migrate the same event.  A stalled selected output must retain valid, event,
source, and origin.  Reset clears mailbox, fence, and toggle state, preventing
post-reset retirement.

## Deadlock and livelock contract

Permanent stall of every retire lane is environmental blocking, not protocol
deadlock.  Under weak sink fairness (each lane asserted ready infinitely often):

- a native head either retires at home or enters an empty ready neighbor;
- a migrant has no further dependency except its destination ready signal;
- the fence blocks only later events of the same source, and its unique migrant
  eventually retires and acknowledges it;
- the local toggle grants native and mailbox heads alternately when both remain
  eligible.

There is no channel-dependency cycle because migrants cannot re-migrate.  The
bounded explorer must nevertheless search simultaneous opposite handoffs,
mailbox contention, every ready mask, reset from every reachable occupancy, and
toggle polarity at N=2 and N=4.  RTL is gated on zero safety counterexamples and
zero fair no-progress cycles.

## Fundamental performance limitation

H1 cannot repair the second-assignment always-ready fixed-stripe imbalance.
Its migration predicate requires a stalled home lane; all frozen neutrality
traces use always-ready retire lanes.  More importantly, each static stripe
still exposes at most one head per cycle.  Moving that head sideways does not
create a second dequeue port, so even an eager always-ready migration policy
cannot exceed one event/cycle from the congested stripe.  Eager migration adds
one mailbox cycle and a source fence, and can reduce single-source throughput.

Solving the observed elephant/mouse affine loss would require a handoff before
the congested merge or multiple stripe-head dequeue.  With multiple in-flight
events per source and arbitrary independent lane stalls, such a rule needs
route epochs and return acknowledgments at every migrating source, or it admits
the ordering counterexample above.  That becomes a routed mesh/control network
rather than the minimal empty-slot fabric and is not accepted merely to obtain
a favorable number.

Therefore H1 is useful only if it safely recovers work during asymmetric output
stall at modest cost.  The third-assignment verdict must remain negative for
fixed-stripe imbalance unless bounded proof and workload data demonstrate a
stronger genuinely local rule.

## Required evidence and rejection gate

1. Exhaustively enumerate bounded N=2 and N=4 H1 states with arbitrary
   injection, ready masks, reset, mailbox/toggle/fence states, checking loss,
   duplication, source order, fence correspondence, stall stability, and fair
   progress.
2. Add the RTL variant only if that model passes; keep it selectable and
   separate from static A9.
3. Directed RTL assertions cover single-stripe hotspot, moving hotspot,
   alternating paired stripes, all-stripe saturation, asymmetric lane stalls,
   and reset with a migrant outstanding.
4. Compare static distributed, H1 diffusive distributed, and identical-L
   centralized reference.  Report migrations, fence-block cycles, added state,
   latency, throughput, overrun, and arbitrary-stall recovery.
5. Reject H1 as an imbalance solution if always-ready workloads are unchanged,
   or if its stall benefit does not justify the mailbox/fence/toggle cost.  Do
   not add a global remapper, crossbar, request scan, tree, or reservoir to hide
   a negative result.

## H1 review and H2 refinement

H1 as written above is rejected before RTL.  If a native head has already been
visible during `valid && !ready`, moving it into a mailbox when the neighbor
later becomes idle makes the home output disappear under continuous stall.
That violates output stability even though event conservation still holds.

H2 permits handoff only for a fresh, unpinned head.  If its home lane is stalled
while the paired lane is ready and has no native head, the event is driven to
the paired lane and retires on that same edge.  Otherwise it is presented at
home; observing one stalled home cycle sets a one-bit pin which forbids later
migration until that head retires at home.  A direct migrant is never stored,
never re-migrates, and cannot be duplicated.  Because the destination is ready
and empty by predicate, no mailbox arbitration or source fence is necessary.

H2 adds one pin bit per lane and a local neighbor-valid/ready check.  It adds a
ready-to-valid/control path across one stripe-pair endpoint but zero migration
pipeline cycles.  It still cannot increase an always-ready congested stripe's
one-head-per-cycle dequeue rate.  The exhaustive model and any RTL variant use
H2; H1 remains documented as the discovered counterexample.

## Bounded-state result

`scripts/explore_a9_neighbor_handoff.py` enumerates every abstract H2 state
with a two-entry ordered lane queue, fresh/pinned head state, every injection
mask, every retire-ready mask, and reset.  A direct migration is counted as one
retirement, never as a second stored copy.

| Bound | Abstract states | One-cycle transitions | Transitions containing migration | Result |
| --- | ---: | ---: | ---: | --- |
| N=2 | 25 | 400 | 16 | PASS |
| N=4 | 625 | 160,000 | 12,800 | PASS |

The checks cover `next_occupancy = occupancy + accepted - retired`, queue
bounds, at most one migrant per pair, empty/ready neighbor preconditions,
pinned-head immobility, reset from every state, and immediate progress whenever
an occupied home lane is ready.  FIFO head-only retirement supplies ordering;
H2 creates no second copy or alternate stored path.  Under weak lane fairness
and stopped injection, every pinned head eventually sees its home ready and
every fresh head either retires home or at its one neighbor.  This is exhaustive
for the stated bounded abstract model, not an unbounded formal proof of the
entire N=16 RTL.

## RTL and directed assertion result

The gated H2 rule is implemented in `a9_neighbor_handoff_fabric.sv` as a wrapper
around the unchanged static fabric.  The common binding selects it only with
`A9_NEIGHBOR_HANDOFF`; it cannot become a fallback.  Assertions check stalled
output stability, pinned non-migration, neighbor emptiness, and destination
readiness.  The N=4 RTL test covers the four requested traffic shapes plus
rotating asymmetric stalls and reset.

In the single-stripe asymmetric-stall case, six of eight source-0 events retired
on neighbor lane 1; the final two retired natively after the test released all
lanes for drain.  In the pin counterexample test, a source-0 head was first
presented with all lanes stalled.  Making only neighbor lane 1 ready for five
cycles produced neither migration nor retirement; it retired exactly once when
home lane 0 became ready.  Moving/alternating stalls and all-stripe saturation
then drained without loss, duplication, corruption, or source reorder.  Reset
from occupied/pinned state produced no phantom.

## Static, H2, and same-L centralized comparison

The comparison uses N=16, L=4, 128 fixed stimulus cycles, the same one-entry
source occurrence model, always-ready retire lanes, and drain-after-measurement.
`throughput` counts completions during the 128-cycle measurement window;
accepted/delivered include drain.  The four phase-3 workloads are deliberately
more concentrated than the frozen suite but do not replace it.

| Workload | Implementation | overrun | accepted=delivered | event/cycle | average / max latency |
| --- | --- | ---: | ---: | ---: | ---: |
| single-stripe hotspot | static | 374 | 138 | 0.976562 | 13.514 / 42 |
|  | H2 diffusive | 374 | 138 | 0.976562 | 13.514 / 42 |
|  | centralized L4 | 378 | 134 | 0.976562 | 8.821 / 9 |
| moving hotspot | static | 304 | 208 | 1.523438 | 10.885 / 23 |
|  | H2 diffusive | 304 | 208 | 1.523438 | 10.885 / 23 |
|  | centralized L4 | 336 | 176 | 1.304688 | 7.909 / 9 |
| alternating stripe 0/1 | static | 236 | 276 | 1.945312 | 12.529 / 41 |
|  | H2 diffusive | 236 | 276 | 1.945312 | 12.529 / 41 |
|  | centralized L4 | 246 | 266 | 1.945312 | 8.308 / 9 |
| all-stripe saturation | static | 1,496 | 552 | 3.906250 | 13.514 / 42 |
|  | H2 diffusive | 1,496 | 552 | 3.906250 | 13.514 / 42 |
|  | centralized L4 | 1,512 | 536 | 3.906250 | 8.821 / 9 |

H2 records zero migrations and zero pin-block cycles in all four always-ready
runs, as required by its predicate.  Static and H2 are exactly equal in every
reported value.  The distributed designs sometimes accept more of these short
bursts because they contain more transport storage; this is not extra steady
service bandwidth.  The centralized reference has lower latency and reaches
the same per-active-lane steady completion limit.

Four high-risk frozen traces were also rerun through the common binding.  All
passed with zero correctness errors and zero migrations, exactly reproducing
the static `cd6ba55` numbers: elephant/mouse affine 27 overruns and 0.877441
event/cycle; moving multi-row 9 and 0.883301; rotating-victim affine 2 and
1.027832; uniform-2.00 s2001 11 and 1.991211.  Thus the phase-3 wrapper does not
silently perturb the frozen always-ready result.

## Cost and verdict

H2 adds `L` synthesized pin bits: four bits at N=16 L4.  It adds no mailbox,
source fence, migration toggle, global state, or extra event payload register.
Migration toggle cost is therefore zero bits and a successful direct migration
adds zero registered latency cycles.  The nonzero cost is a local paired-lane
mux plus a neighbor `valid/ready` control path; no physical timing/PPA claim is
made because server tools remain prohibited.  At N=16 L4 the previous 960-bit
static state proxy becomes 964 bits; the local output decision now includes the
paired neighbor predicate but its fan-in remains independent of N.

Verdict: **safe local-handoff variant, rejected as a fixed-stripe imbalance
solution**.  It can recover a fresh head from an asymmetrically stalled output,
but cannot change any always-ready hotspot, alternating-stripe, or saturation
result.  A stronger mid-stripe migration would need stored alternate paths and
per-source route acknowledgment to preserve ordering under arbitrary stalls;
that complexity is outside the accepted minimal A9 protocol.  No global scan,
central encoder, tree, reservoir, remapper, or crossbar was introduced.
