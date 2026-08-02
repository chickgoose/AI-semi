# A23 EE430 final candidate qualification

Date: 2026-08-02

Branch: `integration/a23-final-candidate`

## Decision

A23 is the current internal final candidate for the Digital AER submission. It
combines the A2 FIFO-free rotating round-robin arbiter with the A3 bubble-free TX
refill path and the baseline one-entry elastic RX:

```text
sources -> rotating round-robin -> bubble-free TX -> elastic RX -> output
```

The qualified functional RTL is exactly commit `57d17e6`; the qualification
branch adds tests, PPA records, and documentation only. Relative to the original
fixed-priority baseline, A23 supplies bounded service under contention and
doubles steady-state throughput from 0.5 to 1 event/cycle without adding a FIFO.

## Qualification inputs

| Gate | Source commit | Result |
| --- | --- | --- |
| Committed-RTL functional regression | `3461f06` | 18/18 PASS |
| Genus PPA comparison | `67f4d5e` | 12/12 PASS |
| Independent stress regression | `7677fca` | 120/120 PASS |

The commits were replayed onto `57d17e6` as documentation and verification
changes. `git diff 57d17e6 -- rtl` is empty.

After integration, A1 reran both simulators for the 18 functional cases, the
full 120-run stress matrix, and all six existing stream/contention parameter
checks. Every replay passed and no qualification log contained a FAIL result.

## Functional result

Icarus Verilog 12.0 and Verilator 5.032 both passed `NUM_SOURCES=1,3,4`.
Continuous traffic sustained input and output initiation interval 1 after the
two-cycle pipeline fill. Under all-source contention, maximum service gaps were
1, 3, and 4 for source counts 1, 3, and 4. The cycle scoreboards found no
missing, duplicate, reorder, source/address corruption, occupancy, grant, or
priority-advance failure.

The stress matrix covered 20 seeds per source count in both simulators. It also
covered random producer activity and backpressure, alternating ready, unequal
bursts, long full-pipeline stalls, simultaneous RX drain/TX refill/input accept,
and reset boundaries. All 120 runs passed. The worst measured latency was 98
cycles during an intentionally generated 96-cycle downstream stall; normal
downstream-ready latency remained two cycles.

## Genus PPA at NUM_SOURCES=4

Common conditions were Genus 23.14-s090_1, `slow_vdd1v0_basicCells.lib`,
`PVT_0P9V_125C`, a 5.000 ns clock, identical I/O constraints and load, and
medium generic/map/opt effort.

| Design | Area (um2) | Estimated Fmax (MHz) | Vectorless power (mW) | Throughput (event/cycle) |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 432.288 | 762.486 | 0.0535469 | 0.5 |
| A2 round-robin | 481.946 | 738.225 | 0.0600751 | 0.5 |
| A3 bubble-free | 433.656 | 752.615 | 0.0620979 | 1.0 |
| A23 combined | 478.458 | 670.961 | 0.0700068 | 1.0 |

A23 costs 10.68% more raw area and 30.74% more vectorless power than baseline,
but its doubled throughput reduces area per event/cycle by 44.66%, reduces power
per event/cycle by 34.63%, and improves throughput/area by 80.70%. A3 has the
best raw throughput-normalized PPA but retains fixed-priority starvation risk;
A23 is preferred when bounded fairness is part of the design objective.

## Open contract and measurement limits

- `src_ready_o` can be high while reset is asserted. TX/RX state still clears,
  output valid remains low, and the first post-reset event transfers exactly
  once. Gating ready must remain a separate decision until the official reset
  contract is known.
- Power is a Genus vectorless relative estimate, not VCD/SAIF activity-annotated
  signoff power.
- Fmax is inferred from the mapped 5 ns synthesis result, not a period sweep or
  post-route achieved frequency.
- The official AER interface, testbench, PVT, constraints, scoring weights, and
  submission format remain unknown and may change the ranking.

## Next architecture gate

Do not add storage to this qualified branch. If the official workload shows
that burst absorption is valuable, evaluate a one- or two-entry shared elastic
buffer in a new experiment branch against this A23 candidate. Keep SMP, CMP,
event replication, predictors, and superpipelining deferred until the official
event semantics and scoring contract are available.

Detailed evidence:

- [Committed RTL functional verification](../verification/a23-functional.md)
- [Independent stress verification](../verification/a23-stress-report.md)
- [Genus PPA comparison](a23-ee430-genus-comparison.md)
