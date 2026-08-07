# A4 final local structural gate and physical-shortlist decision

Status: local generic structural gate complete, 2026-08-07. Frozen-46 RTL
qualification commit `67051fa` and topology/mapping falsification commit
`32cbc0c` are unchanged. No common file, server, SSH/tmux panel, Xcelium,
Genus, or Innovus was used.

## Decision

**Hold flat at N=16. Conditionally shortlist the quadtree at N=64.** N=64 is
the first tested size at which the declared local break-even gate passes; this
is not a blanket approval for every N >= 64. A 256-source tree would add three
sparse pipeline cycles rather than two and must be gated again.

This decision is deliberately stricter than comparing RTL lines or generic
cell count alone. N=16 quadtree already maps to fewer generic cells, but its
state is 40% higher and it adds one cycle; its 25% wire and 55% maximum-fanout
proxy improvements do not justify displacing the qualified flat option for the
frozen-size physical shortlist. At N=64 the logic-depth, fanout, and wire
separation becomes large enough to justify one head-owned physical experiment,
subject to the conditions below.

The local break-even rule was fixed as all of:

- mapped combinational cells at least 30% below flat;
- equal-width full-channel wire proxy at least 40% below flat;
- maximum non-clock/reset sink-pin fanout proxy at least 75% below flat;
- total mapped state premium no more than 60%; and
- sparse tree latency penalty no more than two cycles.

N=16 fails the wire and fanout thresholds. N=64 passes all five, with the state
premium close to the limit. These thresholds are an explicit shortlist policy,
not a claim of silicon PPA.

## Fair structural contract

The candidate-only file
[`a4_structural_compare.sv`](../../rtl/candidates/a4_quadtree_fabric/structural/a4_structural_compare.sv)
contains both tops. They use the same:

- N one-entry ingress slots, each storing valid plus the 16-bit event;
- source width (`clog2(N)`), eight-bit age field, and retire payload width;
- transfer-driven rotating priority rule;
- back-to-back refill behavior and ready/valid registered retire boundary; and
- external ports, reset style, and synthesis/lint options.

The flat top has one N-way rotating node/output slot. The tree top recursively
uses only four-way nodes and retains every level register. At N=16 the flat/tree
pipeline depths are 1/2; at N=64 they are 1/3. Thus the N=16 `+1` latency and
N=64 `+2` latency are included rather than balanced away with free registers.
The eight-bit age is exposed by both structural tops so Yosys cannot delete the
tree's per-hop state. The mapped flip-flop count exactly equals the independent
architectural formula in all four cases.

This reference is not A7-style compaction or A9 token arbitration. It is one
flat rotating selector used only as the candidate-local comparison baseline.
No mechanism from another track is present.

## Identical local tool flow

Tools reused from non-server `/tmp` packages:

- Verilator 5.032;
- Yosys 0.52, git `fee39a3284c90249e1d9684cf6944ffbbcbb8f90`;
- Yosys ABC `simple` generic gate set: AND, OR, XOR, MUX, and automatic NOT.

Every design/N pair uses the same Verilator options:

```text
--lint-only --timing -Wall -Wno-fatal -Wno-DECLFILENAME
```

Every Yosys pair uses the same pass sequence:

```text
read_verilog -sv -DSYNTHESIS
hierarchy -check -top <TOP> -chparam NUM_SOURCES <N>
proc; flatten; opt; memory; opt; techmap; opt
abc -g simple; clean; check; stat; write_json
```

Reproduce all four cases and the CSVs with:

```bash
python3 tests/a4/run_structural_gate.py \
  --yosys /tmp/a7-yosys/usr/bin/yosys \
  --verilator /tmp/a7-sim-bin/verilator \
  --work-dir /tmp/a4-structural-gate \
  --output docs/research/results/a4_local_structural_gate.csv \
  --comparison-output docs/research/results/a4_local_structural_comparison.csv
```

All four Verilator runs have zero warnings after suppressing only the expected
multi-module filename warning. Yosys `check` reports zero problems. This is a
generic technology-independent mapping, not a standard-cell area or timing run.

## Result

`cells` excludes Yosys `$scopeinfo`. State is one mapped generic DFF per bit.
Logic depth is the maximum mapped combinational gate count between a primary or
register boundary and a register/input boundary. Fanout counts sink pins and
excludes clock and asynchronous-reset pins.

