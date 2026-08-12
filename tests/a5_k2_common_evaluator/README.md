# A5 common digital K2 transaction evaluator

Status: **fail-closed evaluator/self-falsification ready; the committed owner
files are test fixtures, not owner RTL evidence, so no candidate PASS exists**.

This directory is an independent N=16, K=2 evaluation package for exactly
three new candidate implementations. It neither imports nor edits candidate
branches, team RTL, common testbench code, or frozen manifests. Candidate
owners consume vectors and return transaction evidence at the normalized
boundary described below.

## Frozen semantic oracle

The authoritative scalar policy is a committed-event wheel:

```text
row wheel = [0,1,1,1,1,1,2,2,2,2,2,3]
row(source) = source // 4
within each row = round robin over columns 0..3
reset state = wheel_pos 0, all column pointers 0
```

Starting at `wheel_pos`, the oracle searches wheel entries until it finds a
row with a pending source, then searches that row from its column pointer. An
empty wheel entry is searched but is not independently consumed. State moves
only after a real accepted event: the wheel cursor moves after the selected
token and that row's column pointer moves after the selected column.

K2 is defined by two scalar folds over the same pre-edge pending cohort:

```text
g0 = scalar(P, q)
g1 = scalar(P - {g0}, transition(q, g0))
```

An observed zero-, one-, or two-event acceptance must be a contiguous prefix
of `[g0,g1]`. Adaptive logic may choose the prefix length, but not the winner
identity. Policy state advances once per observed acceptance, not once per
physical cycle, attempted lane, or speculative token.

This is frozen-cohort scalar-prefix equivalence. It is deliberately not full
future-trace equivalence: after K2 accepts `0,8` from pending `{0,8}`, a new
source 4 can make the scalar K1 stream `0,4,8` while K2 is already `0,8,4`.
The adversarial package contains and self-checks this witness.

## Vector format

Generate the exact adversarial bundle:

```sh
python3 tests/a5_k2_common_evaluator/generate_vectors.py \
  --output /tmp/a5-k2-adversarial-v1.json
```

The generated document uses schema `a5_k2_vector_bundle_v1`. Its expected
semantic bundle SHA and seven per-run SHAs are frozen in
`adversarial-lock.json`. `bundle_sha256` and `run_sha256` are SHA-256 hashes of
canonical JSON (`sort_keys`, compact separators, ASCII, one trailing newline)
after removing the corresponding hash field.

Each indexed input cycle is:

```json
{
  "cycle": 2,
  "reset_n": true,
  "retire_ready": [true, true],
  "occurrences": [
    {
      "event_id": "same_row_distinct_pair:c2:s0",
      "source": 0,
      "payload": {"address": 0, "x": 0, "y": 0}
    }
  ]
}
```

Cycle semantics are exact: reset is sampled first; when released, occurrences
update the one-entry source latches before accepts observed at the same indexed
edge. A new occurrence at an occupied source is terminal `source_overrun`.
`event_id` is a TB-only identity sidecar and must never enter synthesized DUT
ports. Candidate-native event bits come from `payload`.

The seven required adversaries are:

| Run | Required observation |
|---|---|
| `persistent_weight_120` | first 120 actual accepted events have row counts exactly `10,50,50,10`, and every vector is a scalar prefix |
| `sparse_work_conservation` | no overrun/missing event and maximum occurrence-to-accept is one cycle |
| `same_row_distinct_pair` | two same-row winners remain distinct and ordered |
| `stale_second_revalidation` | a new generation at source 8 cannot be confused with the earlier source-8 event |
| `future_arrival_divergence_witness` | documents the expected boundary of the prefix claim; divergence from K1 is not a candidate failure |
| `ordered_lane_stall` | stalled presentation is stable and lane 1 cannot retire around stalled lane 0 |
| `reset_abort_no_phantom` | reset aborts pre-reset pending/inflight records; only the post-reset sentinel may appear |

## Fail-closed candidate evidence format

Each owner produces one JSON file using
`a5_k2_candidate_evidence_v2`; see `evidence-template.json`,
`schemas/k2-candidate-evidence.schema.json`, and
`schemas/k2-run-artifact.schema.json`. A bare hash is not identity. Source,
binding, and runner are mandatory `{path,digest_kind,digest}` records. Paths
must resolve to single-link regular files and are opened without following the
leaf. `digest_kind` is byte `sha256` or Git-object `git_blob_sha1`.

```json
{
  "schema": "a5_k2_candidate_evidence_v2",
  "candidate": {
    "id": "owner-k2-name",
    "source": {"path": "owner.sv", "digest_kind": "git_blob_sha1", "digest": "40 lowercase hex digits"},
    "binding": {"path": "binding.sv", "digest_kind": "sha256", "digest": "64 lowercase hex digits"},
    "runner": {"path": "runner.py", "digest_kind": "sha256", "digest": "64 lowercase hex digits"},
    "contract": {
      "policy": {"class": "exact_weighted_scalar_prefix_k2", "definition": "exact evaluator-owned class definition"},
      "edge": {"...": "exact evaluator-owned edge definition"},
      "latency": {"...": "exact evaluator-owned latency definition"}
    },
    "claims": {"full_future_trace_equivalence": false}
  },
  "vector_bundle_sha256": "...",
  "runs": [
    {
      "name": "same_row_distinct_pair",
      "run_sha256": "...",
      "artifact": {"path": "same_row_distinct_pair.run.json", "digest_kind": "sha256", "digest": "..."}
    }
  ]
}
```

