# Ganghee anti-reconstruction regression

## Contract

Mandatory traffic carries AER address identity only. `tb_only_event_id`, event
sequence, polarity/type annotation, and arbitrary pending `source_event` values
may not be used to synthesize a completed address.

The one-lane native normalizer derives `retire_event` from native `addr`.  The
cluster2 common path instantiates the raw RTL directly in `aer_clean_tb`, drives
`req = source_valid`, and observes `valid/row/col_mask` in a non-synthesizable
monitor.  The monitor expands each set column to `row*4+col` for scoreboard
bookkeeping.  It contains no queue, arbitration, request masking, stored grant,
sink-ready behavior, or metadata reconstruction. Raw results are not suppressed
when no request is pending; they remain visible to the common scoreboard as
duplicate/phantom completions.

Logical occurrence cycles and logical sources in JSONL traces are unchanged.
`prepare_sv_trace.py` projects them to address-only `y*width+x`; polarity and
event type remain annotations rather than transported payload.

## Regression gates

`tests/clean_native/run_binding_test.sh` runs:

1. structural lint of the native binding and direct cluster2 section;
2. mutation unit tests proving pending-event and free-metadata copies fail;
3. a native-address canary with five deliberately mismatched metadata values;
4. a raw cluster2 direct-instantiation canary spanning both outputs, all four
   rows, and multiple column bits.

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
