# REDRED A2/A3 single-edge campaign evidence boundary

This wrapper validates the published hardened actual-RTL replay receipt without
turning a receipt into artifacts that were not retained. Its current outcome is
deliberately split:

| Gate | State |
| --- | --- |
| committed hardened receipt | `PASS` |
| canonical synthetic receipt semantics | `PASS` |
| retained replay artifacts | `HOLD` |
| canonical single-edge campaign | `HOLD` |
| UZH public projected producer-native extension | `PASS` (noncanonical scope) |
| campaign-v3 public tuple binding | `HOLD_SCHEMA_INCOMPATIBLE_UNBOUND` |
| system release | `HOLD` |

The raw published result is pinned at SHA-256
`e21e714e4c4ebbeba4caf63ad5656b2b29fc05881ebb74ea6d93114c5f7d8cf4`.
Its sorted compact JSON semantic SHA-256 is
`9fd365edc6b5b57db8a99de32bde95117f08a6ada547abd6a0c44a8149cad56f`.
The validator reads those bytes from publication commit
`72491e45a35e6883bd4ee65d5c30409c108ab190` and closes their provenance to
hardened source commit `6fc5e167918fa4c54786c9a3abb5f60ecd8b991b`
and integration commit `a0a4eb38632245db8ff5937ea5b6c6e3f3839246`.

## Evidence classes do not mix

`full50` is the canonical REDRED campaign traffic, but it is
`TEAM_DEFINED_SYNTHETIC`. It is never organizer-supplied or official contest
traffic. The historical registry filename `scripts/common_suite_official.py`
does not change that provenance.

The UZH Shapes projection is a separate `PUBLIC_PROJECTED_EXTENSION`. Its
1x/64x/256x traces are timing variants of one source window containing exactly
1,100 source occurrences; they are not three independent samples. A reviewed
producer-native v2 result, reproduction, closed export, and publication are
committed under `tests/a23_public_projected_v2`. That package passes only its
noncanonical, nonofficial scope and is never pooled with full50.

The public dependency is no longer missing execution evidence. It remains
unbound here because the producer-native publication, manifest, result, member
paths, and CSV schemas are not the campaign-v3 generic sealed-tuple schemas.
Relabeling or lossy repacking is forbidden; a reviewed native-schema adapter or
slot-specific consumer is required before `public_v2.state` can become
`BOUND`.

## What the committed receipt establishes

The producer evidence class is exactly
`A23_FULL_SINGLE_EDGE_REPLAY_ACTUAL_RTL_V1` from
`tests/a23_full_single_edge_replay`. The wrapper checks its frozen full50 trace
roster, package pins, tool identities, hardened RTL bytes in both Git trees,
100 actual full50 executions, reset and mutation-activation executions, and
all eight killed literal RTL mutants. It reports generated, source overrun,
accepted, retired, occurrence-to-accept latency, and accept-to-retire latency
separately. The hard accounting rules are:

```text
generated = source_overrun + accepted
accepted = retired after bounded drain
```

No P6 or parallel-interface result is imported. The producer's receipt claims
single-edge digital RTL `GO`; physical, power, and CDC/RDC remain outside that
claim and stay `HOLD`.

## Why the campaign still holds

The committed result binds full50 prepared-input, event, and summary hashes,
auxiliary logs, and mutation logs, but it does not bind the full50 simulator
logs. The actual run artifacts are not committed here. A retained index must
name separate A2 and A3 prepared files for every full50 run; the wrapper hashes
their bytes and compares the two retained copies independently. Missing,
partial, extra, aliased, symlinked, size-mismatched, or hash-mismatched retained
artifacts fail closed. A new index cannot retroactively extend the semantics of
the old receipt, so even complete receipt-bound artifacts cannot lift the
campaign gate without the full50 logs and a producer-compatible sealed bundle.

The optional explicit input is therefore a retained-artifact index, not a
replacement result. It must conform to the pinned
[schema](replay_receipt.schema.json), bind both result hashes, and be supplied
as one complete tuple:

```sh
python3 benchmarks/redred_single_edge_campaign/campaign.py evaluate \
  --replay-schema benchmarks/redred_single_edge_campaign/replay_receipt.schema.json \
  --replay-schema-sha256 cb8b0e91c7a4f25191bbaff33692de440169d63cc97c8ed8a06ac9512c4500f4 \
  --replay-receipt /path/to/retained-artifact-index.json \
  --replay-receipt-sha256 <lowercase-64-hex-index-sha256> \
  --artifact-root /path/to/retained-artifact-root
```

Supplying only part of the tuple exits 2. The normal absent-artifact state is a
valid HOLD and exits 3; add `--allow-hold` to make that expected state exit 0.

```sh
python3 benchmarks/redred_single_edge_campaign/campaign.py evaluate --allow-hold
tests/redred_single_edge_campaign/run_all.sh
```

## Version-three sealed-tuple consumer

`campaign_v3.py` is a separately versioned consumer. It does not reinterpret
the legacy receipt and it does not search producer directories. Its committed
manifest has two independent `UNBOUND` inputs:

- `synthetic_v2`: canonical `TEAM_DEFINED_SYNTHETIC` actual-RTL evidence.
- `public_v2`: noncanonical `PUBLIC_PROJECTED_EXTENSION` actual-RTL evidence.

With neither tuple available, the exact gates are
`HOLD_MISSING_SYNTHETIC_V2_PRODUCER_TUPLE` and
`HOLD_MISSING_PUBLIC_V2_PRODUCER_TUPLE`. Supplying only a publication or only a
bundle is an error. Supplying bytes while the corresponding committed producer
entry is still `UNBOUND` is also an error; it can never manufacture a PASS.

