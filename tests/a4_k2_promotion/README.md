# A4 frozen-v4 transaction replay for A2/A3/A4 K2 promotion

This additions-only A4 package converts the exact SHA-pinned generator-v4
`full50` and `capacity22` traces into cycle-explicit source-occurrence vectors
and runs them against the final A2, A3, and A4 K2 owner RTL. It does not edit or
copy the common testbench, manifests, generator, official suite policy, or
any owner source.

The pinned owners are A2 `d74ff962aaf07c5209f1a1d1c69832735c654a0d`,
A3 `bd1c1ee955685fc077afe930116a03bc49a8218f`, and A4
`0e613b6933f1bb92e9b2f75b79a50663187f17d3`. Their native interfaces are
adapted by separate, storage-free A4-owned bindings to one atomic boundary:

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

## Frozen common edge ordering

The pinned A1 common TB SHA-256 is
`27d9437a5179b0cb909d02edee1ac2f82ea6d20aeab9cfb64997b458192102a2`.
It calls `generate_workload` at negedge and classifies a new occurrence against
the current pending latch before the following posedge acceptance/retirement
monitor. Therefore, if an old source record fires on that following edge, a
new same-source occurrence immediately before it is still `source_overrun`;
the source is rearmed only for the next occurrence edge. A focused RTL test
proves this exact `generated=3, overrun=1, accepted=retired=2` witness for all
three pinned owners. The report also records the old/new full replay delta;
the pre-resolution driver already implemented common ordering, so the expected
count and latency delta is zero rather than a reused result.

The tracked compact result is
`results/promotion_replay_summary.json`. It is checked against the SHA-256 of
the fresh full report on every `run_all.sh` execution. Each owner ran 73 cases
(50 full, 22 capacity-subset, one directed reset/drain): A2 accepted/retired
167294 events with maximum occurrence-to-accept latency 23, A3 accepted/retired
150927 with maximum 265, and A4 accepted/retired 163553 with maximum 23.
Accepted-to-retired latency remained zero at this normalized replay boundary.
All nine owner/suite comparisons against pre-resolution commit `0dda9a7` have
zero deltas for generation, overrun, reset abort, accept, retire, measured
retire, and both reported latency bounds.
