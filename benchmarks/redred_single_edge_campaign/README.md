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
| UZH public projected extension | `HOLD` |
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
1,100 source occurrences; they are not three independent samples. The
projection specification and expected trace hashes are committed, but no
retained projection receipt or actual A2/A3 replay receipt is committed. It
therefore remains `HOLD_PUBLIC_PROJECTED_EXTENSION_UNREPLAYED` and is never
pooled with full50.

The public dependency is a retained projection package followed by actual A2
and A3 replay on each identical projected trace, with the same fixed window,
prepared input, tool, and replay boundary for both candidates. This wrapper
does not depend on uncommitted public replay work.

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

The committed result binds full50 event and summary hashes, auxiliary logs,
and mutation logs, but it does not bind the full50 simulator logs. The actual
run artifacts are not committed here. Missing, partial, extra, symlinked,
size-mismatched, or hash-mismatched retained artifacts fail closed. A new index
cannot retroactively extend the semantics of the old receipt, so even complete
receipt-bound artifacts cannot lift the campaign gate without a producer
receipt that also hashes the full50 simulator logs.

The optional explicit input is therefore a retained-artifact index, not a
replacement result. It must conform to the pinned
[schema](replay_receipt.schema.json), bind both result hashes, and be supplied
as one complete tuple:

```sh
python3 benchmarks/redred_single_edge_campaign/campaign.py evaluate \
  --replay-schema benchmarks/redred_single_edge_campaign/replay_receipt.schema.json \
  --replay-schema-sha256 72b7842d3856a6e38d8f9e9983110d1cdb88129c7ed9e7cadacbfa0c6a06461d \
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
