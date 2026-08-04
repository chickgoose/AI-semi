# Clean-slate AER benchmark aggregator

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
