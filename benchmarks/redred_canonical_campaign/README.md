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

Dataset IDs and classes are hard-bound: `full50` and `capacity22` are
synthetic, `organizer_supplied` is supplied, and `public_dataset` is public.
They cannot be renamed or relabeled. Synthetic suites must match the frozen
registry and committed manifest bytes. A supplied dataset needs a
`redred_dataset_provenance_v2` receipt containing its provider, delivery
identifier, license, and `{path, sha256}` references for content, adapter, and
trace manifest. A public receipt uses source URL, version, retrieval date, and
license with the same three byte references. All referenced bytes must be
locally accessible, nonsymlink files with matching hashes.

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
runs, inherits those runs' full50 windows even if a candidate's separate
full50 pointer is absent, and cannot be pooled with full50.

The actual-P6 envelope check is strict. It verifies the exact result schema,
boundary declarations, immutable package commit and pins, every locally pinned
file and tool, execution accounting, all three reset rows, qualification
boundary, and all 15 killed actual-RTL mutations with their exact diagnostics.
Unknown fields, missing fields, Boolean counters, and contradictory values are
fatal.

## Trust levels

The committed replay result has summary hashes but not committed event and
summary CSV bytes. Without those artifacts, its evidence is reported only as
`RECEIPT_CONSISTENT`, with `event_evidence=NOT_REPLAYED` and an explicit
statement that no independent event replay occurred. It is never described as
event-level validation.

If an untouched replay work directory is available, pass its directory that
directly contains `artifacts/`:

```sh
python3 benchmarks/redred_canonical_campaign/campaign.py validate \
  --verify-run-root /path/to/replay/work --allow-hold
```

The validator then requires every A2/A3 full50 and reset event/summary file,
checks its receipt hash, recomputes event identity/order, conservation,
throughput, per-run latencies, and full50/capacity22 aggregate latencies, and
marks only the event-evidence class as `ARTIFACT_RECOMPUTED`. The receipt
envelope remains `RECEIPT_CONSISTENT`, and overall release remains `HOLD`.

## Non-EDA use

Both commands are read-only and execute no simulator or EDA job:

```sh
python3 benchmarks/redred_canonical_campaign/campaign.py validate --allow-hold
python3 benchmarks/redred_canonical_campaign/campaign.py dry-run --allow-hold
```

`validate` checks the pinned existing evidence. `dry-run` performs the same
checks and additionally emits the exact pinned actual-P6 runner plan with
placeholders for a new work directory, output receipt, and pinned Verilator.
Neither mode executes that plan. Exit status is 3 for an honest `HOLD` and 2
for malformed, inconsistent, missing-but-claimed, or hash-mismatched evidence.
`--allow-hold` changes only a valid HOLD's exit code to 0; the emitted status
remains `HOLD`.

Run the focused tests with:

```sh
tests/redred_canonical_campaign/run_all.sh
```
