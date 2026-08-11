# A8 Non-Crossing Frontier Fabric

Status: **cycle-model HOLD; no RTL was authorized by the model gate**.

This isolated Wave-3 candidate gives `K` lanes strictly ordered contiguous
address territories.  Its only ownership state is `K-1` frontiers.  Each lane
uses local round-robin selection.  A frontier moves by at most one address per
cycle toward the request-mass quantile for that boundary.  Minimum territory
width is one, so frontiers cannot cross and every source always has exactly one
owner.

The bounded emergency rule observes only lane occupancy streaks.  When one lane
has remained overloaded while an adjacent lane is empty, their shared frontier
may move one address into the overloaded territory.  It neither timestamps nor
ages requests.  Reverse-direction debounce prevents the static `0x0808`
oscillation counterexample found by the first model revision.

The model deliberately contains no calendar, age ordering, quadtree, work
stealing, source splitting, or hotspot predictor.  It consumes the common
one-pending-source request mask but does not modify any common testbench or
manifest.

## Reproduce

```bash
tests/a8_non_crossing_frontier/run_model_tests.sh
```

Results are frozen in `w3_model_results.json` and interpreted in
`docs/experiments/a8-non-crossing-frontier-results.md`.
