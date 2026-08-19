# K2 single-edge mapped CDC/RDC diagnostic payload

`mapped_cdc_artifacts.tar` is a deterministic, uncompressed archive with
SHA-256:

```text
9c85f74d4fd399149891bf39c56674132c46a554a15baa3d4c00d60ea198b698
```

It contains, for A2 and A3, the exact Genus mapped netlist and SDC, Innovus
post-route netlist, selected timing/PPA/DRC/connectivity reports, artifact
ledger, and qualification, plus the diagnostic same-environment-snapshot
cohort. It excludes proprietary Liberty/LEF/QRC
bytes, tool binaries, licenses, environment variables, and license-server
coordinates.

The archive is evidence, not authority. Its qualifications explicitly say
`HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE`; the offline verifier preserves that
ceiling. Validate it with:

```sh
python3 -B contracts/redred_single_edge_mapped_cdc_rdc/verify_contract.py
```
