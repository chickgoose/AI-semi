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
compiled/executed source mutants. Both executions must record the same package,
hardened source/integration, tool, trace, and prepared-input identities.

The semantic digest removes exactly the 22 concrete build/simulation log-hash
JSON pointers listed in `synthetic_v2_result.json`. All other fields—including
package, source, integration, tools, traces, metrics, event hashes, summaries,
mutants, and reset evidence—remain digest inputs. The second raw result and its
campaign log are retained in the sealed export.

The primary export retains the 50 shared prepared inputs bound to all 100 A2/A3
runs, all 100 event/summary/simulation triplets, generator JSONL/manifests and
logs, reset/activation artifacts, all baseline/mutant build logs, eight mutation
logs and mutant sources, the base result, exact repository inputs, and the v2
result. Compiler products under `work/build/` are inventory-hashed as excluded
reproducible scratch but are not publication evidence.

The archive uses fixed gzip/tar metadata and a closed hash/size inventory.
Validation rejects symlinks, hardlinks, path escapes, duplicate/missing/extra
members, unsafe metadata, and hash/size drift, then reopens the archive,
materializes its primary evidence in a fresh directory, and independently
recomputes replay claims, semantic reproduction, and retained row-sequence
digests.

This is synthetic digital evidence only. Canonical campaign, physical, power,
and CDC/RDC qualification remain outside its PASS scope.
