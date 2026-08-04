# Clean-slate deterministic AER event traces

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

When an event occurs while its source is not ready, a benchmark driver should
place it in architecture-neutral pending stimulus state and present it under
the DUT's normal ready/valid contract. It must not regenerate, discard, delay
the occurrence timestamp, or consume random numbers based on `ready`.

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
