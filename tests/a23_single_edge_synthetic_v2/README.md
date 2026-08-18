# Hardened synthetic single-edge replay v2

This package is a separate retained-evidence lane for the team-defined
synthetic `full50` replay. It does not overwrite the pinned legacy
`tests/a23_full_single_edge_replay/result.json`, modify any public-projected
file, or change the canonical campaign wrapper.

Two fresh executions use the pinned producer through an explicit retained root:

```sh
/usr/bin/python3.14 tests/a23_single_edge_synthetic_v2/run_v2.py campaign \
  --retained-root /tmp/a23-single-edge-synthetic-v2-primary
/usr/bin/python3.14 tests/a23_single_edge_synthetic_v2/run_v2.py campaign \
  --retained-root /tmp/a23-single-edge-synthetic-v2-reproduction
```

Each execution is exactly 100 full50 actual-RTL processes, two clean-drain
reset processes, two count-two activation processes, and eight separately
compiled/executed source mutants. A separate actual-RTL ordinal observer runs
all 100 full50 owner/trace cases and assigns explicit global acceptance and
retirement ordinals, including lane order within the same cycle. Thus each
campaign has 212 actual-RTL executions and the two retained campaigns have 424.
Both executions must record the same package, hardened source/integration,
tool, trace, prepared-input, and ordinal-semantic identities.

The semantic digest removes exactly the 22 concrete build/simulation log-hash
JSON pointers listed in `synthetic_v2_result.json`. All other fields—including
package, source, integration, tools, traces, metrics, event hashes, summaries,
mutants, and reset evidence—remain digest inputs. Ordinal reproduction excludes
only each simulation-log hash, whose variable Verilator wall-time footer is
still retained and validated for its exact per-case PASS sentinel.

For both primary and reproduction campaigns, the export retains the 50 shared
prepared inputs bound to all 100 A2/A3 runs, all 100
event/summary/simulation triplets, all 100 ordinal CSV/simulation pairs,
generator JSONL/manifests and logs, reset/activation artifacts, all
baseline/mutant build logs, eight mutation logs and mutant sources, the base
result, exact repository inputs, and the v2 result. Compiler products under
`work/build/` are excluded reproducible scratch and are not publication
evidence; the v2 result makes no counters or digest claims about excluded
scratch.

The archive uses fixed gzip/tar metadata and a closed hash/size inventory.
Validation rejects symlinks, hardlinks, path escapes, duplicate/missing/extra
members, unsafe metadata, and hash/size drift, then reopens the archive,
materializes both campaigns in fresh directories, and independently recomputes
replay claims, semantic reproduction, CSV schemas, per-run ordinal PASS logs,
and retained global-order digests.

This is synthetic digital evidence only. Canonical campaign, physical, power,
and CDC/RDC qualification remain outside its PASS scope.
