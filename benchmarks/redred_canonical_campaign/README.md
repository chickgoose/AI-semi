# Canonical REDRED digital campaign

This package is a fail-closed validation and planning layer over the frozen
common traces and the existing actual scheduler-plus-P6 replay. It does not
copy a trace generator, testbench, scheduler, P6 model, or result parser. The
manifest pins and reuses:

- `scripts/common_suite_official.py` for the ordered full50/capacity22 names
  and trace SHA-256 identities;
- the committed full50 and capacity22 manifests;
- `tests/a23_full_p6_replay/run_replay.py` and `run_all.sh`; and
- the committed actual-P6 receipt, selecting only its A2 and A3 owners.

The canonical candidate order is Fovea, Cluster2, A2+P6, and A3+P6. Current
Fovea/Cluster2 results were produced by a different recovered campaign, so
this package does not silently promote them into the canonical comparison.
Their missing same-campaign evidence is reported as `HOLD`.

## Provenance classes

Every dataset is exactly one of `synthetic`, `supplied`, or `public`.
Synthetic suites must match the frozen registry and committed manifest bytes.
A supplied dataset needs a `redred_dataset_provenance_v1` receipt containing
its provider, delivery identifier, license, archive hash, adapter hash, and
trace-manifest hash. A public dataset instead needs its source URL, version,
retrieval date, license, and the same content/adapter/trace identities.

The organizer-supplied and public entries are intentionally unpopulated in
`campaign.json`. Validation therefore emits `HOLD`; it never substitutes the
synthetic suite or fabricates dataset results.

## Enforced comparison contract

For every available hard-correct run the validator requires:

```text
generated = source_overrun + accepted
accepted = delivered
```

Here the inherited receipt's `retired` count is normalized to `delivered`.
Compared candidates must use the identical measurement-definition hash,
ordered run names, source trace SHA, prepared trace SHA, generated-event count,
and fixed-window cycle count. Aggregate totals are recomputed from per-run
evidence. Capacity22 must be a zero-execution view of the exact 22 full50
runs, and any aggregation group attempting to pool full50 with capacity22 is
rejected.

## Non-EDA use

Both commands are read-only and execute no simulator or EDA job:

```sh
python3 benchmarks/redred_canonical_campaign/campaign.py validate --allow-hold
python3 benchmarks/redred_canonical_campaign/campaign.py dry-run --allow-hold
```

`validate` checks the pinned existing evidence. `dry-run` performs the same
checks and additionally emits the exact pinned actual-P6 runner plan with
placeholders for a new work directory, output receipt, and pinned Verilator.
Neither mode executes that plan. Exit status is 0 for `PASS`, 3 for an honest
`HOLD`, and 2 for malformed, inconsistent, missing-but-claimed, or hash-mismatched
evidence. `--allow-hold` changes only a valid HOLD's exit code to 0; the emitted
status remains `HOLD`.

Run the focused tests with:

```sh
tests/redred_canonical_campaign/run_all.sh
```
