# A8 Age Calendar-Wheel Functional Results

Status: phase-2 quantization/scaling screening complete, 2026-08-07

## Configuration and gates

The candidate is `a8-age-calendar-wheel`, N=16, ADDR_WIDTH=16, one native
always-ready retire lane. The default wheel uses four cycles per bucket and
eight modulo epochs (32-cycle horizon). It captures only the first observed
epoch of a held request; no deadline, occurrence timestamp, TB relation, or
event ID enters the DUT.

The exact input was the unmodified
`benchmarks/clean_slate_aer/manifest.neutrality-n16.json`. All 46 generated
trace SHA256 values matched the frozen golden fixture. Common workload, TB,
trace, and golden files were not edited.

| Gate | Result |
| --- | --- |
| wheel RTL unit | PASS; simultaneous max wait 3 at N=4 |
| wrap/quantization counterexample model | PASS |
| frozen neutrality generator | PASS; 46 runs, deterministic |
| generator self-test | PASS; 15 workloads |
| benchmark Python unit tests | PASS; 33 tests |
| capability profile | core RUN; backpressure/polarity/multi-lane SKIP |
| default B4 frozen regression | 46/46 PASS, zero correctness errors |
| B1 quantization control regression | 46/46 PASS, zero correctness errors |
| RR mock calibration | 46/46 PASS, zero correctness errors |
| portability gate | PASS; no `python3 -c` under `scripts/` or `tests/` |
| server PPA | NOT RUN; approval was not requested or given |

The RR result is the common testbench smoke candidate, not A2 and not a
competition candidate. It is used only to tell whether age ordering changes the
same frozen traffic.

## Default B4 workload results

`zero-window` is the common aggregator's demand-conditioned zero-service source
window ratio. `DN fairness` is demand-normalized delivery Jain fairness. E2E
tail is occurrence-to-delivery. Repeated seeds are aggregated by the common
tool; ranges with separately named placement/mapping runs are reported at their
worst observed value.

| Workload/family | event/cycle | overrun ratio | max wait | E2E p95/p99 | zero-window | DN fairness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| sparse identity | 0.031250 | 0 | 0 | 2 / 2 | 0.028084 | 1.000000 |
| elephant/mouse, worst mapping | 0.891602 | 0 | 0 | 2 / 2 | 0.017571 | 1.000000 |
| moving hotspot, worst run | 0.888672 | 0 | 0 | 2 / 2 | 0.017652 | 1.000000 |
| rotating victim, worst mapping | 0.977295 | 0.050036 | 7 | 5 / 6 | 0.002709 | 0.999811 |
| phase transition, 2 seeds | 0.521240 | 0.322867 | 14 | 13 / 14 | 0.006279 | 0.998768 |
| retrigger, either mapping | 0.250000 | 0 | 0 | 2 / 2 | 0.025276 | 1.000000 |
| timing pair, 2 seeds | 0.615235 | 0.006698 | 4 | 3 / 4 | 0.006745 | 0.999911 |
| global fan-in | 0.250000 | 0 | 15 | 17 / 17 | 0 | 1.000000 |

The nonzero sparse/retrigger zero-window ratios do not mean a live request
waited: measured max request wait is zero. They are windowed service statistics
over intermittent per-source demand. The raw default B4 counts include 325 / 18,496
zero-demand-service windows for elephant/mouse, 171 / 63,496 for rotating-victim
identity, 528 / 84,091 for phase transition, and 402 / 59,598 for timing pair.

Elephant/mouse, moving-hotspot, and retrigger do not create simultaneous live
contention in these particular frozen runs at the one-entry source seam, so A8
is identical to RR there. This is not evidence of an age-policy gain.

## Uniform saturation

| Offered event/cycle | completed event/cycle | overrun ratio | max wait | E2E p95/p99 | zero-window | DN fairness |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.125 | 0.120931 | 0 | 0 | 2 / 2 | 0.023196 | 1.000000 |
| 0.50 | 0.496582 | 0 | 0 | 2 / 2 | 0.009102 | 1.000000 |
| 0.90 | 0.903808 | 0 | 0 | 2 / 2 | 0.002909 | 1.000000 |
| 1.00 | 0.999512 | 0 | 0 | 2 / 2 | 0.001704 | 1.000000 |
| 1.25 | 0.999512 | 0.196867 | 10 | 9 / 10 | 0.001978 | 0.999022 |
| 1.50 | 0.999512 | 0.328939 | 14 | 11 / 12 | 0.001168 | 0.998002 |
| 2.00 | 0.999512 | 0.497884 | 15 | 13 / 15 | 0.000437 | 0.998036 |

