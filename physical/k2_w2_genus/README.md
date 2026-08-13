# K2 W2 candidate-neutral Genus flow

This package freezes the five exact synthesizable designs available at source
commit `13c60f936fe5a265e650b4b91436ed79fc20dc91`:

1. `a2_k2`
2. `a3_k2`
3. the shared `p6_endpoint`
4. `a2_p6`
5. `a3_p6`

`designs.json` is the top/filelist/source-hash registry. The sole server-flow
authority is `ganghee-pnr-golden-20260813.tar.gz`, SHA-256
`1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.
`golden_reference.json` binds 25 exact members: both 1.0 ns Genus Tcl/log/cmd
runs, their area/timing/power/netlist/SDC outputs, and every Fovea/Cluster2
wrapper/core RTL input. A renamed local source tree, repacked archive, modified
member, missing archive, or different archive bytes are rejected.

The native-core authority is separately pinned as
`ganghee-pnr-raw-golden-20260813.tar.gz`, SHA-256
`7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3`.
Its 22 anchors bind both raw tops, exact Verilog source lists, Tcl/log/cmd,
area/timing/power reports, mapped netlists and mapped SDCs. The raw and
buffered archives share byte-identical native core RTL but remain distinct
measurement cohorts. Neither cohort's reports are promoted as endpoint
candidate results.

The functional loss-only reference is
`eval-fovea-cluster2.yZr1kmYL.tar.gz`, SHA-256
`22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39`.
The verifier binds `provenance.txt`, both candidate run logs, the exact original
attempt prefix, all 338 ledger entries, reset and pairwise status, and the
archive. It deliberately excludes the stale outer `eval-driver-final.log`.
This is labeled `NON_OFFICIAL_WORKSPACE_DIFF` and may support only generated,
accepted, delivered and overrun accounting. It is forbidden as PPA evidence.

The driver follows the byte-proven golden command order and assumptions:
Genus `23.14-s090_1`, `slow_vdd1v0_basicCells.lib`, automatic clock-gating
enabled, `read_hdl`, elaborate, SDC load, generic/map/opt, then the three real
report classes `*_area.rpt`, `*_gtiming.rpt`, `*_gpower.rpt` and mapped
`*_netlist.v`/`*_out.sdc`. It does not fabricate the earlier local-only
check/QoR/clocks report set. Candidate reports must match the actual Ganghee
Genus report grammar and the log must show the pinned version, zero Error/Fatal,
and normal exit.

The runner rejects any candidate source that differs from the named source
commit, snapshots the authoritative archive, every source and supplied Liberty
into a new attempt namespace, records the Genus executable path/hash/version
before and after execution, disables scan insertion, and emits a canonical
`attempt.json`.

After Genus exits zero, publication still fails unless all golden-format
reports, the log sentinel/status, mapped netlist, mapped SDC, library-cell
inventory, zero unresolved/blackbox types, and zero scan-cell types pass. A
failed run may leave diagnostic files in its unique attempt directory but never
publishes `receipt.json` and never deletes or overwrites another attempt.
Reports alone are never sufficient: exact candidate source/tool/library, both
golden archives, mapped netlist, mapped SDC, mapped-cell inventory and bound
functional smoke evidence are all mandatory.

## Mapped smoke hook

Physical mapped simulation depends on the selected Liberty's functional model
and installed simulator, so it is an explicit mandatory hook rather than a
fabricated local PASS. The executable receives:

```text
--top TOP --netlist MAPPED_V --library LIBERTY_SNAPSHOT --output RESULT_JSON
```

It must exit zero, print `W2_MAPPED_SMOKE_PASS`, and emit schema
`k2_w2_mapped_smoke_v1` with status `PASS`, exact top, mapped-netlist SHA-256,
and Liberty SHA-256. The hook itself is snapshotted and hashed before execution.
The server hook must compile the mapped netlist with the matching functional
cell model and perform candidate-specific reset/output smoke checks; a hook
that merely echoes PASS does not constitute hardware qualification.

## Invocation

```sh
python3 physical/k2_w2_genus/run_genus.py \
  --design a2_p6 \
  --genus /absolute/immutable/genus-entrypoint \
  --library /absolute/slow_vdd1v0_basicCells.lib \
  --golden-archive /tmp/ganghee-pnr-golden-20260813.tar.gz \
  --raw-golden-archive /tmp/ganghee-pnr-raw-golden-20260813.tar.gz \
  --functional-loss-archive /tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz \
  --mapped-smoke-hook /absolute/pinned/mapped-smoke-hook \
  --output-root /absolute/new-results-root \
  --attempt a2-p6-period5-attempt1
```

The 5 ns constraints are screening constraints, not an Fmax result. P6 clocks
remain phase-related in the generated SDC; there are no false paths or
multicycle exceptions. Vectorless `report_power` is retained only as screening
evidence. Genus output is not post-route area, power, timing, or physical PPA.

Run local fixture and mutation tests with:

```sh
tests/k2_w2_genus/run_all.sh
```
