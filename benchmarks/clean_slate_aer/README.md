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

## Ratios and aggregation

Rows are grouped by `(test, load_pct)` across every input file and seed.

- `delivery_ratio = delivered / accepted`: post-accept transport delivery.
- `acceptance_ratio = accepted / (generated - source_overrun)`: how many events
  retained by the source were accepted.
- `overrun_ratio = source_overrun / generated`: source-side saturation loss.
- `end_to_end_ratio = delivered / generated`: deliberately includes overrun.

Ratios use summed counters, not an unweighted mean of seed ratios. Average
latency and timing error are weighted by delivered events. Average throughput,
fairness, and request wait are arithmetic means across runs; their worst values
are minimum throughput/fairness and maximum latency/wait/timing error.

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
counts are classified as correctness failures.
