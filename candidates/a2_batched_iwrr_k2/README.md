# A2 Batched-IWRR-K2 isolated candidate

Status: **local candidate functional GO; physical and widened-A7 integration
HOLD**.

This directory is additions-only and self-contained.  It does not modify or
instantiate the frozen common TB, manifests, or team RTL.  The synthesizable
candidate implements the N16 atomic K2 boundary frozen in [CONTRACT.md](CONTRACT.md).

## Result

The scheduler consumes the six ordered calendar batches

`(1,2), (0,1), (2,3), (1,2), (1,2), (1,2)`.

With every row persistently requesting, every six accepted cycles produce 12
distinct event grants with exact row count `[1,5,5,1]` and peak service of two
events/cycle.  Each row has a two-bit rotating source pointer.  The phase and
four pointers are the complete 11-bit scheduling state.

Sparse semantics are deliberately non-borrowing.  An empty scheduled row's
entitlement is waived, survivors are compacted in token order, and no debt or
return burst is created.  A nonempty batch changes state only on atomic ready;
an all-empty phase advances automatically.  This preserves drain without
claiming a weighted event ratio for rows that supply no events.

## Qualification

Run from the repository root:

```sh
candidates/a2_batched_iwrr_k2/run_all.sh
```

The command fails closed when Verilator or Yosys is unavailable.  Optional
overrides are `A2_K2_VERILATOR`, `A2_K2_YOSYS`, `A2_K2_YOSYS_LIB`, and
`A2_K2_A1_REPO`.  It reruns and byte-compares all committed evidence:

- eight Python contract tests;
- 1,572,864 exhaustive N16 bitmap/phase/uniform-pointer cases and all 64
  row-mask/pointer picker cases;
- 20,000 deterministic cycles of independent Python versus synthesizable RTL
  lockstep, including reset, sparse, full-demand, and stalls;
- seven model negative controls and five separately compiled RTL mutants;
- pinned generator-v4 full50 and capacity22 local replay;
- candidate-only Yosys generic LUT4 state/cell/depth proxy.

Canonical evidence is [qualification.json](results/qualification.json).  The
current Yosys 0.52 proxy is 11 state bits, 186 LUT4 cells, and 11 combinational
cell levels.  These are generic structural diagnostics, not physical PPA.

The frozen-v4 replay is intentionally performed by the independent model after
pinning the exact generator and manifests.  RTL equivalence is supported by the
separate random/directed lockstep, not by an RTL execution of all 72 traces.

## Explicit limits

- No widened low-pin A7 endpoint, serialization, lane backpressure adapter,
  timing closure, physical mapping, power, or PVT evidence is present.
- Atomic ready requires the request bitmap to remain stable throughout a stall.
- One pending bit per source cannot represent a second occurrence at an already
  pending source; frozen replay reports such occurrences as source overrun.
- The full cross-product of all 256 row-pointer vectors is not exhaustively
  enumerated with every N16 bitmap and phase.  The primitive picker is exhaustive,
  four uniform pointer rotations are exhaustive over bitmap/phase, and arbitrary
  pointer vectors occur in RTL lockstep.
- Exact `[1,5,5,1]` event service is asserted only under persistent demand in
  all four rows.  Sparse execution follows the waiver semantics above.