The wheel cannot raise the one-lane capacity above one event/cycle. Above load
1.0 it changes which pending source is served, but approximately 19.7%, 32.9%,
and 49.8% of generated events overrun the common one-entry source latches. That
loss is reported rather than hidden in latency samples.

The rate-shape control exposes serialization, not an age win: equal-mean
1/4/16-way bursts produce E2E p99 of 2/5/17 cycles and max waits of 0/3/15 for
both B4 and B1. Simultaneous arrivals have the same timestamp in either wheel.

## Timing pair and phase behavior

The exact TB-only A/B analyzer reports pair-gap error, not a DUT-visible
deadline. B4 produced pair-gap error p95/p99 of 2/3 cycles for seed 3901 and
2/2 cycles for seed 3902. It had 17 TB deadline misses among 2,538 events and no
censored accepted event; the misses include source-side overrun effects. The
DUT did not receive or compare those deadlines.

For phase-transition seeds 3501/3502, B4 overload-phase p95 E2E latency was 13
cycles, post-overload sparse p95 returned to 2 cycles, and the zero-injection
phase began with no retained backlog (`recovery_to_zero_cycles=0`). The runs
were not lossless: overload produced 1,017 and 1,018 source overruns, and seed
3502 had one additional post-sparse overrun.

## RR comparison and hypothesis result

| Metric | RR mock | B4 wheel | Direction |
| --- | ---: | ---: | --- |
| rotating-victim identity max wait | 10 | 7 | better |
| rotating-victim identity E2E p99 | 7 | 6 | better |
| rotating-victim identity overrun | 0.050984 | 0.050036 | slightly better |
| phase-transition max wait | 15 | 14 | better |
| phase-transition E2E p99 | 16 | 14 | better |
| phase-transition event/cycle | 0.521606 | 0.521240 | slightly worse |
| phase-transition overrun | 0.322391 | 0.322867 | slightly worse |
| timing-pair E2E p95/p99 | 3 / 4 | 3 / 4 | unchanged |
| worst timing-pair gap p95/p99 | 2 / 3 | 2 / 3 | unchanged |
| sparse E2E p95/p99 | 2 / 2 | 2 / 2 | unchanged |

The functional hypothesis is only partially supported. Age classes reduce the
rotating-victim and phase-transition latency tail without sparse regression,
but do not improve the frozen timing-pair gap. The phase run also trades a small
throughput/overrun loss for its tail improvement. No PPA result exists, so this
screening does not establish an area, power, Fmax, or efficiency win.

## Bucket quantization loss

The B1 control uses 1-cycle buckets and 32 epochs, preserving the same 32-cycle
horizon. It therefore removes same-bucket ambiguity between requests arriving
on different cycles, at the cost of a wider tag and a 32-way rather than 8-way
oldest-bucket search.

| Metric | B4 default | B1 control | B4 quantization cost |
| --- | ---: | ---: | ---: |
| sparse E2E p99 | 2 | 2 | 0 |
| rotating-victim worst max wait | 7 | 4 | +3 cycles |
| rotating-victim worst E2E p99 | 6 | 5 | +1 cycle |
| phase-transition max wait | 14 | 12 | +2 cycles |
| phase-transition E2E p99 | 14 | 13 | +1 cycle |
| worst timing-pair gap p95 | 2 | 1 | +1 cycle |
| worst timing-pair gap p99 | 3 | 2 | +1 cycle |
| uniform 1.25 E2E p99 | 10 | 8 | +2 cycles |
| uniform 2.00 E2E p99 | 15 | 13 | +2 cycles |

B1 is not uniformly better: timing-pair overrun/deadline misses increased from
17 to 19, and its aggregate completed rate fell from 0.615235 to 0.614747
event/cycle. Different exact-age choices can alter acceptance order and hence
which future source occurrence overruns.

