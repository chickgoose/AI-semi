# Cluster2/CAV polarity-v1 release gate

This self-contained package is safe to cherry-pick before the release
artifacts. It does not modify or synthesize RTL. The repository probe remains
`HOLD` until an actual receipt and the fixed manifest exist at:

```text
benchmarks/redred_cluster2_cav_bridge/polarity_release_authority.json
```

The v2 manifest binds full raw and semantic SHA-256 values for:

- the explicit, wildcard-free source filelist containing exactly `arbiter2.v`,
  `arbiter4_tree.v`, and the selected polarity-v1 RTL;
- polarity-v1 RTL, the native observational TB and runner, the authoritative
  raw addr/polarity trace, the raw CYCLE ledger, the independent verifier, and
  the actual receipt; and
- a separate integration-authority document granting
  `EXPLICIT_INTEGRATION_RELEASE_AUTHORITY` with
  `NATIVE_POLARITY_V1_BOUND`.

All text declares canonical `LF`, or (for an input that supports it) exact
`CRLF` raw bytes plus a normalized-LF digest. The raw CYCLE ledger uses
canonical LF and the framing introduced by `da329e3`; its lane-polarity
interpretation follows the pinned RTL semantics described below.

Authority has three stages. Ganghee commit
`44f8918c6e0085f7b75bb90fbe6c099abe1882cc` is pinned as external source
provenance and is intentionally **not** required to exist as a commit object in
the integration repository. The receipt and integration commits are distinct
local commits; the receipt must be an ancestor of the integration commit, and
their scoped blobs must match every declared hash.

The gate independently reparses raw `cycle addr_mask polarity_mask` records and
reconstructs the two-entry per-source polarity FIFOs from the cycle-complete
ledger contract introduced by `da329e3`. The raw trace must expand to 8,503
generated occurrences. Delivered and overrun totals are not assumed: they are
derived only after replay and must conserve `generated = delivered + overrun`.
For a valid lane, the pinned RTL emits the full four-bit `pol_front_bus` slice
for the selected row. Consequently, `pol_mask` bits outside `col_mask` are
permitted and carry no retirement meaning. Every asserted `col_mask` bit must
still match that source FIFO's front polarity. An invalid lane must remain
canonical all-zero, including its row, column mask, and polarity mask. Phantom,
duplicate, incomplete-drain, malformed-geometry, and selected-column polarity
failures hold the release. JSONL `EVENT` IDs and predeclared manifest counters
have no release authority.

The TB binding follows the actual native observational interface and markers:
the `redred_cluster2_polarity_v1_native_observational_tb` top, native
`polarity_in`/`pol_mask0`/`pol_mask1` wiring, and
`REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%0d delivered=%0d overrun=%0d phantom=0 duplicate=0 drain_empty=1`.
The final raw TB contains no `polarity mismatch` diagnostic string.
The runner binding likewise requires its external source pin, trace/TB hashes,
and `POLARITY_V1_NATIVE_PASS commit=%s simulator=%s events=%d
identity_order_independence_claimed=false output_root=%s` (one output line).

Run the mutation suite and current-tree probe with:

```sh
tests/redred_cluster2_cav_polarity_release/run_all.sh
```

The probe exits `0` only for `GO`; absent manifest or receipt and all other
fail-closed cases return `HOLD` with exit `2`.
