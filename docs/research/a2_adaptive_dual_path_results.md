# A2 Adaptive Dual-Path: local RTL and exact-trace results

Status: local screening complete, head EDA qualification pending, 2026-08-07

## Result identity and boundary

- frozen common base: `ad96895`;
- architecture document/skeleton: `f3f301a`;
- synthesizable RTL milestone: `3c33dd1`;
- candidate: `a2-adaptive-dual-path`, N=16, address width 16, one retire lane,
  reservoir depth 8;
- local RTL unit simulator: Icarus Verilog 12.0;
- frozen common TB simulator: Verilator 5.032;
- exact input: all 46 runs generated from
  `manifest.neutrality-n16.json`, with generator-reported SHA matching the
  frozen golden fixture;
- comparison reference: the actual common `aer_clean_mock_candidate` RTL,
  compiled at N=16 and run through the same frozen common TB and exact prepared
  traces;
- raw traces, generated binaries, logs, summary CSV, and event CSV stayed under
  `/tmp` and are not committed.

This is cycle-accurate RTL evidence, not a Python performance model. Verilator
compiled the canonical `tb/clean/aer_clean_assertions.sv` and unchanged
`tb/clean/aer_clean_tb.sv`. The candidate-owned replacement cell only replaces
the common TB's named compatibility slot and contains no state.

## Correctness and directed falsification

The native directed test passed under Icarus. It checked:

- isolated direct bypass with zero reservoir occupancy;
- one direct retirement plus two bank writes on the same acceptance edge;
- one queued retirement concurrent with two tail enqueues;
- same-source refire behind its queued predecessor;
- both bank pointers across repeated wraparound;
- fan-in entry to burst mode, quiet hysteresis dwell, and sparse-mode recovery;
- accepted/delivered conservation, complete drain, and post-drain quiet.

All 46 exact common traces passed with `errors=0`; every accepted event was
delivered after drain. Across the 46 independent runs there was no aggregate
accepted-minus-delivered mismatch. The common mock also passed 46/46, so the
comparison does not manufacture a correctness failure in the reference.

## Main findings

| Metric across 46 traces | A2 better | A2 worse | Tie |
| --- | ---: | ---: | ---: |
| source overrun | 15 | 0 | 31 |
| fixed-window throughput | 29 | 0 | 17 |
| p95 E2E latency | 33 | 13 | 0 |
| p99 E2E latency | 33 | 13 | 0 |
| worst request wait | 10 | 0 | 36 |

Most `+0.000488 event/cycle` throughput deltas are exactly one extra completion
inside a 2048-cycle measurement window. They are a real consequence of direct
same-edge completion, but not evidence of sustained throughput above the
single retire lane's one-event/cycle limit. The rotating-victim throughput gain
is larger and comes from accepting more events before source overrun.

### Sparse latency

Both sparse identity traces have E2E p50/p95/p99 of 1/1/1 cycles versus
2/2/2 for the registered mock. Internal sparse latency is 0 cycles versus 1.
No sparse overrun occurs. This directly supports the zero-queue bypass claim.

### Uniform saturation and the reservoir tradeoff

At offered load up to 1.0 event/cycle, A2 has zero overrun and reaches a fixed
window throughput of 1.0 at load 1.0. At 1.25, 1.5, and 2.0, throughput remains
one event/cycle and each seed has exactly eight fewer source overruns than the
mock. Those eight events are the reservoir's finite capacity, not a shift in
the sustained one-lane service ceiling.

The cost is explicit: overload p95 and p99 are generally seven cycles worse
than the mock (one 1.5-load seed is +6 p95/+8 p99). The reservoir converts eight
source drops into queued events and exposes their waiting time. Therefore A2
does not claim a universal tail-latency win under overload.

### Burst shape, fan-in, and spatial controls

- `shape_b1/b4/b16` all retain zero overrun and have p95/p99 one cycle below
  the mock. Worst wait changes 0/1/7 versus 0/3/15.
- simultaneous and periodic global fan-in have p95/p99 16 versus 17 and worst
  request wait 7 versus 15.
- local, dispersed, and mirrored spatial traces are identical: throughput
  0.75, zero overrun, and p95/p99 4 versus 5. A2 gains no hidden locality
  preference and suffers no dispersed/relabelled penalty.

### Rotating victim and fairness

