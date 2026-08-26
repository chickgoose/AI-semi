# Final handoff: Cluster2 CAV polarity-v1 integration

Date: 2026-08-26

Disposition: **GO — final pinned polarity-v1 functional integration evidence**

This handoff records the authoritative integrated evidence from
`/tmp/cluster2-cav-polarity-integration`. The final packaging follow-up binds
the separate upstream source, reproduction, and receipt commits; it does not
amend the legacy address-only canonical result JSON or claim a new simulation.

## Final authority

- Ganghee RTL/native-run source:
  `44f8918c6e0085f7b75bb90fbe6c099abe1882cc`.
- Ganghee ledger reproducibility/tool commit:
  `58c132fb475013634ee156eddf5037128c0ce0b3`.
- Ganghee ledger manifest receipt commit:
  `f2f93a830414aff2e0a3b7db05154294e1d4b78d`.
- Exact receipt manifest SHA-256:
  `df7ecc74be802c55dedb2596ef8dc7063c71f9324d48ab45dfaa360cb87a02fa`.
- Unchanged polarity transport JSONL SHA-256:
  `518a2a5ba977516ea687fdc23a9246ff9cfe90fbf3d013efdd358200596e9cd3`.
- Selected v1 RTL SHA-256:
  `20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81`.
- Historical upstream ledger/P&R milestones:
  `b9f2b76cc3c5fcfeee071193a2fbcb70aa35f55d` and
  `9b0d95121cf88ba55bee13cf0e5d444d688010b6`.
- Native receipt commit:
  `29d785661fec4062930d7bf54ff3fec0d306be60`.
- Release-gate integration commit:
  `c3d0a2479bcfd1bc68e942acfc418f023f6d3506`.
- Verification report SHA-256:
  `19d2ff34720b4fa594eb8dc847f47c383e20e16b14727a5a4e353a8d0f6a375e`.
- Raw ledger SHA-256:
  `7096a7b0ebdc2fd50cda31e4c977cd4ed1a7e75585174d17051fe89cbab89ea8`.
- Native evidence archive SHA-256:
  `28401809a244571f084d01a2cc950ad381fc393f8b9a747364c45abbb16e8610`.

The archive is a separately preserved convenience bundle. Release GO is
derived from the manifest-bound raw trace, raw CYCLE ledger, RTL, TB, runner,
independent verifier, receipt, and integration authority rather than from the
archive alone.

Xcelium `23.09-s013` passed with generated=8503, delivered=8503,
overrun=0, phantom=0, duplicate=0, drain_empty=true, final_cycle=59426, and
observed_cycles=59427. The release tests are 14/14 PASS; the bridge regression
is 189 PASS with 4 environment-gated skips. The final functional gate is
**GO**. `identity_order_independence_claimed=false` remains an explicit claim
limit.

For full-row masks, only `pol_maskN[col]` positions selected by
`col_maskN[col] == 1` carry retired-event polarity. Other `pol_maskN` bits are
unselected don't-care/non-events and must not be counted or compared as
retirements.

The detailed authority boundary and decision tree are in
`docs/presentation/cluster2_cav_polarity_v1_integration_evidence_matrix_20260826.md`.

## Legacy evidence remains separate

The existing presentation branch at
`41e2999b55235ad9ccd0a0b13bfdc7642ef5b20b` and sealed checkpoint
`f5109974236d297b5b60b0f1c18aecc4c1d184e6` remain address-only authority.
Their source pin, evidence, receipts, counts, and canonical result are not
reinterpreted as polarity evidence and were not edited by this handoff.

## Final correction for the shared `TEAM_PROGRESS.md`

The shared `TEAM_PROGRESS.md` is not present in this worktree and was not
edited. If its Ganghee section still reports that polarity-v1 P&R was not run
or that its outputs are server-only/uncommitted, use this replacement:

> **Polarity-v1 status correction (2026-08-26):** Ganghee upstream commit
> `9b0d95121cf88ba55bee13cf0e5d444d688010b6` commits the
> `cluster2_steal_buf_polarity` resynthesis/P&R sweep outputs; the statement
> that polarity-v1 P&R was not run or remained uncommitted is stale. Separately,
> the pinned native polarity-v1 functional integration from Ganghee
> `44f8918c6e0085f7b75bb90fbe6c099abe1882cc` is GO under receipt
> `29d785661fec4062930d7bf54ff3fec0d306be60` and integration commit
> `c3d0a2479bcfd1bc68e942acfc418f023f6d3506`. This functional GO does not
> qualify the P&R bundle as signoff, establish exact Fmax or workload-activity
> power, or turn the separate `41e2999...` address-only evidence into polarity
> evidence. P&R artifact existence is GO; polarity-v1 PPA/signoff claims remain
> HOLD until separately qualified.

This correction preserves the existing caveats for vectorless power,
setup/hold and routing scope, unconstrained paths, warnings/errors, PDK/signoff
limits, and the distinct RTL top.

## Work performed for this final handoff

The final packaging follow-up updated only provenance authority, evidence
packaging, documentation, tests for that authority, and the submission
verifier/inventory. It preserved the exact upstream manifest bytes and did not
change RTL, source-crosswalk code, ledgers, simulation reports, native receipt,
release gate, or the canonical result JSON. Xcelium was not rerun; the results
above remain the authoritative integrated evidence, not a newly executed
campaign.
