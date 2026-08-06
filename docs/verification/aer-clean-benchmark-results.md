# Clean-Slate AER Benchmark Bring-Up

Date: 2026-08-04

## Scope

This bring-up validates the benchmark mechanism, not a new competition
architecture. Existing baseline and A23 RTL are connected through a storage-free
legacy ready/valid adapter only to confirm that:

1. conventional AER implementations still pass basic address-event correctness;
2. source overrun, tail latency, starvation-related wait, and timing distortion
   become visible under limit workloads; and
3. the testbench does not hide source-side loss in an unbounded driver queue.

The normative intent and PPA boundary are defined in
[`aer-clean-benchmark-spec.md`](aer-clean-benchmark-spec.md).

## Implemented foundation

- source occurrence is generated independently of DUT `ready` in limit tests;
- each source has one pending-event latch;
- a refire while occupied is counted as `source_overrun`, not queued for free;
- counters separate `generated`, `accepted`, and `delivered`;
- end-to-end latency starts at occurrence, while internal latency starts at DUT
  acceptance;
- the normalized output has parameterized retire lanes, so a future packed or
  multi-lane candidate is not capped at one logical event/cycle;
- TB-only event sequence is not transmitted as DUT payload;
- the legacy adapter adds no buffering.

## Regression status

| Simulator / candidate | Tests | Result |
| --- | ---: | --- |
| Verilator 5.032 / clean stall-safe smoke | 12 | 12/12 PASS |
| Verilator 5.032 / legacy fixed-priority baseline | 8 | 8/8 PASS |
| Verilator 5.032 / legacy A23 | 8 | 8/8 PASS |
| Xcelium 23.09 / legacy fixed-priority baseline | 8 | 8/8 PASS |
| Xcelium 23.09 / legacy A23 | 8 | 8/8 PASS |

The independently developed benchmark utilities were merged after review:

- the deterministic trace generator covers 10 workload families and passes its
  byte-for-byte reproducibility self-test;
- the architecture-neutral aggregator passes 5/5 unit tests and keeps
  `CORRECTNESS_FAIL` distinct from a valid but `SATURATED` result.

The repository's old combinational `aer_mock_dut` was found to change its selected
event when a new request arrived while the output was stalled. It was not changed.
A separate stall-safe smoke candidate was added under `tb/clean/` so benchmark
self-validation does not inherit that known protocol violation.

## Limit exposure example

The following provisional N=4, seed=1, 128-cycle results use a 25% Bernoulli
offer probability per source where applicable. All accepted events were delivered
exactly once with zero scoreboard errors.

| Workload / legacy candidate | Generated | Source overrun | Accepted/delivered | Throughput | Max E2E | Max request wait |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `limit_load` / baseline | 126 | 61 | 65/65 | 0.488722 | 65 | 62 |
| `limit_load` / A23 | 126 | 19 | 107/107 | 0.816794 | 6 | 3 |
| `limit_elephant_mouse` / baseline | 136 | 70 | 66/66 | 0.492537 | 133 | 130 |
| `limit_elephant_mouse` / A23 | 136 | 8 | 128/128 | 0.977099 | 4 | 1 |
| `limit_retrigger` / baseline | 128 | 63 | 65/65 | 0.492424 | 4 | 1 |
| `limit_retrigger` / A23 | 128 | 0 | 128/128 | 0.977099 | 3 | 0 |
| `limit_backpressure_shock` / baseline | 126 | 77 | 49/49 | 0.371212 | 62 | 59 |
| `limit_backpressure_shock` / A23 | 126 | 50 | 76/76 | 0.580153 | 38 | 35 |

These numbers are benchmark-calibration evidence, not official competition scores
and not a decision to use A23 as the new architecture. Their purpose is to show
that correctness and limit behavior are now reported separately.

## Remaining benchmark work

1. connect the completed deterministic trace files and manifests to the SV source
   model;
2. extend the completed result aggregator with p50/p95/p99 latency, deadline miss,
   and sliding-window service-gap inputs;
3. run multi-seed offered-load sweeps and detect the saturation knee;
4. add 16/64/256-source scaling runs;
5. define a fixed physical pin budget and charge required serializers/decoders to
   the candidate PPA boundary;
6. freeze the benchmark before starting clean-slate candidate RTL.
