# MC-WTB Stage 3 candidate-neutral cost and integration review — 2026-08-22

Status: **pre-RTL review contract only; all three candidates remain hardware HOLD**

Scope authority:

- `docs/MC_WTB_PREDICTOR_STAGE12_CONTRACT_20260822.md`
- `docs/MC_WTB_STAGE12_ARCHITECTURE_CANDIDATES_20260822.md`

This review defines one candidate-neutral method for estimating logical
hardware cost and accepting a model into the MC-WTB integration boundary. It
does not implement `RG3-CAV`, `DSPB`, or `SO3-PLL`; select an accuracy winner;
open external data; create RTL; or measure synthesis, timing, area, power, or
place-and-route. A software accuracy verdict and an implementation-feasibility
verdict are separate. Neither can compensate for failure of the other.

## 1. Review outcome and claim boundary

At this checkpoint the only defensible hardware conclusions are:

1. the required accounting method and integration gates are defined;
2. each candidate has a candidate-specific inventory and falsification list;
3. all three candidates are provisionally `N3` because RG3 uses acceleration,
   DSPB adapts model credit/selection, and SO3-PLL uses adaptive feedback;
4. no candidate has supplied the frozen widths, schedules, bit-true arithmetic,
   or integration receipts needed to assign final `S`, `C`, or `P` classes;
5. consequently every candidate is `MODEL_STUDY_ONLY / PRE_RTL_HOLD`.

The 6.5 ns endpoint target, two events per cycle, and `II_event=1` are
requirements to be preserved, not achieved results. The existing 108,799-bit
logical envelope gives no free storage credit. Existing 45 nm elaboration
evidence is not timing, area, power, or P&R evidence for any predictor.

Permitted pre-RTL statements are limited to exact or symbolic logical bit
counts, typed operator counts, port demand, a schedule bound, symbolic
combinational depth, pipeline-register bits, fixed latency/II declarations,
maximum logical wire width/fanout, and disclosed numeric risk. The following
terms are prohibited without later same-flow implementation evidence:
`area`, `gate equivalents`, `timing met`, `frequency met`, `slack`, `power`,
`energy/event`, `PPA improvement`, `routing feasible`, and `P&R ready`.

## 2. Candidate-neutral CNCP record

Every candidate version shall publish one immutable record:

```text
CNCP = (B_ff, B_sram, read_ports, write_ports, O_pose, O_event,
        II_event, critical_depth, pipeline_bits, max_wire_width,
        numeric_risk)
```

The record is produced twice:

- `CNCP_delta_A`: all resources added to the frozen current-CAV endpoint `A`;
- `CNCP_total_endpoint`: the complete endpoint after integration, including
  `A`, fallback, predictor, arbitration, publication, and adapter state.

The delta view prevents an existing baseline resource from being charged
twice. The total view prevents a candidate from hiding cost by claiming that a
new resource is “shared.” A sharing credit is legal only when the manifest
identifies the same physical logical instance, proves non-overlapping lifetime
or a sufficient port/schedule, and counts every mux, arbitration register,
fanout buffer, and replicated bank needed to share it.

### 2.1 Frozen width vocabulary

Each candidate manifest shall bind, at minimum:

| Symbol | Meaning |
|---|---|
| `Wq` | bits per quaternion component, including sign |
| `Wv` | bits per angular-rate/tangent-vector component |
| `Wa` | bits per acceleration component |
| `Wr` | bits per residual, score, or loop-error component |
| `Wt` | bits per timestamp or time delta |
| `Wg` | bits per gain, threshold, clamp, or hysteresis value |
| `Wc` | bits per counter, epoch, version, or reason code |
| `Wp` | bits per valid/lock/publication flag bundle |
| `L` | number of event lanes; frozen endpoint value is two |
| `E` | number of DSPB experts; Stage12 value is exactly four |

