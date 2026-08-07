# Survivor novelty and prior-art audit: A2, A4, and A7

Date: 2026-08-07

Auditor: A3, independent devil's-advocate follow-up

Scope: primary papers and official documentation; no server or other-worktree write

## 1. Boundary and conclusion

This audit does not reopen the eight-track decision. A2 B4/D16 remains a
conditional one-lane survivor, A4 N=64 remains a physical research hypothesis
(N=16 `HOLD_FLAT`), and A7 N=16/K=4 remains a conditional four-lane survivor.
It asks only how narrowly novelty may be described.

The reviewed branch snapshots were A2 `0cf40b8`, A4 `5f07aee`, and A7
`2859ed7`. Dirty A2 phase-3 work and later A4 handoff files were read only as
uncommitted context and supply no positive claim. A7 `2859ed7` adds the
radix-4 segmented implementation but no committed rescue result, so the fixed
K=4 conclusion still comes from the earlier equal-state comparison. This is a
technical prior-art screen, not a patent search, freedom-to-operate opinion, or
proof that no earlier implementation exists.

| Track | Broad novelty verdict | Defensible contribution after prior art | Presentation posture |
| --- | --- | --- | --- |
| **A2** | Individual parts are established: empty-path bypass/FWFT, banked buffering, occupancy thresholds, and hysteresis. | A repository-specific combination of queue-free sparse retirement, same-edge one-retire-plus-B-admit, strict global FIFO order, and level/delta/dwell control, evaluated on the frozen AER contract. | Call it an **adaptive composition/hypothesis**, not a new FIFO, bypass, congestion detector, or lossless >1-event/cycle transport. |
| **A4** | AER arbiter trees, hierarchical RR trees, bounded-radix arbitration, and elastic pipeline registers all predate A4; even radix-4 RR trees with `O(log4 N)` depth are published. | A synchronous full-identity elastic merge-tree instance plus an unusually explicit topology/permutation and equal-boundary falsification on this AER workload. | Call it a **spatial AER integration and scaling hypothesis**, not a novel quadtree arbiter or proven wire/PPA win. |
| **A7** | The central algorithm is strong prior art: scan-based compaction, parallel-prefix RR, and parallel-prefix **m-select** RR all predate A7. | The exact cyclic-rank/available-lane ready-valid composition, its contract proof/tests, and the measured equal-state crossover at N=16/K=4 are implementation evidence. | Do not claim invention of prefix compaction, bitmap-to-K selection, or multi-grant RR. Present only the AER specialization and measured crossover. |

Novelty confidence is therefore lowest for A7's algorithmic headline, moderate
only at the composition level for A2, and low for A4's structure but useful at
the evaluation-method level. None of these findings upgrades a proxy to
physical PPA evidence.

## 2. Primary-source map

Only original papers, author/institution copies, and vendor/standards-style
official documentation are used below. No secondary survey supplies a novelty
conclusion.

