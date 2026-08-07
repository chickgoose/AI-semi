# A2: Adaptive Dual-Path Sparse Bypass + Burst Reservoir

Status: architecture hypothesis and falsification plan, 2026-08-07

## Scope and benchmark contract

A2 is a clean-slate, synchronous, one-hop AER transport candidate. It consumes
the common one-entry-per-source pending interface and produces normalized
logical completions. The frozen traces, source model, scoreboard, golden SHA,
and workload meaning remain unchanged. No TB adapter supplies storage,
arbitration, retry, decoding, or ordering state. All state described below is
synthesizable candidate state and belongs inside the PPA boundary.

The mandatory target is N=16, one logical completion lane, sink always ready.
The event payload contains only the normalized address; the source index is
transport identity required by the common completion seam. Occurrence time and
`tb_only_event_id` never enter the RTL.

## Bottleneck hypothesis

A conventional always-queued transport pays a register/FIFO and arbitration
latency even for the dominant isolated-spike case. A pure combinational bypass
avoids that tax but cannot absorb a correlated burst and is vulnerable to a
phase transition: source latches remain occupied, same-source refires overrun,
and tail latency grows. A large always-active FIFO absorbs bursts but costs
state, switching energy, and sparse latency.

The falsifiable hypothesis is that AER traffic has two operational regions that
deserve different physical data paths:

1. when the reservoir is empty and contention is absent, an event can be
   selected and completed directly without a queue write/read;
2. when simultaneous demand, positive occupancy slope, or retained occupancy
   signals a burst, a small two-bank reservoir can accept up to two queued
   events per cycle while one older event retires.

Occupancy alone is a lagging indicator. A2 therefore also observes immediate
fan-in and the sign of the reservoir occupancy change. Separate enter/exit
conditions plus a quiet dwell prevent sparse/burst mode thrashing.

## Proposed structure

```text
source pending latches
        |
        +--> rotating selector 0 --> direct sparse bypass --> retire lane 0
        |
        +--> rotating selectors 1/2 --> interleaved 2-bank reservoir --+
                                                                    |
                                     oldest global read pointer -----+

mode inputs: valid population, occupancy, occupancy delta, quiet dwell
```

The reservoir is one logical FIFO implemented as two interleaved banks. Global
read/write pointers define total order. Consecutive enqueue slots always land
in different banks, permitting two writes in one cycle without a multi-write
single array. This is not two independently arbitrated queues: bank choice is a
pointer bit, and retirement is strictly global FIFO order.

Sparse operation is queue-free. If occupancy is zero and the sink is ready, the
first rotating selection drives the completion seam combinationally and is
accepted on the same edge. If more requests are present on that edge, up to two
additional rotating selections are written behind the bypassed event. Thus a
burst can trigger reservoir use immediately rather than waiting for the mode
register to change.

When occupancy is nonzero, the head entry exclusively owns the retire path.
Up to two new sources can be accepted into free tail slots concurrently. A pop
in the same cycle counts as available capacity. The selection base advances
past the last accepted source, avoiding a fixed source-number preference.

### Mode controller

`burst_mode` enters when any of these is true:

- instantaneous valid population is at least two;
- occupancy reaches the high-water enter threshold; or
- occupancy has increased since the prior sample.

It exits only when occupancy is at or below a lower exit threshold, occupancy
is not increasing, instantaneous fan-in is below two, and those conditions
hold for `QUIET_CYCLES` consecutive cycles. The data path remains correctness
safe independently of the hint: nonzero occupancy always forces queued-head
retirement, while immediate fan-in can enable reservoir writes before the
registered mode changes.

## State and storage

For N sources, address width A, reservoir depth D (even power of two), and
source width S=`ceil(log2(N))`, planned state is:

- reservoir payload: `D * (A + S)` bits;
- read pointer, write pointer: `2 * log2(D)` bits;
- occupancy: `log2(D+1)` bits;
- rotating selection base: S bits;
- previous occupancy: `log2(D+1)` bits;
- burst mode and quiet counter: `1 + ceil(log2(QUIET_CYCLES+1))` bits.

For N=16, A=16, D=8, and quiet dwell 3, this is 160 payload bits and
21 control bits (181 explicit state bits total). No per-source
queue, predictor table, neural score, timestamp, sequence tag, or replay state
is present.

## Correctness invariants

