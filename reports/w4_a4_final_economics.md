# W4 A3 Final A4 Moving-Block Economics Audit

Status: **six-way generic synthesis PASS; economics GO FAIL / HOLD**, 2026-08-11

This independent audit reads only pinned git objects from A4 commit
`41f239dad4a342277f33d94bb3ed3db53e3497e0`.  It compares the W3
`MAX_ADVANCE=1` reference, frozen `MAX_ADVANCE=2`, and selected
`shared_clearance_local_enable` at N16 and N64.  A4/common source is not
modified or copied into the commit.

## Identical comparison contract

All six cases have the same logical boundary: clock/reset, N source
valid/ready bits, N independent 32-bit address-only events, and one
ready/valid retire event/source.  Declaration-only normalization packs the
unpacked dimensions and expresses the W3 event array as the identical flat
`N*32` bus used by W4.  It adds no state or function.  After parameterization,
the selected wrapper is renamed to the common mapped top
`a4_moving_block_tree`; flattening removes the wrapper.

Every case uses the same pinned local tools and pass sequence:

```text
Yosys 0.52 fee39a3284c90249e1d9684cf6944ffbbcbb8f90
synth -top a4_moving_block_tree -flatten -noabc
delete t:$scopeinfo
abc -g simple
clean -purge; check -assert; stat -json; ltp -noff; write_json
```

The JSON receipt freezes the two RTL/filelist objects, A4 functional receipts,
normalized-source hashes, all six parameter sets and canonical recipe hashes,
Yosys/ABC/Tcl hashes, and all six variant/N run identities.  Any changed object, rewrite count,
tool, warning, latch, unresolved hierarchy/cell, residual process/memory,
multiple driver, combinational cycle, or count inconsistency fails before
atomic report publication.

## Generic mapped results

| N | design | cells | seq | comb | depth | nets / bits | data fanout max / p95 / >=16 | data sink-pin proxy |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | MAX1 | 6,467 | 1,162 | 5,305 | 13 | 4,107 / 6,998 | 42 / 2 / 91 | 11,607 |
| 16 | MAX2 frozen | 11,474 | 1,162 | 10,312 | 21 | 9,114 / 12,005 | 39 / 2 / 168 | 21,533 |
| 16 | selected | 7,469 | 1,162 | 6,307 | 23 | 5,080 / 8,001 | 39 / 2 / 137 | 16,081 |
| 64 | MAX1 | 29,830 | 5,016 | 24,814 | 18 | 19,712 / 31,945 | 55 / 3 / 398 | 53,674 |
| 64 | MAX2 frozen | 51,132 | 5,016 | 46,116 | 25 | 41,014 / 53,247 | 48 / 2 / 719 | 96,356 |
| 64 | selected | 32,620 | 5,016 | 27,604 | 31 | 22,377 / 34,736 | 66 / 2 / 566 | 70,060 |

Sequential state is identical in all designs.  The selected implementation
maps 1,116/1,162 N16 bits and 4,826/5,016 N64 bits to generic DFFE cells, so its
eventual physical value remains library-dependent.

### Selected versus frozen MAX2

- cells: -34.91% N16, -36.20% N64;
- comb cells: -38.84%, -40.14%;
- net bits: -33.35%, -34.76%;
- data sink-pin proxy: -25.32%, -27.29%;
- depth: **+9.52%** (21 to 23), **+24.00%** (25 to 31);
- data max fanout: equal at N16, **+37.50%** (48 to 66) at N64.

Thus selected is a large area/connectivity reduction, but is not a strict
generic Pareto replacement for MAX2 in this exact common flow.  This differs
from A4's earlier local pass sequence, which reported no selected depth
regression.  The difference is flow sensitivity, not a functional mismatch;
the pinned lockstep evidence remains PASS.

### Selected versus W3 MAX1

- cells: +15.49% N16, +9.35% N64;
- comb cells: +18.89%, +11.24%;
- depth: +76.92%, +72.22%;
- net bits: +14.33%, +8.74%;
- data sink-pin proxy: +38.55%, +30.53%;
- data max fanout: -7.14% at N16, +20.00% at N64.

## Functional/economic gate

Pinned A4 evidence proves selected is cycle-exact to frozen MAX2 on all 72
generator-v4 traces plus 2,982 N16/N64 stall/reset cycles.  It therefore
inherits MAX2's delta relative to MAX1:

| suite | accepted delta | overrun delta | acceptance gain | p99 |
| --- | ---: | ---: | ---: | ---: |
| full50 | +41 | -41 | +0.0491% | 46 to 47 |
| cap22 | +35 | -35 | +0.0815% | 46 to 47 |

Two gates are intentionally separate:

1. A MAX2 structural replacement must be exact-equivalent, strictly smaller,
   and non-worse in every reported N16/N64 proxy.  **FAIL** because selected
   depth and N64 fanout regress.
2. An economic upgrade over MAX1 must add service without p99 or generic-proxy
   regression.  **FAIL** because p99 rises one cycle and most cost/depth/net
   proxies rise.  No arbitrary exchange rate converts 76 events into area or
   timing value.

**Decision: HOLD__MAX2_PARETO_AND_MAX1_ECONOMIC_GATES_FAIL.**  Selected remains
the area/connectivity-reduced MAX2 implementation worth physical comparison,
but it is not a same-flow generic Pareto replacement.  This evidence also does
not economically justify choosing MAX2 semantics over MAX1 and does not
establish physical PPA.

Reproduction:

```sh
python3 scripts/w4_a4_final_economics/run.py \
  --output reports/w4_a4_final_economics.json
python3 -m unittest -v scripts.w4_a4_final_economics.test_run
```
