# Candidate-neutral K2 common binding contract

This A1-owned contract is the shared normalized boundary for the A2 and A3 K2
owners. It adds no arbitration policy and changes none of the frozen common
testbench, trace manifests, or candidate-owned RTL.

## Fixed shape and capability declaration

`aer_k2_binding_pkg` fixes `K2_RETIRE_LANES=2`, a two-entry charged link, and a
two-bit `0/1/2` offer count. `3` is invalid and fails closed. The package's
`K2_COMMON_READY_CAPABILITY` is `K2_READY_UNIFORM`: the shared A2/A3 common
claim covers always-ready and uniform two-lane stalls. Independent per-lane
stall support is not inferred from this declaration and requires its own
capability-gated qualification.

The helper `k2_atomic_offer_ready(count, ready)` defines the unbuffered owner
rule: count zero is vacuously ready, count one uses `ready[0]`, and count two
requires `ready[0] && ready[1]`. A count-two scheduler offer is one ordered
transaction; partial lane progress must never advance scheduler policy.

## Reusable charged shim

`aer_k2_ordered_link_shim` sits after an owner scheduler and remains inside the
candidate RTL/PPA boundary. Its state is not a free testbench binding. Each
nonzero owner offer carries an ordered count plus `offer_source0/1`. The shim:

1. checks that every valid source index is distinct, in range, and live;
2. computes capacity after the current edge's ordered retire handshakes;
3. asserts `offer_ready` only when the complete offer fits;
4. asserts `source_ready` for exactly the accepted source set on that edge;
5. captures `source_event[offer_sourceN]` and that source identity on the same
   acceptance edge; and
6. stores and retires those event/source pairs in their original order.

The scheduler must use `offer_count != 0 && offer_ready` as its sole commit
condition. An offered source remains live, and its `source_event` remains
stable, until that edge. `source_ready` is therefore never speculative and is
never a capacity advertisement for sources the owner did not select.

Lane 0 is always the oldest buffered event. If it alone is ready, it may retire
and the younger entry compacts to lane 0. Lane 1 is exposed only on an edge on
which both entries transfer; `ready[1]` can never bypass a blocked head. A
fitting offer may refill on the same edge as retirement, without a transport
bubble. Retire movement changes only charged link state and never feeds owner
policy state.

## Reset and drain contract

Active-low reset clears both charged entries and forces `source_ready` and
`retire_valid` low. The shim reports reset as externally drained, so no
pre-reset event can leak after release.

Outside reset, `drain_idle` is true only when all four conditions hold:

- the owner declares `scheduler_idle`;
- the charged link is empty;
- all normalized source latches are empty; and
- `offer_count` is zero.

Portable in-module assertions enforce reset quiet, exact `source_ready`, live
source acceptance, legal count/capacity, ordered retirement, truthful
`link_empty`, and truthful final drain. A wrapper must drive `scheduler_idle`
from real owner state; it must not tie it high while an internal offer or
pipeline entry remains.

## Integration sketch

An A2 or A3 wrapper connects its policy-neutral owner fields to
`offer_count`, `offer_source0`, and `offer_source1`, and connects
`offer_ready` back to the owner's atomic bundle-ready input. The wrapper does
not recreate `source_ready`, buffer events elsewhere, or interpret the two
offer entries as independently committed scheduler lanes.

Compile and directed contract checks are isolated from the frozen common suite:

```sh
IVERILOG=/path/to/iverilog \
VVP=/path/to/vvp \
VERILATOR=/path/to/verilator \
  tests/a1_k2_common_binding/run_compile_tests.sh
```

The tests compile the package independently, compile and run the reusable shim,
exercise count-one/count-two mapping, blocked-head ordering, compaction,
refill, exact event capture, reset flush, and clean drain, then lint the same
sources with Verilator.
