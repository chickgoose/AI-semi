# A5/A8 cross-validation

Contract commits inspected read-only:

- A5 common transaction evaluator `41c425bec79aca6c84f5856ca7dee2a4865a6447`;
- A8 independent scheduler falsifier `1248a19e1f3bea4c519645460cb810b19fab4c5d`.

## A8 result

A8 agrees with this candidate's pinned Ganghee Fovea equations and atomic
boundary.  Its own suite passed 22 tests, exhaustive initial-mask checks for
all three proposed contracts, and all ten diagnostic-specific mutations.  The
candidate transaction adapter matched all seven A8 scalar-prefix owner cases.
An additional candidate-local comparison matched ordered grants and complete
post-prefix policy state for all 65,536 initial request masks.

The immutable owner-binding check is performed after the adapter commit so A8
can materialize the exact committed blobs and challenge them from a closed
environment.  Its result is reported with that commit rather than embedded
recursively here.

## A5 result

A5's own evaluator qualification passed five unit tests and killed all seven
mutations.  The candidate exporter passed transaction accounting, persistent
aggregate weight, sparse work conservation, distinct same-row winners,
ordered link stalls, reset abort, and final drain behavior.  Its persistent
first-120 row vector was exactly `[10,50,50,10]`.

The A5 evaluation nevertheless returned the expected `HOLD`, with 187 hard
failures, all caused by its different winner oracle:

| Run | Candidate grade | Failures |
| --- | --- | ---: |
| persistent_weight_120 | FAIL | 90 prefix + 88 primary mismatches |
| stale_second_revalidation | FAIL | 3 prefix + 3 primary mismatches |
| future_arrival_divergence_witness | FAIL | 1 prefix + 1 primary mismatch |
| reset_abort_no_phantom | PRIMARY_ONLY | 1 prefix mismatch |

The counts overlap because a primary mismatch is also a prefix mismatch.  The
first decisive witness is A5 `[0,4]` versus Ganghee Fovea `[4,11]` under full
reset-state demand.  A5 uses row wheel
`[0,1,1,1,1,1,2,2,2,2,2,3]` and four row-local column pointers.  This candidate
uses center/peripheral arbiter trees, a shared column arbiter tree, and the
six-state Fovea round.  Rewriting the candidate to satisfy A5 would violate the
requested exact canonical Fovea contract and the independently matching A8
oracle, so no RTL or policy fix is justified.

Two A5 test-only synthetic references were supplied solely because its CLI
requires exactly three evidence files.  They both passed; they are not owner
candidates or comparison claims.
