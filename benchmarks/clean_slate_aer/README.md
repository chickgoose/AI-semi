# Clean-slate AER benchmark utilities

## Deterministic event-trace generator

This directory contains an architecture-neutral trace generator. It creates
the complete occurrence trace before a testbench observes any source `ready`
signal. Arbitration, buffering, backpressure, and DUT latency therefore cannot
change offered traffic or random-number consumption.

The implementation uses only the Python standard library. A fixed SplitMix64
PRNG and canonical JSON serialization make identical manifests byte-for-byte
reproducible across runs.

## Event schema

Each output `*.events.jsonl` file contains one JSON object per line, ordered by
`occurrence_cycle` and then generator order:

| Field | Meaning | DUT visibility |
| --- | --- | --- |
| `occurrence_cycle` | Cycle when the event becomes eligible for injection | TB only |
| `tb_only_event_id` | Monotonic trace identity for matching and scoreboarding | TB only; never encode in DUT payload |
| `logical_source` | Architecture-neutral source index | Source sideband/driver selection |
| `x`, `y` | Event coordinate within the declared geometry | DUT payload |
| `polarity` | Signed event polarity, exactly `-1` or `1` | DUT payload |
| `event_type` | Semantic type string | DUT payload or adapter mapping |
| `deadline` | Absolute cycle for deadline analysis | TB only |

The per-run output manifest repeats these classifications as
`dut_payload_fields`, `dut_sideband_fields`, and `tb_only_fields`. An adapter
may encode the DUT-visible fields to its native address format, but must keep
`tb_only_event_id` exclusively in its reference/scoreboard state.

When an event occurs while its source is not ready, the benchmark driver places
it in that source's single architecture-neutral pending latch and presents it
under the DUT's normal ready/valid contract. A later occurrence at the same
occupied source is counted as `source_overrun`; it is not hidden in an unbounded
TB queue. The driver must not regenerate events, delay occurrence timestamps, or
consume random numbers based on `ready`.

## Manifest format

The top-level JSON object has `schema_version: 1` and a non-empty `runs` array.
Every run records all reproducibility inputs:

```json
{
  "name": "uniform_load_0p50",
  "workload": "uniform",
  "seed": 2001,
  "geometry": {"width": 8, "height": 8},
  "load": 0.50,
  "stim_cycles": 512,
  "parameters": {"deadline_slack": 32}
}
```

`load` is aggregate offered events per occurrence cycle. Integer load produces
that many events each cycle; a fractional remainder is sampled by the fixed
PRNG. Workloads that are intrinsically finite (`basic_*`, `retrigger`, and
`timing_pair`) record load for provenance and use their documented count
parameters to form the trace. The output manifest records requested load and
actual `event_count`, so achieved load is always auditable.

The example manifest includes the full suite and three `uniform` points for a
load sweep:

- `basic_sparse`: deterministic low-count sanity events across the geometry.
- `basic_simultaneous`: several sources with the exact same occurrence cycle.
- `uniform`: uniform random sources at the requested aggregate load.
- `elephant_mouse`: a configurable hot source plus uniformly selected mice.
- `global_fanin`: many logical sources mapped to one target coordinate.
- `local_cluster`: traffic constrained to a square neighborhood.
- `distributed_burst`: temporally separated bursts rotating across quadrants.
- `retrigger`: repeated events from one logical source at a fixed interval.
- `timing_pair`: typed A/B pairs with a controlled cycle gap and deadline.
- `backpressure_shock`: low background traffic plus a high-rate shock window.

Optional parameters are validated when used. Common `deadline_slack` defaults
to 32 cycles. See [manifest.example.json](manifest.example.json) for workload-
specific parameters.

## Generate and verify

From the repository root:

```bash
python3 benchmarks/clean_slate_aer/generate_trace.py \
  --manifest benchmarks/clean_slate_aer/manifest.example.json \
  --output-dir /tmp/clean-slate-aer-traces
```

The output directory receives, for each run, an event JSONL file and a run
manifest containing the seed, geometry, requested load, stimulus cycles,
event count, and trace SHA256. `generation-index.json` summarizes the complete
invocation. Generated traces are outputs and should not be committed here.

List workload identifiers or run the self-test with:

```bash
python3 benchmarks/clean_slate_aer/generate_trace.py --list-workloads
python3 benchmarks/clean_slate_aer/self_test.py
```

