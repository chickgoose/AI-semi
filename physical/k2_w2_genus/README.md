# K2 W2 candidate-neutral Genus flow

This package freezes the five exact synthesizable designs available at source
commit `13c60f936fe5a265e650b4b91436ed79fc20dc91`:

1. `a2_k2`
2. `a3_k2`
3. the shared `p6_endpoint`
4. `a2_p6`
5. `a3_p6`

`designs.json` is the authoritative top/filelist/source-hash registry. The
runner rejects any source that differs from the named source commit, snapshots
every source and the supplied Liberty into a new attempt namespace, records the
Genus executable path/hash/version before and after execution, disables scan
and automatic clock-gating insertion, and emits a canonical `attempt.json`.

After Genus exits zero, publication still fails unless all required reports,
the completion/log sentinels, mapped netlist, mapped SDC, library-cell
inventory, zero unresolved/blackbox types, and zero scan-cell types pass. A
failed run may leave diagnostic files in its unique attempt directory but never
publishes `receipt.json` and never deletes or overwrites another attempt.

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
  --library /absolute/slow.lib \
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
