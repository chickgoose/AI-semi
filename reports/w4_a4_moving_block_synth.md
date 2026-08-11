# W4 A3 Independent A4 Moving-Block Synthesis/Scaling Audit

Status: **local generic synthesis PASS; physical conclusion HOLD**, 2026-08-11

This is an independent, read-only audit of A4 commit
`850fbcfa4ad168b1250223610780f11378f6c391`.  It does not modify or own A4 RTL,
common RTL, manifests, or testbenches.  The machine-readable evidence is
`reports/w4_a4_moving_block_synth.json`.

## Frozen input and flow

- top: `a4_moving_block_tree`;
- sole filelist entry: `rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.sv`;
- RTL SHA256: `18e00a2acba587af7f81f2f1608268f4c37d9068a3e7e3f2b29611c4f8ea5677`;
- filelist SHA256: `d7a70ae9e7764e35b26618bdd0411f34c8d85d0ca01bf179423d25a3a8f2799e`;
- parameters: `{NUM_SOURCES=16/64, ADDR_WIDTH=32, SOURCE_WIDTH=4/6,
  MAX_ADVANCE=1/2}`;
- Yosys 0.52 SHA256:
  `30aa795bec7533dac08bad56309edb6ac70dd33f017c28082d3c1dae1012112f`;
- ABC 1.01 SHA256:
  `21869d0f63b6a2962ad7e54044e7a694f6cc392db6443ad7bf70cdb8ad6ca16a`;
- mapping: `synth -flatten -noabc`, then `abc -g simple`, `clean -purge`.

Yosys 0.52 cannot parse the RTL's unpacked input port, and its frontend warns
when async-reset unpacked state arrays are converted to registers.  The runner
therefore performs nine declaration-only rewrites from unpacked to packed
dimensions.  Every existing `[index]` remains unchanged; no state, selection,
ready/backpressure, buffering, or functional adapter is added.  The normalized
source SHA256 is
`f8225ab51572f90a0074e515716e4289483d022757e1668db0e1dcd155d922ed`.
The two MAX_ADVANCE variants use precisely this same normalized boundary and
source.

The run fails before publication on commit/source/filelist/tool/normalized
source mismatch, any Yosys warning, actual latch inference or latch cell,
implicit/unresolved objects, hierarchy/check failure, multiple drivers,
non-generic residual cells, or a combinational cycle.  All four runs passed
these gates.

## Mapped results

| N | advance | total cells | FF bits | comb cells | comb depth | max fanout data / all | unique-wire bits | data sink-pin wire proxy |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 1 | 6,467 | 1,162 | 5,305 | 13 | 42 / 1,823 | 6,998 | 11,607 |
| 16 | 2 | 11,474 | 1,162 | 10,312 | 21 | 39 / 1,824 | 12,005 | 21,533 |
| 64 | 1 | 29,830 | 5,016 | 24,814 | 18 | 55 / 7,744 | 31,945 | 53,674 |
| 64 | 2 | 51,132 | 5,016 | 46,116 | 25 | 48 / 8,720 | 53,247 | 96,356 |

`FF bits` counts mapped sequential Q bits; it exactly matches A4's structural
register formula and is identical between advance 1 and 2.  `comb depth` is
the longest mapped combinational-cell dependency chain between registered/IO
boundaries.  Data fanout excludes clock/reset nets; the much larger `all`
value exposes the unbuffered asynchronous-reset/clock distribution proxy.
Wire proxies are netlist connectivity counts, not routed length.

### Relative cost

| comparison | total cells | comb cells | comb depth | unique-wire bits | data sink-pin proxy |
| --- | ---: | ---: | ---: | ---: | ---: |
| N16 advance2 / advance1 | 1.774x | 1.944x | 1.615x | 1.715x | 1.855x |
| N64 advance2 / advance1 | 1.714x | 1.858x | 1.389x | 1.667x | 1.795x |
| advance1 N64 / N16 | 4.613x | 4.677x | 1.385x | 4.565x | 4.624x |
| advance2 N64 / N16 | 4.456x | 4.472x | 1.190x | 4.435x | 4.475x |

The synthesis supports the claim that MAX_ADVANCE=2 adds no architectural
register state, but it does not support treating the cost as merely a doubled
abstract child-check count.  It nearly doubles mapped combinational cells and
wire-load proxies.  The local one/two-edge movement bound also does not equal
whole-design mapped depth: depth rises from 13 to 21 at N16 and 18 to 25 at
N64.  N scaling is superlinear relative to the 4x source count for both
variants, partly because the stored source identity widens from four to six
bits.

## Decision and limitations

**HOLD for physical implementation.**  MAX_ADVANCE=2's measured common-workload
fill/recovery benefit must justify roughly 1.7x total generic cells, 1.8x
connectivity load, and deeper combinational logic.  This local result neither
rejects A4 functional correctness nor establishes timing closure, standard-cell
area, power, congestion, buffering, or routed wire length.  The N64 result is
a successful elaboration/mapping and a scaling warning, not server PPA.

Reproduction:

```sh
python3 scripts/w4_a4_moving_block_synth/run.py \
  --output reports/w4_a4_moving_block_synth.json
python3 -m unittest -v scripts.w4_a4_moving_block_synth.test_run
```
