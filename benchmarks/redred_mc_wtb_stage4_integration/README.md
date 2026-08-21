# MC-WTB Stage-4 score-free integration

This package is the fixture-tested bridge from canonical Stage-4 assay
artifacts to all four cycle arms, receipt v2, `ScoreFreeAccounting`, and
`ScoreInputManifest`. It does not call the scorer or calculate any metric.

The loader requires the caller's expected canonical assay-manifest SHA-256
before it reads any artifact. It then verifies canonical JSON/JSONL, every
artifact byte/count/hash, the authoritative assay binding, ordered 102-bit
records, and the refrozen contract/registry identities. Dataset, occurrence
snapshot, and oracle pose-value hashes are recomputed from pose ID, timestamp,
and quaternion. Snapshot packet hashes must resolve to the hash-pinned dataset
packet stream, and equal-timestamp clusters must retain one snapshot.

Per-window integration runs the signed warm-up plus query stream through each
cycle arm with same-cycle admissions. Only afterward does it project query
decisions into receipt v2. `ScoreInputManifest` v2 and independently supplied
`ScoreBoundaryEvidence` bind the authoritative assay manifest, full cycle
result (including pose-ring accounting), cycle receipts, and receipt query
projection. The projection digest is exactly
`DecisionReceipt.decision_records_sha256`. Manifest artifacts additionally
bind the assay authority SHA and hashes of this adapter and the reviewed pose
recovery geometry.

World shadows use the reviewed `rotate_sensor_ray_to_world` camera-to-world
implementation. ZOH and oracle use the latest occurrence packet; CAV and
delayed interpolation use the existing Stage-4 pose-recovery geometry.
Because the current scorer cannot represent a delayed raw record's score-only
future bracket, the adapter derives that shadow from the authoritative offline
dataset stream. It raises
`UPSTREAM_DELAYED_RAW_SHADOW_ARITY_UNREPRESENTABLE` only when that bracket is
unavailable.

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

One assay-boundary limitation remains fail closed:

- `UPSTREAM_WINDOW_LIMITS_NOT_SERIALIZED`: current assay summaries do not
  serialize exact warm-up start, query start, and window end timestamps needed
  by the cycle and rate accounting boundary. The fixture supplies these
  anticipated fields.

Tests create only synthetic temporary artifacts. No official source, holdout,
arm result, scorer entry point, or metric is accessed.
