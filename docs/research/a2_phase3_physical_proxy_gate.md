# A2 phase-3 local physical-proxy gate

Status: frozen before phase-3 Yosys/VCD results, 2026-08-07

## Fixed candidate

Phase 3 does not reopen the phase-2 search. The only A2 point is
B=4, depth=16, enter=4, exit=0, dwell=1 at N=16 and N=64. No predictor,
remapper, compactor, token, codec, tree, or other track mechanism may be added.

## Equal boundary

All three cores sit behind one candidate-owned wrapper with identical:

- one elastic event register per source at ingress;
- occurrence acceptance/overrun semantics;
- one elastic registered retire stage;
- 16-bit event and one-event/cycle retire limit;
- rotating source priority and always-ready downstream during measurement.

The compared cores are selected A2, flat rotating arbitration with no internal
reservoir, and an always-buffered tail-striped B4/D16 FIFO. Common boundary
cells and their activity remain included in all physical proxies.

## Frozen local flow

Yosys 0.52 performs `proc`, `memory_map`, `flatten`, `techmap`, and generic ABC
4-LUT mapping. There is no liberty file and no timing claim. The resulting JSON is
used to count generic cells, sequential Q bits, maximum combinational cell
depth, and maximum net sink fanout.

Icarus runs the same deterministic sparse, modulo-4 hotspot, recurrence, and
span-4 oscillation occurrences for every design. A VCD parser counts bit
transitions below the physical wrapper, divided by delivered events. Zero-delay
RTL VCD is an activity proxy and does not model glitches or capacitance.

## Decision rule fixed before results

For each N, A2 is **keep** only if all conditions hold:

1. all three implementations conserve accepted/delivered events with no
   corruption, duplicate, phantom, or drain failure;
2. A2 pressure overrun (hotspot plus recurrence) is no worse than the
   equal-capacity always-buffered reference;
3. A2 aggregate pressure events/cycle/cell is at least 98% of
   always-buffered, allowing only a 2% control premium;
4. A2 sparse VCD toggle/delivered-event is at most 80% of always-buffered;
5. A2 aggregate pressure VCD toggle/delivered-event is at most 110% of
   always-buffered;
6. A2 p99 is never worse than always-buffered and is at most 16 cycles worse
   than flat RR on either pressure trace;
7. A2 generic logic depth and maximum fanout are each at most 125% of
   always-buffered.

The report also identifies individual workloads where A2 events/cycle/cell
exceeds either reference. A point is rejected if no such recovery region
exists, even if the aggregate inequalities happen to round equal. Flat RR is
reported as the absolute area-efficiency floor; A2 is not required to beat it
because flat RR deliberately provides no burst storage.

No gate is relaxed after observing results. Server EDA and common workload/TB
edits are prohibited.

## Independent-review finalization

The A3 review of snapshot `9613b6b` found that RTL VCD aliases made activity
thresholds representation-dependent and that the original fanout maximum could
be clock/reset. Final keep/reject therefore uses only three independent mapped
gates: pressure EPCC, LUT depth, and existence of an EPCC recovery region.
Toggle, overrun, tail, and data/control fanout remain reported observations but
cannot cause rejection. This disposition preserves the original thresholds as
historical preregistration while removing invalid evidence from the decision.
