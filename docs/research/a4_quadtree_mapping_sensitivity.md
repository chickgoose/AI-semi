# A4 quadtree topology and static-mapping falsification

Status: candidate-only model/test complete, 2026-08-07. The frozen 46-trace RTL
result at commit `67051fa` is unchanged. No common workload, TB, runner, golden,
RTL, SSH/tmux panel, or server file was modified or used.

## Question and boundary

This study tries to falsify the claim that a spatial quadtree is robust to
placement. It keeps the exact A4 mechanism: four-child transfer-driven RR, one
elastic event slot per internal node, one output event/cycle, and a complete
radix-4 hierarchy. Only the static logical-source-to-tree-port permutation
changes. There is no compactor, token/ring, prediction, compression, urgency,
calendar, reservoir, or runtime mode switch.

The generic model was first checked against the original fixed N=16 model.
Identity mapping produced identical accepted, overrun, event p99, and maximum
request-wait values. Unit tests also preserve a same-cycle 16-way occurrence,
drain N=18 through 46 empty padded ports without phantom events, validate every
mapping as a one-to-one assignment, and bound every link to at most one
transfer/cycle. This is model evidence, not a new N=64 RTL qualification.

## Candidate-only experiment

The deterministic manifest uses 2,048 stimulus cycles and one source-side
pending slot, matching the A4 clean contract:

- `quadrant_boundary_move`: a two-source hotspot sweeps back and forth across
  the central quadrant boundary in 32-cycle steps; pair load is approximately
  1.56 events/cycle.
- `single_quadrant_overload`: paired traffic remains inside the upper-left
  physical quadrant at approximately 1.64 events/cycle.
- `all_quadrants_equal`: the same cycle injects one rotating source in each of
  four quadrants at approximately 1.62 events/cycle.
- `padded_uniform`: approximately 1.25 events/cycle over N=18 and N=48 in a
  64-port complete tree, exposing empty-port placement and padding cost.

Each exact trace is run under identity, numeric interleaved, address-bit
reversed, traffic-ranked spread (`placement_best`), traffic-ranked pack
(`placement_worst`), and 32 seeded static shuffle mappings. The placement names
describe constructive heuristics, not oracle optima. The published bracket is
the observed per-metric minimum/maximum over all 37 mappings and is not claimed
as a proof over all `N!` permutations.

The mapping definitions are fixed and reproducible. Identity maps each physical
`(x,y)` to its Morton/quadtree port. Interleaved maps row-major numeric source
`i` directly to tree port `i`, which interleaves its physical coordinates among
Morton branches. Bit reversed reverses all padded-port address bits before port
assignment. Spread ranks sources by trace offer count and assigns them in
base-4 digit-reversed port order (root child first); pack assigns the same rank
to consecutive ports. All remain static for the whole trace.

`pair p99` is the p99 time from a tagged simultaneous group occurrence until
its last member retires, computed only for groups whose members all survive the
one-entry source admission rule. `pair_completion_ratio` is therefore reported
beside it to expose survivor bias. Link utilization counts transfers through
each physical child-to-node link over stimulus plus finite drain: level 0 is
source-to-leaf, level 1 is leaf-to-parent, and level 2 exists at N=64/padded
depth three. Both level mean and hottest-link values are in the full CSV.

Wire cost is an auditable pre-layout estimate. A logical source starts at its
row-major physical coordinate; its assigned Morton tree port has another
coordinate. `mapping_wire_span` is their Manhattan distance. Internal-tree
longest spans remain 2 at N=16 and 4 at N=64, and arbitration fan-in/ready
fanout remain exactly four for every mapping. Thus mapping can change injection wire span and
congestion, but cannot claim a fanout or merge-depth improvement.

Reproduce locally:

```bash
PYTHONPATH=tests/a4 python3 -m unittest -v tests/a4/test_topology_mapping.py

python3 tests/a4/run_topology_mapping_study.py \
  --output docs/research/results/a4_topology_mapping_sweep.csv \
  --named-output docs/research/results/a4_topology_named_mappings.csv \
  --bracket-output docs/research/results/a4_topology_mapping_bracket.csv \
  --trace-manifest-output docs/research/results/a4_topology_trace_manifest.csv \
  --trace-dir /tmp/a4-topology-traces
```

## Named mappings: N=16

`L0 max` is the hottest source-to-leaf link. Wire is maximum/total added
source-to-assigned-port Manhattan span.