RTL state accounting is 94 bits for B4 versus 127 bits for B1 at N=16 and
ADDR_WIDTH=16. The extra 33 bits are chiefly two additional tag bits per source;
the combinational oldest-bucket search also grows from 8 to 32 entries. These
are structural counts and path expectations, not synthesis/PPA measurements.

## Phase-2 official 46-trace Pareto (N=16)

All B1/B2/B4/B8 wheels, the exact per-source counter reference, and RR passed
46/46 with zero correctness issues. The table uses uniform load 1.25 and the
identity rotating-victim row; pair p95/p99 is the worse of seeds 3901/3902.
`state` includes tracked/tag-or-age/tie/global state and the same
valid/event/source output register.

| Arch | state (bit) | uniform maxwait / p99 | uniform overrun / zero-window / DN fairness | rotating maxwait / p99 | pair p95 / p99 | phase maxwait |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| RR | 25 | 12 / 11 | 0.196867 / 0.001768 / 0.998862 | 10 / 7 | 2 / 3 | 15 |
| exact | 121 | 9 / 8 | 0.196997 / 0.002209 / 0.998669 | 4 / 5 | 2 / 2 | 12 |
| B1 | 127 | 9 / 8 | 0.196997 / 0.002177 / 0.998748 | 4 / 5 | 1 / 2 | 12 |
| B2 | 110 | 9 / 9 | 0.196867 / 0.002020 / 0.998648 | 4 / 5 | 2 / 2 | 13 |
| B4 | 94 | 10 / 10 | 0.196867 / 0.001978 / 0.999022 | 7 / 6 | 2 / 3 | 14 |
| B8 | 78 | 16 / 12 | 0.196736 / 0.002313 / 0.999031 | 8 / 7 | 2 / 3 | 18 |

Sparse p99 stayed 2 cycles for every architecture. Elephant/mouse, retrigger,
and moving-hotspot max wait stayed zero because those frozen instances did not
produce live same-cycle contention at this seam. Throughput at uniform 1.25
remained the single lane's 0.999512 event/cycle; the approximately 0.197
overrun is not a scheduler throughput win and is kept explicit.

## N=16/32/64 scaling matrix

The candidate-owned scaling manifests add sparse, simultaneous, two uniform
seeds at 0.9 and 1.25, rotating victim, timing pair, elephant/mouse, moving
hotspot, and retrigger at each N. Six architectures by three N values by eleven
runs produced 198/198 PASS. The compact table reports structural state,
uniform-1.25 max wait/p99, rotating max wait/p99, and timing-pair gap p99.

| N | Arch | state | uniform wait/p99 | rotating wait/p99 | pair p99 |
| ---: | --- | ---: | ---: | ---: | ---: |
| 16 | RR / exact / B1 / B2 / B4 / B8 | 25 / 121 / 127 / 110 / 94 / 78 | 13/12 · 8/8 · 8/8 · 9/9 · 11/10 · 13/12 | 8/7 · 4/6 · 4/6 · 6/6 · 7/7 · 9/7 | 2 / 2 / 2 / 2 / 2 / 2 |
| 32 | RR / exact / B1 / B2 / B4 / B8 | 27 / 251 / 258 / 225 / 193 / 161 | 21/19 · 14/13 · 14/13 · 13/14 · 15/14 · 20/17 | 12/9 · 6/7 · 6/7 · 7/7 · 9/8 · 10/9 | 4 / 2 / 2 / 2 / 4 / 4 |
| 64 | RR / exact / B1 / B2 / B4 / B8 | 29 / 541 / 549 / 484 / 420 / 356 | 36/32 · 20/21 · 20/21 · 21/21 · 22/21 · 26/25 | 13/11 · 8/9 · 8/9 · 9/10 · 9/10 · 13/11 | 3 / 2 / 2 / 2 / 2 / 3 |

Uniform overrun stayed in 0.19798--0.20167 across the matrix. At N=64 its
zero-service ratio was 0.08682 for RR and 0.09239--0.09252 for the wheels/exact;
DN fairness was 0.99494--0.99551. Rotating-victim zero-service at N=64 was
0.03763--0.04260 and DN fairness 0.99946--0.99955. These small differences do
not justify hiding the clear B8 wait-tail loss. Simultaneous max wait was always
N-1 and sparse p99 always 2, as expected.

## Sequential toggle and local Yosys proxies

