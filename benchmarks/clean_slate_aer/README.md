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
| `logical_source` | Firing-neuron address; the complete mandatory event identity | DUT address/source selection |
| `x`, `y` | Coordinate aliases derived from `logical_source` | TB metadata |
| `polarity` | Signed polarity for a future optional capability | TB metadata in the mandatory suite |
| `event_type` | Semantic label for optional analysis | TB metadata in the mandatory suite |
| `relation_id`, `relation_role` | Optional timing-pair/group relation | TB only; never encode in DUT payload |
| `deadline` | Absolute cycle for deadline analysis | TB only |

The per-run output manifest declares `event_identity_mode: address_only`, puts
`logical_source` in `dut_address_fields`, and leaves `dut_payload_fields`
empty. Polarity/type remain trace metadata unless a separately frozen optional
suite and native contract explicitly carry them. An adapter may encode the
address into its native format, but must never recover metadata from TB pending
state and present it as if the physical link transported it.

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

The example manifest includes one illustrative run per workload family plus
three `uniform` points for a load sweep. The official full suite is the frozen
50-run N=16 manifest described below:

- `basic_sparse`: deterministic low-count sanity events across the geometry.
- `basic_simultaneous`: several sources with the exact same occurrence cycle.
- `pairwise_contention`: every unordered source-address pair under the same
  quiescent ingress spacing, with optional address permutation. The DUT is not
  reset between pairs; the analyzer reports any prior-pair service overlap.
- `uniform`: uniform random sources at the requested aggregate load.
- `elephant_mouse`: a configurable hot source plus uniformly selected mice.
- `global_fanin`: many distinct source coordinates occurring on the same cycle.
- `local_cluster`: traffic constrained to a square neighborhood.
- `distributed_burst`: temporally separated bursts rotating across quadrants.
- `retrigger`: repeated events from one logical source at a fixed interval.
- `timing_pair`: typed A/B pairs with a controlled cycle gap and deadline.
- `backpressure_shock`: low background traffic plus a high-rate shock window.
- `rate_shape`: the same event count and source histogram with only temporal
  burst size changed.
- `matched_spatial`: local and dispersed placements with identical occurrence
  cycles and demand-by-rank.
- `moving_hotspot`: one or more nonstationary hot sources with a fixed dwell.
- `rotating_victim`: every source becomes the low-rate victim in turn while
  aggressor traffic remains active.
- `phase_transition`: sparse, near-saturation, overload, post-overload sparse
  probes, and a zero-injection drain phase in one trace.
- `mixed_phase_always_ready`: one no-reset trace that pairs Bernoulli versus
  count-matched smooth traffic, sustained versus rotating partitions, and a
  spatial A/B/A replay to expose history, hysteresis, and mapping sensitivity.

`source_permutation` is a generator-level metamorphic control, not a candidate
feature. It supports identity, affine bijections, mirror, rotate, transpose,
and bit-reversal mappings. Event times and TB identities remain unchanged;
only the AER source/address labels move. A candidate is allowed to benefit from
spatial locality. The paired mapping runs merely distinguish that legitimate
benefit from accidental dependence on fixed row-major source numbers.

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
event count, declared/actual mean load, peak events/cycle, report group, and
trace SHA256. `generation-index.json` summarizes the complete
invocation. Generated traces are outputs and should not be committed here.

The committed `fixtures/neutrality_n16_golden.json` freezes all 50 expected
event counts, achieved loads, peak rates, report groups, and SHA256 values. The
neutrality self-test fails if a generator change silently changes any official
trace.

List workload identifiers or run the self-test with:

```bash
python3 benchmarks/clean_slate_aer/generate_trace.py --list-workloads
python3 benchmarks/clean_slate_aer/self_test.py
python3 benchmarks/clean_slate_aer/neutrality_self_test.py
```

The self-test generates the full example suite twice in temporary directories,
requires byte-identical results, validates every event and DUT/TB field
classification, checks workload-specific signatures, and confirms that a seed
change changes a stochastic trace.

## Frozen N=16 candidate-neutral suite

[manifest.neutrality-n16.json](manifest.neutrality-n16.json) is the common
exact-trace suite for candidate comparison. It has 50 sink-always-ready runs,
uses the shared N=16 geometry supported by the current candidates, fixes the
logical event to a coordinate-address spike, and never emits two occurrences
from the same logical source in one cycle. It includes:

- a 0.125 through 2.0 event/cycle uniform sweep with three fixed seeds;
- matched 1/4/16-event temporal bursts at the same 0.5 event/cycle mean;
- matched local/dispersed spatial traffic;
- single and multiple moving hotspots;
- rotating starvation victims under identity and affine address mappings;
- exhaustive pairwise address contention under identity and affine mappings;
- sparse-to-overload-to-post-sparse-to-drain phase transitions;
- no-reset mixed temporal/spatial phases under identity and bit-reverse maps;
- cross-source timing pairs under independent background traffic;
- legacy AER fan-in, elephant/mouse, and retrigger bottlenecks with relabeling
  controls.

Every candidate must consume the generated JSONL with the same recorded SHA.
The built-in SystemVerilog `limit_*` tests remain smoke/calibration tests and
must not be used for final cross-candidate ranking. Results are reported by
bottleneck family first; a candidate winning its natural workload is a valid
architectural advantage, not benchmark bias. Bias means omitting other major
AER bottlenecks, changing offered traffic, or giving a candidate free adapter
functionality.