| Workload | mapping | overrun | pair p99 | max wait | pair complete | L0 max | wire max/total |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| boundary move | identity | 1172 | 7 | 4 | 0.2885 | 0.4080 | 0 / 0 |
| boundary move | interleaved | 1172 | 6 | 3 | 0.2778 | 0.4939 | 3 / 24 |
| boundary move | bit reversed | 1183 | 7 | 4 | 0.2822 | 0.3281 | 3 / 24 |
| boundary move | spread | 1183 | 7 | 4 | 0.2866 | 0.3208 | 6 / 40 |
| boundary move | pack | 1223 | 5 | 3 | 0.2484 | 0.7096 | 6 / 42 |
| single quadrant | identity | 1255 | 5 | 3 | 0.4192 | 0.9922 | 0 / 0 |
| single quadrant | interleaved | 1241 | 6 | 3 | 0.2461 | 0.4995 | 3 / 24 |
| single quadrant | bit reversed | 1244 | 6 | 3 | 0.4210 | 0.4985 | 3 / 24 |
| single quadrant | spread | 1238 | 8 | 3 | 0.4392 | 0.2505 | 4 / 24 |
| single quadrant | pack | 1255 | 5 | 3 | 0.4192 | 0.9922 | 3 / 12 |
| all quadrants | identity | 1260 | 20 | 15 | 0.3966 | 0.2484 | 0 / 0 |
| all quadrants | interleaved | 1263 | 20 | 15 | 0.4063 | 0.2501 | 3 / 24 |
| all quadrants | bit reversed | 1257 | 20 | 15 | 0.4051 | 0.2518 | 3 / 24 |
| all quadrants | spread | 1259 | 20 | 15 | 0.4075 | 0.2485 | 3 / 20 |
| all quadrants | pack | 1266 | 20 | 15 | 0.2975 | 0.2546 | 4 / 32 |

Single-quadrant spreading cuts hottest leaf utilization from 0.9922 to 0.2505
and overrun by 17, but pair p99 rises from 5 to 8 and adds physical wire. With
all quadrants already balanced, mapping changes overrun by only nine among the
named cases and does not change pair p99 or max wait. The hierarchy has no
mapping-created throughput above its one-event/cycle root.

## Named mappings: N=64

| Workload | mapping | overrun | pair p99 | max wait | pair complete | L0/L1/L2 max | wire max/total |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |
| boundary move | identity | 1142 | 9 | 5 | 0.3069 | .3223/.4075/.9922 | 0 / 0 |
| boundary move | interleaved | 1142 | 8 | 3 | 0.3013 | .4104/.4961/.9922 | 6 / 192 |
| boundary move | bit reversed | 1142 | 10 | 5 | 0.3239 | .1636/.3247/.9922 | 11 / 296 |
| boundary move | spread | 1142 | 9 | 3 | 0.3270 | .1641/.2498/.9922 | 10 / 270 |
| boundary move | pack | 1245 | 5 | 3 | 0.2346 | .6179/.9430/.9430 | 8 / 224 |
| single quadrant | identity | 1322 | 21 | 15 | 0.4814 | .2506/.9985/.9985 | 0 / 0 |
| single quadrant | interleaved | 1321 | 21 | 14 | 0.4938 | .2515/.5000/.9985 | 6 / 192 |
| single quadrant | bit reversed | 1319 | 27 | 14 | 0.5027 | .1254/.2502/.9985 | 11 / 296 |
| single quadrant | spread | 1307 | 36 | 15 | 0.5139 | .0626/.2498/.9986 | 9 / 264 |
| single quadrant | pack | 1322 | 21 | 15 | 0.4666 | .2506/.9985/.9985 | 6 / 160 |
| all quadrants | identity | 1273 | 84 | 63 | 0.3929 | .0630/.2498/.9967 | 0 / 0 |
| all quadrants | interleaved | 1273 | 84 | 63 | 0.3655 | .0635/.2507/.9967 | 6 / 192 |
| all quadrants | bit reversed | 1270 | 84 | 63 | 0.3845 | .0629/.2499/.9967 | 11 / 296 |
| all quadrants | spread | 1276 | 83 | 63 | 0.3738 | .0631/.2496/.9967 | 9 / 226 |
| all quadrants | pack | 1276 | 83 | 63 | 0.3286 | .0660/.2539/.9967 | 11 / 332 |

The boundary mover is the strongest mapping counterexample. Four reasonable
maps have exactly 1142 overruns despite very different lower-link balance; a
packed map reaches 1245 because it destroys admission parallelism. Spreading a
single-quadrant overload improves overrun only 15 (1.1%) while pair p99 grows
21 to 36 and total injection span grows 0 to 264. In the already balanced case,
the root is approximately 99.7% utilized for every mapping, so rewiring cannot
materially help.

## Observed static-mapping bracket

The independent per-metric ranges below include the named maps and 32 seeded
static shuffles. Minima and maxima need not come from the same mapping; exact
mapping names for every endpoint are in the bracket CSV.

| N/workload | overrun min--max | pair p99 min--max | max-wait min--max | hottest L0 min--max | mapping wire max min--max |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16 boundary move | 1172--1227 | 5--7 | 3--4 | .3208--.7096 | 0--6 |
| 16 single quadrant | 1238--1255 | 5--9 | 3--5 | .2505--.9922 | 0--6 |
| 16 all quadrants | 1257--1268 | 20--20 | 15--15 | .2476--.2581 | 0--6 |
| 64 boundary move | 1142--1245 | 5--10 | 3--5 | .1636--.6179 | 0--14 |
| 64 single quadrant | 1307--1322 | 21--65 | 14--47 | .0626--.2515 | 0--13 |
| 64 all quadrants | 1270--1281 | 83--84 | 63--63 | .0627--.0660 | 0--14 |

