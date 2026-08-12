# A5 fovea + A7 structural comparison

This test owns no candidate RTL. It compares two complete compositions at the
same external boundary: the canonical 16-source, `WEIGHT=5` direct-coordinate
fovea followed by either the A7 R1 DDR2 endpoint or its parallel4 reference.
Both retain the same request input, clocks/reset, launch qualifier, TX/RX,
retire observer, retire address/valid, drain guard, and visible physical link.

The fixtures are byte-pinned copies of the uncommitted canonical Ganghee source
snapshot. The A7 evidence script is verified from commit `0f2db4b460fab0e45c4c22756209cad400789944`;
all endpoint RTL is materialized from commit `42377ca81340951bfcd453b3bd664e673091f9f3`
and checked against its SHA-256 registry before synthesis. Mutable A7 worktree
files are never read.

Run:

```bash
A5_FOVEA_A7_OUT=/new/output/path \
  A7_REPO=/home/chickgoose/projects/a7 \
  tests/a5_fovea_a7_structural/run.sh
```

The output reports physical link pins, state bits, register/latch cells,
functional cells after excluding only Yosys `$scopeinfo`, and combinational
depth both before and after generic technology mapping. Missing Yosys, missing
Git objects, any blob mismatch, an existing output path, synthesis/check
failure, malformed stats, or missing depth observations returns nonzero. The
`--verify-only` mode checks provenance but deliberately emits no structural PASS
sentinel.

This is a generic structural proxy. It is not timing closure, characterized
DDR/ICG mapping, physical pin cost, power, or functional no-loss qualification.
The native fovea has no ready input; exposing endpoint ready does not add
backpressure to it.

With portable Yosys 0.52 (`fee39a3284c90249e1d9684cf6944ffbbcbb8f90`),
the frozen structural contract is:

| composition | link pins | state bits | reg/latch cells | charged cells | excluded `$scopeinfo` | operator/generic depth |
|---|---:|---:|---:|---:|---:|---:|
| fovea + DDR2 | 3 | 37 | 24 | 150 | 19 | 28 / 33 |
| fovea + parallel4 | 5 | 35 | 23 | 148 | 17 | 28 / 33 |

Thus DDR saves two link pins but costs two state bits and two generic functional
cells at unchanged proxy depth. These numbers are checked, not merely printed.
