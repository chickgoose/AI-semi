# A2 phase-2 Pareto preregistration

Status: criteria frozen before phase-2 sweep results, 2026-08-07

## Fixed phase-1 evidence

Phase-1 architecture and results remain the immutable comparison point at
commits `3c33dd1` and `1006b39`. Phase 2 does not edit the common workload, TB,
fixture, golden SHA, or either phase-1 result document. The original N=16,
two-bank, depth-8, enter-4, exit-1, dwell-3 configuration remains the anchor.

Phase 2 stays on the same mechanism axis: zero-queue sparse bypass, a finite
tail-striped banked reservoir, occupancy level and derivative, and hysteretic
mode retention. It will not introduce prediction, learned scoring, spatial
trees, codecs/packing, prefix compaction, calendar scheduling, tokens, or
multi-hop routing.

## Questions fixed before seeing results

1. Does wider temporary admission still help at N=32/64, or does selector and
   bank control cost dominate a one-lane retire boundary?
2. Is depth-8's phase-1 exchange (eight fewer drops for roughly seven worse
   overload-tail cycles) a general capacity/latency law?
3. Can hysteresis retain burst readiness without mode thrashing under an
   alternating offered load?
4. Does tail-striped storage avoid the source-to-bank collision that defeats a
   tempting fixed `source % banks` mapping?
5. Is any point nondominated against both a flat rotating one-lane reference
   and an always-buffered reference with identical depth and bank width?

## Frozen sweep

### Structural grid

- source count: 16, 32, 64;
- reservoir banks/admission writes: 1, 2, 4;
- total depth: 4, 8, 16, with `depth >= banks` and `depth % banks == 0`;
- control anchor: enter=`depth/2`, exit=1 (0 for depth 1, not used here),
  quiet dwell=3.

The RTL mapping is tail-striped: consecutive logical FIFO locations map to
consecutive banks. It preserves one global order and makes up to `banks`
same-cycle writes conflict-free. A fixed source-hash mapping is evaluated only
as a falsification reference, never silently substituted into the candidate.

### Control grid

For every structurally nondominated point, sweep:

- enter threshold: `depth/4`, `depth/2`, `3*depth/4` (integer, minimum 1);
- exit threshold: 0, 1, `depth/4`, retaining only `exit < enter`;
- quiet dwell: 1, 3, 7 cycles.

No parameter is selected from a single favorable seed. Candidate-only
adversarial generators use seeds 4201, 4202, and 4203 where randomization is
applicable.

## Candidate-only falsification workloads

These do not alter or enter the frozen common ranking.

### Fixed-bank hotspot

For B banks, active sources are chosen from one congruence class
`s % B == 0`. Bursts also include a matched spread control with one source per
bank. The offered cycle/count histogram is identical. Report:

- source overrun and accepted/delivered conservation;
- explicit `bank_conflict_reject` (valid/free capacity existed, but a fixed
  bank could not take another write);
- p95/p99 latency and drain cycles.

The source-hash reference is expected to fail here. The tail-striped A2 must
show zero structural bank conflicts; overload caused solely by finite depth or
the one-lane retire rate remains separately reported.

### Recurrence absorption

A rotating group of hot sources refires after short gaps around the drain
service time, followed by sparse probes. This asks whether temporary multiwrite
admission frees one-entry source latches soon enough to prevent the next
occurrence from overrunning.

### Oscillating load

The trace alternates every 1, 2, and 4 cycles between isolated traffic and
`banks+1` simultaneous sources, then ends with isolated probes and drain. It
reports mode transitions, cycles in burst mode, return-to-bypass delay, sparse
probe latency, and a naive single-threshold controller's transition count.

## Reference definitions

- **Flat RR:** one rotating selection accepted per cycle and one registered
  completion per cycle, no candidate storage. It is lossless for accepted
  events and uses the same one-entry source latches.
- **Always-buffered:** same total depth and maximum bank writes as the A2 point,
  the same global FIFO order and rotating admission, but every event writes and
  reads storage; no direct bypass or adaptive mode.
- **Fixed-hash banked:** same total depth/bank count as A2, but source `s` can
  write only bank `s % banks`. It exists only to expose bank conflicts.

All references use the same occurrence stream and one-lane retirement. A2 does
not receive free storage, lanes, or a different drain window.

## Metric and proxy definitions

- sparse bypass latency: occurrence-to-delivery on isolated probes;
- absorption: reduction in source overrun against flat RR;
- bank-conflict overrun: occurrences lost after a source remains pending due
  specifically to a bank conflict, separated from full-capacity/retire-rate
  pressure;
- toggle proxy/event: payload bit transitions on reservoir writes and reads,
  valid/pointer/count/mode transitions, divided by delivered events;
- state bits: `depth*(address_width+source_width)` plus pointers, count,
  previous count, rotation, quiet counter, and mode bit;
- cell proxy: state bits plus weighted compare/select/mux/write-port terms;
- depth proxy: `ceil(log2(N))` rotating-select levels +
  `ceil(log2(banks+1))` admission levels + bank/read mux and mode compare levels.

Toggle/cell/depth figures are architecture proxies, not standard-cell PPA.
Server tools remain prohibited in this phase.

## Predeclared shortlist gates

A point is shortlisted only if all hard gates pass:

1. synthesizable RTL and directed tests pass for N=16/32/64 and banks=1/2/4,
   with no loss, duplicate, corruption, ordering error, overflow, or phantom;
2. isolated sparse p95 and p99 are exactly one cycle and never worse than flat
   RR or always-buffered;
3. tail-striped A2 records zero structural bank conflicts on hotspot and spread
   controls; any fixed-hash point with a conflict is rejected;
4. recurrence or hotspot overrun improves over flat RR for every N, and by at
   least 10% for at least two of the three N values;
5. oscillating-load mode transitions are no more than half the naive
   single-threshold count, with post-oscillation sparse p95 still one cycle;
6. sparse toggle proxy/event is at most 60% of equal-capacity always-buffered;
7. no result obtains capacity gain by exceeding one retire/event per cycle or
   by changing the offered trace;
8. the point is nondominated across overrun, p99, toggle proxy/event, state
   bits, and depth proxy within its N group.

Soft ranking prefers the smallest depth and bank count that retain at least 90%
of the best observed overrun reduction, then lower tail latency, toggles, state,
cell, and depth proxies in that order. If no point clears every hard gate, A2 is
not shortlisted; the report will identify the first failing gate rather than
relaxing it after seeing results.
