# A4 frozen-v4 transaction replay for A2/A3 K2 promotion

This additions-only A4 package converts the exact SHA-pinned generator-v4
`full50` and `capacity22` traces into cycle-explicit source-occurrence vectors
and runs them against the final A2 and A3 K2 owner RTL. It does not edit or
copy the common testbench, manifests, generator, official suite policy, or
either owner source.

The pinned owners are A2 `d74ff962aaf07c5209f1a1d1c69832735c654a0d`
and A3 `bd1c1ee955685fc077afe930116a03bc49a8218f`. Their native interfaces are
adapted by separate, storage-free A4 bindings to one atomic boundary:

- one pending occurrence latch per logical source;
- ordered `grant_count`/address acceptance only when the complete K2 bundle is
  ready;
- ordered normalized retirement lanes for the same committed transaction; and
- TB-only occurrence identity carried beside the owner, never into scheduler
  policy inputs.

Owner source is extracted with `git show <pinned-commit>:<source-path>` into
the caller's temporary work directory. The runner verifies the commit object,
regular blob mode/OID, and source SHA-256, and never compiles mutable HEAD,
index, or worktree bytes. A unit negative control advances HEAD and dirties the
same source path, then proves that only the pinned commit blob is materialized.

Every vector has one row for every original stimulus cycle and every drain
cycle. Its header records the exact generated-event count, half-open
measurement window, generated events inside that window, and allowed
accept-to-retire latency. The JSON bundle additionally records trace, index,
manifest, vector, occurrence-stream, and per-run provenance hashes. `capacity22`
is checked as the exact byte-identical subset of `full50`, not 22 new workloads.

The replay driver reconstructs source overrun, reset abort, acceptance, and
retirement from observed handshakes. It hard-checks ordered accept versus
retire identities globally, distinct K2 lanes, conservation, occurrence-to-
accept latency, accept-to-retire latency, measurement-window counts, held
transaction stability, reset quietness, and final drain. The directed
`reset_drain` vector stalls live work, injects an occupied-source overrun,
asserts reset to abort the pending epoch, then requires two post-reset sentinels
to drain with no phantom retirement.

The mutation gate must kill all of the following:

- a modified frozen JSONL trace;
- a reordered generation index;
- a rehashed vector whose occurrence is shifted away from its trace cycle; and
- a replay-driver cycle/index time shift.

Run the complete qualification from any directory:

```sh
tests/a4_k2_promotion/run_all.sh
```

Or retain an explicit machine-readable receipt:

```sh
python3 -B tests/a4_k2_promotion/run_promotion_replay.py \
  --work-dir /tmp/a4-k2-promotion-work \
  --output /tmp/a4-k2-promotion-result.json
```

All output paths are no-overwrite. Generated traces and vectors belong in
caller-selected temporary storage; no frozen/common file is changed.
