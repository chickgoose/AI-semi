# A2 phase-3 local physical-proxy results

Status: corrected after independent A3 review, 2026-08-07

## Final decision and admissible basis

- **N=16: REJECT** B4/D16/E4/X0/Q1.
- **N=64: REJECT** B4/D16/E4/X0/Q1.

The decision now depends on exactly three independent local gates. Both sizes
fail all three:

| Independent reject gate | N16 | N64 |
|---|---:|---:|
| pressure events/cycle/(LUT4+FF) ≥98% of always-buffered | FAIL, 83.0% | FAIL, 73.2% |
| 4-LUT depth ≤125% of always-buffered | FAIL, 137.8% | FAIL, 146.9% |
| any pressure workload recovers events/cycle/(LUT4+FF) | FAIL, none | FAIL, none |

Toggle, fanout, raw overrun, and tail observations are explicitly **not reject
bases**. Removing them leaves both decisions unchanged. N64 recurrence p99 is
also one cycle worse than always-buffered, but that fact is only an observation.

Raw JSON/VCD/log/CSV outputs remain uncommitted under `/tmp`.

## Independent-review corrections

The A3 review at commit `3133a29` audited A2 snapshot `9613b6b`. The following
corrections supersede the original phase-3 report:

1. The first VCD parser counted wrapper/core port replicas and wrapper
   output/Q aliases as distinct activity. Those activity thresholds are
   withdrawn. The revised parser chooses one RTL representative per alias
   group, but the result remains a zero-delay RTL diagnostic—not mapped-net
   activity or power—and cannot affect keep/reject.
2. `A2_PHASE3_SKIP_YOSYS=1` trusted arbitrary nonempty JSON and still required
   a discoverable Yosys binary. The cached runner path is removed. Setting the
   variable now exits status 2 before tool checks. Direct analyzer use on
   manually curated JSON is permitted only as non-reproducible diagnostics.
3. N16 recurrence generates six stride-4 offers, but modulo 16 makes offers 4
   and 5 repeat offers 0 and 1 in the same cycle. The TB now reports
   `duplicate_overrun` separately from `backpressure_overrun`.
4. The original N16 fanout maximum was clock/reset fanout. Structural output
   now reports both all-net and clock/reset-filtered data/control fanout.
   Fanout is not a decision gate.
5. The stale committed N16 recurrence toggle prose is removed. The diagnostic
   table below is generated from the current alias-filtered CSV/VCD.

## Equal boundary and method limits

All designs use the same candidate-owned elastic event register per source and
one registered retire stage. They see the same 16-bit occurrences and
one-event/cycle ready retire service. The internal choices are fixed selected
A2, flat rotating arbitration, and always-buffered tail-striped B4/D16 storage.

Yosys 0.52 cannot parse the normalized unpacked event-array port, so the local
wrapper uses a packed event bus. A bounded randomized ready-stall test found no
cycle mismatch between the packed A2 mirror and canonical phase-2 core over 768
cycles at N16 and N64. This is useful simulation evidence, not formal proof.

The mapping is one disclosed FPGA-style proxy:

```text
proc; flatten; opt; memory_map; opt; techmap; opt;
abc -fast -lut 4; clean; write_json
```

Counts are therefore **4-input LUTs plus mapped FF cells**, not generic ASIC
cells or area. Depth is LUT depth, not timing. `memory_map` expands storage into
FF/mux logic. `flatten; opt` also permits cross-boundary optimization, so equal
RTL boundary registers are established but cell-for-cell survival of common
combinational logic is not.

## Yosys-0.52 fast-LUT4 structure

| N | Design | LUT4+FF | State bits | LUT depth | Fanout all | Fanout data/control |
|---:|---|---:|---:|---:|---:|---:|
| 16 | A2 selected | 5,996 | 636 | 153 | 636 | 356 |
| 16 | flat RR | 1,021 | 297 | 39 | 297 | 152 |
| 16 | always-buffered | 4,978 | 630 | 111 | 630 | 356 |
| 64 | A2 selected | 32,206 | 1,488 | 711 | 2,371 | 2,371 |
| 64 | flat RR | 7,801 | 1,117 | 203 | 2,118 | 2,118 |
| 64 | always-buffered | 23,094 | 1,482 | 484 | 2,334 | 2,334 |

At N16, all-net maxima 636/297/630 are clock/reset loads; the corrected A2 and
always-buffered data/control maxima are both 356. At N64 the maximum is already
a data/control RR-base net. These are reported only as topology observations.

A2 adds six state bits over always-buffered, but 1,018 LUT4+FF units at N16 and
9,112 at N64. A2/always structural ratios are 1.2045 and 1.3946.

## Functional/EPCC trace metrics

Triples are **A2 / flat RR / always-buffered**. EPCC means fixed-window
events/cycle/(LUT4+FF) ×10^6.

### N=16

| Workload | Throughput | Total overrun | p99 | EPCC ×10^6 |
|---|---:|---:|---:|---:|
| sparse | .0969/.0969/.0969 | 0/0/0 | 3/3/4 | 16.16/94.88/19.46 |
| fixed hotspot | .1339/.1071/.1339 | 0/12/0 | 10/6/11 | 22.34/104.94/26.90 |
| recurrence | .3214/.2344/.3214 | 72/111/72 | 29/18/30 | 53.61/229.55/64.57 |
| oscillate-4 | .5234/.4609/.5234 | 0/32/0 | 7/6/8 | 87.30/451.46/105.15 |

