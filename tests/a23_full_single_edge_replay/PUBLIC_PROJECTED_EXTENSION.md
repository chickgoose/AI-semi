# Actual single-edge replay of the public projected UZH extension

This extension executes the three deterministic projected
`shapes_rotation` inputs against the pinned hardened A2 and A3 single-edge
RTL. Its status is always `PUBLIC_PROJECTED_EXTENSION`; release and selection
remain `HOLD`.

The source window contains exactly 1,100 public projected events. `1x`, `64x`,
and `256x` are three timing projections of those same identities in the same
order. They produce three executions per owner but must never be pooled or
described as 3,300 unique events. They are neither official nor canonical
REDRED traffic, and P6 provenance is forbidden.

The runner pins and verifies the projection receipt/completion, scenario
order, 1,100 rows per trace, all JSONL fields, event identity/order, trace
hashes, the projection source Git objects, the hardened replay pins, every
actual RTL/filelist byte, and the complete Verilator toolchain. Each projected
trace is prepared once; that exact prepared file is passed to both A2 and A3.

Every actual run reports generated, source overrun, accepted, retired,
fixed-window throughput, occurrence-to-accept latency, and accept-to-retire
latency. PASS requires per-scenario event-level exact-once/order and:

```text
generated = source_overrun + accepted
accepted = retired after clean drain
```

The extension additionally runs one clean-drain reset scenario and one
count-two mutation activation per owner, then separately compiles and executes
drop, duplicate, reorder, and reset-escape source mutants for both owners.

## Retained export

`public_projected_export.tar.gz` is an explicit sealed export bundle. It
contains the source projection receipt/completion/license/projected JSONL and
all three input traces, prepared numeric traces and manifests, per-run event
and summary CSVs, simulation/build logs, copied mutant RTL, and the replay
result. `public_projected_publication.json` binds the bundle, its internal
manifest, the result, and the immutable package commit by SHA-256.

Run with the pinned projection directory:

```sh
REDRED_UZH_PROJECTION_DIR=/tmp/redred-uzh-shapes-projection-f59c10e \
  tests/a23_full_single_edge_replay/run_public_projected_all.sh
```

Run static and adversarial contract tests:

```sh
python3 -m unittest \
  tests/a23_full_single_edge_replay/test_public_projected_extension.py
```
