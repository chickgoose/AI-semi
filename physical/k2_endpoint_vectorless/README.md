# Complete-endpoint vectorless power qualification

This path qualifies a separate **vectorless** Genus mapped-power evidence
class for the exact Fovea+A7, A2+P6, and A3+P6 complete endpoints. It does not
replace activity-annotated VCD/SAIF power and cannot be cited as such.

The implementation reuses `physical/k2_w2_genus/run_genus.py` for the complete
source/boundary registry, staged top hashes, GPDK045 server preflight, strict
SDC, mapped netlist and endpoint inventory, mapped functional proof, report
production, and tool checks. The only derived flow content is a switching
activity stanza inserted immediately before the existing `report_power` call.
The derived driver is byte-bound in every attempt and rechecked by the
qualifier.

## Frozen comparison

- exact order: `fovea_a7`, `a2_p6`, `a3_p6`;
- exact technology-staged complete endpoints from the existing Genus registry;
- GPDK045 slow setup/power Liberty, 0.9 V / 125 C, with its pinned SHA;
- separate pinned fast hold Liberty, macro LEF, and shared typical QRC identity;
- 6.5 ns ref/sample timing cohort;
- input delay 0.10 ns minimum and 0.50 ns maximum;
- 0.01 pF load on every output;
- ref/sample clock toggle rate 2.0 per period, probability 0.5;
- source-pending toggle rate 0.2 per period, probability 0.5;
- reset held deasserted for vectorless estimation; and
- exact Genus 23.14-s090_1 executable path, resolved path, and SHA.

The qualification fails if any row uses a different boundary, source/staged
hash, mapped netlist identity, SDC, driver, server environment, technology
file, tool, or vectorless assumption.

Any VCD/SAIF filename, import command, or nonempty user/activity-file field in
the report or log is rejected. Such evidence belongs to the existing
activity-power path. A passing result from this directory explicitly sets
`activity_annotated=false` and `activity_power_eligible=false`.

## Local preflight (no Cadence)

```sh
python3 physical/k2_endpoint_vectorless/vectorless.py preflight \
  --output /tmp/k2-endpoint-vectorless-preflight.json
```

This validates all local provider hashes, registries, staged boundary, timing
profile, and derived driver. It exits successfully but publishes only
`HOLD_NO_REAL_SERVER_ARTIFACTS`; it never probes or launches Cadence.

## Real server execution

Only run this after producing a `PROVEN_ENVIRONMENT` receipt with the existing
server preflight. The CLI intentionally mirrors the inputs required by the
existing Genus flow:

```sh
python3 physical/k2_endpoint_vectorless/vectorless.py execute \
  --genus /tools/cadence/DDI231/GENUS231/bin/genus \
  --library /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib \
  --hold-library /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/fast_vdd1v0_basicCells.lib \
  --cell-lef /home/aiasic26911/gsclib045_all_v4.7/gsclib045/lef/gsclib045_macro.lef \
  --shared-qrc /home/aiasic26911/gsclib045_all_v4.7/gsclib045/qrc/qx/gpdk045.tch \
  --golden-archive /tmp/ganghee-pnr-golden-20260813.tar.gz \
  --raw-golden-archive /tmp/ganghee-pnr-raw-golden-20260813.tar.gz \
  --functional-loss-archive /tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz \
  --server-environment-receipt /absolute/server-environment-go.json \
  --mapped-functional-hook physical/k2_w2_genus/run_mapped_functional_xcelium.py \
  --functional-model /absolute/gsclib045_functional.v \
  --output-root /absolute/new-exclusive-result-root \
  --attempt-prefix endpoint-vectorless-6p5
```

Execution is exclusive/no-overwrite. It runs the exact three existing Genus
flows using the derived driver, publishes `vectorless-evidence.json`, and then
qualifies that evidence. Until that command is executed on the proven server,
there are no real vectorless results and the package remains HOLD.

An already published evidence manifest can be checked offline with:

```sh
python3 physical/k2_endpoint_vectorless/vectorless.py qualify \
  --evidence /absolute/vectorless-evidence.json \
  --output /absolute/vectorless-qualification.json
```

Run focused local tests with:

```sh
tests/k2_endpoint_vectorless/run_all.sh
```
