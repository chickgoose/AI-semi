# Batched-IWRR-K2 boundary contract

This isolated N16 candidate accepts a level request bitmap and offers up to two
ordered, distinct 4-bit source addresses per reference cycle.

## Interface and acceptance

- `req[15:0]`: one pending occurrence per row-major source, rows `0..3` and
  columns `0..3`.
- `grant_count[1:0]`: `0`, `1`, or `2` ordered address grants.  Address 0 is
  valid when count is nonzero; address 1 is valid only when count is two.
- `grant_addr0`, `grant_addr1`: ordered source addresses.
- `grant_bitmap`: exact union of the valid address grants; it is provided for
  source acknowledgement and checking, not as an encoded link.
- `bundle_ready`: the only acceptance signal.  On a rising edge with a nonzero
  count and `bundle_ready=1`, every valid address commits exactly once as one
  atomic bundle.  No lane can commit independently.
- A nonempty offer first observed with `bundle_ready=0` is captured internally.
  Its count, ordered addresses, bitmap, and all policy state remain stable until
  the atomic commit, even if `req` changes.  The requester must nevertheless
  keep each offered source pending until commit; accepted bits are cleared for
  the following cycle.  A source cannot encode a second occurrence while its
  bit is already pending.
- On atomic commit the token cursor advances by exactly `grant_count`
  microsteps.  A zero-count cycle never advances policy.
- `drain_idle` is true only when `req == 0` and no bundle is held.  Reset clears
  the calendar cursor, row pointers, and held offer and forces count zero;
  system reset must clear the external pending bitmap as well.

## Calendar and sparse fallback

The cyclic event-entitlement calendar is

`[1,2, 0,1, 2,3, 1,2, 1,2, 1,2]`.

Under persistent all-row demand, each accepted K2 bundle consumes two
consecutive tokens, so six accepted cycles grant rows `[1,5,5,1]` exactly.
Every adjacent cyclic token pair names different rows, and each row uses an
independent rotating source pointer.

Sparse fallback is deterministic per committed microstep:

1. inspect the current calendar token's preferred row;
2. if it is empty, inspect rows `preferred+1`, `preferred+2`, and `preferred+3`
   modulo four, selecting the first nonempty row;
3. select that row's first requester from its rotating source pointer;
4. mask that exact source before constructing the next ordered lane;
5. repeat for lane 1 using the next calendar token and updated scratch pointer;
6. on atomic acceptance, consume one calendar token per selected event and
   update only the rows that won; with no request, offer count zero and freeze.

Fallback consumes the current token immediately; it creates no debt, credit, or
later catch-up burst.  Thus sparse traffic drains at up to two events/cycle, but
the exact `[1,5,5,1]` theorem is scoped to persistent demand in all four rows.

Independent per-lane stalls are outside this boundary.  A separately buffered
link adapter may absorb them, but it must accept this bundle atomically and may
not feed partial-lane progress back into scheduler policy.
