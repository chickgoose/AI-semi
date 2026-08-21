# MC-WTB Stage-4 score-free integration

This package is the fixture-tested bridge from canonical Stage-4 assay
artifacts to all four cycle arms, receipt v2, `ScoreFreeAccounting`, and
`ScoreInputManifest`. It does not call the scorer or calculate any metric.

The loader verifies canonical JSON/JSONL, every artifact byte/count/hash,
packet hashes, the authoritative assay binding, ordered 102-bit records, and
the refrozen contract/registry identities. Per-window integration runs the
full warm-up plus query stream through each cycle arm. Only afterward does it
project query decisions into receipt v2. A separate digest over the complete
cycle decisions and cycle receipts is included in the manifest's cycle-model
artifact binding, preventing warm-up evidence from being silently replaced.

World shadows are normalized active camera-to-world rotations of assay sensor
rays. ZOH and oracle use the latest occurrence packet; CAV and delayed
interpolation use the existing Stage-4 pose-recovery geometry and must agree
with cycle-record provenance.

Accounting is derived without inspecting ray quality. The always-bypass
retirement rows are the query subset of the full cycle result. Corrected
events plus deadline/full-pressure bypasses are attempted corrections;
no-pose/stale bypasses are freshness vetoes; invalid/missing-bracket bypasses
are invalid-pose bypasses. Buffer bit-cycles are the sum of
`102 * (retire_cycle - admission_cycle)` over the full warm-up and query run.
The trace state count charges the six-entry ingress serializer, observed arm
peak, and frozen sixteen-entry 192-bit pose ring. Event and pose bandwidths
use integer-ceiling bit counts over the explicitly serialized window limits.

Two current upstream limitations fail closed with stable blocker labels:

- `UPSTREAM_WINDOW_LIMITS_NOT_SERIALIZED`: current assay summaries do not
  serialize exact window start/end timestamps needed by the cycle and rate
  accounting boundary. The fixture supplies these anticipated fields.
- `UPSTREAM_SIGNED_HISTORY_CYCLE_UNSUPPORTED`: the assay legitimately emits
  pre-window negative commit cycles, while current cycle `PosePacket` and
  receipt-v2 types accept only non-negative cycles.
- `UPSTREAM_CYCLEMODEL_INGRESS_SCHEDULE_MISMATCH`: the current cycle model
  admits a newly captured occurrence batch in the same cycle, while the assay
  charged serializer presents it no earlier than the following cycle.
- `UPSTREAM_DELAYED_RAW_SHADOW_ARITY_UNREPRESENTABLE`: current `ShadowRay`
  requires two poses for `delayed_slerp`, but a valid delayed raw bypass may
  carry zero or one used pose.

Tests create only synthetic temporary artifacts. No official source, holdout,
arm result, scorer entry point, or metric is accessed.
