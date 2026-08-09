# Fail-closed common-suite receipt contract

`scripts/common_suite_receipt.py` publishes a receipt only after the generation
index, frozen expected-run set, and every run's fresh result and analyzer output
agree.  It is candidate-neutral and does not discover files by globbing.

## Inputs

The expected-runs file freezes the exact suite:

```json
{
  "schema_version": 1,
  "suite_id": "common-multilane-n16-v1",
  "expected_run_count": 20,
  "index_provenance": {
    "input_manifest": "manifest.multilane-n16.json",
    "generator_version": "3.0"
  },
  "runs": [
    {
      "name": "trace-name",
      "trace_file": "trace-name.events.jsonl",
      "trace_sha256": "...64 lowercase hex digits..."
    }
  ]
}
```

The execution writes an artifact manifest. All paths are relative to the
explicit `--artifact-root`; absolute paths, `..`, symlinks, empty files, and
shared artifact/marker paths are rejected.

```json
{
  "schema_version": 1,
  "suite_id": "common-multilane-n16-v1",
  "runs": [
    {
      "name": "trace-name",
      "freshness_marker": "trace-name/freshness.marker",
      "result": {"path": "trace-name/trace.events.csv", "sha256": "..."},
      "analyzer": {"path": "trace-name/analysis.json", "sha256": "..."}
    }
  ]
}
```

Create each empty marker immediately before its DUT run and retain it until the
receipt is published. Both artifacts must be nonempty and have an mtime strictly
newer than that run's marker. The analyzer JSON must bind itself to its input:

```json
{
  "_common_suite_provenance": {
    "schema_version": 1,
    "run_name": "trace-name",
    "trace_sha256": "...",
    "result_sha256": "..."
  }
}
```

Invocation:

```sh
python3 scripts/common_suite_receipt.py \
  --generation-index "$trace_root/generation-index.json" \
  --expected-runs "$frozen_expected_runs" \
  --artifacts "$attempt_root/artifacts.json" \
  --artifact-root "$attempt_root" \
  --output "$attempt_root/common-suite.receipt.json"
```

The output parent must already exist. Publication uses a fully written and
`fsync`ed temporary inode followed by an atomic hard-link into the final name.
An existing receipt is never overwritten. Any validation or publication error
returns exit 2 and publishes no new receipt.

## Non-destructive concurrency plan

Prefer an immutable attempt namespace plus a short per-run lock. Never run two
attempts into the same result directory and never delete an earlier attempt:

```sh
attempt_id="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
attempt_root="$out_root/attempts/$attempt_id"
mkdir -p "$out_root/.locks" "$attempt_root"

exec {lock_fd}>"$out_root/.locks/$candidate.$suite_id.$run_name.lock"
flock -n "$lock_fd" || exit 75
run_root="$attempt_root/runs/$run_name"
mkdir -p "$run_root"
```

The persistent lock file contains no result data and need not be removed.
Holding its file descriptor covers marker creation, DUT execution, analyzer
execution, and artifact-manifest update for that run. The random attempt path
prevents another invocation from satisfying freshness with its output. Publish
the suite receipt inside that same attempt directory and expose a `latest`
pointer only after receipt success, using a separately locked atomic rename.
Do not repoint `latest` on failure.

The receipt proves completeness and artifact identity; it does not decide
metric thresholds. A caller must separately reject analyzer outcomes such as
`evaluable=0` before adding the analyzer artifact to the manifest.
