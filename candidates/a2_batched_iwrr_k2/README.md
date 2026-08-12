# A2 Batched-IWRR-K2 isolated candidate

Status: **local candidate functional GO; physical and widened-A7 integration
HOLD**.

This directory is additions-only and self-contained.  It does not modify the
frozen common TB, manifests, or team RTL.  The normalized runner compiles the
common TB in place without changing it.  The synthesizable candidate implements
the N16 atomic K2 boundary frozen in [CONTRACT.md](CONTRACT.md).

## Result

The scheduler consumes the cyclic event-token calendar

`[1,2, 0,1, 2,3, 1,2, 1,2, 1,2]`.

With every row persistently requesting, every six accepted cycles produce 12
distinct event grants with exact row count `[1,5,5,1]` and peak service of two
events/cycle.  Each row has a two-bit rotating source pointer.  The four-bit
token cursor and four pointers are 12 policy bits.  Ten more bits hold a
backpressured offer (valid, one/two count, and two addresses), for 22 minimal
implemented state bits; the held bitmap is derived from those addresses.

Sparse execution tries the token's preferred row and then the other rows in
cyclic order.  Every selected event consumes exactly one token; an empty system
consumes none.  Fallback creates no debt, credit, or return burst.  The exact
weighted ratio is claimed only for persistent demand in every row.

The sole boundary handshake is atomic `bundle_ready`.  A nonzero offer has
count one or two plus ordered addresses.  A stalled offer is internally held
stable, and all valid addresses commit together.  Partial-lane backpressure is
permitted only behind a separate buffered link adapter and never advances this
scheduler's policy.

## Frozen RETIRE_LANES=2 normalized binding

`rtl/a2_batched_iwrr_k2_normalized.sv` promotes the native N16 scheduler onto
the common two-lane ready/valid seam.  The separately instantiated
`a2_k2_ordered_link_adapter` is charged DUT RTL: for the default 16-bit event
identity it contains 42 state bits (two-bit occupancy plus two ordered copies
of `{event[15:0], source[3:0]}`).  The interface binding itself is storage-free.

The scheduler still has exactly one acceptance signal.  Its `bundle_ready` is
the link's complete-offer capacity result, and `source_ready` is the exact
native grant bitmap only on an atomic 1- or 2-entry commit.  A two-entry offer
does not split when only one link slot is free.  On the retire side, a blocked
head hides the younger entry; head-only retirement compacts it to lane 0, and
both lanes are exposed together only when both are ready.  Partial drain
changes link state only and never advances IWRR policy.

The wrapper gates ready/valid during active-low normalized reset and reports
`drain_idle` only when both native request/hold state and the charged link are
empty.  It is fixed to `NUM_SOURCES=16`, `RETIRE_LANES=2`, and four-bit source
identity; elaboration fails closed for another boundary.

The isolated normalized suite runs directed count0/1/2, back-to-back,
partial-ready, hold/order, exact source-ready, reset/drain, and conservation
checks, then six frozen common-TB scenarios and a Yosys structural check:

```sh
candidates/a2_batched_iwrr_k2/run_normalized.sh
```

## Qualification

Run from the repository root:

```sh
candidates/a2_batched_iwrr_k2/run_all.sh
```

The command fails closed when Verilator or Yosys is unavailable.  Optional
overrides are `A2_K2_VERILATOR`, `A2_K2_YOSYS`, `A2_K2_YOSYS_LIB`, and
`A2_K2_A1_REPO`.  It reruns and byte-compares all committed evidence:

- nine Python contract tests;
- 3,145,728 exhaustive N16 bitmap/cursor/uniform-pointer cases and all 64
  row-mask/pointer picker cases;
- 20,000 deterministic cycles of independent Python versus synthesizable RTL
  lockstep, including reset, sparse, full-demand, and stalls;
- seven model negative controls and six separately compiled RTL mutants;
- pinned generator-v4 full50 and capacity22 local replay;
- candidate-only Yosys generic LUT4 state/cell/depth proxy.

Canonical evidence is [qualification.json](results/qualification.json).  The
current Yosys 0.52 proxy is 22 state bits, 395 cells including 373 LUT4 cells,
and 28 combinational cell levels.  These are generic structural diagnostics,
not physical PPA.

The optional [A5 compatibility note](A5_COMPATIBILITY.md) and
`tools/run_a5_schema_trace.py` produce schema-correct A5 `41c425b` evidence
through a separate ordered link adapter.  They also document the unavoidable
semantic HOLD against A5's different frozen scalar wheel; the adapter does not
rewrite scheduler winners.

The frozen-v4 replay is intentionally performed by the independent model after
pinning the exact generator and manifests.  RTL equivalence is supported by the
separate random/directed lockstep, not by an RTL execution of all 72 traces.

## Explicit limits

- No widened low-pin A7 endpoint, serialization, timing closure, physical
  mapping, power, or PVT evidence is present.  The normalized lane adapter has
  local RTL simulation and generic synthesis checks only.
- Held offer payload and policy are stable through backpressure, even if `req`
  changes; protocol correctness still requires offered sources to remain
  pending until the atomic commit.
- One pending bit per source cannot represent a second occurrence at an already
  pending source; frozen replay reports such occurrences as source overrun.
- The full cross-product of all 256 row-pointer vectors is not exhaustively
  enumerated with every N16 bitmap and cursor.  The primitive picker is exhaustive,
  four uniform pointer rotations are exhaustive over bitmap/cursor, and arbitrary
  pointer vectors occur in RTL lockstep.
- Exact `[1,5,5,1]` event service is asserted only under persistent demand in
  all four rows.  Sparse execution follows the cyclic fallback semantics above.
