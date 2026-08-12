# W8 A8 independent K2 scheduler falsification suite

This additions-only directory owns its oracle, vectors, mutation models, and
read-only bindings. It does not edit or import an owner worktree, team RTL,
common tests, or manifests.

## Re-audit disposition

| Blocker | Executable disposition |
|---|---|
| immutable executed snapshot | schema-3 binder forces `pinned_tool materialized_artifact fixed_argv`; artifact/tool/source hashes, argv, and closed environment are bound; pre/post artifact tamper, external artifact, placeholder command, changed tool, inherited environment, and missing real output are negative-tested |
| actual diagnostic matching | mutation success requires the first token of the caught `ContractViolation` to equal the required diagnostic; a deliberately wrong label must fail |
| exact paired-column relationship | no immutable A4 paired-column contract exists in the visible Git refs, so this suite rejects that identifier and makes no column-pair claim; the third local oracle is named only `paired_row_calendar_proposal_k2` |
| real batched-IWRR | the oracle now uses A2's exact calendar `[1,2,0,1,2,3,1,2,1,2,1,2]`, fixed two-token phases, compacted survivors, per-row RR pointers, waive-empty/no-borrow sparse behavior, and automatic all-empty phase advance |

## Atomic boundary

A nonempty scheduler offer contains `grant_count` 1 or 2 and ordered addresses.
It has one `bundle_ready`: no lane commits separately; while stalled, count,
addresses, and policy state remain stable; acceptance retires every valid lane
atomically. `grant_count=0` represents no offer.

The exact A2 owner contract automatically waives an all-empty phase without
consulting `bundle_ready`. The suite models and tests that behavior explicitly.
If the shared boundary instead requires a count-zero phase to be a held offer,
A2 is not compatible with that stronger interpretation; A8 does not conceal
the difference.

Independent lane stalls are modeled only by `TwoLaneBufferedLink`, downstream
of the scheduler. A stalled link lane retains its address and cannot mutate
scheduler policy.

## Contracts

`exact_weighted_scalar_prefix_k2` equals two successive canonical WEIGHT=5
Fovea scalar steps on one stable request snapshot. `g0` is removed before `g1`,
and `g1` consumes every intermediate RR transition.

`batched_iwrr_k2` follows immutable A2 commit
`7c30d54866d81e856f9aa652db236c3a9face924`. Its six phases are
`(1,2),(0,1),(2,3),(1,2),(1,2),(1,2)`. Empty row entitlements are waived,
never borrowed or banked. Under persistent all-row demand the exact row total
is `[1,5,5,1]`.

`paired_row_calendar_proposal_k2` uses
`(0,1),(2,1),(2,1),(2,1),(2,1),(2,3)` and asserts only row opportunities and
aggregate `[1,5,5,1]`. It makes no cortical-column, same-column, paired-column,
or cross-lane column claim. `paired_cortical_column_k2` and the obsolete
staggered identifier are rejected until an exact immutable owner contract can
replace this proposal.

## Mutation diagnostics

The ten actual required diagnostics are:

```text
FALSE_AGGREGATE_1551
CALENDAR_ADVANCE_ON_UNCOMMITTED_LANE
STALE_G1
DUPLICATE_SOURCE
WRONG_RR_STATE_AFTER_G0
FUTURE_ARRIVAL_OVERCLAIM
INDEPENDENT_LANE_STALL_CORRUPTION
RESET_PHANTOM
SPARSE_FALLBACK_DEBT
BITMAP_POPCOUNT_CONFUSION
```

`SPARSE_FALLBACK_DEBT` now kills the incorrect behavior—creating cross-row
borrow/debt where A2 requires waive-empty—not the former A8 debt proposal.

## Owner bindings

Schema 3 binds and executes two immutable owners:

- A2 `7c30d54866d81e856f9aa652db236c3a9face924`: the materialized committed
  model self-test, including its 1,572,864 bitmap/phase/pointer cases;
- A3 `632e68d247ec36a35b62dbd5c100b0a23d47cf7b`: the materialized committed
  exact-scalar owner model and persistent `[20,100,100,20]` probe.

Both bindings include their RTL source hash, but their evidence scopes are
`owner_selftest` and `owner_model`, not `owner_rtl`. No Verilog simulator is
installed in this environment, so claiming executed RTL would be false. The
binder reports the exact scope in its PASS sentinel. No A4 paired-column owner
binding is present because no such immutable commit was found.

The executable shape cannot be supplied by the registry: it is always the
hash-pinned absolute tool, then the hash-verified materialized artifact, then
fixed placeholder-free argv, under a three-variable closed environment. The
artifact is hash-checked immediately before and after execution and required
owner output must actually appear.

## Run and limits

```bash
tests/w8_k2_scheduler_contracts/run_all.sh
```

The suite exhausts all 65,536 initial request masks for each of three Python
contracts, runs directed atomic hold/reset/sparse/RR cases, kills ten mutations
with exact diagnostics, and executes the two immutable owner-model bindings.

This is limited to N=16, WEIGHT=5, K2, the directed vectors, and the stated
owner-model scopes. It does not simulate owner RTL, establish the unavailable
paired-column relationship, or cover deep queues, multiple same-source
occurrences, bitmap transport, CDC, PHY, pins, PVT, power, area, timing, or
post-route behavior.
