# Eight-track state/toggle/energy normalization audit

Status: read-only cross-track audit, 2026-08-07. The A8 calendar-wheel
shortlist decision remains frozen and is not revised by this document.

## 1. Evidence boundary

This audit reads only committed `HEAD` content from the eight clean-slate
worktrees. Dirty and untracked work in another worktree is not evidence. No
candidate RTL, common workload/TB/trace/golden, or server state was modified;
no synthesis or simulation was rerun. The snapshots are:

| Track | HEAD | Committed evidence used |
| --- | --- | --- |
| A2 | `0cf40b8` | `a2_phase2_pareto_results.md`, phase-2 proxy code, phase-3 gate preregistration |
| A3 | `6bad03a` | `a3_homeostatic_inhibition.md` sections 9--14 |
| A4 | `5f07aee` | `a4_quadtree_structural_shortlist.md`, committed structural CSVs |
| A5 | `66c76c3` | `a5_speculative_pregrant.md` sections 10--12 |
| A6 | `c5f8a39` | `a6_v2_nonexpanding_codec.md`, committed v2 synthesis/RTL JSON |
| A7 | `cc7c8c5` | `adversarial-scaling.md`, `adversarial-structural.csv` |
| A8 | `4b92f59` | frozen B8 scaling, toggle, and local Yosys proxy report |
| A9 | `d3a386d` | distributed-token results and analytic scaling table |

In particular, uncommitted A2 physical wrappers, A6 v3 outputs, A7 rescue RTL,
and A9 phase-4 Yosys work visible in their worktrees were ignored. A3's
untracked cross-track audit was also ignored.

The common clean benchmark places one source latch outside the candidate. It
is therefore absent from most candidate state counts. A4's structural pair
deliberately adds an equal ingress slot per source, while A9 deliberately owns
another ingress register per cell. These are real boundary differences, not
policy bits. “State bits” below means declared or mapped sequential Q bits; it
does not include clock-tree state, scan, reset cells, or the common TB.

## 2. State decomposition

`Ingress/transport` is payload-bearing or occupancy state used to move or hold
events. `Policy/control` is history, priority, age, score, pointer, or local
arbitration control. `Output` states the number of logical lanes and their
explicit registered bits. A dash means that committed evidence does not
support the number. A dagger is a formula extrapolation, not an N=64 RTL run.

| Track/configuration | N | Ingress/transport bits | Policy/control bits | Output lane state | Codec endpoints | Total state bits | Boundary warning |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A2 B4/D16 selected | 16 | 320 reservoir payload/source | 25 reported residual | 1 lane, no equalized output register | — | **345 reported** | phase-2 model/proxy boundary; direct output can be combinational |
|  | 64 | 352 reservoir payload/source | 27 reported residual | 1 lane, no equalized output register | — | **379 reported** | same |
| A3 membrane | 16 | 0 inside policy core | 104 membrane/homeostasis/phase | 1 × 21 | — | **125** | common source latches excluded |
|  | 64 | 0 | 394† | 1 × 23† | — | **417†** | formula only; no committed N=64 proxy |
| A4 quadtree structural top | 16 | 272 equalized ingress + 116 internal slots | 10 node rotations | 1 × 29 root slot | — | **427 mapped Q** | every slot carries an extra 8-bit age field |
|  | 64 | 1,088 equalized ingress + 620 internal slots | 42 node rotations | 1 × 31 root slot | — | **1,781 mapped Q** | N=64 structural wrapper, not frozen-46 RTL |
| A5 H4/T16/C2 | 16 | 0 | 187 predictor/fallback/streak | 1 × 21 | — | **208** | predictor metrics counters disabled in synthesis |
| A5 default-parameter extrapolation | 64 | 0 | 975† | 1 × 23† | — | **998†** | T and history width scale to 64/6; no N=64 run |
| A6 v2 codec | 16 | included in endpoints | 4 RR pointer outside endpoints | decoder retires 1 logical event; embedded | **624** encoder+decoder | **628 mapped Q** | endpoint boundary, 2-bit serialized link; not an arbiter-only state count |
|  | 64 | — | — | — | — | **not supported/reported** | committed codec/address widths are an N=16 point |
| A7 prefix compactor K4 | 16 | 16 inflight + 84 lane buffers | 4 rotation | 4 × 21 | — | **104 mapped Q** | four output lanes, not comparable with one-lane schedulers |
|  | 64 | 64 inflight + 92 lane buffers | 6 rotation | 4 × 23 | — | **162 mapped Q** | structural N=64/K4 point |
| A8 B8 wheel | 16 | 0 payload storage | 57 tracked/tag/epoch/phase/tie | 1 × 21 | — | **78** | common source latch external; final rejection remains fixed |
|  | 64 | 0 | 333 | 1 × 23 | — | **356** | candidate-owned scaling RTL proxy |
| A9 static distributed | 16 | 272 local ingress + 640 FIFO payload + 32 occupancy | 16 local toggles | 4 lanes; tail FIFO state already counted | — | **960** | lane count and storage topology are the mechanism |
|  | 64 | 1,088 + 2,816 + 128 | 64 | 8 lanes; embedded | — | **4,096** | analytic square L=D=8 point, not synthesis |