Signedness, radix point, saturation bound, rounding mode, reset value, and
invalid encoding are part of each width. A width written as “software float,”
“TBD,” or an unconstrained host integer does not support a CNCP class.

### 2.2 State and memory accounting

`B_ff` is the sum of all live sequential logical bits:

```text
B_ff = B_architectural_state + B_control_and_validity + B_pipeline
       + B_fallback_and_replay + B_debug_required_for_correctness
```

`pipeline_bits` is the `B_pipeline` subset and is reported separately; it is
not added a second time. Count pose/rate histories, timestamps, residuals,
gains, thresholds, valid/lock flags, winner and fallback state, counters,
epoch/version IDs, pending updates, atomic publication copies, skid/holding
registers, replay state, and correctness-required receipts. Diagnostic state
may be excluded only when removal cannot change runtime behavior and the
diagnostic is absent from the proposed endpoint.

`B_sram` counts logical bits in every inferred or proposed array. Replication
to obtain ports counts once per replica. Register realization of a proposed
array moves the bits to `B_ff`; it does not erase them. Report each array as:

```text
(name, depth, width, copies, read_ports/copy, write_ports/copy,
 read_latency, write_policy, simultaneous_access_rule)
```

Read/write ports are peak simultaneous demands in the frozen schedule, not the
number of source-code accesses. Multi-pumping, time multiplexing, dual-port
assumptions, or bank conflicts require an explicit minimum-clock and
minimum-pose-commit-spacing proof. The nominal UZH pose cadence cannot be used
as free cycles for an on-chip interface whose minimum commit interval is not
frozen.

Stage12 state classes apply to candidate-added state, including its charged
pipeline and fallback additions:

| Class | Candidate-added bits |
|---|---:|
| `S0` | `<= 256` |
| `S1` | `257..1024` |
| `S2` | `1025..4096` |
| `S3` | `> 4096` |

### 2.3 Operator accounting

`O_pose` and `O_event` are typed multisets, not scalar “operation counts.”
Every row shall contain:

```text
(operation, operand widths, result width, signed/radix convention,
 quantity, update domain, initiation interval, maximum latency,
 shared_instance_id, replication factor, saturation/rounding point)
```

Count add/subtract, compare, mux, priority/arg-min, multiply, reciprocal or
divide, dot/cross product, quaternion multiply, vector transport, norm,
normalize, Log, Exp, table lookup, and conversion. Constant multiplication is
still an operator unless the exact shift/add expansion is declared. A Log/Exp
or normalize block cannot be summarized as one add. Arbitration and fallback
muxes belong to `O_event` when they affect the event path.

Use the Stage12 compute classes:

| Class | Meaning |
|---|---|
| `C0` | add/compare only |
| `C1` | fixed multiply present |
| `C2` | shared pose-rate nonlinear work |
| `C3` | event-rate nonlinear work |
| `C4` | replicated two-lane nonlinear work |

A replicated pose-rate nonlinear engine is reported as `C2-R<n>` and is
treated as above the ordinary `C2` promotion ceiling until a separate review
accepts its replication. It must not be force-fit into `C2` merely because it
runs at pose rate.

### 2.4 Critical-depth accounting

Build two dependency DAGs: `D_pose` from committed-pose input to atomic
publication, and `D_event` from accepted event plus published predictor state
to the two event outputs. Report the longest combinational chain in typed
primitive levels, for example:

```text
D_event = compare -> validity gate -> age arithmetic -> predictor mux
          -> fallback mux -> output register
```

The DAG includes validity checks, state-version selection, winner selection,
fallback priority, saturation detection, address decode, and output muxing.
Log, Exp, reciprocal, normalize, memory read, and multiply remain named opaque
nodes until a bit-true microarchitecture fixes their internal depth. A symbolic
depth must not be converted to nanoseconds or a frequency claim.

The event path is the acceptance-critical path. Pose work may be multi-cycle,
but its worst-case schedule must publish before the next permitted pose commit
without blocking, corrupting, or retroactively changing an event decision.

