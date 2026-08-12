# W7-A2 provenance audit (2026-08-13 KST)

Read-only server inspection established these identities:

| Location | Git identity/status | Scalar Fovea evidence |
|---|---|---|
| `/home/aiasic26911/redred-faer` | `main` at `5e564d7f3aebc561ef5419f1a09b64deba9f1276`; dirty | `rtl/aer_tx16_trad_rowcol_fovea.v` is **untracked**; SHA256 `353ffa6e...f806e`. This is origin evidence, not an immutable qualification source. |
| `/home/aiasic26911/semi-ai` | `main` at `ba603bd409d4b086a8476c6ee600c1814acafde0`; the three files are tracked and clean | Introduced by `0558c89285acd79f639d3e2c5e11fddb204d3e13` (`2026-08-09 16:20:40 +0900`). Top/arbiter SHA256 values are `353ffa6e...f806e`, `25d2ffcf...a684`, `108d3ddf...e31`. |
| local `/home/chickgoose/projects/a1` | integration commit `2a3a3be94be8f12585f484b5b1da2b372f7282d9`; unrelated untracked files exist | Its three tracked A5 structural fixtures are byte-identical to the semi-ai blobs. The W7 runner reads the commit, not the worktree. |

Exact native top and filelist:

1. `arbiter2.v` — SHA256 `25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684`
2. `arbiter4_tree.v` — SHA256 `108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31`
3. `aer_tx16_trad_rowcol_fovea.v` — SHA256 `353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e`

This order and top are independently present in tracked server files
`semi-ai/synth/resynth_pnr_sweep_fovea_raw.sh:5-6` and
`semi-ai/synth/run_genus_fovea.tcl:20-26`.  The former has SHA256
`f9699d03...dc5e`; the latter has `93bd2d39...8acb`.  They are provenance
evidence only: W7 neither runs nor edits either synthesis script.

The server exposes Xcelium at
`/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun`, version `23.09-s013`.
This audit only queried its version; it did not create a server result or
alter a server repository.  `/home/aiasic26911/AI-semi/integration` is dirty at
`0b327d05a3b8b0e236108d9544d33db5ccab6cee` and is intentionally excluded.

Frozen common inputs are A1 commit `2a3a3be...`: generator-v4 SHA256
`59b649a1...1b50`, full50 manifest `9fe40060...bba9`, capacity22 manifest
`99a8bbd3...8c62`, common TB `27d9437a...0a2`, and native binding
`26f988e3...cdef`.  The executable contract carries the complete digests.

Decision: provenance/preparation **GO**; new Xcelium qualification
**PENDING_HEAD_XCELIUM**.  A functional GO must not be inferred from this
read-only audit or from older server results.
