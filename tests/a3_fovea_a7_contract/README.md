# W6 scalar FOVEA + A7 composition contract

This test-only checker pins the canonical scalar FOVEA bytes and the six A7 R1
endpoint source blobs at commit `42377ca81340951bfcd453b3bd664e673091f9f3`.
It verifies the zero-state composition seam, not physical feasibility and not a
Cluster2 bitmap composition.

Checked invariants:

- canonical FOVEA SHA-256, module, ports, `WEIGHT=5`, weighted-round expression,
  and registered scalar `valid` plus four-bit coordinate address;
- stateless current-result one-hot masking, live-request `source_ready` ACK, and
  direct FOVEA-address-to-A7-to-retire identity;
- no seam queue, sequential state, arbitration, payload/metadata reconstruction;
- exact A7 source SHA-256 values, active-low A7 reset, active-high FOVEA reset
  inversion, charged post-reset arming, and standard ready-valid launch.

Run against an external canonical FOVEA file and A7 repository:

```sh
python3 tests/a3_fovea_a7_contract/check_contract.py \
  --fovea /path/to/aer_tx16_trad_rowcol_fovea.v \
  --a7-repo /home/chickgoose/projects/a7
python3 -m unittest discover -s tests/a3_fovea_a7_contract -p 'test_*.py'
```

The checker emits a JSON receipt to stdout and exits 1 on every pin or invariant
failure. The seam has no buffering or backpressure compensation. Its composition
contract therefore relies on the common reset epoch: FOVEA cannot raise its
registered `valid` before A7 completes its one-edge arming, after which R1 remains
ready. Physical phase/STA/ICG/DDR claims remain outside this checker.
