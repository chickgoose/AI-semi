# A8 actual-owner W6 mutation regression

This audit materializes final owner commit `eaf3cf7` in a temporary shared clone and
runs its exact canonical qualification against the pinned three-file fovea.
Neither the A7 nor A1 worktree is modified. Set
`A8_W6_OWNER_COMMIT=61b7fb5` to audit the A1 integration commit instead of the
default owner commit. The only allowed identities are final owner
`eaf3cf7260e3268fb9519d570cc4e825fe5b187c` and A1 integration
`61b7fb5ab298d6b25c23655c92538350fcf7041b`. Their qualification, directed TB,
fault TB, fixture, contract checker, and wrapper blobs are pinned explicitly.

The final owner runner resolves its protected-diff baseline from its frozen
source/integration allowlist and commit ancestry. A8 does not override that
selection.

For premature-drain and latency mutations, the temporary owner TB receives
audit-only assertions for:

- `drain_idle_o` remaining low over a live source, request, raw result,
  acknowledgement, endpoint work, or registered retirement;
- registered availability at acceptance +1 cycle;
- a real pre-NBA sink observing retirement at acceptance +2 cycles.

Three temporary source mutations are then qualified independently:

| Mutation | Required outcome |
| --- | --- |
| premature drain | nonzero and no exact qualification PASS |
| extra retire latency | nonzero and no exact qualification PASS |
| stale/no-live native result hidden from endpoint | nonzero, no directed PASS, and A8 raw-causality diagnostic |

The outer runner fails if a mutant returns zero, emits
`A7_W6_SHA_PINNED_DIRECTED_RTL_PASS`, or lacks its A8 independent-monitor
diagnostic. The unmodified baseline must return zero, emit that sentinel, report
all three 146-event counters, emit the directed-RTL marker, and pass the owner's
five-mutant gate. Mutated provenance inputs are committed only inside their
disposable `/tmp` clones so the owner's clean-HEAD binding remains active.

Run:

```sh
tests/a8_fovea_a7_adversarial_actual/run_all.sh
```

All generated repositories, mutated sources, build products, and logs live
under a fresh `/tmp` directory.  This is digital mutation evidence, not physical
qualification.
