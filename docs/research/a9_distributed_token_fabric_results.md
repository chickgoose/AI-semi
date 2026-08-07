# A9 N=16 distributed empty-slot results

Date: 2026-08-07.  Status: local simulation evidence; no server PPA was run.

## Method and outcome

The frozen neutrality N=16 manifest was run without modifying the common TB,
trace generator, traces, scoreboard, or golden metadata.  Verilator 5.032 ran
all 46 traces for four configurations: distributed A9 and a measurement-only
flat per-stripe round-robin reference, each at `RETIRE_LANES=4` and
`RETIRE_LANES=1`.  The reference has the same fixed source-to-stripe mapping,
one source ingress entry, and identical retire width.  It is compiled only by
`A9_CENTRALIZED_REFERENCE`; it is not a fallback or mode inside A9.

| Configuration | Pass | Generated | Source overrun | Accepted | Delivered | Correctness errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A9 distributed L4 | 46/46 | 87,000 | 107 | 86,893 | 86,893 | 0 |
| centralized reference L4 | 46/46 | 87,000 | 54 | 86,946 | 86,946 | 0 |
| A9 distributed L1 | 46/46 | 87,000 | 12,847 | 74,153 | 74,153 | 0 |
| centralized reference L1 | 46/46 | 87,000 | 12,796 | 74,204 | 74,204 | 0 |

Every accepted event drained exactly once with the required payload and
per-source order.  Cell and fabric assertions found no overflow, underflow,
double-producer admission, or post-reset phantom.  Directed cell testing
delivered 25/25 contested events; the full fabric directed test delivered
48/48 after fan-in, stall, reset, and hotspot cases.

The performance result is negative against the strongest required comparison:
same-L4 centralized arbitration is slightly better in aggregate throughput and
overrun and materially better in latency.  A9 therefore cannot claim a
functional performance win or a four-lane win.  Its remaining hypothesis is
that constant local two-producer control and nearest-neighbor wiring can obtain
better physical scaling than the centralized request/grant/mux network.  That
hypothesis remains unproven until an approved, pin-matched server PPA run.

## Throughput, fairness, and tails

Percentiles below are exact nearest-rank occurrence-to-delivery latency over
all delivered events in the named trace or three-seed uniform group.

| Workload | Configuration | event/cycle | event/cycle/lane | overrun | p95 | p99 | Jain fairness |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| sparse identity | A9 L4 | 0.031250 | 0.007813 | 0 | 6 | 6 | 1.000000 |
| sparse identity | central L4 | 0.031250 | 0.007813 | 0 | 3 | 3 | 1.000000 |
| global fan-in | A9 L4 | 0.250000 | 0.062500 | 0 | 6 | 6 | 1.000000 |
| global fan-in | central L4 | 0.250000 | 0.062500 | 0 | 6 | 6 | 1.000000 |
| uniform 2.00, 3 seeds | A9 L4 | 1.992513 | 0.498128 | 23 | 7 | 8 | 0.994919 minimum |
| uniform 2.00, 3 seeds | central L4 | 1.993001 | 0.498250 | 30 | 4 | 5 | 0.995026 minimum |
| uniform 2.00, 3 seeds | A9 L1 | 0.996419 | 0.996419 | 6,032 | 142 | 861 | 0.614217 minimum |
| uniform 2.00, 3 seeds | central L1 | 0.999023 | 0.999023 | 6,077 | 32 | 33 | 0.999538 minimum |
| rotating victim identity | A9 L4 | 1.028320 | 0.257080 | 1 | 6 | 7 | 0.997814 |
| rotating victim affine | A9 L4 | 1.027832 | 0.256958 | 2 | 6 | 7 | 0.997811 |
| phase transition s3501 | A9 L4 | 0.765137 | 0.191284 | 5 | 7 | 8 | 0.996416 |

At the normalized retire boundary, address 16 + source 4 + valid/ready gives
22 pins/lane.  Uniform-2.00 event/pin-cycle is therefore 0.022642 for A9 L4
and 0.022648 for central L4.  At L1 it is 0.045292 for A9 and 0.045410 for the
central reference.  Lane and pin normalization removes the apparent four-lane
gain: there is no throughput-efficiency advantage in this N=16 simulation.

The rate sweep also exposes the L1 saturation transition.  A9 L4 reaches mean
0.998372 event/cycle at offered 1.00 with no overruns, 1.243815 at 1.25 with
five overruns, 1.491374 at 1.50 with two overruns, and 1.992513 at 2.00 with 23
overruns.  A9 L1 plateaus near one event/cycle; at offered 1.25 its p99 is 294
cycles and at offered 2.00 its p99 is 861.  The long A9 L1 tail is queueing in
the 16-cell distributed path, not evidence of more service bandwidth.

