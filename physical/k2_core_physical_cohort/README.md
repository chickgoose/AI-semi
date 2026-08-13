# Kanghee Fovea/Cluster2 core-only physical cohort

This directory implements exactly two comparable rows: the raw Kanghee
`aer_tx16_trad_rowcol_fovea` core and the raw
`aer_tx16_trad_rowcol_fovea_cluster2` core. It does not use a wrapper,
endpoint, common testbench, or candidate workload. The team-owned RTL and
common TB remain read-only. Source bytes are selected from the pinned raw
archive only after the archive SHA-256, member uniqueness, regular-file type,
member order, and member SHA-256 all pass.

Both rows use the same 5.0 ns clock, 0.25 ns clock uncertainty, 0.5 ns input
and output delays, 0.01 pF output load, slow setup Liberty, fast hold Liberty,
shared typical QRC, LEFs, CoreSite floorplan, 35% target utilization, 10 um
margins, pin policy, and power-ring geometry. Genus clock-gating insertion is
enabled and scan mapping is not enabled. Power reports are deliberately
labelled vectorless screening only; they are not activity-based power or
signoff evidence.

## Fail-closed lifecycle

The plan command performs no EDA work and writes with create-exclusive
semantics:

```sh
python3 -B physical/k2_core_physical_cohort/core_cohort.py plan \
  --source-archive /tmp/ganghee-pnr-raw-golden-20260813.tar.gz \
  --output /absolute/new/path/core-plan.json
```

On the server, first generate a current `GO` receipt with
`physical/k2_w2_server_env/preflight.py`. Preparation revalidates that receipt,
the exact tool identities, all technology roles, the plan, templates, archive,
and source members. Its output root must not exist:

```sh
python3 -B physical/k2_core_physical_cohort/core_cohort.py prepare \
  --plan /absolute/path/core-plan.json \
  --server-environment-receipt /absolute/path/server-go.json \
  --output-root /absolute/new/path/core-run
```

Native execution is intentionally a separate operation. It requires the
literal authorization value `I_UNDERSTAND_THIS_LAUNCHES_GENUS` or
`I_UNDERSTAND_THIS_LAUNCHES_INNOVUS`. Innovus additionally refuses to start
until the same row has a valid, hash-bound Genus execution receipt. Each stage
uses a new output directory and writes its receipt only after the native
version, zero-error completion, expected reports, and output hashes pass.
Innovus also requires closed setup/hold machine summaries plus native zero DRC,
antenna, and connectivity evidence. `seal` validates all four execution
receipts and their artifacts before creating the final two-row receipt.

## Runtime-only prerequisites

- `/tmp/ganghee-pnr-raw-golden-20260813.tar.gz`, or another path containing
  exactly the pinned archive bytes.
- The live PDK root and exact Liberty/LEF/QRC bytes named in `contract.json`.
- Genus 23.14-s090_1 and Innovus 23.14-s088_1 at the pinned paths and resolved
  executable SHA-256.
- A fresh server-environment `GO` receipt bound to the pinned upstream
  environment contract.
- New plan, prepared-root, stage-output, and final-receipt paths. Nothing is
  overwritten.

Local tests are non-EDA:

```sh
TMPDIR=/dev/shm tests/k2_core_physical_cohort/run_all.sh
```
