# A23 integration stress verification

Date: 2026-08-02

RTL under test: `57d17e6f6b811079e247aef357da466f73d84af4`

Branch: `verification/a23-stress`

## Verdict

PASS. The actual A23 integration completed all cycle-by-cycle data-integrity,
ordering, occupancy, round-robin fairness, reset-boundary, and pipeline-boundary
checks without an RTL failure.

- Icarus Verilog 12.0: 60/60 parameter/seed runs passed.
- Verilator 5.032: 60/60 parameter/seed runs passed.
- Total: 120/120 runs passed (`NUM_SOURCES=1,3,4`, seeds 1 through 20).
- Each run exercised eight deterministic phases, for 960 phase executions.

Simulation artifacts were written below `/tmp/a23-stress-run`; no result files
or generated binaries were placed in the repository.

## Independent test structure

The testbench directly instantiates `a23_ee430_core`, including the integrated
round-robin arbiter, bubble-free TX, and baseline elastic RX. It does not import
or copy an A1 testbench. Direct core instantiation also avoids an Icarus
elaboration limitation on the single-element unpacked-array port of the thin
`a23_ee430_dut` address-packing wrapper; the integration logic under test is
unchanged.

Every input handshake is stored as `{source,address,accept_cycle}` in a reference
queue. Every output handshake is checked against the queue head. The checker
detects missing, duplicate, corrupt, and reordered events and verifies that
`accepted-emitted` equals the physical TX+RX occupancy in the range 0 through 2.
It also checks grant onehot0, priority advance only on input handshake, stable
producer and output payloads while stalled, and simultaneous RX drain/TX refill/
input accept at full occupancy.

The eight phases per run are:

1. Continuous single-source stream.
2. All sources valid every cycle.
3. Unequal per-source burst lengths.
4. Random valid and handshake-dependent changing addresses.
5. Random output backpressure.
6. Alternating output ready (`0101`).
7. Long output stall followed by full-pipeline drain/refill.
8. Valid held across reset and immediate post-reset handshake.

## Results

Latency is measured from input handshake cycle to output handshake cycle.
Service gap is measured only in the all-sources-contending, downstream-ready
phase; waits caused by downstream backpressure are reported separately.

| Condition | Worst result | Seed / configuration |
|---|---:|---|
| Continuous single source | input II 1, output II 1, latency 2 | all seeds, all source counts |
| All sources continuously valid | service gap 4, max wait 3, latency 2 | all seeds, `NUM_SOURCES=4` |
| Unequal bursts, downstream ready | max wait 3, latency 2 | all seeds, `NUM_SOURCES=4` |
| Random valid/address, downstream ready | max wait 3, latency 2 | all seeds, `NUM_SOURCES=4` |
| Random backpressure | max latency 11 | seed 6, `NUM_SOURCES=3` |
| Random backpressure | max arbitration wait 11 | seed 11, `NUM_SOURCES=4` |
| Alternating ready | max latency 4, max wait 1 | all seeds/source counts |
| Long stall then release | max latency 98, backpressure wait 96 | seed 16, all source counts |
| Reset boundary | first post-reset handshake cycle 1, latency 2 | all seeds/source counts |

Normal-service fairness met the bound `service_gap <= NUM_SOURCES`: gaps were 1,
3, and 4 for `NUM_SOURCES=1,3,4`, respectively, with Jain fairness 1.0 in the
all-sources phase. Continuous traffic sustained input and output initiation
interval 1 after fill. Backpressure latency is intentionally unbounded by the
arbiter; the measured 98-cycle worst case corresponds to the longest generated
96-cycle downstream stall plus the two pipeline cycles.

All full-boundary runs observed simultaneous RX drain, TX refill, and new input
accept while retaining occupancy 2. No missing, duplicate, corruption, reorder,
occupancy, or fairness-bound failure occurred.

## Reset contract observation

`in_ready` was observed high while reset was asserted in every tested
configuration. This is the current combinational interface behavior, not a data
integrity failure: reset cleared TX/RX valid state, no reset-time event appeared
at the output, and a valid held through reset handshook on the first cycle after
release and emerged exactly once. If the external contract requires
`in_ready==0` during reset, that remains a separate interface-contract issue.

## Compile and lint observations

Existing RTL warnings were limited to unconnected completion outputs in the
core (`PINCONNECTEMPTY`) and missing timescales (`TIMESCALEMOD`, also reported by
Icarus). Testbench-only Verilator warnings were width expansion in integer
bookkeeping, an unused upper temporary-address slice, blocking assignments in
the procedural scoreboard, and mixed synchronous/asynchronous observation of
reset. No latch, combinational-loop, multi-driven, or fatal RTL warning was
reported.

## Reproduction and waveform

Run the full default matrix with installed `iverilog`, `vvp`, and `verilator`:

```sh
scripts/run_a23_stress.sh
```

Reproduce a single configuration and emit a VCD outside the repository:

```sh
A23_SIMULATORS=iverilog \
A23_NUM_SOURCES=3 \
A23_SEEDS=6 \
A23_TRACE=1 \
A23_RESULTS_ROOT=/tmp/a23-stress-repro \
scripts/run_a23_stress.sh
```

The waveform is `/tmp/a23-stress-repro/iverilog/n3/seed-6.vcd`. Replace
`iverilog` with `verilator` to reproduce with Verilator. A failure reports
`A23_STRESS_FAIL` with the seed, source count, phase, cycle, and reason before
terminating with `A23_STRESS_RESULT FAIL`.
