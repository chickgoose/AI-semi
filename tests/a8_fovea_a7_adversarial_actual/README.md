# A8 actual-owner W6 mutation regression

This audit materializes owner commit `e9f27e6` in a temporary shared clone and
runs its exact canonical qualification against the pinned three-file fovea.
Neither the A7 nor A1 worktree is modified. Set
`A8_W6_OWNER_COMMIT=0f49816` to audit the source commit instead of the default
integration commit. The only allowed identities are latest owner integration
`e9f27e6aed302491011a5deb803a7b42a0c712b3` and source
`0f49816b48a4cba027d40733a09edb590bfc7a86`. Their qualification, directed TB,
fault TB, fixture, contract checker, and wrapper blobs are pinned explicitly.

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
| stale/no-live native result hidden from endpoint | nonzero, no directed PASS, and A8 raw-causality diagnostic |

The outer runner fails if a mutant returns zero, emits
`A7_W6_SHA_PINNED_DIRECTED_RTL_PASS`, or lacks its A8 independent-monitor
diagnostic. A baseline exact qualification must first return zero and emit that
new sentinel, preventing an unavailable tool or broken fixture from
masquerading as successful mutation killing.

Run:

```sh
tests/a8_fovea_a7_adversarial_actual/run_all.sh
```

All generated repositories, mutated sources, build products, and logs live
under a fresh `/tmp` directory.  This is digital mutation evidence, not physical
qualification.