### 2.5 Pipeline, latency, and wire accounting

For every stage report stage name, register width, replication by lane,
valid/epoch/fallback sideband width, fixed latency, and backpressure rule.
`pipeline_bits` includes data and all sideband registers. Report both
candidate-added event latency and total endpoint latency.

Acceptance requires `L=2`, `II_event=1`, fixed declared latency, equal treatment
of both lanes, and no policy-added event buffering. Variable iteration,
data-dependent stall, an uncharged skid buffer, or a single nonlinear unit
silently serialized across the two event lanes is a STOP.

Stage12 pipeline classes count candidate-added registered stages on the
acceptance path:

| Class | Candidate-added stages |
|---|---:|
| `P0` | `<= 1` |
| `P1` | `2..3` |
| `P2` | `4..8` |
| `P3` | `> 8` or variable |

`max_wire_width` is the widest logical bus after including state version,
validity, fallback, and lane sidebands. Also report maximum logical fanout and
the consumers of any quaternion, gain, winner ID, or global validity signal.
Fanout is not converted to capacitance or power at this stage.

### 2.6 Numeric-risk accounting

The manifest shall cover analytic ranges and bit-true mutations for zero or
negative `dt`, unequal cadence, quaternion sign canonicalization, norm drift,
near-pi Log ambiguity, small-angle cancellation, overflow, underflow,
saturation, rounding bias, gain quantization, accumulator windup, hysteresis,
limit cycles, timestamp wrap, and invalid-pose recovery.

Stage12 numeric classes remain:

| Class | Meaning |
|---|---|
| `N0` | exact integer |
| `N1` | bounded fixed point |
| `N2` | normalization or division with a bounded bit-true realization |
| `N3` | acceleration or adaptive feedback whose bounded realization is not yet accepted |

All three candidates begin at `N3`. Only a separately reviewed, fixed-iteration,
bit-true profile with explicit ranges, errors, saturation behavior, and
fallback equivalence may request reclassification to `N2`. Floating-point
agreement alone cannot reduce numeric risk.

### 2.7 Estimate confidence and closure

Every numeric CNCP field carries one of three statuses:

- `EXACT`: derived from a frozen field/operator/schedule manifest;
- `BOUNDED`: a symbolic lower and upper bound are both stated, with every
  parameter bound frozen;
- `UNBOUNDED`: a width, iteration count, replication factor, port schedule,
  or lifetime is missing.

Candidate totals are interval sums of their rows. A class may be assigned only
when the upper bound fits that class; crossing a class boundary reports the
larger class. One `UNBOUNDED` acceptance-path item makes the corresponding
`S`, `C`, `P`, depth, latency, or wire field unknown and forces HOLD. Best-case
sharing, typical cadence, compiler constant folding, or expected sparsity may
be reported as sensitivity analysis but never as the promotion estimate.

## 3. Candidate-specific mandatory inventories

The following are minimum line items. Omitting an item is not a zero-cost
estimate; it is `CNCP_INCOMPLETE`.

### 3.1 RG3-CAV

State inventory:

- the third committed pose and timestamp beyond current CAV;
- latest transported angular rate and three-component acceleration, if cached;
- cadence, residual, direction, magnitude, reversal, and clamp parameters;
- history-valid, frame/version, saturation, and fallback state;
- atomic publication and any cached horizon coefficients;
- every event-lane pipeline and sideband register.

The incremental architectural-state worksheet shall include at least:

```text
4*Wq + Wt                     extra pose/history floor
+ 3*Wa                        cached acceleration, when stored
+ 3*Wv                        extra transported-rate cache, when stored
+ sum(all gate/bound fields)
+ validity/version/fallback/publication bits
+ pipeline_bits
```

If a value is recomputed instead of stored, remove its state bits but add the
complete operator and schedule cost.

