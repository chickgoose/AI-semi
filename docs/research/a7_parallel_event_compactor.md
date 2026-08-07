# A7 GPU-style Parallel Prefix Event Compactor

Status: pre-RTL architecture record, 2026-08-07

## 1. Hypothesis and boundary

A7 tests one narrow hypothesis: an N-bit live-event bitmap can be ranked once
with a shared parallel-prefix network and the first `K` cyclic ranks can be
placed directly into `K` completion slots.  This should sustain more than one
logical event/cycle with less duplicated arbitration logic and less request
wiring than `K` independently masked priority encoders followed by a general
crossbar.

The candidate is a synchronous, one-hop, coordinate-event transport.  It owns
its K one-entry retire buffers and arbitration state.  The normalized binding
is combinational and storage-free.  The frozen mandatory experiment is N=16,
sink-always-ready, one occurrence per source/cycle.  Independent lane stalls
are a separate unit qualification because the common multi-lane stall suite is
not yet frozen.  A2 dual mode, A3 homeostasis, A4 quadtree, A5 prediction, A6
codec, A8 calendar buckets, and A9 token-ring mechanisms are not used.

## 2. Primary-source basis

Blelloch defines all-prefix-sums (scan) as a general parallel primitive and
shows logarithmic-depth tree implementations and compaction-style allocation
uses [CMU-CS-90-190](https://www.cs.cmu.edu/~scandal/papers/CMU-CS-90-190.html).
Merrill and Garland explicitly connect work-efficient GPU scan to in-place
compaction and emphasize sharing prefix propagation rather than repeating
global data movement [NVIDIA Research, 2016](https://research.nvidia.com/publication/2016-03_single-pass-parallel-prefix-scan-decoupled-look-back).
CUDA's official programming guide defines `__ballot_sync` as producing a lane
bitmap and shuffle operations as exchanging values among participating SIMD
lanes; together these are the software analogue of bitmap-to-compacted-lane
selection used here [CUDA Programming Guide](https://docs.nvidia.com/cuda/cuda-programming-guide/).
The published TC-PPA round-robin arbiter independently establishes that
rotating-priority arbitration can be expressed with a parallel-prefix logic
network rather than a linear priority chain [Dimitrakopoulos et al., 2012](https://doi.org/10.1016/j.mejo.2012.04.005).

Those sources motivate the primitive, but the A7 circuit and its fairness proof
below are specific to this repository's handshake contract.

## 3. Scan network and rotation-neutral rank

Let `r[i]` be the pending bit for physical source `i`.  A Kogge-Stone-like
iterative-doubling network computes the inclusive population prefix

```text
P[i] = sum(r[0:i]),       T = P[N-1]
E[i] = P[i] - r[i]       // requests physically before i
```

The state `b` is the first source in cyclic priority order.  Define `B=E[b]`.
No request vector or payload bus is rotated.  The exclusive cyclic rank is

```text
C_b(i) = E[i] - B                 when i >= b
       = (T - B) + E[i]           when i <  b
```

for an active source `i`.  Thus `C_b(i)=0` is the first active source at or
after `b`, wrapping once, and active ranks are exactly `0..T-1`.  Rotation
changes only the small base subtraction/mux layer; the physical prefix wires
and source payload locations remain fixed.

Retire buffers advertise availability `a[l] = !valid[l] || ready[l]`.  A second,
tiny prefix across K bits gives each available physical lane its compacted slot
rank `S[l]=sum(a[0:l))-a[l]`.  Source `i` is accepted iff

```text
r[i] && C_b(i) < A,       A = sum(a[0:K))
```

and available lane `l` refills from the unique source satisfying
`C_b(i)=S[l]`.  A stalled lane is not available and retains its registered
payload.  Other lanes may independently retire and refill.

## 4. Tie, rotation, and output semantics

- A lower cyclic rank wins; physical source number is only a tie-break through
  the current rotation base.  Distinct active bits cannot have equal rank.
- Lane order is compacted available-slot order, not permanent source affinity.
  Among events admitted in the same cycle, ascending lane-slot rank preserves
  cyclic arbitration order.
- Already buffered events keep their lane and may retire independently.  The
  contract imposes source-local order, not a global order between sources.
- After accepting `g>0` events, `b` becomes one past the source with rank
  `g-1`.  With no acceptance the base is unchanged.  Refill after a retire can
  occur on the same edge, so sustained traffic has no bubble.
- A source is represented by one pending bit in the frozen benchmark, so two
  lanes can never select the same event.  The RTL additionally derives exactly
  one `source_ready` bit per selected cyclic rank.

## 5. K scaling and structural cost

For count width `W=ceil(log2(N+1))` and iterative-doubling depth
`L=ceil(log2 N)`, the implemented prefix contains at most

```text
prefix adders = sum(d=0..L-1, max(N-2^d, 0))
              = N*L - (2^L-1)                    for power-of-two N
prefix depth  = L W-bit adders
```

The cyclic-rank layer adds one subtract/add-select level per source.  Selection
uses N comparisons against `A` and rank equality into K destinations.  Payload
data has K N-to-1 mux cones, which are unavoidable for K arbitrary outputs,
but request priority is computed once and fanned to those cones.  A conservative
gate/depth proxy is

```text
G_A7 = W*(N*L-(2^L-1)) + N*(2W+1) + K*N*(W+1)
D_A7 = L + 1 + ceil(log2 N)       // count stages, rank, payload mux
```

versus a replicated-select reference with roughly

```text
G_rep = K*(N*ceil(log2 N) + N) + K*N*DATA_WIDTH
D_rep = ceil(log2 N) + masking dependency (up to K selections)
```

The frozen RTL instantiates the four N=16 iterative-doubling stages explicitly;
the formula states the natural generalized network.  The exact standard-cell crossover is technology dependent and requires the
approved common PPA flow; these expressions are declared pre-result proxies,
not synthesis claims.  K=1, 2, and 4 use the identical scan primitive.  Only
the available-slot prefix, rank comparisons, buffers, and payload muxes scale
with K.

## 6. Correctness and bounded-fairness argument

**Rank uniqueness.** For two active sources in cyclic traversal order, the
exclusive population count increases by one.  The wrap expression preserves
that sequence, hence their cyclic ranks differ.

**No duplicate and at most K.** An accepted source must have rank below `A`,
where `A<=K`.  Exactly one active source owns each present rank and exactly one
available lane owns each slot rank.  Equality connects them bijectively.

**Conservation.** Acceptance is asserted only for a source copied into an
available registered lane on the same edge.  An occupied unready lane is never
overwritten.  A ready occupied lane is either refilled or cleared.  Therefore
each accepted item remains in exactly one buffer until its retire handshake.

**Source-local order.** The common source cannot offer its next event until its
current event is accepted.  Once accepted, that event occupies a lane; any next
event from that source can enter only later.  Under independent lane stalls,
global completion may reorder sources.  To preserve same-source order beyond
the frozen one-entry source model, the environment must not allow a second
same-source acceptance while the earlier lane is stalled; A7's declared native
contract is therefore the frozen one-outstanding-event-per-source contract.

**Fairness bound.** Assume all retire lanes ready, a source remains requested,
and at least one request is accepted each service cycle.  Each nonempty cycle
advances `b` past up to K winners and never skips a persistent request within
the selected cyclic prefix.  At most N-1 other persistent requests precede it,
so it is accepted within `ceil(N/K)` service cycles (N cycles for K=1).  Sink
stall cycles are excluded from this bound because no arbiter can guarantee
service without capacity.

## 7. Experiment and comparison plan

Run the exact frozen 46 JSONL traces for K=1/2/4.  K=1 is the fair reference,
not a fixed-priority strawman.  Report per run and by family:

- fixed-window completed logical events/cycle and K-normalized lane utilization;
- generated, accepted, delivered, source overrun, and complete-drain errors;
- p95/p99 end-to-end latency and request wait;
- demand-normalized delivery fairness, minimum source service ratio, and
  demand-conditioned zero-service windows;
- declared gate/depth proxies, separately from any future measured PPA.

The required cuts are global fan-in, rate-shape B1/B4/B16, simultaneous,
matched spatial pairs, phase overload/recovery, uniform 0.125 through 2.0, and
rotating victim.  Sparse (`<=1` offered event/cycle, especially 0.125) overhead
is reported as latency plus unused-lane/register and proxy cost, not hidden by
peak throughput.

## 8. Break-even and rejection criteria

A7 advances only if all 46 runs have zero transport errors and accepted equals
delivered after drain.  It must show fixed-window throughput above 1 event/cycle
for K>1 on an offered-above-one trace, lower overrun or tail latency than K=1 in
at least two orthogonal high-contention families, and no address-remapping
fairness anomaly.  Sparse p95 latency may increase by at most one cycle (the
explicit retire buffer) and sparse throughput/acceptance may not regress.

Reject the architecture if K=2 cannot reach at least 1.5x K=1 measured
throughput before the offered-load ceiling, if K=4 cannot materially improve
over K=2 on B16/global fan-in/2.0-load traffic, if rotating-victim persistent
wait violates the stated bound in directed tests, or if the declared
logic-plus-register proxy grows as badly as replicated K-way selection.  Final
area/Fmax break-even is deliberately deferred until server PPA is approved.
