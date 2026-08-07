# A3 Bio-inspired Homeostatic Inhibition Arbiter

Status: pre-RTL design record, 2026-08-07

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
