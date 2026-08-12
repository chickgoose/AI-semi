# A7 K2 same-flow structural audit

Status: **read-only generic structural proxy complete**.  Cross-policy semantic
equivalence, Liberty area/timing, physical fanout/wire length, power, and P&R
remain **HOLD**.

This directory is an isolated A7 audit artifact.  It does not edit or import
candidate RTL into the current worktree, and it does not modify common, team,
frozen benchmark, or scheduler files.  `run_audit.py` reads the three exact Git
blobs, writes all working files under a temporary directory, and synthesizes
the same temporary boundary for every candidate.

## Frozen candidate identities

| Candidate | Exact evaluated commit | RTL origin | RTL SHA-256 | Semantic grade |
| --- | --- | --- | --- | --- |
| A3 exact scalar prefix | `29a5003bb47c9c502a3bec9a727de2ed14afcfeb` | `632e68d247ec36a35b62dbd5c100b0a23d47cf7b` | `bd00ade6ebd5f6c5e03ff356393a59f1baf6d890cfb3809a10bf0cda3bb1b0d9` | exact scalar-prefix K2 |
| A2 batched IWRR | `d74ff962aaf07c5209f1a1d1c69832735c654a0d` | same | `800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d` | weighted aggregate, not A5 scalar prefix |
| A4 paired cortical column | `0e613b6933f1bb92e9b2f75b79a50663187f17d3` | same | `56bde1a765cd750e5b4581e51d90ec1cf6893bcea9cbe904b09aeeafe89a0185` | aggregate-only, not A5 scalar prefix |

The A3 follow-up commit is a direct descendant of its RTL-origin commit and
does not change the evaluated RTL blob.  The script verifies ancestry, full
commit identities, and every RTL SHA before synthesis.

A4 commit `2884eb831cc6437efaa52bcd21929ab288f3d265` is explicitly
superseded: live request inputs could leak an offer while reset was asserted.
Only follow-up `0e613b6933f1bb92e9b2f75b79a50663187f17d3` is stored as the
canonical A4 result.  Its reset-quiet gating is included in the common output
cone and its superseded RTL is synthesized only ephemerally to calculate the
recorded gating delta.

## Identical boundary and flow

Only this atomic scheduler boundary is observed:

```text
clk, rst, pending[15:0], bundle_ready
    -> grant_count[1:0], grant_addr0[3:0], grant_addr1[3:0]
```

Candidate-only `grant_bitmap`, `source_ready`, and `drain_idle` outputs and all
downstream link adapters are unobserved and optimized away.  A4's native
active-low reset is normalized to the common active-high reset; its inverter
remains charged.

The exact common Yosys recipe is:

```text
read_verilog -sv -DSYNTHESIS
hierarchy -check -top k2_common_boundary
proc; flatten; opt
memory_map; opt
setundef -zero; opt
techmap; opt
abc -g simple
clean; check; stat
```

Canonical results use:

```text
Yosys 0.52 (git sha1 fee39a3284c90249e1d9684cf6944ffbbcbb8f90)
yosys executable SHA-256: 30aa795bec7533dac08bad56309edb6ac70dd33f017c28082d3c1dae1012112f
yosys-abc SHA-256:       21869d0f63b6a2962ad7e54044e7a694f6cc392db6443ad7bf70cdb8ad6ca16a
```

The canonical executable is `/tmp/a7-toolchain/usr/bin/yosys`; each JSON also
records the resolved executable, data directory, binary identities, and full
flow.  `memory_map` is intentional: the rejected exploratory `memory` flow
turned A2's combinational calendar case into a non-architectural two-bit ROM
read register.  The common `memory_map` flow preserves A2's documented 22-bit
state and is applied without exception to all three candidates.

Canonical artifact SHA-256 identities are:

```text
f482d0637696d7a76e684573e3d7c97e6aa7a287229430dfe7761b6c05142258  a3_exact_scalar_prefix.json
e13434d16d651552787ecf09feeb74a47567749b3e42e8ce372e7c3ad634a3a4  a2_batched_iwrr.json
f50cf4b2d2ac68c076a82c6048a99b28a905e0abc1c05fb32faf59688faa3854  a4_paired_cortical_column.json
```

## Same-flow result

Lower is better for every numeric proxy below.