| Trace | Overrun A2/mock | Throughput A2/mock | p95 A2/mock | p99 A2/mock | Min source acceptance A2/mock |
| --- | ---: | ---: | ---: | ---: | ---: |
| identity | 113/215 | 1.000/0.976318 | 12/5 | 14/7 | 0.9506/0.9283 |
| affine | 114/212 | 1.000/0.977295 | 12/5 | 13/7 | 0.9620/0.9316 |

Demand-normalized acceptance fairness improves slightly (identity
0.999900 versus 0.999800; affine 0.999950 versus 0.999845), and the affine worst
wait improves 6 versus 7 while identity ties at 10. However, the
demand-conditioned zero-service-window ratio worsens to 0.00692/0.00672 from
0.00291/0.00268. The extra admitted backlog improves capacity and minimum
service ratio but worsens tail/service-window visibility; both sides are
reported.

### Phase recovery

For both phase-transition seeds, A2 reduces total overrun by eight. Sparse and
near-saturation phase p95 are 1 versus 2; overload p95 is 22 versus 15. The
post-overload sparse phase returns to p95=1 versus mock p95=2. Both designs
reach zero backlog before the explicit drain phase, so
`recovery_to_zero_cycles=0` and `recovery_censored=false`. Both remain
`recovery_lossless=false` because the overloaded source model already recorded
overruns. A2 backlog peak is 22/21 versus 15/14, the expected finite-buffer
latency cost.

### Timing pairs

| Seed | Dropped pairs A2/mock | Mean gap error A2/mock | p95 A2/mock | p99 A2/mock |
| --- | ---: | ---: | ---: | ---: |
| 3901 | 0/2 | 0.3906/0.4603 | 1/2 | 2/3 |
| 3902 | 0/1 | 0.3906/0.4252 | 1/2 | 2/2 |

The timing-pair result is a genuine combined win: fewer source overruns, no
dropped relations, and lower distortion without an overload-tail reversal.

## PPA proxy and risk

A2 has 181 explicit state bits at N=16/A=16/D=8:

- 160 reservoir payload/source bits;
- two 3-bit pointers;
- 4-bit current and 4-bit previous occupancy;
- 4-bit rotating base;
- 2-bit quiet counter and 1 burst-mode bit.

The common mock has approximately 25 explicit bits (valid, 16-bit event,
4-bit source, 4-bit rotation state), so A2 adds 156 bits and is about 7.24x the
reference's state-bit count. A2 also has two rotating scans, a direct address
mux, occupancy/derivative comparators, and two bank write paths. Its direct
path removes a latency register but can make selection-to-retire timing the
critical combinational path. These are proxies only; no area, power, or Fmax
claim is made without head-controlled Genus/Innovus.

The strongest rejection signal is the uniform overload exchange: eight fewer
overruns cost roughly seven tail cycles and 156 extra state bits. The strongest
retention signal is rotating-victim traffic: about 100 fewer overruns, full
fixed-window throughput, improved minimum source acceptance, plus one-cycle
sparse latency. Genus area/Fmax and activity power should decide whether that
trade is worthwhile.

## Every trace: gain and loss audit

Delta signs below are `A2 - mock`; negative overrun/latency is better. The table
contains every frozen trace, including all 13 tail regressions.

