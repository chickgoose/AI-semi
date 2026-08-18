# Complete-endpoint default-vectorless power qualification

This directory defines a separate **Genus default-vectorless** mapped-power
evidence class for the exact Fovea+A7, A2+P6, and A3+P6 complete endpoints. It
does not replace activity-annotated VCD/SAIF power and cannot be cited as such.
Every result keeps `activity_annotated=false`.

## Frozen comparison

- exact order: `fovea_a7`, `a2_p6`, `a3_p6`;
- exact technology-staged complete endpoints from the native Genus registry;
- GPDK045 slow setup/power Liberty at 0.9 V / 125 C, separately pinned fast
  hold Liberty, macro LEF, and shared typical QRC;
- 6.5 ns ref/sample timing cohort, 0.10/0.50 ns min/max input delay, and
  0.01 pF output load;
- exact Cadence Genus 23.14-s090_1 entrypoint, resolved executable, and SHA;
- native Genus default sequential-element activity `0.2` and primary-input
  activity `0.2`; and
- no VCD/SAIF import and no per-object `set_switching_activity` command.

The vectorless driver is the byte-exact existing
`physical/k2_w2_genus/genus_driver.tcl`. This layer does not patch it or replace
`run_genus.DRIVER_TCL`. A power report must contain exactly these native Genus
headers and values:

```text
* User-Defined Activity : N.A.
* Activity File: N.A.
* Sequential Element Activity: 0.200000
* Primary Input Activity: 0.200000
```

Anything imported, user-defined, absent, duplicated, or different is rejected.

## Fail-closed evidence model

`vectorless-evidence.json` is only an index of the three canonical native
`run_genus` attempt directories. It contains no caller-supplied artifact hashes
and no `execution_class` string. The qualifier re-reads canonical files from
each attempt directory and checks:

- exact attempt and receipt schemas and keys, exact command/tool/version/path/
  hash, common server receipt, and native PASS status;
- the complete frozen runner/RTL/filelist inventory, staged source bytes, top,
  materialized SDC, and technology snapshot bytes;
- all six required reports and the actual Genus log, including the native
  version, `W2_GENUS_PASS`, `Normal exit.`, and zero Error/Fatal summary;
- the actual mapped-netlist bytes, SDF, mapped SDC, complete endpoint
  connectivity map, and independently recomputed whole-top/endpoint inventory;
- mapped-functional GO plus its SDF/netlist/model/simulator/hook/testbench/log/
  filelist bindings; and
- the `PROVEN_ENVIRONMENT` receipt, with no unknown top-level or gate fields.

Unknown fields, contradictory fields, path escapes/symlinks, hash mutations,
non-Cadence receipts, caller-authored PASS strings, and incomplete reports fail.
All artifact files are stable-read as regular single-link files.

### Current producer limitation: HOLD

The inherited `k2_w2_genus_exact_three_endpoint_receipt_v3` is strong but does
not publish all facts required to promote this new evidence class to GO. The
native producer must add and bind at least:

1. `genus_log_sha256` owned by the native producer;
2. `genus_exit_code=0` in that receipt; and
3. a producer-owned hash of a complete canonical attempt-artifact inventory;
4. the actual executed argv, cwd, and relevant environment (the current
   attempt records a normalized relative argv while execution uses an absolute
   driver path);
5. mapped-functional hook stdout/log hashes, its exit code and PASS sentinel,
   and a pinned approved functional-model set; and
6. a trusted producer attestation/signature or verifier-owned immutable
   execution root. Unkeyed self-hashes establish consistency, not ownership.

This directory does not forge those facts downstream. Therefore a fully intact
current-v3 real server attempt ends
`HOLD_INHERITED_GENUS_V3_PRODUCER_RECEIPT_INCOMPLETE`, and a synthetic fixture
can only HOLD or FAIL—never QUALIFIED/GO. A future producer receipt must retain
the current complete source/connectivity/functional proofs and update the
pinned runner contract and qualifier before GO can be enabled here.

## Local preflight (no Cadence)

```sh
python3 physical/k2_endpoint_vectorless/vectorless.py preflight \
  --output /tmp/k2-endpoint-vectorless-preflight.json
```

This only validates local contracts, registries, staged boundary, timing
profile, and the untouched native driver. It publishes
`HOLD_NO_REAL_SERVER_ARTIFACTS`; it never probes or launches Cadence.

## Exclusive server execution

`execute` retains the existing `run_genus.run_flow` interface and requires a
new, nonexistent output root. It does not edit or wrap the runner:

```sh
python3 physical/k2_endpoint_vectorless/vectorless.py execute \
  --genus /tools/cadence/DDI231/GENUS231/bin/genus \
  --library /absolute/slow_vdd1v0_basicCells.lib \
  --hold-library /absolute/fast_vdd1v0_basicCells.lib \
  --cell-lef /absolute/gsclib045_macro.lef \
  --shared-qrc /absolute/gpdk045.tch \
  --golden-archive /absolute/ganghee-pnr-golden-20260813.tar.gz \
  --raw-golden-archive /absolute/ganghee-pnr-raw-golden-20260813.tar.gz \
  --functional-loss-archive /absolute/eval-fovea-cluster2.tar.gz \
  --server-environment-receipt /absolute/server-environment-go.json \
  --mapped-functional-hook physical/k2_w2_genus/run_mapped_functional_xcelium.py \
  --functional-model /absolute/gsclib045_functional.v \
  --output-root /absolute/new-exclusive-result-root \
  --attempt-prefix endpoint-default-vectorless-6p5
```

With the inherited v3 producer this command completes the native attempts and
then publishes the documented HOLD, not GO.

An existing attempt index can be checked without running EDA:

```sh
python3 physical/k2_endpoint_vectorless/vectorless.py qualify \
  --evidence /absolute/vectorless-evidence.json \
  --output /absolute/vectorless-qualification.json
```

Run focused tests with:

```sh
tests/k2_endpoint_vectorless/run_all.sh
```
