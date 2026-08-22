# MC-WTB Stage 2 architecture candidates — 2026-08-22

Status: **research shortlist only; implementation prohibited until the head
agent selects the Stage 3 candidate-model plan**

## 1. Prior-art boundary

SO(3) constant-angular-velocity event warping, event-specific time horizons,
constant-acceleration pose propagation, event-alignment motion estimation, and
multi-model filtering are established ideas. Gallego and Scaramuzza already
warp every event at its own time under an exponential pure-rotation model
([RA-L 2017](https://rpg.ifi.uzh.ch/docs/RAL16_Gallego.pdf)). Lie-EKF work has
compared constant-position, constant-velocity, and constant-acceleration models
for event-camera tracking, and event/IMU work predicts between slower upstream
updates ([BMVC 2020](https://www.bmva-archive.org.uk/bmvc/2020/assets/papers/0366.pdf),
[CVPRW 2023](https://openaccess.thecvf.com/content/CVPR2023W/EventVision/html/Chamorro_Event-IMU_Fusion_Strategies_for_Faster-Than-IMU_Estimation_Throughput_CVPRW_2023_paper.html)).
Contrast-maximization feedback is also mature
([CVPR 2018](https://openaccess.thecvf.com/content_cvpr_2018/html/Gallego_A_Unifying_Contrast_CVPR_2018_paper.html)).

Therefore none of the candidates below is presented as a novel motion model.
The defensible research opportunity is the bounded, strictly causal AER
hardware contract: supplied-pose authority, event-outcome-free and
pose-residual-only future adaptation,
immutable past decisions, exact fallback, exact-once event semantics, and
charged latency/PPA.

Acceleration novelty is especially crowded: recent work explicitly compares
CV, CA, jerk, and multiple acceleration segments for event-camera rotation
([Shi et al., Pattern Recognition 2026](https://www.sciencedirect.com/science/article/pii/S0031320326009118)).
Multi-model selection and feedback are also not new by themselves
([Blom and Bar-Shalom IMM](https://ieeexplore.ieee.org/abstract/document/1299),
[Benedict-Bordner alpha-beta filter](https://doi.org/10.1109/TAC.1962.1105477)).

## 2. Predictor shortlist

### P1 — RG3-CAV: residual-gated three-pose predictor

Use three committed active sensor-to-world poses `R0,R1,R2`, all visible before
the event edge, to estimate a bounded body-tangent acceleration. With
`R01=R0^T R1` and `R12=R1^T R2`, compute `w01=Log(R01)/dt01`,
`w12=Log(R12)/dt12`, transport `w01` into frame 1 by `R01^T w01`, form the
unequal-cadence acceleration using `0.5*(dt01+dt12)`, then transport both the
latest rate and acceleration through `R12^T` into frame 2 before extrapolating
from `R2`. This convention must be falsified by synthetic coupled-axis tests;
it may not be silently changed after outcome inspection. Apply acceleration
only when cadence, residual, direction, and magnitude gates are valid.

- Purpose: improve acceleration, stopping, and reversal intervals without a
  learned filter.
- Strength: smallest change and easiest ablation; fixed pose-rate state.
- Risk: second differences amplify pose/timestamp noise; SO(3) integration
  order and body/world convention matter; generic acceleration is prior art.
- Required ablations: acceleration disabled; transported versus deliberately
  untransported prior rate; clamp-only versus residual-only gates; reversal
  deadband removed; 2/3/4-pose history; acceleration horizon term removed;
  shared versus dedicated Log/Exp; unequal cadence, near-pi, fixed-point width,
  and subtraction-cancellation mutations. An adaptive bound must be a frozen
  causal formula, never an outcome-retuned threshold.
- Fallback: RG3 failure -> exact frozen current CAV -> fresh ZOH only when its
  age is at most 1 ms -> sensor-fixed bypass.
- Verdict: **MODEL-ONLY GO, NOVELTY LOW, RTL HOLD**.

### P2 — DSPB: delayed supplied-pose-residual predictor bank

The Stage 2 bank is exactly four experts: `E0` frozen current CAV, `E1` EWMA
body-angular-velocity CAV, `E2` bounded RG3-CAV, and `E3` a past-only
axis-coherent signed-speed predictor. A different count or composition is a
new candidate ID. At pose commit `k`, compare predictions made before `k` with
the newly committed authoritative pose. Each expert's immutable pre-pose state
defines a prediction function before `k` exists; when `k` commits, that frozen
function may be evaluated at `t_k` before any state update. Store the source
state/pose IDs and evaluation order so this cannot become a hindcast that
reconstructs a predictor after observing `k`. Update a past-only residual statistic,
then choose one model for the **next** epoch. Event loss, oracle routing, and
future pose are absent.

- Purpose: retain CAV in steady motion and select a higher-order model only
  when its past pose prediction has actually been better.
- Strength: separates model improvement from the later event-quality selector;
  model scoring runs in the sparse pose-rate domain.
- Risk: selection lags regime changes; all models may agree and be wrong; model
  banks/IMM are established prior art. An untrained bank, winner tie, invalid
  winner, excessive disagreement, or credit corruption clears/unlocks the bank
  and uses the common fallback chain. Invalid poses never update expert credit.
- Required ablations: every model alone, exact expert-composition alternatives,
  leave-one-out and current-CAV-removed banks, no hysteresis, instant versus
  EWMA residual, update every pose versus every two poses, fixed schedule,
  time-multiplexed versus parallel arithmetic, invalid-pose poisoning/recovery,
  winner-tie/disagreement, reset/pre-roll/reacquisition, winner-ID/state-epoch
  receipts, and a past-only **offline headroom diagnostic** that never enters
  runtime selection or promotion metrics.
- Fallback: DSPB failure -> exact frozen current CAV -> fresh ZOH only when its
  age is at most 1 ms -> sensor-fixed bypass.
- Verdict: **CONDITIONAL PRIMARY MODEL-ONLY GO; CO-DESIGN DIFFERENTIATION IS AN
  UNVERIFIED HYPOTHESIS REQUIRING CLAIM-CHART REVIEW; RTL HOLD**.

### P3 — SO3-PLL: supplied-pose residual feedback

Keep a persistent angular-rate state anchored at a pose's **measurement
timestamp**, not its later commit time. When a valid new pose commits, replay
an immutable pre-pose forecast-state version to that measurement timestamp,
compute the shortest-arc
SO(3) residual, and apply bounded fixed proportional/integral corrections
effective only after the commit edge. A same-edge event cannot see the update.
Invalid poses never update loop state. The loop begins unlocked and uses CAV
until the frozen lock count is met; near-pi residual, long gap, phase jump,
normalization failure, saturation, or limit-cycle guard clears the lock and
resets the predictor state.

- Purpose: reduce interval-to-interval velocity noise and gradually correct
  systematic predictor lag.
- Strength: smallest streaming state among adaptive candidates; feedback
  arithmetic can run off the event critical path, but an atomic published state
  version and same-edge priority are still required.
- Risk: gain tuning, lag under acceleration, pose jitter amplification,
  quantized limit cycles, and near-pi log ambiguity. SO(3) observers and alpha-
  beta/PLL filters are known.
- Required ablations: integral gain zero, proportional gain zero, exact
  current-CAV equivalent, lock gate removed, reset versus retained-unlocked
  state, fixed versus phase-error-dependent gain, one versus two correction
  iterations per pose, jitter/gap/phase-jump, invalid-pose no-update, near-pi
  unlock, initial acquisition/dropout reacquisition, fixed-point limit cycles,
  and a falsifier that incorrectly anchors at commit time.
- Fallback: PLL failure/unlocked state -> exact frozen current CAV -> fresh ZOH
  only when its age is at most 1 ms -> sensor-fixed bypass.
- Verdict: **SECONDARY MODEL-ONLY GO, ALGORITHM NOVELTY LOW, RTL HOLD**.

### P4 — event-residual rotational servo

Use strictly earlier tile-local event pairs to form small rotational sufficient
statistics and correct CAV angular velocity for future events.

All members of one equal-timestamp cluster consume the same old state. A
residual update derived from that cluster becomes visible only after the entire
cluster commits.

- Potential: feedback at event cadence between approximately 5 ms pose packets.
- Blockers: translation, depth, moving objects, aperture effects, hot pixels,
  low texture, and per-event state-update pressure can masquerade as rotation.
  Event-alignment angular-rate estimation is strong prior art.
- Verdict: **HOLD** until pose-only candidates and dynamic/6-DoF diagnostics are
  understood.

## 3. Hardware implementation policy

`Multi-horizon CAV` is not treated as an accuracy innovation. Continuous
event-time extrapolation already exists; discretizing it into horizon bins can
lose accuracy. It is retained as a later implementation technique:

```text
committed pose update
  -> shared Log/Exp/normalize engine
  -> precompute H=3..4 predicted quaternions
event path
  -> age class + mux
  -> two charged ray-rotation lanes
```

This can move nonlinear work out of the event-rate path. No 1 kHz upstream pose
interface is claimed: official UZH mocap is approximately 200 Hz, while the
actual on-chip supplied-pose cadence, arrival jitter, and upstream cost remain
unfrozen. Shared-engine scheduling must therefore use a later frozen minimum
commit interval rather than assume 154,000 free cycles. The candidate-neutral
pre-RTL cost vector is:

```text
(B_ff, B_sram, read_ports, write_ports, O_pose, O_event,
 II_event, critical_depth, pipeline_bits, max_wire_width, numeric_risk)
```

No candidate may hide nonlinear pipeline registers, 2R2W memory replication,
wide-pose fanout, fallback storage, or clock power inside the existing
108,799-bit logical envelope. The existing qualifier has only 45 nm
elaboration evidence, not mapped timing, area, power, or P&R.

## 4. Predictor-independent innovation candidates

These are recorded but are not applied to the predictor experiment.

| Rank | Candidate | Value | Prior-art/implementation verdict |
|---:|---|---|---|
| 1 | Pose-Epoch Transactional WTB | tentative world-tile bank with bounded pre-commit remap/rollback and exact RAW fallback | **GO to contract study**, not implementation |
| 2 | Dual-Domain Streaming Alignment Sketch | fixed-size one-pass sensor/world occupancy, spread, and consistency statistics | **HOLD**; contrast/alignment overlap |
| 3 | Post-Warp Adaptive Vector AER | choose scalar/bitmap after world warp rather than in sensor rows | **HOLD**; row-vector/projected-stream prior art is strong and patent claim review is pending |
| 4 | Reversible Dual-Domain WTB | world representation plus exact sensor-event replay residual/escape | **HOLD**; overlaps existing G1 plan and lossless codecs |
| 5 | Certified Uncertainty-Envelope WTB | one logical event carries nominal world tile plus bounded angular region | **long-term HOLD**; needs a new set-valued metric |
| 6 | Layered Multi-Motion WTB | separate ego/raw/object/depth motion layers | **HOLD-STRONG / likely STOP** due prior art and state cost |

World-coordinate accumulation itself is old
([panoramic event tracking](https://arxiv.org/abs/1703.05161)); warped-event
images and volumes are established, and FPGA back-projection/ray accumulation
already exists ([Eventor](https://arxiv.org/abs/2203.15439)). Row/tile bitmap
AER and adaptive row-vector formats also have strong prior art. Accordingly,
the future novelty claim cannot be “world warp,” “bitmap,” “feedback,” or
“lossless compression” alone.

Relevant references include the [Prophesee EVT3 row-vector
format](https://docs.prophesee.ai/stable/data/encoding_formats/index.html),
[EP4642020A1 adaptive scalar/bitmask encoding](https://patents.google.com/patent/EP4642020A1/en),
and [US20220101006 projected/compensated event streams](https://patents.justia.com/patent/20220101006).
These are collision warnings, not an infringement or freedom-to-operate
opinion.

The most promising non-predictor question is instead:

> Can a bounded pose-epoch transaction preserve exact sensor-domain replay
> while validating and compacting a tentative world-domain stream, with every
> buffer, escape, rollback, latency, and 45 nm endpoint cost charged?

That question remains separate so a later transport improvement cannot be
misreported as predictor accuracy.

## 5. Stage 3 handoff recommendation

Implement no RTL yet. The next jointly reviewed step should build bit-true,
always-on software models for `RG3-CAV`, `DSPB`, and `SO3-PLL` against identical
consumed development events. First prove causal eligibility, ordered event
identity, exact fallback equivalence to the frozen CAV path, and MID/HIGH
incremental improvement. Predictor geometry may differ on valid candidate-use
events. Only the single stable winner
proceeds to an event-quality-selector experiment; only after that does multi-horizon
fixed-point/RTL mapping begin.

First screen every candidate on synthetic failures and the common 108 windows
with the frozen 50 ms causal pre-roll. At most the top two passing stateful
finalists then replay the full locked `shapes_rotation` recording
chronologically. Full-stream stability is noncompensatory: the higher-ranked
stable finalist wins, or the unchanged runner-up wins if the first fails. If
neither passes, STOP. This avoids spending a roughly 260-times-larger replay on
weak candidates. Full-sequence development still counts as one consumed scene;
it is not generalization evidence.

This is a technical prior-art screen, not a patent freedom-to-operate opinion.
