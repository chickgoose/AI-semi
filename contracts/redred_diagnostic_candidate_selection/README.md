# Diagnostic A2/A3 candidate selection

This successor contract combines the latest-RTL full50 replay with the matched
A2/A3 Genus/Innovus diagnostic cohort.  It verifies both upstream contracts,
recomputes a scalar-free Pareto front, and then applies the predeclared policy:

- default aggregate-weighted use: A2 when it remains nondominated;
- exact scalar-prefix semantics required: A3, subject to its own hard gates;
- any shared organizer/producer/freshness HOLD: no official candidate.

The current Pareto front is `[A2, A3]`.  A2 is better in overrun, throughput,
occurrence-to-accept maximum latency, and setup WNS.  A3 is better in
accept-to-retire maximum latency, hold WNS, area, and vectorless total power.
Neither dominates the other, so the conditional team recommendation is A2 and
the exact-prefix fallback is A3.  The official selected candidate remains
`null` and release remains HOLD.

Run:

```sh
tests/redred_diagnostic_candidate_selection/run_all.sh
```

