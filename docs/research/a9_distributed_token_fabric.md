# A9 Arbiterless Distributed Token/Empty-Slot Event Fabric

Status: protocol-first research note, 2026-08-07

## 1. Scope and benchmark boundary

A9 replaces a flat request vector and global priority encoder with a transport
whose storage availability is itself the distributed arbitration mechanism.  It
does not reuse the A2 reservoir mode switch, A3 homeostasis, A4 quadtree, A5
predictor, A6 codec, A7 prefix scan, or A8 calendar wheel.  The frozen logical
event, one-entry common source latch, trace, scoreboard, golden metadata, and
always-ready N=16 measurement boundary remain unchanged.

All queueing below is synthesizable candidate state.  The common binding is a
wire-only mapping.  The first implementation deliberately supports the frozen
always-ready suite and four completion lanes.  Independent lane backpressure,
polarity/type, asynchronous ingress, multicast, and more than one occurrence
per source per cycle are not claimed by that result.

The four retire stripes are an implementation point, not the novelty claim.
A9 is not A7's global K-lane prefix scan/compactor: it never computes a global
selected set and never dynamically packs arbitrary winners into K output lanes.
Every source remains on one fixed physical stripe and an event advances only by
neighbor credit/empty-slot transfers.  The claimed contribution is removal of
the central grant vector, conservation of locally propagated slots, bounded
local fan-in, and the resulting wire/critical-path scaling.

## 2. Prior art and the exact borrowing boundary

| Primary source or open design | URL | Borrowed point | A9 difference |
| --- | --- | --- | --- |
| Carloni, *Latency-Insensitive Design* | https://www2.eecs.berkeley.edu/Pubs/TechRpts/2004/4238.html | valid/stop channels, relay storage, and correctness independent of inserted channel latency | A9 uses empty transport capacity to select ingress; it is not only a wrapper around a pre-selected stream |
| Collins and Carloni, *Topology-Based Optimization of Maximal Sustainable Throughput in a Latency-Insensitive System* | https://www.cs.columbia.edu/~rlc2119/papers/DAC-2007-CC.html | topology and queue depth determine recycling/throughput; extra registers alone do not guarantee full rate | A9 fixes a feed-forward striped topology and makes queue depth and bubble return explicit rejection metrics |
| Bhuyan, Ghosal, and Yang, *Approximate Analysis of Single and Multiple Ring Networks* | https://doi.org/10.1109/12.30853 | multiple walking tokens/slots trade latency against throughput and decentralize control | A9 uses multiple feed-forward elastic stripes instead of a closed LAN ring, avoiding token-loss recovery state and fixed-ring hogging |
| Teruya and Shiratori, *Evaluation of transmission control method in a slotted ring network* | https://search.ieice.org/bin/summary.php?id=e78-a_11_1519 | a vacant slot can be captured locally, but unrestricted capture can hog the ring; fixed ownership restores fairness at a throughput cost | A9 uses a one-bit local contention toggle and fixed source-to-stripe mapping, not fixed per-slot ownership |
| Nikolic et al., *Distributed arbitration scheme for on-chip CDMA bus with dynamic codeword assignment* | https://onlinelibrary.wiley.com/doi/full/10.4218/etrij.2020-0016 | simple registered ring elements, local short connections, and several synchronous tokens remove a central arbitration block | A9 tokens are empty FIFO entries/credits carrying events, not a separate arbitration phase or CDMA code allocator |
| Wu, *A Router for a Massively Parallel Computer System* (SpiNNaker router dissertation) | https://apt.cs.manchester.ac.uk/ftp/pub/apt/theses/Wu10_phd.pdf | small independent neural packets benefit from pipelining; idle pipeline stages should avoid switching; store-and-forward avoids flit-dependent deadlock | A9 is a one-hop source collector with no routing table, multicast, packet drop timeout, or adaptive routing |
| BaseJump STL open RTL | https://github.com/bespoke-silicon-group/basejump_stl | parameterized ready/valid FIFOs and compositional network blocks are useful implementation references | no BaseJump RTL is copied; A9's cell is a purpose-written two-producer, one-consumer event cell |
| verilog-axis open RTL | https://github.com/alexforencich/verilog-axis | stable ready/valid payload under stalls and skid/pipeline register discipline | no AXI module or centralized `arb_mux` is used; arbitration remains physically inside each A9 cell |