| N | design | cells / comb / state | logic depth | max / p95 fanout | pipeline | merge fan-in | links | full wire | longest span |
| ---: | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 16 | quadtree | 2059 / 1632 / 427 | 24 | 115 / 4 | 2 | 4 | 20 | 720 | 2 |
| 16 | flat | 3220 / 2915 / 305 | 43 | 255 / 3 | 1 | 16 | 16 | 960 | 3 |
| 64 | quadtree | 9705 / 7924 / 1781 | 27 | 127 / 4 | 3 | 4 | 84 | 3584 | 4 |
| 64 | flat | 15485 / 14360 / 1125 | 114 | 1074 / 3 | 1 | 64 | 64 | 8192 | 7 |

The p95 fanout is not favorable to the tree, but the maximum global-control
tail is: 255 to 115 at N=16 and 1074 to 127 at N=64. This is why both p95 and
maximum are disclosed instead of summarizing fanout with one average.

Relative tree-versus-flat deltas:

| N | all cells | comb cells | state | depth | max fanout | full wire | links | latency |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | -36.1% | -44.0% | +40.0% | -44.2% | -54.9% | -25.0% | +25.0% | +1 |
| 64 | -37.3% | -44.8% | +58.3% | -76.3% | -88.2% | -56.3% | +31.3% | +2 |

The tree therefore does not win every dimension. It replaces a large flat
combinational/global fanout cone with more state, physical links, and latency.
Its mapped-cell advantage is real in this generic library but cannot substitute
for clock-tree, routing, congestion, or standard-cell timing evidence.

## Wire and internal-link proxy

Both candidates transport the same `{event, source, age}` width plus valid and
ready. Identity/Morton placement is used, so mapping injection span is zero.
The tree proxy sums every source-to-leaf and registered internal edge; flat sums
equal-width source-to-center links. This gives:

- N=16: tree 20 links, span 2, 720 bit-grid; flat 16 links, span 3,
  960 bit-grid;
- N=64: tree 84 links, span 4, 3584 bit-grid; flat 64 links, span 7,
  8192 bit-grid.

Tree link count is higher because payload crosses levels. The full-channel
proxy breaks even before or at N=16 under this equal-width contract, but the
shortlist gate does not pass until N=64 because wire benefit alone does not pay
the register/latency cost. Phase-3 remapping can add injection spans up to 6 at
N=16 and 14 at N=64; such a mapping can erase the local-span premise and must
be re-evaluated rather than inheriting the identity result.

## Padding cost remains a veto

The generic Yosys gate intentionally covers only exact 4^L sizes. Phase-3
complete-tree padding remains part of the shortlist decision:

| live N | padded ports | empty | complete internal state | pruned lower bound | total state including common ingress | flat total state |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 18 | 64 | 46 (71.9%) | 672 | 192 | 978 | 341 |
| 48 | 64 | 16 (25.0%) | 693 | 528 | 1509 | 853 |

N=18 complete padding is 3.5x the pruned internal-state lower bound and 2.87x
flat total state. N=48 is 31.25% above the pruned internal-state bound and
76.9% above flat total state. Both remain rejected by the phase-3 25% padding/
internal-state-premium rule. An N between powers of four may enter the physical
shortlist only with structurally pruned radix-4 subtrees and a fresh identical
gate; empty full-tree nodes are not free.

## Final local shortlist conditions

The N=64 quadtree proceeds only as `CONDITIONAL_SHORTLIST`:

1. head-owned synthesis must preserve the bounded-fan-in/depth advantage after
   real cells, clocking, and constraints;
2. routed full-channel wire/capacitance must include every tree register and
   mapping injection wire;
3. the +2 sparse latency and +58.3% state must fit the system budget;
4. an implemented N=64 RTL must separately pass conservation, bounded progress,
   padding silence if applicable, and workload qualification; and
5. any non-identity placement must rerun the phase-3 mapping bracket.

N=16 remains `HOLD_FLAT` for the physical shortlist. N=64 is the first measured
adoption-worthy size, but not yet a silicon winner. Server gates remain
`PENDING_HEAD_GENUS` and `PENDING_HEAD_INNOVUS`; no server PPA is claimed.

## Evidence

- [raw four-case structural CSV](results/a4_local_structural_gate.csv)
- [tree-versus-flat delta and decision CSV](results/a4_local_structural_comparison.csv)
- [reproduction and netlist-analysis script](../../tests/a4/run_structural_gate.py)
- [candidate-only structural pair](../../rtl/candidates/a4_quadtree_fabric/structural/a4_structural_compare.sv)
- [phase-3 mapping/padding report](a4_quadtree_mapping_sensitivity.md)
