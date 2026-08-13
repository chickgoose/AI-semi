# Final A2/A3 K2+P6 digital selection

This receipt closes the digital-only selection objective. It consumes the
immutable actual-scheduler+actual-P6 replay and the identical-boundary generic
Yosys cost closure. Both A2 and A3 must first pass conservation, order,
full50/capacity22, reset-scope, actual-RTL mutation, semantic-grade, and
same-flow structural-Pareto gates.

The declared competition-oriented digital policy selects A2 because its
full50 and capacity22 throughput advantages over A3 exceed 5%, while its
mapped-cell, state, and wire-proxy penalties remain within the frozen guard and
it remains on the structural Pareto set. A3 remains the exact-scalar-prefix
fallback if that semantic property becomes a hard requirement.

This is not a physical selection receipt. Standard-cell area, Fmax, routed
wires, power, energy/event, CDC/RDC, and mid-flight reset abort remain HOLD.

Run:

```sh
K2_FINAL_SELECTION_OUT=/tmp/k2-final-selection.json \
  tests/k2_final_selection/run_all.sh
```