| Source | Prior-art teaching relevant here | Audit use |
| --- | --- | --- |
| Lazzaro et al., [*Silicon Auditory Processors as Computer Peripherals* (1993)](https://john-lazzaro.github.io/biblio/asynch-ieee.pdf), DOI [10.1109/72.217193](https://doi.org/10.1109/72.217193) | An AER peripheral and arbitration tree for multiplexing event identities. | AER plus tree arbitration is foundational, not an A4 novelty. |
| Boahen, [*Point-to-Point Connectivity Between Neuromorphic Chips Using Address Events* (2000)](https://repository.upenn.edu/bitstreams/4087da08-9301-4ab1-9de3-47924602da72/download), DOI [10.1109/82.842110](https://doi.org/10.1109/82.842110) | AER bandwidth, arbitration, queueing, clustered activity, and parallel readout are co-designed. | Burst/locality-aware AER transport is prior motivation for A2/A4. |
| Boahen, [*A Burst-Mode Word-Serial Address-Event Link—I* (2004)](https://web.stanford.edu/group/brainsinsilicon/documents/04_journ_IEEEtcs_AERChanI.pdf) | Hierarchical arbitration exploits row-level parallel activity and burst readout. | Refutes novelty of hierarchical/local burst aggregation; A4's synchronous full-coordinate semantics are the difference. |
| Chen et al., [*SMART: A Single-Cycle Reconfigurable NoC* (DATE 2013)](https://people.csail.mit.edu/suvinay/pubs/2013.smart.date.pdf) | Bypassing intermediate storage protects low-load latency, with reservation and wiring costs. | Bypass as a latency mechanism is prior art; A2 is an endpoint, not SMART routing. |
| Michelogiannakis et al., [*Elastic-Buffer Flow Control for On-Chip Networks*](https://doi.org/10.1109/TC.2011.237) | Valid channel registers can act as distributed FIFO storage and replace conventional input buffers. | Elastic valid/ready storage and same-cycle pipeline movement are not A2/A4 inventions. |
| AMD, [UltraScale FIFO `FIRST_WORD_FALL_THROUGH`](https://docs.amd.com/r/en-US/ug573-ultrascale-memory-resources/FIRST_WORD_FALL_THROUGH) | Empty-FIFO data can be exposed without a conventional read-latency step. | A2's zero-queue latency goal is not itself novel; its direct retire semantics are only a contract-specific composition. |
| AMD, [Zynq-7000 FIFO controller](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/FIFO-Controller) | Synchronous FIFO control includes occupancy flags and programmable almost-full/almost-empty thresholds. | Occupancy watermark control is official commodity functionality, not A2 novelty. |
| TI, [EDMA3 controller guide, ping-pong example](https://www.ti.com/lit/ug/sprufi0/sprufi0.pdf) | Alternating buffers decouple producer and consumer work for continuous transfer. | Banking/double-buffering is familiar; A2 is not DMA ping-pong because its banks are striped slots of one ordered FIFO. |
| Hu et al., [*Dynamical Hysteresis Phenomena in Complex Network Traffic* (2009)](https://doi.org/10.1103/PhysRevE.79.047101) | Congestion onset and recovery can follow different trajectories. | Supports the motivation for separate enter/exit conditions, not A2's exact controller or a stability proof. |
| Luo et al., [*A Low-Latency Fair-Arbiter Architecture for NoC Switches* (2022)](https://doi.org/10.3390/app122312458) | A decentralized radix-4 RR arbiter tree targets `O(log4 N)` levels. | Direct counterevidence to any broad A4 radix-4/tree/fairness novelty claim. |
| McKeown, [*The iSLIP Scheduling Algorithm for Input-Queued Switches* (1999)](https://yuba.stanford.edu/~nickm/papers/ToN_April_99.pdf) | Parallel rotating-priority matching can schedule several non-conflicting transfers. | Multiple fair grants and rotating pointers are established, although A7 solves one bitmap-to-K-resource problem rather than bipartite matching. |
| Blelloch, [*Prefix Sums and Their Applications*](https://www.cs.cmu.edu/~guyb/papers/Ble93.pdf) | Scan provides logarithmic-depth prefix computation and supports stable packing/compaction. | A7's bitmap ranks and compaction primitive are prior art. |
| Ugurdag and Baskirt, [*Fast Parallel Prefix Logic Circuits for n-to-n Round-Robin Arbitration* (2012)](https://doi.org/10.1016/j.mejo.2012.04.005) | Rotating-priority RR is implemented with a parallel-prefix network. | Prefix-based RR itself is not A7 novelty. |
| Ugurdag, Temizkan, and Goren, [*Generating Fast Logic Circuits for m-Select n-Port Round Robin Arbitration* (2013)](https://doi.org/10.1109/VLSI-SoC.2013.6673286) | Saturating parallel-prefix logic finds the first `m` asserted requests and constructs a one-cycle m-select RR arbiter. | Strongest anticipation of A7's broad “rank once, select K” claim. |
| Merrill and Garland, [*Single-pass Parallel Prefix Scan with Decoupled Look-back* (2016)](https://research.nvidia.com/sites/default/files/pubs/2016-03_Single-pass-Parallel-Prefix/nvr-2016-002.pdf) | Scan is used for allocation and in-place compaction while sharing prefix work. | “GPU-style compaction” is an analogy to established work, not novelty. |
| NVIDIA, [CUDA Programming Guide: warp vote functions](https://docs.nvidia.com/cuda/cuda-programming-guide/#warp-vote-functions) | `ballot` materializes an active-lane bitmap; warp primitives support cooperative lane operations. | Bitmap/lane terminology is established software vocabulary, not a hardware invention claim. |

## 3. A2: adaptive dual-path sparse bypass plus banked reservoir

### 3.1 Minimum mechanism decomposition

| Minimum unit | Already known | A2-specific combination/difference | Novelty assessment |
| --- | --- | --- | --- |
| Empty-state combinational direct path | Bypass NoCs and FWFT/look-ahead FIFOs already avoid a storage/read stage. | A source event retires directly only when the global ordered reservoir is empty; no older queued event can be overtaken. | Contract-specific use, not a new bypass or FIFO mode. |
| B-way admission into striped banks | Interleaved/banked memories and ping-pong buffers are standard ways to obtain concurrent accesses. | Consecutive global FIFO tail positions map to banks; bank identity is not an independent queue, and one global head preserves order. | Familiar banking with a specific multi-admit FIFO realization. Do not call it ping-pong DMA. |
| Direct one plus queued younger events on one edge | Cut-through/bypass and concurrent buffering are known independently. | One rotating winner may retire while up to B younger winners enter consecutive FIFO positions under the one-entry/source AER seam. | Plausibly distinctive composition in this benchmark, not shown unique by this search. |
| Occupancy level, occupancy delta, immediate fan-in | Congestion and buffer-occupancy feedback are standard NoC controls. | These signals arm retention of the burst path but do not determine correctness; nonzero occupancy always owns retirement. | Control selection is an engineering combination, not a new congestion principle. |
| Separate enter/exit plus quiet dwell | Hysteresis and programmable almost-full/empty thresholds are established stabilizers. | Level, derivative sign, and dwell are combined to avoid sparse/burst thrashing. | Narrow policy composition only. |
| Rotating selection | RR is established arbiter practice. | Rotation supplies bounded neutral tie-breaking around the data-path switch. | No novelty claim. |

The closest defensible novelty sentence is: “We evaluate a synchronous AER
endpoint that combines an ordered queue-free sparse retire path with B-way
striped burst admission, and uses level/delta/dwell feedback to decide how long
the burst path remains armed.” Even that is a design-combination claim, not a
claim of first invention.

### 3.2 What this workload actually verified

- Committed phase 1 established 46/46 transport correctness and one-cycle
  isolated retirement; it did not establish sink-stall support.
- The phase-2 B4/D16 model reduced summed fixed-hotspot-plus-recurrence overrun
  versus flat RR at N=16/32/64, but recurrence p99 was 10/10/12 cycles worse
  and toggle/event was worse. This supports burst absorption, not universal
  latency or energy improvement.
- A single retire lane still has at most one sustained completion/cycle. Extra
  acceptance is finite storage displacement, not new output bandwidth.
- The current phase-3 gate has no committed result. There is therefore no
  canonical-equivalence, memory-mapping, Fmax, area, or power novelty evidence.

### 3.3 Expressions prohibited in a presentation

- “new zero-latency/fall-through FIFO,” “novel bypass network,” or “new banked
  memory architecture”;
- “adaptive routing,” because no route changes;
- “lossless burst transport” or “more than one event/cycle throughput”;
- “occupancy prediction,” because occupancy delta is observation, not future
  prediction; and
- “proven power saving,” “PPA win,” or “universal latency win.”

## 4. A4: radix-4 spatial elastic merge tree

### 4.1 Minimum mechanism decomposition

| Minimum unit | Already known | A4-specific combination/difference | Novelty assessment |
| --- | --- | --- | --- |
| Hierarchical AER request tree | Lazzaro and Boahen established AER arbitration trees and hierarchical address/event readout. | A4 is synchronous ready/valid and carries full event/source identity through every node. | A protocol translation/integration, not invention of AER trees. |
| Radix-4 hierarchical RR | Published NoC work already gives decentralized radix-4 RR trees with logarithmic levels. | Each A4 node is also a one-entry payload-holding elastic merge, and phase advances only on transfer. | The broad structure is anticipated; exact state placement is an implementation choice. |
| One elastic register per internal node | Elastic buffers and relay stages are established pipeline elements. | The register is simultaneously arbitration capture, local backpressure boundary, and burst slot. | Known primitives combined in a particular AER tree. |
| Spatial 2x2 grouping/quadtree placement | Hierarchical/local interconnect and clustered AER readout are prior art. | Identity/mirror/affine and local/row/column/dispersed controls explicitly expose mapping sensitivity. | Evaluation discipline is stronger than an architectural novelty claim. |
| Saturating hop-age field | Pipeline metadata accumulation is routine. | Age is diagnostic only and never changes grant priority. | No scheduling novelty; it should be removable from a minimal physical comparison. |
| Nested local RR progress argument | Fair arbiter trees and compositional service bounds are established. | A4 derives a conservative bound for its exact two-level handshake. | Contract proof, not a new fairness algorithm. |

A4's defensible novelty is not “a quadtree arbiter.” It is the evaluated
combination of full-identity synchronous elastic merge nodes with explicit
spatial falsification and equal-boundary accounting. The 2022 radix-4 fair
arbiter tree is particularly close structural prior art; A4 must distinguish
payload-holding handshake semantics and the AER experiment, not tree topology.

### 4.2 What this workload actually verified

- N=16 passed all 46 traces, but its full-channel paper wire proxy was 720
  bit-grid versus flat 704, and the committed structural decision is
  `HOLD_FLAT` despite generic cell/depth reductions.
- Overrun improved on rotating-victim and timing-pair cases, but uniform
  overload and tails/fairness did not form a universal win; internal slots
  absorb events and must remain charged.
- Only N=64 passed the local structural gate as `CONDITIONAL_SHORTLIST`. That
  verifies a generic topology trend, not routed wirelength, congestion, clock
  power, or standard-cell Fmax.
- Frozen N=16 address permutations do not prove arbitrary physical-placement
  neutrality. Spatial dependence is the hypothesis, not a nuisance that can be
  omitted from the result.

### 4.3 Expressions prohibited in a presentation

- “first hierarchical/quadtree AER arbiter,” “novel radix-4 RR tree,” or
  “new elastic pipeline”;
- “topology neutral,” “mapping independent,” or “globally fair” without the
  exact persistent-request and ready assumptions;
- “lower wire cost at N=16” or “proven scalable wiring”; and
- “higher throughput,” unless explicitly described as one-lane finite burst
  admission with a one-event/cycle root.

## 5. A7: shared-prefix K-lane event compactor

### 5.1 Minimum mechanism decomposition

| Minimum unit | Already known | A7-specific combination/difference | Novelty assessment |
| --- | --- | --- | --- |
| Bitmap population scan and rank | Blelloch establishes scan and stable packing; GPU work applies it to compaction/allocation. | A7 builds exact hardware ranks for a live AER request bitmap. | Direct application of known primitive. |
| Rotation-neutral cyclic rank | Parallel-prefix RR arbiters already combine prefix logic with rotating priority. | A7 avoids physically rotating request/payload buses by subtracting a base prefix and wrapping ranks. | Implementation formulation; not enough for a broad RR novelty claim. |
| First K cyclic requests | The 2013 m-select RRA paper explicitly selects the first m asserted requests using parallel-prefix saturated sums. | A7 uses full population counts/rank equality rather than the paper's exact gate topology. | Broad algorithm is anticipated. Only circuit/contract details may differ. |
| Compact into currently available lanes | Parallel compaction assigns active items to dense output positions; multi-resource arbitration is established. | A tiny second prefix ranks only ready-or-empty physical output slots, allowing independent stalled lanes to retain data while others refill. | Best candidate for a narrow implementation-combination claim. |
| K registered elastic outputs | Output queues and ready/valid elastic stages are standard. | The source-inflight guard and refill rule preserve the repository's one-outstanding/source semantics. | Contract engineering, not general novelty. |
| Advance past last accepted source | Multi-select RR advances after the last selected request in prior work. | A7 states and exhaustively checks the exact `ceil(N/K)` all-ready opportunity bound. | Verification contribution, not new policy. |
| Radix-4 segmented scan rescue | Segmented/block scans and hierarchical prefix circuits are established. | Current A7 commit reconstructs exact source prefixes from narrow four-source counts plus a wide segment prefix. | No positive novelty or efficiency claim until its preregistered comparison is committed. |

The 2013 m-select paper is fatal to the headline “a novel prefix multi-grant
arbiter.” The defensible sentence is narrower: “For this AER handshake, we
couple one cyclic population-rank network to a second available-lane rank so
independently stalled registered lanes can be compactly refilled, then measure
where it crosses an equal-state replicated selector.”

### 5.2 What this workload actually verified

- Prefix and same-K replicated selection produced identical frozen metrics;
  throughput and fairness are properties of K lanes and the shared policy, not
  evidence that prefix is algorithmically superior.
- N=16/K=2 failed structural break-even: 4,299 versus 3,733 generic gates at
  equal depth 133. N=16/K=4 was the first measured point to beat the replicated
  proxy, 5,592 versus 6,729 gates and depth 139 versus 248, with equal 104-bit
  registered state.
- The frozen offered-load ceiling is two events/cycle, so K=4 never demonstrates
  more throughput than K=2 and reaches only about 50% utilization at load 2.
  The K=4 result is a structural crossover, not a four-lane workload win.
- K=4 exposes four 22-signal retire lanes, 88 pins total. No pin/floorplan,
  sink-endpoint, standard-cell, power, or routed-wire advantage is established.
- The newly committed segmented implementation has no committed rescue result;
  it cannot amend the fixed K=2 rejection or K=4 conditional status.

### 5.3 Expressions prohibited in a presentation

- “first GPU-style hardware compactor,” “novel parallel-prefix arbiter,”
  “first bitmap-to-K selector,” or “new multi-grant round robin”;
- “4x throughput,” “K=4 performance win,” or any throughput number without
  offered load, lane utilization, pin count, and same-K reference;
- “less wiring/area/Fmax” based only on generic gate/depth proxies; and
- “backpressure-complete AER generally,” because the proof and source-inflight
  contract assume at most one outstanding event per source.

## 6. Cross-candidate novelty claims that survive

| Candidate | Already-known foundation | New combination worth reporting | Claim actually supported by repository evidence | Physical question still unresolved |
| --- | --- | --- | --- | --- |
| A2 B4/D16 | bypass/FWFT + banked FIFO + hysteretic occupancy control | strict-order direct-retire plus B-way tail admission and load-state retention | finite burst absorption with sparse one-cycle retirement, alongside worse overload tails/toggles | Does canonical equivalent RTL retain activity benefit after memory mapping and worst-path timing? |
| A4 N=64 | AER arbiter tree + radix-4 RR + elastic registers | full-identity spatial elastic merges plus permutation/topology falsification | N=64 generic structural scaling only; N=16 is held flat | Does placement preserve wire/timing gain after payload links, clocks, remap, and root congestion? |
| A7 N=16/K=4 | scan compaction + prefix RR + m-select RR | cyclic source-rank joined to available-lane rank under independent ready | equal-state generic structural crossover at K=4, not workload superiority | Does the crossover survive 88 pins, four endpoints, lane stalls, prefix fanout, and routed timing? |

The strongest publishable common contribution is therefore methodological:
three familiar mechanism families were instantiated under one AER correctness
contract, compared with mechanism-matched references, and allowed to fail at
predeclared break-even points. The architecture claims must remain narrower
than that evaluation contribution.

## 7. Citation and wording policy for a paper or talk

1. Cite the closest source beside the mechanism, not only in a background
   slide: FWFT/SMART for A2 bypass, Lazzaro/Boahen plus the radix-4 RR tree for
   A4, and the 2013 m-select prefix RRA for A7.
2. Use “we combine,” “we specialize,” “we evaluate,” or “we measure a
   crossover,” unless a claim names the exact implementation feature absent
   from the cited work.
3. Separate novelty from utility. A combination may be distinctive but lose
   its gate; a known primitive may still be a useful survivor at a measured
   operating point.
4. State negative evidence in the same frame: A2 tail/toggle cost, A4 N=16
   hold, and A7 K=2 rejection plus K=4 workload under-utilization.
5. Call all local Yosys, analytic wire, and VCD results proxies. No “silicon,”
   “physical,” “power,” “Fmax,” or “PPA” win exists without the forbidden-until-
   approved common physical flow.

No long quotation from any source is reproduced. All external sources above
are linked at the paper, author/institution copy, DOI, or official vendor page.
