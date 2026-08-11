# ECRF Wave-3 preregistration

Status: fixed before the exhaustive sweep and trace replay.

## Scope

ECRF is the only mechanism under test.  It does not use a pressure/phase mode,
calendar, rotor, arrival lock, lookahead policy, round-robin state, FIFO, or a
second candidate's selection algorithm.  The exact matcher in the Python tool
is an oracle used to prove that a local-reaction failure is real; it is never a
proposed datapath.

The sweep is fixed to:

- `N=16`;
- `K in {2,4}`;
- `B in [K,12]`;
- `d in [1,min(4,B)]`;
- 64 deterministic topology seeds per `(K,B,d)`; and
- at most `K` conservative reaction rounds.

One representative topology per Hall-feasible `(K,B,d)` point is exhaustively
checked.  The representative minimizes, in order, maximum cell fan-in,
source-to-cell plus cell-to-lane wire proxy, smallest peeling stopping-set
risk, and seed.

## Functional invariants

For every one of the 65,536 active-source masks and every lane-availability
mask, including the zero-available-lane control:

1. every grant names an active source and an available lane;
2. a source, intermediate cell, and lane each appear at most once;
3. `accepted <= min(popcount(active), popcount(lane_available), K)`;
4. `pending_after + accepted == active_before` (snapshot P-invariant);
5. a committed occurrence remains unique; intermediate proposals never count
   as acceptance; and
6. if an oracle matching of target cardinality exists, failure to reach that
   cardinality within `K` rounds is a recorded bounded-progress failure.

The topology must satisfy the truncated Hall condition for every source subset
of size at most `K`.  Peeling stopping sets (no degree-one neighboring cell),
reaction deadlocks, and capacity deadlocks are searched independently and the
smallest witnesses are retained.

## Frozen comparison proxies

For a flat replicated `K`-grant selector:

```text
wire_flat  = N*K
work_flat  = N*K
depth_flat = K*ceil(log2(N))
```

For ECRF:

```text
wire_ecrf  = N*d + B*K
work_ecrf  = Rmax*(2*N*d + B*K + B)
depth_ecrf = Rmax*(ceil(log2(max_cell_fanin))
                   + ceil(log2(d)) + ceil(log2(B)) + 1)
```

These are structural comparison proxies, not synthesized area or timing.

## Pre-RTL GO gate

A `(K,B,d,seed)` point is GO only if all conditions hold:

- zero conservation, uniqueness, legality, P-invariant, Hall, reaction
  deadlock, and bounded-capacity failures;
- full50 and capacity22 replay have zero accepted/delivered mismatch;
- fixed-window delivered events are no lower than flat K-grant;
- source overrun is no higher than flat K-grant;
- p99 end-to-end latency is no more than one cycle above flat K-grant;
- `wire_ecrf <= 0.85*wire_flat`;
- `work_ecrf <= work_flat`; and
- `depth_ecrf <= depth_flat`.

RTL is permitted only for a point passing every gate.  A functional PASS with
a proxy failure remains HOLD and must not produce candidate RTL.
