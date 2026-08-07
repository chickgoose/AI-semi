# A5: Safe Speculative Pre-grant with Confidence Fallback

Status: research/design freeze, 2026-08-07

## 1. Scope and hypothesis

A5 predicts the **source identity of the next accepted logical event**.  It is
not a last-grant priority rule.  The predictor is a compact first-order
transition table: the previously accepted source is the context, and each
context stores a predicted successor plus confidence.  A confident hit bypasses
the general request scan and precomputes the source select.  A cold, low-
confidence, aliased, or wrong prediction uses a deterministic rotating fallback.

The hypothesis is deliberately narrow: correlated source streams (sparse
recurrence, elephant/mouse, and moving hotspots after adaptation) can remove the
request-vector scan from the fast grant/select path.  Prediction may change
latency or achievable clock period, but never which accepted events are
eventually delivered.  The frozen common source latches remain authoritative.

This candidate does not use occupancy dual-path routing, a homeostatic score,
spatial trees, codecs, K-lane compaction, age calendars, or distributed tokens.
It has one conventional output lane and one deterministic recovery arbiter.

## 2. Primary literature and borrowed scope

The implementation borrows mechanisms, not claimed results, from these primary
sources:

| Primary source | URL | Borrowed scope | Not borrowed |
| --- | --- | --- | --- |
| Yeh and Patt, “Two-Level Adaptive Training Branch Prediction,” MICRO 1991 | <https://doi.org/10.1145/123465.123475> | runtime history selects a table entry; correlation can outperform a context-free last outcome | instruction semantics, speculative execution, or published accuracy |
| McFarling, “Combining Branch Predictors,” WRL TN-36, 1993 | <https://shiftleft.com/mirrors/www.hpl.hp.com/techreports/Compaq-DEC/WRL-TN-36.html> | saturating confidence provides hysteresis so one anomaly does not immediately reverse policy | hybrid/meta-predictor and SPEC results |
| Lipasti, Wilkerson, and Shen, “Value Locality and Load Value Prediction,” ASPLOS 1996 | <https://doi.org/10.1145/237090.237173> | explicitly predict a multi-bit value (here, source ID), and gate use by confidence | load-value speculation or replay machinery |
| Stankovic and Milenkovic, “DRAM Controller with a Complete Predictor,” IEICE 2009 | <https://doi.org/10.1587/transinf.E92.D.584> | use request history to prepare the next resource action before the request arrives | analog timing predictor and DRAM row policy |
| Matsutani et al., “Prediction Router: Yet Another Low Latency On-Chip Router Architecture,” HPCA 2009 | <https://www.arc.ics.keio.ac.jp/~matutani/papers/matsutani_hpca2009.pdf> | predict the next channel and complete arbitration speculatively; a hit bypasses normal arbitration | NoC routing assumptions and reported network gains |
| Peh and Dally, “A Delay Model and Speculative Architecture for Pipelined Routers,” HPCA 2001 | <https://doi.org/10.1109/HPCA.2001.903263> | speculative allocation must validate before use; failure returns to the ordinary pipeline | virtual-channel allocation and router topology |

The CPU papers motivate correlation, multi-bit targets, and confidence.  The
memory-controller paper motivates preparing a resource decision from request
history.  The router papers are the closest structural precedent for moving
arbitration off the hit path while retaining a non-speculative recovery path.

## 3. Predictor state and bit budget

Let `N = NUM_SOURCES`, `S = ceil(log2(N))`, and confidence width `C = 2`.
There are `N` direct-mapped transition entries indexed by the previously
accepted source:

```text
entry[context] = {valid:1, target:S, confidence:C}
history        = {valid:1, last_accepted_source:S}
fallback_rr    = S bits
```

The predictor-only budget is `N * (1 + S + C) + 1 + S` bits.  The complete
A5 arbitration state adds the `S`-bit deterministic fallback pointer and the
ordinary one-event output register (`valid + S + ADDR_WIDTH`), which is
transport state rather than predictor state.

