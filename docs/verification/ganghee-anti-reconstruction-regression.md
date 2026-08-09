# Ganghee anti-reconstruction regression

## Contract

Mandatory traffic carries AER address identity only. `tb_only_event_id`, event
sequence, polarity/type annotation, and arbitrary pending `source_event` values
may not be used to synthesize a completed address.

The one-lane native normalizer derives `retire_event` from native `addr`.  The
cluster2 production path uses a candidate-specific TB binding; the common TB
contains no inline cluster2 protocol implementation.  That binding instantiates
the raw RTL and derives a combinational current-result mask from native
`valid/row/col_mask`.  Its level request is
`req = source_valid & ~current_result_mask`, preventing a registered DUT from
sampling an acknowledged held request again on the same edge.

The mask affects request acknowledgement only.  Retirement expands every raw
set column to `row*4+col` without checking `source_valid`, `req`, or the mask.
Consequently a repeated raw result remains visible to the common scoreboard as
a duplicate/phantom completion.  The binding contains no queue, arbitration,
stored grant, sink-ready behavior, backpressure compensation, or metadata
reconstruction.

Logical occurrence cycles and logical sources in JSONL traces are unchanged.
`prepare_sv_trace.py` projects them to address-only `y*width+x`; polarity and
event type remain annotations rather than transported payload.

## Regression gates

`tests/clean_native/run_binding_test.sh` runs:

1. structural lint of both candidate-specific bindings and proof that the
   common TB contains no inline cluster2 protocol logic;
2. mutation unit tests proving pending-event and free-metadata copies fail;
3. a native-address canary with five deliberately mismatched metadata values;
4. a direct-instantiation regression oracle spanning both outputs, all four
   rows, and multiple column bits;
5. a registered held-request case proving six requests receive exactly six
   acknowledgements; and
6. a fault mock that repeats all six raw results, proving all six repeats remain
   visible as phantoms rather than being suppressed by acknowledgement state.

Reproduce locally with:

```bash
VERILATOR=/tmp/a7-sim-bin/verilator tests/clean_native/run_binding_test.sh
python3 -m unittest discover -s benchmarks/clean_slate_aer/tests -v
```

The external raw cluster2 regression uses identical prepared traces:

```bash
AER_GANGHEE_CLUSTER2_TOP=<raw_top> \
AER_GANGHEE_CLUSTER2_RTL=<read-only-raw-rtl.sv> \
scripts/run_ganghee_cluster2_benchmark.sh
```

No candidate RTL is copied or modified by these checks.