The Verilator activity proxy drives the six designs together for 4,096 cycles
at 1.25 offered occurrences/cycle. It counts Hamming changes in scheduler
sequential state plus output valid/source; the 16-bit event is held at zero, so
its storage is counted above but contributes no artificial payload toggles.
This is not SAIF power and excludes combinational glitches.

| N | exact toggles/cycle | B1 | B2 | B4 | B8 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 16.18 | 42.67 | 27.88 | 13.35 | 11.64 |
| 32 | 29.66 | 90.49 | 60.50 | 30.43 | 14.04 |
| 64 | 59.58 | 199.87 | 139.57 | 78.31 | 15.55 |

B8 validates the intended O(1)-global-aging activity trend. B1 does not:
frequent full-width epoch movement and source retagging cost more transitions
than incrementing the exact tracked counters. B4 crosses above exact by N=64.

Local Yosys 0.52 was run from `/tmp`, without a server or technology library.
The generic `stat` cells and FF-excluding `ltp` length are structural proxies;
the procedural scans synthesize as unbalanced priority chains.

| N | exact cells/depth | B1 | B2 | B4 | B8 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 906 / 163 | 1229 / 261 | 1088 / 213 | 1017 / 189 | 981 / 177 |
| 32 | 2330 / 323 | 2973 / 517 | 2688 / 421 | 2545 / 373 | 2473 / 349 |
| 64 | 6714 / 643 | 7997 / 1029 | 7424 / 837 | 7137 / 741 | 6993 / 693 |

State savings therefore do not become an area or select-depth proxy win in the
current RTL. No server PPA was run.

## Adversarial bounds and shortlist decision

The strengthened test crossed the modulo boundary, held valid before ready,
created a same-bucket later-arrival win, stalled service continuously for eight
cycles, and then drained all sources. B1 matched exact cycle-by-cycle in the
wrap sequence; observed max wait was 8, within the configured proof bound 11.
An equality horizon (`HORIZON == N-1+MAX_STALL_CYCLES`) is rejected at
elaboration, preventing an old/new modulo alias.

The current candidate is **not shortlisted for advancement or server PPA**.
B8 remains the only compelling state/activity research point (N=64: 356 bits
and 15.55 toggles/cycle versus exact 541 and 59.58), but its official N=16
phase max wait is 18 versus exact 12 and its uniform max wait is 16 versus 9.
It also has worse local cells/depth. B4's N=16 balance does not scale in toggle,
and B1/B2 do not beat exact activity or depth. This is a transparent negative
result rather than an efficiency claim based only on state bits.

## Reproduction

```bash
tests/a8_age_calendar_wheel/run_unit_tests.sh
python3 benchmarks/clean_slate_aer/neutrality_self_test.py
python3 benchmarks/clean_slate_aer/self_test.py
python3 -m unittest discover -s benchmarks/clean_slate_aer/tests -v

A8_CLEAN_OUT=/tmp/a8-age-calendar-wheel-regression \
  scripts/run_a8_age_calendar_wheel.sh \
  --manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json

A8_BUCKET_CYCLES=1 A8_EPOCH_COUNT=32 \
  A8_CANDIDATE_NAME=a8-age-calendar-wheel-b1 \
  A8_CLEAN_OUT=/tmp/a8-age-calendar-wheel-b1-regression \
  scripts/run_a8_age_calendar_wheel.sh \
  --manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json

scripts/run_a8_quantization_matrix.sh
scripts/summarize_a8_quantization_matrix.sh
scripts/run_a8_toggle_proxy.sh
python3 scripts/run_a8_yosys_proxy.py --output-dir /tmp/a8-yosys-proxy
scripts/summarize_a8_official_pareto.sh
```

The runner invokes only file-based Python helpers. It does not use inline
`python3 -c`, so the command arguments are identical under the local Python and
the server's Python wrapper. Generated traces, simulator builds, logs, and CSVs
remain under `/tmp`; only this summary and candidate-owned source are committed.
The 198-run matrix keeps aggregate/event-aggregate/timing summaries, generated
trace manifests, and a SHA256 manifest. After checksum validation, exactly 198
regenerable raw `*.events.csv` files were gzip-compressed; active RTL, trace,
Yosys, and Verilator inputs were not touched.
