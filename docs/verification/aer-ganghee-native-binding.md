# Ganghee native AER clean-benchmark binding

## Purpose and boundary

The common deterministic trace, source pending-latch model, scoreboard, and
metrics remain unchanged. Ready/valid is not imposed as Ganghee's hardware
interface. An Xcelium configuration replaces only the common TB's candidate
cell with a TB-only normalizer that instantiates Ganghee RTL through its native
ports exactly as published:

```text
input  clk
input  rst
input  req[15:0]
output valid
output addr[3:0]
```

Ganghee's original RTL and TB are external inputs to the runner. They are not
modified or copied into this repository.

## Stateless mapping

| Common TB observation | Native signal/mapping |
| --- | --- |
| clock | `clk` directly |
| active-low common reset | `rst = ~rst_n` |
| sixteen source pending bits | `req = source_valid & ~current_ack_onehot` |
| implicit completion | `valid && source_valid[addr]` |
| acknowledged source | exactly `addr[3:0]` |
| common completed event | current pending event at `source_event[addr]`, TB-only reconstruction |

The binding contains no sequential process, event storage, FIFO, arbitration,
grant history, or output-backpressure compensation. The native DUT alone
selects `addr`. The reconstructed event value and source identity are
scoreboard sideband; neither `tb_only_event_id` nor an arbitrary payload is
added to Ganghee's physical native interface.

As soon as `valid/addr` identifies a still-pending request, the TB driver masks
that one source from native `req`. Thus the acknowledged request is low before
the next active sampling edge, while all other pending sources remain visible.
At that edge the common scoreboard records accept and delivery together and
clears exactly that pending source. The fastest legal same-source retrigger
waits until the following falling edge, verifies that the request is low, then
presents the next event. This prevents an edge-sampled native implementation
from capturing the old request again. The mask is a combinational consequence
of the current result; it is neither stored history nor duplicate suppression.

A native `valid/addr` observation for a source whose request is no longer live
is reported as duplicate/phantom behavior and is not converted into another
completion. This is a protocol check, not stateful duplicate suppression.

## Capabilities

Supported:

- exactly 16 logical sources and one native completion per cycle;
- active-high native reset derived from the common active-low reset;
- sink-always-ready core tests: `basic_single`, `basic_sparse`,
  `basic_simultaneous`, `limit_load`, `limit_elephant_mouse`,
  `limit_global_fanin`, `limit_local_cluster`,
  `limit_distributed_burst`, `limit_retrigger`, and
  `limit_timing_fidelity`;
- deterministic prepared traces only when their frozen sink schedule is
  `always_ready`;
- common occurrence, TB-only identity, source-overrun, correctness, latency,
  deadline, fairness, and event-metrics output.

Explicitly unsupported:

- `basic_backpressure`, `limit_backpressure_shock`, periodic sink stalls, or
  any other output-backpressure schedule;
- interpreting `valid` as a held transaction after its corresponding `req`
  has cleared;
- more or fewer than 16 sources, more than one retire lane, or a native event
  payload wider than source identity;
- using the binding to improve throughput, queue requests, change selection,
  or repair native protocol behavior.

The current normalized event is recoverable only because the common source
model permits one live pending event per source. An occurrence arriving while
that latch is occupied remains `source_overrun`; the binding does not queue it.

## External RTL run

Xcelium configuration binding is used so `tb/clean/aer_clean_tb.sv` remains
byte-for-byte unchanged. Supply either a single source path or a file list and
the actual native module name:

```bash
AER_GANGHEE_TOP=<native_module_name> \
AER_GANGHEE_RTL=/absolute/read-only/path/to/native_rtl.sv \
AER_CLEAN_OUT=/tmp/ganghee-native-clean \
scripts/run_ganghee_native_benchmark.sh
```

For multiple original sources:

```bash
AER_GANGHEE_TOP=<native_module_name> \
AER_GANGHEE_FILELIST=/absolute/read-only/path/to/native_files.f \
scripts/run_ganghee_native_benchmark.sh basic_single basic_simultaneous
```

The runner reads external RTL in place. It never stages or copies it. Trace
mode uses the common JSONL/manifest preparation path and rejects a nonzero
sink-mode field before elaboration. Its result candidate is explicitly named
`ganghee-native-coordinate-source-projection`: native `addr` validates which
source/coordinate was selected, but the common TB reconstructs the event
record from that source's pending latch. Consequently these results do **not**
demonstrate physical transport of polarity or event type by the native DUT.

## Binding-level verification

The repository test fixture is an edge-sampled registered native protocol
model, not Ganghee RTL. Leaving `req` high through the next sampling edge would
make this fixture return a duplicate. It verifies simultaneous requests,
onehot implicit acknowledgement, pending-event reconstruction, the fastest
safe same-source retrigger, and reports a measured `duplicates=0`:

```bash
tests/clean_native/run_binding_test.sh
```

Passing this fixture validates the TB binding contract only. Final native RTL
qualification still requires the external original RTL under Xcelium and the
supported sink-always-ready suite.

## Local qualification (2026-08-06)

| Check | Result |
| --- | --- |
| edge-sampled native fixture | PASS: issued 5, acknowledgements 5, native results 5, masked sampling edges 5, duplicates 0 |
| structural repository self-check | PASS |
| clean-slate Python unit tests | PASS: 14/14 |
| deterministic trace-generator self-test | PASS: 10 workloads |
| runner capability parsing | PASS: all 10 always-ready tests accepted |
| unsupported capability guards | PASS: both backpressure tests rejected with exit 2 |

The local host has no Xcelium executable and Ganghee's read-only original RTL
was not supplied to this worktree. Therefore the external native RTL suite was
not claimed as run. An attempted pre-existing Icarus common-mock regression
stopped at elaboration because that Icarus build does not support the common
interface-modport and concurrent-assertion constructs; it did not execute a
DUT and is not a native-binding failure. The committed runner is the Xcelium
qualification path for the external original.
