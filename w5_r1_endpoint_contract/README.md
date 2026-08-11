# W5 candidate-neutral R1 full-endpoint contract

This A2-owned package freezes an executable contract for an address-only N16
core-to-DDR-to-retirement endpoint at link ratio R1.  It is a contract-checker
self-test, not candidate qualification and not PPA evidence.

Run:

```sh
bash w5_r1_endpoint_contract/run_tests.sh
```

The runner executes the golden state machine, exhaustive address/pair checks,
continuous-valid and stalled-valid cases, reset/mid-frame checks, and mutation
tests.  It also rejects any W5 diff to common TB, frozen manifests, or candidate
RTL relative to its creation base.

The contract was cross-checked against exact A7 endpoint commit
`42377ca81340951bfcd453b3bd664e673091f9f3`.  Superseded `ca1a209` is not bound
evidence because its drain and synchronous-consumer accounting were incomplete.
The final cross-check freezes reset arming, six-bit observer, launch/pending-valid
drain guards, +1 output availability, +2 synchronous consumption, and the
DDR/parallel `3 pins, 20 bits, 29 cells` versus `5 pins, 18 bits, 27 cells`
charged boundary.  Compatibility is field-specific; A7 physical and adoption
status remain HOLD.

Normative details and decision gates are in
[`docs/r1_full_endpoint_contract.md`](docs/r1_full_endpoint_contract.md).
