# A2 phase-2 parameter Pareto and decision

Status: local RTL/model evidence complete, 2026-08-07

Phase-1 46-trace evidence remains fixed at commit `1006b39`; none of its CSV,
trace, TB, manifest, fixture, or conclusions was regenerated or edited. Phase 2
only asks how the same adaptive dual-path mechanism scales with N, reservoir
banks/depth, and hysteresis. Raw outputs are intentionally outside the repo in
`/tmp/a2-phase2-rtl-postlint` and `/tmp/a2-phase2-model-final`.

## Decision

Shortlist **B=4, depth=16, enter=4, exit=0, dwell=1** as the phase-2 family for
N=16/32/64. It is the only family that both clears every predeclared model gate
and retains at least 90% of the best observed hotspot-plus-recurrence overrun
reduction at every N. The selected control uses level hysteresis (`exit=0`) as
the dominant anti-thrashing mechanism; a longer dwell is not free and was not
needed once exit was zero.

This is a local architectural shortlist, not a PPA win claim. The selected
point pays substantial state and overload-tail costs. Head-controlled Xcelium
and synthesis/place-and-route remain pending and were not run.

## Accepted protocol and preregistration amendment

The gates and ranking order in
`docs/research/a2_phase2_pareto_preregistration.md` were committed first as
`9af58a7`. Before accepting any sweep output, two trace-definition conflicts
were corrected without changing those gates:

1. A hotspot defined as `source % B == 0` changes the offered trace when B
   changes, contradicting the same-trace comparison rule. The accepted trace
   always uses a modulo-4 source class. It adversarially aliases both B=2 and
   B=4 fixed hashes while every structural point sees identical occurrences.
2. Alternating `banks+1` arrivals with isolated traffic is bank-dependent and
   has an offered average above the one-event/cycle retirement limit. It became
   permanent overload rather than a mode-thrashing test. The accepted
   oscillation alternates two simultaneous arrivals and zero arrivals over
   spans 1/2/4, exactly averaging one event/cycle for every bank count.

The discarded exploratory outputs predate `/tmp/a2-phase2-model-final` and are
not used below. The deterministic adversarial traces do not use randomization;
the preregistered seeds therefore do not apply.

## RTL evidence

The generalized synthesizable core tail-stripes each global FIFO location:
`bank = write_pointer % BANK_COUNT`, `row = write_pointer / BANK_COUNT`.
Admission can retire one direct event and write up to B younger events in the
same cycle. Retirement remains a single global FIFO sequence. The testbench
keeps a per-source accepted-event scoreboard and detects payload corruption,
source-local reorder, duplicate, phantom, overflow, and non-unit isolated
latency.

Local results:

- 27 structural combinations: N=16/32/64 × B=1/2/4 × D=4/8/16, PASS;
- 24 control combinations at N=16/B=2/D=8: enter=2/4/6, valid exit=0/1/2,
  dwell=1/3/7, PASS;
- total `A2_PARAMETER_SWEEP_PASS runs=51`;
- original phase-1 directed conservation test: PASS, accepted=delivered=3;
- selected B4/D16 normalized common-TB `basic_sparse` and
  `basic_simultaneous`: PASS, errors=0 and accepted=delivered=32 in both;
- Verilator lint of N=64/B=4/D=16/E=4/X=0/Q=1: PASS with no warning.

Reproduction:

```bash
PATH=/tmp/a2-iverilog/usr/bin:$PATH \
  A2_PHASE2_RTL_OUT=/tmp/a2-phase2-rtl-postlint \
  tests/a2/run_parameter_sweep.sh

PATH=/tmp/a2-iverilog/usr/bin:$PATH AER_SIMULATOR=iverilog \
  A2_TEST_OUT=/tmp/a2-phase2-directed-postlint \
  tests/a2/run_directed_test.sh

scripts/a2_phase2_pareto.py --output-dir /tmp/a2-phase2-model-final
```

## Structural sweep

Each cell is `A2 overrun / flat-RR overrun (reduction)`, summed over the fixed
hotspot and recurrence traces. Negative reduction is a loss.

