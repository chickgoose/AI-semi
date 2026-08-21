# MC-WTB Stage-4 score-free UZH input generator

This package converts the hash-pinned UZH `shapes_rotation` sources into the
frozen Stage-4 comparison inputs. It stops before every arm transform and
quality computation.

The generator:

- validates `events.txt`, `groundtruth.txt`, and `calib.txt` hashes before
  parsing, plus the event byte and line counts;
- validates the frozen comparison contract and existing 24-window registry;
- copies the registry's exact inclusive warmup start, inclusive query start,
  and exclusive query end into every manifest window summary, so downstream
  integration never reconstructs window bounds;
- preserves global source event IDs and emits timestamp, pixel, polarity,
  normalized sensor ray, query membership, equal-timestamp cluster identity,
  and exact integer occurrence cycle;
- forms one atomic batch per occurrence cycle with up to six ingress lanes.
  Every member carries the same two-pose pre-edge snapshot hash, and the
  development exact-timestamp burst is fail-closed at the corrected bound of
  five;
- packs the occurrence-baseline 102-bit payload, including a 14-bit causal
  pose source index. Matching the existing occurrence baseline, the remaining
  fields are a 24-bit dataset event index, 11-bit join sequence, 36-bit
  timestamp, 8-bit x/y, and polarity;
- passes accepted batches through a charged six-entry staging serializer. It
  atomically captures the complete up-to-six batch first, charges occupancy,
  and then presents at most two stable-order records on that same cycle. The
  manifest charges 612 payload-state bits, peak occupancy, entry-cycles, and
  payload bit-cycles. Overflow is a protocol failure; there is no implicit
  external queue;
- emits normalized, deterministic-sign dataset pose packets with
  arrival/commit/visibility cycles and a canonical hash for every packet;
- emits one authoritative occurrence `PoseSnapshot` per atomic batch. It is
  selected from the hash-bound dataset-packet stream as the true latest two
  packets whose commit cycles are strictly earlier than the occurrence edge.
  Each snapshot carries packet hashes, values, commit and visible cycles, and
  its packet-stream authority hash. Equal-timestamp members are fail-closed to
  one identical snapshot hash;
- constructs the counterfactual oracle stream on the global phase
  `t = 0 mod 1,000,000 ns`, using only two source brackets, the frozen
  shortest-arc rule, and explicit bracket provenance. Every oracle packet has
  a canonical `packet_sha256` over the packet without that field; the stream
  and each packet are verified before scheduling, and schedule records carry
  the exact packet hash they consume;
- serializes each stream as canonical JSONL and records its SHA-256, count, and
  byte size in a canonical manifest. The manifest also has one canonical
  binding over the ordered 26-hex-digit 102-bit records, raw event,
  calibration and ground-truth hashes, dataset-pose and occurrence-snapshot
  streams, oracle packet/schedule streams, generator dependency hashes, and
  Python runtime identity/executable hashes. Oracle authority additionally
  binds the canonical hash of the ordered packet-hash sequence.

The forbidden interval may be scanned by whole-file hashing/parsing but cannot
appear in any selected event, dataset pose packet, oracle packet, or schedule.

Outputs are `stage4_events.jsonl`, `stage4_occurrence_batches.jsonl`,
`stage4_occurrence_pose_snapshots.jsonl`, `stage4_dataset_pose_packets.jsonl`,
`oracle_resampled_groundtruth_1khz.jsonl`,
`stage4_oracle_window_schedule.jsonl`, and `stage4_input_manifest.json`.
They contain identities, source geometry, pose values, provenance, and timing
only. They contain no arm output, quality value, ranking, or disposition.

The public CLI accepts only the official frozen source pins:

```sh
python3 -m benchmarks.redred_mc_wtb_stage4_assay.generator \
  --dataset-dir <official-shapes_rotation> --output-dir <new-empty-directory>
```

Tests use an explicit `SYNTHETIC_FIXTURE_ONLY` pin set and never access the
official dataset or any holdout artifact.