```sh
python3 benchmarks/redred_single_edge_campaign/campaign_v3.py evaluate
# exit 3: valid HOLD
python3 benchmarks/redred_single_edge_campaign/campaign_v3.py evaluate --allow-hold
# exit 0: the same valid HOLD
```

A future producer promotion must update the committed manifest from `UNBOUND`
to `BOUND` and provide all of these independently reviewable fields:

```text
publication_sha256, publication_size_bytes
producer.commit, producer.tree
producer.verifier_sha256, producer.schema_sha256, producer.runner_sha256
producer.testbench_sha256, producer.tool_pins_sha256
producer.inventory: [{role, path, blob_sha256}] for the exact slot-specific
  verifier/schema/runner/testbench/tool-pins roster
rtl.source_commit, rtl.source_tree, rtl.integration_commit, rtl.integration_tree
rtl.source_inventory and rtl.integration_inventory:
  [{role, path, blob_sha256}] for the complete hardened source/filelist roster
bundle_sha256, bundle_size_bytes
manifest_schema, manifest_member, manifest_sha256, entry_count
result_schema, result_member, result_sha256, result_semantic_sha256,
result_size_bytes
owners, traffic_runs, reset_run, activation_run, mutations, diagnostics
```

The publication and gzip bundle are then required as one tuple. The consumer
rejects symlinks, hard links, aliases, unsafe or duplicate archive members,
duplicate JSON keys, unknown fields, stale/extra/missing members, and any raw or
semantic digest mismatch. It reads, but never extracts, the archive.

The bundle must retain one source JSONL and one prepared trace per traffic run;
both A2 and A3 run artifacts are recomputed against those same bytes. It also
requires per-event accept and retirement ordinals, recomputes conservation,
fixed-window throughput and both latency distributions, proves bounded reset
after clean drain with no protocol error, and checks nonvacuous activation and
the exact killed-mutation roster. A per-source one-entry latch replay requires
the first occurrence seen while a source is free to be the accepted occurrence,
keeps that source occupied through its acceptance edge, and classifies all
other same-source occurrences during that interval as overruns. Thus two
occurrences of one source on one edge can never both be accepted. Synthetic and
public aggregates remain separate. Official, P6, physical, power, selection,
and release claims remain outside the tuple and stay false/HOLD even after both
tuple gates validate.

## Campaign-native normalized aggregate gate

`aggregate_gate.py` consumes two separately verified normalized views rather
than reopening either producer-native archive. The input contract is
[`redred_single_edge_campaign_normalized_view_v1`](campaign_normalized_view.schema.json):
one `synthetic_v2` view and one `public_v2` view, each with a PASS verification
envelope, source result and publication hashes, false
official/P6/physical/power/release classifications, shared gate states, and
independent A2/A3 candidate gate states. This small schema is the stable seam
targeted by both producer-native adapters.

The public view must classify all retiming labels as one
`PUBLIC_DATASET_RETIMING_FAMILY` with `independent_sample_count: 1`.
Retimings may remain visible as within-family conditions, but the aggregate
result contains no pooled totals and never treats 1x/64x/256x as three samples.
Synthetic and public views may not opt into cross-slot pooling.

The decision is deliberately campaign-scoped:

- A2 is recommended when it passes both views and no shared gate is non-PASS.
- A3 is recommended only when exact-prefix semantics are explicitly requested,
  or A2 has a candidate-specific `FAIL`, and A3 independently passes both
  views.
- An A2 `HOLD`, any A3 non-PASS, or any shared failure produces HOLD. Shared
  interface, evidence, CDC/RDC, and PDK/I/O failures can never activate A3.
- `final_selected_candidate` is always null; final selection and release are
  always HOLD and official, physical, power, release claims are always false.

The output conforms to
[`aggregate_result.schema.json`](aggregate_result.schema.json). A scoped A2/A3
campaign recommendation is available only to the authenticated in-process
pipeline context. Standalone file inputs cannot prove which adapter created
them, even when their schema and claimed adapter hash look valid, so the CLI
always emits `HOLD_UNAUTHENTICATED_EXTERNAL_VIEWS` and exits 3.
`--allow-hold` converts that expected HOLD to exit 0; malformed or
contradictory inputs exit 2.

```sh
python3 benchmarks/redred_single_edge_campaign/aggregate_gate.py evaluate \
  --synthetic-v2-view /path/to/verified-synthetic-view.json \
  --public-v2-view /path/to/verified-public-view.json
```

`native_pipeline.py` is the end-to-end repository path. It invokes both native
adapters itself from hash-pinned local module bytes, validates the byte-pinned
[`team_canonical_policy.json`](team_canonical_policy.json), and applies exactly
three authorized synthetic changes: the full50 canonical-policy gate and the
A2/A3 candidate gates move from HOLD to PASS. No native adapter or public view
is rewritten. The promoted synthetic common view and unchanged public common
view are then passed to `aggregate_gate.py`.
The pass-capable aggregate call stays in memory and requires the pipeline's
private authentication context; external normalized files are never used by
this path.

The pipeline receipt retains each upstream raw artifact hash and the full
synthetic and public candidate metrics. Public 1x/64x/256x values remain
visible separately but still count as one public dataset family. Its result
schema is
[`native_pipeline_result.schema.json`](native_pipeline_result.schema.json).
The successful current result is only `A2_PRIMARY` at campaign scope; final
selection, official evidence, physical, power, and release remain false/HOLD.
The receipt ends with a canonical-JSON SHA-256 seal covering every non-seal
field.

```sh
python3 benchmarks/redred_single_edge_campaign/native_pipeline.py evaluate \
  --output /path/to/new-pipeline-result.json
```
