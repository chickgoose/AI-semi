# MC-WTB Stage-4 score-free cycle model

This package is an executable timing and disposition model for the normative
Stage-4 comparison contract. It accepts only event identity/timing, pose
packet validity/provenance, and transform guard inputs. It has no quality,
loss, scorer, reference-bank, arm-ranking, or UZH scoring interface.

The public entry point is `run_cycle_model(...)`. Inputs are immutable
`Event` and `PosePacket` values and one frozen `Arm`. The result contains one
ordered `DecisionRecord` per input event, a deterministic canonical SHA-256
over those records, a separate deterministic `CycleReceipt` per event, and
fixed accounting metadata.

## Frozen timing

- `timestamp_to_cycle(t, start)` computes
  `ceil((t - start) * 1000 / 6500)` using exact integer arithmetic and rejects
  an event timestamp before the window start. `pose_timestamp_to_cycle`
  applies the same ceiling to a signed difference, so committed pose history
  before the event window has negative relative cycles. Pose timestamps remain
  nonnegative; only their window-relative commit cycles may be signed.
- Dataset packets commit on their timestamp cycle and become visible only
  when `commit_cycle < event_cycle`; oracle packets commit one cycle after
  their global-phase 1 kHz effective timestamp and likewise become visible on
  the following cycle.
- Event occurrence snapshots are computed before same-edge pose commits.
  Six raw ingress lanes atomically capture up to six records into a charged
  six-entry staging serializer. The observed development maximum exact-time
  burst is five. The serializer serves stable event-ID order at two records
  per cycle, and every member retains its occurrence-edge snapshot even when
  it exits staging later.
- Any present `Event.causal_pose_index` must fit the frozen 14-bit payload. In
  normal integration mode, every dataset-arm event must carry it, and it must
  equal the latest pose in the occurrence-edge snapshot. Missing or corrupted
  bindings fail closed. Tests that intentionally synthesize events without it
  must opt into
  `synthetic_test_mode=True`; the result-level
  `all_event_pose_indices_verified` and per-event cycle receipt state disclose
  whether every applicable binding was verified. Oracle events require this
  dataset-only field to be `None`; their cycle receipts mark it not applicable,
  and oracle packet identity is checked against the global 1 kHz schedule.
- Common serializer residence is included in occurrence-to-retire latency.
  `SimulationResult.common_serializer_cycles` and the matching
  `always_bypass_retire_cycles` make it explicit; policy-added latency is
  `retire_cycle - always_bypass_retire_cycle`, so common serialization is
  subtracted rather than credited to or charged against an arm.
- `queue_cycles` counts only residency after serializer exit inside the arm;
  it does not double-count the common ingress staging delay.
- Causal arm selection is frozen at occurrence. Queueing never rereads pose
  state. Every causal selection or bypass traverses the one-cycle transform
  pipeline without lane reordering.

## Charged pose ring

All pose packets are replayed into an immutable-while-referenced 16-entry by
192-bit circular ring. The charge is exactly 3,072 state bits; provenance
hashes are verification evidence and are not counted as hardware state. A
packet's authoritative nonnegative `pose_id` assigns slot `pose_id % 16`.
Packet IDs are not limited to 14 bits: oracle IDs are the global 1 kHz phase
index and may exceed 16,383. Duplicate IDs and decreasing presentation order
are rejected. Gaps are explicitly legal and never renumber later slots, so
deleting a packet cannot change the slot phase of every following packet.
An event's occurrence-snapshot references become live before pose writes on
that occurrence cycle and remain live through its retirement phase. A delayed
right-bracket reference becomes live at transform launch and likewise remains
live through retirement. An invalid delayed right packet inspected to choose
`invalid_pose` is also a live internal ring reference from inspection through
that raw retirement, even though receipt-v2 correctly keeps it out of
`used_pose_*`. Cycle evidence also lists every precise failed check in frozen
order: `left_value_invalid`, `right_value_invalid`,
`left_arithmetic_invalid`, `right_arithmetic_invalid`, then
`transform_guard_invalid`. Thus `invalid_pose` never ambiguously attributes a
guard failure to the inspected right packet, and simultaneous failures are all
preserved.

`SimulationResult.pose_ring_entries` and `pose_ring_state_bits` expose the
fixed charge directly. `pose_ring_accounting` additionally exposes writes,
safe overwrites, peak occupancy, peak live references, reference checks, and
zero successful-run failures, with a deterministic evidence digest. An
attempted overwrite of a live slot, or resolution of a reference that is no
longer resident, raises `PoseRingSafetyError`; its immutable
`PoseRingFailureEvidence` binds the reason, signed cycle, slot, involved pose
IDs, live event IDs, partial accounting, fixed ring charge, and its own
deterministic digest. No decision result is returned on this fail-closed path.