The self-test generates the full example suite twice in temporary directories,
requires byte-identical results, validates every event and DUT/TB field
classification, checks workload-specific signatures, and confirms that a seed
change changes a stochastic trace.

## Architecture-neutral result aggregator

This directory contains an architecture-neutral CSV aggregator. It does not
know whether a row came from a fixed-priority baseline, round-robin A23, a
serialized ROW/COL design, or another implementation.

## Input schema

The required header is:

```text
test,seed,load_pct,stim_cycles,generated,source_overrun,accepted,delivered,errors,total_cycles,avg_e2e_latency,max_e2e_latency,avg_internal_latency,max_internal_latency,throughput,fairness,max_request_wait,avg_timing_error,max_timing_error
```

Additional columns are allowed and ignored. Counter fields must be nonnegative
integers; load and measured metrics must be finite nonnegative numbers. `test`
and `seed` are labels, so numeric and nonnumeric seed identifiers are accepted.
An optional `candidate` column is strongly recommended and is emitted by the
common runner.  It becomes part of every aggregation key so different DUTs that
use the same test, seed, and load can never be pooled accidentally.  Older files
without it are labeled `unspecified` for backward-compatible single-candidate
analysis.

## Ratios and aggregation

Rows are grouped by `(candidate, test, load_pct)` across every input file and
seed. Files without `candidate` use the compatibility label `unspecified`.

- `delivery_ratio = delivered / accepted`: post-accept transport delivery.
- `acceptance_ratio = accepted / (generated - source_overrun)`: how many events
  retained by the source were accepted.
- `overrun_ratio = source_overrun / generated`: source-side saturation loss.
- `end_to_end_ratio = delivered / generated`: deliberately includes overrun.

Ratios use summed counters, not an unweighted mean of seed ratios. Average
latency and timing error are weighted by delivered events. Average throughput,
fairness, and request wait are arithmetic means across runs; their worst values
are minimum throughput/fairness and maximum latency/wait/timing error.

## Optional per-event result schema

The summary CSV cannot recover latency distributions, deadline outcomes, or
per-source service intervals. A benchmark may therefore emit an additional
per-event CSV and pass it with `--events`. Its required header is:

```text
test,seed,load_pct,tb_only_event_id,logical_source,source_count,occurrence_cycle,accept_cycle,delivery_cycle,deadline_cycle,observation_end_cycle,event_state
```

`candidate`, `test`, `seed`, and `load_pct` form the run key and must match one
summary row. The compatibility label applies when both older files omit
`candidate`.
There must be exactly one per-event row for every generated event in each run
that supplies event detail. `tb_only_event_id` must be unique inside that run.
It exists only for trace matching and scoreboarding and is never DUT payload.
If present, `candidate` is also part of the run key and must match the summary
CSV.  Cross-candidate aggregation always remains separated.

All time fields are integer cycles:

- `occurrence_cycle` is when the trace event occurs, independent of source
  readiness.
- blank `accept_cycle`, `delivery_cycle`, or `deadline_cycle` means the
  corresponding event did not occur or no deadline was assigned.
- `observation_end_cycle` is the last cycle included by that run. It is
  repeated on every row and must be constant within the run.
- `logical_source` is in `[0, source_count)`. Repeating `source_count` makes
  sources with no delivered event observable instead of silently dropping
  them from fairness/service accounting.

`event_state` is exactly one of:

| State | Required cycles | Interpretation |
| --- | --- | --- |
| `source_overrun` | accept/delivery blank | terminal source-latch saturation loss |
| `pending` | accept/delivery blank | retained but not accepted when observation ended |
| `accepted` | accept present, delivery blank | accepted but not delivered when observation ended |
| `delivered` | accept and delivery present | completed event |

The aggregator validates per-run event counts and the overrun/accepted/
delivered state counts against the summary row. Event files may cover only a
subset of summary seeds; the output marks each `(candidate, test, load_pct)` group as
`COMPLETE`, `PARTIAL`, or `NOT_PROVIDED` rather than silently treating missing
detail as zero. The normal load summary pools event samples across supplied
seeds. JSON also includes exact `(candidate, test, seed, load_pct)` records under
`event_runs`; `--event-output` writes those same seed-specific records as CSV.

### Tail latency and censoring

End-to-end latency is `delivery_cycle - occurrence_cycle`; internal latency is
`delivery_cycle - accept_cycle`. Only `delivered` rows are samples. `pending`
and `accepted` rows are right-censored and are counted in
`censored_event_rows`, but are not replaced with `observation_end_cycle` in a
latency percentile. `source_overrun` is a terminal saturation loss, not a
latency sample or a censored sample.

