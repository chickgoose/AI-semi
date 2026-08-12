# W7 Weighted-Fovea+A7 standalone profile and receipt

## Decision

This additions-only package is pinned to A1 `2a3a3be94be8f12585f484b5b1da2b372f7282d9` and the exact ten-file synthesis closure for `a7_weighted_fovea_ddr`. It has no dependency on the earlier A9 W7 release chain. Its release decision is **HOLD**: it records identities and rejection boundaries; it does not create physical evidence.

The candidate contract is address-only, 16 sources, a four-bit retired address, and the three-pin A7 DDR link. The synthesis closure excludes every TB, binding, result, log, and receipt file. Verification and common-boundary inventories are separate fields. The common-boundary field preserves ABI v4 and the expected full50/capacity22 counts, but does not call the archived run an official common receipt.

## Xcelium archive boundary

The externally supplied `fovea-cluster2-0FfaT8kp.tar.gz` is pinned by SHA-256 `0600293426d41441cb597f8b43ff635df6251dcb0c8289e0e258b7c49d633b96`, size, 736-member closure, and its 338-entry artifact index. Validation rejects links, special members, duplicate or unsafe paths, then re-hashes the bytes of all 338 indexed members after safely rebasing the historical absolute `/tmp` prefix. It also binds the two run logs and provenance.

This is deliberately classified `FOVEA_VS_CLUSTER2_LOCAL_ARCHIVE_NOT_EXACT_WEIGHTED_FOVEA_A7_NOT_OFFICIAL_COMMON`. Its own provenance says snapshot `47e1f2f...` and `binding_reset_quiet_arming_patch=workspace-diff`; it therefore cannot qualify the exact `2a3a3be` Weighted-Fovea+A7 composition. The Xcelium executable path/hash is absent and remains `UNBOUND`.

## Genus and Innovus boundary

The receipt reserves distinct raw-report fields for tool logs, checks, timing, constraints, area, power/activity, databases, DRC, and antenna evidence. Today every field must be null and both stages must be `ABSENT_HOLD`. A claimed PASS, an arbitrary report, or a self-declared marker causes regeneration comparison to fail. Accepting future physical evidence requires a reviewed follow-up policy that pins executable identities, PDK/library/LEF/QRC/PVT, SDC and IO loads, exact argv/environment, ordered input closure, raw reports, and a trusted parser plus parser receipt. No Genus, Innovus, STA, or physical PPA was run here.

## Use after selective cherry-pick

From a clean A1 checkout descended from `2a3a3be`, run:

```sh
/usr/bin/python3 -I scripts/w7_weighted_fovea_a7_receipt.py audit-archive \
  --repo . --xcelium-archive /secure/read-only/fovea-cluster2-0FfaT8kp.tar.gz
/usr/bin/python3 -I scripts/w7_weighted_fovea_a7_receipt.py generate \
  --repo . --xcelium-archive /secure/read-only/fovea-cluster2-0FfaT8kp.tar.gz \
  --output /new/unique/receipt-root
/usr/bin/python3 -I scripts/w7_weighted_fovea_a7_receipt.py validate \
  --repo . --xcelium-archive /secure/read-only/fovea-cluster2-0FfaT8kp.tar.gz \
  --receipt /new/unique/receipt-root/weighted_fovea_a7_receipt.json
```

Generation rejects dirty or untracked state, a changed base/current source blob, filelist reordering, fake caller-PATH Git, archive substitution, and reuse of an output directory. The validator and profile are bootstrap inputs; this package does not claim hostile self-attestation if both are replaced.
