# A9 W3 online primal-dual lane-price matcher

This candidate-owned directory is a protocol gate.  It does not modify or bind
the common TB, manifests, existing A9 RTL, or any other candidate.

The cycle model implements a degree-two fixed bipartite proposal graph.  Each
pending source proposes to exactly one legal lane per cycle.  Each lane sees
only its fixed adjacency and accepts at most one proposal.  Price updates are
small signed steps derived exclusively from actual lane FIFO occupancy and
output stall.  Source age, deficit, request pressure, and a global request
vector are not inputs to the decision.

Same-source ordering is protected by a route lock: while a source has an
outstanding accepted event, later accepted events from that source use the same
FIFO lane.  Rejected unlocked sources enter a bounded escape state after the
small rejection counter saturates.  The escape proposal remains pinned until a
grant, so a local cyclic lane tie serves it within the lane adjacency degree
under always-ready service.  Under stalls, the bound counts service
opportunities and therefore requires weak fairness for the pinned lane.

The centralized references are measurement models only:

- `ExactKGrant` computes maximum-cardinality matching from the complete source
  set and exposes the performance ceiling and global search cost.
- `FlatRoundRobin` grants at most one source per cycle through a flat source
  scan.

Run local protocol tests with:

```text
python3 -m unittest discover -s experiments/a9_online_lane_price_matcher -p 'test_*.py'
```

`run_w3_evaluation.py` verifies the canonical full50, cap22, and generator-v4
SHA values before generating traces in a secure temporary directory.  It runs
all cap22 rows, five moving-hotspot controls, and candidate-owned symmetry/stall
tests against the price matcher, a price-disabled ablation, exact K-grant, and
flat RR.  Result CSV/JSON paths are optional and should point outside the repo:

```text
python3 experiments/a9_online_lane_price_matcher/run_w3_evaluation.py \
  --suite-root /home/chickgoose/projects/a1 \
  --csv /tmp/a9-w3.csv --summary /tmp/a9-w3.json
```

The runner returns status 1 when the recorded gate is HOLD; that is the
expected fail-closed result, not a trace-generation or correctness failure.

The final gate is **HOLD**.  Price-on and price-off are identical on cap22 and
the moving-hotspot controls.  Alternating lane stalls make the price toggle but
do not improve delivery or p99.  The strong cap22 result is therefore evidence
for the fixed degree-two matcher, not for online primal-dual pricing.  A route-
locked event also has an explicit starvation counterexample when its chosen
lane is stalled while its other legal lane stays ready.  Per the assignment,
no synthesizable SV or lockstep TB is created under HOLD.