For a `phase_transition` run, derive phase-local completion rate, p95 latency,
backlog peak/end, and recovery-to-zero directly from the exact trace and the
candidate's per-event CSV:

```bash
python3 benchmarks/clean_slate_aer/phase_metrics.py \
  --trace generated/phase_transition_s3501.events.jsonl \
  --run-manifest generated/phase_transition_s3501.manifest.json \
  --events results/phase_transition_s3501.events.csv \
  -o results/phase_transition_s3501.phases.json
```

The analyzer joins on the TB-only event ID and verifies the simulator's cycle
offset, so phase boundaries come from the frozen trace rather than being guessed
from candidate-dependent delivery time.

`timing_pair` events similarly carry TB-only `relation_id` and `relation_role`
fields. They never enter the DUT payload. Use `timing_pair_metrics.py` with the
same `--trace`, `--run-manifest`, and `--events` arguments to calculate the
cross-source A/B gap error, including dropped and censored pairs. This replaces
the old same-source consecutive-delivery approximation for timing-pair claims.

Use `pairwise_contention_metrics.py` with the same three inputs for a
`pairwise_contention` run. It reports every trial, repeated-address-pair
aggregates, completion latency, service skew, drop/censor counts, service-order
bias, distinct worst-completion/worst-skew pairs, and prior-pair overlap.
The official common multi-lane runners require both identity and affine
reports, join them by canonical relation ID, and publish one no-overwrite
`identity-vs-affine.json`. A report with dropped/censored or otherwise
non-evaluable pairs is preserved for diagnosis but makes the runner return 3
and is not rankable.

## Architecture-neutral result aggregator

This directory contains an architecture-neutral CSV aggregator. It does not
know whether a row came from a fixed-priority baseline, round-robin A23, a
serialized ROW/COL design, or another implementation.

## Detached release manifest

`benchmark_release.py` freezes an address-only benchmark release only after the
repository is clean. It reads the generator, v4 preparer, clean TB, runners,
official 48-run and 20-run manifests, golden fixture, and analyzers from a Git
commit or tree object and records their SHA-256 values. The output must be a
sidecar outside the repository; it is never included in its own hash set.

Generation and validation are fail-closed on any tracked or untracked dirty
path. Artifact paths under `results/` or `logs/`, and `*.log` artifacts, are
rejected. Test evidence is embedded only as a PASS marker, not as a result or
log path. See `benchmark_release.py generate --help` for the required explicit
file list and `benchmark_release.schema.json` for the interchange schema.

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
`throughput_stddev` is also emitted across runs. It does not replace publishing
the individual seed rows or a confidence interval when making a saturation
claim.

The common SV runner defines `throughput` as normalized deliveries completed
during the fixed `stim_cycles` measurement window divided by `stim_cycles`.
Candidate-dependent drain cycles are excluded. Total delivered count and drain
completion remain separate correctness/capacity observations. Sustainable
throughput is inferred from the plateau of the frozen multi-load sweep, never
from one sparse trace or from a hard-coded lane count.
New runner CSV files also include `measurement_delivered` and
`measurement_cycles`; the aggregator rejects a row when these counters disagree
with its throughput value. On a multi-load sweep, a fixed-window completion
ratio below `--completion-floor` (default 0.95) can mark saturation even when a
deep buffer postpones visible loss until drain.

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

For candidate ranking, use the demand-conditioned fields rather than the legacy
raw Jain field. `demand_normalized_acceptance_fairness` and
`demand_normalized_delivery_fairness` first divide each active source's service
by that source's generated demand, then apply Jain's index. Never-offered
sources are excluded. `zero_demand_service_source_window_ratio` evaluates only
windows overlapping a live occurrence-to-delivery demand interval. The raw
`fairness` column from the SV summary is retained for compatibility; on an
elephant/mouse trace it mostly describes the intentionally unequal traffic
distribution and is not arbitration fairness.

## Verdict policy

Correctness and capacity are separate axes.

`CORRECTNESS_FAIL` is reserved for explicit scoreboard errors, accepted events
that were not delivered, or impossible counter relationships such as delivered
greater than accepted. Source overrun, reduced acceptance, a throughput plateau,
high latency, or long request waits do not become correctness failures by
themselves.

The default saturation knee is the first increasing load where any of these
hold:

- aggregate acceptance ratio falls below 0.99; or
- aggregate source-overrun ratio exceeds 0.01; or
- in a sweep containing at least three loads, fixed-window completions divided
  by declared offered events fall below 0.95.

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

Compare the separate identity and affine pairwise analyzer artifacts by their
frozen canonical relation contract:

```sh
python3 benchmarks/clean_slate_aer/pairwise_cross_map_compare.py \
  --identity-manifest pairwise_contention_identity.manifest.json \
  --identity-report pairwise_contention_identity.pairs.json \
  --affine-manifest pairwise_contention_affine.manifest.json \
  --affine-report pairwise_contention_affine.pairs.json \
  --output pairwise_identity_vs_affine.json
```

The comparison fails closed on current pairwise-report provenance, permutation,
candidate, relation, repeat, canonical-pair, physical-address, measurement-state,
and overlap-summary disagreement. A result is rankable only when both reports are
`COMPLETE` and every trial is isolated. Partial/drop/censor or overlap evidence
sets `rankable=false` and suppresses global and per-pair completion/skew
aggregates, while retaining per-trial diagnostics, overlap strata, order changes,
and independent drop, censor, and combined drop-and-censor capacity deltas.

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