Every N16 recurrence design sees exactly 72 same-cycle duplicate-source
offers. Backpressure overruns on that trace are A2=0, flat=39, always=0.
Therefore `72/111/72` must not be read as A2/always storage absorption; their
72 losses are entirely generator/source-interface duplicates. Across hotspot
plus recurrence, backpressure-only overrun is 0/51/0.

### N=64

| Workload | Throughput | Overrun | p99 | EPCC ×10^6 |
|---|---:|---:|---:|---:|
| sparse | .0969/.0969/.0969 | 0/0/0 | 3/3/4 | 3.01/12.42/4.19 |
| fixed hotspot | .2411/.1339/.2344 | 12/60/15 | 26/10/26 | 7.49/17.17/10.15 |
| recurrence | .4219/.3348/.4152 | 27/66/30 | 46/33/45 | 13.10/42.92/17.98 |
| oscillate-4 | .5234/.4609/.5234 | 0/32/0 | 7/6/8 | 16.25/59.09/22.67 |

N64 has no same-cycle source duplicates. Its pressure backpressure overruns are
39/126/45, so A2's six-event advantage over always-buffered is real but too
small to recover its 39.46% structural premium.

All 24 runs pass `generated = accepted + overrun`, `accepted = delivered`,
payload, source order, duplicate retirement, phantom, and drain checks.

## EPCC recovery range

No representative pressure workload has A2 EPCC at least as high as either
reference:

- N16 A2 throughput equals always-buffered, but A2 has 20.45% more LUT4+FF;
  its per-trace EPCC is 83.02%. Break-even requires hotspot throughput 0.161
  instead of 0.134 or recurrence 0.387 instead of 0.321.
- N64 A2 throughput improves 2.86% on hotspot and 1.61% on recurrence, while
  LUT4+FF rises 39.46%. Break-even requires 0.327 instead of 0.241 and 0.579
  instead of 0.422; actual EPCC ratios are 73.76% and 72.86%.

Aggregated hotspot+recurrence EPCC is 37.97/167.25/45.74 ×10^-6 at N16 and
10.29/30.04/14.06 ×10^-6 at N64. The recovery region is empty independently of
all VCD activity calculations.

## Activity diagnostic—invalid for decision/power

The original activity table is invalid because of RTL alias double counting.
The revised parser filters wrapper/core input replicas and output aliases while
retaining one representative net, state, combinational signals, and explicit
storage shadows. This demonstrates the sensitivity but still does not bind RTL
signals to post-map nets, capacitance, or glitches. These values are diagnostic
only.

| N | Workload | Alias-filtered toggle/delivered, A2/flat/always |
|---:|---|---:|
| 16 | sparse | 24.26/24.26/34.16 |
| 16 | fixed hotspot | 23.40/13.67/20.07 |
| 16 | recurrence | 25.63/16.21/25.49 |
| 16 | oscillate-4 | 33.79/17.19/29.43 |
| 64 | sparse | 26.55/26.55/36.94 |
| 64 | fixed hotspot | 24.28/14.47/21.69 |
| 64 | recurrence | 29.32/16.41/29.19 |
| 64 | oscillate-4 | 35.97/18.17/32.15 |

The alias-filtered sparse ratios are 71.0% and 71.9%, which would pass the old
80% threshold—the precise reason toggle failure is removed from the reject
basis. Pressure ratios are 104.5% and 103.8%. Neither ratio is promoted as a
power result.

## Decision record

The corrected `decision.json` contains only these fields under
`independent_gates`: `pressure_epcc`, `lut_depth`, and `recovery_region`. Toggle,
fanout, overrun, functional, and tail values are under
`observations_not_reject_basis`. Both N values remain reject because all three
independent gates fail.

## Reproduction and cache disposition

Full clean reproduction requires Yosys and regenerates all six structural
JSONs:

```bash
PATH=/tmp/a2-iverilog/usr/bin:/tmp/a5-yosys/usr/bin:$PATH \
LD_LIBRARY_PATH=/tmp/a5-yosys/usr/lib/x86_64-linux-gnu \
YOSYS_DATDIR=/tmp/a5-yosys/usr/share/yosys \
A2_PHASE3_OUT=/tmp/a2-phase3-physical-final-reviewed \
  tests/a2/run_phase3_physical_proxy.sh
```

`A2_PHASE3_SKIP_YOSYS=1` is intentionally rejected because cached JSON lacks
self-authenticating RTL/flow/tool provenance. Calling
`scripts/a2_phase3_physical_proxy.py` directly on a manually curated directory
is analysis convenience only and is not a reproduction claim.

No common file, server, SSH/tmux panel, Xcelium, Genus, or Innovus operation was
used. The frozen-boundary command remains empty:

```bash
git diff ad96895 -- scripts/run_clean_benchmark.sh tb/clean/aer_clean_tb.sv \
  benchmarks/clean_slate_aer/fixtures \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json
```
