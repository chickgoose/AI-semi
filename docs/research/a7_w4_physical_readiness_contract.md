# A7 W4 physical readiness contract

Status: **PHYSICAL HOLD — no server Genus/Innovus/CDC/RDC run has been made.**

This follow-up freezes the experiment inputs and evidence gates for RTL commit
`db3f04fe0e01699e63c596145fe71effc601e57c`. It does not upgrade the generic
Yosys proxy or nominal RTL simulation into physical evidence.

## Pre-run gate

The site manifest must identify and hash Genus, Innovus, and the CDC/RDC tool;
setup/hold Liberty, technology LEF, QRC technology, full PVT and derate identity;
and deterministic activity files. Named cells for all three technology roles
must be present in both Liberty views and have separate query evidence:

- characterized ICG replacing the generic latch-and-gate boundary;
- characterized ODDR or an explicitly enumerated equivalent launch-cell set;
- characterized IDDR or an explicitly enumerated opposite-edge capture set.

The generic RTL model is never accepted as cell-availability evidence. Missing
one role, a compiled/uninspectable library without evidence, mismatched hashes,
tool placeholders, activity-window drift, or load drift blocks the run.

## Timing and CDC/RDC receipt

Both Genus and Innovus receipts carry separate rise-edge setup/hold, fall-edge
setup/hold, rise-to-fall and fall-to-rise half-cycle slack, clock-gating
setup/hold, and asynchronous reset recovery/removal slack. Every value must be
nonnegative. Post-route high and low pulses must each remain at least 7 ns,
clock skew at most 0.5 ns, and unconstrained paths zero.

The reset is not false-pathed. Recovery/removal reports and RDC analysis are
mandatory. Internal unwaived CDC and RDC counts must be zero. Because W4 has no
consumer-core synchronizer, `retire_*` must be reported exactly as
`EXPLICIT_ASYNC_OUTPUT_BOUNDARY`; the receipt cannot claim it is synchronous.

Innovus qualification additionally requires placement, CTS, detailed route and
extraction completion, zero DRC and antenna violations, and hashed post-route
netlist/SPEF/timing reports. Genus remains screening evidence even when its gate
passes.

## Boundary, activity, and energy

The full charged boundary contains TX, the mapped ICG, two data wires, forwarded
clock, and RX. It excludes the upstream producer and downstream core
synchronizer. The top has five non-clock/reset functional input bits and eight
non-clock functional output bits; the link itself has two data plus one clock
pin. Output load is 0.01 pF per pin with 0.05 ns clock and 0.10 ns data input
transition. These values and the boundary ID must agree in site and result
receipts.

Two 512-cycle post-warm-up windows are frozen: sparse one event per eight cycles
and saturated one event per cycle. Both use `(5e+3) mod 16`, exclude reset/drain,
and require 64 and 512 completed logical events respectively. Activity coverage
must be at least 95%, the exact activity hash must propagate from site manifest
to power receipt, and vectorless power is rejected.

Energy/event is accepted only from extracted, post-route, activity-annotated
total power including the clock tree and full boundary:

```text
energy_pJ/event = total_power_mW * 8192 ns / completed_logical_events
```

The validator recomputes the value. Zero work, coverage below 95%, activity hash
drift, missing clock-tree/boundary inclusion, or formula disagreement fails.

## Current evidence and reproduction

The local suite proves only that the immutable contract is internally
consistent and that synthetic fixtures fail or pass the intended gates. It
tests missing ICG/ODDR/IDDR roles, pin-load drift, negative half-cycle and
recovery slack, unwaived CDC, energy disagreement, and missing reports. Fixture
mode requires both the explicit command flag and `synthetic_fixture:true` in the
site and receipt; production-labeled inputs are rejected on that path. Synthetic
fixtures can never print physical qualification.

```sh
scripts/run_a7_w4_physical_preflight.sh
```

The authoritative machine-readable files and templates are under
`physical/a7_event_triggered_ddr_burst_link_w4/`. Until a non-synthetic site
manifest and receipt from the authorized server pass, the only valid conclusion
is **PHYSICAL HOLD**.