There is one report/code discrepancy worth preserving. A2's committed table
states 345/379 bits, while the committed `proxy()` expression evaluates to
344/378 for N=16/64 with B4/D16/E4/X0/Q1. This audit uses the published table
as the result and does not silently repair the one-bit difference. Until A2's
equal-boundary phase-3 wrapper has committed results, A2 state is not suitable
for a one-bit-precise cross-track ranking.

Useful state comparisons are consequently limited:

- A3, A5, and A8 all exclude the common source latches and own one conventional
  output register, so their N=16 totals are structurally close enough for a
  **bit-budget** comparison. They still implement different functions.
- A4 can be compared with its own flat structural reference because both carry
  identical ingress, age, and output state. Its 427/1,781 totals must not be
  ranked against A8 without first removing the 272/1,088 equalized ingress and
  acknowledging the artificial 8-bit age carried at every merge level.
- A7 K4 and A9 L4/L8 buy multiple output lanes. Dividing their state by lane is
  descriptive only: it does not equalize accepted bandwidth, internal storage,
  pin count, or stall semantics.
- A6's 624 bits are two codec endpoints and their block buffers. Subtracting an
  arbiter output register cannot turn that endpoint cost into scheduler state.

## 3. Synthesis and depth evidence

“Mapped” is not one common meaning in the committed reports. The flow/library
column is part of every number and prevents a false global ranking.

| Track/configuration | N=16 cells / depth | N=64 cells / depth | What was counted | Cross-track use |
| --- | ---: | ---: | --- | --- |
| A2 B4/D16 | 497 / 9 | 723 / 11 | analytical `cell_proxy` and operator depth | **not mapped**; compare only inside A2 phase-2 model |
| A3 membrane | — | — | no committed mapped synthesis result | unavailable |
| A4 quadtree | 2,059 / 24 | 9,705 / 27 | Yosys 0.52, `techmap`, ABC `simple`; total cells include 427/1,781 DFF | valid against A4 flat only (3,220/43; 15,485/114) |
| A5 H4/T16/C2 | 2,844 / 62 | — | Yosys/ABC NAND+NOT mapping, 208 FF; test counters removed | valid against A5 fallback only (1,441/61) |
| A6 v2 full candidate | 16,565 / — | — | local Yosys generic cells; endpoints 16,143 cells, 628 FF | endpoint-vs-RAW study only; no depth reported |
| A7 prefix K4 | 5,592 comb gates / 139 | 33,105 / 398 | post-`techmap` one-bit generic combinational gates; Q bits separate | valid against equal-state replicated K4 only (6,729/248; 72,845/836) |
| A8 B8 | 981 RTLIL cells / 177 | 6,993 / 693 | `proc; flatten; opt; stat; ltp -noff`, without techmap/ABC | word/operator-cell proxy; **not mapped gates** |
| A9 static | — / local 2:1 claim | — / local 2:1 claim | analytic state/channel/fan-in model | no mapped cells or numeric gate depth |

A4, A5, and A7 all used Yosys 0.52 locally, but that alone does not make their
cell counts comparable. A4 maps to ABC's `simple` gate set, A5 maps to a
NAND/NOT basis, and A7 reports post-techmap one-bit gates. A8 stops at RTLIL
operators. Their `ltp` lengths therefore have different cell alphabets. Only
same-flow, same-wrapper, same-N/K comparisons within each report are defensible.

## 4. Toggle measurements and normalization limits

The committed toggle evidence has four incompatible measurement boundaries.
Values are reproduced without merging them into a synthetic “energy score.”

| Track | N/workload | toggles/cycle | toggles/delivered event | Exact measurement boundary |
| --- | --- | ---: | ---: | --- |
| A2 | 16 sparse / hotspot / recurrence | — | 1.87 / 12.32 / 19.43 | phase-2 executable-model state-transition proxy |
|  | 64 sparse / hotspot / recurrence | — | 1.97 / 15.34 / 18.79 | same model; not VCD |
| A3 | 16 sparse | 0.2246 | 7.1875 | VCD transitions in 96 membrane bits, `h`, phase, and retire register |
|  | 16 uniform 1.25 | 12.7889 | 12.7743 | same state-only VCD boundary; common TB and combinational nodes excluded |
| A4 | 16/64 | — | — | no committed state-toggle/VCD proxy |
| A5 | 16 directed patterns | — | — | only relative ready/retire control+payload transition deltas; hidden predictor table writes excluded |
| A6 | 16 sparse / uniform / retrigger | — | 7.0625 / 2.5469 / 1.842 | **link-pin** data/count/ready transitions, not endpoint state toggles |
| A7 | 16/64 | — | — | no committed toggle proxy |
| A8 B8 | 16 deterministic 1.25 proxy | 11.6370 | 11.6370 | scheduler sequential state + output valid/source; event payload fixed zero |
|  | 64 deterministic 1.25 proxy | 15.5483 | 15.5483 | same 4,096-cycle proxy; one delivery/cycle |
| A9 | 16/64 | — | — | occupancy and lane-utilization counters only, not bit transitions |