1. **Acceptance ownership:** every asserted `source_ready[s]` corresponds to
   exactly one of direct bypass, enqueue slot 0, or enqueue slot 1; the three
   selections are mutually exclusive.
2. **Capacity:** queued acceptance count never exceeds free slots including a
   same-cycle pop; occupancy remains in `[0,D]`.
3. **Single retirement owner:** occupancy zero permits only direct retirement;
   occupancy nonzero permits only reservoir-head retirement.
4. **No bypass overtake:** direct bypass is legal only when occupancy is zero.
   Therefore it cannot pass an older accepted queued event.
5. **Global queued order:** enqueue slots receive consecutive write-pointer
   positions and retirement uses consecutive read-pointer positions.
6. **Source-local order:** the common boundary exposes at most one pending event
   per source; after an event is accepted, a later event from that source can
   only enter the tail. It cannot use bypass while any older queued event exists.
7. **Conservation:** each accepted direct event retires on its acceptance edge;
   each accepted queued event increments occupancy and exactly one successful
   queued retirement decrements it. Simultaneous increments/decrement are
   algebraically combined once.
8. **Stall stability:** mandatory results use an always-ready sink. If optional
   sink backpressure is later enabled, a queued head is naturally stable; the
   direct path must be registered or admission-frozen before claiming that
   optional capability. A2 initially declares it unsupported.
9. **Reset/quiet:** reset clears occupancy, pointers, and mode state; no storage
   valid bit exists outside occupancy, so stale payload cannot retire.

The self-checking evidence must exercise simultaneous direct+enqueue,
enqueue+dequeue, full-boundary replacement, wraparound, source-local refire,
mode hysteresis, reset, complete drain, and post-drain quiet behavior. Common
scoreboard evidence then checks no loss, duplicate, corruption, phantom, or
reordering on frozen traces.

## Why this is innovative rather than a renamed FIFO

The innovation claim is narrow: the accepted event's physical path changes
with measured load state. An isolated event neither writes nor reads the
reservoir. Burst traffic gains multi-source admission into a finite banked
reservoir, while the mode detector uses both level and derivative with
hysteretic recovery. The reservoir is not the universal path and the bypass is
not merely an empty-FIFO combinational optimization; immediate fan-in can
retire one event and bank two more on the same edge, and registered load state
controls how aggressively the burst path remains armed.

This does not change the problem into FIFO+round-robin, prediction, learned
scoring, spatial trees, compression, K-lane prefix compaction, a calendar wheel,
or a token ring. Rotation is only a bounded tie-breaker around the core adaptive
data-path mechanism.

## Non-overlap with A3--A9

The sibling tracks are independent clean-slate explorations and their final
implementations are not assumed here. A2's identity is constrained so it cannot
silently absorb likely orthogonal mechanisms: it has no predictor, neural
policy, quadtree/locality hierarchy, codec/packing format, prefix lane
compactor, time-slot/calendar scheduler, or distributed token protocol. It
also does not claim multi-hop adaptive routing. If a sibling uses one of those
mechanisms, A2 remains distinguishable by zero-queue sparse retirement plus a
derivative/hysteresis-controlled banked burst reservoir.

## Prior work, ideas taken, and deliberate differences

The sources below are primary papers or official publisher/author copies.

- Lazzaro et al., *Silicon Auditory Processors as Computer Peripherals*, NIPS
  1992, <https://papers.neurips.cc/paper_files/paper/1992/file/3493894fa4ea036cfc6433c3e2ee63b0-Paper.pdf>.
  It establishes event-address communication: transmit identity at spike onset
  and rely on bounded/constant communication latency rather than payload
  timestamps. A2 preserves address-only transport but explicitly measures the
  latency distribution; it does not assume constant delay.
- Boahen, *Point-to-Point Connectivity Between Neuromorphic Chips Using
  Address Events*, IEEE TCAS-II 47(5), 2000,
  <https://doi.org/10.1109/82.842110>. It analyzes AER bandwidth, arbitration,
  queueing, clustered activity, pipelining, and parallel readout. A2 does not
  reproduce its asynchronous arbiter tree or row/column architecture; it uses
  a flat normalized synchronous boundary and switches between bypass and
  finite burst absorption.