Operator inventory includes the additional relative rotation, Log, unequal-
cadence reciprocal/division, vector transports, acceleration difference and
scale, horizon-square term, clamp/gate comparisons, Exp/normalization path,
and final fallback mux. Reuse of current-CAV Log/Exp is credited only with a
conflict-free pose/event schedule.

Critical-depth review must show that acceleration gates do not enter the
two-lane event path as an unpipelined compare/multiply/normalize chain. Numeric
review must falsify coupled-axis frame transport, second-difference noise,
small-`dt` amplification, reversal, stopping, near-pi, and cancellation.

Provisional classification: `N3`; `S?`, `C?`, and `P?` remain unassigned. A
shared pose-rate realization may request `C2`; event-rate Exp/normalize is at
least `C3`, and per-lane replication is `C4`.

### 3.2 DSPB

State inventory:

- all four frozen expert states (`E0` current CAV, `E1` EWMA CAV, `E2` RG3,
  and `E3` signed-speed predictor), with sharing proved field by field;
- immutable pre-pose forecast snapshots and their source state/pose IDs;
- four residual/credit statistics, validity, age, and update epochs;
- EWMA coefficients, hysteresis/deadband, disagreement and tie state;
- winner ID, next-epoch publication, clear/unlock state, and fallback reason;
- expert-engine scheduling queues or holding state, including all ports;
- every event-lane winner/fallback mux and pipeline sideband.

The state worksheet is:

```text
sum_e(max-live expert state[e])
+ sum_e(residual/credit/valid/epoch state[e])
+ immutable forecast snapshots
+ winner/hysteresis/disagreement/publication/fallback state
+ scheduler and port-conflict state
+ pipeline_bits
- proved physically identical shared fields (listed individually)
```

The subtraction is forbidden for merely similar values or values requiring an
extra copy/port to meet the schedule.

Operator inventory includes every expert, residual quaternion/error measure,
score norm, EWMA update, compare/arg-min tree, hysteresis/disagreement logic,
atomic winner publication, and event winner/fallback mux. Time multiplexing
reduces quantity only when the frozen minimum pose spacing closes the complete
four-expert worst-case schedule. Otherwise count parallel engines and apply
the `C2-R<n>` modifier.

Critical-depth review covers both the pose-domain residual-to-winner chain and
the event-domain winner/version/fallback mux. Numeric review must falsify
credit poisoning, invalid-pose update, stale snapshot hindcast, score overflow,
tie oscillation, EWMA rounding freeze, epoch wrap, disagreement unlock, and
dropout reacquisition.

Provisional classification: `N3`, with the highest state/port/replication risk
of the three candidates; `S?`, `C?`, and `P?` remain unassigned.

### 3.3 SO3-PLL

State inventory:

- published angular-rate and integral/accumulator vectors;
- last authoritative pose, measurement timestamp, and pre-pose forecast state;
- proportional/integral gains, residual and clamp bounds;
- lock count, gap/near-pi/phase-jump/limit-cycle guards, valid and fallback
  state;
- pending correction plus atomic old/new publication versions;
- saturation/windup state and all event-lane pipeline sidebands.

The state worksheet shall include at least:

```text
3*Wv + 3*Wr                  rate and integral/accumulator
+ 4*Wq + Wt                  pose/timestamp anchor
+ forecast snapshot and pending correction
+ gains/bounds/lock/guard/version/fallback state
+ atomic publication copy when old and new versions coexist
+ pipeline_bits
```

Operator inventory includes prediction to the pose measurement timestamp,
residual quaternion and shortest-arc Log, proportional/integral multiplies,
accumulate/clamp/anti-windup logic, normalize, lock/guard comparisons, atomic
publication, current-CAV behavior while unlocked, and final fallback mux.

Critical-depth review must keep residual feedback off the same-edge event path;
events on the commit edge use the old published state. Numeric review must
falsify measurement-time versus commit-time anchoring, gain quantization,
integrator windup, fixed-point limit cycles, near-pi unlock, phase jump,
timestamp jitter/gap, invalid-pose no-update, and lock/relock behavior.

