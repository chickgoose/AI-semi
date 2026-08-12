# A8 actual-owner W6 mutation regression

This audit materializes owner commit `d3c52f0` in a temporary shared clone and
runs its exact canonical qualification against the pinned three-file fovea.
Neither the A7 nor A1 worktree is modified.  Set `A8_W6_OWNER_COMMIT=b520125`
to audit the integration cherry-pick, whose W6 patch and relevant blobs are
identical.

The owner runner's supported `A7_W6_BASE_COMMIT` input is bound to the audited
commit's first parent.  This preserves the protected-path check for both the A7
source commit and its integration cherry-pick without treating older unrelated
integration changes as W6 changes.

The temporary owner TB receives audit-only assertions for:

- `drain_idle_o` remaining low over a live source, request, raw result,
  acknowledgement, endpoint work, or registered retirement;
- registered availability at acceptance +1 cycle;
- a real pre-NBA sink observing retirement at acceptance +2 cycles.

Three temporary source mutations are then qualified independently:

| Mutation | Required outcome |
| --- | --- |
| premature drain | nonzero and no exact qualification PASS |
| extra retire latency | nonzero and no exact qualification PASS |
| stale same-address retrigger caused by bypassing the current-result mask | nonzero and no exact qualification PASS |

The outer runner fails if a mutant returns zero **or** emits
`A7_W6_EXACT_CANONICAL_QUALIFICATION_PASS`.  A baseline exact qualification must
first return zero and emit that sentinel, preventing an unavailable tool or a
broken fixture from masquerading as successful mutation killing.

Run:

```sh
tests/a8_fovea_a7_adversarial_actual/run_all.sh
```

All generated repositories, mutated sources, build products, and logs live
under a fresh `/tmp` directory.  This is digital mutation evidence, not physical
qualification.
