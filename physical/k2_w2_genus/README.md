# K2 W2 candidate-neutral Genus flow

This package freezes the five exact synthesizable designs available at source
commit `13c60f936fe5a265e650b4b91436ed79fc20dc91`:

1. `a2_k2`
2. `a3_k2`
3. the shared `p6_endpoint`
4. `a2_p6`
5. `a3_p6`

The registry is intentionally `blocked_missing_tech_staged_manifest`. The
techmap owner has not yet supplied a committed `w2-physical-staging-v2`
manifest with exact top, filelist, source hashes and repository commit. Until
all of those fields exist, both the single-design runner and cohort launcher
exit nonzero before Genus and create no result namespace. An owner-generic or
native-debug top is never used as a fallback.

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

- `ref_clk_i`, `sample_clk_i`, `rst_n`
- `source_pending_i[15:0]`

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

All three also expose `link_clk_o`; R1 has `link_data_o[1:0]`, while P6 has
`link_data_o[4:0]`. These are the only inherent width differences. The aliases
`load_i`, `pending_i`, `source_ready_o`, `protocol_fault_o`, `burst_*`, and
`p6_*` are forbidden at the final top.
No scheduler/debug output, padding, normalized-away link pin, or extra port is
accepted. The runner parses the actual staged top's ANSI port declaration and
checks the exact name/direction/width set rather than trusting manifest claims.

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

The shared manifest also pins the complete endpoint technology inventory. R1
requires exactly 1 `TLATNTSCAX2`, 2 `MX2X1`, 2 `DFFRHQX1`, and 5
`DFFNSRX1`; P6 requires exactly 1, 5, 5, and 12 respectively. The negative-edge
cells are the four/ten address-or-closing-state bits plus the commit toggle,
not merely the serialized data width.

## Diagnostic registries

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

Raw and buffered golden archives remain tool/report/source authorities, while
the yZr1 functional archive remains non-official loss-only evidence and is
forbidden as PPA evidence.

## Launch behavior

Once the staged manifest is ready, `run_goal_cohort.py` creates an exclusive
attempt root, records the exact three commands, and publishes a cohort result
only after all three mapped Genus receipts, endpoint connectivity maps, and
mapped staged-vs-netlist functional gates pass. In the current
blocked state, even `--plan-only` exits nonzero instead of rendering commands
for substitute tops.

Execution additionally requires a byte-bound `PROVEN_SERVER_ENV` receipt. The
Genus mapping run consumes the slow setup Liberty only. Fast hold Liberty,
macro LEF, and the shared typical QRC are verified environment and downstream
Innovus provenance inputs; they are not relabeled as Genus MMMC consumption.
Each passing screening receipt hashes the mapped netlist, mapped SDF, mapped
SDC, endpoint leaf hierarchy/pin map, SDF-annotated functional transcript,
vendor functional models, and every timing/area report. The functional hook
must use the Xcelium executable authenticated by `PROVEN_SERVER_ENV`; a hash-
only smoke result cannot pass. Common workloads are never synthesized into
this boundary; scoped workload activity may be attached later as a separately
bound SAIF artifact.

Run local contract, provenance, archive and mutation tests with:

```sh
tests/k2_w2_genus/run_all.sh
```
