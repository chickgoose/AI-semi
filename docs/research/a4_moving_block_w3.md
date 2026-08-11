# A4 W3 Moving-Block Elastic Reservation Tree

Status: implementation contract, local-only qualification

## 1. Scope

This candidate is a binary merge transport whose stored events move through
explicit link blocks.  It is not the earlier A4 quadtree and it is not an
arbitrary fall-through FIFO.  Downstream clearance is returned as a bounded
movement authority.  The combinational skip bound is frozen before RTL work:

- `MAX_ADVANCE = 2` tree edges per clock;
- one registered block at every node of a complete binary tree;
- one registered output (the root block);
- one event may be accepted from a source at most once per clock; and
- no event inserted during a clock may move more than `MAX_ADVANCE` edges.

The parameterized RTL accepts `MAX_ADVANCE=1` for the fixed one-step reference
and `MAX_ADVANCE=2` for the W3 candidate.  Values above two are rejected.  This
keeps the longest clearance/selection chain independent of `log2(N)` pipeline
depth even when `N` grows.

## 2. Exact cycle model

Tree nodes use heap indices: root 0, children of node `p` are `2p+1` and
`2p+2`, and source `s` enters leaf `N-1+s`.  Every node contains one
`{valid,event,source}` block.  Every internal node contains one branch phase.

For clock `t`, outputs and next state are computed from state `Q[t]`:

1. The root item is the only visible retire item.  If root valid and
   `retire_ready`, consume it and clear the root in the tentative state.
2. Repeat exactly `MAX_ADVANCE` microsteps:
   1. Inject each valid source into its empty leaf unless that source was
      already accepted in an earlier microstep of this clock.  Acceptance sets
      `source_ready[s]` for the whole clock.
   2. Visit internal nodes in root-to-leaf heap order.  An empty parent may
      reserve one valid child.  If both children are valid, the node phase
      chooses; a grant rotates the phase to the other child.
   3. Move the selected child item into the parent and clear the child.
3. Commit the tentative nodes and phases at the active edge.

Root-to-leaf visitation is essential: an item moved to its parent cannot be
examined again in the same microstep because that parent was already visited.
Therefore each microstep grants exactly one edge of authority and the cycle
bound is exact.  A vacancy can move downward by at most two edges, and an item
can move upward by at most two edges.

If the root retires, step 2 may refill it before the same active edge commits:
this is same-cycle retire/refill.  A newly refilled root is not also retired in
that cycle.  If the root is stalled, it remains unchanged and clearance cannot
cross it.

## 3. Safety and progress obligations

- Conservation: committed items plus the retired item equal prior items plus
  accepted source items as a multiset.
- Unique acceptance: `source_ready[s]` implies `source_valid[s]`, and a source
  can inject at most once per clock even if its leaf clears during both
  microsteps.
- Stall stability: root payload and source are stable for every continuous
  `retire_valid && !retire_ready` interval.
- Source-local ordering: one outstanding source event plus unique acceptance
  prevents a later event from entering before its predecessor.
- Merge exclusion: an empty parent receives at most one child in a microstep.
- Bounded movement: an item crosses at most two registered edges per clock.
- Bounded progress: with `retire_ready=1` continuously and finite injection,
  every accepted event retires.  Persistent contention is bounded by the
  rotating two-child phase at each merge.

## 4. Fixed one-step reference

The reference has identical node slots, ingress boundary, output boundary and
branch phase state, but executes one microstep per clock.  Hence both variants
have identical architectural register count.  For a binary tree:

- stored blocks: `2N-1`;
- branch phase bits: `N-1`;
- payload/register proxy: `(2N-1)*(ADDR_WIDTH+SOURCE_WIDTH+1)+(N-1)` bits;
- one-step control-touch proxy: `2*(N-1)` child checks per clock;
- moving-block control-touch proxy: `2*MAX_ADVANCE*(N-1)` child checks;
- longest skip/merge depth: one versus two local merge decisions.

Moving-block is worthwhile only if the measured latency and shock recovery
benefit justify the doubled local control-touch proxy.  It does not claim a
peak rate above the single output's one event/cycle.

## 5. Executed failure counterexamples

Candidate-owned regression fixtures retain these falsifiers and execute a
corresponding mutated model.  A fixture passes only when its named detector
fires; checking fixture names is not qualification.

1. `retire_refill_clear_after_write`: the mutated clear deletes the root
   replacement and triggers `conservation`;
2. `two_microstep_double_inject`: repeated injection duplicates an accepted
   event and triggers `duplicate`;
3. `stalled_root_clearance_leak`: clearance overwrites a stalled root and
   triggers `stall stability`;
4. `dual_child_single_parent`: committing two contenders to one parent loses
   one and triggers `conservation`;
5. `no_reset_shock_recovery`: a shock-induced frozen state triggers `deadlock`.

## 6. Qualification gate

The RTL is locally eligible only when the Python cycle model, executable
mutation falsifiers, adversarial conservation tests, Verilator lint, and
cycle-by-cycle SV lockstep all pass for the frozen skip bound.  `run.sh`
resolves Verilator in strict order: `AER_VERILATOR`, `VERILATOR`, then `PATH`.
An absent, non-executable, or non-Verilator command terminates with exit 2; a
silent skip is forbidden.

This remains **HOLD for common qualification and PPA**.  The generator-v4
replay below neither binds the frozen common scoreboard nor supplies timing,
area, power, or routed evidence.