The old slotted-ring results are an explicit warning: “first station after the
release point” capture is work-conserving but can be unfair.  A9 therefore does
not implement a unidirectional ring with one fixed release point.  It uses
short feed-forward stripes and bounds the number of local merge points crossed.

## 3. Topology

For `N = L * D` sources, A9 has `L` independent output stripes and `D` small
cells per stripe.  N=16 uses `L=4`, `D=4`.  Adjacent stripes reverse their
physical source order (serpentine placement), reducing a uniform left/right
layout bias without changing source identity.

```text
source local latch (common TB)
          |
          v
  [one-entry ingress]              source-local state
          |
upstream -> [two-slot cell FIFO] -> downstream -> ... -> retire lane
              ^         |
              |         +-- one-bit local upstream/ingress toggle
              +-- empty entry is the admission token
```

Each cell has only two possible producers: its upstream neighbor and its local
ingress register.  A free FIFO entry is an empty slot/token.  If both producers
want the same entry, a one-bit toggle alternates them.  There is no request
vector, global grant, source-number priority encoder, prefix computation, or
mode switch.  Backpressure/credit crosses one cell boundary; event data crosses
one cell boundary per transfer.

The source-to-stripe mapping is static.  Consequently all events of one source
take one ordered FIFO path and cannot overtake.  Parallelism comes from stripes,
not from sending consecutive events of one source down paths of unequal length.
There is deliberately no global remapper, crossbar, work-stealing compactor, or
dynamic lane assignment to conceal stripe imbalance.

## 4. Cell transition and work conservation

State per cell is:

- one valid bit plus event for the local ingress register;
- a two-entry FIFO containing `{event, source}` plus a 0/1/2 occupancy count;
- one contention toggle.

On every cycle a nonempty FIFO head transfers if the downstream cell advertises
space.  If the local FIFO has space, it accepts exactly one of upstream and
local ingress whenever either is valid.  If both are valid it chooses the side
selected by the toggle and flips the toggle after that contested transfer.
The local ingress can be consumed and replaced by a newly accepted common-TB
event on the same edge.

The first RTL uses conservative registered credit: a full two-entry cell does
not promise space merely because it may dequeue on the same edge.  This breaks
the global combinational ready chain and can create one bubble while a full
region first drains.  After every cell on a saturated path reaches occupancy
one, simultaneous dequeue/enqueue sustains one event/cycle per stripe.

“Work-conserving” is therefore defined precisely at the cell boundary: a cell
with a free entry never idles when either legal producer is valid, and a retire
lane never idles when its final FIFO is nonempty.  It does not mean that a free
entry teleports across several cells in one cycle.

## 5. Conservation and token/slot invariants

Let `A(t)` be accepted source handshakes, `R(t)` completed output handshakes,
`I(t)` the number of valid local ingress registers, and `Q(t)` the number of
occupied transport entries after edge `t`.

The main invariant after reset release is:

```text
A(t) - R(t) = I(t) + Q(t)
0 <= I(t) <= N
0 <= Q(t) <= 2N
```

Equivalent empty-slot conservation is `E(t) = 2N - Q(t)`.  A transport move
decrements one FIFO occupancy and increments the adjacent one on the same edge,
so it neither creates nor destroys an occupied slot.  Injection changes
`I--, Q++`; common acceptance changes `I++`; retirement changes `Q--, R++`.
Simultaneous replacement preserves the same equation.

Required assertions/checks are:

1. FIFO occupancy is always 0, 1, or 2.
2. dequeue implies a nonempty FIFO; enqueue implies advertised capacity.
3. at most one producer is accepted by a cell per cycle.
4. a stalled head retains valid, event, and source.
5. each common source acceptance increments the global outstanding count once;
   each retirement decrements it once, never below zero.
6. after injection stops and the sink remains ready, all valid bits eventually
   clear.

These rules prevent token/event loss and duplication without a separate token
manager.

## 6. Deadlock, livelock, and ordering arguments

The transport graph of each stripe is a finite directed acyclic path.  With its
retire lane ready, choose the occupied entry closest to the sink.  It either
retires immediately or all entries closer to the sink retire/move first.  Thus
the chosen event's distance strictly decreases in finite time.  Repeating the
argument drains every accepted event.  There is no cyclic channel dependency,
so no protocol deadlock is possible in the mandatory always-ready contract.

Local arbitration cannot livelock a continuously valid producer.  At one cell,
when both producers remain valid, the toggle grants each at least once in two
available-entry opportunities.  An upstream event crosses only `D-1` such
merges.  This is a finite bound, although service share can shrink geometrically
with path depth; the N=16 `D=4` choice and rotating-victim result must expose
whether that bound is practically acceptable.  A future version is rejected if
any demanded source has zero delivered service during a demand-conditioned
window despite a continuously ready sink.

Source-local ordering is structural: a source has one ingress FIFO, one fixed
stripe, and FIFO-preserving cells.  Different sources are intentionally allowed
to reorder.  No TB-only sequence ID enters the RTL.

## 7. Reset and recovery

Asynchronous active-low reset clears every ingress-valid bit, FIFO occupancy,
payload register, and toggle.  Therefore reset creates exactly `2N` empty
transport slots and no valid event.  No explicit token seeding is needed, so
there is no zero-token or duplicate-token reset state.  Traffic accepted before
reset is discarded by the specified reset boundary and must not appear as a
post-reset phantom.  The current common suite does not qualify mid-traffic reset;
cell unit tests must still exercise reset from occupancy 0, 1, and 2 and verify
quiet recovery.

## 8. Latency, throughput, and hotspot scaling

For a source at cell position `p` in a stripe of depth `D`, uncontended internal
latency is approximately one ingress-register cycle plus `D-p` transport cycles.
Thus sparse latency is `O(D)`.  A consumed empty slot becomes visible as credit
one cell per cycle; worst-case empty-slot return/circulation latency is `D`
cycles, not `N`.

Each stripe retires at most one event/cycle, so peak logical throughput is `L`
event/cycle and total in-candidate storage is `3N` events (one local plus two
transport entries per source).  With square geometry choose `L=D=sqrt(N)`:

```text
peak throughput       = sqrt(N) event/cycle
worst empty-slot RTT  = sqrt(N) cycles
sparse path depth     = O(sqrt(N))
cell count/state      = O(N)
```

A one-source hotspot is limited by the source contract to one occurrence/cycle
and can sustain one event/cycle once its stripe is flowing.  It does not wait
for a ring token to make an N-source circuit.  Several hotspots scale until they
collide on the same stripe.  Moving-hotspot and row/column/dispersed controls are
therefore mandatory disclosure, not an optimization-training input.

Token utilization is reported as `occupied transport entries / (2N)` and as
`retire transfers / available lane-cycles`.  High occupancy with low retire
utilization indicates bubble/merge inefficiency rather than useful buffering.

## 9. State, wire, and PPA proxy model

Let address width be `A` and source width `S=ceil(log2(N))`.  Counting the RTL
registers exactly (but not clock/reset implementation overhead), A9 bits are:

```text
B_A9 = N*(1 + A)                    local ingress
     + 2N*(A + S)                   transport payloads
     + N*(2 + 1)                    occupancy count and toggle
     = N*(4 + 3A + 2S)
```

Nearest-neighbor internal channel bits are approximately
`(N-L)*(A+S+2)` plus local credit.  The external normalized output is
`L*(A+S+2)` bits.  Long data wires are limited to one cell pitch; no net has
request-vector fanout N.

