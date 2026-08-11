# Expander-Conservative Reaction Fabric (ECRF)

This candidate-private directory contains the Wave-3 A2 ECRF research gate.
It does not modify or replace the common testbench, manifests, or any existing
candidate RTL.

The executable reference explores `N=16`, `K=2/4`, intermediate-cell count
`B`, and source degree `d`.  A source is statically connected to `d` cells.
During one bounded reaction round:

1. every free cell proposes its lowest-index active neighbor;
2. a source proposed by several cells conservatively commits to one cell; and
3. distinct committed cells are assigned to available retire lanes.

Only the final round result may accept an event.  Intermediate proposals never
own an occurrence.  An exact bipartite matcher is used solely as a reference
certificate and is not part of the candidate mechanism.

Run unit and exhaustive checks with:

```bash
ecrf/run_w3.sh
```

For the current full50/capacity22 replay, point `ECRF_COMMON_ROOT` at a
read-only common benchmark checkout containing generator-v4 and both official
manifests.  Generated traces and temporary data are written below `/tmp`.

