# Cluster2/CAV polarity-v1 release gate

This self-contained package is safe to cherry-pick before the polarity release
artifacts.  It does not modify or synthesize RTL.  The repository probe remains
`HOLD` until the fixed manifest exists at:

```text
benchmarks/redred_cluster2_cav_bridge/polarity_release_authority.json
```

The manifest must bind exact SHA-256 values for an explicit, wildcard-free
synthesis filelist and source closure; polarity-v1 RTL, TB, trace, ledger, and
receipt; and a separate integration-authority document.  All text declares
either canonical `LF` or exact `CRLF` raw bytes plus its normalized-LF digest.

The source, receipt, and integration commits are three distinct stages.  The
source commit may identify Ganghee's external repository; the receipt and
integration commits must be available in the integrated repository, with the
receipt commit an ancestor of the integration commit and every integrated blob
matching the manifest.  The integration-authority document must explicitly say
`NATIVE_POLARITY_V1_BOUND` and grant release authority.

The trace and ledger are canonical JSONL with ordered event IDs `0..8502`.
The gate recomputes exactly 8,503 generated and delivered events, zero overrun,
and zero expected-versus-observed polarity mismatches.  Declared counters alone
cannot promote the gate.

Run the mutation suite and current-tree probe with:

```sh
tests/redred_cluster2_cav_polarity_release/run_all.sh
```

The probe exits `0` only for `GO`; a fail-closed `HOLD` exits `2`.
