# Exact-Scalar-Prefix-K2 local evidence

Generated from the committed candidate-local qualification flow on 2026-08-13.

## Outcome

| Gate | Result |
| --- | --- |
| Independent Python scalar fold | PASS |
| Directed RTL semantics | PASS |
| Directed cycle lockstep | PASS, 18 vectors |
| Frozen-v4 full50 | PASS, 50 runs |
| Frozen-v4 capacity22 | PASS, 22 runs |
| Combined frozen RTL/oracle lockstep | PASS, 171,641 cycles |
| Persistent opportunity probe | PASS, `[20,100,100,20]` over 120 bundles |
| Stale/duplicate/state-advance mutations | 3/3 expected failures caught |
| Generic Yosys synthesis | PASS |

## Frozen address-grant replay

These numbers use exact ordered **addresses**, not the earlier analytical
row-bitmap interpretation.  Thus they supersede the second-round bitmap-model
number for this implemented boundary.

| Suite | generated | accepted | overrun | fixed-window throughput | mean / p99 / max latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| full50 | 106,416 | 99,535 | 6,881 | 0.857944 | 2.416 / 8 / 271 |
| capacity22 | 65,616 | 61,029 | 4,587 | 1.098160 | 2.560 / 8 / 271 |

Against the previously frozen weighted-scalar and actual-Cluster2 address-event
baselines, accepted-event gain retained is 94.92% on full50 and 94.09% on
capacity22.  It trails actual Cluster2 by 1,046 and 1,168 accepted events,
respectively.  Persistent capacity is two addresses per committed bundle, with
240 address grants in 120 bundles.

The maximum latency of 271 is workload/policy evidence, not a universal bound.
The 5:1 policy plus sparse fallback and one-slot/source overrun semantics can
produce long finite-tail cases even though persistent all-row service is
periodic.

## Structural proxy

Yosys 0.52 plus its bundled ABC mapped the candidate to:

| Metric | Result |
| --- | ---: |
| Registered state | 34 bits |
| Generic mapped cells | 667 |
| Longest generic topological path | 42 cells |
| AND / OR / NOT / XOR / MUX | 268 / 255 / 91 / 9 / 18 |
| Reset/enable FF cells | 26 |

This is a same-flow generic structural observation only.  It is not a Liberty
area, routed delay, Fmax, dynamic/leakage power, congestion, or pin-cost result.
The path depth makes physical timing the primary unresolved risk.

## Mutation evidence

| Mutation | Fault | Result |
| --- | --- | --- |
| `A3_K2_MUT_STALE` | retain a nonzero count when refill is empty | expected lockstep failure caught |
| `A3_K2_MUT_DUP` | omit the `g0` address mask before `g1` | expected lockstep failure caught |
| `A3_K2_MUT_STATE_ADV` | compute `g1` from pre-`g0` policy/RR state | expected lockstep failure caught |

The receipt contains exact frozen input hashes, tool identities, run names,
candidate/oracle hashes, aggregate metrics, and the explicit claim boundary.
