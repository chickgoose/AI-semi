# W7 characterized ICG and hold-ECO feasibility

This is a local-only design note above `fce278a`.  The failed f786982 root
remains HOLD.  The staging bundle now selects Candidate A (`TLATNCAX2`), but
that selection is not physical closure and cannot authorize a server run.

## Read-only evidence

The preserved f786982 Innovus log identifies these usable cells from
`slow_vdd1v0_basicCells.lib`:

- no-test clock gates: `TLATNCAX2`, `TLATNCAX3`, `TLATNCAX4`, `TLATNCAX6`,
  `TLATNCAX8`, `TLATNCAX12`, `TLATNCAX16`, `TLATNCAX20`;
- test clock gates: `TLATNTSCAX2`, `TLATNTSCAX3`, `TLATNTSCAX4`,
  `TLATNTSCAX6`, `TLATNTSCAX8`, `TLATNTSCAX12`, `TLATNTSCAX16`,
  `TLATNTSCAX20`;
- delay cells: `DLY1X1`, `DLY1X4`, `DLY2X1`, `DLY2X4`, `DLY3X1`,
  `DLY3X4`, `DLY4X1`, `DLY4X4`;
- usable buffers/inverters include `BUFX2`, `CLKBUFX2`, `INVXL`, `INVX1`,
  and `CLKINVX1`.

Innovus 23.14 explicitly classified both TLAT families as clock gates, while
the f786982 netlist contained zero recognized ICGs.  Hold optimization used
12 `DLY1X1`, one `DLY2X1`, and three `CLKBUFX2` cells.  It reported that the
remaining reg2cgate net could not be fixed because it was a clock net.

The GPDK045 cell interfaces used by the two candidates are:

| cell | inputs | output | characterized relationship |
| --- | --- | --- | --- |
| `TLATNCAX2` | `CK`, `E` | `ECK` | clock propagation `CK` to `ECK`; clock-gating setup/hold checks from `E` to the closing `CK` edge |
| `TLATNTSCAX2` | `CK`, `E`, `SE` | `ECK` | the same clock/gating arcs for `E`, plus test-enable gating arcs for `SE` |
| `DLY1X1`, `DLY2X1` | `A` | `Y` | positive-unate combinational minimum/maximum delay `A` to `Y` |
| `BUFX2`, `CLKBUFX2` | `A` | `Y` | positive-unate combinational delay `A` to `Y` |
| `INVXL`, `INVX1`, `CLKINVX1` | `A` | `Y` | negative-unate combinational delay `A` to `Y` |

The `TLATNTSCAX2` `CK/E/SE/ECK` interface is also documented by Cadence in
its GPDK045 clock-gating guidance:
<https://community.cadence.com/cadence_technology_forums/f/digital-implementation/36152/rtl-compiler-technology-transformation>.

Local evidence hashes are: Genus log
`4c2ccf17ccf4a24deaccbdec1bf93ef42d01a8a7216002207bf477499391aa82`,
Innovus log
`0c765440c91ae01f8fcd1810f7c72201fdba54bdbd8b15067526f2231c5cfa77`,
reg2cgate report
`6bf47e496c35b5cdc3e3f37334743fef92d11f4ff3153fcb5ecfce3737437f36`,
and reg2reg report
`cfa9344b1123ad4c6c09c9a7fd92aee031f8aa26eaf7ccb160dd6089f2d5a9cd`.

The actual Liberty body and f786982 mapped Verilog were not present in the
local audit extraction.  Therefore numerical Liberty tables are deliberately
not claimed as locally reverified.  The preserved Genus/Innovus logs do prove
that Innovus 23.14 recognized the `TLATNCA*` and `TLATNTSCA*` families as
clock gates, while the failed mapped implementation used `TLATXL+AND3X1` and
contained no recognized ICG.  A future authorized activation must hash the
server Liberty and require the generated mapping reports to show exactly one
`TLATNCAX2`, zero `TLATNTSCAX2`, and the same inventory in Innovus.  The
qualifier now rejects a missing/substituted ICG at either stage.

## Selected Candidate A: no-test ICG

`a7_r1_icg_boundary_tlatnca.sv` instantiates `TLATNCAX2` with
`CK=clock_i`, `E=enable_i & rst_n`, and `ECK=clock_o`.  It has no scan/test
wire.  It is selected because this flow explicitly excludes scan mapping and
has no test-enable contract.  Candidate B remains an unselected fallback.

## Candidate B: test-capable ICG

`a7_r1_icg_boundary_tlatntsca.sv` instantiates `TLATNTSCAX2` with the same
functional pins and ties `SE=0`.  It preserves the three-wire functional
boundary and provides a fallback when the mapping flow requires the
test-capable ICG family.

Both candidates keep reset release on the ICG enable cone.  The 13 ns release
phase occurs one nanosecond after the sample falling edge and while the sample
clock is low.  The existing reset input delay, clock-gating check, ref/link
recovery/removal metrics, and sample setup/hold metrics remain mandatory.
Neither candidate adds a reset false path or gates the ICG output with a new
AND cell.

### Reset-architecture risk that remains

`TLATNCAX2` has no asynchronous reset pin.  Feeding `rst_n` through `E`
preserves the legal t1-assert/t29-release smoke waveform because both events
occur while `sample_clk` is low.  It is not equivalent to the owner boundary
for an assertion that occurs while `sample_clk` is high: the owner output AND
immediately truncates `clock_o`, whereas the ICG cannot latch `E=0` until the
clock returns low.  A local negative fixture demonstrates exactly that
`owner_clock_o=0` while candidate `clock_o=1` after high-phase assertion.

Consequently the selected staging candidate is valid only under the frozen
reset phase contract.  If asynchronous assertion at an arbitrary phase is a
required external contract, Candidate A is rejected and the boundary must be
redesigned with a reset-capable characterized gate or explicit reset
synchronizer/arming scheme.  Reset timing coverage, the 0.500 ns uncertainty,
and ref/sample/generated-domain path-count gates may not be removed to hide
this risk.

Local functional stubs model only latch-low clock behavior.  The full DDR and
parallel owner/staged/candidate traces are cycle- and edge-identical, including
reset and drain observations.  This proves the legal contract behavior, not
the uninspected numerical Liberty timing.

## Targeted reg2reg minimum-delay ECO plan

1. Preserve the 0.500 ns uncertainty and all reset constraints.
2. Target only the two failing data paths:
   - `canonical_fovea_col_arb_pair_lo_last_gnt_reg/Q` through the arbitration
     feedback cone to the same register's D pin (-0.027 ns);
   - `endpoint_retire_observer_seen_toggle_o_reg/Q` through the XOR/buffer
     cone to `endpoint_retire_observer_retire_valid_o_reg/D` (-0.004 ns).
3. Prefer one `DLY1X1` on each affected data branch, placed near the launch
   branch before reconvergence.  Allow `DLY2X1` only if extracted min delay
   remains insufficient.  Do not add delay to a clock or reset net.
4. Re-extract RC, then require zero setup, hold, recovery, and removal
   violations.  Check both endpoints explicitly and verify that the ECO did
   not alter the other fanouts of `last_gnt` or `seen_toggle`.
5. Repeat the exact owner/staged/mapped handshake scoreboard before physical
   qualification.  Any Boolean, reset, scan, or three-wire interface change
   rejects the ECO.

The selected ICG boundary removes the discrete `TLATXL`-Q to `AND3X1` self gating
check that produced -0.413 ns.  Actual closure remains unproven until one
candidate maps to exactly one recognized ICG and the post-route gating check
is nonnegative.
