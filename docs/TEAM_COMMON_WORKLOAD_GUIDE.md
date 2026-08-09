# Team Common AER Workload and Testbench Guide

Status: shared address-only team baseline, 2026-08-10

## 1. Purpose and boundary

This package freezes the workload, logical AER event meaning, source model,
scoreboard, and result schema. Ganghee's traditional address-only AER semantics
are the implementation starting point and raw cluster2 is the current reference
RTL. The workload does not require cluster2's row split, bitmap lanes,
foveation, or arbitration policy.

The mandatory logical event is `(source coordinate/address, occurrence time)`.
The address is the event; arbitrary 16-bit payload transport is not required.
Polarity/type are optional metadata unless a native capability suite declares
them transported. A TB-only event ID tracks loss and duplication but is never
inserted into the DUT. Each source has exactly one pending
latch. A refire while that latch is occupied is reported as `source_overrun`;
the testbench does not hide it in an unbounded queue.

Candidate-specific TB-only bindings are retained. They must satisfy the
[zero-feature native-binding contract](verification/aer-native-capability-profile.md#native-harness-boundary):
pin wiring, stateless address/bitmap observation, and native ACK timing are
allowed; hardware features and stateful decoding are not. Earlier no-binding
or inline-only directions are superseded. Approved workloads, traces,
scoreboards, and metrics remain unchanged.

## 2. Implemented workloads

| Workload | Stimulus | Main question |
| --- | --- | --- |
| `basic_single` | isolated source-0 events | Does basic address-event transport work without loss or duplication? |
| `basic_sparse` | low-rate events rotated across sources | Are sparse AER events reconstructed in source-local order with low latency? |
| `basic_simultaneous` | every source requests on the same cycle | Does arbitration legally drain all simultaneous events? |
| trace `pairwise_contention` | every unordered address pair under identity and affine mappings | Which address pairs expose partition, HOL, priority, or overlap effects? |
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
| trace `mixed_phase_always_ready` | matched temporal and spatial A/B/A phases without reset | Does phase history leave persistent performance bias? |

The deterministic generator also supports manifest-controlled `basic_sparse`,
`basic_simultaneous`, `uniform`, `elephant_mouse`, `global_fanin`,
`local_cluster`, `distributed_burst`, `retrigger`, `timing_pair`,
`backpressure_shock`, `rate_shape`, `matched_spatial`, `moving_hotspot`,
`rotating_victim`, `phase_transition`, and `mixed_phase_always_ready`. Use this trace path for final
cross-candidate comparisons:
the complete occurrence stream is generated before any DUT `ready` is observed.

The official full screening input is the 50-run N=16
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
- source payload changing during a continuous input stall; and
- retire payload/source changing during a continuous output stall.

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
- pairwise completion latency, service skew, order bias, and worst pair; and
- mixed-phase backlog, latency, service, and matched phase deltas.

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
RUN. The current reference and historical reproductions are classified as:

| Candidate | Role | Always-ready core | Backpressure | Polarity/type | Independent-lane stall |
| --- | --- | --- | --- | --- | --- |
| Ganghee raw cluster2 | current address-only reference | RUN, fixed N=16 | SKIP | SKIP | SKIP |
| Ganghee direct-coordinate fovea | historical reproduction | RUN, fixed N=16 | SKIP | SKIP | SKIP |
| Hyeonsu rotation-priority | historical reproduction | RUN, fixed N=16 | RUN | SKIP | SKIP |
| DREC prefix N=16/K=4 | historical research reproduction | RUN, fixed N=16 | RUN | SKIP | RUN |
| legacy baseline / A23 | historical calibration | RUN | RUN | SKIP | SKIP |

Ganghee's original direct-coordinate RTL was not modified. Its historical
Xcelium 23.09 qualification passed all 10 then-supported always-ready workloads.
The run exposed capacity limitations,
including 128/272 overrun in `limit_elephant_mouse`, 128/256 overrun in
`limit_retrigger`, and 17-cycle maximum end-to-end latency in simultaneous and
global-fan-in traffic. These are measured structural limits while accepted-event
correctness remains PASS.

Raw cluster2's earlier 18-run result is also historical. Current ranking uses
the generator-v4 50-run full suite and the exact 22-run capacity subset; older
10/18/46-run results are not silently promoted to current-suite qualification.

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

Reproduce the historical direct-coordinate fovea without editing its RTL:

```bash
setenv AER_GANGHEE_TOP aer_tx16_trad_rowcol_fovea
setenv AER_GANGHEE_FILELIST /absolute/path/to/ganghee-native.f
scripts/run_ganghee_native_benchmark.sh
```

The Ganghee file list must contain the original top and its arbiter dependencies
using absolute paths. Its runner deliberately rejects backpressure workloads.

Generate or run the exact 22-trace capacity subset. DREC and Hyeonsu modes are
historical reproductions; `ganghee-cluster2` is the current reference mode:

```bash
scripts/run_common_multilane_benchmark.sh generate-only
scripts/run_common_multilane_candidate.sh ganghee-cluster2
AER_SIMULATOR=xrun scripts/run_common_multilane_benchmark.sh drec-prefix 4
AER_SIMULATOR=xrun \
  scripts/run_common_multilane_candidate.sh clean rotation-priority
```

The current A1 common runners automatically create `*.pairs.json` for the two
pairwise runs and `*.mixed.json` for the two mixed-phase runs. Missing inputs,
invalid provenance, analyzer exceptions, or missing/freshness-failed result
files propagate nonzero because the runners use `set -e`. Analyzer process
success alone is not metric qualification: pairwise must report `COMPLETE`
with no dropped/censored/nonevaluable pairs, and mixed must report
`correctness_status=qualified_pass`. A partial, `evaluable=0`, or unqualified
artifact must be rejected by the fail-closed receipt/ranking gate even if the
analyzer successfully wrote JSON.

Aggregate result files from multiple candidates/seeds:

```bash
python3 benchmarks/clean_slate_aer/aggregate.py \
  result-a.csv result-b.csv \
  --events result-a.events.csv --events result-b.events.csv \
  --output /tmp/aer-common-summary.csv
```

## 7. Current gaps

The written specification includes, but the current common runner does not yet
fully implement, native `basic_polarity`, automatic 16/64/256 `limit_scale`,
and fixed-pin `limit_pin_budget`. `basic_reset_drain` and independent multi-lane
stall qualification now have dedicated tests. Do not claim unimplemented items
as qualified results. The old in-SV `limit_load` uses
a per-source probability; frozen comparisons should use the deterministic trace
generator, whose `load` is aggregate offered events/cycle.

The final cross-candidate evaluation layer is also not yet implemented. Raw
cluster2 is the current conventional address-only reference; direct-coordinate
fovea, Hyeonsu, and DREC commands above reproduce historical comparisons and do
not select a current base or finalist. Before ranking frozen candidates, the
team must still:

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