| Trace | overrun Δ | throughput Δ | p95 Δ | p99 Δ | worst wait A2/ref | Classification |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| core_simultaneous_identity | 0 | 0 | -1 | -1 | 7/15 | latency gain |
| core_sparse_identity | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| core_sparse_rotate180 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| elephant_mouse_affine | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| elephant_mouse_identity | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| global_fanin_identity | 0 | 0 | -1 | -1 | 7/15 | latency gain |
| moving_hotspot_multi_column_s3301 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| moving_hotspot_multi_disperse_s3301 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| moving_hotspot_multi_row_s3301 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| moving_hotspot_single_s3301 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| moving_hotspot_single_s3302 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| phase_transition_s3501 | -8 | +0.001954 | +7 | +7 | 15/15 | capacity gain, tail loss |
| phase_transition_s3502 | -8 | +0.001954 | +7 | +7 | 15/15 | capacity gain, tail loss |
| retrigger_affine | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| retrigger_identity | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| rotating_victim_affine | -98 | +0.022705 | +7 | +6 | 6/7 | capacity gain, tail loss |
| rotating_victim_identity | -102 | +0.023682 | +7 | +7 | 10/10 | capacity gain, tail loss |
| shape_b1 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| shape_b16 | 0 | 0 | -1 | -1 | 7/15 | latency gain |
| shape_b4 | 0 | 0 | -1 | -1 | 1/3 | latency gain |
| spatial_dispersed | 0 | 0 | -1 | -1 | 1/3 | latency gain |
| spatial_local | 0 | 0 | -1 | -1 | 1/3 | latency gain |
| spatial_local_mirror | 0 | 0 | -1 | -1 | 1/3 | latency gain |
| timing_pair_s3901 | -6 | +0.002930 | -1 | -1 | 0/4 | latency/capacity gain |
| timing_pair_s3902 | -11 | +0.005860 | -1 | -2 | 0/4 | latency/capacity gain |
| uniform_l0p125_s2001 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p125_s2002 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p125_s2003 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p50_s2001 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p50_s2002 | 0 | 0 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p50_s2003 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p90_s2001 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p90_s2002 | 0 | +0.000489 | -1 | -1 | 0/0 | latency gain |
| uniform_l0p90_s2003 | 0 | +0.000489 | -1 | -1 | 0/0 | latency gain |
| uniform_l1p00_s2001 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| uniform_l1p00_s2002 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| uniform_l1p00_s2003 | 0 | +0.000488 | -1 | -1 | 0/0 | latency gain |
| uniform_l1p25_s2001 | -8 | +0.000488 | +7 | +7 | 11/11 | capacity gain, tail loss |
| uniform_l1p25_s2002 | -8 | +0.000488 | +7 | +7 | 12/12 | capacity gain, tail loss |
| uniform_l1p25_s2003 | -8 | +0.000488 | +7 | +7 | 12/12 | capacity gain, tail loss |
| uniform_l1p50_s2001 | -8 | +0.000488 | +7 | +7 | 14/14 | capacity gain, tail loss |
| uniform_l1p50_s2002 | -8 | +0.000488 | +7 | +7 | 14/14 | capacity gain, tail loss |
| uniform_l1p50_s2003 | -8 | +0.000488 | +6 | +8 | 14/14 | capacity gain, tail loss |
| uniform_l2p00_s2001 | -8 | +0.000488 | +7 | +7 | 15/15 | capacity gain, tail loss |
| uniform_l2p00_s2002 | -8 | +0.000488 | +7 | +7 | 15/15 | capacity gain, tail loss |
| uniform_l2p00_s2003 | -8 | +0.000488 | +7 | +7 | 15/15 | capacity gain, tail loss |

The audit can be regenerated from aggregate CSVs with:

```bash
scripts/analyze_a2_trace_deltas.py A2_AGGREGATE.csv MOCK_AGGREGATE.csv
```

## Head-controlled pending EDA

`PENDING_HEAD_XCELIUM`: local Verilator evidence is complete, but official
Xcelium 23.09 qualification is deliberately not run. The exact candidate-only
commands are:

```bash
AER_SIMULATOR=xrun A2_TEST_OUT=/tmp/a2-head-directed \
  tests/a2/run_directed_test.sh

AER_SIMULATOR=xrun \
  A2_TRACE_DIR=/tmp/a2-head-neutrality-traces \
  A2_SUITE_OUT=/tmp/a2-head-neutrality-results \
  scripts/run_a2_neutrality_suite.sh
```

`PENDING_HEAD_GENUS`: after the head selects A2 for synthesis, use the
candidate-only synthesis filelist/config with the already approved common
library environment:

```bash
test -f "$AER_STD_CELL_ROOT/timing/slow_vdd1v0_basicCells.lib"
AER_LIBRARY_FILE="$AER_STD_CELL_ROOT/timing/slow_vdd1v0_basicCells.lib" \
  AER_RUN_ID=a2-head-genus-n16-5ns \
  scripts/run_synth.sh a2-adaptive-dual-path \
  rtl/candidates/a2_adaptive_dual_path/head_genus_config.sh
```

`PENDING_HEAD_INNOVUS`: Innovus is necessary only if A2 survives the common
Genus screen. This branch/base contains no frozen Innovus driver, MMMC view,
floorplan, pin placement, or routing contract, so there is no safe exact
Innovus command to invent here. The head must run A2 through the same eventual
common finalist Innovus flow as every shortlisted candidate, using the mapped
netlist/SDC produced by the Genus command above. Until that shared flow exists,
post-route area, frequency, power, and energy/event remain explicitly unclaimed.
