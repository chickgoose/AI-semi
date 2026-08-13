# K2 W2 final receipt release gate

This gate is the final permission check before a ranking process may consume
metrics. It consumes exactly seven immutable JSON receipts: server environment,
technology-staged manifest, Genus v2, Innovus, activity-power, functional-loss,
and boundary. It does not parse a Genus or Innovus raw report, SAIF, power table,
workload CSV, or candidate metric, and its output contains no ranking or metric
values.

Every upstream producer must add the common `release_binding` described by the
test fixture. The binding fixes the campaign ID/generation/nonce, exact ordered
candidate set `fovea_a7,a2_p6,a3_p6` and commits, the
`tech_staged_complete_compositions` cohort, slow-setup and fast-hold Liberty/PVT,
shared QRC, LEFs, multi-clock full-link v6 SDC, identical external load, exact
staged-manifest/non-link boundary hashes, and frozen generator-v4
full50/capacity22 workload hashes. A native receipt lacking that binding remains
useful at its original evidence boundary but is not a release receipt.

The server-environment receipt must be `PROVEN`, hash both tool executables and
all live technology inputs, and match the common provenance byte for byte. The
staged-manifest receipt must name only the exact three production tops and must
retain the R1 three-bit and P6 six-bit link outputs. Genus v1, generic/native
substitution, or a two-candidate signed cohort is rejected.

Freshness is deterministic: the manifest's exact campaign generation and
256-bit nonce are authoritative, and both are inside the authenticated boundary
payload. A receipt from an older generation or any other nonce is stale. The
gate intentionally does not make a wall-clock decision that could change when
the same immutable bundle is replayed later.

The boundary receipt is the trust root. Its HMAC-SHA256 attestation covers the
release ID, exact campaign, byte hashes of all six upstream receipts, and the
boundary receipt body. Therefore a power receipt is not authenticated merely
because it says `authenticated`: the entire activity-power receipt hash must
appear in the verified boundary payload. The keyring is caller-owned and must
not be packaged with public
receipts. Its exact byte SHA-256 is a mandatory out-of-band command argument,
so replacing the keyring and re-signing is not accepted. This is a symmetric
campaign MAC, not a public signature.

Innovus must publish a complete `MONOTONIC_QUALIFIED` fail/pass sweep for every
candidate. Every frequency point references four immutable, unique receipts:
Innovus run/clean-exit and post-route netlist/database, STA with
setup/hold/recovery/removal report hashes and results, DRC/antenna, and
signal/PG connectivity. The gate loads those receipts, verifies their hashes
and common release binding, and derives qualification from them; a point cannot
substitute WNS booleans or a PASS sentinel. Setup WNS and pass/fail state must be
monotonic, and the selected period must be the first passing point adjacent to
the last failure.

Activity-power requires one common trace/window and clock period plus, for each
candidate, unique SAIF, power-report, and scope hashes, coverage, retired-event
count, total/dynamic/leakage power, and an energy/event value that exactly
matches those inputs. Vectorless or self-asserted power is forbidden. Functional
loss must be an official full50/capacity22 receipt with exact trace/manifests and
accepted/delivered/overrun conservation; workspace-diff loss remains loss-only.

The boundary receipt preserves `link_clk_o` and `link_data_o`. It marks them as
`AER_LINK_CUT`: native-boundary link accounting is zero and cut accounting is
exactly three bits for R1 or six bits for P6, so the link is neither omitted nor
charged twice. TX and RX remain connected by those same nets and the external
load is applied once. The common non-link seam must be identical and stateless,
and the clock contract must retain ref, sample, and generated/gated link clocks.

The current yZr1 receipt is intentionally ineligible: it is workspace-diff,
loss-only evidence and says the official common receipt is HOLD. Likewise the
authoritative raw Fovea/Cluster2 baseline and its non-monotonic sweep are a
separate diagnostic cohort. The current local server-environment receipt is
also HOLD rather than PROVEN. The release gate does not reinterpret any of
these into final qualification.

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