A flat one-lane central baseline has peak one event/cycle and needs N requests,
an N:1 address/source selection network, and grant return.  Its logical mux
input is `N*(A+S)` bits and its decision depth is O(N) for a priority chain or
O(log N) for a tree, with global placement wires.  A9 pays explicit O(N*A)
register area and L output pins to turn that global selection into local flow.
Comparisons must therefore report both event/cycle and event/pin-cycle; A9 is
not allowed to call lane widening free.

Before approved server PPA, report these reproducible proxies at N=16, 64, 256:
registered bits, local channel bits, retire pins, maximum cell depth, local
two-input arbitration count, and peak logical events/cycle.  No server Genus or
Innovus run is authorized in this phase.

## 10. Rejection criteria

Reject or redesign A9 if any of the following occurs:

- any mandatory trace has a phantom, duplicate, corrupt, reordered, or missing
  accepted event after drain;
- a reset occupancy case creates a post-reset retirement;
- a cell assertion finds occupancy overflow/underflow or two accepted producers;
- all-ready traffic can deadlock or a continuously demanded source has no
  finite service;
- uniform sustained throughput does not exceed the one-lane central baseline
  while paying four retire lanes;
- local versus dispersed, row versus column, affine relabeling, or moving
hotspot produces an unexplained correctness difference or severe service
  collapse;
- the apparent improvement against a one-lane baseline disappears against a
  centralized reference with the same `RETIRE_LANES`, or A9 L=1 fails to show
  the expected wire/fan-in/critical-path distinction against a central L=1
  reference; simple four-lane bandwidth is not an A9 result;
- fixed source-to-stripe mapping produces hotspot, row/column, or affine-
  permutation imbalance beyond the disclosed threshold; this must trigger a
  rejection/redesign discussion, not a hidden global remap or crossbar;
- p99 sparse latency or post-overload recovery grows without the predicted
  `O(sqrt(N))` path bound;
- proxy or later approved PPA shows that local registers/wires cost more than
  the throughput gain under identical pin normalization; or
- the implemented critical control path silently becomes a flat request scan,
  global priority encoder, or central RR fallback.

## 11. Planned evidence sequence

1. Cell-directed tests plus assertions for empty/full, simultaneous producers,
   replacement, stall stability, reset at every occupancy, and bounded toggle.
2. Small fabric conservation/order tests, then the storage-free N=16 binding.
3. Frozen 46-trace correctness and metrics: global fan-in, matched spatial,
   moving hotspot, rotating victim, phase transition, rate shape, uniform sweep,
   sparse, elephant/mouse, retrigger, and timing pairs.
4. Compare A9 L=4 with a centralized L=4 reference under identical retire-lane
   pins, and A9 L=1 with centralized L=1.  Separately report raw throughput,
   throughput/lane, utilization/lane, and event/pin-cycle so no K-lane gain is
   attributed to distributed arbitration.
5. Report per-stripe offered/accepted/delivered load and the max/min stripe
   ratio for stationary/moving hotspots, row/column layouts, and affine source
   permutations.  Do not repair an unfavorable result with global remapping.
6. Compute analytic N=16/64/256 state/wire/throughput and control-path scaling.
   Physical server PPA remains prohibited until head approval.

The completed N=16 evidence and current rejection assessment are in
[`a9_distributed_token_fabric_results.md`](a9_distributed_token_fabric_results.md).

## 12. Optional physical handoff boundary

The final local gate at `e571e67` rejects A9 for the always-ready shortlist.
The only preserved follow-ups are a static N=64 timing-first experiment and an
H2 N=64 persistent-asymmetric-stall experiment.  They are separate immutable
capabilities, never an N=16 default shortlist.  Their candidate-owned tops,
locks, eligibility gate, and fail-closed physical preflight are specified in
`docs/research/a9_optional_physical_handoff.md`.  That handoff does not authorize
a server run or a common-flow modification.
