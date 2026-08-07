# Wave-1 physical experiment matrix (read-only preparation)

Date: 2026-08-07
Status: preparation only; no Xcelium, Genus, Innovus, server, SSH, or other
worktree write was performed.

## Evidence boundary and verdict partition

This matrix uses only files reachable from each track's committed `HEAD` as
observed on 2026-08-07. It deliberately ignores working-tree modifications and
untracked physical-handoff files. The fixed A5 rejection and the committed
capacity/latency audit at A5 `991f164` are not changed or reinterpreted.

| Track point | Committed evidence used | Wave-1 class | Reason and allowed scope |
| --- | --- | --- | --- |
| A4 N16 | A4 `5f07aee` | `REJECTED / HOLD_FLAT` | The committed structural verdict fails the local wire/fanout gate. It does not enter wave 1. |
| A5 speculative pre-grant | A5 `66c76c3` and earlier fixed result commits | `REJECTED` | The rejection is fixed; no physical experiment is reopened. |
| A7 N16/K2 prefix | A7 `2219040` | `REJECTED` | Equal-state generic mapping loses area break-even and does not improve depth. |
| A9 N16 static/H2, always-ready | A9 `e571e67` | `REJECTED` | Same-lane centralized arbitration is smaller and had lower functional latency. |
| A7 original prefix N16/K4 | A7 `3a115c0`, `2219040` | `ELIGIBLE_FOR_SCREENING` | It is the first committed equal-state structural win. Eligibility means paired physical screening only, not adoption. |
| A4 N64 quadtree | A4 `5f07aee` | `CONDITIONAL_SHORTLIST` | It may test the timing/wire hypothesis only after an N64 correctness gate; the committed N64 evidence is structural, not qualified candidate RTL. |
| A9 N64/L8 static | A9 `e571e67` | `CONDITIONAL_DIAGNOSTIC` | It is retained only to test whether the centralized depth/global-placement path misses frequency enough to pay roughly 2.3x cells, 3.0x core state, and pipeline latency. It is not an unconditional shortlist. |
| A2 phase 3 | A2 `0cf40b8` | `PENDING_UNCOMMITTED_IMPLEMENTATION` | HEAD contains the frozen gate, but not the phase-3 implementation/wrapper/filelist/results. No dirty or untracked filename or result is inferred. |
| A7 radix-4 rescue | A7 `2859ed7` | `PENDING_COMMITTED_VERDICT` | Rescue RTL and the preregistered rule are committed, but no committed outcome applies that rule. The modified working-tree comparison is excluded. The original N16/K4 row remains the only eligible A7 point. |

“Eligible” therefore means eligible to spend wave-1 screening effort after the
mandatory Xcelium gate. “Conditional” means diagnostic evidence only and must
not be reported as a candidate win. `PENDING` points cannot enter the matrix
until a later committed verdict supplies the missing evidence.

## Frozen experiment pairs

The file column records the committed compile recipe, not a newly created
filelist. Where no dedicated synthesis `.f` exists at HEAD, that absence is an
explicit preparation blocker and must not be repaired by reading an untracked
handoff directory.

| Point / class | Sources / lanes | Physical top and equal-contract reference | Committed file recipe | Capability boundary | Same-frequency comparison condition | Mandatory Xcelium gate before Genus |
| --- | --- | --- | --- | --- | --- | --- |
| **A4 N64 / `CONDITIONAL_SHORTLIST`** | 64 / 1 | Proxy pair `a4_struct_quadtree_top` and `a4_struct_flat_top`, each with `NUM_SOURCES=64`. A candidate-qualified N64 top is `PENDING`. | Structural proxy is the single committed `rtl/candidates/a4_quadtree_fabric/structural/a4_structural_compare.sv`; there is no committed N64 physical filelist. The N16 candidate `.f` must not be presented as N64 qualification. | One ingress entry/source, one registered retire lane, backpressure, source/event/age observability. The committed capability profile is fixed at N16; N64 conservation, bounded progress, and workload capability remain unqualified. | Compare tree and flat with identical source/event/age widths, registered ready/valid boundaries, reset, libraries/corners, SDC, target period, utilization, placement region, pin constraints, and routed full-channel wires. Retain the real 1-stage flat versus 3-stage tree latency; do not add free balancing registers. | **Blocked at HEAD.** Commit and run a candidate-owned N64 Xcelium test proving accepted=delivered after drain, no loss/duplicate/phantom/corruption, source-local order, bounded progress, stable output under stall, and padding silence if applicable. Non-identity placement also requires the committed mapping bracket. No Genus launch before all pass. |
| **A7 original prefix N16/K4 / `ELIGIBLE_FOR_SCREENING`** | 16 / 4 | Pair `a7_prefix_structural_top` and `a7_replicated_structural_top`, `N=16`, `K=4`; architectural candidate top is `a7_parallel_event_compactor`. | The committed manifest names prefix-count, compactor, and replicated-reference RTL; the equal-boundary wrapper is `tests/a7_parallel_event_compactor/a7_structural_wrappers.sv`. The committed TB filelist is `tb/filelists/a7_parallel_event_compactor.f`; no dedicated Genus `.f` is committed, so the exact synthesis recipe must be frozen before launch. | One outstanding event/source; four independently ready registered retire lanes; correctness, latency, fairness, and backpressure observable. Frozen 46-trace always-ready qualification is committed at K4; independent-lane stall is candidate-unit evidence. | Prefix and replicated K4 must use the same N/AW/SW/K, ingress and four-lane output registers, pin widths, ready pattern, libraries/corners, SDC, period, utilization, floorplan/pins, and route settings. Compare only matched closed periods; never compare one design's maximum frequency with the other's nominal-period area. | Re-run under Xcelium: exhaustive 65,536 N16 request bitmaps, persistent contention/uniqueness/at-most-K/fair service, same-edge refill, independent lane stall with stable output and no duplicate inflight source, plus the frozen 46 traces for prefix and same-K reference. Require zero assertions and identical accepted/delivered event sequences and metrics. |
| **A9 static N64/L8 / `CONDITIONAL_DIAGNOSTIC`** | 64 / 8 | `a9_phase4_synth_top`, `NUM_SOURCES=64`, `RETIRE_LANES=8`; select static by default and centralized reference with `A9_PHASE4_CENTRAL`. H2 is outside this diagnostic. | Committed `rtl/candidates/a9_distributed_token_fabric/a9.f` plus `a9_phase4_synth_top.sv`. The `.f` alone omits the synth shell, so both inputs must be frozen together; untracked `physical/` files are forbidden. | Static fixed source-to-stripe assignment, one source ingress entry, distributed transport slots, eight retire lanes. The committed N64 evidence is generic structural scaling; common 46-trace capability was N16 and cannot qualify N64. | Static and central must retain identical registered source/retire boundaries, N64/L8 widths and pins, fixed source mapping, ready conditions, libraries/corners, SDC, period, utilization, floorplan/pins, and route effort. A9 has utility only at a period where central fails the frequency gate and static closes it; area/state and added latency remain charged. | **Blocked at HEAD for N64 functional qualification.** A candidate-owned Xcelium N64/L8 static-versus-central run must prove conservation/drain, no corruption/duplicate/phantom, source order, lane uniqueness, stable stalled outputs, and identical occurrence semantics. Always-ready is the diagnostic contract. Do not substitute the N16 common suite or H2's asymmetric-stall result. |