The denominators also differ. A2 reports per delivered model event but not
cycles. A3 uses frozen/common-like workloads and counts actual sequential VCD
changes. A5 reports absolute deltas for selected directed sequences without a
portable denominator. A6 counts external link-pin transitions, where delimiter
cycles and serialization are the object of study. A8 uses a deterministic
candidate-owned offered stream and makes toggles/cycle equal toggles/event by
delivering exactly one event each measured cycle. None of these is a legal
numerical substitute for another.

Even within one definition, `toggles/delivered-event` can reward dropping or
stalling offered traffic unless generated, overrun, accepted, delivered, and
measurement cycles are held equal. A6 accepted only 24,147 of 87,000 offered
events in its frozen run; its link-toggle/event values therefore describe the
accepted serialized stream, not offered-event energy. A7 and A9 have multiple
lanes, so a per-cycle toggle comparison must also hold output utilization and
ready masks fixed.

## 5. What can and cannot be normalized

| Quantity | Comparable now | Not comparable now |
| --- | --- | --- |
| State bits | exact formula/mapped Q within a declared wrapper; policy/transport/output decomposition above | raw totals across different ingress storage, lane count, synthetic age, or codec endpoints |
| Mapped cells | candidate vs its own reference under the same committed flow (A4, A5, A7) | A4 vs A5 vs A7; any of them vs A2/A8 operator proxies or A9 analytics |
| Depth | same cell alphabet and register boundary within one structural study | numeric `ltp` across ABC-simple, NAND, techmap, and RTLIL; analytic depth mixed with mapped depth |
| Toggles/cycle | same stimulus, hierarchy, reset/drain window, and bit set | A2 model vs A3 VCD vs A5 output-control vs A6 pins vs A8 sequential proxy |
| Toggles/event | same accepted/delivered population and toggle boundary | different overrun, lane width, serialization, or output utilization |
| Energy/event | none of the eight tracks has comparable physical evidence | every toggle proxy above; toggles lack capacitance, voltage, glitches, clock and leakage |

The only responsible cross-track statements are qualitative:

1. A8 B8 demonstrates low sequential age-state activity scaling under its own
   proxy, but its frozen rejection and worse local selection proxy remain fixed.
2. A4 and A7 demonstrate within-track combinational scaling advantages at
   selected N/K points, paid for with extra pipeline or lane state.
3. A2, A3, and A5 show that extra policy state/activity did not automatically
   monetize into a physical or workload-wide advantage.
4. A6 and A9 are transport/endpoint architectures; their storage and pins are
   inseparable from their mechanism and cannot be normalized as arbiter policy.

## 6. Missing physical evidence

A cross-track energy ranking requires a new head-owned common experiment. At a
minimum it must freeze:

- one Liberty/PVT/voltage, clock constraint, wire-load or placed/routed flow,
  reset treatment, DFF mapping, and synthesis pass sequence;
- one pin-matched wrapper per lane class, with common source latches either
  included for all or excluded for all;
- explicit accounting for candidate ingress queues, internal transport,
  output lanes, and both codec endpoints rather than subtracting them after the
  result is known;
- identical frozen occurrence traces, ready patterns, reset/drain windows, and
  generated/accepted/delivered denominators;
- hierarchical SAIF/VCD partitions for clock, sequential state,
  combinational internal nodes, payload datapath, and I/O/link pins;
- glitch-aware cell power, clock-tree power, leakage, load capacitance,
  multi-lane output capacitance, codec serializer/deserializer I/O energy, and
  post-route parasitics; and
- delivered-event, accepted-event, offered-event, cycle, lane, and pin
  normalizations reported together so loss, idle lanes, or serialization cannot
  manufacture an efficiency win.

No committed report supplies that complete boundary. Consequently this audit
makes no cross-track power, energy/event, area, or Fmax winner claim and does
not authorize server work.

## 7. Fixed A8 disposition

The prior A8 conclusion is unchanged: B8 remains an interesting state/activity
Pareto point, but the current calendar-wheel RTL is not shortlisted for
advancement or server PPA because quantization tails and local selection
cell/depth proxies fail its fixed gates. Cross-track evidence above neither
reopens nor softens that decision.
