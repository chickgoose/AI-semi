# Fail-closed common-suite receipt

`scripts/common_suite_receipt.py` qualifies exactly one immutable attempt. It
does not glob for runs, reuse a stale example list, or delete output.

## Frozen suites

The candidate-owned registry in `scripts/common_suite_official.py` was derived
from A3 commit `abd6a721b515ded8a9ef76cb96129b7e0af21e2b`:

| receipt suite | committed manifest | runs | manifest byte SHA256 |
|---|---|---:|---|
| `full50` | `manifest.neutrality-n16.json` | 50 | `9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9` |
| `capacity22` | `manifest.multilane-n16.json` | 22 | `99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62` |

The registry freezes the exact ordered names and every trace SHA from the
committed 50-run golden. Capacity22 is an exact subset, not a regenerated or
lane-specific workload.

The receipt rejects a manifest with the right-looking contents but different
bytes, a generation index with missing or extra top-level schema fields, a
generator other than 4.0, a wrong input manifest, duplicate/missing/extra runs,
or a generated run configuration that differs from the committed input.
For every run it reads `<name>.manifest.json`, requires both parsed equality to
the embedded generation-index object and byte equality to the generator's
canonical sorted/indented serialization, hashes those actual bytes, and checks
the named JSONL against the frozen trace SHA.

## Immutable attempt and execution binding

`common_suite_attempt.py` allocates only a new
`attempts/<suite>/<candidate>/<unique-id>/` directory. It snapshots the candidate
manifest, every declared runner/analyzer entrypoint and its explicitly declared
transitive dependency closure, the simulator executable, and captured simulator
version output into `provenance/`. It hashes the snapshot bytes and writes/fsyncs
schema-3 `attempt.json`. The receipt requires
that exact directory shape and refuses a missing, moved, renamed, or hash-mismatched
attempt. Existing attempts and user results are never removed or overwritten.

The candidate manifest is schema 2 with no optional identity fields. It contains
exactly `candidate`, a full lowercase 40-hex `commit_sha`, an ordered non-empty
`filelist` of relative path/SHA256 pairs, `bundle_sha256` over that canonical
filelist, `top`, JSON `parameters` and `defines`, unique relative `includes`,
positive `source_count`, and `retire_lanes` in `1..source_count`. The file hashes
are verified against regular, non-symlink files relative to the manifest, and
each file is separately snapshotted into the attempt. The receipt rechecks every
snapshot against both the filelist and bundle identity.

Each tool row binds a snapshotted entrypoint, ordered dependency snapshots, and
a bundle SHA over logical names and byte hashes. `dependency_closure` is fixed
to `declared_complete`; the helper cannot infer omitted imports, sourced shell
files, or dynamically loaded code. Simulator identity separately binds the
executable snapshot and exact captured version-output bytes.

The artifact manifest has schema 4 and resides in that attempt root:

```json
{
  "schema_version": 4,
  "suite": "full50",
  "candidate": "candidate-key",
  "attempt": {"path": "attempt.json", "sha256": "..."},
  "runs": [{
    "name": "core_sparse_identity",
    "freshness_marker": "runs/core_sparse_identity/freshness.marker",
    "result": {
      "path": "runs/core_sparse_identity/trace.events.csv",
      "sha256": "..."
    },
    "execution_sidecar": {
      "path": "runs/core_sparse_identity/execution.sidecar.json",
      "sha256": "..."
    }
  }]
}
```

Paths are relative to `--artifact-root`. Absolute paths, `..`, symlinks,
shared paths, hard links, reused inodes, reused result SHA values, empty
artifacts, hash mismatches, and artifacts not strictly newer than their per-run
empty marker fail the receipt. Each result CSV must have one consistent
candidate/test/seed/load tuple matching both the generated run manifest and the
attempt candidate.

Every run requires a sidecar created after its result/analyzer. Its complete
schema binds the exact suite, attempt ID, candidate, run name, trace SHA,
generated run-manifest SHA, snapshotted candidate-manifest SHA, runner/analyzer
bundle identity, simulator executable/version identity, result SHA, and optional
analyzer SHA. A swapped result or
sidecar therefore cannot satisfy another run merely because filenames or mtimes
look fresh. Checking `load_pct` also prevents a uniform result from being rebound
between different offered loads that share candidate/test/seed.

`pairwise_contention`, `mixed_phase_always_ready`, `phase_transition`, and
`timing_pair` rows must add an `analyzer` object with the same path/SHA form.
Analyzer declarations on other workloads are rejected. Provenance follows the
actual analyzer schemas:

- pairwise: candidate/test/seed/load, trace SHA, generator version, and logical
  permutation must agree. Official N16 cardinality is exactly 240 trials from
  120 unordered source pairs and two repeats. All 240 must be evaluable, the
  120 aggregate rows and 240 trial rows must have the actual analyzer fields,
  counts must conserve, metrics must be finite/nonnegative and ordered, and
  dropped/censored/nonevaluable must all be zero.
- mixed: candidate/test/seed/load and trace SHA must agree; schema 1,
  address-only/always-ready modes, and every actual `provenance_validation`
  check must pass. Its correctness status must be `qualified_pass`; valid
  analysis outcomes are `pass` and `capacity_loss` (loss is a measured outcome,
  not a receipt failure). The exact seven phase boundaries and actual phase,
  latency/service-gap, matched-trace, summary, and classification schemas are
  checked. Event conservation, derived rates/ratios, source ranges, and ordered
  percentiles must agree.
- phase transition: candidate/test/seed/load/trace provenance, the exact five
  names and v4 boundaries, actual accounting fields, event conservation,
  derived completion rate, numeric ranges, and uncensored recovery are required.
- timing pair: candidate/test/seed/load/trace provenance, exact v4 schema and
  official cardinality 128, total=evaluable+dropped+censored accounting, finite
  nonnegative metrics, and percentile ordering are required. Dropped source
  events remain a measured capacity outcome, while censoring is rejected.

No nonexistent `_common_suite_provenance` or result-SHA analyzer field is
assumed. Result SHA remains bound by the artifact manifest and receipt.

Invocation:

```sh
attempt_root="$(python3 scripts/common_suite_attempt.py \
  --root "$out_root" --suite full50 --candidate "$candidate" \
  --candidate-manifest "$candidate_manifest" \
  --tool runner="$runner" \
  --tool generator="$common/generate_trace.py" \
  --tool pairwise_contention="$pairwise_analyzer" \
  --tool mixed_phase_always_ready="$mixed_analyzer" \
  --tool phase_transition="$phase_analyzer" \
  --tool timing_pair="$timing_analyzer" \
  --tool-dependency runner="$runner_library" \
  --tool-dependency generator="$generator_dependency" \
  --tool-dependency pairwise_contention="$aggregate_py" \
  --tool-dependency mixed_phase_always_ready="$aggregate_py" \
  --tool-dependency phase_transition="$aggregate_py" \
  --tool-dependency timing_pair="$aggregate_py" \
  --simulator-name "$simulator_identity" \
  --simulator-executable "$simulator_executable" \
  --simulator-version "$attempt_inputs/simulator.version.txt")" || exit $?

python3 scripts/common_suite_receipt.py \
  --suite full50 \
  --official-manifest "$common/manifest.neutrality-n16.json" \
  --generation-index "$trace_root/generation-index.json" \
  --artifacts "$attempt_root/artifacts.json" \
  --artifact-root "$attempt_root" \
  --output "$attempt_root/common-suite.receipt.json"
```

The runner writes markers and outputs only below this returned path, then emits
each execution sidecar and finally schema-4 `artifacts.json`. The sidecar helper
computes hashes from the actual files and refuses overwrite:

```sh
python3 scripts/common_suite_execution_sidecar.py \
  --attempt-root "$attempt_root" \
  --run-manifest "$run_manifest" --trace "$trace" \
  --result "$result" --analyzer "$analysis" \
  --output "$attempt_root/runs/$run_name/execution.sidecar.json"
```

Omit `--analyzer` only for a workload outside the four analyzer schemas. The
runner must not reuse another attempt's inode or result digest.

The integration tests locate the real v4 common generator in the current tree
or sibling A1 tree (override with `AER_V4_COMMON_ROOT`), generate both official
50- and 22-run manifests, and require every generated trace SHA to match the
frozen registry before exercising the receipt. Analyzer artifacts in this test
are schema-complete deterministic execution fixtures; DUT simulation itself
remains runner responsibility and is not claimed by the receipt test.

## Atomic publication boundary

The receipt file is fully written and fsynced, then linked into a previously
absent final name, and the containing directory is fsynced. Existing receipts
are never overwritten. Exit 0 means both file and directory fsync completed.
If the directory fsync fails after the link, the command returns 2 but the
final pathname may already exist; callers must treat only exit 0 as published
and must not infer success from pathname existence. The unique attempt
namespace makes that ambiguous failed attempt harmless and preserves it for
diagnosis.