| Banks | Depth | N=16 | N=32 | N=64 |
|---:|---:|---:|---:|---:|
| 1 | 4 | 117/123 (4.9%) | 131/129 (-1.6%) | 120/126 (4.8%) |
| 1 | 8 | 117/123 (4.9%) | 131/129 (-1.6%) | 120/126 (4.8%) |
| 1 | 16 | 117/123 (4.9%) | 131/129 (-1.6%) | 120/126 (4.8%) |
| 2 | 4 | 99/123 (19.5%) | 110/129 (14.7%) | 113/126 (10.3%) |
| 2 | 8 | 87/123 (29.3%) | 89/129 (31.0%) | 84/126 (33.3%) |
| 2 | 16 | 72/123 (41.5%) | 55/129 (57.4%) | 57/126 (54.8%) |
| 4 | 4 | 99/123 (19.5%) | 108/129 (16.3%) | 115/126 (8.7%) |
| 4 | 8 | 87/123 (29.3%) | 88/129 (31.8%) | 91/126 (27.8%) |
| **4** | **16** | **72/123 (41.5%)** | **46/129 (64.3%)** | **39/126 (69.0%)** |

All B=2 and B=4 families clear the minimum absorption gate. Only B=4/D=16
retains 90% of the best reduction for all N. B=1 is rejected first at the
absorption gate because N=32 loses two more occurrences than flat RR; more
depth cannot help a one-write/cycle reservoir.

## Selected-family workload comparison

Throughput is fixed-window delivered events/cycle. Tail values are
occurrence-to-delivery cycles. These rows expose both gain and loss.

| N | Workload | Model | Overrun | Throughput | p95/p99 | Toggle/event |
|---:|---|---|---:|---:|---:|---:|
| 16 | fixed hotspot | A2 | 0 | 0.134 | 7/8 | 12.32 |
| 16 | fixed hotspot | flat RR | 12 | 0.107 | 5/5 | 11.23 |
| 16 | recurrence | A2 | 72 | 0.321 | 26/27 | 19.43 |
| 16 | recurrence | flat RR | 111 | 0.234 | 17/17 | 12.05 |
| 32 | fixed hotspot | A2 | 12 | 0.241 | 23/24 | 15.34 |
| 32 | fixed hotspot | flat RR | 60 | 0.134 | 9/9 | 11.58 |
| 32 | recurrence | A2 | 34 | 0.406 | 40/42 | 17.98 |
| 32 | recurrence | flat RR | 69 | 0.328 | 31/32 | 13.52 |
| 64 | fixed hotspot | A2 | 12 | 0.241 | 23/24 | 15.34 |
| 64 | fixed hotspot | flat RR | 60 | 0.134 | 9/9 | 11.58 |
| 64 | recurrence | A2 | 27 | 0.422 | 42/44 | 18.79 |
| 64 | recurrence | flat RR | 66 | 0.335 | 31/32 | 13.93 |

The reservoir frees source latches sooner and raises fixed-window throughput,
but accepted events wait behind a deeper backlog. Thus A2's recurrence p99 is
10/10/12 cycles worse than flat RR at N=16/32/64, and its burst toggle proxy is
also worse. This is the principal shortlist risk, not a hidden regression.

Against equal-capacity always-buffered B=4/D=16, A2 recurrence overruns are
72 versus 72 at N=16, 34 versus 32 at N=32, and 27 versus 30 at N=64. Adaptive
bypass therefore does not universally beat always-buffering under recurrence;
its decisive benefit is sparse latency/activity.

## Sparse path, conflicts, and oscillation

For all N, A2 sparse p95/p99 is 1/1 cycle; flat RR and always-buffered are 2/2.
A2 sparse toggle/event is 1.87, 1.94, and 1.97 at N=16/32/64, or only
12.2%, 12.3%, and 12.5% of equal-capacity always-buffered. All A2 hotspot and
spread rows record zero structural bank-conflict reject.

The fixed-hash falsification does fail: on the selected B=4/D=16 hotspot its
conflict reject/conflict-induced overrun counts are 90/12 at N=16 and 300/60 at
both N=32 and N=64. Tail striping removes this source-to-bank failure without a
source predictor or remapper.

