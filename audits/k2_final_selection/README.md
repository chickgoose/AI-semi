# Historical A2/A3 K2+P6 digital selection (superseded/noncurrent)

This immutable receipt records a former P6 digital-only selection study. The
current goal contract at `contracts/redred_system_goal/active_goal.json`
supersedes it. Its recorded A2 selection is **noncurrent** and has no authority
for the implemented single-edge endpoint, the current A2/A3 decision, release
interface selection, or team release. All of those release decisions remain
HOLD. The current single-edge `PARALLEL_FALLBACK` interface is implemented and
selected as the only release-eligible interface, but its release remains held.
That interface policy does not restore any authority to this P6 study.

Within its historical scope, this receipt consumed the
immutable actual-scheduler+actual-P6 replay and the identical-boundary generic
Yosys cost closure. Both A2 and A3 must first pass conservation, order,
full50/capacity22, reset-scope, actual-RTL mutation, semantic-grade, and
same-flow structural-Pareto gates.

The former competition-oriented digital policy selected A2 because its
full50 and capacity22 throughput advantages over A3 exceed 5%, while its
mapped-cell, state, and wire-proxy penalties remain within the declared guard and
it remains on the structural Pareto set. A3 remains the exact-scalar-prefix
fallback if that semantic property becomes a hard requirement.

This historical policy was declared with its selection publication after the
underlying measurements existed; it is a deterministic engineering decision,
not a preregistered statistical threshold. A2 also pays a 27.906977% generic
logic-depth penalty versus A3. That proxy is reported rather than hidden and is
not interpreted as a physical Fmax result.

This is not a current or physical selection receipt. Standard-cell area, Fmax,
routed wires, power, energy/event, CDC/RDC, mid-flight reset abort, current
candidate selection, interface selection, and release remain HOLD.

Run:

```sh
K2_FINAL_SELECTION_OUT=/tmp/k2-final-selection.json \
  tests/k2_final_selection/run_all.sh
```