Provisional classification: `N3`; `S?`, `C?`, and `P?` remain unassigned. A
single fixed-schedule pose-rate residual engine may request `C2`; any feedback
nonlinear work placed on the event path is at least `C3`.

## 4. Common integration acceptance checklist

Every item is mandatory for `INTEGRATION_READY`. A checked item cites a frozen
artifact or executable test receipt; prose assertion alone is not evidence.

### Identity, causality, and state publication

- [ ] Candidate executable/config, numeric profile, neutral input, selector,
      evaluator, stress bundle, and CNCP manifest digests are bound together.
- [ ] Predictor input excludes sequence/window/block/query/event IDs, roles,
      ranks, archive/source digests, absolute query starts, labels, and scorer
      fields.
- [ ] Only poses with `commit_cycle < decision_edge` and measurement timestamp
      no later than event occurrence are visible.
- [ ] Same-edge pose updates are invisible; equal-timestamp clusters consume
      one immutable predictor-state version.
- [ ] Feedback updates only future state and replay proves earlier sealed event
      decisions byte-identical.
- [ ] Recording reset, 50 ms screen pre-roll, and single-reset chronological
      full-stream behavior match the Stage12 contract.

### Event semantics and fallback

- [ ] `S`, `A`, and candidate arms consume identical ordered `Q`; no candidate-
      specific history shortage removes a query.
- [ ] Two event lanes accept at `II_event=1` with fixed declared latency and no
      policy-added buffering, deletion, duplication, reorder, or retry.
- [ ] Candidate failure follows the exact frozen chain: current CAV, then fresh
      ZOH only under its frozen 1 ms rule, then sensor-fixed bypass.
- [ ] Baseline-model fallback is distinguished from candidate use, and every
      fallback reason remains in all denominators.
- [ ] Invalid pose, zero/negative `dt`, overflow, saturation, near-pi, gap,
      disagreement, unlock, and insufficient history fail closed.

### Resource and schedule completeness

- [ ] Both `CNCP_delta_A` and `CNCP_total_endpoint` balance exactly.
- [ ] Every live state field has width, signedness, reset, lifetime, storage
      class, and ownership; pipeline bits are included in `B_ff` once.
- [ ] Every array includes depth, width, copies, ports, latency, and conflict
      policy; replication is charged.
- [ ] Every operator includes width, quantity, domain, II, latency, sharing,
      and replication; Log/Exp/normalize are not collapsed into adds.
- [ ] `D_pose` and `D_event` include control, validity, version, winner, and
      fallback dependencies.
- [ ] The worst-case pose schedule closes against a frozen minimum commit
      spacing without borrowing nominal dataset idle cycles.
- [ ] Event pipeline registers, sidebands, lane replication, fixed latency,
      maximum wire width, and maximum fanout are reported.
- [ ] Candidate-added storage is classified `S0..S3`, compute `C0..C4` plus any
      replication modifier, pipeline `P0..P3`, and numeric risk `N0..N3`.

### Candidate-specific integration

- [ ] RG3 transports all rates/accelerations into one declared tangent frame,
      uses unequal cadence correctly, and bounds every acceleration/gate path.
- [ ] DSPB has exactly four bound experts, immutable pre-pose snapshots,
      past-only credit, next-epoch winner publication, and invalid-pose
      no-credit behavior.
- [ ] SO3-PLL anchors at measurement time, preserves same-edge old state,
      starts unlocked, and clears/resets on every frozen guard condition.
- [ ] Candidate-specific falsifiers in Section 3 pass without changing the
      model ID, widths, thresholds, fallback, or numeric policy.

### Claim discipline

- [ ] Logical estimates are not labeled synthesis, area, timing, power, PPA,
      or P&R evidence.
