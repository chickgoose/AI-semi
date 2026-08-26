# Cluster2 CAV polarity-v1 final integration evidence matrix

Date: 2026-08-26

Working branch: `work/polarity-a8-evidence`

Status: **FINAL INTEGRATED POLARITY-V1 EVIDENCE — GO**

This document records the completed polarity-v1 functional integration. It
does not replace, amend, or reinterpret the separately sealed address-only
first-round evidence, and it does not modify an existing canonical result
JSON.

## 1. Authority boundary

### Legacy address-only evidence (preserved)

The earlier public presentation authority remains
`chickgoose/AI-semi:integration/cluster2-steal-buf-cav-bridge` at
`41e2999b55235ad9ccd0a0b13bfdc7642ef5b20b`, with sealed evidence checkpoint
`f5109974236d297b5b60b0f1c18aecc4c1d184e6`. It binds Ganghee source
`5ac1f0e3c0e6991558afa699e64680f708ff625d`, address-only RTL SHA-256
`56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96`,
and native-authority JSON SHA-256
`90e659358423368ce6a27850cdffa36a0eb85cea508babc66e72ecafb8e70530`.

That authority remains valid only for its sealed address-only native
observation and observational CAV/world projection. It has no native polarity
payload. The polarity-v1 result below neither adds polarity to those legacy
records nor changes their counts, digests, receipt, or canonical result.

### Final polarity-v1 integration authority

The new functional authority selects Ganghee polarity **v1**, not v2:

- source commit:
  `44f8918c6e0085f7b75bb90fbe6c099abe1882cc`;
- selected RTL:
  `rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v`;
- selected RTL SHA-256:
  `20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81`;
- upstream hardware-polarity ledger milestone:
  `b9f2b76cc3c5fcfeee071193a2fbcb70aa35f55d`;
- upstream committed polarity-v1 P&R sweep milestone:
  `9b0d95121cf88ba55bee13cf0e5d444d688010b6`;
- native receipt commit:
  `29d785661fec4062930d7bf54ff3fec0d306be60`;
- release-gate integration commit:
  `c3d0a2479bcfd1bc68e942acfc418f023f6d3506`.

The integrated release authority grants
`EXPLICIT_INTEGRATION_RELEASE_AUTHORITY` with
`NATIVE_POLARITY_V1_BOUND` for this pinned functional evidence.

## 2. Authoritative evidence and results

| Evidence/gate | Authoritative value | Decision |
| --- | --- | --- |
| Simulator | Xcelium `23.09-s013` | **PASS** |
| Native conservation | generated=8503, delivered=8503, overrun=0 | **PASS** |
| Native integrity | phantom=0, duplicate=0, drain_empty=true | **PASS** |
| Completion | final_cycle=59426, observed_cycles=59427 | **PASS** |
| Identity scope | `identity_order_independence_claimed=false` | explicit limitation |
| Verification report SHA-256 | `19d2ff34720b4fa594eb8dc847f47c383e20e16b14727a5a4e353a8d0f6a375e` | bound |
| Raw CYCLE ledger SHA-256 | `7096a7b0ebdc2fd50cda31e4c977cd4ed1a7e75585174d17051fe89cbab89ea8` | bound |
| Native evidence archive SHA-256 | `28401809a244571f084d01a2cc950ad381fc393f8b9a747364c45abbb16e8610` | bound |
| Polarity release tests | 14/14 PASS | **PASS** |
| Bridge regression | 189 PASS, 4 environment-gated SKIP | **PASS with disclosed skips** |
| Polarity-v1 functional release gate | all required bound checks closed | **GO** |

The native evidence is source-FIFO polarity-sequence evidence. Identical
same-source events with equal polarity cannot be distinguished observationally,
so execution-time event-ID order independence is deliberately **not** claimed.
The zero-loss, zero-phantom, zero-duplicate result must be stated together with
that identity scope.

The test totals above are authoritative integrated-evidence results. This
documentation follow-up inspected their committed records and hashes; it did
not rerun Xcelium or either test suite and therefore does not claim a new run.

## 3. Full-row `pol_mask` semantics

For each valid retired lane, `rowN` selects a row and `pol_maskN` exposes that
row's full four-bit polarity slice. `col_maskN` selects which column positions
are retired events. Therefore:

- only `pol_maskN[col]` bits for which `col_maskN[col] == 1` are meaningful and
  must equal the corresponding source FIFO's front polarity;
- `pol_maskN` bits outside the asserted `col_maskN` may be nonzero, are
  don't-care/non-events, and must not be scored as retired polarity; and
- `validN`, `rowN`, and `col_maskN` define the retired population; `pol_maskN`
  is polarity sideband for only that selected population.

This is full-row-mask behavior, not an error or evidence of extra events.
Invalid lanes remain canonical all-zero as required by the release gate.

## 4. Minimal GO/HOLD decision tree

1. If the claim concerns the legacy `41e2999` address-only result, use only its
   sealed evidence and address-only claim limits: **GO in legacy scope**.
2. If the claim concerns pinned polarity-v1 native transport at Ganghee
   `44f8918...`, require receipt `29d7856...`, integration `c3d0a24...`, the
   report/ledger/archive hashes above, and the recorded regressions: all are
   present, so **GO**.
3. If a claim treats unselected `pol_mask` bits as events or claims independent
   event-ID ordering, it exceeds the verified semantics: **HOLD**.
4. If a claim concerns polarity-v1 P&R availability, upstream `9b0d951...`
   establishes artifact availability: **GO for existence only**.
5. If a claim concerns polarity-v1 PPA/signoff, exact Fmax, workload-activity
   power, or wire-complete CAV/world RTL/PPA, the functional release evidence
   does not establish it: **HOLD pending separate qualified physical or system
   authority**.

Functional polarity GO does not imply physical signoff, PPA qualification, or
wire-complete CAV/world implementation.
