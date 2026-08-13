# K2 physical W2 raw-report qualifier

## yZr1 functional evidence: loss only

The latest functional archive is `eval-fovea-cluster2.yZr1kmYL.tar.gz`,
SHA-256 `22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39`.
The archive retains the original attempt path in `provenance.txt` even if the
tarball is copied elsewhere. The qualifier normalizes ledger paths only against
that exact provenance path; it never rewrites or guesses an attempt identity.

```sh
python3 physical/k2_w2_qualifier/qualify_functional_yzr1.py \
  --archive /tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz \
  --output /writable/new-yzr1-loss-only-receipt.json
```

The archive has 344 files. `result-artifacts.sha256` must contain exactly 338
unique, hash-correct rows and equal the complete `results/**` closure. The six
remaining root files are exactly the provenance, 50/22 stem inventories, two
candidate run logs, and ledger. Both candidates must have the exact 50 full
runs, 22 capacity runs, 50 ordered `RUN_PASS` markers, one reset PASS, final
pairwise status zero, and no run failure marker. Every trace CSV must satisfy
`generated = source_overrun + accepted`, `accepted = delivered`, and
`errors = 0`; the corresponding trace log must independently report one PASS
and one metrics record.

The actual full50 loss accounting is:

| Candidate | Generated | Source overrun | Accepted | Delivered |
|---|---:|---:|---:|---:|
| Fovea | 106416 | 28187 | 78229 | 78229 |
| Cluster2 | 106416 | 12259 | 94157 | 94157 |

`source_overrun` is ingress capacity loss, not corruption of an accepted
event. The receipt status is
`WORKSPACE_DIFF_FUNCTIONAL_LOSS_EVIDENCE_GO`: `provenance.txt` explicitly says
`binding_reset_quiet_arming_patch=workspace-diff`, so this is not an official
common receipt. It may support only workload loss accounting and accepted-event
conservation. It must never support area, timing, power, energy, or other PPA
claims.

The stale outer `eval-driver-final.log` naming attempt `0FfaT8kp` is neither in
the yZr1 archive nor part of its trust closure. The qualifier explicitly
rejects any packaged outer driver log and records it as
`STALE_0Ffa_NOT_IN_ARCHIVE_NOT_BOUND`. Only yZr1 `provenance.txt`, both candidate
run logs, the 338-entry ledger, and the immutable archive are bound.

## Authoritative Ganghee fixture

### Raw canonical endpoint sweep

The canonical raw-endpoint archive is
`ganghee-pnr-raw-golden-20260813.tar.gz`, SHA-256
`7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3`.
It is a distinct 215-file fixture and must not be substituted for, or combined
with, the buffered 302-file archive below. Run it with the exact profile:

```sh
python3 physical/k2_w2_qualifier/qualify_ganghee_golden.py \
  --profile raw \
  --archive /tmp/ganghee-pnr-raw-golden-20260813.tar.gz \
  --output /writable/new-ganghee-raw-golden-receipt.json
```

The ten real Innovus late-path points are:

| Design | Period (ns) | WNS (ns) | Path result |
|---|---:|---:|---|
| Raw Fovea | 1.2 | 0.000 | MET |
| Raw Fovea | 1.3 | -0.024 | VIOLATED |
| Raw Fovea | 1.4 | 0.036 | MET |
| Raw Fovea | 1.6 | -0.003 | VIOLATED |
| Raw Fovea | 2.0 | -0.007 | VIOLATED |
| Raw Cluster2 | 0.7 | -0.178 | VIOLATED |
| Raw Cluster2 | 0.8 | -0.088 | VIOLATED |
| Raw Cluster2 | 0.9 | -0.029 | VIOLATED |
| Raw Cluster2 | 1.0 | 0.042 | MET |
| Raw Cluster2 | 1.3 | 0.080 | MET |

Raw Fovea is explicitly `NON_MONOTONIC_HOLD`: slack decreases at 1.2→1.3,
1.4→1.6, and 1.6→2.0 ns, including MET→VIOLATED reversions at 1.2→1.3
and 1.4→1.6 ns. Consequently its receipt has no qualified bracket and no
selected period. Selecting the isolated 1.4 ns pass is forbidden
cherry-picking.

Raw Cluster2 has a monotonic observed fail/pass transition between 0.9 and
1.0 ns, but this is recorded only as
`observed_transition_not_a_qualified_bracket`. It remains
`MONOTONIC_OBSERVED_HOLD` with no selection because none of its full period
records passes all W2 gates. Both raw designs have `no_drive=18`; all ten
Innovus logs contain two explicit errors, and the same missing connectivity,
TNS/violation-count, recovery/removal, external-input hashes, and process-exit
evidence apply. The raw campaign result is therefore PASS 0 / FAIL 10.

Sweep analysis is always ordered by the pinned expected period inventory, not
archive order. Any missing point produces `MISSING_DATA_HOLD`. Even a test
mutation that makes all WNS values monotonic cannot produce a bracket while
period qualification is incomplete. Appended PASS/SELECT sentinels are ignored
and invalidate the tool-log terminal marker.

### Buffered resynthesis sweep

