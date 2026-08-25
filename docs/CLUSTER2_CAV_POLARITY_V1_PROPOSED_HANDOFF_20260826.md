# Proposed local handoff: Cluster2 CAV polarity-v1 integration

Date: 2026-08-26

Disposition: **HOLD — evidence preparation only**

This is a proposed local handoff for the next integration owner. It is not the
canonical `TEAM_PROGRESS.md`, does not amend the sealed first-round result, and
does not assert that polarity-v1 qualification tests have run.

## Authority at handoff

- Existing public presentation authority:
  `chickgoose/AI-semi` branch
  `integration/cluster2-steal-buf-cav-bridge` at
  `41e2999b55235ad9ccd0a0b13bfdc7642ef5b20b`.
- Existing sealed evidence checkpoint:
  `f5109974236d297b5b60b0f1c18aecc4c1d184e6`.
- Existing native authority remains address-only Ganghee commit
  `5ac1f0e3c0e6991558afa699e64680f708ff625d` and RTL SHA-256
  `56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96`.
- Proposed new source is Ganghee polarity **v1**, not v2:
  `rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v`, observed
  SHA-256
  `20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81`.
- Upstream polarity ledger commit:
  `b9f2b76cc3c5fcfeee071193a2fbcb70aa35f55d`.
- Upstream committed polarity-v1 P&R sweep:
  `9b0d95121cf88ba55bee13cf0e5d444d688010b6`.

Read
`docs/presentation/cluster2_cav_polarity_v1_integration_evidence_matrix_20260826.md`
before importing any artifact. It defines the exact promotion gates and keeps
the old and new authorities separate.

## Proposed correction for the shared `TEAM_PROGRESS.md`

The shared `TEAM_PROGRESS.md` is not present in this worktree and was not
edited. If its Ganghee section still says that polarity-v1 P&R has not been run
or that its outputs are uncommitted/server-only, replace that stale statement
with the following scoped text:

> **Polarity-v1 P&R status correction (2026-08-26):** Ganghee upstream commit
> `9b0d95121cf88ba55bee13cf0e5d444d688010b6` commits the
> `cluster2_steal_buf_polarity` resynthesis/P&R sweep outputs, so the narrow
> statement “polarity-v1 P&R was not run” or “its outputs are uncommitted” is
> stale. The chickgoose presentation authority remains address-only at
> `41e2999b55235ad9ccd0a0b13bfdc7642ef5b20b`; it has not imported, hash-bound,
> parsed, independently reproduced, or release-qualified those P&R artifacts.
> Therefore artifact availability is GO, while polarity-v1 PPA release use and
> attribution to the sealed first-round candidate remain HOLD. Do not call the
> sweep signoff, exact Fmax, workload activity power, or CAV/world PPA.

This correction supersedes only the artifact-availability statement. It does
not override older caveats concerning vectorless power, setup/hold and routing
scope, unconstrained paths, warnings/errors, PDK/signoff limits, or the fact
that polarity-v1 is a different RTL top from the sealed address-only candidate.

## Next owner checklist

- [ ] Select and record an immutable Ganghee v1 commit/blob; do not bind moving
      `main` and do not substitute polarity v2.
- [ ] Import only the minimum source/ledger/P&R closure and create a canonical
      integration authority with SHA-256 for every artifact.
- [ ] Reconcile the upstream polarity manifest's internal identities with the
      selected source commit and fail closed on any mismatch.
- [ ] Build an independent hardware-polarity ledger validator and polarity
      mutations before accepting the upstream recorded counts.
- [ ] Prove the CAV crosswalk consumes hardware-retired polarity for the same
      event, rather than source-side or TB-only polarity.
- [ ] Generate a distinct polarity-v1 result and receipt; do not edit or
      overwrite existing canonical first-round result JSON.
- [ ] Parse the `9b0d951` raw P&R closure and disclose all mandatory timing,
      route, DRC/antenna, power-mode, warning, and terminal-error fields.
- [ ] Run the applicable suites and a fresh-clone reproduction before recording
      any PASS count. Until then, leave every new functional/PPA/presentation
      claim HOLD.
- [ ] Update the public evidence matrix, briefing, status, and authority commit
      only after the preceding gates close.

## Work performed in this handoff commit

Documentation only: one proposed integration evidence matrix/status document
and this proposed handoff. No RTL, scripts, tests, ledgers, reports, canonical
result JSON, or existing authority documents were changed. No qualification
test or physical tool was run.
