# W7-A2 scalar Fovea Xcelium qualification

This directory is an A2-owned, read-only consumer of the canonical Ganghee
scalar Fovea and the frozen address-only common benchmark.  It contains no
candidate RTL and does not modify either source repository.

## Frozen identities

`contract.json` binds the exact A1 integration commit, twelve required Git
blobs, the scalar top `aer_tx16_trad_rowcol_fovea`, parameter `WEIGHT=5`, and
the 50/22 official generator-v4 suite identities.  `run_w7.py` embeds the
contract's SHA256, reads files using `git show COMMIT:path`, and therefore does
not consume dirty or untracked A1 files.

The canonical scalar native interface is `clk, rst, req[15:0] -> valid,
addr[3:0]`.  The compile order is `arbiter2.v`, `arbiter4_tree.v`, then the
Fovea top (Verilog elaboration is order-independent).  The common native
binding is the frozen, zero-functional-state observation seam; the DUT itself
owns arbitration and its native 4-bit address is the complete event identity.

## Commands

Local immutable-blob check (does not need Xcelium):

```sh
python3 tests/w7_scalar_fovea_xcelium/run_w7.py verify \
  --a1-repo /home/chickgoose/projects/a1
python3 -m unittest -v tests/w7_scalar_fovea_xcelium/test_w7.py
```

On the approved Xcelium host, use a new attempt root; never point at an
existing integration result directory:

```sh
python3 tests/w7_scalar_fovea_xcelium/run_w7.py run \
  --a1-repo /path/to/a1-with-commit-2a3a3be \
  --xrun /tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun \
  --out /tmp/w7-a2-scalar-fovea.$USER.$$
```

The run performs one fresh elaborate, `basic_reset_drain`, all 50 full50
traces, and the exact 22 capacity traces (72 trace runs total; capacity22 is
deliberately rerun rather than inferred).  Every simulation must exit zero,
print its exact `AER_CLEAN_TEST_PASS` sentinel, and create both metric files.
The runner returns 2 for provenance/output/result failure and 3 when Xcelium
is unavailable; tool absence is never a skip or PASS.

## Scope of the current evidence

The checked-in evidence establishes immutable source/common provenance and
executable fail-closed preparation.  The local host has no `xrun`, so no new
Xcelium functional PASS is claimed here.  Completion remains
`PENDING_HEAD_XCELIUM` until the command above creates `receipt.json` with
reset PASS and suite counts 50 and 22.
