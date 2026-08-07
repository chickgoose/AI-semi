# A2 phase-3 local physical-proxy results

Status: complete local evidence, 2026-08-07

## Decision

- **N=16: REJECT** B4/D16/E4/X0/Q1 at the local physical-proxy gate.
- **N=64: REJECT** B4/D16/E4/X0/Q1 at the local physical-proxy gate.

The phase-2 functional result remains valid: the reservoir reduces flat-RR
source overrun and preserves the sparse bypass. Phase 3 rejects both sizes
because the throughput gain does not recover generic mapped area, the sparse
VCD reduction misses the predeclared 20% requirement, and A2's combinational
depth exceeds the equal-capacity always-buffered bound. N64 also loses the
strict tail gate by one cycle on recurrence.

Raw JSON, VCD, logs, and aggregate CSVs are intentionally uncommitted under
`/tmp/a2-phase3-physical-final`.

## Boundary and flow audit

All implementations use the same candidate-owned one-entry register per
source and one-entry registered retire boundary. All see the identical
16-bit occurrences and one-event/cycle ready retire service. The only internal
differences are selected A2, flat rotating selection, and always-buffered
tail-striped B4/D16 storage.

Yosys 0.52 cannot parse the normalized unpacked event-array port. The local
physical wrapper therefore uses a packed event bus. A cycle-by-cycle test with
backpressure proved the packed A2 mirror equal to the original phase-2 selected
core for 768 cycles at both N=16 and N=64.

The preregistration said generic ABC mapping. An initial simple-gate ABC run at
N64 did not finish in a practical reproducibility window and was discarded
before any result was accepted. The final flow fixes the generic unit to a
4-input LUT (`abc -fast -lut 4`); all six structures use that exact flow. No
liberty file, clock constraint, placement, routing, or server tool is involved.

Icarus does not automatically dump unpacked memory words. Identically sized
simulation-only packed shadows expose the 16 event/source words in both A2 and
always-buffered VCDs. They are excluded under `SYNTHESIS` and therefore add no
Yosys cells. The parser excludes testbench inputs, clock/reset, parameters, and
procedural integer loop variables; it counts bit transitions on wrapper/core
registers, combinational nets, and explicit storage shadows. This is a
zero-delay RTL activity proxy, not power.

## Generic physical structure

| N | Design | Generic cells | State bits | Logic depth | Max fanout |
|---:|---|---:|---:|---:|---:|
| 16 | A2 selected | 5,996 | 636 | 153 | 636 |
| 16 | flat RR | 1,021 | 297 | 39 | 297 |
| 16 | always-buffered | 4,978 | 630 | 111 | 630 |
| 64 | A2 selected | 32,206 | 1,488 | 711 | 2,371 |
| 64 | flat RR | 7,801 | 1,117 | 203 | 2,118 |
| 64 | always-buffered | 23,094 | 1,482 | 484 | 2,334 |

The adaptive controller adds only six state bits over always-buffered, but its
level/derivative checks and conditional direct-plus-multiwrite selection add
1,018 cells at N16 and 9,112 at N64. A2/always cell ratios are 1.2045 and
1.3946; logic-depth ratios are 1.378 and 1.469. Fanout ratios are only 1.010
and 1.016, so fanout passes while depth fails.

## Representative trace metrics

Triples below are **A2 / flat RR / always-buffered**. EPCC is fixed-window
events/cycle/cell × 10^6. Toggle is VCD bit transitions per delivered event.

### N=16

| Workload | Throughput | Overrun | p99 | Toggle/event | EPCC ×10^6 |
|---|---:|---:|---:|---:|---:|
| sparse | .0969/.0969/.0969 | 0/0/0 | 3/3/4 | 44.65/44.65/54.55 | 16.16/94.88/19.46 |
| fixed hotspot | .1339/.1071/.1339 | 0/12/0 | 10/6/11 | 36.12/27.60/32.78 | 22.34/104.94/26.90 |
| recurrence | .3214/.2344/.3214 | 72/111/72 | 29/18/30 | 42.11/33.19/42.01 | 53.61/229.55/64.57 |
| oscillate-4 | .5234/.4609/.5234 | 0/32/0 | 7/6/8 | 53.81/33.23/49.45 | 87.30/451.46/105.15 |

### N=64