The A7 TB filelist includes rescue sources because that is the committed HEAD,
but wave 1 must elaborate the original prefix top only. Until a committed
rescue verdict exists, no rescue macro/top/result may replace either side of
the A7 pair.

## Execution order

1. **Freeze provenance locally.** Record the candidate/reference commit IDs,
   exact source hashes, top parameters, macro set, libraries/corners, SDC,
   target-period grid, floorplan/pin constraints, tool versions, and random
   seeds. Abort if any input comes from a dirty/untracked path or a common
   benchmark/TB/trace/golden modification.
2. **Run the mandatory Xcelium pair gate.** Compile both sides from the frozen
   recipes and execute the row-specific checks above. Archive command, log,
   seed, assertion count, accepted/delivered counts, and event-sequence digest.
   A compile/elaboration warning that changes width, parameterization, or
   synthesizable semantics is a failure, not a waiver.
3. **Genus screening, paired and period-matched.** For each admitted row, read
   the same library/corner and boundary constraints, elaborate the declared
   candidate and reference, and synthesize both at the same screening period.
   Report achieved slack/frequency, sequential and combinational area, cell
   count, register bits, unconstrained paths, max fanout, and tool warnings.
   Screening establishes whether a physical hypothesis deserves routing; it
   is not a routed PPA result.
4. **Select a head-approved period bracket.** Keep only periods for which a
   pairwise question remains: A4 bounded-depth/wire benefit, A7 equal-frequency
   area/timing break-even, or A9 a central timing failure that static A9 can
   close. Do not interpolate an unmatched point and do not use a single
   synthesis netlist for multiple periods.
5. **Resynthesize separately at every period.** For each retained period `T`,
   run fresh Genus optimization for candidate at `T` and reference at `T`.
   Preserve the same hierarchy policy, clock uncertainty, IO delays, loads,
   dont-use set, and boundary registers. Only those two period-specific
   netlists and constraints may enter Innovus.
6. **Innovus in matched pairs.** At each `T`, run import/pre-place checks,
   placement/optimization, CTS, post-CTS optimization, routing, extraction,
   and post-route STA for both sides with identical technology, RC corner,
   utilization target, placement region, pin policy, CTS targets, routing
   layers, and effort. Report post-route slack, area, cell/buffer count, wire
   length/capacitance, congestion, clock tree, and activity-qualified power
   only when both paired runs are valid.
7. **Decide from a matched period row.** Preserve functional/pipeline latency
   charges alongside PPA. A conditional diagnostic cannot be promoted merely
   because it routes; it must satisfy its committed hypothesis against its
   equal-contract reference.

## Stop and exclusion criteria

Stop before Genus for any missing committed top/file recipe, failed Xcelium
check, parameter/width mismatch, unqualified source count, dirty input, or
common-file dependency. This currently blocks A4 N64 candidate qualification
and A9 N64 functional qualification; it also excludes A2 phase 3 and the A7
rescue verdict.

Stop a Genus point if either side has unconstrained functional paths, inferred
latches/memories not shared by contract, boundary-register removal, parameter
mis-elaboration, or non-comparable clock/IO constraints. Stop the track after
screening when its committed hypothesis is already falsified: A4 loses the
bounded-fan-in/depth premise or cannot accept its +2 latency/+58.3% state
budget; A7 prefix K4 has neither equal-frequency area nor timing benefit over
the replicated K4 reference; A9 central meets the target frequency, or static
A9 fails it, because the diagnostic's only justification then disappears.

At each Innovus period, stop the pair if either fresh period-specific netlist
fails import, floorplan/pin equivalence, CTS, route legality, extraction, or
post-route timing validation. Do not publish a ratio from an unpaired point.
Terminate the period sweep after the head-approved bracket is resolved or both
designs fail the next tighter period; do not increase effort asymmetrically to
manufacture a crossover. Post-route promotion requires correctness to remain
independent of physical timing assumptions and requires all latency, lane,
state, and toggle costs to remain visible.

No command in this document authorizes server execution. Xcelium, Genus, and
Innovus remain head-owned gates.