For the frozen `N=16`, `S=4`: predictor state is `16*(1+4+2)+1+4 = 117`
bits; including the fallback pointer it is 121 bits.  There are no tags because
the context space is exactly the source-ID space, so N=16 has no index aliasing.
If a future implementation uses fewer than N physical entries, modulo indexing
is permitted only with an explicit partial tag; a tag mismatch is cold/
low-confidence and cannot issue a speculative grant.

### Update rule

On each real source acceptance, `(previous_source -> accepted_source)` is the
training transition.  The entry is updated as follows:

- invalid entry: install target, set confidence to weak (`01`);
- target match: saturating increment toward `11`;
- target mismatch: saturating decrement; when already `00`, replace the target
  and return to weak confidence (`01`).

Prediction is usable only for a valid entry with confidence at least `10`.
Reset invalidates all entries and history.  Thus cold start cannot create a
grant.  An alternating `A,B,A,B,...` stream is learnable because entry A predicts
B and entry B predicts A; an adversarial `A,B,A,C,...` stream sharing context A
causes confidence hysteresis and then fallback, rather than silent loss.

## 4. Hit, miss, and recovery

An **opportunity** exists when at least one source is pending and the output
transport can accept a new event.  Definitions are tied to such opportunities:

- `prediction_attempt`: history entry is valid and confidence is at least `10`;
- `prediction_hit`: an attempted target is currently pending and is accepted;
- `prediction_miss`: an attempted target is not pending while at least one
  other source is pending;
- `confidence_fallback`: requests exist but no prediction is attempted;
- `idle`: no request exists and is excluded from accuracy.

Accuracy is `hits / (hits + misses)`, and coverage is
`(hits + misses) / non_idle_opportunities`.  Both counters must be reported;
accuracy without coverage can hide a predictor that almost never acts.

On a hit, the target source directly drives the grant and event-select mux.  If
the registered output slot is empty and the sink is ready, that validated event
also bypasses to the retire seam in the same cycle; otherwise it is captured in
the ordinary stable output register.  On a miss or confidence fallback, the
rotating deterministic arbiter selects the first pending source at or after
`fallback_rr`.  The chosen source is accepted only after checking its current
`source_valid`; no predicted payload is stored or fabricated.  Recovery is
same-cycle in the functional reference RTL.  A future timing-pipelined fallback
may defer acceptance by one cycle only if the common source latch remains
unacknowledged and stable, giving identical event conservation.

## 5. Correctness invariants

Prediction is a performance hint, never a correctness oracle:

1. `source_ready[s]` may assert only when `source_valid[s]` is observed and the
   output slot can preserve that exact `source_event[s]`.
2. At most one source is accepted per cycle and each acceptance creates exactly
   one output record.
3. An output record is held stable until `retire_valid && retire_ready`.
4. Predictor state contains no event payload and cannot synthesize a completion.
5. A miss cannot clear a source.  Only the selected, validated handshake clears
   the common source latch.
6. After injection stops, rotating fallback provides finite service independent
   of prediction and confidence.
7. Reset clears transport valid, history validity, entry validity, counters, and
   fallback state; no pre-reset prediction can produce a completion.

The mandatory gate remains `errors == 0` and, after drain,
`accepted == delivered`.  `generated = source_overrun + pending + accepted`
and source-local ordering remain common-TB checks.

## 6. Timing path before and after

Reference rotating arbitration hit-independent path:

```text
N request bits -> rotate/priority scan -> encode -> N:1 event mux
               -> output-register D / source_ready
```

A5 confident-hit path:

```text
registered history -> direct-mapped target/confidence read
                   -> target valid check -> indexed event select
                   -> same-cycle retire bypass or output-register D
                   -> onehot source_ready
```

Fallback still contains the full scan, but it is selected only on miss/cold.
The functional RTL uses same-cycle fallback to protect sparse latency; therefore
static timing tools may still report the fallback cone as the worst path.  A5
must not claim a frequency gain until identical synthesis/place-and-route shows
that path factoring or a one-cycle recovery pipeline removes it from the clock
limit.  Server PPA is intentionally not run without approval.

