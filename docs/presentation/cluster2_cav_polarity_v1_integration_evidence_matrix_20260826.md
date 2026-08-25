# Cluster2 CAV polarity-v1 integration evidence matrix and status

Date: 2026-08-26

Working branch: `work/polarity-a8-evidence`

Status: **PROPOSED INTEGRATION EVIDENCE — POLARITY-V1 HOLD**

This document does not replace or extend the sealed first-round result. It
records the evidence boundary between the existing address-only authority and
the proposed Ganghee polarity-v1 integration. No canonical result JSON is
modified by this work.

## 1. Two authorities that must remain separate

### Existing sealed address-only authority

The public presentation authority remains:

- repository: `https://github.com/chickgoose/AI-semi.git`;
- branch: `integration/cluster2-steal-buf-cav-bridge`;
- live branch HEAD observed on 2026-08-26:
  `41e2999b55235ad9ccd0a0b13bfdc7642ef5b20b`;
- final first-round evidence-package checkpoint:
  `f5109974236d297b5b60b0f1c18aecc4c1d184e6`;
- pinned Ganghee source commit:
  `5ac1f0e3c0e6991558afa699e64680f708ff625d`;
- pinned address-only RTL:
  `rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v`, SHA-256
  `56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96`;
- native authority JSON SHA-256:
  `90e659358423368ce6a27850cdffa36a0eb85cea508babc66e72ecafb8e70530`.

That authority covers the already sealed address-only native observation and
the observational software CAV/world projection described by the first-round
status, briefing, evidence matrix, receipt, and canonical result. It does not
contain a polarity RTL input/output, a hardware-polarity transport ledger, or
the Ganghee polarity P&R sweep.

Nothing in the proposed polarity-v1 track may be attributed to that authority,
and its sealed counts, digests, receipt, and canonical result JSON must not be
silently reinterpreted as polarity-aware evidence.

### Proposed Ganghee polarity-v1 input authority

The proposed new input is a separate upstream track in
`https://github.com/GangHeeJo/AI-SEMI.git`:

- polarity-v1 RTL:
  `rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v`;
- SHA-256 at ledger commit `b9f2b76` and unchanged at observed Ganghee live
  `main` `44f8918c6e0085f7b75bb90fbe6c099abe1882cc`:
  `20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81`;
- hardware-polarity ledger commit:
  `b9f2b76cc3c5fcfeee071193a2fbcb70aa35f55d`;
- committed polarity-v1 P&R sweep commit:
  `9b0d95121cf88ba55bee13cf0e5d444d688010b6`.

The proposed track is specifically **v1**. The separate
`aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity_v2.v` implementation is
not selected or covered by this matrix.

Upstream `b9f2b76` contains these observed ledger artifacts:

| Upstream artifact | Observed SHA-256 | Scope in this document |
| --- | --- | --- |
| `common_traces_uzh/event_logger_out/uzh_shapes_rotation_patch.polarity_manifest.json` | `b780d2be9c026acaffb51d9a391d1728f0820603b260f82b35e4d52571052993` | unqualified input |
| `common_traces_uzh/event_logger_out/uzh_shapes_rotation_patch.aer_transport_polarity.jsonl` | `518a2a5ba977516ea687fdc23a9246ff9cfe90fbf3d013efdd358200596e9cd3` | unqualified input; 8,503 rows observed |
| `common_traces_uzh/event_logger_out/uzh_shapes_rotation_patch.polarity_eventlog.txt` | `28ebcecf999b995a2c403e3d2e44896f3dfdaa7cba34e927b945b2503cc80b83` | unqualified raw ledger input |
| `tb/tb_steal_buf_polarity_event_logger.v` | `2921f63274939c69aca6b0c392a5efe34acbb9dd0378426be2a74dba65bc4634` | unqualified producer input |
| `scripts/join_polarity_event_logger_output.py` | `ab1e00238a21c5b8cd1298037e1e64bf38e06f219005ace74f526d8a5b6873ad` | unqualified joiner input |

The upstream manifest records 8,503 generated and delivered events, zero
overrun, and zero polarity mismatches. Those are upstream-recorded values, not
an integration PASS. This documentation change did not execute the producer,
joiner, simulator, bridge suite, or P&R flow.

## 2. Current decision matrix

