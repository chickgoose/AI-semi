# A2/A3 single-edge mapped default-vectorless evidence

This directory defines the fail-closed power evidence class for the exact
complete single-edge endpoints introduced by commit
`4ce4836fab1309d3468db8e660d2da9af371f784`:

- `a2_batched_iwrr_single_edge_top`;
- `a3_exact_scalar_prefix_k2_single_edge_top`.

The pinned candidate filelists, generic single-edge filelist, scheduler RTL,
TX, RX, endpoint, and complete top hashes are in `source-manifests.json`.
Neither normalized pre-endpoint wrappers nor raw scheduler cores satisfy this
boundary. P6 files, tops, receipts, dependencies, and inherited 6.5 ns results
are categorically outside this evidence class.

## What the local preflight proves

```sh
python3 physical/k2_single_edge_vectorless/preflight.py preflight \
  --output /tmp/k2-single-edge-vectorless-preflight.json
```

Preflight checks the exact commit objects, local contract, system-policy hash,
Genus and SDC template hashes, source/filelist identities, operating point, and
anti-activity policy. It never invokes Genus. Its only successful decision is
`HOLD_NO_PRODUCER_BOUND_SERVER_ARTIFACTS`, with `candidate_go=false`.

The frozen operating point is GPDK045 slow setup/power Liberty at 0.9 V and
125 C, a 6.5 ns single `clk_i` clock with a 0.0/3.25 ns waveform and 0.25 ns
uncertainty, 0.1/0.5 ns input and output delays, 0.05 ns input transition, and
0.01 pF on every output. Both the slow setup/power and fast hold Liberty bytes
are exact SHA-256 inputs.

## Server attempt layout

`vectorless-evidence.json` is a locator-only index. It must list exactly the
A2 row and then the A3 row; it contains no caller-provided PASS or artifact
hashes:

```json
{
  "schema": "k2_single_edge_vectorless_evidence_v1",
  "evidence_class": "GENUS_MAPPED_A2_A3_SINGLE_EDGE_DEFAULT_VECTORLESS",
  "candidate_order": ["a2_single_edge", "a3_single_edge"],
  "interface": "SINGLE_EDGE_PARALLEL",
  "contract_sha256": "<exact committed contract bytes>",
  "rows": [
    {
      "candidate": "a2_single_edge",
      "top": "a2_batched_iwrr_single_edge_top",
      "attempt_directory": "a2"
    },
    {
      "candidate": "a3_single_edge",
      "top": "a3_exact_scalar_prefix_k2_single_edge_top",
      "attempt_directory": "a3"
    }
  ]
}
```

Each attempt is immutable and contains `producer-receipt.json`, `bundle/`,
`logs/`, `reports/`, and `work/`. Every evidence file must be a regular
single-link file reachable without a symlink. The accepted producer receipt is
only `k2_single_edge_vectorless_producer_receipt_v1`; the committed receipt
template has a different schema and is deliberately non-evidence.

The receipt binds all of the following through one complete artifact ledger
and its authenticated payload:

- producer authority, run ID, server fingerprint, start/end time, and direct
  this-run lineage;
- exact commit, candidate, complete top, filelists, and source snapshots;
- Genus requested and resolved paths, version, and binary SHA;
- exact absolute executed argv, in-place cwd, exit code zero, controlled
  semantic environment, and environment hash;
- executed Tcl, input/materialized/mapped SDC, both Liberty snapshots, mapped
  netlist, SDF, Genus log, command receipt, and server environment receipt;
- area, timing, power, QoR, timing-intent, and clock report bytes; and
- exact corner, clock, I/O delays/transition, load, and default-vectorless
  activity policy.

The power report must have the native Genus header, exact top and W units, one
subtotal, and exactly:

```text
* User-Defined Activity : N.A.
* Activity File: N.A.
* Sequential Element Activity: 0.200000
* Primary Input Activity: 0.200000
```

VCD, SAIF, TCF, activity imports, switching-activity commands, default activity
overrides, and per-object activity are forbidden in the driver, environment,
log, and reports.

## Producer authentication and GO

Hashes alone establish consistency, not provenance. Without an external trust
anchor, a structurally complete bundle can only produce
`HOLD_UNAUTHENTICATED_PRODUCER_ARTIFACTS`:

```sh
python3 physical/k2_single_edge_vectorless/preflight.py qualify \
  --evidence /absolute/live/server/root/vectorless-evidence.json \
  --output /absolute/live/server/root/qualification.json
```

GO additionally requires an owner-only (`0600`) keyring outside the evidence
root and the keyring's SHA-256 obtained through an out-of-band trusted channel:

```sh
python3 physical/k2_single_edge_vectorless/preflight.py qualify \
  --evidence /absolute/live/server/root/vectorless-evidence.json \
  --keyring /secure/redred/single-edge-producer-keyring.json \
  --keyring-sha256 "$TRUSTED_OUT_OF_BAND_KEYRING_SHA256" \
  --output /absolute/live/server/root/qualification.json
```

The keyring schema is `k2_single_edge_vectorless_keyring_v1`; each key binds
an authority ID to `hmac-sha256`, at least 256 secret bits, and producer origin
`DIRECT_GENUS_SERVER_RUN`. The receipt MAC covers the canonical receipt with
the attestation field removed. Qualification also requires both attempts to
remain at their exact recorded absolute server cwd. A copied, relabeled,
rehashed, synthetic, inherited, or P6 artifact tree cannot be promoted.

Focused regression:

```sh
tests/k2_single_edge_vectorless/run_all.sh
```
