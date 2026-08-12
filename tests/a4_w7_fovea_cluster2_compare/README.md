# W7-A4 Fovea versus Cluster2 process comparator

This candidate-owned orchestrator runs the protected A1 common semantics without
editing them. It generates exact generator-v4 full50 once, prepares each trace
once, elaborates one Fovea and one Cluster2 Xcelium snapshot, then runs reset plus
all 50 traces from each snapshot. Capacity22 is an exact 22-stem analysis-only
view of those same results; it causes no second compile or simulation.

The Fovea boundary is `RETIRE_LANES=1`; Cluster2 is its native two-row-bitmap
boundary expanded by the protected stateless binding to `RETIRE_LANES=8`.
Both use `NUM_SOURCES=16`, `ADDR_WIDTH=16`, `FIFO_DEPTH=0`, address-only identity,
and the same protected TB, generator, preparer, and analyzers.

```sh
python3 tests/a4_w7_fovea_cluster2_compare/run_w7_compare.py \
  --output /fresh/w7-attempt \
  --fovea-top aer_tx16_trad_rowcol_fovea \
  --fovea-filelist /absolute/fovea.f \
  --cluster2-top aer_tx16_trad_rowcol_fovea_cluster2 \
  --cluster2-filelist /absolute/cluster2.f
```

Filelist paths, nested `-f` paths, source entries, and `+incdir+` directories
must be absolute. The orchestrator hashes every listed source and every regular
file below include directories before and after execution; unsupported
provenance-opaque options fail closed. `+define+` tokens are forwarded.

Output must not already exist. Xcelium is discovered from `--xrun`,
`AER_XRUN_BIN`, `XRUN`, then `PATH`; absence is fatal. Every tool return code,
Xcelium error/fatal diagnostic, exact PASS marker, reset evidence, CSV
provenance/cardinality/conservation, frozen throughput counter, analyzer
cardinality, and protected-file pre/post hash is checked before `receipt.json`.

Per candidate the analyzer set is aggregate full50 and capacity22 views, two
pairwise, two phase-transition, two timing-pair, two mixed-phase reports, and one
identity-versus-affine cross-map report. A cross-map non-rankable result is not a
functional failure: both candidates and all analyzers finish, a HOLD receipt is
published, and the orchestrator exits 3. Other failures exit 2 without receipt.
