# A23 committed RTL functional verification

Date: 2026-08-02

Branch: `verification/a23-functional`

Design under test: committed integration RTL at `57d17e6`

## Scope

This verification drives `a23_ee430_core` from the repository file list. It does
not use the temporary A3 precheck wrapper and it does not modify functional RTL.
The test adds a per-source cycle scoreboard and checks:

- missing, duplicate, source-local reorder, and address/source corruption;
- one-hot-or-zero input acceptance;
- accepted minus emitted occupancy in the inclusive range zero through two;
- continuous input/output initiation interval and bounded round-robin service;
- random valid/address traffic with random output backpressure;
- stable valid/address/source throughout an output stall;
- a full 30-cycle stall followed by simultaneous drain and refill; and
- reset flush and the first post-reset transfers while source valid remains high.

The reproducible entry point is:

```bash
scripts/run_a23_functional_checks.sh
```

It runs source counts 1, 3, and 4 with deterministic seeds 17, 23001, and
48879. Set `AER_SIMULATOR=iverilog` or `AER_SIMULATOR=verilator` to select the
simulator and set `AER_SIM_OUT` to keep generated files outside the repository.

## Results

The added committed-core scoreboard passed with Icarus Verilog 12.0 and
Verilator 5.032 for all source-count/seed combinations.

| Sources | Continuous input II | Continuous output II | Max handshake service gap | Occupancy range |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 1 | 0-2 |
| 3 | 1 | 1 | 3 | 0-2 |
| 4 | 1 | 1 | 4 | 0-2 |

The integration commit's existing common regression also passed. Its measured
latency and throughput remain those recorded in `docs/experiments/a23-ee430-core.md`:

| Workload | Average/max latency (cycles) | Throughput (event/cycle) |
| --- | ---: | ---: |
| single | 2.0000 / 2 | 0.941176 |
| simultaneous | 2.0000 / 2 | 0.984615 |
| burst | 2.0000 / 2 | 0.993789 |
| backpressure | 5.0000 / 5 | 0.397516 |

The stream-specific measurement excludes fill/drain overhead and confirms a
steady-state throughput of exactly 1 event/cycle.

## Reset contract observation

`src_ready_o` can be combinationally high while `rst_ni` is low when a request
is asserted. Sequential TX/RX state is still cleared, `event_valid_o` is low,
and the first transfer after reset release is correct. The test therefore counts
ready/valid transfers only while reset is deasserted and records
`reset_ready_high=1` as an explicit interface-contract observation.

This is not a functional failure under the current contract. If the official
testbench requires ready low during reset, reset gating should be evaluated as a
separate RTL change because it adds reset to the combinational ready path.

## Tool observations

- The committed unpacked-array `a23_ee430_dut` wrapper is accepted by Verilator.
  Icarus 12.0 cannot elaborate that wrapper's whole unpacked-array port
  connection. The added test drives the committed core's packed port directly,
  which Icarus and Verilator both accept.
- Existing warnings are limited to missing module timescales, two intentionally
  unconnected completion outputs, and testbench style/width warnings. No latch,
  combinational-loop, multi-driver, or Verilator `UNOPTFLAT` warning was seen.

## Verdict

PASS. No functional RTL fix is indicated by this regression. Genus timing/PPA
comparison remains the next design gate.
