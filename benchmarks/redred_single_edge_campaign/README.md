# REDRED A2/A3 canonical single-edge campaign wrapper

This package is the canonical evidence boundary for an **independent** A2/A3
single-edge full50 replay. It does not contain a replay result and it does not
reuse P6 or parallel-interface measurements. The committed state is therefore
`HOLD_NO_ACTUAL_REPLAY_ARTIFACTS`.

`full50` is identified everywhere as the team-defined synthetic suite. It is
not organizer-supplied or official competition data. The historical filename
`scripts/common_suite_official.py` is used only as the frozen team trace SHA-256
registry; the wrapper never changes the dataset's provenance class.

## Explicit replay interchange

The producer under `tests/a23_full_single_edge_replay` can interoperate without
being imported or guessed. It must emit a receipt conforming byte-for-byte to
the pinned [replay receipt schema](replay_receipt.schema.json), retain all
referenced artifacts below one nonsymlink artifact root, and invoke:

```sh
python3 benchmarks/redred_single_edge_campaign/campaign.py evaluate \
  --replay-schema tests/a23_full_single_edge_replay/replay_receipt.schema.json \
  --replay-schema-sha256 <lowercase-64-hex-schema-sha256> \
  --replay-receipt tests/a23_full_single_edge_replay/result.json \
  --replay-receipt-sha256 <lowercase-64-hex-receipt-sha256> \
  --artifact-root /path/to/actual-single-edge-replay-root
```

The schema path/hash, receipt path/hash, and artifact root are all explicit.
Supplying only part of that tuple is fatal. Supplying none is a valid `HOLD`
(exit 3, or exit 0 with `--allow-hold`). A claimed receipt with absent,
symlinked, size-mismatched, or hash-mismatched artifacts is malformed evidence
and exits 2.

## Required closure

Both candidates must use exactly the ordered 50 frozen trace hashes, identical
prepared inputs, identical fixed windows, and one immutable common tool,
testbench, runner, and cycle-semantics binding. Each candidate additionally
needs a hash-closed single-edge RTL inventory and, for every run, actual event
JSONL, summary JSON, and simulator log artifacts.

The wrapper recomputes event identity and order against the immutable trace
bytes. It separately reports:

```text
generated
source_overrun
accepted
retired
occurrence_to_accept = accept_cycle - occurrence_cycle
accept_to_retire      = retire_cycle - accept_cycle
```

A hard-correct run must satisfy
`generated = source_overrun + accepted` and `accepted = retired`. Phantom,
duplicate, corruption, reorder, accepted-missing, partial retirement, illegal
output, drain timeout, reset escape, and protocol error counters must all be
zero. `source_overrun` is a capacity loss and is never folded into a hard-error
counter.

Artifact paths containing historical P6/parallel-result lineage are rejected;
the receipt must also declare no borrowed P6 or parallel results. A test-only
receipt class is not accepted. The only accepted producer evidence class is
`A23_FULL_SINGLE_EDGE_REPLAY_ACTUAL_RTL_V1`, rooted at
`tests/a23_full_single_edge_replay` and bound to single-edge RTL commit
`4ce4836fab1309d3468db8e660d2da9af371f784`. Each inventoried RTL source is
checked byte-for-byte against that commit. A complete exact-producer receipt
can set this package's digital gate to `GO`; system release still remains
outside this digital campaign's scope.

## Current HOLD and tests

```sh
python3 benchmarks/redred_single_edge_campaign/campaign.py evaluate
tests/redred_single_edge_campaign/run_all.sh
```

No actual replay receipt or artifacts are committed here. The first command
must continue to report `HOLD` until the external replay producer supplies the
complete immutable input tuple.
