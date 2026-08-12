# W7-A3 canonical Ganghee Cluster2 qualification

Status: **local digital GO; independent-lane backpressure and physical PPA HOLD**.

## Frozen inputs

The authoritative source is the tracked `semi-ai` tree, not the dirty
`redred-faer` copy.  Read-only server inspection on 2026-08-13 found:

- current `semi-ai` HEAD `ba603bd409d4b086a8476c6ee600c1814acafde0`;
- introduction commit `3fb6a70592addcd5b3094987223c474d70f3db22`, parent
  `d36744257df1ebed3be4f0389eaa9786ce7e047d`, author and committer time
  2026-08-09 15:11:36 +0900;
- unchanged top `aer_tx16_trad_rowcol_fovea_cluster2` and exact closure
  `arbiter2.v`, `arbiter4_tree.v`, and
  `aer_tx16_trad_rowcol_fovea_cluster2.v`;
- the server `redred-faer` copy has the same three SHA-256 values but remains
  untracked at its HEAD, so it is corroboration rather than Git provenance.

`provenance.json` freezes the Git blobs and SHA-256 values.  The runner rejects
any source, file-list, port/semantic token, generator, manifest, run-count, or
capacity-subset mismatch before execution.  The scalar Fovea file is a pinned
counterfactual reference and is deliberately absent from the Cluster2
elaboration closure.

## Executed contract

The direct TB instantiates the real canonical top.  It has one exact pending
slot per address source, drives the held request level directly, and decodes
only raw `valid{0,1}`, `row{0,1}`, and `col_mask{0,1}`.  There is no queue,
metadata reconstruction, output arbitration, or ready/backpressure adapter.
An occurrence while its source slot is occupied is `source_overrun`, not
post-acceptance corruption.  The checks require
`generated=accepted+overrun`, `accepted=delivered`, exact source identity,
clean drain, legal lane rows, and no duplicate/phantom/empty result.

The canonical three-file closure is compiled once.  All generator-v4 full50
and capacity22 traces are then replayed from the same image, with exact
RTL/cycle-model metric lockstep on 72 executions.  A directed reset test checks
both registered valids, mid-traffic reset, post-reset quiet, and the first
dual-lane result.  Two independently compiled mutants must fail with exact
diagnostics: stale `valid1` during reset and a removed peripheral lane.

Icarus may emit its generic warning that source files lack explicit time units;
this exact warning is allowlisted because the unchanged Verilog closure has no
timescale directive and the TB supplies one.  Every other warning/error is
fatal.

## Separating 1:5:5:1 from parallelism

The actual Cluster2 RTL contains no `WEIGHT`, `round`, or `prefer_center`.
It does not preserve the scalar Fovea's 1:5:5:1 row opportunity allocation;
it replaces team competition with independent center and peripheral lanes.
Four source-equation models separate the effects:

1. pinned canonical weighted scalar: one selected column event;
2. weighted 5:1 bitmap: same team policy, all columns of the selected row;
3. equal-split bitmap: weight removed, still one selected team/row lane;
4. actual Cluster2: independent center and peripheral bitmap lanes.

With persistent `req=16'hffff` for 120 cycles, the pinned weighted policy has
row opportunities `[10,50,50,10]`, equal split has `[30,30,30,30]`, and
Cluster2 has `[60,60,60,60]` with both lanes active in all 120 cycles.  Thus
the apparent foveal preference is not “kept and hidden by throughput”; it is
structurally gone.

| frozen suite | model | accepted | overrun | fixed-window throughput | mean / p99 / max latency |
|---|---|---:|---:|---:|---:|
| full50 | weighted scalar | 79,992 | 26,424 | 0.688931 | 4.817 / 44 / 357 |
| full50 | weighted bitmap | 94,705 | 11,711 | 0.816208 | 2.849 / 10 / 14 |
| full50 | equal-split bitmap | 95,641 | 10,775 | 0.824236 | 2.749 / 5 / 6 |
| full50 | Cluster2 RTL/model | 100,581 | 5,835 | 0.866963 | 2.256 / 4 / 4 |
| capacity22 | weighted scalar | 42,439 | 23,177 | 0.762511 | 6.750 / 60 / 357 |
| capacity22 | weighted bitmap | 56,669 | 8,947 | 1.019477 | 3.179 / 11 / 14 |
| capacity22 | equal-split bitmap | 57,663 | 7,953 | 1.037334 | 3.007 / 5 / 6 |
| capacity22 | Cluster2 RTL/model | 62,197 | 3,419 | 1.119186 | 2.286 / 4 / 4 |

Cluster2's accepted-event gain over the scalar reference decomposes as:

| effect | full50 accepted delta / share | capacity22 accepted delta / share |
|---|---:|---:|
| within-row bitmap | +14,713 / 71.46% | +14,230 / 72.02% |
| remove 5:1 team weight | +936 / 4.55% | +994 / 5.03% |
| second independent team lane | +4,940 / 23.99% | +4,534 / 22.95% |

Therefore most gain is bitmap width, about one quarter is the second team
lane, and only about five percent is attributable to removing the 5:1 policy
under these traces.  This is a transport-capacity result, not evidence that
the original perceptual weighting survives.  Repeated same-source occurrences
can still overrun because the level-valid seam has only one outstanding slot.
Cluster2 has no output-ready input, so independent lane stalls/backpressure,
routed cost, clock/power, and physical timing remain HOLD.

## Reproduction

```sh
python3 tests/a3_w7_cluster2/run.py \
  --output tests/a3_w7_cluster2/w7_results.json
python3 -m unittest -v tests.a3_w7_cluster2.test_w7_cluster2
```

The runner uses system temporary storage only and atomically publishes the
requested receipt.  `A3_W7_IVERILOG` and `A3_W7_VVP` may select caller-owned
executables; otherwise PATH and the existing local `/tmp/a7-toolchain` are
searched.  A missing tool is a hard failure.
