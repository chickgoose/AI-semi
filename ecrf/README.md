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

The default command exits zero when a reproducible evaluation completes and
prints its explicit `GO` or `HOLD` decision.  Automation that requires an RTL
candidate must run `ecrf/run_w3.sh --require-go`; a valid HOLD exits 3.

For the current full50/capacity22 replay, point `ECRF_COMMON_ROOT` at a
read-only common benchmark checkout containing generator-v4 and both official
manifests.  Generated traces and temporary data are written below `/tmp`.
The runner fail-closes unless the common commit and all three recorded input
SHA-256 values exactly match the pinned Wave-3 provenance.

The recorded Wave-3 decision is HOLD; see `docs/w3_results.md`.  Because the
pre-RTL gate failed, this directory intentionally contains no candidate RTL or
lockstep SV testbench.
