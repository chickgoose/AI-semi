# A2 W6 Fovea → A7 R1 executable reference

This candidate-owned test directory is an independent Python oracle for the
address-only scalar seam between:

- canonical 2026-08-09 Fovea, `WEIGHT=5`, source commit
  `0558c89285acd79f639d3e2c5e11fddb204d3e13`, blob
  `c57c2ffda5cb03ce0370b286f7460c356b429b68`; and
- A7 W5 R1 DDR endpoint commit
  `42377ca81340951bfcd453b3bd664e673091f9f3`.

It does not import, modify, or claim to qualify either owner's RTL. It freezes
the independently reconstructed behavioral boundary used for integration
reasoning.

## Modeled contract

`FoveaWeight5` reproduces the three `arbiter4_tree` instances, their nine
one-bit `arbiter2.last_gnt` states, the three-bit `round`, and registered scalar
`valid/address`. With all 16 requests continuously asserted, its first twelve
selected rows are:

```text
1 2 1 2 1 0  2 1 2 1 2 3
```

This is five center-class services followed by one peripheral-class service;
the independent intra-class tree arbiters make the twelve-service row count
`row0:row1:row2:row3 = 1:5:5:1`. It is not four independent row-weight counters.

`A7R1Endpoint` accepts one occurrence on every reference posedge with
`valid && ready`. The first edge after reset release only arms ready. A frame
commits before the next reference edge, becomes registered retirement there,
and is consumed by the always-ready synchronous consumer two cycles after
admission. There is no valid-edge detector, FIFO, payload, or address-derived
event reconstruction.

Reset is accepted only after the modeled frame/observer/consumer pipeline has
drained. The composed scenario drains completely before its second reset and
then sends a post-reset address to detect phantom stale completion.

The exact checker rejects dropped, duplicated, reordered, wrong-address, and
wrong-latency retirements. Dedicated mutations demonstrate fail-closed
drop/duplicate/reorder detection.

## Run

```sh
tests/a2_fovea_a7_reference/run_tests.sh
```

Success requires the executable JSON report, all unit tests, and final sentinel
`A2_FOVEA_A7_REFERENCE_PASS`. No simulator, common benchmark, team RTL, or
external repository is used or changed.
