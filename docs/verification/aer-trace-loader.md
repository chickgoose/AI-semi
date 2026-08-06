# Deterministic Trace-to-SV Connection

The canonical workload remains the generated JSONL plus its per-run manifest.
SystemVerilog does not provide a portable JSON parser, so the runner validates
the manifest, event schema, count, filename, and SHA256 in Python before emitting
a numeric simulator input.  The numeric file is derived build output, not a
second source of truth.

The preparation step also enforces the AER-aligned invariant
`logical_source == y * width + x`.  A workload cannot turn the source address
into an unrelated arbitrary payload.  Workload names are likewise not exposed
through `event_type`; ordinary events use `spike`, while a test may explicitly
define a real semantic type such as `timing_a` or `timing_b`.

The source coordinate, polarity, and semantic event type are packed into the
normalized event address.  `tb_only_event_id`, occurrence cycle, and deadline
remain scoreboard-only.  A normalized retire-source sideband lets the common
scoreboard select the originating one-entry source latch; it is produced by the
candidate adapter and is not an arbitrary physical-link payload.

## Smoke run

```bash
python3 benchmarks/clean_slate_aer/generate_trace.py \
  --manifest benchmarks/clean_slate_aer/manifest.smoke.json \
  --output-dir /tmp/aer-clean-traces

AER_NUM_SOURCES=4 \
AER_TRACE_JSONL=/tmp/aer-clean-traces/trace_smoke.events.jsonl \
AER_TRACE_MANIFEST=/tmp/aer-clean-traces/trace_smoke.manifest.json \
scripts/run_clean_benchmark.sh mock
```

Trace mode and the older in-SV synthetic tests are both retained during
calibration.  Benchmark-freeze reports must use the deterministic trace path.
The run manifest carries an `always`, `periodic`, or bounded `shock` sink-ready
schedule, and the preparation step places that schedule in the validated numeric
header.  Source occurrence and sink backpressure are therefore reproducible from
one run description rather than selected from the DUT or a test-name heuristic.

Every result row also carries a `candidate` identity supplied by the runner.
The aggregator includes it in the run and sweep keys, so measurements from two
implementations with the same workload, seed, and load cannot be merged.