For phase-transition s3501, A9 L4 reports sparse/near-saturation/overload p95
latencies of 6/6/7 cycles.  Overload completion is 1.992188 event/cycle,
backlog peaks at 13, five occurrences overrun at their source, and the
post-sparse phase returns to zero backlog without a censored recovery.

## Token/empty-slot utilization

The simulation-only counter sums occupied entries in the two transport slots
per cell.  It adds no synthesis state.  Empty-slot return bound is the stripe
depth, four cycles at N=16 L4.

| Workload | occupied transport-slot fraction | retire lane-service fraction |
| --- | ---: | ---: |
| sparse identity | 0.002395 | 0.007663 |
| global fan-in | 0.019436 | 0.062196 |
| spatial local | 0.058309 | 0.186589 |
| spatial dispersed | 0.069971 | 0.186589 |
| moving hotspot multi-row | 0.073392 | 0.219903 |
| uniform 2.00 s2001 | 0.164267 | 0.495272 |
| phase transition s3501 | 0.063002 | 0.190818 |

The equal local/dispersed lane-service fraction but different occupancy is the
expected topology cost: dispersed sources traverse more occupied transport
segments.  No token manager is present; conservation follows from FIFO moves,
and accepted minus retired equals ingress plus transport occupancy.

## Fixed-stripe imbalance disclosure

No global remap, crossbar, work stealing, or K-lane compactor was added.  For
L4, source `s` remains on stripe `floor(s/4)`.  The committed
`analyze_a9_stripes.py` reconstructs the following offered/dropped vectors
directly from common per-event CSV files.

| Trace | offered per stripe | A9 overrun per stripe | central L4 overrun | Observation |
| --- | --- | --- | --- | --- |
| elephant/mouse identity | 1532/103/94/98 | 0/0/0/0 | 0 total | 16.30x nonzero max/min imbalance |
| elephant/mouse affine | 1537/92/102/96 | 27/0/0/0 | 0 total | 16.71x; affine placement exposes source position sensitivity |
| moving multi-disperse | 453/457/472/439 | 0/0/1/0 | 0 total | one distributed overrun |
| moving multi-row | 462/451/470/438 | 1/3/4/1 | 0 total | nine overruns despite broadly balanced stripe totals |
| moving multi-column | 453/457/472/439 | 0/0/0/1 | 0 total | one overrun |
| rotating victim identity | 1056/1046/1056/1059 | 0/0/0/1 | 1 total | A9 fairness 0.997814 |
| rotating victim affine | 1062/1054/1047/1054 | 2/0/0/0 | 0 total | relabeling moves the loss to stripe 0 |

The elephant/mouse affine loss and row/dispersed difference are not explained
by output width.  They show that fixed stripe assignment plus position-local
merges can convert spatial correlation into ingress overrun.  Under the stated
rejection criteria this is a redesign warning, not a reason to hide the result
with a global remapper.

## Analytic N scaling and PPA proxies

For square scaling use `L=D=sqrt(N)`, address width `A=16`, and source width
`S=ceil(log2(N))`.  Exact candidate state counted from RTL is
`B_A9=N*(4+3A+2S)`: local valid/event, two transport payloads, two-bit count,
and one toggle.  The reference count includes N source ingress entries plus,
per lane, output valid/event/source and a `ceil(log2(D))` RR pointer.

| N / L / D | A9 state bits | central state bits | A9 local channel bits | central mux-input bits | retire pins | decision depth |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 16 / 4 / 4 | 960 | 364 | 264 | 320 + 32 req/grant | 88 | A9 local 2:1; central scan 4 |
| 64 / 8 / 8 | 4,096 | 1,296 | 1,344 | 1,408 + 128 req/grant | 192 | A9 local 2:1; central scan 8 |
| 256 / 16 / 16 | 17,408 | 4,816 | 6,240 | 6,144 + 512 req/grant | 416 | A9 local 2:1; central scan 16 |

Both same-L designs have peak `L` event/cycle, so this table does not claim a
throughput advantage.  A9 pays 2.64x/3.16x/3.61x the reference state at the
three points.  Its proposed benefit is physical: each internal channel spans
one cell and control fan-in remains two, whereas the reference's request/grant
and data selection span a stripe and its flat scan depth grows with D.  Logical
bit counts do not prove wirelength, timing, area, or power; approved PPA is
required before accepting that trade.

## Reproduction

Use `scripts/run_a9_benchmark.sh` with
`AER_A9_IMPLEMENTATION=distributed|centralized` and
`AER_RETIRE_LANES=4|1`.  The four runs must use separate `AER_A9_TRACE_DIR`
when launched concurrently because the frozen generator atomically replaces
temporary files.  Then run, for example:

```text
python3 scripts/analyze_a9_stripes.py RESULTS_DIR --lanes 4 -o stripes.csv
```

The raw simulation directories stay uncommitted under the project result-data
policy.  The reported counters and formulas are reproducible from committed
RTL, runner, manifest, and analysis script.  No synthesis, STA, or power tool
was invoked.