`zoh_freshness` and the oracle interface use the inclusive 1 ms age gate.
`causal_cav` selects the two occurrence-snapshot poses only when pose values,
arithmetic, and the event guard are valid and
`age <= min(5 ms, latest_timestamp - previous_timestamp)`; otherwise it uses
valid fresh ZOH or raw bypass.

## Delayed diagnostic FIFO

`delayed_exact` is always labeled `DIAGNOSTIC_UPPER_BOUND`. Its complete FIFO,
including entries already launched into the one-cycle transform, is exactly
1,024 x 102-bit records. The 102-bit payload already includes the 14-bit
causal pose index. Each cycle observes visible poses, retires up to two
ordered heads, applies full-pressure oldest-head raw bypass if needed, admits
up to two records, and launches only a consecutive ready head prefix.

The deadline is
`occurrence_cycle + ceil(6_000_000 * 1000 / 6500)`. A right bracket committing
at `D-1` is visible at `D` and may launch; a packet committing at `D` or later
is too late. The receipt-v2 raw reason tokens are exactly `deadline_timeout`,
`fifo_full_forced_bypass`, `invalid_pose`, and `missing_bracket`. These cases
all produce explicit ordered `raw_bypass` records. No path drops or flushes an
event.

A corrected delayed record is accepted only when its used-pose pair is the
occurrence-snapshot left pose plus the first strict right bracket; it declares
`intentional_future_pose_use=true`. Timeout, full-pressure, invalid-bracket,
and missing-left raw bypasses declare false and never list a future pose as
used. The arm remains `delayed_exact` with semantic label
`DIAGNOSTIC_UPPER_BOUND` for both corrected and raw dispositions. Both causal
arms use receipt-v2 label `CAUSAL_CANDIDATE`; the oracle uses
`INTERFACE_VALUE_ONLY`. Every `DecisionRecord` and `CycleReceipt` carries this
exact label, and the result-level label is identical.

`CycleReceipt` supplements the base decision fields without weakening them.
It binds serializer admission cycle/lane, optional transform launch
cycle/lane, retirement cycle/lane, FIFO occupancy immediately before and
after admission and retirement, disposition/reason, and the corresponding
decision-record digest. It also binds event-index verification and any invalid
right-packet inspection cycle/provenance plus exact failure-cause codes. Raw
delayed bypass has no launch cycle or lane.

The pose generators and quaternion interpolation math remain upstream of this
cycle/packet-selection boundary. `PosePacket.oracle_1khz` validates the frozen
delivery schedule for already serialized, hash-bound oracle packets; it does
not generate or score them.

## Unbounded-depth diagnostic

`run_delayed_unbounded_diagnostic(...)` is a separate, delayed-only,
score-free replay used to establish the minimum zero-loss FIFO depth. It takes
the same immutable `Event` and dataset `PosePacket` values as the bounded
`delayed_exact` run and applies the same validation, six-record atomic ingress,
stable two-per-cycle admission, strict pose visibility, deadline, ordered
two-lane retirement, one-cycle transform pipeline, cycle receipts, and charged
pose-ring verification. Its sole semantic difference is that it neither caps
the delayed queue at 1,024 entries nor emits the bounded
`fifo_full_forced_bypass` pressure action. It cannot change or supplement a
bounded run's decisions.

The returned frozen `DelayedUnboundedDiagnosticEvidence` contains:

- the exact immutable validated `Event` and `PosePacket` sequences,
  `window_start_ns`, and canonical input-stream hashes, including unused pose
  packets and event guards that do not change a disposition;
- every ordered input and retired event ID plus their canonical hashes and
  counts;
- every `DecisionRecord` and `CycleReceipt`, their independent stream hashes,
  common serializer and policy-added latency accounting;
- exact peak unbounded FIFO depth and peak ingress-staging occupancy;
- the immutable `DelayedUnboundedDiagnosticConfig` and its identity hash,
  including timing, lane, deadline, visibility, priority, pipeline, and the
  one removed pressure action;
- exact-once ordered conservation, an explicit no-full-pressure-reasons flag,
  pose-index verification, and pose-ring accounting with its evidence hash;
- a deterministic `evidence_sha256` property over the complete canonical
  evidence body.

`evidence.validate()` fails closed on altered input events, pose packets,
window timebase, IDs, counts, ordering, config, decision or receipt bindings,
peak depth, latency cardinality, pose-ring hash, or any full-pressure reason.
It also deterministically replays the embedded validated inputs and requires
exact equality of decisions, cycle receipts, FIFO/staging peaks, and pose-ring
accounting, so recomputing an input subhash cannot legitimize inconsistent
retirement evidence.
Below 1,024 entries, native tests require byte-for-
byte equality with the bounded decisions and receipts and pin pre-extension
bounded hashes. Above 1,024, tests require identical admissions but no pressure
bypass, exact ordered retirement, and the observed unbounded peak.

Run the focused suite from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/redred_mc_wtb_stage4_cyclemodel -p 'test_*.py' -v
```
