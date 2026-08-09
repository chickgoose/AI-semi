# Clean-Slate AER Benchmark Specification

Status: team-internal address-only contract, 2026-08-10

## 1. Purpose

The team uses Ganghee's traditional address-only AER semantics and raw cluster2
as the current implementation reference. This benchmark deliberately does not
freeze cluster2's row split, foveation, bitmap packing, or arbitration policy;
new RTL may change those structures while preserving the address-event contract.

The benchmark has two equally important jobs:

1. prove that a candidate implements the basic Address-Event Representation
   function; and
2. expose the operating regions where a conventional flat, shared-channel AER
   implementation suffers from bandwidth saturation, queueing, arbitration
   scaling, starvation, source overrun, timing distortion, or an excessive pin
   budget.

A conventional AER implementation must not be made artificially incorrect.  It
must pass the conformance tests.  In the limit tests it may remain logically
correct while its latency, loss, timing fidelity, or efficiency degrades.  A new
architecture earns an improvement only by moving that measured limit without
regressing the conformance suite.

## 2. What is an AER problem

The logical AER event is:

```text
(source coordinate/address, occurrence time)
```

The address itself is the mandatory event and no arbitrary payload is required.
Polarity/type are optional capability metadata, not mandatory transported bits.
The occurrence time belongs to the test trace. A sequence ID is attached only
inside the testbench to detect loss and duplication; it is never DUT payload.

The problem statements in `~/TEAM_PROGRESS.md` are used with the following
precise scopes.

| Problem | Precise scope | Benchmark evidence |
| --- | --- | --- |
| Bandwidth bottleneck | Offered event rate approaches or exceeds the service rate of a shared channel | saturation point, delivered events/cycle, queueing latency, overrun |
| Pin shortage | Inter-chip full-address parallel AER consumes an increasing physical I/O budget | logical events/pin-cycle under a fixed data/control-pin budget |
| Arbitration latency | Fan-in and simultaneous request count increase grant delay, logic depth, area, or Fmax cost | request-to-accept latency plus Genus scaling sweep |
| Fairness | A fixed or otherwise biased grant policy can indefinitely postpone a legal requester | bounded victim wait and per-source service counts |
| Queueing and source overrun | A source fires again before its outstanding request is accepted, or finite DUT storage fills | generated/accepted/delivered counts reported separately |
| Timing distortion | Queueing changes the inter-event timing that carries neuromorphic information | input/output inter-event interval error and deadline misses |

Bandwidth, arbitration, and fairness limitations belong to particular shared-link
implementations and policies, not to the abstract meaning of an address event.
Likewise, pin count is primarily an inter-chip physical-link constraint.  Reports
must not claim that every possible AER architecture has all of these defects.

## 3. Measurement boundary

The common trace and scoreboard are architecture-neutral:

```text
logical event trace
        |
        v
one-entry source latch model
        |
        v
candidate-specific input adapter
        |
        v
candidate DUT, including required synthesizable buffers/codecs
        |
        v
candidate-specific output normalizer
        |
        v
completed logical events and common scoreboard
```

The normalized test interface is not the official or physical AER interface.
Serialization, packing, arbitration, buffering, and physical handshakes remain
candidate choices.  Any encoder, decoder, queue, or adapter logic required in
silicon belongs inside the reported PPA boundary.  A behavioral testbench adapter
must not provide free storage or free protocol conversion.

The source model has exactly one outstanding-event latch per source.  It holds a
request until the candidate accepts it.  If the source fires again while that
latch is occupied, the new logical event is counted as `source_overrun`; it is not
silently placed in an unbounded testbench queue.  A candidate may reduce overrun
by accepting faster or by implementing synthesizable source-side storage whose
area and power are included.

## 4. Required counters and latency definitions

Every run records these distinct counts:

```text
generated = accepted + source_overrun still attributable at the source
accepted  = events transferred from source latch into the candidate
delivered = accepted events reconstructed at the normalized output
```

After a complete drain, `accepted == delivered` is a hard correctness condition.
Generated events may exceed accepted events only in a limit test that deliberately
causes source overrun; the difference must be reported, never hidden.

Two latency origins are required:

- end-to-end latency: occurrence to normalized delivery;
- internal latency: candidate acceptance to normalized delivery.

Request wait is occurrence to acceptance.  Timing distortion for two consecutive
delivered events from the same source is:

```text
abs((delivery_2 - delivery_1) - (occurrence_2 - occurrence_1))
```

## 5. Test suites

### 5.1 Conformance and capability suite

| Test | Status | Stimulus and hard checks |
| --- | --- | --- |
| `core_sparse_*` | frozen mandatory core | isolated coordinate spikes, exact address, no loss/duplicate, source-local order |
| `core_simultaneous_identity` | frozen mandatory core | one event from every source on one cycle, legal arbitration, complete drain |
| conservation/quiet guard | frozen mandatory core | generated/overrun/pending/accepted/delivered conservation and no late phantom |
| `basic_reset_drain` | mandatory direct-SV core | disjoint address-only epochs, complete drain, reset, quiet/stale guard, post-reset correctness and drain |
| `basic_backpressure` | optional capability | stable output during a sink stall and complete recovery |
| `basic_polarity` | optional capability | preserve declared polarity/event type when the native contract carries it |

Mandatory conformance requires zero phantom, duplicate, corrupt, or missing
accepted event. Output stability during a stall is required only when that
candidate declares the optional backpressure capability as RUN.

Implementation status matters: the exact N=16 coordinate-spike core currently
qualifies sparse/simultaneous/event-conservation behavior. Output backpressure
and polarity/type are capability-gated because the candidates do not share
those physical contracts. `basic_reset_drain` is a direct-SV mandatory test;
its second reset occurs only after complete accepted-event drain and makes no
cancel-or-preserve claim for traffic active during reset. Therefore an older
“10/10 core PASS” must not be described as passing every row in this target
conformance table.

