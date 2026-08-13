# Exact-three Genus server cohort package

This directory packages, but does not execute, the physical campaign.  The
launcher accepts one runtime `PROVEN_ENVIRONMENT` receipt and exactly three
completed Genus receipts in the fixed order Fovea+A7, A2+P6, A3+P6.  It never
invokes Genus, Innovus, Xcelium, or a shell command.

The accepted producer is the committed exact-three Genus v3 contract at
`62d792e9f16c3052442521417b66b5aaf4d86d78`; both its runner and registry
bytes are pinned. Legacy v1 receipts and receipts from a merely similar flow
are rejected.

Each candidate gate verifies the producer's attempt/environment/source binding,
mapped netlist, SDF, mapped SDC, exact area/timing/power report set, endpoint
connectivity map, staged-vs-mapped functional PASS, Innovus handoff, and Genus
normal-exit log.  Only then is the complete attempt tree copied into the unique
cohort root.  All three must share the same exact Genus flow-file map and Git
head.  Symlinks, hard links, reused roots/inodes, mutations, missing artifacts,
HOLD receipts, and noncanonical ordering fail closed.

The unique root is created exclusively and is never overwritten.  Successful
and failed runs retain `logs/launcher.log`; a failed candidate also retains its
readable source logs when that can be done safely.  Candidate gates are emitted
one at a time, so a failure prevents later candidate gates.  A successful
manifest states `eda_launch_performed=false` and physical qualification remains
HOLD pending separate Innovus and final qualifier PASS.  There is no skip,
force, allow-HOLD, or qualification-bypass option.

Server-side packaging after the three Genus attempts exist:

```sh
python3 physical/k2_w4_server_cohort/launch_server_cohort.py \
  --environment-receipt /server/preflight/PROVEN_ENVIRONMENT.json \
  --genus-receipt fovea_a7=/server/genus/fovea/receipt.json \
  --genus-receipt a2_p6=/server/genus/a2/receipt.json \
  --genus-receipt a3_p6=/server/genus/a3/receipt.json \
  --output-parent /server/campaigns --attempt k2-physical-ATTEMPT_ID
```

Run local fixture and mutation tests with
`tests/k2_w4_server_cohort/run_all.sh`.
