# W7-A5 Fovea versus Cluster2 evaluator

Status: **implemented and mutation-tested evaluator; real paired evidence
pending**. A successful invocation means the supplied evidence was internally
consistent. It is not a candidate winner, common-suite qualification, or
physical/PPA result.

## Comparison boundary

The evaluator compares the native address-only N=16 designs at their normalized
retirement boundaries:

- direct-coordinate Fovea: one scalar address lane;
- raw Cluster2: eight fixed row/column slots (two selected rows by four column
  bits), with no claim that it is an arbitrary eight-source selector.

Both candidates must supply exact generator-v4 `full50` and `capacity22`
per-event and summary results. The evaluator independently regenerates the
official traces with the SHA-pinned generator and manifests. Capacity22 must be
the exact 22-run subset of full50, and its overlapping result artifacts must be
byte-identical; it is never counted as 22 additional independent workloads.

## Required evidence bundle

Each candidate directory contains `evidence.json` with schema
`a5_fovea_cluster2_evidence_v1`:

```json
{
  "schema": "a5_fovea_cluster2_evidence_v1",
  "candidate": {
    "id": "immutable-id",
    "architecture": "fovea",
    "top": "aer_tx16_trad_rowcol_fovea",
    "source_sha256": "...",
    "binding_sha256": "...",
    "runner_sha256": "...",
    "simulator_sha256": "...",
    "source_count": 16,
    "retire_lanes": 1,
    "address_only": true
  },
  "identity_artifacts": {
    "source_bundle": {"path": "...", "sha256": "..."},
    "binding": {"path": "...", "sha256": "..."},
    "runner": {"path": "...", "sha256": "..."},
    "simulator": {"path": "...", "sha256": "..."}
  },
  "suites": {
    "full50": {"manifest_sha256": "...", "runs": []},
    "capacity22": {"manifest_sha256": "...", "runs": []}
  },
  "reset": {"path": "reset.json", "sha256": "..."},
  "policy": {"path": "policy.json", "sha256": "..."}
}
```

Every run entry binds its exact official trace SHA and the path/SHA of one
standard summary CSV and one per-event CSV. Paths must remain inside the
candidate evidence directory, must be regular non-symlink files, and cannot be
duplicated or omitted. Identity hashes are checked against actual artifact
bytes rather than accepted as labels.

Reset evidence must directly observe native valid during reset, apply reset
only after complete drain, require normalized ready/retire quiet, close
generated/accepted/delivered accounting, exclude stale/phantom/loss/duplicate,
and kill a real negative control. The policy probe must hold all 16 sources
continuously for at least 12 cycles and report the four physical-row service
counts. Fovea must reproduce the ideal shares
`[1/12, 5/12, 5/12, 1/12]` within one percentage point. Cluster2 is measured
against the same shares and classified as preserved or natively transformed;
it is not forced to emulate Fovea.

## Recomputed metrics and Pareto rule

The evaluator never trusts precomputed favorable metrics. It reconstructs:

- generated/overrun/accepted/delivered conservation, address identity,
  per-source order, fixed-window delivery, and drain completion;
- uniform capacity curve and the first load whose completion is below 0.95 or
  overrun exceeds 0.05;
- p50/p95/p99/max occurrence-to-delivery latency and maximum request wait;
- demand-normalized Jain fairness and minimum source delivery ratio;
- spatial and moving-hotspot/rotating-victim family outcomes;
- the exact 240 pairwise relation occurrences per identity and affine mapping,
  including completion churn and p99 mapping delta;
- native row-policy distance from 1:5:5:1.

The Pareto vector is unweighted. A candidate dominates only if it is no worse
on every declared dimension and strictly better on at least one. The output
uses `PARETO_ONLY_NO_SCALAR_WINNER`; it does not silently turn a weighted sum
into a winner. Correctness or reset failure aborts before a performance vector
can be produced.

## Run

```sh
python3 tests/a5_fovea_cluster2_evaluator/evaluate_fovea_cluster2.py \
  --fovea /new/fovea-evidence \
  --cluster2 /new/cluster2-evidence \
  --generator /pinned/a1/benchmarks/clean_slate_aer/generate_trace.py \
  --manifest-root tests/common_suite_receipt/fixtures \
  --output /new/a5-w7-evaluation.json

tests/a5_fovea_cluster2_evaluator/run_all.sh
```

The output path must not exist. Publication writes a new temporary file,
flushes and fsyncs it, atomically renames it, and fsyncs the containing
directory. Any failure is nonzero and leaves no completed receipt.

## False-pass mutations

The regression runs real official 50/22 generation and kills mutations for:

1. duplicate/reordered occurrence ID;
2. fabricated delivery from a source-overrun event;
3. forged fixed-window delivery count;
4. stale completion incorrectly marked reset-safe;
5. false Fovea 1:5:5:1 preservation;
6. swapped/forged official trace provenance;
7. correctness failure reaching Pareto ranking;
8. capacity22 overlap rebound to byte-different evidence;
9. source identity artifact swap.

The fixture producer is test-only and uses explicit synthetic identity bytes;
its numbers are not stored or reported as hardware results.
