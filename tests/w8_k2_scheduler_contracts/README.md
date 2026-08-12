# W8 A8 independent K2 scheduler falsification suite

This additions-only directory defines an independent executable oracle for the
final three proposed scheduler contracts. `owner_bindings.json` stays empty
until head supplies immutable owner SHAs. Nothing here imports team RTL,
manifests, common tests, or an owner worktree.

## Common atomic scheduler boundary

Every scheduler exposes one offer consisting of `grant_count` (0, 1, or 2) and
two ordered address slots. It has one `bundle_ready` handshake:

- when `bundle_ready=0`, count, addresses, and policy state stay stable;
- no lane commits separately at this boundary;
- on commit, all valid addresses retire atomically;
- policy advances by exactly `grant_count` successful scalar microsteps.

The independent-lane-stall falsifier is applied only to
`TwoLaneBufferedLink`, a post-scheduler adapter. It proves that a stalled link
lane retains its payload and never reaches back into scheduler policy.

## The three proposed contracts

`exact_weighted_scalar_prefix_k2` is strong equivalence to two successive
canonical WEIGHT=5 Fovea scalar steps on one stable request snapshot. `g0` is
removed before `g1`, and `g1` uses the intermediate RR state.

`batched_iwrr_k2` is an explicit interleaved weighted-round-robin calendar. One
12-microstep batch is:

```text
round 1: 0,1,2,3
round 2: 1,2
round 3: 1,2
round 4: 1,2
round 5: 1,2
```

A K2 offer executes the next zero, one, or two successful token microsteps;
the token index advances by that count only at atomic commit. A missing nominal
row may lend to an eligible row, producing equal-and-opposite signed debt. When
an indebted nominal is reached, eligible positive debt is repaid first. This
sparse fallback/debt law is an engineering proposal, not a biology claim.

`paired_row_calendar_proposal_k2` uses the token sequence
`0,1,2,1,2,1,2,1,2,1,2,3`. It asserts row opportunities and aggregate
`[1,5,5,1]` only. It intentionally makes **no cortical-column or cross-lane
column relationship claim**, because no exact immutable A4 owner contract was
provided. The obsolete identifiers `staggered_two_slot_epoch_k2` and
`paired_cortical_column_k2` are rejected.

For the two calendars, `[1,5,5,1]` is aggregate service under continuous
eligibility. Only scalar-prefix claims exact canonical scalar order. A row
bitmap is eligibility data, never multiple scalar events.

## Required mutation diagnostics

The mutation runner catches the actual `ContractViolation` and requires its
first token to equal the diagnostic below. A difference with the wrong label is
a test failure.

| Mutant | Required actual diagnostic |
|---|---|
| false aggregate | `FALSE_AGGREGATE_1551` |
| policy advance while atomic offer is uncommitted | `CALENDAR_ADVANCE_ON_UNCOMMITTED_LANE` |
| stale second address | `STALE_G1` |
| repeated address | `DUPLICATE_SOURCE` |
| pre-g0 RR state used by g1 | `WRONG_RR_STATE_AFTER_G0` |
| next-cycle request sampled by g1 | `FUTURE_ARRIVAL_OVERCLAIM` |
| separately buffered stalled lane overwritten/policy-touched | `INDEPENDENT_LANE_STALL_CORRUPTION` |
| reset retains an offer | `RESET_PHANTOM` |
| signed sparse debt discarded | `SPARSE_FALLBACK_DEBT` |
| bitmap population emitted as extra grants | `BITMAP_POPCOUNT_CONFUSION` |

## Immutable owner binding

Binding schema version 2 requires:

- a full 40-hex owner commit and SHA-256 for every materialized source;
- an adapter artifact that is itself one of those committed sources;
- an absolute execution tool with an exact SHA-256;
- fixed argv containing `{snapshot}`, `{vectors}`, `{result}`, and a fresh
  `{challenge}` path;
- a closed environment containing only fixed `LANG`, `LC_ALL`, and
  `PYTHONHASHSEED` values.

The binder materializes blobs with `git show <commit>:<path>`, invokes only the
pinned tool plus the materialized adapter, and requires the result to echo a
fresh snapshot challenge bound to the owner commit, adapter hash, and complete
source-manifest hash. Missing proof, external adapters, changed tools, inherited
environment, unsafe paths, moving/short commits, and output differences fail.
This establishes which immutable artifact ran; an adapter result is still only
evidence for its listed vectors, not proof of arbitrary RTL behavior.

The included fake adapter is solely a positive transport fixture. It and its
oracle dependency are copied into a temporary git commit and materialized by
the tests; it is never treated as owner evidence.

## Run and limits

```bash
tests/w8_k2_scheduler_contracts/run_all.sh
```

The suite exhausts all 65,536 initial request masks for each Python contract,
runs directed atomic hold/reset/debt/RR cases, kills all ten mutations with
matched diagnostics, and emits an explicit owner PASS or SKIP sentinel.

Coverage is limited to the independent N=16, WEIGHT=5, K2 models and committed
vectors. Exhaustive masks are one initial bundle-ready-high oracle cycle, not owner RTL
exhaustion. No owner RTL is compiled while the binding registry is empty. Owner
binding later checks only the exact listed sources and vectors. Deep queues,
multiple occurrences per source per cycle, bitmap transport, CDC, link PHY,
pins, PVT, power, area, timing, and post-route behavior are outside scope.
