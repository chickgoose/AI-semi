# K2 W2 final receipt release gate

This gate is the final permission check before a ranking process may consume
metrics. It consumes exactly five immutable JSON receipts: Genus, Innovus,
activity-power, functional-loss, and boundary. It does not parse a Genus or
Innovus report, SAIF, power table, workload CSV, or candidate metric, and its
output contains no ranking or metric values.

Every upstream producer must add the common `release_binding` described by the
test fixture. The binding fixes the campaign ID/generation/nonce, sorted exact
candidate IDs and commits, cohort ID, Liberty and PVT, SDC, load model, and the
frozen generator-v4 full50/capacity22 workload hashes. A native receipt lacking
that binding remains useful at its original evidence boundary but is not a
release receipt.

Freshness is deterministic: the manifest's exact campaign generation and
256-bit nonce are authoritative, and both are inside the authenticated boundary
payload. A receipt from an older generation or any other nonce is stale. The
gate intentionally does not make a wall-clock decision that could change when
the same immutable bundle is replayed later.

The boundary receipt is the trust root. Its HMAC-SHA256 attestation covers the
release ID, exact campaign, byte hashes of the four metric receipts, and the
boundary receipt body. Therefore a power receipt is not authenticated merely
because it says `authenticated`: the entire activity-power receipt hash must
appear in the verified boundary payload. The keyring is caller-owned and must
not be packaged with public
receipts. Its exact byte SHA-256 is a mandatory out-of-band command argument,
so replacing the keyring and re-signing is not accepted. This is a symmetric
campaign MAC, not a public signature.

Innovus must publish a complete `MONOTONIC_QUALIFIED` fail/pass sweep for every
candidate. Periods and WNS must be monotonic, qualification must agree with WNS,
and the selected period must be the first passing point adjacent to the last
failure. `NON_MONOTONIC_HOLD`, missing points, pass-to-fail reversions, or an
isolated cherry-picked pass cannot authorize ranking.

The current yZr1 receipt is intentionally ineligible: it is workspace-diff,
loss-only evidence and says the official common receipt is HOLD. Likewise the
authoritative raw Ganghee sweep is non-monotonic/HOLD. The release gate does not
reinterpret either receipt into stronger evidence.

Run with a new output path:

```sh
python3 physical/k2_w2_release_gate/release_gate.py \
  --bundle-root /immutable/campaign-bundle \
  --manifest /immutable/campaign-bundle/release-manifest.json \
  --keyring /protected/w2-release-keyring.json \
  --keyring-sha256 OUT_OF_BAND_TRUSTED_64_HEX_DIGEST \
  --output /writable/new-release-gate-receipt.json
```

Exit 0 and `RANKING_PERMITTED` are required before ranking. Exit 2 publishes a
compact `RANKING_HOLD` diagnostic; exit 1 means even publication failed. An
existing output is never overwritten.