P50/P95/P99 use the deterministic nearest-rank definition: sort `N` samples
and select rank `ceil(percentile * N / 100)`, with ranks starting at one. An
empty delivered sample set produces a blank percentile.

### Deadlines

A delivered event meets its deadline when `delivery_cycle <= deadline_cycle`;
delivery one or more cycles later is a miss. A terminal `source_overrun` with a
deadline is a miss. An undelivered `pending` or `accepted` event is a definite
miss once `observation_end_cycle >= deadline_cycle`; before that boundary its
deadline outcome is censored.

`deadline_miss_ratio` is `deadline_misses / (deadline_events -
deadline_censored)`. The censored count is reported alongside it so a short
observation cannot appear as an artificially good result. Events with blank
deadlines are excluded.

### Source service gaps and sliding windows

For every run and every source, delivered cycles are sorted. Consecutive
differences form the service-gap samples used for P95, P99, and maximum service
gap. A source with fewer than two deliveries contributes no fabricated gap;
sources with no delivery are reported explicitly in
`service_sources_unobserved`.

`--service-window-cycles W` selects a cycle window (default 64). For each
source, every full integer-cycle sliding window `[start, start + W)` from cycle
zero through `observation_end_cycle` is evaluated. The aggregator reports:

- total source-window pairs (`service_source_windows`);
- the minimum delivered service in any source window;
- the count and ratio of source windows with zero service.

Runs shorter than one full window contribute zero windows and a blank minimum.
These are offered-traffic observations, not a proof that a source requested
continuously; bounded-arbitration claims still require request/handshake-aware
verification.

## Verdict policy

Correctness and capacity are separate axes.

`CORRECTNESS_FAIL` is reserved for explicit scoreboard errors, accepted events
that were not delivered, or impossible counter relationships such as delivered
greater than accepted. Source overrun, reduced acceptance, a throughput plateau,
high latency, or long request waits do not become correctness failures by
themselves.

The default saturation knee is the first increasing load where either:

- aggregate acceptance ratio falls below 0.99; or
- aggregate source-overrun ratio exceeds 0.01.

That load and all following loads are labeled `SATURATED`. Tail degradation is
reported when mean end-to-end latency, mean per-run maximum request wait, or
mean timing error reaches 1.5x the last pre-knee load. These thresholds are
policy knobs, not properties of a particular architecture:

```sh
python3 benchmarks/clean_slate_aer/aggregate.py \
  --acceptance-floor 0.995 \
  --overrun-ceiling 0.005 \
  --tail-factor 2.0 \
  input.csv
```

This makes a saturated baseline visible as a knee followed by tail growth while
preserving `correctness=PASS` when all retained/accepted events are delivered
without scoreboard errors.

## Usage

CSV load summary to stdout:

```sh
python3 benchmarks/clean_slate_aer/aggregate.py run-a.csv run-b.csv
```

JSON includes per-test knee/correctness summaries as well as per-load records:

```sh
python3 benchmarks/clean_slate_aer/aggregate.py \
  --format json --output /tmp/aer-summary.json run-*.csv
```

Add optional per-event detail (repeat `--events` for multiple files):

```sh
python3 benchmarks/clean_slate_aer/aggregate.py \
  run-summary.csv \
  --events run-events.csv \
  --service-window-cycles 64 \
  --event-output /tmp/aer-event-runs.csv \
  --output /tmp/aer-metrics-v2.csv
```

Every latency, deadline, gap, and window value emitted by this layer remains in
cycles. The aggregator intentionally has no clock-frequency or nanosecond
option. Cycle-to-nanosecond conversion belongs to the separate PPA layer that
owns post-layout frequency and corner information.

`--fail-on-correctness` returns exit status 2 for CI when any test has a real
correctness failure. Saturation alone still returns success.

## Fixture self-test

```sh
python3 -m unittest discover \
  -s benchmarks/clean_slate_aer/tests -v
```

The saturation fixture proves that 30% source overrun, a visible knee, and large
tail latency remain a performance result rather than a correctness failure. A
separate malformed result proves that explicit errors and impossible delivery
counts are classified as correctness failures. Deterministic per-event
fixtures lock nearest-rank percentiles, censored and terminal-loss handling,
deadline equality, unobserved sources, consecutive service gaps, and exact
sliding-window counts.