| Claim or gate | Existing `41e2999` evidence | New polarity-v1 material | Current decision | Exact closure required |
| --- | --- | --- | --- | --- |
| Existing first-round address-only authority remains applicable within its stated scope | sealed first-round package at `f510997` | no dependency | **GO_EXISTING_ADDRESS_ONLY_SCOPE_ONLY** | preserve its authority, receipts, canonical result JSON, and speaker limits unchanged; this documentation commit does not claim a new reproduction |
| Polarity-v1 RTL source identity | absent | upstream v1 path and observed SHA-256 above | **HOLD_NOT_IMPORTED_OR_PINNED** | commit an integration-owned authority that binds repository URL, exact commit/blob, path, SHA-256, module/ports, filelist, and all RTL dependencies |
| Hardware polarity survives native transport | not tested; polarity is not a native payload | upstream ledger and manifest exist | **HOLD_NOT_INDEPENDENTLY_REPLAYED** | regenerate from pinned inputs; verify address, source, lane, order, occurrence, overrun, conservation, and hardware polarity against an independent oracle |
| Polarity failures are detectable | no polarity mutation gate | no integration mutation receipt | **HOLD_NO_POLARITY_FALSIFIER** | kill at least polarity constant/drop, inversion, and lane/source association swap mutations with exact diagnostics |
| Ledger provenance is integration-grade | address-only ledger has its own sealed authority | upstream polarity ledger is not consumed | **HOLD_LEDGER_AUTHORITY_OPEN** | bind raw trace, polarity input, RTL, TB, tool/version, compile/run commands, raw log, joiner, output ledger, manifest, canonical encoding, and SHA-256 closure; reject inconsistent internal identities |
| CAV source polarity is caused by hardware-retired polarity | existing software event polarity comes from source-side authority | hardware ledger is not joined into the official bridge | **HOLD_CAUSAL_CROSSWALK_OPEN** | prove one-to-one event identity and polarity equality from native retirement through the bridge input without TB-only polarity substitution |
| Existing CAV/world geometry claims transfer unchanged | sealed occurrence-time software projection | polarity semantics may be added without changing geometry, but this has not been integrated | **HOLD_NO_AUTOMATIC_CARRYOVER** | regenerate a polarity-v1 result/receipt and compare population, identity, time semantics, geometry digest, and allowed differences explicitly |
| Polarity-v1 P&R artifacts exist | existing matrix says separate server-local diagnostic | upstream sweep files are committed at `9b0d951` | **GO_ARTIFACT_AVAILABILITY_ONLY** | none for the narrow existence statement |
| Polarity-v1 P&R is release-qualified | no release-bound authority | raw reports/logs are upstream but unparsed here | **HOLD_PPA_NOT_IMPORTED_OR_QUALIFIED** | pin complete top/filelist/netlist/SDC/MMMC/TCL/tool/library/corner/activity/report/log closure; independently extract setup, hold, unconstrained paths, route/connectivity, DRC, antenna, area, power, warnings, and terminal errors |
| Polarity-v1 presentation authority | absent | this matrix is proposed documentation only | **HOLD_NO_SEALED_RESULT_OR_FRESH_CLONE_RECEIPT** | complete every applicable gate, commit generated receipts/results, verify fresh-clone reproduction, then name the exact publication commit and permitted claims |
| Wire-complete CAV/world RTL and its PPA | HOLD in first-round authority | not supplied by polarity-v1 native AER work | **HOLD_OUT_OF_SCOPE_AND_UNIMPLEMENTED** | separate synthesizable interface, functional evidence, source closure, and qualified physical flow |

## 3. GO/HOLD promotion sequence

Polarity-v1 may be promoted only in this order:

1. **Source authority GO:** exact v1 commit/blob and complete source closure are
   imported and hash-bound. A branch name or upstream `main` alone is not an
   authority.
2. **Producer/ledger GO:** a clean pinned execution regenerates the raw ledger,
   canonical polarity ledger, and manifest; independent validation and polarity
   mutations pass.
3. **Bridge GO:** the integration crosswalk proves that CAV input polarity is
   the hardware-retired polarity for the same event identity. Address-only
   sidecar reconstruction is not polarity-v1 evidence.
4. **Functional-result GO:** a new polarity-v1 result and receipt are generated
   under a distinct schema/name. Existing canonical first-round JSON is not
   overwritten.
5. **PPA GO, if claimed:** the `9b0d951` bundle is imported and a trusted parser
   reproduces all mandatory disclosures from pinned raw evidence. Committed
   files alone do not satisfy this gate.
6. **Presentation GO:** a clean fresh clone reproduces the declared suites and
   receipts, with exact executed counts and skips recorded only after the run.

Any failed or unexecuted step leaves that step and all dependent claims HOLD.
Physical and functional GO are separate: neither implies the other.

## 4. Tests and evidence run for this document

No RTL simulation, polarity replay, unit suite, mutation suite, PPA parser,
Genus, Innovus, or fresh-clone reproduction was run for this documentation
change. The only completed audit actions were read-only Git ref, ancestry,
path, commit, and artifact-hash inspection. Accordingly, this matrix records no
new functional or physical PASS.