## 7. Local W3 results

The candidate-owned regression executed nine tests, including 1,200 cycles of
random branch contention, a 64-cycle continuous root stall, repeated B16/global
fan-in, overload-to-sparse recovery without reset, and 760-cycle Verilator
lockstep runs for both `MAX_ADVANCE=1` and `2`.  Every accepted event retired
exactly once in source order; both models drained without deadlock.  Verilator
`-Wall` lint produced no warnings.  All five intentionally broken models were
also rejected by their frozen detector.

Measured Python cycle-model comparisons use identical occurrence streams and
sink patterns.  Latency is occurrence-to-retire; throughput includes fill and
drain.  Overloaded rows can accept different event sets, so their latency means
are descriptive, not survivor-based rankings.

| workload | fixed accepted/overrun | moving accepted/overrun | fixed/moving throughput | fixed/moving mean e2e | fixed/moving p99 e2e | fixed/moving bubbles |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| isolated sparse | 40 / 0 | 40 / 0 | 0.084567 / 0.084926 | 5.000 / 3.000 | 5 / 3 | 160 / 80 |
| B16 | 512 / 0 | 512 / 0 | 0.992248 / 0.996109 | 12.500 / 10.500 | 20 / 18 | 4 / 2 |
| branch merge | 369 / 111 | 370 / 110 | 0.745455 / 0.747475 | 17.415 / 17.278 | 19 / 19 | 3 / 2 |
| global fan-in | 418 / 350 | 421 / 347 | 0.990521 / 0.995272 | 39.720 / 40.230 | 46 / 47 | 4 / 2 |
| shock/recovery, no reset | 232 / 2163 | 235 / 2160 | 0.359133 / 0.364341 | 65.422 / 64.945 | 87 / 88 | 47 / 22 |

The moving authority removes two fill cycles for N=16 and halves observed
ready-but-empty cycles in these tests.  It does **not** uniformly improve tail
latency: global fan-in and shock p99 are one cycle worse because it admits three
additional events and changes branch-phase timing.  RTL results take precedence
over the initial hypothesis; the candidate is a latency/fill and recovery
tradeoff, not a universal fairness improvement.

| N | variant | node slots | register bits | child-control checks | max local branch fanout proxy | max skip depth |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 16 | fixed | 31 | 1162 | 30 | 2 | 1 |
| 16 | moving | 31 | 1162 | 60 | 4 | 2 |
| 64 | fixed | 127 | 5016 | 126 | 2 | 1 |
| 64 | moving | 127 | 5016 | 252 | 4 | 2 |

Both variants have identical stored-event and phase registers.  Moving-block
doubles the unrolled local control checks and fanout proxy while keeping the
predeclared combinational chain at two merge decisions for both N=16 and N=64.
Physical adoption therefore remains conditional on two-level logic meeting the
target period; no server PPA claim is made.

## 8. Frozen generator-v4 replay

The candidate-owned replay tool reads (and never edits) the A1 generator-v4
contract.  Before generation it verifies the exact generator, official-policy,
and suite-manifest SHA-256 values; after generation it verifies version 4.0,
the exact 50/22 run names and ordering, every official trace SHA, 4x4 geometry,
always-ready sink metadata, and address-only identity semantics.  The recorded
common HEAD was `47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be`, with an empty tracked
status.  Full provenance and exact aggregate values are frozen in
`results/generator_v4_replay_summary.json`.

| suite | model | accepted / overrun | throughput | bubbles | mean e2e | p95 / p99 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| full50 | fixed one-step | 83,514 / 22,902 | 0.729214 | 19,465 | 14.943 | 44 / 46 |
| full50 | moving two-step | 83,555 / 22,861 | 0.729999 | 14,211 | 14.073 | 45 / 47 |
| cap22 | fixed one-step | 42,948 / 22,668 | 0.789979 | 5,919 | 22.742 | 45 / 46 |
| cap22 | moving two-step | 42,983 / 22,633 | 0.790856 | 3,126 | 22.586 | 46 / 47 |

The moving model accepts 41 more full50 events and 35 more cap22 events and
reduces bubbles, but aggregate latency distributions contain different
survivor sets.  Their means and tails are descriptive and are not qualified
latency rankings; in particular, p95/p99 are one cycle worse for moving-block.

Four representative frozen full50 traces are converted into normalized local
vectors and replayed cycle-by-cycle against the parameterized RTL: simultaneous
fanin (259 cycles), `shape_b16` (4,099), global fanin (2,051), and no-reset
`mixed_phase_always_ready_identity` (4,117).  All four pass.  This harness uses
the same trace occurrences and address-only event identity, but it is
candidate-owned and does not claim frozen common-TB qualification.

Reproduction:

```bash
AER_VERILATOR=/absolute/path/to/verilator \
  bash rtl/candidates/a4_moving_block_tree/tests/run.sh
python3 rtl/candidates/a4_moving_block_tree/compare_fixed.py
tmp_base=$(mktemp -d /tmp/a4-w3-replay.XXXXXX)
python3 rtl/candidates/a4_moving_block_tree/replay_generator_v4.py \
  --common-root /home/chickgoose/projects/a1 --suite all \
  --generated-root "$tmp_base/generated" --output "$tmp_base/report.json" \
  --rtl-vectors-dir "$tmp_base/vectors"
```