The authoritative server update is represented by the immutable archive
`ganghee-pnr-golden-20260813.tar.gz`, SHA-256
`1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.
It contains 302 regular files and 14 period points: Fovea at 0.8, 1.0, 1.2,
1.4, 1.6, 1.8, 2.0, 2.2, and 2.5 ns, and Cluster2 at 0.8, 1.0, 1.3, 1.6,
and 2.0 ns.

`qualify_ganghee_golden.py` reads the gzip tar directly without extracting it,
rejects links, unsafe/duplicate paths, unexpected inventory or size, and any
archive hash mismatch. Its receipt hashes every archived member. A fixture
named `golden` is not presumed to pass.

```sh
python3 physical/k2_w2_qualifier/qualify_ganghee_golden.py \
  --archive /tmp/ganghee-pnr-golden-20260813.tar.gz \
  --output /writable/new-ganghee-golden-receipt.json
```

The authoritative archive produces exit 2 and a diagnostic receipt, not a
PASS. All 14 points fail the W2 qualification gates. The following numbers are
parsed from the actual worst-path reports; they are not synthetic fixtures.

| Design | Periods (ns) | Genus WNS (ns) | Innovus late WNS (ns) | Innovus early WNS (ns) | no_drive | Innovus errors |
|---|---:|---:|---:|---:|---:|---:|
| Fovea | 0.8 | -0.341 | -0.349 | -0.044 | 19 | 2 |
| Fovea | 1.0 | -0.125 | -0.301 | 0.106 | 19 | 2 |
| Fovea | 1.2 | 0.000 | -0.128 | 0.079 | 19 | 2 |
| Fovea | 1.4 | 0.001 | 0.023 | 0.086 | 19 | 2 |
| Fovea | 1.6 | 0.001 | 0.022 | 0.067 | 19 | 2 |
| Fovea | 1.8 | 0.010 | 0.042 | 0.080 | 19 | 2 |
| Fovea | 2.0 | 0.116 | 0.005 | 0.073 | 19 | 2 |
| Fovea | 2.2 | 0.176 | 0.058 | 0.088 | 19 | 2 |
| Fovea | 2.5 | 0.470 | 0.107 | 0.083 | 19 | 2 |
| Cluster2 | 0.8 | -0.048 | -0.096 | 0.060 | 20 | 2 |
| Cluster2 | 1.0 | 0.004 | 0.012 | 0.070 | 20 | 2 |
| Cluster2 | 1.3 | 0.043 | 0.016 | 0.040 | 20 | 4 |
| Cluster2 | 1.6 | 0.343 | 0.253 | 0.051 | 20 | 4 |
| Cluster2 | 2.0 | 0.777 | 0.652 | 0.070 | 20 | 4 |

Every point also lacks a connectivity report, explicit TNS and violation-count
summaries, independent recovery/removal coverage, attached process exit codes,
and hash-bound tool executables and Liberty/LEF/QRC inputs. Every Innovus log
has explicit `**ERROR` lines and a nonzero final error summary. DRC and antenna
zero summaries are retained as passing sub-gates; they cannot override the
failed qualification. Scan/ICG facts are derived from the Genus scan-type rows,
mapped netlist ICG instances, Innovus final ICG inventory, and no-scan-chain
diagnostic.

Exit meanings are: 0 only if every real period passes, 2 when a complete pinned
archive is interpreted but one or more periods fail, and 1 for a malformed,
unbound, changed, or unpublishable archive/receipt. A classification failure
still publishes the exclusive diagnostic receipt; an existing output is never
overwritten.

The test suite requires this exact archive at the path above, or at
`K2_GANGHEE_GOLDEN_ARCHIVE`. It runs the real reports and applies negative
mutations to the archive pin, report completeness, tool/top provenance,
timing, constraint coverage, error/clean-exit evidence, scan/ICG, DRC, and
antenna parsers.

## Future machine-recorded bundles

This independent parser converts a hash-bound Genus/Innovus raw artifact bundle
into a canonical receipt. It targets the locally recorded server builds Genus
`23.14-s090_1` and Innovus `23.14-s088_1`.

Production PASS requires all declared sources, tool executables, commands,
constraints, technology inputs, raw reports, netlists, smoke evidence, and
clean markers to match their SHA-256 references as stable regular files. The
ordered RTL file list must equal the source closure and contain the declared
top. Tool commands and their environment are exact, and both tool exits must be
zero.

Raw reports must contain the following exact machine records in addition to
their normal human-readable text:

- `W2_DESIGN`: zero unresolved/blackbox/unmapped and nonzero mapped instances;
- `W2_COVERAGE`: exactly the six classes `unconstrained_paths`, `no_clock`,
  `no_input_delay`, `no_output_delay`, `no_drive`, and `no_load`, all zero;
- `W2_TIMING`: setup, hold, recovery, and removal with nonzero path coverage,
  zero violations, nonnegative WNS and TNS;
- `W2_SCAN_ICG` and `W2_ICG`: no scan/dangling/unrecognized objects and exact
  expected ICG inventory at both mapped and placed boundaries;
- mapped smoke: nonzero vectors/events, conservation, no mismatch or X/Z;
- Innovus placement, DRC, connectivity, and antenna summaries, all clean.

The separate clean marker must equal the final nonempty tool-log line and bind
the run ID and top. Error/fatal diagnostics anywhere in the text artifacts are
fatal. Warning lines are counted in the receipt but do not become physical
claims.

Run from the repository root:

```sh
python3 physical/k2_w2_qualifier/qualify_raw.py \
  --bundle-root /path/to/run-bundle \
  --manifest /path/to/run-bundle/manifest.json \
  --output /writable/new-result.json
```

The output path is exclusive: an existing file or symlink is never overwritten.
W2 qualifies raw Genus/Innovus structural/timing/implementation gates only.
Activity-annotated power, energy/event, signoff STA, and foundry signoff DRC
remain HOLD.
