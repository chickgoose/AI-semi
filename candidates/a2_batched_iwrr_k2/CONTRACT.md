# Batched-IWRR-K2 boundary contract

This isolated N16 candidate accepts a level request bitmap and offers up to two
ordered, distinct 4-bit source addresses per reference cycle.

## Interface and acceptance

- `req[15:0]`: one pending occurrence per row-major source, rows `0..3` and
  columns `0..3`.
- `grant_valid[1:0]`, `grant_addr0`, `grant_addr1`: ordered address grants.
  Lane 1 is never valid unless lane 0 is valid.
- `grant_bitmap`: exact union of the valid address grants; it is provided for
  source acknowledgement and checking, not as an encoded link.
- `grant_ready`: atomic readiness.  On a rising edge with `grant_ready=1`, all
  valid lanes are accepted exactly once.  There is no partial-lane acceptance.
- While a nonempty batch is stalled, the requester must keep `req` stable.  The
  scheduler then keeps every output and all state stable.  Accepted request bits
  must be cleared for the following cycle.  A source may not create a second
  occurrence while its bit is already pending.
- `drain_idle` is exactly `req == 0`.  Reset clears scheduling state and emits no
  post-reset history; system reset is required to clear the external pending
  bitmap as well.

## Calendar and sparse fallback

The cyclic event-entitlement calendar is

`[1,2, 0,1, 2,3, 1,2, 1,2, 1,2]`.

Under persistent all-row demand, each accepted K2 batch consumes two consecutive
tokens, so six accepted cycles grant rows `[1,5,5,1]` exactly.  Every token pair
contains different rows, and each row uses an independent rotating source
pointer.

Sparse fallback is deterministic **waive-empty, never borrow**:

1. inspect only the current phase's two fixed consecutive tokens;
2. waive an entitlement whose row is empty;
3. select the first requesting source in each nonempty scheduled row, starting
   at its rotating pointer;
4. compact surviving event grants into lanes 0 then 1 in token order;
5. advance one phase and the granted row pointers on atomic acceptance;
6. advance an all-empty phase automatically, without changing row pointers.

Thus sparse traffic drains and can use up to two grants per cycle, but no claim is made
that absent rows receive a fictitious event share.  Empty entitlements create no
credit, catch-up burst, or cross-row substitution.  The exact `[1,5,5,1]` theorem
is scoped to persistent demand in every row.