- Chen et al., *SMART: A Single-Cycle Reconfigurable NoC for SoC
  Applications*, DATE 2013,
  <https://people.csail.mit.edu/suvinay/pubs/2013.smart.date.pdf>. SMART bypasses
  intermediate mesh routers using special repeated links and route
  reconfiguration. A2 borrows only the principle that bypassing storage can
  protect zero-load latency; it has no mesh, hop reservation, or special link.
- Guo et al., *A Bypass-Based Low Latency Network-on-Chip Router*, IEICE ELEX
  16(4), 2019, <https://doi.org/10.1587/elex.16.20181147>. BNR forwards
  non-conflicting flits around a conventional VC router. A2 instead adapts an
  AER fan-in endpoint, allows direct retirement only when no queued predecessor
  exists, and couples the bypass to derivative/hysteretic burst admission.
- Michelogiannakis, Balfour, and Dally, *Elastic-Buffer Flow Control for
  On-Chip Networks*, IEEE TC 62(2), 2013,
  <https://doi.org/10.1109/TC.2011.237>. Elastic buffers reuse channel pipeline
  registers as distributed FIFO storage and derive congestion from channel
  occupancy. A2's reservoir is local explicit PPA state, not distributed link
  storage; occupancy delta and hysteresis choose a data path rather than a NoC
  route.
- Michelogiannakis et al., *Evaluating Bufferless Flow Control for On-Chip
  Networks*, NOCS 2010, <https://doi.org/10.1109/NOCS.2010.10>. Its results warn
  that removing buffers can lose throughput/power efficiency outside very light
  load. That is a motivation for A2's finite reservoir rather than evidence that
  pure bypass is sufficient.
- Hu et al., *Dynamical Hysteresis Phenomena in Complex Network Traffic*,
  Physical Review E 79, 047101, 2009,
  <https://doi.org/10.1103/PhysRevE.79.047101>. It shows that network recovery
  can follow a different path from congestion onset. A2 uses an engineering
  Schmitt-style enter/exit separation and quiet dwell; it does not reproduce
  that paper's network model.
- Jimenez-Fernandez et al., *Pipeline AER Arbitration with Event Aging*,
  conference repository copy,
  <https://rodin.uca.es/handle/10498/33925>. It permits continuous cells and
  discards aged petitions under congestion. A2 explicitly rejects that semantic:
  every accepted event must drain, with no aging discard.

No cited circuit is copied. The combination, ordering boundary, and mode logic
will be implemented from the clean benchmark contract.

## PPA and timing risks

- Sparse bypass places rotating select, address mux, and ready/retire logic in
  one cycle. It may improve cycle latency while worsening Fmax.
- Two enqueue selections and two bank write decoders increase combinational
  fan-in and clocked switching during bursts.
- Small D may merely delay overload; large D may reduce overruns at the cost of
  bufferbloat-like p99 latency and area.
- Occupancy counters and mode logic could cost more than the activity saved for
  N=16.
- A single retire lane caps sustained throughput at one event/cycle; reservoir
  advantage should appear mainly as burst absorption, lower overrun, and phase
  recovery, not a false claim of >1 sustained completion/cycle.
- Direct combinational retirement is intentionally not claimed safe under
  optional sink stalls in the first implementation.

State-bit count, mux depth, comparator/priority depth, and inferred storage are
reported as PPA proxies. Genus/Innovus are not run before head approval.

## Failure and rejection criteria

A2 is rejected or reduced if any of these occurs:

- any mandatory trace has `errors != 0` or accepted does not equal delivered
  after drain;
- directed tests find duplicate selection, occupancy overflow/underflow,
  wraparound corruption, bypass overtake, or mode-dependent ordering;
- sparse identity latency is worse than the fair registered reference, or the
  bypass critical path is structurally implausible at target N;
- burst storage does not reduce source overrun or p95/p99 latency in any frozen
  burst/phase family;
- rotating-victim minimum service ratio or maximum wait materially regresses;
- post-overload sparse probes remain on the queued path beyond the declared
  hysteresis window after occupancy reaches the exit region;
- the 181-bit nominal state plus selection/mux cost is not justified by measured
  gains;
- benefits appear only on local spatial placement and disappear under matched
  dispersed/relabelled controls; or
- parameter changes needed for correctness turn the design into an always-used
  ordinary FIFO.

Every frozen trace is reported, including regressions. Final conclusions remain
screening evidence until common physical synthesis is approved and run.
