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

## Attempt artifacts

The artifact manifest has schema 2:

```json
{
  "schema_version": 2,
  "suite": "full50",
  "runs": [{
    "name": "core_sparse_identity",
    "freshness_marker": "runs/core_sparse_identity/freshness.marker",
    "result": {
      "path": "runs/core_sparse_identity/trace.events.csv",
      "sha256": "..."
    }
  }]
}
```

Paths are relative to `--artifact-root`. Absolute paths, `..`, symlinks,
shared paths, empty artifacts, hash mismatches, and artifacts not strictly
newer than their per-run empty marker fail the receipt. Each result CSV must
have one consistent, nonempty candidate/test/seed tuple matching the generated
run manifest, and the candidate must be the same across all 50 or 22 runs.

Only `pairwise_contention` and `mixed_phase_always_ready` rows must add an
`analyzer` object with the same path/SHA form. Analyzer declarations on other
workloads are rejected. Provenance follows the analyzers that actually exist:

- pairwise: candidate/test/seed, trace SHA, generator version, and logical
  permutation must agree; `measurement_state` must be `COMPLETE`, evaluable
  must equal total, and dropped/censored/nonevaluable must all be zero.
- mixed: candidate/test/seed and trace SHA must agree; schema 1,
  address-only/always-ready modes, and every actual `provenance_validation`
  check must pass. Its correctness status must be `qualified_pass`; valid
  analysis outcomes are `pass` and `capacity_loss` (loss is a measured outcome,
  not a receipt failure).

No nonexistent `_common_suite_provenance` or result-SHA analyzer field is
assumed. Result SHA remains bound by the artifact manifest and receipt.

Invocation:

```sh
attempt_root="$(python3 scripts/common_suite_attempt.py \
  --root "$out_root" --suite full50 --candidate "$candidate")" || exit $?

python3 scripts/common_suite_receipt.py \
  --suite full50 \
  --official-manifest "$common/manifest.neutrality-n16.json" \
  --generation-index "$trace_root/generation-index.json" \
  --artifacts "$attempt_root/artifacts.json" \
  --artifact-root "$attempt_root" \
  --output "$attempt_root/common-suite.receipt.json"
```

`common_suite_attempt.py` creates, with exclusive `mkdir`, a private path below
`$out_root/attempts/$suite/$candidate/` containing `runs/` and an fsynced
`attempt.json`. It never removes or overwrites an earlier result. A runner may
therefore put the freshness marker, DUT output, analyzer output, artifact
manifest, and receipt in one unique namespace without a shared-path race.

## Atomic publication boundary

The receipt file is fully written and fsynced, then linked into a previously
absent final name, and the containing directory is fsynced. Existing receipts
are never overwritten. Exit 0 means both file and directory fsync completed.
If the directory fsync fails after the link, the command returns 2 but the
final pathname may already exist; callers must treat only exit 0 as published
and must not infer success from pathname existence. The unique attempt
namespace makes that ambiguous failed attempt harmless and preserves it for
diagnosis.