These brackets reject any claim of permutation neutrality at the transport
level. Local RR remains address-independent, but the spatial partition is not.
Lower-link balance is also not a sufficient objective: it can improve while
root utilization, pair completion, and tail latency remain unchanged or get
worse.

## Non-power-of-four padding

| N | padded ports / empty | levels/nodes/state | mapping | overrun | pair p99 | max wait | L0/L1/L2 max | wire max/total |
| ---: | ---: | --- | --- | ---: | ---: | ---: | --- | ---: |
| 18 | 64 / 46 (71.9%) | 3/21/672 b | identity | 567 | 25 | 23 | .2149/.8527/.9956 | 6 / 42 |
| 18 | 64 / 46 (71.9%) | 3/21/672 b | spread | 555 | 48 | 31 | .0829/.2559/.9985 | 12 / 91 |
| 48 | 64 / 16 (25.0%) | 3/21/693 b | identity | 444 | 60 | 47 | .0906/.3349/.9966 | 10 / 156 |
| 48 | 64 / 16 (25.0%) | 3/21/693 b | spread | 435 | 64 | 47 | .0656/.2505/.9966 | 11 / 266 |

Naively padding N=18 to 64 consumes 21 nodes/672 state bits; the full 4-ary
tree lower bound for 18 live leaves is six nodes/192 bits (allowing one dummy
leaf), so complete padding is 3.5x the state lower bound. Spreading active ports
prevents one upper subtree from reaching 85.3% link utilization, but doubles
pair p99 and more than doubles mapping wire total. N=48 padding is less severe:
21 nodes/693 bits versus a 16-node/528-bit full-tree lower bound, a 31.25% state
premium. Empty ports never generate, consume, or reorder an event.

## Placement-aware rule

1. Default to coordinate/Morton identity when offered load is below saturation
   or quadrant load is already balanced. It has zero injection-remap wire and
   the study finds no dependable throughput reason to replace it.
2. At placement time only, estimate offered rate per radix-4 subtree. If one
   child is persistently hotter, distribute heavy sources round-robin over the
   highest differing base-4 digit first, then over lower digits. This is the
   spread heuristic; it does not alter RR or add runtime state.
3. Preserve communicating/simultaneous pairs in nearby leaves unless admission
   loss is the primary objective. A spread is accepted only if measured overrun
   reduction outweighs both pair-p99 regression and added wire span on the
   target workload set.
4. For non-power-of-four N, distribute live leaves across root children but
   prune empty subtrees structurally. Do not instantiate a complete next-power
   tree when padding fraction or register premium violates the conditions below.
5. Publish identity, interleaved, bit-reversed, chosen placement, and at least
   one adverse pack/shuffle result together. Reporting only the favorable
   spatial placement is invalid.

## Rejection conditions

A placement or scaled A4 instance is rejected if any of the following holds:

- conservation, same-source order, finite drain, or empty-padding silence fails;
- any merge exceeds fan-in/fanout four, or mapping introduces runtime arbitration
  state or another track's mechanism;
- the chosen map improves overrun but worsens pair p99 or max wait by more than
  the explicitly accepted system budget; absent such a budget, any regression
  over 25% is a reject rather than an implicit trade;
- maximum mapping injection span exceeds the identity tree's longest internal
  span without a measured admission benefit on all mandatory placement classes;
- complete-tree padding exceeds 25% empty ports or 25% state over the pruned
  radix-4 lower bound; N=18 complete padding is therefore rejected, and N=48 is
  also just beyond the state threshold;
- the selected map is tuned to one quadrant/hotspot but is not bracketed on
  boundary-moving and all-quadrant-balanced traffic; or
- later head-owned physical evidence fails to convert bounded local fanout into
  useful timing/wire benefit after the remap wires and registers are included.

Under these conditions the current fixed N=16 identity RTL remains eligible,
but a generic "quadtree is mapping-neutral" or "spread always helps" claim is
rejected. N=64 and padded-N remain analytical candidates until separately
implemented and qualified in RTL.

## Evidence

- [all 296 mapping/run rows](results/a4_topology_mapping_sweep.csv)
- [40 named mapping/run rows](results/a4_topology_named_mappings.csv)
- [per-metric observed brackets](results/a4_topology_mapping_bracket.csv)
- [candidate trace identities and SHA-256](results/a4_topology_trace_manifest.csv)
- [candidate manifest](../../tests/a4/topology_mapping_manifest.json)
- [generic model](../../tests/a4/topology_mapping_model.py)
- [model/unit tests](../../tests/a4/test_topology_mapping.py)
