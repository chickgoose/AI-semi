# A4 Distributed Quadtree Spatial Event Fabric

Status: pre-RTL architecture contract, 2026-08-07

## 1. Claim and scope

A4 replaces one flat, globally wired AER arbiter with a physical 4-ary tree.
For the frozen 4 x 4, N=16 geometry, four adjacent 2 x 2 source clusters feed
four independent leaf merge nodes and those nodes feed one root merge node.
Every merge owns only a one-event elastic register, a valid/age summary, and a
two-bit round-robin phase.  Arbitration and ready propagation are local to one
parent and its four children; there is no N-way request cone, global grant
vector, row-first/column-second serialization, fovea replication, mode switch,
urgency network, prediction, compression, flat prefix compactor, calendar
wheel, ring, or token.

The innovation being tested is therefore narrow: spatial hierarchy changes
arbitration fan-in, physical wire span, critical-path growth, and the number of
events that can be accepted concurrently into distributed elastic state.  The
root still retires one logical event per cycle, so this candidate does not claim
more than one-event/cycle sustained egress.

## 2. Prior work and the A4-specific transformation

Primary sources and maintained open RTL were consulted, but no external RTL is
copied into A4.

| Source | Relevant result | What A4 keeps | What A4 changes or excludes |
| --- | --- | --- | --- |
| K. Boahen, [“A Burst-Mode Word-Serial Address-Event Link—I: Transmitter Design,” IEEE TCAS-I 51(7), 2004](https://web.stanford.edu/group/brainsinsilicon/documents/04_journ_IEEEtcs_AERChanI.pdf) | Asynchronous AER requests can be decomposed into an arbiter hierarchy; the published transmitter then chooses a row and events in that row. | Hierarchical request reduction and locality-aware physical placement. | A4 is synchronous ready/valid, uses full coordinates through every merge, and expressly does not perform ROW then COL arbitration or row-state burst latching. |
| Z. Su, H. Hwang, T. Torchet, and G. Indiveri, [“Core interface optimization for multi-core neuromorphic processors,” 2023](https://arxiv.org/abs/2308.04171) | A hierarchical arbiter tree with an asynchronous encoding pipeline reduces sparse latency and area relative to compared asynchronous schemes. | Distributed small-fan-in arbiters and per-level transport state. | A4 uses standard synchronous one-entry elastic registers, deterministic RR, and the frozen common clock/interface; it does not reuse their asynchronous circuits or CAM. |
| Y. Wang, S.-Y. Peng, and S. Shah, [“An Ultra-Low-Power Synthesizable Asynchronous AER Encoder for Neuromorphic Edge Devices,” 2026](https://arxiv.org/abs/2604.05313) | A fabricated encoder aggregates events in a hierarchical tree and places arbitration in tree nodes. | Synthesizable node-local arbitration and bundled event identity. | A4 is a clean synchronous baseline with deterministic fairness, not a bundled-data micropipeline or random-priority asynchronous arbiter. |
| P. Purohit et al., [“Field-programmable encoding for address-event representation,” Frontiers in Neuroscience, 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9760944/) | Reviews binary/greedy AER trees and shows workload/topology dependence of spatially clustered readout. | Treat locality dependence as a measured hypothesis, not universal benefit. | A4 has no programmable mode and no token-ring configuration; local, dispersed, row, column, and moving cases all remain mandatory. |
| PULP Platform, [`common_cells`](https://github.com/pulp-platform/common_cells) | Maintained open SystemVerilog provides `cc_rr_arb_tree`, stream arbiters, and spill registers as reusable arbitration/elastic primitives. | RR advances on successful transfer; valid/data remain stable under stall; tree fan-in is bounded. | A4 is independently written, fixed to spatial 4-way merges, carries source age summaries, and places one register at every quadtree node rather than instantiating PULP RTL. |
| A. Forencich, [`verilog-axis/rtl/arbiter.v`](https://github.com/alexforencich/verilog-axis/blob/master/rtl/arbiter.v) | Open RTL demonstrates parameterized fixed/RR arbitration with handshake-aware grant update. | Update fairness phase only on an actual transfer. | A4 neither imports this module nor uses a flat N-port arbiter; every phase applies to only four spatial children. |

The older asynchronous tree literature normally sends a request up and a grant
back down, or reuses a token near a common ancestor.  A4 instead sends an event
up through registered elastic merges.  This makes accepted-event ownership and
backpressure explicit under the clean-slate synchronous contract and puts all
storage inside the PPA boundary.

## 3. N=16 topology

Coordinates use `source = 4*y + x`.  Physical grouping, not numeric priority,
defines the leaves:

```text
leaf q0: (0,0) (1,0) (0,1) (1,1) --\
leaf q1: (2,0) (3,0) (2,1) (3,1) ----\
leaf q2: (0,2) (1,2) (0,3) (1,3) ----- root ---- retire lane 0
leaf q3: (2,2) (3,2) (2,3) (3,3) ----/
```

Each source has one fixed path: source -> its 2 x 2 leaf -> root -> retire.
Four leaf registers allow up to four spatially dispersed events to be accepted
in one cycle.  Multiple simultaneous sources in one leaf remain asserted in the
common one-entry source latches and are accepted in leaf RR order; they are not
dropped or hidden in a TB queue.

## 4. Merge-node protocol

A node has four child channels and one registered parent channel.  A child
channel contains `valid`, event payload, source identity, and a saturating age
summary; the reverse channel is `ready`.

1. `out_valid && !out_ready` holds event, source, and age stable.
2. The node may replace an empty slot or a slot transferred to its parent on the
   same clock edge.  This is the back-to-back refill rule.
3. Among valid children, selection starts at the local two-bit phase and wraps.
   The phase advances to one past the transferred child only when a transfer
   into this node occurs.
4. Exactly the selected child sees `ready`.  No child is acknowledged unless
   its payload is captured on that edge.
5. A captured age is incremented saturating once per tree hop and then held
   stable with its event.  It is a local tree-depth summary, not neural urgency
   or an occurrence timestamp.  The clean candidate deliberately does not use
   age to override RR, preserving a simple bounded-progress proof.
6. Reset clears valid, age, and phase, preventing phantom retirement.

Leaf nodes map their four fixed physical source positions into the same child
protocol with incoming age zero.  The root is the identical merge primitive;
its output register directly drives retire lane 0.  Unused retire lanes are
invalid.  Always-ready is the mandatory qualification mode, but the node
protocol remains stable under root backpressure.

## 5. Ordering, conservation, and progress arguments

### Same-cycle multiple occurrence preservation

For every asserted source, either its leaf raises ready and captures exactly its
event, or ready remains low and the frozen source latch holds valid/event.  At
most one child per leaf transfers, but four leaves act independently.  Thus a
16-way occurrence accepts up to four events on its first edge and preserves the
other twelve at their sources.  There is no overwrite: a full node is writable
only if its old item transfers on the same edge.

### Tree-level and source-local ordering

A transferred item moves at most one registered level per clock.  Within a
fixed source path, every level is a one-entry FIFO with simultaneous pop/push;
a later event cannot pass an earlier event in either the leaf or root.  Because
the common source model exposes no second event from one source until its first
event is accepted, source-local completion order follows by induction over the
two merge levels.

### Back-to-back refill

If the root retires event A in cycle t and selects leaf event B, its register
captures B on that same edge.  If that selected leaf simultaneously selects
source event C, its register captures C on the same edge.  Consequently a full
pipeline can retire one event every cycle without a bubble.

### Bounded progress

With continuous root readiness, a valid leaf wins the root within at most four
root transfers because root RR cannot choose the same child again before all
other continuously valid children.  A continuously valid source wins its leaf
within at most four leaf admissions.  Since a continuously busy leaf receives
at least one admission opportunity per four root transfers, occurrence-to-leaf
acceptance is bounded by 16 root-transfer opportunities, followed by at most
four transfers to root and one retirement edge.  The conservative synchronous
bound is 21 service edges after the request is continuously visible.  This is a
functional liveness bound, not a latency promise under sink backpressure; an
unbounded sink stall necessarily removes bounded progress.

## 6. State and storage count

Let payload identity width be `P = ADDR_WIDTH + SOURCE_WIDTH`, age width `A`,
and radix `R=4`.  One merge node stores:

```text
slot valid             1 bit
slot event/source      P bits
slot age               A bits
RR phase               log2(R) = 2 bits
node total             P + A + 3 bits
```

For N=16, `ADDR_WIDTH=16`, `SOURCE_WIDTH=4`, and `A=8`, five nodes (four
leaves plus root) use `5*(20+8+3) = 155` architectural state bits.  This count
includes the root output slot and all arbitration history, and excludes only
the frozen TB source latches.  A flat one-slot RR reference needs approximately
`P + 1 + log2(N) = 25` state bits but has no distributed burst absorption.

For a full R-ary tree with N sources and a registered merge at every internal
node, internal-node count is `(N-1)/(R-1)`.  A4's N=16 physical grouping uses a
two-level pruned tree with five internal nodes, equal to `(16-1)/3`.  Total node
state therefore scales O(N), while state per node and arbitration fan-in remain
constant.

## 7. Latency and scaling formulas

For `N=4^L`, the number of registered merge levels is `L = log4(N)`.  With no
queueing, an event accepted at a leaf-facing edge reaches retirement after L
merge captures; under the common cycle accounting the minimum internal latency
is approximately L cycles (plus any observation-edge convention in the TB).
For k events ahead on its path, latency is approximately `L+k` at a one-event/
cycle root.

Worst-case persistent-request service opportunity is bounded by `4^L = N`
root service edges under nested RR, plus L pipeline/retirement edges.  This
bound is intentionally conservative.  Queueing throughput is still capped at
one completion/cycle, but ingress can accept up to `N/4` events/cycle when leaf
slots are empty or refilling.

The combinational choice depth is O(log R) per registered level rather than an
unregistered O(log N) priority/encode cone.  The architectural pipeline depth
grows O(log4 N).  Register and small-arbiter area grows O(N).

## 8. Physical wire/PPA hypotheses

These are pre-PPA hypotheses, not measured results:

- Flat arbitration brings N request wires and a grant/ready decode across the
  source array.  A4 confines source wires to a 2 x 2 neighborhood and sends one
  payload/valid/ready channel per occupied quadtree edge.
- If a square N-source array has side sqrt(N), flat request-to-center aggregate
  Manhattan span grows approximately O(N*sqrt(N)); recursively placed tree
  control span is expected to grow O(N log4 N) bit-distance with bounded local
  fanout.  Exact routed capacitance can reverse a simplistic proxy result
  because A4 transports a payload and age at each level.
- Critical combinational fan-in should remain four and long paths should end at
  each node register, improving Fmax scaling.  Additional registers and clock
  load may increase area and sparse dynamic/leakage power at N=16.
- The report will publish two auditable pre-layout proxies: maximum arbitration
  fan-in/logic levels and sum over tree edges of `(channel bits * Manhattan
  span)`.  These proxies are not substituted for Genus/Innovus evidence.

Server PPA is forbidden until head approval.  No synthesis or P&R result will
be claimed in the initial regression report.

For the N=16 paper proxy, count a source-to-leaf Manhattan span of 1 grid unit
and a leaf-to-root span of 2.  With 20 identity bits, 8 age bits, valid, and
ready, A4's full-channel proxy is `16*30*1 + 4*30*2 = 720 bit-grid`; the flat
source-to-center proxy is `16*21*2 + 16*1*2 = 704 bit-grid`.  A4 is therefore
2.3% worse at N=16 under this deliberately simple full-data proxy.  The
control-only request/ready proxy is `48` versus flat `64 bit-grid` (-25%).
This split is important: bounded control wiring is the hypothesis, while
repeated registered payload transport is a real cost.

## 9. Permutation neutrality policy

RR phase depends only on successful local transfers, never on payload address
bits, neural urgency, predicted traffic, or a privileged global source number.
All four children of every node execute the same wrap rule.  Rotation by traffic
history is therefore address-independent.

The physical quadtree is intentionally not neutral to arbitrary coordinate
permutations: a permutation can move mutually active sources into or out of the
same leaf.  That is the spatial hypothesis under test and must be disclosed.
Identity, mirror, rotate/affine pairs quantify unintended numeric priority;
matched local/dispersed and row/column/dispersed cases quantify intended
topology dependence.  If mirror or affine pairs differ after controlling for
which sources share a leaf, that is a failure of the arbitration policy.  A
per-level fixed phase offset may be evaluated only if this occurs; it must not
depend on addresses or workload classification.

## 10. Mandatory measurements and failure conditions

The A4 regression must run all 46 frozen N=16 traces, including the explicitly
required families: matched local/dispersed, row/column/dispersed multi-hotspot,
moving hotspot, global fan-in, rate shape, rotating victim, and the uniform load
sweep.  A flat RR one-slot reference uses the exact same generated JSONL SHA.

For every run report correctness, generated/accepted/delivered/overrun,
measurement event/cycle, p95/p99 end-to-end latency, demand-normalized fairness,
minimum service ratio, and maximum wait.  Architecture summaries additionally
report 155 state bits, two registered logic levels, maximum fan-in four, and the
declared wire proxy.  Local wins and dispersed/row/column losses are all kept.

A4 fails eligibility on any accepted-event loss, duplicate, phantom, corruption,
source-local reorder, unstable stalled output, or incomplete drain.  It fails
its architectural claim if the implementation contains a hidden flat N-way
arbiter, free binding storage, ROW->COL serialization, or one of the forbidden
A2/A3/A5/A6/A7/A8/A9 mechanisms.  It also fails the research hypothesis if
balanced physical evidence eventually shows no useful wire/timing scaling gain,
or if topology bias causes an undisclosed severe regression outside the spatial
case it favors.
