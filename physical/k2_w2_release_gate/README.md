# K2 W2 final receipt release gate

This gate is the final permission check before a ranking process may consume
metrics. It consumes exactly seven top-level immutable JSON receipts—server
environment, technology-staged manifest, Genus v3, Innovus, activity-power,
functional-loss, and boundary—plus their hash-referenced Genus, post-route,
common-suite, and reset auxiliary receipts. It does not parse a Genus or
Innovus raw report, SAIF, power table, or workload CSV. It validates bounded
receipt metrics only to derive qualification, and its output contains no
ranking or metric values.

Every upstream producer must add the common `release_binding` described by the
test fixture. The binding fixes the campaign ID/generation/nonce, exact ordered
candidate set `fovea_a7,a2_p6,a3_p6` and commits, the
`tech_staged_complete_compositions` cohort, slow-setup and fast-hold Liberty/PVT,
shared QRC, LEFs, multi-clock full-link v6 SDC, identical external load, exact
staged-manifest/non-link boundary hashes, and frozen generator-v4
full50/capacity22 workload hashes. It also binds the official candidate
source/binding manifest, runner/generator/analyzer bundles, simulator executable
and version, and the ordered run-name/workload/trace index for both suite views.
A native receipt lacking that binding remains
useful at its original evidence boundary but is not a release receipt.

The one shared committed top contract is literal in the campaign SDC and staged
manifest and is repeated in the staged-manifest, Genus-v2, and every Innovus
point receipt. Inputs are `ref_clk_i`, `sample_clk_i`, `rst_n`, and
`source_pending_i[15:0]`. Outputs are `source_accept_o[15:0]`, `link_clk_o`,
`link_data_o[W-1:0]`, `retire_valid_o[1:0]`, `retire_addr0_o[3:0]`,
`retire_addr1_o[3:0]`, `drain_idle_o`, and `protocol_error_o`, with W=2 for R1
and W=5 for both P6 candidates. `load_i`, `pending_i`, `source_ready_o`, and
`protocol_fault_o` are forbidden final-top aliases. The gate cannot authorize a
physical launch from a proposed or uncommitted manifest: a committed manifest
hash and repository commit must first be present in every bound producer.

The server-environment receipt must be `PROVEN`, hash both tool executables and
all live technology inputs, and match the common provenance byte for byte. The
staged-manifest receipt must name only the exact three production tops and must
retain the R1 three-bit and P6 six-bit link outputs. Pre-v3 Genus,
generic/native substitution, or a two-candidate signed cohort is rejected. Each
Genus candidate must reference a loaded, immutable mapped-proof receipt for
every swept target period. Each proof binds the candidate commit and source
manifest, staged manifest, proven server
contract, exact top/ports and SDC, nonzero mapped-cell inventory, zero
unmapped/blackbox inventory, mapped netlist/SDC, report receipt, and mapped
smoke identities.

Freshness is deterministic: the manifest's exact campaign generation and
256-bit nonce are authoritative, and both are inside the authenticated boundary
payload. A receipt from an older generation or any other nonce is stale. The
gate intentionally does not make a wall-clock decision that could change when
the same immutable bundle is replayed later.

The boundary receipt is the trust root. Its HMAC-SHA256 attestation covers the
release ID, exact campaign, byte hashes of all six upstream receipts, and the
boundary receipt body. Therefore a power receipt is not authenticated merely
because it says `authenticated`: the entire activity-power receipt hash must
appear in the verified boundary payload. The keyring is caller-owned, must be
outside the public bundle root, and must be owned by the invoking user with no
group/other access. Its exact byte SHA-256 is a mandatory out-of-band command
argument,
so replacing the keyring and re-signing is not accepted. This is a symmetric
campaign MAC, not a public signature.

Innovus must publish a complete `MONOTONIC_QUALIFIED` fail/pass sweep for every
candidate. Every frequency point's Innovus receipt must name the exact
same-period Genus mapped netlist and mapped SDC for that candidate plus the
common constraint-set hash, completed detailed routing, and the post-route
netlist/database/SPEF.
Every frequency point references four immutable, unique receipts:
Innovus run/clean-exit and post-route netlist/database, STA with
setup/hold/recovery/removal report hashes and results, DRC/antenna, and
signal/PG connectivity. STA, DRC, and connectivity must each name that same
point's post-route netlist and Innovus database. STA additionally binds the
setup/hold Liberty files, shared QRC, SDC, post-route SPEF, propagated clocks,
zero unconstrained/no-clock/no-delay/no-drive/no-load coverage classes, and
nonzero analyzed paths for all four timing checks. The gate rejects reuse of a
physical evidence hash across candidates, points, or evidence roles, loads the
receipts, verifies their hashes and common release binding, and derives
qualification from them; a point cannot substitute WNS booleans or a PASS
sentinel. Setup WNS and pass/fail state must be monotonic, and the selected
period must be the first passing point adjacent to the last failure.

Activity-power requires one common trace/window and clock period plus a loaded,
immutable post-route power proof for each candidate. Each proof binds unique
VCD, SAIF, VCD-to-SAIF conversion, activity-window, post-route-netlist/SPEF,
power-report, and scope hashes, coverage, retired-event count,
total/dynamic/leakage power, and an energy/event value that exactly
matches those inputs. The power netlist must be the first passing implementation
selected by the Innovus bracket; its SPEF and named power scope must match that
same selected implementation and exact candidate top. Vectorless or
self-asserted power is forbidden.
Functional loss must contain the existing canonical schema-5 official common
suite receipts for full50 and the capacity22 subset view, plus basic reset. Each
receipt is hash-loaded and checked for exact source/binding/runner/simulator,
manifest, ordered trace identity, pair/mixed/phase/timing analyzer closure, and
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

Exit 0 and `RANKING_PERMITTED` are required before ranking. After successful
argument parsing, exit 2 publishes a compact `RANKING_HOLD` diagnostic; exit 1
means even publication failed. Command-line usage errors are argparse exit 2
and do not claim to publish a HOLD. An existing output is never overwritten.
