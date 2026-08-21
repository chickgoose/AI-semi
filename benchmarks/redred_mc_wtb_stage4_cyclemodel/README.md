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
  `ceil((t - start) * 1000 / 6500)` using integer `divmod` arithmetic.
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
is too late. Timeout, invalid bracket, missing left pose, and full pressure all
produce explicit ordered `raw_bypass` records. No path drops or flushes an
event.

A corrected delayed record is accepted only when its used-pose pair is the
occurrence-snapshot left pose plus the first strict right bracket; it declares
`intentional_future_pose_use=true`. Timeout, full-pressure, invalid-bracket,
and missing-left raw bypasses declare false and never list a future pose as
used. The arm remains `delayed_exact` with result label
`DIAGNOSTIC_UPPER_BOUND` for both corrected and raw dispositions.

`CycleReceipt` supplements the base decision fields without weakening them.
It binds serializer admission cycle/lane, optional transform launch
cycle/lane, retirement cycle/lane, FIFO occupancy immediately before and
after admission and retirement, disposition/reason, and the corresponding
decision-record digest. Raw delayed bypass has no launch cycle or lane.

The pose generators and quaternion interpolation math remain upstream of this
cycle/packet-selection boundary. `PosePacket.oracle_1khz` validates the frozen
delivery schedule for already serialized, hash-bound oracle packets; it does
not generate or score them.

Run the focused suite from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/redred_mc_wtb_stage4_cyclemodel -p 'test_*.py' -v
```