## 7. Adversarial and anti-overfit workloads

- cold one-shot sparse sources: confidence must remain gated and sparse latency
  must not regress;
- strict two-source alternation: distinguishes transition prediction from a
  last-winner preference and should converge to high accuracy;
- `A,B,A,C` ambiguity: same context has two successors, exercising confidence
  decay and deterministic recovery;
- round-robin across all 16 sources: exposes whether a compact transition table
  learns a longer cycle;
- phase flip/hotspot handoff every 128 cycles: measures adaptation delay and
  miss burst after movement;
- affine relabel pair: accuracy and event metrics must not depend on low numeric
  source IDs;
- elephant/mouse and rotating victim: fallback must preserve demand-normalized
  fairness and bounded drain despite a confident hot transition;
- rate-shape B1/B4/B16 and uniform overload sweep: accuracy must be accompanied
  by fixed-window throughput, p95/p99 latency, overrun, and fairness.

## 8. PPA break-even and rejection criteria

Let `A_p` be predictor area, `P_p` its dynamic+leakage power, `D_base` the
reference arbitration delay, `D_hit` the direct predicted-select delay, `h`
accuracy, `c` coverage, and `M` any miss penalty in cycles.  A useful design
requires all of:

```text
c*h*(D_base-D_hit) > predictor lookup/update timing overhead
event-rate benefit or achievable-frequency benefit > miss penalty c*(1-h)*M
energy saved in avoided full scans > P_p at the measured activity point
```

The candidate is rejected, or simplified to deterministic arbitration, if any
of these predeclared conditions holds:

- any correctness/conservation failure on the 46 frozen traces;
- a prediction can acknowledge an inactive source or fabricate/overwrite data;
- alternating, hotspot-handoff, or affine-relabel tests show unbounded service
  or material fairness regression;
- confidence-gated accuracy is below 70% on both moving-hotspot and
  elephant/mouse, or coverage is below 25% (accuracy alone is insufficient);
- fixed-window throughput, p99 latency, or overrun is worse by more than 2% on
  two or more mandatory workload families without a demonstrated Fmax gain;
- post-layout arbitration/Fmax does not improve by at least 10%, or total cell
  area grows above 5%, under the frozen physical contract;
- predictor activity increases workload-based energy/event with no throughput
  or latency-tail benefit.

## 9. Required report set

The final local report will include all 46 trace identities and SHA-bound common
metrics.  It will call out moving-hotspot, elephant/mouse, sparse, timing-pair,
rotating-victim, affine relabel, rate-shape, and every uniform load/seed.  For
each group it reports attempts, hits, misses, accuracy, coverage, fixed-window
throughput, p95/p99 end-to-end latency, source overrun, demand-normalized
delivery fairness, and correctness.  Predictor accuracy is supporting evidence,
not the outcome metric.

## 10. Local 46-trace result (2026-08-07)

The candidate-only runner replayed the unchanged manifest with local Verilator
5.032.  The manifest SHA-256 was
`77da0c02a1db2755653195790a1af43f82e4f1be27be2f9570d9014f648b9726`.
All 46 exact generated traces passed: every run had zero scoreboard errors and
`accepted == delivered` after drain.  These are cycle-level functional results,
not server PPA evidence.  No server synthesis, place-and-route, or power run was
started.

### Required event metrics and prediction behavior