Cycles are forbidden inline. Every run path names a real SHA-256-bound JSON
artifact with schema `a5_k2_run_artifact_v2`. Its envelope repeats the vector
and run hashes and binds `candidate_identity_sha256` over the exact ID and three
path/digest records plus `contract_sha256`. Changing a bound file after the run
or substituting a free-standing output therefore cannot reuse the artifact.

The exact edge contract samples reset first, latches occurrences before
acceptance at the same indexed rising edge, treats `accepts` as the ordered
handshakes on that edge, samples `outputs` immediately before it, and retires on
`output.valid && retire_ready`. Latencies are rising-edge differences:
`accept_cycle - occurrence_cycle` and `retire_cycle - accept_cycle`.
Percentiles use nearest-rank ceiling; comparison uses each run's event-ID cohort
accepted by all three candidates.

The evaluator owns exact definitions for `exact_weighted_scalar_prefix_k2`,
`batched_iwrr_k2`, and `paired_row_calendar_proposal_k2`. It hashes the complete
policy/edge/latency document. If the three fingerprints differ, the result is
`INCOMPARABLE`, `pareto_frontier` is null, no global Pareto ranking is performed,
and the command exits 4.

`accepts` is the ordered, contiguous list of actual source handshakes at that
edge; slots must be numbered `0,1`. `outputs` is the level-sensitive external
retire presentation before the edge. A retirement occurs exactly when
`outputs[lane].valid && vector.retire_ready[lane]`. A valid output therefore
contains the TB-sidecar event ID and source; an invalid output contains only
`lane` and `valid`. The owner cannot override handshake interpretation with an
internal “fire” counter.

## Metrics and hard gates

The evaluator reconstructs, rather than trusts:

- generated, source-overrun, pending, accepted, reset-aborted, retired, and
  inflight accounting;
- exact accepted-to-retired global order and per-event identity;
- distinct same-cycle winners and stale/wrong source generations;
- `FULL`, `PRIMARY_ONLY`, or `FAIL` prefix-equivalence grade;
- committed row counts and the first-120 persistent count vector;
- sparse work conservation;
- output stability, contiguous lanes, and ordered behavior under independent
  lane stalls;
- reset quietness, post-reset phantom exclusion, and truthful drain;
- occurrence-to-accept and accept-to-retire mean/p50/p95/p99/max; and
- fixed-window retired event/cycle.

The exact gates and comparison bands are in `thresholds.json`. Correctness,
`FULL` prefix grade, `10:50:50:10`, sparse maximum wait one, ordered lane
behavior, reset, and drain are hard gates. No weighted aggregate score is
allowed. Pairwise differences inside the frozen absolute/relative bands are
ties. Latency comparison uses only event IDs accepted by all three candidates;
accepted-set symmetric difference is reported so source-overrun survivor bias
cannot manufacture a win. Capacity22 is always a subset view of full50.

Run exactly three owner artifacts:

```sh
python3 tests/a5_k2_common_evaluator/evaluate_k2.py \
  --vectors /tmp/a5-k2-adversarial-v1.json \
  --candidate /owner-a/evidence.json \
  --candidate /owner-b/evidence.json \
  --candidate /owner-c/evidence.json \
  --output /tmp/a5-k2-adversarial-evaluation.json
```

Exit 0 means all three passed under one exact contract. Exit 3 publishes
`HOLD`; exit 4 publishes valid but differently contracted evidence as
`INCOMPARABLE`. Schema, provenance, regular-file, digest, artifact-envelope, or
cardinality errors exit 2 and publish nothing. Output files are never
overwritten.

## Frozen generator-v4 adapter

The adapter validates the committed full50/capacity22 manifest hashes and
every trace SHA from `scripts/common_suite_official.py`. It reads the A1
generator-v4 source when generation is requested; it does not use or modify a
candidate branch or common TB.

```sh
python3 tests/a5_k2_common_evaluator/adapt_frozen_v4.py \
  --generate \
  --trace-dir /tmp/a5-k2-v4-traces \
  --suite full50 \
  --output /tmp/a5-k2-v4-full50.json
```

Run all three owners on this bundle and invoke `evaluate_k2.py` again. To
publish capacity22, either adapt it independently from the same SHA-validated
trace directory or select its exact 22 names from the full50 evidence; never
count it as 22 additional independent executions. The adapter adds 64 quiet
drain cycles by default and refuses a drain allowance below N=16.

## Evaluator qualification

```sh
tests/a5_k2_common_evaluator/run_all.sh
```

The test-only reference is explicitly marked `TEST_ONLY_NOT_RTL_EVIDENCE`.
The mutation suite kills false weight, stale generation, same-source duplicate,
stalled-lane corruption, younger-lane bypass, reset phantom, future-trace
overclaim, unattached identity hashes, fabricated output artifacts, and rebound
binding identity. `fixtures/owners/` supplies three actual regular-file test
owners; their trivial RTL and runner markers are fixtures, not executed owner
RTL evidence. Suite PASS qualifies only the evaluator.