### 5.2 Limit-exposure suite: scored, not architecture-specific

Every frozen N=16 sink-always-ready trace is a mandatory capacity run for the
common screening table. Capacity degradation is reported, not converted into a
functional error. Backpressure is optional RUN/SKIP. `limit_scale` and
`limit_pin_budget` are separate finalist physical-evidence gates, not missing
zero-valued workload rows and not tie-break points for optional capability
coverage.

| Test | Stimulus | Primary problem and metrics |
| --- | --- | --- |
| `limit_load` | seeded Bernoulli load sweep from sparse through overload | saturation, throughput, overrun, p95/p99 latency |
| `limit_elephant_mouse` | one continuously active source plus a periodic low-rate victim | starvation, victim maximum wait, throughput |
| `limit_global_fanin` | all sources fire together at a fixed period | arbitration/drain latency and scaling |
| `limit_pairwise_contention` | every unordered address pair under equal ingress spacing and address permutation, without hidden reset | pair-dependent arbitration, partition HOL, prior-pair overlap; automatic identity/affine cross-map delta with non-rankable exit 3 |
| `limit_local_cluster` | temporally clustered events from adjacent coordinates | locality opportunity, burst efficiency |
| `limit_distributed_burst` | equally large burst from dispersed coordinates | anti-overfit check for locality-dependent schemes |
| `limit_retrigger` | one source refires faster than service time | source overrun and required buffering |
| `limit_timing_fidelity` | precise event pairs under independent background traffic | interval distortion, tail latency, deadline misses |
| `limit_backpressure_shock` | long sink stall inserted into sustained traffic | finite storage, recovery time, loss |
| `limit_rate_shape` | same event count/source histogram at 1-, 4-, and 16-event bursts | temporal correlation sensitivity at fixed mean load |
| `limit_matched_spatial` | identical event times and demand-by-rank placed locally or dispersed | genuine locality benefit versus input mismatch |
| `limit_moving_hotspot` | one or more hot regions move at frozen dwell boundaries | adaptation delay, hotspot handoff, dynamic congestion |
| `limit_rotating_victim` | every source becomes a low-rate victim under aggressor load | fixed-priority sensitivity, starvation, HOL effects |
| `limit_phase_transition` | sparse, near-saturation, overload, post-sparse probe, then zero-injection drain | backlog growth, hysteresis, sparse-latency recovery, recovery-to-zero |
| `limit_mixed_phase_always_ready` | matched burst/smooth and sustained/rotating demand plus spatial A/B/A replay in one no-reset trace | temporal fan-in, partition imbalance, mapping sensitivity, state pollution, and hysteresis |
| `limit_scale` | identical normalized profiles at 16, 64, and 256 sources | area/Fmax/power and latency scaling |
| `limit_pin_budget` | fixed physical data/control pin budget | events/pin-cycle and codec PPA |

`limit_local_cluster` uses logical coordinates only.  It does not prescribe a
row/column implementation.  `limit_distributed_burst` is mandatory so that a
row-local, bitmap, or compression proposal cannot win solely because all traces
match its preferred encoding.

These tests are not removed when an existing candidate performs well on them.
That result is a valid candidate advantage. Architecture-neutrality instead
requires coverage of orthogonal bottlenecks, identical offered traces, and
paired address/spatial controls. The implementation-backed coverage and known
gaps are tracked in `aer-bottleneck-coverage-audit.md`.

### 5.3 Mixed-phase regression

At least one trace transitions through idle, sparse, local burst, distributed
burst, near-saturation, sink stall, and recovery phases.  This detects adaptive
schemes whose mode switch is too slow or whose high-load optimization damages
normal sparse-event latency and power.

## 6. Workload reproducibility

Every randomized run records:

- workload name and version;
- source geometry and count;
- seed;
- stimulus and drain cycles;
- per-source offered-load rule;
- sink-ready rule;
- generated logical-event trace or a deterministic generator manifest;
- candidate and adapter identity.

Primary reports use multiple fixed seeds.  A single favorable seed is not
evidence of improvement.

## 7. Physical-link and PPA fairness

Function, performance, and physical efficiency are separate views.

- A logical completion may represent one decoded event from a serialized word,
  one lane of a multi-lane output, or one member of a packed group.
- Sustainable throughput is completed logical events per cycle, so a legal packed
  or multi-lane design may exceed one event/cycle.
- Multi-lane and wider-bus designs must also report events/pin-cycle.
- PPA includes synthesizable encoding, buffering, arbitration, link, and decoding
  needed to recover the logical event at the selected boundary.
- All candidates use the same Xcelium/Genus versions, source geometry, Liberty,
  PVT, SDC, clock/load constraints, trace window, and VCD/SAIF activity method.

The provisional common tool flow remains Xcelium 23.09, Genus 23.14, GPDK045,
`slow_vdd1v0_basicCells.lib`, and the repository's common scripts.  These are
compatibility defaults and must be replaced when official competition constraints
arrive.

## 8. Selection rule for a clean-slate architecture

A candidate is eligible only if all conformance tests pass.  It is preferred only
if it improves a balanced set of limit tests and does not obtain that improvement
by adding unreported testbench storage, dropping events, widening the physical
link without pin normalization, or severely regressing sparse traffic.

Raw cluster2 is the current address-only implementation reference. It is a
comparison point, not a requirement to inherit its row split, bitmap lanes,
foveation, or arbitration. New candidates are independently developed against
the frozen logical contract; older A23, DREC, direct-coordinate fovea, and
payload-oriented designs remain historical research evidence.
