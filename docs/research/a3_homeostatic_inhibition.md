# A3 Bio-inspired Homeostatic Inhibition Arbiter

Status: second-pass RTL and counterevidence record, 2026-08-07

## 1. Research question and boundary

A3 asks whether a small, synchronous, synthesizable excitation/inhibition
network can stabilize AER arbitration as offered activity changes.  It is not a
renamed round-robin, age, or deficit scheduler.  Every source owns a leaky
membrane state; a grant resets the winner and inhibits the still-competing
population; a slow global state changes excitation, inhibition, and the
competition threshold.  A rotating origin is used only when multiple sources
are in the same protected class or have exactly equal membrane state.  Source
number is never added to a score.

The frozen logical event, one-entry source latch, accepted-event conservation,
always-ready measurement window, and scoreboard semantics remain unchanged.
The candidate has one registered retire lane and owns that storage.  Its TB
binding is combinational and contains no retry, queue, history, or arbitration.

## 2. Primary-paper basis and exact adaptation

The biology is an engineering motif, not a claim that the RTL is a faithful
neural model.

| Primary source | Observation used | A3 borrowing and deliberate modification |
| --- | --- | --- |
| Turrigiano et al., 1998, [Activity-dependent scaling of quantal amplitude in neocortical neurons](https://pubmed.ncbi.nlm.nih.gov/9495341/), DOI 10.1038/36103 | A neuron's synaptic input strengths scale up or down as a function of sustained activity. | A single global activity integrator changes all excitation gains symmetrically.  The biological hours-to-days process becomes a saturating cycle-level controller; no learned per-address weight is used. |
| Hartline and Ratliff, 1958, [Spatial summation of inhibitory influences in the eye of Limulus](https://pmc.ncbi.nlm.nih.gov/articles/PMC2194856/), DOI 10.1085/jgp.41.5.1049 | Competing receptor responses can be modeled by additive, bounded nonnegative inhibitory influence. | A grant emits one bounded inhibitory pulse to every other currently requesting competitor.  A3 uses a uniform competition neighborhood, rather than physical address adjacency, so affine relabeling cannot change a coupling constant. |
| Lapicque 1907, English translation by Brunel and van Rossum, [Quantitative investigations of electrical nerve excitation treated as polarization](https://doi.org/10.1007/s00422-007-0189-6) | An integrating membrane with leakage, threshold firing, and reset captures excitation over time. | Continuous voltage becomes an unsigned saturating integer.  A request supplies excitation, inactivity leaks, a grant is the spike/reset, and a protected threshold creates a bounded fair competition class. |
| Nessler, Pfeiffer, Buesing, and Maass, 2005, [Spiking inputs to a winner-take-all network](https://papers.neurips.cc/paper_files/paper/2005/hash/881c6efa917cff1c97a74e03e15f43e8-Abstract.html) | Integrate-and-fire units with inhibition can perform winner-take-all competition on spiking inputs. | A3 uses a deterministic single-winner maximum/protected selection; stochastic spiking and learned synapses are removed for reproducibility and synthesis. |
| Boahen, 2000, [Point-to-point connectivity between neuromorphic chips using address events](https://doi.org/10.1109/82.842110) | AER shares a random-access time-multiplexed channel; arbitration, queue occupancy, latency, locality, and throughput are coupled. | A3 keeps the common one-link AER transport boundary and changes only the arbitration dynamics.  It does not borrow row/column decomposition, parallel readout, or a hierarchical tree. |
| Demers, Keshav, and Shenker, 1989, [Analysis and simulation of a fair queueing algorithm](https://dl.acm.org/doi/10.1145/75246.75248) | Scheduling claims need an explicit service model and bounded departure from fair service, rather than an informal fairness label. | A3 states a service-opportunity starvation bound below.  It does not implement virtual finish times or packet fair queueing. |

The later DRR result of Shreedhar and Varghese ([DOI
10.1145/217382.217453](https://doi.org/10.1145/217382.217453)) is consulted as a
hardware-fairness control: A3 does not keep byte deficits, quanta, or per-flow
round lists.  This distinction is important because a saturating urgency counter
plus a biological name would not be a clean-slate contribution.

## 3. Fixed-point state and update equations

Default N=16 state is:

| State | Bits | Reset | Meaning |
| --- | ---: | ---: | --- |
| `u[i]` | 6/source (96 total) | 0 | unsigned membrane/urgency, range 0..63 |
| `h` | 4 global | 0 | homeostatic activity, range 0..15 |
| `phase` | 4 global | 0 | rotating origin used only within an equal/protected class |
| retire valid/source/address | 1+4+16 | 0 | candidate-owned one-entry output register |

Let `r_i(t)` be source-valid, `w(t)` the accepted winner when the output slot is
available, and `A(t)=sum_i r_i(t)`.  All additions and subtractions below are
unsigned saturating operations.  The default constants are:

```text
U_MAX=63, H_MAX=15, L=1, H_LOW=2, H_HIGH=4
G(h)=6-(h>>3)                 # 6 at h=0..7; 5 at h=8..15
I(h)=1+(h>>3)                 # 1 at h=0..7; 2 at h=8..15
Theta(h)=8+(h<<1)             # 8..38
```

The slow global controller is a hysteretic saturating integrator:

```text
             sat_H(h(t)+1),  A(t) > H_HIGH
h(t+1) =     sat_H(h(t)-1),  A(t) < H_LOW
             h(t),           otherwise
```

The per-source membrane update is:

```text
u_i(t+1) = 0                                      if i = w(t)
           sat_U(u_i(t) - L + G(h(t))
                 - I(h(t))*grant(t))              if r_i(t) and i != w(t)
           sat_U(u_i(t) - L)                      if not r_i(t)
```

Thus an accepted source spikes and resets.  Every other active competitor sees
one bounded lateral-inhibition pulse.  Inhibition never depends on coordinate,
rank, row, column, or a source-specific constant.  Under high global activity,
homeostasis reduces excitation, increases inhibition, and raises the protected
threshold; recent hotspot winners repeatedly reset and cannot remain in the
protected class, while continuously deprived sources still make guaranteed
positive progress.

Selection is work-conserving:

1. if any requester has `u[i] >= Theta(h)`, scan cyclically from `phase` and
   choose the first such protected requester;
2. otherwise choose the maximum `u[i]`; exact maxima are scanned cyclically
   from `phase`;
3. after an acceptance, set `phase` to the successor of the winner.

No grant is withheld merely because no source crosses the threshold.  A newly
arrived sole request therefore has no artificial integrate-to-threshold delay.
The rotating origin resolves symmetry; it is not the policy's accumulated
state and source indices never contribute to membrane value.

## 4. Fairness invariant and bound

Assumptions: reset is released, a source keeps `valid` asserted, the sink
provides an output-slot service opportunity, parameters obey
`G_min > L + I_max`, and N is finite.  With the defaults,

```text
Delta_min = G_min - L - I_max = 5 - 1 - 2 = 2.
```

Every nonwinning persistent requester therefore gains at least two membrane
units per service opportunity, even at maximum homeostatic inhibition.  It
enters the protected set within `ceil(Theta_max/Delta_min)=ceil(38/2)=19`
nonwinning opportunities.  Protected selection advances its cyclic origin past
each selected source, and a selected source resets below threshold.  A
persistent protected source is consequently selected within at most N further
service opportunities.  The default analytical starvation bound is:

```text
B_service <= ceil(Theta_max / Delta_min) + N = 19 + 16 = 35.
```

The RTL elaborates a fatal parameter check if positive progress is violated.
The bound is in available service opportunities, not wall-clock cycles under
sink backpressure.  The frozen suite is sink-always-ready, where the registered
one-lane candidate exposes one opportunity per cycle after reset.  Unit tests
must also search adversarial request patterns and assert the 35-opportunity
bound; empirical results do not replace the invariant.

## 5. Stability and limit-cycle risks

- `h` can chatter if active count sits on a single boundary.  Separate low/high
  thresholds create a hold band at 2..4 active sources.
- Under permanent global fan-in, `h` intentionally saturates high and the
  protected set becomes a finite rotating limit cycle.  This is a safe service
  orbit, not starvation; each winner resets and every source is revisited.
- If `I_max + L >= G_min`, a persistently inhibited membrane can pin at zero and
  the proof fails.  Such parameter sets are illegal, even if a finite random
  simulation looks fair.
- A too-low threshold collapses high-load behavior toward cyclic service and
  loses demand sensitivity.  A too-high threshold increases elephant/mouse
  tail wait.  The stability sweep therefore varies `U_W`, threshold base/slope,
  gain, and inhibition while enforcing positive progress.
- Saturation can create many equal maxima.  The rotating origin makes that
  limit cycle permutation-neutral; fixed-low-index tie-breaking is forbidden.
- Homeostasis reacts to pending request population, not completed throughput.
  This prevents output stalls from being misread as low offered activity, but
  means backpressure behavior needs a separate capability study.

## 6. Expected PPA and activity cost

For N=16 the policy state is 104 bits before the required output register.  The
combinational path contains a 16-input population count, threshold/gain decode,
16 protected comparisons, and a max plus cyclic-select network.  Per-source
updates use small saturating add/subtract logic.  There is no event FIFO, codec,
predictor, tree/quadtree partition, multi-grant prefix, calendar bucket, or token
network.

The likely costs versus a minimal flat arbiter are the membrane flops and the
wide compare/select network.  Clock-enable inference on unchanged inactive
membranes and on the hysteresis hold band is expected to reduce switching, but
no power win is claimed before common VCD/SAIF evidence.  Pre-PPA reporting uses
state bit toggles per stimulus cycle and per delivered event as a power proxy.
Server Genus/Innovus runs are prohibited until head approval.

## 7. Non-overlap with the other clean-slate tracks

- not A2: no dual path or mode-selected transport;
- not A4: no spatial quadtree or hierarchical partition;
- not A5: no predictor, history table, or speculative grant;
- not A6: no address codec/compression mechanism;
- not A7: one grant/retire lane, no prefix multi-grant;
- not A8: no calendar or age buckets;
- not A9: no distributed token fabric.

The only rotating state is a symmetry-safe tie origin.  Removing membrane,
inhibition, and `h` would change decisions and destroy the derived bound; thus
rotation is not the core mechanism.

## 8. Required evaluation and honest decision rule

All 46 frozen N=16 JSONL traces and their golden SHA identities are required.
The report separates rotating-victim, elephant/mouse, moving-hotspot,
identity/affine and mirror controls, phase-transition, the full uniform load
sweep, rate shape/spatial controls, and sparse latency.  It reports generated,
accepted, delivered, overrun, fixed-window events/cycle, drain cycles, p50/p95/
p99 and maximum latency, demand-normalized acceptance/delivery fairness,
minimum source service ratio, demand-conditioned zero-service windows, request
wait/service gap, and state-toggle proxy.

Reject or substantially redesign A3 if any of the following occurs:

1. any accepted-event loss, duplicate, corruption, phantom, ordering error, or
   post-drain mismatch;
2. a parameter set violates positive membrane progress or the adversarial bound;
3. identity/affine/mirror pairs show an unexplained policy dependence on source
   numbering;
4. frozen sparse p95 latency materially regresses without a compensating
   bottleneck-family benefit;
5. moving-hotspot handoff or post-overload phase recovery shows persistent
   hysteresis/limit cycling;
6. fairness improves only by reducing fixed-window throughput or increasing
   source overrun enough to hide offered demand;
7. toggles and later approved PPA cost dominate the measured fairness/tail
   benefit; or
8. the result wins only one workload or one seed.  No aggregate victory is
   claimed from a single favorable trace.

## 9. Frozen N=16 measured results

Run date: 2026-08-07.  Candidate RTL identity: commit `2b70eb3`.  The run used
Verilator 5.032 with the frozen 46-run
`manifest.neutrality-n16.json`; generated trace SHA values matched the checked
golden fixture.  The common TB, trace generator, golden file, and A1/public
benchmark assets were not edited.  `scripts/run_a3_homeostatic_benchmark.sh`
reproduces the run and uses the repository aggregator with
`--fail-on-correctness`.

All 46 runs have `errors=0` and `accepted==delivered` after drain.  This is an
accepted-event correctness result, not a lossless-offered-traffic claim:
overload still causes source-latch overrun as required by the common model.

### 9.1 Uniform load and sparse latency

The throughput column is fixed-window completed event/cycle.  Overrun is summed
over the three frozen seeds; tail/fairness columns show the seed range.

| Offered event/cycle | Mean throughput | Overrun, 3 seeds | p99 E2E cycles | Demand-normalized delivery fairness | Min source delivery ratio |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.125 | 0.120931 | 0 | 2 | 1.000000 | 1.000000 |
| 0.50 | 0.496582 | 0 | 2 | 1.000000 | 1.000000 |
| 0.90 | 0.903808 | 0 | 2 | 1.000000 | 1.000000 |
| 1.00 | 0.999512 | 0 | 2 | 1.000000 | 1.000000 |
| 1.25 | 0.999512 | 1509 | 8 | 0.998641..0.998937 | 0.754601..0.762821 |
| 1.50 | 0.999512 | 3021 | 11 | 0.997344..0.998943 | 0.617512..0.634146 |
| 2.00 | 0.999512 | 6119 | 13..14 | 0.997785..0.998989 | 0.465704..0.468504 |

The isolated sparse identity and rotate-180 controls are identical: 16/16
delivered, zero overrun, p95/p99 E2E 2 cycles, throughput 0.03125, and
demand-normalized fairness/min-source ratio 1.0.  Simultaneous N=16 fan-in has
p99/max E2E 17 cycles and maximum request wait 15, the expected serialization
tail of a one-lane design.  The saturation knee is therefore one logical event
per cycle; A3 does not claim a bandwidth improvement above one event/cycle.

### 9.2 Hotspots, victims, permutation, and burst shape

| Frozen control | Overrun | Throughput | p99 E2E | Worst request wait | DN delivery fairness | Min source ratio | Demand zero-window ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| elephant/mouse identity | 0 | 0.891602 | 2 | 0 | 1.000000 | 1.000000 | 0.017571 |
| elephant/mouse affine | 0 | 0.891602 | 2 | 0 | 1.000000 | 1.000000 | 0.017571 |
| rotating victim identity | 202 | 0.979736 | 6 | 5 | 0.999834 | 0.928315 | 0.002741 |
| rotating victim affine | 201 | 0.979980 | 6 | 6 | 0.999850 | 0.930314 | 0.002741 |
| moving hotspot single, two seeds | 0 | 0.888672..0.891602 | 2 | 0 | 1.000000 | 1.000000 | 0.017629..0.017676 |
| moving hotspot multi, 3 layouts | 0 | 0.888672 | 2 | 0 | 1.000000 | 1.000000 | 0.010922..0.011368 |

Elephant/mouse is below link capacity and presents no overlap after immediate
acceptance, so its perfect ratio is a weak test for this one-lane candidate; it
must not be used alone as a fairness victory.  Rotating victim is the meaningful
overloaded fairness result.  Identity/affine differences are small but nonzero
(one overrun, 0.000244 event/cycle, and 0.0020 min-ratio), consistent with an
initial phase transient; they are disclosed rather than rounded away.
Retrigger identity/affine are exactly equal (zero overrun, throughput 0.25,
p99 E2E 2), as are sparse identity/rotate-180 and local/mirror controls.

Equal-mean burst shape exposes serialization rather than loss: `shape_b1/b4/b16`
all deliver 2048/2048 at 0.5 event/cycle with zero overrun and fairness 1.0, but
p99 E2E rises 2 -> 5 -> 17 cycles and maximum request wait rises 0 -> 3 -> 15.
Local, dispersed, and mirrored four-way bursts are also identical at throughput
0.75, p99 E2E 5, zero overrun, and fairness/min ratio 1.0.  This candidate has no
locality optimization and claims none.

### 9.3 Phase transition and timing tail

For phase-transition seeds 3501/3502, sparse and near-saturation p95 E2E remain
2 cycles.  During the 2.0-event/cycle overload phase, backlog peaks at 14,
accepted throughput is 1.0/cycle, p95 E2E is 12, and source overrun is 1017 in
each seed.  The post-overload sparse phase returns to p95 E2E 2; backlog reaches
zero before the explicit drain, giving `recovery_to_zero_cycles=0`.  One seed
has one post-sparse source overrun.  `recovery_lossless=false` refers to the
already-counted source overruns, not loss of accepted events.

Timing-pair seeds deliver 1254/1254 and 1265/1265 accepted events, with 5 and 14
source overruns.  Of 128 pairs, 126 and 124 are evaluable; 2 and 4 are dropped
because an endpoint overran at the source.  Pair timing-error p95 is 1 cycle,
p99 is 1/2 cycles, maximum is 2, and no accepted pair is censored.

### 9.4 Toggle proxy and counterexample search

The candidate-only VCD analyzer counts bit transitions only in the 96 membrane
bits, 4-bit `h`, 4-bit phase, and candidate-owned retire register.  It excludes
combinational temporaries and all common-TB state.  It is a switching proxy, not
power or energy:

| Workload/load | State toggles/cycle | State toggles/delivered event |
| --- | ---: | ---: |
| core sparse identity | 0.2246 | 7.1875 |
| uniform 0.125, 3-seed mean | 0.9279 | 7.6760 |
| uniform 0.50, 3-seed mean | 3.4834 | 7.0124 |
| uniform 0.90, 3-seed mean | 5.6421 | 6.2391 |
| uniform 1.00, 3-seed mean | 6.0007 | 6.0007 |
| uniform 1.25, 3-seed mean | 12.7889 | 12.7743 |
| uniform 1.50, 3-seed mean | 16.9209 | 16.8852 |
| uniform 2.00, 3-seed mean | 22.2700 | 22.1798 |

The sharp post-saturation increase is the cost of membrane/inhibition dynamics
and is a power-risk flag.  It is not hidden by reporting only sparse activity.

The stability sweep covers 108 parameter combinations and four deterministic
patterns (permanent fan-in, elephant/mouse, moving hotspot, and randomized
persistent-victim adversary) for 4096 cycles each: 432 rows total.  Of these,
360 satisfy positive progress and threshold bit-fit; none violates its computed
bound.  The other 72 are rejected before evidence because the proof assumptions
fail.  For the RTL defaults, the maximum observed victim wait is 16 under fan-in,
2 under elephant/mouse and moving hotspot, and 13 under the randomized adversary,
all below the analytical bound of 35.  Across all legal grid rows the largest
observed/bound ratio is 0.8571.  This search increases confidence but does not
replace the proof or constitute formal verification.

Reproducibility hashes for the generated aggregate, corrected state-only
activity table, and sweep table are respectively
`1666cba620aeb6a3ad27783124dbe33e1cd939ab237b90f399c7de4686d0e0b2`,
`6b8366d912ff774ce24b1e28c22965833897df416150a8e5e51b49959b8d6d07`, and
`1c833be50791f29f472d53d9e2460ac7fea22853e11a410923c606a9f3caf950`.

## 10. Current decision

A3 passes the frozen functional gate and shows strong demand-normalized service
balance in the overloaded victim and uniform families, with rapid post-overload
sparse-latency recovery.  It does not increase single-lane bandwidth, cannot
remove common source-latch overrun above capacity, and its high-load state
switching rises substantially.  There is not yet an approved same-condition
cross-candidate comparison or physical PPA result, so no overall win, area win,
power win, or final selection claim is made.  Server PPA was not run, per the
head-approval gate.

## 11. Second-pass toggle optimization and counterevidence

### 11.1 RTL/VCD hotspot decomposition

The first-pass corrected VCD counts for the three-seed uniform sweep decompose
as follows.  These are registered value bit transitions, not write attempts or
clock-tree power.

| Load | Membrane | `h` | phase | retire valid | retire address | retire source | Total | Membrane share |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.00 | 0 | 0 | 4,101 | 2 | 4,094 | 4,093 | 12,289 | 0.0% |
| 1.25 | 13,003 | 157 | 4,345 | 2 | 4,343 | 4,342 | 26,192 | 49.6% |
| 1.50 | 21,377 | 44 | 4,389 | 2 | 4,422 | 4,421 | 34,654 | 61.7% |
| 2.00 | 32,409 | 44 | 4,355 | 2 | 4,400 | 4,399 | 45,609 | 71.1% |

Below capacity, every request is normally selected while its membrane is still
zero, so the membrane does not change value.  Above capacity, nonwinners charge
and winners reset; this real value motion, rather than `h`, is the dominant
hotspot.  Address/source output toggles are required one-lane event transport
activity and were not relabeled as policy savings.

### 11.2 Exact update suppression and equivalence

The RTL now independently parameterizes:

- `ENABLE_EXACT_CLOCK_ENABLE`: write a membrane register only when its exact
  next value differs;
- `SUPPRESS_INACTIVE_UPDATE`: suppress zero-to-zero inactive leakage writes;
- `SUPPRESS_SATURATED_NOOP`: suppress active saturated-to-saturated writes.

These are local clock-enable/write-enable conditions around the original
per-cycle equations.  They do not introduce an epoch, bucket, queue, calendar,
or a second arbitration policy.  All are enabled in the normal RTL; setting all
three to zero reconstructs the original update style.

The self-checking equivalence test instantiates original, each isolated option,
and all-options variants on the same directed idle/fan-in/saturation/stall and
random sequence.  For 1,400 cycles it checks every cycle's ready/grant, retire
valid/address/source, all 16 membranes, `h`, and phase.  It passed with identical
states and grant sequence.  Membrane write attempts were:

| Variant | Write attempts | Reduction from original |
| --- | ---: | ---: |
| original | 22,400 | 0% |
| inactive suppression only | 20,031 | 10.58% |
| saturation suppression only | 20,943 | 6.50% |
| exact clock-enable only | 18,574 | 17.08% |
| all options | 18,574 | 17.08% |

Exact clock-enable subsumes the two special no-op cases in this stimulus.  As
required by equivalence, registered value-toggle counts are identical.  Thus a
claim that no-op suppression alone fixes the original VCD value-toggle hotspot
is false.  It can only reduce inferred data/clock-enable activity.  On the
optimized frozen RTL, the fraction of 16 membrane registers enabled per sampled
cycle is 0 at uniform load <=1.0, 19.7% at 1.25, 32.3% at 1.5, and 48.9% at
2.0.  Physical clock-tree/power benefit remains unproved without approved PPA.

### 11.3 Fixed-point Pareto sweep

The frozen-trace model sweeps:

```text
URGENCY_WIDTH       = 5, 6, 7
THRESHOLD_BASE      = 4, 8, 12
THRESHOLD_SHIFT     = 0, 1
LEAK                = 0, 1, 2
INHIBIT_HIGH        = 1, 2, 3       (INHIBIT_LOW=max(0, high-1))
GAIN_HIGH           = 4, 5, 6       (GAIN_LOW=high+1)
```

Of 486 tuples, 345 meet positive progress and threshold bit-fit; 141 are
rejected analytically.  Eleven frozen traces cover sparse/simultaneous,
uniform 0.9/1.25/2.0, 16-way shape, moving hotspot, rotating-victim identity and
affine, phase transition, and elephant/mouse.  Every legal simulation asserts
generated-event conservation and observed wait <= its analytical bound.  The
front contains 41 parameter tuples; several are dynamically identical because
only the net inhibited increment differs in the update equation.

The model's chosen nonzero-leak point is:

```text
U=5, Theta(h)=4+h, L=1, I_low/high=1/2, G_low/high=6/5
```

| Metric over the 11-trace sweep | First-pass default | Chosen point |
| --- | ---: | ---: |
| analytical service bound | 35 | 26 |
| policy state bits | 104 | 88 |
| total source overrun | 3,945 | 3,943 |
| worst request wait | 15 | 15 |
| minimum DN fairness | 0.99778532 | 0.99778532 |
| minimum source ratio | 0.46666667 | 0.46666667 |
| model high-load state toggles/cycle | 10.338806 | 10.306946 |
| model all-trace state toggles/cycle | 8.739596 | 8.532163 |

The model's overrun, worst-wait, minimum fairness, and minimum ratio exactly
cross-check the selected-point RTL aggregate within 1e-6.  This guards against
choosing a model-only Pareto artifact.

### 11.4 Full 46-run RTL countercheck and shortlist decision

The chosen point passes all 46 frozen traces with `errors=0` and
`accepted==delivered`.  Relative to the first-pass default:

- all-run state-toggle sum falls 763,809 -> 756,632 (-0.940%);
- the high-load family sum falls 446,188 -> 445,459 (-0.163%);
- high-load membrane toggles fall 248,774 -> 248,372 (-0.162%);
- total overrun falls 13,106 -> 13,104;
- worst observed request wait remains 15 overall, but individual
  rotating-victim/uniform/phase rows regress by one or two cycles; and
- worst minimum-source ratio falls 0.465704 -> 0.464567.

The strict requested existence test is met: there is a point with lower measured
toggle, fewer state bits, and a valid (indeed smaller) bound.  Therefore A3 is
not rejected under the literal “no such point” rule.  However, full RTL evidence
shows that parameter tuning removes only 0.16% of high-load switching while
slightly moving some fairness tails in the wrong direction.  The major
high-load value-toggle risk is **not materially solved**.  The first-pass fixed
point remains the default; the 5-bit point is retained as a Pareto experiment,
not promoted silently.

Accordingly A3 is at most a conditional, low-priority shortlist candidate:
exact clock-enable is worthwhile for write activity, but A3 must not advance on
a claimed high-load power win without head-approved physical power evidence.
If the shortlist requires a material VCD reduction rather than merely a strict
numeric reduction, A3 should be rejected for this power risk.  No server PPA was
run in this second pass.

## 12. Third-pass control stability and counterevidence

This pass freezes the second-pass RTL and defaults.  It adds no calendar,
epoch, bucket, second path, predictor, codec, multi-grant, or token mechanism.
RR and fixed priority below are executable reference policies only; neither is
incorporated into A3.

### 12.1 Event-driven control law and safe region

Let `a_i[t]` denote a latched request, `q[t]` a successful A3 grant, and
`w[t]` its source.  With unsigned saturation `S_U(x)=min(U,max(0,x))`, the
implemented event-driven state equations are

```text
s(a) = +1, if sum(a_i) > A_high
       -1, if sum(a_i) < A_low
        0, otherwise

H[t+1] = S_H(H[t] + s(a[t]))
Theta(H) = Theta_0 + (H << k)

u_i[t+1] = 0                                             if q and i=w
             S_U(u_i + G(H) - L - q*I(H))               if a_i and i!=w
             S_U(u_i - L)                               otherwise
```

`G/I` select their low or high values from the MSB of `H`.  Arbitration first
chooses a requester with `u_i >= Theta(H)` from the rotating symmetry-neutral
phase, otherwise the maximum membrane, with the same phase resolving exact
ties.  A grant advances phase to `w+1`.  Clock enables from pass two suppress
only exact no-op writes and do not change these equations.

For an always-requesting nonwinner on every grant opportunity, define

```text
d_min = min(G_low-L-I_low, G_high-L-I_high).
```

If `d_min>0` and `Theta_max<=U`, a victim reaches the protected population in
at most `ceil(Theta_max/d_min)` nonwinning grant opportunities.  Once protected,
positive progress keeps it protected and the rotating scan cannot cross it;
therefore it wins within at most another `N` opportunities.  The bound is

```text
B = ceil(Theta_max/d_min) + N.
```

For the frozen default, `d_min=min(4,2)=2`, `Theta_max=38`, `U=63`, hence
`B=35` at N=16 and `B=23` at N=4.  These are grant-opportunity bounds.  There
is no finite wall-clock bound under unbounded downstream backpressure, a limit
shared by every non-buffering arbiter.

The sufficient safe region is stronger than the RTL's generic parameter guard:
both low- and high-activity progress must be positive.  The current values meet
that condition, but arbitrary reuse of the module must not infer low-branch
safety solely from the existing high-branch `$fatal` check.

For a constant active count, `H` is especially simple.  It moves monotonically
to `H_max` above `A_high`, monotonically to zero below `A_low`, and is constant
inside the deadband.  Distance to the applicable rail is a decreasing Lyapunov
function, so a fixed active set cannot create an autonomous `H` oscillation.
The membrane vector is bounded by construction.  Permanent contention produces
a driven reset/charge rotation, an intentional bounded limit cycle rather than
divergence.

### 12.2 N=4 exhaustive and token-identity checks

`control_stability.py` performs two complementary N=4 searches.  The first
merges identical reachable states while enumerating all 16 occurrence masks for
eight cycles.  Its state includes membranes, `H`, phase, all four one-entry
source latches, and the registered retire source.  The exact numeric defaults
reach 188 states over 13,232 transitions.  Because `active_count>4` is
unreachable at N=4, a second run scales only the feedback activity thresholds
to `A_low/A_high=1/2`; this exercises both feedback directions and reaches
1,122 states over 41,200 transitions.  This scaling is verification stimulus,
not an RTL/default change.

Every transition asserts

```text
old outstanding + generated = delivered + overrun + new outstanding,
grant implies a pending token,
0 <= u_i <= 63, 0 <= H <= 15, and 0 <= phase < 4.
```

All 1,310 states drain in at most four cycles with zero new arrivals.  The
search observes the membrane saturation boundaries 110,338 times.  A separate
token-ID search enumerates all 1,048,576 length-five occurrence sequences
(1,118,480 transitions), asserting one live location per token, no phantom or
duplicate retire, and strictly increasing delivered token IDs per source.

The starvation search starts from 2,048 hostile boundary states: all requesters
pending, every phase, `H` at either rail, and every membrane independently at
zero, just below/at maximum threshold, or saturation.  It then enumerates every
nonvictim refill choice.  Source zero is served by opportunity five in the
worst explored path, below the N=4 proof bound of 23.  This finite search does
not replace the proof; it checks the update and boundary cases used by it.

### 12.3 Oscillation and protection-lockout regions

The following regions are rejected or carry explicit recovery risk:

- `d_min<=0`: an inhibited persistent nonwinner need not approach threshold,
  so the homeostatic protected channel can lock out and `B` is undefined.  The
  max-membrane/tie fallback happened to keep max wait at 15 in the bounded
  correlated test, but that empirical fallback is not the homeostatic proof.
- `Theta_max>U`: high-`H` protection is unreachable.  Both this case and the
  nonpositive high-branch progress case are blocked by current RTL elaboration.
- `L=0`: inactive membranes retain stale history, so one-source recovery has no
  finite membrane-decay bound.  It is arithmetically legal but is outside the
  recovery-safe region; correlated-test switching rose from 16.550781 to
  25.054688 policy-state bit toggles/cycle.
- excessive feedback slope such that `Theta_max>U`, or reversed activity
  thresholds, is illegal.  The RTL blocks both bit-fit failure and reversed
  thresholds.
- any periodic offered load that repeatedly crosses both activity thresholds
  can force an `H` limit cycle.  This is input-driven, not autonomous.  In the
  8-cycle synchronized overload/24-cycle quiet test, default `H` traverses
  1..15 and reverses direction 31 times.  Thus A3 is bounded but does not filter
  correlated oscillation; the resulting state switching is real.

Current defaults are therefore inside the arithmetic and starvation-safe
region (`d_min=2`, representable threshold, positive leak, ordered deadband).
They are not inside a no-forced-oscillation region: this instantaneous update
law follows a sufficiently strong periodic input unless extra damping is added.
Time bucketing is intentionally not introduced here.

### 12.4 A3 versus RR and fixed priority

Each 512-cycle test uses the same one-entry source-latch and one-grant/cycle
transport.  Jain fairness is computed over per-source `served/generated`
ratios, so source overrun is included.  Settling is the first 32-cycle window
whose demand-normalized Jain index is at least 0.90 for eight consecutive
windows; `--` means the criterion is never met.  Toggle counts include only
policy state (A3 membranes/`H`/phase or the RR pointer), excluding common
transport.

| Workload | Policy | Overrun | Jain DN | Max wait | Settle | Policy toggles/cycle |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| asymmetric rate step | A3 | 491 | 0.926588 | 15 | 53 | 14.898438 |
|  | RR | 491 | 0.926588 | 15 | 53 | 1.779297 |
|  | fixed | 477 | 0.765796 | 15 | -- | 0 |
| rotating burst | A3 | 244 | 0.999841 | 2 | 40 | 3.212891 |
|  | RR | 244 | 0.999639 | 2 | 40 | 1.634766 |
|  | fixed | 244 | 0.968766 | 64 | 463 | 0 |
| correlated oscillation | A3 | 1,680 | 1.000000 | 15 | -- | 16.550781 |
|  | RR | 1,680 | 1.000000 | 15 | -- | 1.347656 |
|  | fixed | 1,680 | 0.418513 | 22 | -- | 0 |
| one-source recovery | A3 | 1,908 | 0.502883 | 15 | 32 | 8.580078 |
|  | RR | 1,908 | 0.502883 | 15 | 32 | 0.525391 |
|  | fixed | 1,905 | 0.077935 | 147 | 32 | 0 |

The one-source recovery fairness number includes the preceding all-source
overload phase; post-step service settling is 32 cycles, while A3's `H` itself
returns to zero in 29 cycles.  In correlated oscillation, no policy meets the
settling definition before the next forced phase; A3 and RR nevertheless have
perfect full-trace demand-normalized Jain fairness.  Fixed priority's three
fewer overruns in recovery are not a fairness win: it retains old low-priority
requests for up to 147 cycles while favoring source zero.

A3 matches RR's fairness, wait, and settling on the asymmetric and recovery
tests, improves rotating-burst Jain by only 0.000202, and uses about 1.97x to
12.28x RR policy-state toggles/cycle across these tests.  This is evidence of
bounded and stable control, but it falsifies a practical advantage over the
much smaller reference scheduler on this N=16 adversarial set.

### 12.5 Reproduction and decision

Run:

```sh
python3 tests/a3_homeostatic_inhibition/control_stability.py \
  --output-dir /tmp/a3-phase3
```

The exhaustive JSON, comparison CSV, and parameter-region CSV SHA-256 hashes
are respectively `9209d4f4cfa73849a52cbd815cb3d6c48fa6deaccb0835f10fdb3c83485333cd`,
`7a7d5368cd63ced51495b558c71e524fededdbea4e2a85a603d36be62f022f9b`,
and `aa9f3f42a4d63a6262ecdc3b9eae2e48e1d26268ee10d18799be1fb967045bf8`.

**Decision: reject A3 from the implementation shortlist.**  The frozen RTL is
functionally bounded and its current parameters are in the derived safe region,
so the rejection is not for loss, order, saturation, or a starvation violation.
It is rejected because the third-pass controls find no material fairness,
max-wait, or settling benefit over RR while confirming substantially higher
high-activity state switching and a large forced `H` limit cycle.  The A3 RTL
and research remain useful as a negative bio-homeostatic result; they should
not be promoted as a power-competitive candidate.  No common files, frozen
traces, server runs, or server PPA were modified or invoked in this pass.

## 13. Fourth-pass salvage hypothesis: global refractory WTA

This section does not revise or soften the Section 12 rejection.  It defines a
separate, deliberately cheaper experiment on the same biological competition
axis.  The original membrane candidate remains rejected regardless of the
salvage outcome.

### 13.1 Primary research basis and what is changed

- Lazzaro, Ryckebusch, Mahowald, and Mead's fabricated
  [O(N) WTA circuit](https://papers.nips.cc/paper/1988/file/a8f15eda80c50adb0e71943adc8015cf-Paper.pdf)
  uses a shared global inhibition signal and local competition.  The salvage
  borrows only the global winner/inhibition abstraction.  Binary requests have
  no analog magnitude, so equal inputs use an explicit deterministic encoder.
- Douglas and Liu's
  [spiking WTA analysis](https://doi.org/10.1162/neco.2009.07-08-829)
  treats recurrent WTA as a decision element and explicitly connects it to
  neuromorphic address-event communication.  The salvage borrows persistent
  winner identity, but replaces recurrent analog excitation with one stored
  `last_winner` ID.
- Shpiro, Curtu, Rinzel, and Rubin's
  [neuronal competition study](https://doi.org/10.1152/jn.00604.2006)
  compares WTA, release, and escape behavior with slow adaptation.  Curtu et
  al. subsequently analyze
  [slow negative feedback and escape/release](https://doi.org/10.1137/070705842).
  The salvage discretizes the escape idea into a one-grant absolute refractory
  exclusion.  It does not claim to reproduce their continuous population model
  or its time constants.

The proposed state is only

```text
last_valid                 1 bit
last_winner                ceil(log2(N)) bits
refractory                 1 bit
```

There is no per-source membrane, age, deficit, quota, epoch, bucket, queue, or
prediction state.  Let `R` be the request mask and `onehot(last)` the stored
winner bit.  The combinational law is

```text
A = R & ~onehot(last_winner)
escape = last_valid & refractory & R[last_winner] & (A != 0)
eligible = escape ? A : R
winner = fixed_first(eligible)
```

After a successful grant, `last_winner=winner`, `last_valid=1`, and
`refractory=1`.  An idle grant opportunity clears `refractory`; downstream
stall holds all state and output.  If the refractory source is the only active
requester it remains serviceable, preserving work conservation.  This is a
global one-winner refractory escape, not a programmable burst quota.  Extending
the refractory interval or preserving a winner for K grants would become a
quantum/quota scheduler and is outside this experiment.

### 13.2 RR-renaming test and predicted failure mode

This structure is not cyclic RR: with N=4 permanent requests, RR grants
`0,1,2,3,...`, while the refractory WTA predicts `0,1,0,1,...`.  Its fixed
encoder is not rotated by `last_winner`; the stored ID only removes one
temporarily inhibited competitor.  Exhaustive grant-sequence comparison must
find such divergence before any originality claim is allowed.

That difference also predicts the central counterexample.  With three or more
persistent equal requesters, fixed WTA plus one-winner refractory can alternate
between the first two and give zero service to the rest.  Avoiding this without
per-source state requires using `last_winner+1` as a rotating scan origin,
which is structurally RR, or retaining a multi-winner history, which violates
the cheap-state premise.  The RTL and exhaustive search are retained to measure
this boundary rather than relabel it as fairness.

### 13.3 Cost hypothesis and rejection gates

At N=16 the policy state hypothesis is six bits, versus 104 bits for rejected
A3 and four bits for an RR pointer.  Combinational arbitration is one last-ID
decoder, an alternative mask, a fixed priority encoder, and an escape mux.  Its
logical depth should exceed fixed priority and may be comparable to or below a
rotate/priority/unrotate RR implementation; synthesis-independent operator
depth and state/toggle proxies will be reported, not called physical PPA.

The salvage is rejected if any of the following holds:

1. accepted-event loss, duplicate, phantom, source-order, or stall-hold failure;
2. zero-service persistent source or no finite persistent-contention max-wait;
3. exact RR grant-sequence equivalence, meaning the biological name adds no
   distinct policy;
4. no material state/toggle/depth advantage that compensates for measured
   fairness and latency loss versus RR; or
5. any benefit depends on one favorable address identity and disappears under
   the frozen affine/permutation controls.

Passing transport correctness alone is insufficient.  Sparse, persistent
contention, elephant/mouse, rotating victim, and asymmetric rate-step controls
must be compared against the same fixed and RR models before a keep decision.
