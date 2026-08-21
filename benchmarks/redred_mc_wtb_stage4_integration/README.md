# MC-WTB Stage-4 score-free integration

This package is the fixture-tested bridge from canonical Stage-4 assay
artifacts to all four cycle arms, receipt v2, `ScoreFreeAccounting`, and
`ScoreInputManifest`. It does not call the scorer or calculate any metric.

The loader requires the caller's expected canonical assay-manifest SHA-256
before it reads any artifact. It then verifies canonical JSON/JSONL, every
artifact byte/count/hash, the authoritative assay binding, ordered 102-bit
records, and the refrozen contract/registry identities. Dataset, occurrence
snapshot, and oracle pose-value hashes are recomputed from pose ID, timestamp,
and quaternion. Dataset, snapshot, and oracle `packet_sha256` values must close
against their canonical records and ordered stream authorities. Snapshot
packet hashes must resolve to the hash-pinned dataset packet stream, and
equal-timestamp clusters must retain one snapshot. The captured calibration
authority is independently validated, then every sensor ray is recomputed
from the payload-bound x/y values with the reviewed radtan model before use.

Per-window integration reads the assay's
`warmup_start_ns_inclusive`/`query_start_ns_inclusive`/
`query_end_ns_exclusive` bounds and runs the signed warm-up plus query stream
through each cycle arm with same-cycle admissions. Each event carries the
payload-bound 14-bit causal pose index. Every dataset arm must return
`all_event_pose_indices_verified` and matching per-event cycle-receipt
evidence without synthetic test mode. Only afterward does integration project
query decisions into receipt v2. `ScoreInputManifest` v2 and independently supplied
`ScoreBoundaryEvidence` bind the authoritative assay manifest, full cycle
result (including pose-ring accounting), cycle receipts, and receipt query
projection. The projection digest is exactly
`DecisionReceipt.decision_records_sha256`. Manifest artifacts additionally
bind the assay authority SHA and hashes of this adapter and the reviewed pose
recovery geometry.

World shadows use the reviewed `rotate_sensor_ray_to_world` camera-to-world
implementation. ZOH and oracle use the latest occurrence packet; CAV and
delayed interpolation use the existing Stage-4 pose-recovery geometry. For a
delayed raw record, the adapter supplies the scorer-supported authoritative
offline left/right bracket from the sealed dataset stream; the runtime record
remains unchanged and declares no future pose use.

Accounting is derived without inspecting ray quality and uses an arm-specific
reason table. Delayed `invalid_pose` is operational and attempted because a
right bracket was inspected after waiting; delayed `missing_bracket` is an
invalid-pose bypass. Buffer bit-cycles are
`102 * (sum(admission-occurrence) + delayed-only sum(retire-admission))` over
the full stream. Event bandwidth is the integer-ceiling query record rate over
the explicit query interval. Pose bandwidth is conservatively 192,000 bit/s.

Every arm is conservatively charged 108,799 state bits: 104,448 delayed-FIFO
payload bits, 612 ingress payload bits, 3,072 pose-ring payload bits, plus
exact control components of 31 FIFO pointer/count, 6 ingress count/cursor, 9
pose-ring pointer/valid, 176 live references, 204 pipeline, 192 pose ingress,
21 global-cycle, and 28 status-counter bits. The immutable accounting evidence
serializes every component, category ID, reason policy, residency term, and
pose-ring digest. A `fifo_full_forced_bypass` fails closed with
`UNBOUNDED_REPLAY_REQUIRED_FOR_MINIMUM_ZERO_LOSS_DEPTH`; bounded peak occupancy
is not presented as a minimum zero-loss depth in that case.

Tests include both compact hand-built artifacts and an end-to-end output from
the actual score-free assay generator. They use synthetic temporary sources
only. No official source, holdout, arm result, scorer entry point, or metric is
accessed.