| Candidate | Generic cells | State generic/mapped | Mapped cells/comb | Depth | Fanout max/p95 | Nets fanout >=16 | Sink-pin wire proxy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A3 | 283 | 34 / 26 | 644 / 618 | 42 | 20 / 5 | 3 | 1,225 |
| A2 | 210 | 22 / 22 | 720 / 698 | 52 | 12 / 7 | 0 | 1,415 |
| A4 | 629 | 49 / 49 | 1,832 / 1,783 | 101 | 32 / 6 | 11 | 3,685 |

Depth is mapped combinational cell levels from primary inputs or mapped flop
outputs to common outputs or flop data/enable inputs.  Fanout counts mapped
data/enable sink pins per net; clock/reset/set sinks and the clock/reset primary
nets are excluded.  The wire proxy is the total mapped combinational and flop
data/enable input-pin bits.  It is connectivity, not routed wire length.

A3 and A2 form the structural Pareto set.  A3 has fewer mapped cells, lower
depth, lower p95 fanout, and lower wire proxy; A2 has less state and lower
maximum fanout.  A3 structurally dominates A4 in every table column.

### A3 state reduction: 34 to 26

The generic post-process netlist preserves the documented 34 bits: 12
committed policy bits, 12 saved post-bundle policy bits, and 10 registered
bundle bits.  Fixed `CENTER_MASK=0110` and `PERIPH_MASK=1001` mean two leaf-pair
bits in each center/peripheral arbiter state cannot affect a winner.  Uniform
`techmap; opt` therefore removes four committed and four saved post-bundle bits
while retaining all 10 registered bundle bits.  The mapped proxy is 26 flops.
This is one common-flow optimization, not a candidate-specific recipe.

### A4 reset-live gating cost

Relative to superseded `2884eb8` under the same temporary wrapper and flow,
the final RTL adds two generic cells, both muxes, with 20 additional mux data
input bits and two select bits.  State remains 49 bits.  ABC rewrites the whole
cone and happens to produce 21 fewer mapped simple-gate cells, eight fewer
levels, and 63 fewer sink-pin inputs.  Those negative mapped deltas are mapping
interactions, not evidence that reset gating has negative physical cost.  The
canonical A4 JSON records every final-minus-superseded delta, the old and new
full commit/RTL identities, and `superseded_result_saved_as_final=false`.

## Warning classification

The audit fails on any unclassified warning or if `check` does not report zero
problems exactly once.

| Candidate | Memory-array-to-register | ABC combinational-network | Unclassified |
| --- | ---: | ---: | ---: |
| A3 | 0 | 1 | 0 |
| A2 | 3 | 1 | 0 |
| A4 | 6 | 1 | 0 |

The memory messages report deterministic unpacked-array/register expansion.
The ABC message states that ABC received a combinational cone.  Neither is a
Liberty, timing, latch, loop, or failed-check warning.

## Reproduction and self-test

Generate an untracked result set into a new directory:

```sh
python3 audits/a7_k2_same_flow_structural/run_audit.py \
  --yosys /tmp/a7-toolchain/usr/bin/yosys \
  --output-dir /tmp/a7-k2-same-flow-result
```

The release self-test runs all three syntheses twice in independent temporary
directories, compares the two generated result sets byte-for-byte, and then
compares them with the three committed canonical JSON files:

```sh
python3 audits/a7_k2_same_flow_structural/run_audit.py \
  --yosys /tmp/a7-toolchain/usr/bin/yosys \
  --self-test
```

Expected marker:

```text
A7_K2_SAME_FLOW_SELF_TEST_PASS candidates=3 runs=2 two_run_byte_identity=1 committed_byte_identity=1
```

## Qualification boundary for A1

The original candidate receipts used incompatible LUT/gate recipes and output
boundaries, so their raw cell/depth numbers remain forbidden for cross-candidate
ranking.  Only the three JSON files in this audit are directly comparable.

The policies are not semantically equivalent: A3 claims exact scalar-prefix
K2, A2 uses a different IWRR calendar, and A4 is aggregate-only.  Structural
Pareto results cannot upgrade A2 or A4 to A5 scalar-prefix equivalence and
cannot select a scheduler before the required semantic grade is fixed.
Technology area, Fmax, fanout repair, routing, energy, and physical PPA remain
HOLD for all three.
