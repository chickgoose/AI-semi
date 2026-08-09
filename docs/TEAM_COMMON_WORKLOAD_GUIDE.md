# Team Common AER Workload and Testbench Guide

Status: shared address-only team baseline, 2026-08-10

## 1. Purpose and boundary

This package freezes the workload, logical AER event meaning, source model,
scoreboard, and result schema. The team now adopts Ganghee's traditional
address-only AER semantics as the implementation starting point; raw cluster2
is the current reference RTL. The workload remains structure-neutral: it does
not require cluster2's row split, bitmap lanes, foveation, or arbitration.

The mandatory common logical event is `(source coordinate/address, occurrence
time)`. The address itself is the event; there is no arbitrary payload.
Polarity/type are optional metadata and are not scored as transported unless a
separate native capability suite is declared. A TB-only event ID tracks loss
and duplication but is never inserted into the DUT. Each source has one pending
latch. A refire while that latch is occupied is reported as `source_overrun`;
the testbench does not hide it in an unbounded queue.

Candidate bindings may map native pins and observe completion, but must not add
FIFO storage, arbitration, retry, serialization, backpressure, event type, or
retire lanes. Synthesizable logic needed for one of those features belongs to
the candidate and its PPA boundary.

## 2. Implemented workloads

| Workload | Stimulus | Main question |
| --- | --- | --- |
| `basic_single` | isolated source-0 events | Does basic address-event transport work without loss or duplication? |
| `basic_sparse` | low-rate events rotated across sources | Are sparse AER events reconstructed in source-local order with low latency? |
| `basic_simultaneous` | every source requests on the same cycle | Does arbitration legally drain all simultaneous events? |
| trace `pairwise_contention` | every unordered address pair with equal ingress spacing, repeated under identity and affine mappings; no hidden reset between pairs | Which address pairs expose partition, HOL, priority-sensitive service, or incomplete drain overlap? |
| `basic_backpressure` | sparse events with repeating sink stalls | Is output stable during a stall and does it recover completely? |
| `limit_load` / trace `uniform` | seeded load from sparse through overload | Where do throughput plateau, latency growth, and source overrun begin? |
| `limit_elephant_mouse` | one hot source and one low-rate victim | Does biased traffic cause starvation or a long victim wait? |
| `limit_global_fanin` | all sources fire together periodically | How do arbitration and drain latency scale with fan-in? |
| `limit_local_cluster` | bursts from adjacent coordinates | Can the design exploit spatial locality efficiently? |
| `limit_distributed_burst` | equal bursts from dispersed coordinates | Is a locality optimization robust rather than overfit? |
| `limit_retrigger` | one source refires faster than service | How much source overrun occurs and what buffering/acceptance rate is needed? |
| `limit_timing_fidelity` / trace `timing_pair` | precise pairs under background traffic | How much does transport distort inter-event timing and deadlines? |
| `limit_backpressure_shock` | sustained traffic plus a long sink stall | What finite-storage limit, loss, and recovery behavior appear? |
| trace `rate_shape` | same source histogram at 1/4/16-event bursts and equal mean rate | Does temporal correlation expose queueing hidden by a mean-load test? |
| trace `matched_spatial` | identical event times placed locally or dispersed | Is the measured gain a real locality benefit? |
| trace `moving_hotspot` | one or several hot sources move by phase | Can the design track nonstationary congestion without starving mice? |
| trace `rotating_victim` | every source becomes the low-rate victim in turn | Is service sensitive to address/priority position? |
| trace `phase_transition` | sparse, near-saturation, overload, post-sparse probe, zero-injection drain | How fast do backlog and normal sparse latency recover? |
| trace `mixed_phase_always_ready` | count-matched burst/smooth and sustained/rotating phases followed by spatial A/B/A replay without reset | Do temporal fan-in, partition imbalance, address mapping, or prior history leave persistent performance bias? |
| optional `optional_multilane_independent_stall` | rotating four-source bursts with a different deterministic stall phase per retire lane | Does each lane hold stable independently while other lanes continue? |

The deterministic generator also supports manifest-controlled `basic_sparse`,
`basic_simultaneous`, `uniform`, `elephant_mouse`, `global_fanin`,
`local_cluster`, `distributed_burst`, `retrigger`, `timing_pair`,
`backpressure_shock`, `rate_shape`, `matched_spatial`, `moving_hotspot`,
`rotating_victim`, `phase_transition`, and `mixed_phase_always_ready`. Use this trace path for final
cross-candidate comparisons:
the complete occurrence stream is generated before any DUT `ready` is observed.

The official screening input is the 50-run N=16
`manifest.neutrality-n16.json`, not the fixed-source built-in SV tests. It keeps
locality workloads because a spatial design winning them is a legitimate
advantage, while adding orthogonal temporal, dynamic-hotspot, recovery,
starvation, relabeling, and above-one-event/cycle cases so that one solved
bottleneck cannot stand in for all AER bottlenecks.

