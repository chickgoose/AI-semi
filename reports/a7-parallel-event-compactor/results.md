# A7 parallel event compactor: N=16 logical results

Date: 2026-08-07  
Simulator: Verilator 5.032  
Frozen input: `benchmarks/clean_slate_aer/manifest.neutrality-n16.json`  
Runs: 46 traces each for K=1/2/4, 138 total

## Correctness and headline result

All 138 runs completed with zero scoreboard errors and complete post-drain
conservation.  Each K saw 87,000 generated events.  K=1 accepted and delivered
73,878 with 13,122 source overruns; K=2 and K=4 accepted and delivered all
87,000 with no overrun.

The fixed-window three-seed uniform sweep is:

| offered event/cycle | K=1 throughput / util. | K=2 throughput / util. | K=4 throughput / util. | K=1 overrun | K=2/K=4 overrun |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.125 | 0.1209 / 12.1% | 0.1209 / 6.0% | 0.1209 / 3.0% | 0 | 0 / 0 |
| 0.50 | 0.4966 / 49.7% | 0.4966 / 24.8% | 0.4966 / 12.4% | 0 | 0 / 0 |
| 0.90 | 0.9038 / 90.4% | 0.9038 / 45.2% | 0.9038 / 22.6% | 0 | 0 / 0 |
| 1.00 | 0.9995 / 100.0% | 0.9995 / 50.0% | 0.9995 / 25.0% | 0 | 0 / 0 |
| 1.25 | 0.9995 / 100.0% | 1.2461 / 62.3% | 1.2461 / 31.2% | 1,508 | 0 / 0 |
| 1.50 | 0.9995 / 100.0% | 1.4932 / 74.7% | 1.4932 / 37.3% | 3,017 | 0 / 0 |
| 2.00 | 0.9995 / 100.0% | 1.9990 / 100.0% | 1.9990 / 50.0% | 6,120 | 0 / 0 |

Lane utilization is fixed-window throughput divided by K.  K=2 reaches 2.00x
the fair K=1 reference at the offered ceiling and clears the predeclared 1.5x
break-even.  K=4 cannot show throughput above K=2 because this frozen suite
offers at most 2.0 event/cycle; its incremental evidence is burst drain and
tail latency, not a fabricated higher throughput claim.

## Required bottleneck cuts

| workload | metric | K=1 | K=2 | K=4 |
| --- | --- | ---: | ---: | ---: |
| simultaneous 16-way | p99 / max wait | 17 / 15 | 9 / 7 | 5 / 3 |
| global fan-in 16-way | p99 / max wait | 17 / 15 | 9 / 7 | 5 / 3 |
| rate shape B1 | p99 / overrun | 2 / 0 | 2 / 0 | 2 / 0 |
| rate shape B4 | p99 / max wait | 5 / 3 | 3 / 1 | 2 / 0 |
| rate shape B16 | p99 / max wait | 17 / 15 | 9 / 7 | 5 / 3 |
| spatial local | p99 / max wait | 5 / 3 | 3 / 1 | 2 / 0 |
| spatial dispersed | p99 / max wait | 5 / 3 | 3 / 1 | 2 / 0 |
| spatial local mirror | p99 / max wait | 5 / 3 | 3 / 1 | 2 / 0 |
| phase transition, 2 seeds | throughput / overrun | 0.5216 / 2,033 | 0.7698 / 0 | 0.7698 / 0 |
| phase transition, 2 seeds | p95 / p99 | 14 / 16 | 2 / 2 | 2 / 2 |
| rotating victim identity | throughput / overrun | 0.9763 / 215 | 1.0293 / 0 | 1.0293 / 0 |
| rotating victim affine | throughput / overrun | 0.9773 / 212 | 1.0293 / 0 | 1.0293 / 0 |

The matched spatial triplet is identical at each K, as expected from a
rotation-neutral non-spatial primitive.  Rotating-victim demand-normalized
delivery fairness is 0.99980/0.99984 for K=1 identity/affine and 1.0 for K=2/4;
minimum source delivery ratios are 0.928/0.932 for K=1 and 1.0 for K=2/4.
There is no address-remapping anomaly.

## Sparse overhead and structural proxy

All K variants have p95=p99=2 cycles and no overrun at uniform 0.125 through
1.0, so widening does not alter sparse functional latency or throughput.  The
cost is visibly low utilization plus additional mux/buffer logic.  For N=16,
W=5 and L=4, the predeclared proxy is:

| K | gate proxy | depth proxy | state bits (base + inflight + lane buffers) |
| ---: | ---: | ---: | ---: |
| 1 | 517 | 9 | 41 |
| 2 | 613 | 9 | 62 |
| 4 | 805 | 9 | 104 |

Thus K=2 adds 18.6% proxy gates over the shared-scan K=1 reference for up to
2x measured service. K=4 adds 55.7% and halves B16/global-fanin tail latency
again, but the current offered-rate ceiling cannot establish a throughput
break-even versus K=2. These are topology proxies, not standard-cell PPA.
Server PPA was not run.

## Unit qualification

K=1/2/4 each passed all 65,536 N=16 request bitmaps for every inclusive prefix
and total count. Directed persistent contention checked grant uniqueness,
at-most-K selection, `ceil(N/K)` round-robin service behavior and same-edge
retire/refill. An independent lane-0 stall checked stable registered output and
that its inflight source never appeared in another lane.

## Reproduction and boundary checks

```bash
PATH=/path/with/verilator:$PATH AER_SIMULATOR=verilator \
  tests/a7_parallel_event_compactor/run_unit.sh

PATH=/path/with/verilator:$PATH AER_SIMULATOR=verilator \
  AER_CLEAN_OUT=/tmp/a7-46-results \
  AER_A7_TRACE_DIR=/tmp/a7-neutrality-n16-traces \
  scripts/run_a7_46_traces.sh

git diff ad96895 -- scripts/run_clean_benchmark.sh tb/clean/aer_clean_tb.sv
```

The last command produced no output. The common runner and common TB are
byte-identical to branch base `ad96895`. A7 uses a candidate-owned replacement
cell in its dedicated file list and does not add storage or behavior to the
frozen common TB. Aggregate SHA256:

```text
e3e90249e752bf4b11a7dbb4bb45be9d4816efd3a0917f0871a87f20f4b02147  aggregate.csv
204cc2f4a6ad338c5e3faa7a270cf951b6fb8e81f34a73383e82de0da74cf402  event-runs.csv
```

`aggregate.csv` is the complete grouped metric table. `event-runs.csv` holds
the exact per-candidate/test/seed p50/p95/p99, fairness, service-gap, deadline,
and censoring summaries; `aggregate.json` preserves the full aggregate schema.
