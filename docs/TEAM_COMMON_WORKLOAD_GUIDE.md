# Team Common AER Workload and Testbench Guide

Status: shared team baseline, 2026-08-07

## 1. Purpose and boundary

This package freezes the workload, logical AER event meaning, source model,
scoreboard, and result schema before the team selects a new RTL architecture.
It does not select Ganghee, Junyoung, or Hyeonsu's RTL as the new design base.
The existing baseline and A23 profiles are historical benchmark-calibration
fixtures, not active final candidates or a starting point for Junyoung's new RTL.

The common logical event is `(source coordinate/address, optional polarity or
event type, occurrence time)`. A TB-only event ID tracks loss and duplication
but is never inserted into DUT payload. Each source has exactly one pending
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
| `basic_backpressure` | sparse events with repeating sink stalls | Is output stable during a stall and does it recover completely? |
| `limit_load` / trace `uniform` | seeded load from sparse through overload | Where do throughput plateau, latency growth, and source overrun begin? |
| `limit_elephant_mouse` | one hot source and one low-rate victim | Does biased traffic cause starvation or a long victim wait? |
| `limit_global_fanin` | all sources fire together periodically | How do arbitration and drain latency scale with fan-in? |
| `limit_local_cluster` | bursts from adjacent coordinates | Can the design exploit spatial locality efficiently? |
| `limit_distributed_burst` | equal bursts from dispersed coordinates | Is a locality optimization robust rather than overfit? |
| `limit_retrigger` | one source refires faster than service | How much source overrun occurs and what buffering/acceptance rate is needed? |
| `limit_timing_fidelity` / trace `timing_pair` | precise pairs under background traffic | How much does transport distort inter-event timing and deadlines? |
| `limit_backpressure_shock` | sustained traffic plus a long sink stall | What finite-storage limit, loss, and recovery behavior appear? |

The deterministic generator also supports manifest-controlled `basic_sparse`,
`basic_simultaneous`, `uniform`, `elephant_mouse`, `global_fanin`,
`local_cluster`, `distributed_burst`, `retrigger`, `timing_pair`, and
`backpressure_shock`. Use this trace path for final cross-candidate comparisons:
the complete occurrence stream is generated before any DUT `ready` is observed.

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
  service gaps, zero-service windows, correctness, and saturation knee.

## 4. Candidate capability policy

Every candidate runs through its native interface. The mandatory always-ready
core requires observable address correctness, occurrence-to-delivery latency,
loss/duplicate/phantom behavior, and per-source service. Output backpressure,
polarity/event type, and multi-lane retirement are optional suites.

An unsupported optional feature is `SKIP_UNSUPPORTED`, not FAIL and not zero
performance. The harness must not add hardware behavior to turn a SKIP into a
RUN. The checked profiles currently classify:

| Candidate | Always-ready core | Backpressure | Polarity/type | Multi-lane |
| --- | --- | --- | --- | --- |
| Ganghee direct-coordinate | RUN, fixed N=16 | SKIP | SKIP | SKIP |
| legacy baseline (historical calibration) | RUN | RUN | SKIP | SKIP |
| A23 EE430 (historical calibration) | RUN | RUN | SKIP | SKIP |

Ganghee's original RTL was not modified. Its Xcelium 23.09 qualification passed
all 10 supported always-ready workloads. The run exposed capacity limitations,
including 128/272 overrun in `limit_elephant_mouse`, 128/256 overrun in
`limit_retrigger`, and 17-cycle maximum end-to-end latency in simultaneous and
global-fan-in traffic. These are measured structural limits while accepted-event
correctness remains PASS.

## 5. Repository map

```text
benchmarks/clean_slate_aer/       trace generator, validator, aggregator, tests
tb/clean/                         common interface, source model, scoreboard
tb/clean/native/                  storage-free native observation bindings
tests/clean_native/               Ganghee binding protocol fixture
scripts/run_clean_benchmark.sh    mock and historical calibration runner
scripts/run_ganghee_native_benchmark.sh
docs/verification/               specification, profiles, results, PPA contract
```

## 6. How to run

Run Python/trace self-checks:

```bash
python3 benchmarks/clean_slate_aer/self_test.py
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
  --manifest benchmarks/clean_slate_aer/manifest.example.json \
  --output-dir /tmp/aer-common-traces

AER_TRACE_JSONL=/tmp/aer-common-traces/basic_sparse.events.jsonl \
AER_TRACE_MANIFEST=/tmp/aer-common-traces/basic_sparse.manifest.json \
scripts/run_clean_benchmark.sh baseline
```

Run Ganghee's fixed-16 native core without editing its RTL:

```bash
setenv AER_GANGHEE_TOP aer_tx16_trad_rowcol_fovea
setenv AER_GANGHEE_FILELIST /absolute/path/to/ganghee-native.f
scripts/run_ganghee_native_benchmark.sh
```

The Ganghee file list must contain the original top and its arbiter dependencies
using absolute paths. Its runner deliberately rejects backpressure workloads.

Aggregate result files from multiple candidates/seeds:

```bash
python3 benchmarks/clean_slate_aer/aggregate.py \
  result-a.csv result-b.csv \
  --events result-a.events.csv --events result-b.events.csv \
  --output /tmp/aer-common-summary.csv
```

## 7. Current gaps

The written specification includes, but the current common runner does not yet
fully implement, `basic_reset_drain`, native `basic_polarity`, automatic
16/64/256 `limit_scale`, fixed-pin `limit_pin_budget`, and a complete mixed-phase
trace. Do not claim these as qualified results. The old in-SV `limit_load` uses
a per-source probability; frozen comparisons should use the deterministic trace
generator, whose `load` is aggregate offered events/cycle.

The final cross-candidate evaluation layer is also not yet implemented. Before
ranking Ganghee fovea, Hyeonsu's frozen final RTL, and Junyoung's new clean-slate
RTL, the team must still:

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