| Frozen group | Predictor accuracy / coverage | Fixed-window throughput | p95 / p99 E2E latency | Source overrun | Demand-normalized delivery fairness |
| --- | ---: | ---: | ---: | ---: | ---: |
| core sparse identity + rotate180 | no attempt / 0% | 0.03125 | 2 / 2 | 0 | 1.000000 |
| moving hotspot, single (2 seeds) | 73.5% / 66.1% | 0.890137 mean | 2 / 2 | 0 | 1.000000 |
| moving hotspot, three multi-hot layouts | 20.6% / 10.6% | 0.888672 each | 2 / 2 | 0 | 1.000000 |
| elephant/mouse identity + affine | 79.5% / 78.7% | 0.891602 each | 2 / 2 | 0 | 1.000000 |
| rotating victim identity | 26.7% / 11.2% | 0.976074 | 6 / 7 | 216 | 0.999821 |
| rotating victim affine | 20.9% / 9.6% | 0.976807 | 5 / 7 | 214 | 0.999821 |
| timing pair, 2 seeds | 9.6% / 4.6% | 0.615234 mean | 3 / 4 | 17 | 0.999900 |
| rate-shape B1 | 100% / 98.4% | 0.500000 | 1 / 2 | 0 | 1.000000 |
| rate-shape B4 | 100% / 73.8% | 0.500000 | 5 / 5 | 0 | 1.000000 |
| rate-shape B16 | 100% / 73.8% | 0.500000 | 17 / 17 | 0 | 1.000000 |

The confident same-cycle bypass changes real event latency where idle output
slots coincide with a hit: mean E2E latency was 1.811713 cycles for both
elephant/mouse mappings, 1.869243 cycles for the two single-hotspot seeds, and
1.016113 cycles for rate-shape B1.  The tail remains governed by contention and
fallback, which is why moving-hotspot p99 stays at two cycles and B16 stays at
17 cycles.  Core sparse makes no prediction attempt because each context is
cold; its two-cycle latency is therefore neither improved nor harmed.

Timing-pair relation analysis found 253 evaluable pairs out of 256: three pairs
were dropped by source overrun, none were censored, mean pair-gap error was
0.452381/0.425197 cycles for seeds 3901/3902, p95 was two cycles for both, and
p99 was three/two cycles.  Thus low predictor accuracy in this workload did not
become a correctness failure, but it also produced no material tail win.

### Uniform sweep

| Declared load | Accuracy / coverage | Throughput | Mean E2E | p95 / p99 | Overrun | Delivery fairness |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.125 | 3.8% / 3.5% | 0.120931 | 1.998654 | 2 / 2 | 0 | 1.000000 |
| 0.50 | 3.8% / 3.5% | 0.496582 | 1.999017 | 2 / 2 | 0 | 1.000000 |
| 0.90 | 8.0% / 3.6% | 0.903808 | 1.999820 | 2 / 2 | 0 | 1.000000 |
| 1.00 | 8.8% / 3.5% | 0.999512 | 2.000000 | 2 / 2 | 0 | 1.000000 |
| 1.25 | 41.1% / 31.6% | 0.999512 | 5.155859 | 10 / 12 | 1507 | 0.998502 |
| 1.50 | 60.9% / 62.0% | 0.999512 | 7.177893 | 12 / 18 | 3017 | 0.998070 |
| 2.00 | 82.7% / 76.9% | 0.999512 | 9.957847 | 15 / 16 | 6120 | 0.997713 |

The one-lane service ceiling remains approximately one event/cycle; higher
prediction accuracy under overload does not increase that capacity.  Overrun
and latency-tail growth beyond load 1.0 are therefore disclosed rather than
misrepresented as predictor gains.

### Affine relabel and decision status

Elephant/mouse identity and affine runs are exactly equal in throughput,
latency tail, overrun, fairness, attempts, hits, and misses.  Retrigger identity
and affine are also equal.  Rotating-victim relabeling changes throughput by
0.000733 event/cycle and overrun by two events while p99 and fairness remain
equal; this is small but not hidden.

The local result clears correctness and fairness gates.  It does **not** clear
the physical break-even gate: same-cycle event latency improves on correlated
traces, but the functional clock does not measure area, energy, or achievable
frequency.  A5 remains a research candidate pending approved, identical-flow
PPA.  It must be rejected if that later run fails the predeclared 10% Fmax / 5%
area conditions or shows no energy/event benefit.

Reproduction:

```bash
tests/a5_speculative_pregrant/run_frozen_regression.py \
  --output /tmp/a5-frozen-regression \
  --trace-dir /tmp/a5-frozen-traces
```
