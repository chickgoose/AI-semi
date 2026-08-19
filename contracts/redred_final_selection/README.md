# REDRED current A2/A3 final-selection gate

This package turns the current final-selection HOLD into an executable,
fail-closed decision contract. It is the current single-edge successor to the
superseded P6 study in `audits/k2_final_selection/`; it does not reuse that
study's 5% thresholds, structural proxies, or winner.

The fixed CLI verifies immutable Git objects for the active goal, native
campaign publication, endpoint physical contract, vectorless contract, PDK
legality matrix, and source CDC/RDC contract. Caller-supplied evidence and
caller-selected contract paths are not accepted. Git replacement objects and
global/system Git configuration are disabled while reading the pinned blobs.

Current result:

```text
REDRED_FINAL_SELECTION_HOLD candidate=NONE missing=12 selection_authority=false release_authority=false
```

The two existing PASS inputs are the selected single-edge interface policy and
the scoped native canonical campaign. The twelve missing gates are:

- organizer cell/clock/I/O rules, official constraints/corners, controlled
  producer freshness, and a matched A2/A3 cohort;
- for each of A2 and A3: mapped PDK legality, post-route timing/area,
  mapped vectorless power, and final mapped CDC/RDC.

`HOLD` means evidence is absent or not authoritative. `FAIL` means a complete,
authenticated evaluation produced a negative result. Missing evidence can
never activate A3. After complete evaluation, A3 is policy-eligible only when
exact-prefix semantics is required or when every named A2-specific FAIL is
independently passed by A3. Shared interface, evidence, CDC/RDC, or PDK/I/O
failure never activates the fallback.

When every gate passes under the default aggregate-weighted policy, A2 is the
predeclared primary candidate. This is a policy choice, not an organizer score
winner. Raw loss, throughput, latency, timing, area, and vectorless power form a
metric vector; no scalar weights or thresholds are invented.

The pure decision-table evaluator deliberately returns
`final_selection_authority=false` even for an eligible policy candidate. A real
selection requires a separately authenticated payload followed by a distinct,
noncircular reviewed publication. Unsigned Git objects and self-hashes establish
byte identity, not producer identity or freshness.

Run:

```sh
tests/redred_final_selection/run_all.sh
```
