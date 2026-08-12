# A5 fovea + A7 structural comparison

This test owns no candidate RTL. Its decision pair compares the exact owner
composition from `d3c52f0`/`b520125` against an audit-only parallel reference.
Both use the canonical 16-source, `WEIGHT=5` fovea and retain identical
`source_valid[15:0]`, `source_ready[15:0]`, current-result mask, protocol fault,
full drain, clocks/reset, launch qualifier, TX/RX, retire observer and final
retire boundary. Only the A7 DDR2 versus parallel4 endpoint encoding changes.

The fixtures are byte-pinned copies of the uncommitted canonical Ganghee source
snapshot. The A7 evidence script is verified from commit `0f2db4b460fab0e45c4c22756209cad400789944`;
all endpoint RTL is materialized from commit `42377ca81340951bfcd453b3bd664e673091f9f3`.
The owner commits `d3c52f01c91be65b75c6e5fbb6419b711de6145a` and
`b5201254bceb39b3563370567355efe17a3b5e16` must contain the identical pinned
composition blob. Every source is checked before synthesis; mutable A7 worktree
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

| composition | boundary | link pins | state bits | reg/latch cells | charged cells | excluded `$scopeinfo` | operator/generic depth |
|---|---|---:|---:|---:|---:|---:|---:|
| owner fovea + DDR2 | owner semantics | 3 | 37 | 24 | 198 | 19 | 40 / 35 |
| owner fovea + parallel4 | owner semantics | 5 | 35 | 23 | 196 | 17 | 40 / 35 |
| old fovea + DDR2 | **legacy mismatch** | 3 | 37 | 24 | 150 | 19 | 28 / 33 |
| old fovea + parallel4 | **legacy mismatch** | 5 | 35 | 23 | 148 | 17 | 28 / 33 |

The owner wrapper contributes zero sequential state in both decision tops and
has the same 77 pre-flatten combinational operator cells. DDR therefore saves
two link pins but costs two state bits and two charged generic cells at unchanged
owner-boundary proxy depth. The old 150/148 rows omitted source acceptance,
fault, and full-drain semantics; they are retained only as a legacy boundary
mismatch and cannot support the decision. All numbers are checked, not merely
printed.
