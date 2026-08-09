# AER bottleneck coverage and anti-specialization audit

Status: implementation-backed address-only review, 2026-08-10

## What “biased” means here

A workload is not biased merely because Ganghee's fovea, A23, or a future
candidate performs especially well on it. A row/column design winning a real
spatial-locality trace is a legitimate architectural advantage. Bias exists
when the suite omits other important AER bottlenecks, changes the offered trace
between candidates, uses accidental fixed source numbers as the result, or lets
a testbench adapter implement a missing candidate function for free.

For that reason the original locality and fan-in tests remain. The repair is to
add orthogonal bottleneck families and matched controls, not to weaken the
families that an existing design already solves.

## Coverage after the 2026-08-10 audit

| AER bottleneck | Frozen workload/control | What must be reported |
| --- | --- | --- |
| sparse basic AER correctness | `core_sparse_*` | loss, duplicate, corruption, E2E latency |
| simultaneous global fan-in | `core_simultaneous_*`, `global_fanin_*` | drain time, p99 latency, overrun |
| pair-dependent partition/HOL | `pairwise_contention_identity`, `pairwise_contention_affine` | `pairwise_contention_metrics.py`: per-trial/repeat completion latency/skew, order bias, distinct worst pairs, prior-pair overlap; runner-produced identity/affine cross-map delta |
| sustainable shared-link bandwidth | `uniform_l0p125` through `uniform_l2p00`, three seeds | completion/cycle plateau, overrun, backlog and latency tail |
| temporal burst sensitivity at equal mean | `shape_b1`, `shape_b4`, `shape_b16` | throughput/latency/overrun spread across burst size |
| spatial locality opportunity | `spatial_local` | local efficiency and latency |
| locality dependence/control | matched `spatial_dispersed` and `spatial_local_mirror` | paired local-versus-dispersed delta and location sensitivity |
| stationary hot source and mice | `elephant_mouse_*` | demand-normalized fairness, victim wait/loss |
| changing and multiple hotspots | `moving_hotspot_single_*`, `moving_hotspot_multi_*` | handoff transient, worst location, mice service |
| fixed-priority starvation/HOL effects | `rotating_victim_*` | minimum source service ratio, p99/max victim wait |
| source refire before release | `retrigger_*` | source overrun and occurrence-to-accept wait |
| overload recovery/hysteresis | `phase_transition_*` | post-overload sparse latency, backlog peak, recovery-to-zero cycles |
| address-number sensitivity | identity, mirror, bit-reverse, and affine pairs | paired mean, worst case, mapping sensitivity range |
| throughput above one event/cycle | uniform 1.25/1.5/2.0 and 16-way burst | completed logical events/cycle plus physical pin-cycle efficiency |

The exact common input is
`benchmarks/clean_slate_aer/manifest.neutrality-n16.json`: 50 N=16,
sink-always-ready traces. Each candidate must use the same generated JSONL SHA.
The generator regression gate verifies determinism, one occurrence per
source/cycle, fixed coordinate-spike semantics, matched burst histograms,
matched spatial timing, affine relabeling, and a zero-injection recovery phase.
`fixtures/neutrality_n16_golden.json` freezes every trace SHA, event count,
achieved mean load, peak rate, and aggregation group.

## Measurement corrections

- The old SV `fairness` number is raw delivered-count Jain fairness. It measures
  the intentionally unequal input distribution on elephant/mouse and therefore
  is not the ranking fairness metric.
- The aggregator now reports demand-normalized acceptance and delivery fairness,
  minimum per-source service ratios, active offered-source count, and
  demand-conditioned zero-service windows.
- Never-offered sources no longer lower the demand-conditioned result.
- `throughput_stddev` is reported across repeated runs. A final saturation claim
  still requires individual seeds and a frozen load grid.
- Common `throughput` now counts completions only inside the fixed stimulus
  window; candidate-dependent drain time is reported separately and cannot
  change the throughput denominator.
- `phase_metrics.py` joins the exact trace to per-event output and reports
  phase-local completion, latency, backlog, and recovery-to-zero.
- Timing A/B relations are TB-only trace fields, and `timing_pair_metrics.py`
  measures the actual cross-source output-gap distortion plus dropped/censored
  pairs without requiring a DUT payload tag.
- The TB checks generation and transport conservation, drains accepted events,
  and observes an eight-cycle post-drain quiet guard for late phantoms.
- Built-in SystemVerilog workload names are smoke/calibration only. They contain
  fixed source choices and cannot be used for final ranking.

## Deliberately separate suites and remaining gaps

The frozen 50-run core keeps the sink always ready because current candidates
do not expose equivalent output backpressure. Backpressure shock remains an
optional capability suite and cannot award or remove points from candidates
that lack that same external contract.

The common ingress boundary models one live event per source and a common
measurement clock. It measures retrigger loss caused by slow source release,
but cannot prove the benefit of a native multi-event-per-source-per-cycle or
asynchronous ingress. Such a proposal needs a separately frozen capability
suite whose native interface can present that multiplicity without a free TB
FIFO; its synchronizer/codec/storage cost belongs in candidate PPA.

Multi-hop routing/multicast, asynchronous CDC, and native multiple-occurrences
per source/cycle are outside the mandatory N=16 one-hop screening score while no
official competition contract requires them. They may be explored as clearly
labeled optional research suites, but RUN/SKIP count cannot change the current
ranking. If the official interface later requires one of them, the team must
freeze a new mandatory native stimulus boundary before seeing candidate results.

The following items are still required before calling the benchmark completely
frozen for final judging:

1. reset-after-drain is implemented; mid-traffic reset cancel/preserve semantics
   remain deliberately undefined and require a separately frozen contract;
2. fixed physical pin-budget comparison must include every required
   synthesizable serializer/codec/buffer in candidate PPA;
3. final saturation confidence should expand the three-seed screening sweep to
   a predeclared larger seed set and publish percentile/confidence bounds.

These gaps are recorded rather than hidden. They do not invalidate the common
coordinate-spike core, but future A2–A9 architecture agents must not claim an
unmeasured capability from the core suite alone.

## Gate for future architecture agents

Every proposed architecture must first pass exact event conservation on all
mandatory core traces. It then reports results by bottleneck family; no single
weighted score is chosen before results exist. A proposal may specialize and
win a family, but must disclose regressions in sparse latency, other workload
families, pin efficiency, and PPA. Adapter storage or protocol logic is legal
only when synthesizable and included in that proposal's PPA boundary.
