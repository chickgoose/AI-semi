# A2 official always-ready K2 common binding

This candidate-owned binding is the narrow promotion seam for
`a2_batched_iwrr_k2`. It is **official only for the common N16 address-only
suite with both normalized retirement lanes continuously ready**. It is not
the general charged adapter owned by another track.

## Frozen contract

- `NUM_SOURCES=16`, `ADDR_WIDTH=16`, `RETIRE_LANES=2`, `FIFO_DEPTH=0`.
- A core `grant_count` of zero, one, or two maps exactly to normalized valid
  `00`, `01`, or `11`.
- For count two, both source credits and both retire lanes transact on the same
  edge. There is no partial acceptance.
- `retire_source` is the selected source address and `retire_event` is the
  normalized address/event identity already present on that selected source.
  The wrapper only multiplexes this mandatory identity; it invents no payload
  and reconstructs no event field from private state.
- Reset gates `source_ready` and `retire_valid` quiet. The reset test asserts a
  second reset only after full drain; no mid-traffic cancel/preserve contract is
  introduced.
- The wrapper and binding contain no sequential state, queue, skid register,
  or hidden event state. The owner core and all of its existing state remain
  inside the charged functional boundary.

The frozen common testbench selects the compatibility module name
`aer_ganghee_native_binding` with `AER_CLEAN_GANGHEE_NATIVE`. That alias is
zero-state and instantiates the uniquely named A2 binding. The exact compile
closure is `official_common.f`; `provenance.json` fixes its order, owner commit
and blob, candidate source hashes, and the common interface/TB/assertion and
neutral manifest hashes.

## Capability boundary

Independent lane readiness is deliberately unsupported. It must not be
silently treated as atomic readiness:

```sh
./candidates/a2_batched_iwrr_k2/official_common/run_tests.sh \
  --capability independent-lane-stall
# prints A2_K2_CAPABILITY_SKIP and exits 77
```

The direct unit test temporarily allows only the uniform `00 -> 11` transition
to falsify atomic hold/refill. This is test access to owner behavior, not an
advertised common-suite capability. The production binding requires `11` on
every active post-reset edge and fails closed otherwise.

## Reproduction

Run:

```sh
./candidates/a2_batched_iwrr_k2/official_common/run_tests.sh
```

The runner uses a unique `/tmp` build root, separates compile and runtime
status, and requires exact pass/failure sentinels. It runs provenance mutation
tests; directed count 0/1/2, uniform atomic hold/refill, conservation, reset and
drain; five negative mutations; then the frozen common `basic_single`,
`basic_sparse`, `basic_simultaneous`, and `basic_reset_drain` tests. Generated
objects and metrics are not committed.
