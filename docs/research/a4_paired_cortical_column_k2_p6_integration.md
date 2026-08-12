# A4 Paired-Cortical-Column K2 to P6 digital integration

Status: **digital RTL GO; physical PPA and CDC/RDC HOLD**.

## Exact owner and endpoint boundary

The integrated scheduler is the existing A4 owner RTL from commit
`0e613b6933f1bb92e9b2f75b79a50663187f17d3`, Git blob
`b3810b2233fdd47a138c9dda1c182fd5ca0374c8`, SHA-256
`56bde1a765cd750e5b4581e51d90ec1cf6893bcea9cbe904b09aeeafe89a0185`.
The owner file is not copied or modified.  The full top instantiates that file
and the existing exact atomic P6 adapter directly.

A4 already contains the minimal state needed to freeze a blocked offer: one
valid bit and a 16-bit request snapshot, charged inside the owner.  The new
seam therefore adds no register, FIFO, lane-ready path, or policy state.  Its
queue-free `link_enable_i` gate drives owner `bundle_ready` only when P6 is
ready and simultaneously presents P6 with the legal invalid/count-zero shape
when disabled.  Yosys `scc -expect 0` closes the combinational-loop check.

## Policy preservation

The integration does not flatten A4 into a scalar priority wheel.  It retains
the four row-local column arbiters, six-phase paired calendar, rotating
fallback, bounded debt, and debt service implemented by the owner.  Persistent
full demand is checked for six consecutive atomic pair commits with aggregate
row service `[1,5,5,1]`.  This remains A4's aggregate paired policy and makes
no scalar-prefix equivalence claim.

## Qualification

Run:

```sh
VERILATOR=/tmp/a7-toolchain/usr/bin/verilator \
YOSYS=/tmp/a7-toolchain/usr/bin/yosys \
tests/a4_paired_cortical_column_k2_p6/run_all.sh
```

The additions-only test checks count 0/1/2, reset with live inputs, whole-
bundle stall and policy freeze, drain/reset/rearm, continuous `[1,5,5,1]`, P6
retirement order, source acknowledgement atomicity, and commit/retire event
conservation.  Five separately compiled synthesizable RTL mutations must fail:
A4 flat weight, A4 stall advance, A4 live-reset leakage, P6 pair swap, and P6
partial policy microsteps.  The runner also verifies the exact A4 SHA-256 and
that owner, common/shim, P6, testbench-common, constraints, and physical paths
have no working-tree modifications.

The P6 phase-related digital model is not physical closure.  Characterized
clock-gating/DDR cells, implementation timing, activity-based power, reset
recovery/removal, CDC/RDC signoff, and place-and-route evidence remain HOLD.