- [ ] Model accuracy is not used to waive a resource, causality, fallback,
      numeric, or integration failure.
- [ ] Hardware cost is not folded into predictor loss or used to rewrite the
      scientific endpoint.
- [ ] No external sequence or result is needed or accessed for this review.

## 5. Promotion, HOLD, and STOP gates

Verdicts are ordered and noncompensatory.

### Gate C0 — `CNCP_COMPLETE`

Requires an exact candidate ID and complete state, memory, operator, schedule,
depth, pipeline, wire, and numeric manifests. Any `TBD`, hidden shared cost, or
unbound width is `HOLD_C0`. Arithmetic inconsistency, deliberate omission, or
classifying a replicated resource as free is `STOP_C0`.

### Gate C1 — `MODEL_INTEGRATION_READY`

Requires all common and candidate-specific checklist items, exact fallback,
immutable-past causality, exact-once two-lane event semantics, and deterministic
replay. Any future/same-edge access, hindcast, changed `Q`, silent fallback,
event loss/reorder/duplicate, retroactive state change, or outcome-aware gate
is `STOP_C1`. Missing evidence is `HOLD_C1`.

Passing C1 only permits candidate-neutral software/bit-true model comparison on
consumed development inputs. It is not an accuracy GO and not an RTL GO.

### Gate C2 — `PRE_RTL_FEASIBILITY_CANDIDATE`

Requires final classes no worse than `S2/C2/P2`, `L=2`, `II_event=1`, fixed
latency, no uncharged or variable-latency path, a closed worst-case pose
schedule, and no event-rate nonlinear addition beyond the frozen endpoint.
`S3`, `C2-R<n>`, `C3`, `C4`, or `P3` is `HOLD_C2` pending a separately approved
budget or architecture change; it cannot be hidden by better accuracy.

Because RG3, DSPB, and SO3-PLL begin at `N3`, C2 does not authorize RTL. It
only permits a bounded bit-true feasibility study after the model winner is
selected under Stage12.

### Gate C3 — `NUMERIC_PROFILE_ACCEPTED`

Requires a frozen fixed-iteration profile at `N2` or safer, analytic range
proofs, bit-true error bounds, deterministic saturation/rounding, no unhandled
non-finite state, and exact fallback for every mutation. Wrap, unbounded
iteration, limit-cycle escape, accumulator windup, frame ambiguity, or failure
to reproduce the frozen software decision semantics is `STOP_C3`. Incomplete
precision evidence is `HOLD_C3`.

### Gate C4 — future `GO_TO_RTL_REVIEW`

C4 can be considered only for the sole Stage12 model winner after C0–C3 pass
and a separate user-approved RTL task exists. This document cannot issue C4.
The later review must still charge clock/reset, CDC, scan/test, physical memory
realization, buffering, floorplan, and endpoint integration. No logical CNCP
estimate can be promoted into a PPA claim.

## 6. Immediate candidate dispositions

| Candidate | Current CNCP disposition | Integration disposition | Reason |
|---|---|---|---|
| RG3-CAV | `CNCP_INCOMPLETE`, provisional `N3` | `PRE_RTL_HOLD` | widths, engine schedule, pipeline, and bounded acceleration profile do not yet exist |
| DSPB | `CNCP_INCOMPLETE`, provisional `N3` | `PRE_RTL_HOLD` | four-expert state/ports, sharing schedule, winner path, and bounded adaptive credit are not yet fixed |
| SO3-PLL | `CNCP_INCOMPLETE`, provisional `N3` | `PRE_RTL_HOLD` | gain/accumulator widths, atomic publication schedule, limit-cycle proof, and pipeline are not yet fixed |

These HOLDs are expected at a no-implementation checkpoint. They do not rank
model quality, reject future bit-true work, or imply that one candidate has
better area, power, timing, or PPA. A future candidate version must satisfy the
same accounting and checklist without candidate-specific exemptions.