| Workload | Throughput | Overrun | p99 | Toggle/event | EPCC ×10^6 |
|---|---:|---:|---:|---:|---:|
| sparse | .0969/.0969/.0969 | 0/0/0 | 3/3/4 | 49.13/49.13/59.52 | 3.01/12.42/4.19 |
| fixed hotspot | .2411/.1339/.2344 | 12/60/15 | 26/10/26 | 38.64/29.02/36.20 | 7.49/17.17/10.15 |
| recurrence | .4219/.3348/.4152 | 27/66/30 | 46/33/45 | 45.67/32.59/45.94 | 13.10/42.92/17.98 |
| oscillate-4 | .5234/.4609/.5234 | 0/32/0 | 7/6/8 | 57.06/35.06/53.24 | 16.25/59.09/22.67 |

All 24 trace/design/size runs pass `generated = accepted + overrun`,
`accepted = delivered`, payload, per-source ordering, duplicate, phantom, and
drain checks.

## Cost-recovery range

No representative workload has A2 EPCC greater than either reference. This is
not merely an aggregate artifact:

- N16 A2 has the same throughput as always-buffered on every listed trace but
  20.45% more cells, so its EPCC is a fixed 83.02% of always-buffered. Break-even
  would require hotspot throughput 0.161 rather than 0.134, or recurrence 0.387
  rather than 0.321.
- N64 A2 has 2.86% more hotspot throughput and 1.61% more recurrence throughput
  than always-buffered, but 39.46% more cells. Break-even would require 0.327
  rather than 0.241 on hotspot and 0.579 rather than 0.422 on recurrence. Its
  actual EPCC is only 73.76% and 72.86% of always-buffered.
- Against flat RR, A2 cell ratios are 5.87× at N16 and 4.13× at N64. Observed
  throughput multipliers reach only 1.37× and 1.80×, respectively.

Aggregating hotspot and recurrence, EPCC is 37.97/167.25/45.74 ×10^-6 at N16
and 10.29/30.04/14.06 ×10^-6 at N64 (A2/flat/always). Hence the observed
workload recovery range is **empty**.

Sparse bypass still saves activity, but only 18.15% at N16 and 17.45% at N64
relative to always-buffered, narrowly missing the frozen 20% gate. Aggregate
pressure toggle/event is 38.15 versus 37.42 at N16 (+1.94%) and 43.11 versus
42.42 at N64 (+1.63%), within the allowed 10% premium. Oscillate-4 is worse by
8.81% and 7.17%, respectively.

## Gate disposition

| Gate | N16 | N64 | Reason when failed |
|---|---|---|---|
| functional conservation/equivalence | PASS | PASS | — |
| pressure overrun ≤ always-buffered | PASS | PASS | 72=72; 39<45 |
| aggregate pressure EPCC ≥98% always | FAIL | FAIL | 83.0%; 73.2% |
| sparse toggle ≤80% always | FAIL | FAIL | 81.85%; 82.55% |
| pressure toggle ≤110% always | PASS | PASS | 101.94%; 101.63% |
| pressure tail bound | PASS | FAIL | N64 recurrence p99 46 vs always 45 |
| depth ≤125% always | FAIL | FAIL | 137.8%; 146.9% |
| fanout ≤125% always | PASS | PASS | 101.0%; 101.6% |
| any EPCC recovery region | FAIL | FAIL | none of four traces |

The final machine-readable result is
`/tmp/a2-phase3-physical-final/decision.json`: `n16=reject n64=reject`.

## Reproduction

The exact local command used was:

```bash
PATH=/tmp/a2-iverilog/usr/bin:/tmp/a5-yosys/usr/bin:$PATH \
LD_LIBRARY_PATH=/tmp/a5-yosys/usr/lib/x86_64-linux-gnu \
YOSYS_DATDIR=/tmp/a5-yosys/usr/share/yosys \
A2_PHASE3_OUT=/tmp/a2-phase3-physical-final \
  tests/a2/run_phase3_physical_proxy.sh
```

`A2_PHASE3_SKIP_YOSYS=1` may reuse existing JSON only for VCD/debug reruns; it
is not the clean reproduction command. No common file, server, SSH/tmux panel,
Xcelium, Genus, or Innovus operation was used.

The frozen-boundary command remains empty:

```bash
git diff ad96895 -- scripts/run_clean_benchmark.sh tb/clean/aer_clean_tb.sv \
  benchmarks/clean_slate_aer/fixtures \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json
```