For selected E=4/X=0/Q=1, mode transitions are 2 versus naive 256/128/64 for
oscillation spans 1/2/4. Burst-mode residence is 256 cycles and recovery is
2/3/5 cycles; post-phase sparse p99 remains 1. This low transition count is
deliberately sticky behavior, not instantaneous phase tracking.

The dwell sweep exposes the failure boundary. At E=8/X=1, dwell=1 transitions
2/128/64 times for spans 1/2/4 and therefore fails the half-naive gate on the
last two traces. Dwell=3 reduces all three to 2 transitions, while dwell=7 adds
no further suppression and stretches recovery from 4/4/6 to 8/8/10 cycles.
Exit=0 makes Q=1 sufficient and has the smallest quiet counter/toggle cost.

## PPA proxies

| N | Model | State bits | Cell proxy | Depth proxy |
|---:|---|---:|---:|---:|
| 16 | A2 B4/D16 | 345 | 497 | 9 |
| 16 | flat RR | 25 | 61 | 6 |
| 16 | always-buffered B4/D16 | 337 | 489 | 9 |
| 32 | A2 B4/D16 | 362 | 578 | 10 |
| 32 | flat RR | 27 | 96 | 7 |
| 32 | always-buffered B4/D16 | 354 | 570 | 10 |
| 64 | A2 B4/D16 | 379 | 723 | 11 |
| 64 | flat RR | 29 | 163 | 8 |
| 64 | always-buffered B4/D16 | 371 | 715 | 11 |

A2 adds eight control bits over the equal-capacity always-buffered proxy, but
four-bank admission and depth-16 storage dominate absolute cost. The proxy
cannot establish timing closure, SRAM inference, power, or routed congestion.

## Gate disposition

The machine-readable `/tmp/a2-phase2-model-final/decision.json` reports six
model-shortlisted families: B2/D4, B2/D8, B2/D16, B4/D4, B4/D8, and B4/D16.
Only B4/D16 clears the predeclared 90%-of-best soft retention rule for all N.
The separate 51-run RTL gate then passed, so the local shortlist decision is
**retain B4/D16/E4/X0/Q1 for head screening**.

No server, SSH, tmux, Xcelium, Genus, or Innovus operation was performed.

`PENDING_HEAD_XCELIUM`: the candidate-only runner now has explicit phase-2
compile overrides. The head command for the frozen 46-trace suite is:

```bash
AER_SIMULATOR=xrun \
  A2_RESERVOIR_DEPTH=16 A2_BANK_COUNT=4 \
  A2_ENTER_LEVEL=4 A2_EXIT_LEVEL=0 A2_QUIET_CYCLES=1 \
  A2_TRACE_DIR=/tmp/a2-phase2-head-traces \
  A2_SUITE_OUT=/tmp/a2-phase2-head-results \
  scripts/run_a2_neutrality_suite.sh
```

`PENDING_HEAD_GENUS`: the exact selected wrapper prevents phase-1 binding
defaults from being synthesized accidentally. Repeat with
`AER_NUM_SOURCES=16`, 32, and 64:

```bash
test -f "$AER_STD_CELL_ROOT/timing/slow_vdd1v0_basicCells.lib"
AER_NUM_SOURCES=16 \
  AER_LIBRARY_FILE="$AER_STD_CELL_ROOT/timing/slow_vdd1v0_basicCells.lib" \
  AER_RUN_ID=a2-phase2-head-genus-n16-5ns \
  scripts/run_synth.sh a2-phase2-b4-d16 \
  rtl/candidates/a2_adaptive_dual_path/head_phase2_genus_config.sh
```

`PENDING_HEAD_INNOVUS`: as in the phase-1 report, this repository has no frozen
common Innovus driver/MMMC/floorplan contract. The head must use the eventual
shared finalist flow with the mapped netlist and SDC produced above; no
candidate-private server command is invented.

## Frozen-boundary audit

This command is empty at completion:

```bash
git diff ad96895 -- scripts/run_clean_benchmark.sh tb/clean/aer_clean_tb.sv \
  benchmarks/clean_slate_aer/fixtures \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json
```
