# A21 reusable K2 binding mutation harness

This additions-only package checks the normalized N16/K2 seam used by the A2
`a2_batched_iwrr_k2_normalized` and A3 `a3_k2_common_wrapper` wrappers. It does
not edit or import frozen common files, candidate RTL, or owner worktrees.

The harness treats a wrapper runner as a black box. A registry command receives
one JSON stimulus and writes one JSON pin trace. The built-in process-isolated
fixture qualifies the checker; it is explicitly test infrastructure and is not
owner RTL evidence. Replace its command in `bindings.json` with an RTL simulator
runner using the same trace schema to apply the gate to owner snapshots.

## Why the oracle is flattened

For every atomic accepted offer, the oracle appends both event identities in
native offer order to one global FIFO. Retirement handshakes are flattened in
lane order and compared against that FIFO, including the 16-bit payload and
exact presentation cycle. It never derives ordering from independent
per-source queues.

That distinction is executable: `test_cross_source_lane_swap_defeats_per_source_scoreboard_only`
shows that swapping source 0 and source 5 passes a conventional per-source
scoreboard while this oracle raises `GLOBAL_ORDER_MISMATCH`.

## Checked contract

- a count-2 native offer is accepted wholly or not at all;
- `source_ready` names exactly every source captured on the accepted edge;
- the charged two-entry link refuses a non-fitting atomic offer;
- lane 0 is the oldest event and a younger lane cannot bypass it;
- valid source/event/payload remain stable while stalled;
- reset discards pre-reset link contents;
- `drain_idle` cannot precede global quiescence; and
- accepted-to-presented latency is exact, not merely eventual.

The predeclared mutations and required first diagnostics are:

| Mutation | Diagnostic |
|---|---|
| `partial_count2_accept` | `PARTIAL_COUNT2_ACCEPT` |
| `lane_swap` | `GLOBAL_ORDER_MISMATCH` |
| `younger_bypass` | `YOUNGER_BYPASS` |
| `overflow_drop` | `OVERFLOW_DROP` |
| `duplicate` | `DUPLICATE_RETIRE` |
| `wrong_source_ready` | `WRONG_SOURCE_READY` |
| `unstable_stall` | `UNSTABLE_STALL` |
| `early_drain` | `EARLY_DRAIN` |
| `stale_reset` | `STALE_RESET` |
| `latency_shift` | `LATENCY_SHIFT` |

Both wrapper profiles must kill all ten, for 20 total kills.

## Run

```bash
tests/a21_k2_binding_mutation_harness/run_all.sh
```

To check a trace from another runner directly:

```bash
python3 tests/a21_k2_binding_mutation_harness/check_trace.py \
  --binding a2_batched_iwrr_k2_normalized \
  --stimulus /path/stimulus.json \
  --observations /path/observations.json
```

An observation contains `offer_ready`, the accepted `source_ready` source
indices, two level-sensitive retire output records, and `drain_idle` for every
cycle. The stimulus supplies the ordered native offer sideband. For A2,
`drain_idle` is a wrapper pin; an A3 runner derives the same observation from
owner-offer/live state and charged-link emptiness without feeding it into RTL.