## 3. What the common testbench checks

Hard correctness checks are:

- unknown or illegal source/event output;
- phantom or duplicate completion;
- event corruption and source-local reordering;
- accepted event missing after complete drain;
- drain timeout;
- source address/event identity changing during a continuous input stall; and
- retire address/source identity changing during a continuous output stall.

The hard post-drain condition is `errors == 0` and `accepted == delivered`.
`source_overrun`, low throughput, long latency, or poor fairness are capacity
results, not fabricated functional failures.

Each run writes summary and per-event CSV files. Available measurements include:

- generated, source-overrun, accepted, and delivered counts;
- end-to-end and internal average/maximum latency;
- logical delivered events/cycle;
- Jain fairness and maximum request wait;
- average/maximum inter-event timing error; and
- through the aggregator, p50/p95/p99 latency, deadline misses/censoring,
  service gaps, demand-conditioned zero-service windows, demand-normalized
  fairness, correctness, and saturation knee;
- phase-local completion/latency/backlog and recovery-to-zero; and
- actual cross-source A/B timing-gap distortion through TB-only relation IDs;
- pairwise address completion latency, service skew, order bias, and worst pair.

The legacy raw Jain value is not the ranking fairness metric because unequal
offered traffic intentionally makes it low. Never-offered sources are excluded
from demand-normalized fairness. Common throughput counts deliveries only in
the fixed stimulus window; candidate-dependent drain time is separate.

## 4. Candidate capability policy

Every candidate runs through its native interface. The mandatory always-ready
core requires observable address correctness, occurrence-to-delivery latency,
loss/duplicate/phantom behavior, and per-source service. Output backpressure,
polarity/event type, and multi-lane retirement are optional suites.

An unsupported optional feature is `SKIP_UNSUPPORTED`, not FAIL and not zero
performance. The harness must not add hardware behavior to turn a SKIP into a
RUN. Capability profiles classify optional features independently from
address-only core correctness. The current reference and historical candidates
are interpreted as follows:

| Candidate | Always-ready core | Backpressure | Polarity/type | Independent-lane stall suite |
| --- | --- | --- | --- | --- |
| Ganghee raw cluster2 address-only reference | RUN, fixed N=16 | SKIP | SKIP | SKIP for independent-stall suite |
| Ganghee direct-coordinate fovea (historical comparison) | RUN, fixed N=16 | SKIP | SKIP | SKIP |
| Hyeonsu rotation-priority (historical comparison) | RUN, fixed N=16 | RUN | SKIP | SKIP |
| DREC prefix N=16/K=4 (historical research candidate) | RUN, fixed N=16 | RUN | SKIP | RUN |
| legacy baseline (historical calibration) | RUN | RUN | SKIP | SKIP |
| A23 EE430 (historical calibration) | RUN | RUN | SKIP | SKIP |

Ganghee's original direct-coordinate RTL was not modified. Its earlier Xcelium
23.09 qualification passed all 10 then-supported always-ready workloads. The
run exposed capacity limitations,
including 128/272 overrun in `limit_elephant_mouse`, 128/256 overrun in
`limit_retrigger`, and 17-cycle maximum end-to-end latency in simultaneous and
global-fan-in traffic. These are measured structural limits while accepted-event
correctness remains PASS.

Raw cluster2 later passed the legacy 18-run common multi-lane slice through a
stateless address decoder. That result is historical evidence, not generator-v4
qualification: the frozen address-only suite is now 50 runs and its lane slice
is 22 runs, so every reference and new candidate must be rerun on the new trace
hashes before ranking.

## 5. Repository map

```text
benchmarks/clean_slate_aer/       trace generator, validator, aggregator, tests
tb/clean/                         common interface, source model, scoreboard
tb/clean/native/                  storage-free native observation bindings
tests/clean_native/               Ganghee binding protocol fixture
scripts/run_clean_benchmark.sh    mock and historical calibration runner
scripts/run_ganghee_native_benchmark.sh
scripts/run_common_multilane_benchmark.sh
scripts/run_common_multilane_candidate.sh
benchmarks/clean_slate_aer/manifest.multilane-n16.json
reports/common-multilane/           shared lane-capacity instructions/results
docs/verification/               specification, profiles, results, PPA contract
```

## 6. How to run

Run Python/trace self-checks:

```bash
python3 benchmarks/clean_slate_aer/self_test.py
python3 benchmarks/clean_slate_aer/neutrality_self_test.py
python3 -m unittest discover -s benchmarks/clean_slate_aer/tests -v
tests/clean_native/run_binding_test.sh
```

Reproduce the built-in mock and historical ready/valid calibration runs:

```bash
scripts/run_clean_benchmark.sh mock
scripts/run_clean_benchmark.sh baseline
scripts/run_clean_benchmark.sh a23-ee430
```

Generate a deterministic manifest suite and run one trace:

```bash
python3 benchmarks/clean_slate_aer/generate_trace.py \
  --manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  --output-dir /tmp/aer-common-traces

AER_TRACE_JSONL=/tmp/aer-common-traces/basic_sparse.events.jsonl \
AER_TRACE_MANIFEST=/tmp/aer-common-traces/basic_sparse.manifest.json \
scripts/run_clean_benchmark.sh baseline
```

Historical direct-coordinate fovea reproduction, without editing its RTL:

```bash
setenv AER_GANGHEE_TOP aer_tx16_trad_rowcol_fovea
setenv AER_GANGHEE_FILELIST /absolute/path/to/ganghee-native.f
scripts/run_ganghee_native_benchmark.sh
```

The file list must contain the original top and its arbiter dependencies using
absolute paths. This runner deliberately rejects backpressure workloads.

Aggregate result files from multiple candidates/seeds:

```bash
python3 benchmarks/clean_slate_aer/aggregate.py \
  result-a.csv result-b.csv \
  --events result-a.events.csv --events result-b.events.csv \
  --output /tmp/aer-common-summary.csv
```

Diagnose the worst simultaneous address pair after a pairwise run:

```bash
python3 benchmarks/clean_slate_aer/pairwise_contention_metrics.py \
  --trace generated/pairwise_contention_identity.events.jsonl \
  --run-manifest generated/pairwise_contention_identity.manifest.json \
  --events results/pairwise_contention_identity.events.csv \
  -o results/pairwise_contention_identity.pairs.json
```

Generate the exact common lane-capacity trace slice without selecting a DUT:

```bash
scripts/run_common_multilane_benchmark.sh generate-only
```

The following DREC and Hyeonsu commands reproduce historical comparisons; they
do not select either design as the current base.

Run historical DREC through its registered common multi-lane binding:

```bash
AER_SIMULATOR=xrun \
  scripts/run_common_multilane_benchmark.sh drec-prefix 4
```

The multi-lane manifest is not a DREC workload. Single-lane candidates consume
the same 22 traces and expose their natural saturation behavior. Only the
independent-lane stall test is optional and capability-gated.

Run all 22 lane-capacity traces on a historical candidate already registered in
the common clean runner (for example Hyeonsu's server-side
`rotation-priority` entry):

```bash
AER_SIMULATOR=xrun \
  scripts/run_common_multilane_candidate.sh clean rotation-priority
```

Run the current raw cluster2 address-only reference after setting its original
top/file-list environment variables:

```bash
setenv AER_GANGHEE_CLUSTER2_TOP aer_tx16_trad_rowcol_fovea_cluster2
setenv AER_GANGHEE_CLUSTER2_FILELIST /absolute/path/to/ganghee-cluster2.f
scripts/run_common_multilane_candidate.sh ganghee-cluster2
```

The older direct-coordinate fovea binding remains available for historical
reproduction:

```bash
scripts/run_common_multilane_candidate.sh ganghee
```

Both common runners automatically write `*.pairs.json` beside each pairwise
result. Identity and affine reports contain canonical relation IDs, but an
automatic cross-map delta artifact is still pending and must not be claimed.

## 7. Current gaps

The written specification includes, but the current common runner does not yet
fully implement, `basic_reset_drain`, native `basic_polarity`, automatic
16/64/256 `limit_scale`, and fixed-pin `limit_pin_budget`. Independent multi-lane
stall qualification is now implemented as an optional capability suite; it is
not a mandatory core requirement. Do not claim unimplemented items as qualified
results. The old in-SV `limit_load` uses
a per-source probability; frozen comparisons should use the deterministic trace
generator, whose `load` is aggregate offered events/cycle.

The final cross-candidate evaluation layer is also not yet implemented. The
agreed starting point is Ganghee-style address-only event semantics, with raw
cluster2 serving as a conventional reference rather than forcing its row split,
bitmap lanes, or foveation into new designs. Before ranking raw cluster2 and
independently developed address-only candidates, the team must still:

- freeze each candidate's commit SHA, top, file list, parameters, native pins,
  source count, retire lanes, and capability profile;
- qualify the first controlled comparison at N=16;
- export measured events/cycle and a common activity window from the same
  deterministic trace instead of assigning throughput by candidate name;
- add the frozen candidates to an architecture-neutral Genus screening runner;
- run identical Innovus fixed-netlist diagnostics and final per-target
  resynthesis P&R; and
- report same-frequency area/power/energy-per-event separately from each
  candidate's demonstrated post-route frequency bracket, events/s, and
  events/pin-cycle.

The three-seed N=16 uniform sweep is a screening gate for the upcoming parallel
architecture search. Finalist saturation claims must use a larger predeclared
seed set and publish uncertainty. Multi-hop routing/multicast, asynchronous CDC,
and native multiple-occurrences-per-source-per-cycle are not silently claimed by
this one-hop synchronous core; they require separately frozen capability suites
and all required hardware must remain inside candidate PPA.
