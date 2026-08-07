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
