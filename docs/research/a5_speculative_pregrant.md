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

Let `N = NUM_SOURCES`, `S = ceil(log2(N))`, compact context width `H`, table
entries `T`, and confidence width `C`.  Entries are indexed by the low `H` bits
of the previously accepted source modulo `T`:

```text
entry[index]   = {valid:1, tag:H, target:S, confidence:C}
history        = {valid:1, last_accepted_source:S}
fallback_rr    = S bits
```

The predictor-only raw budget is `T * (1 + H + S + C) + 1 + S` bits.  The
complete enabled A5 state additionally has the `S`-bit deterministic fallback
pointer, `ceil(log2(MAX_PREDICT_STREAK+1))` streak bits, and the ordinary
one-event output register (`valid + S + ADDR_WIDTH`).

For the default `N=16, S=4, H=4, T=16, C=2`, predictor state is
`16*(1+4+4+2)+1+4 = 181` bits and complete algorithm/transport state is 208
bits.  Tags were added during adversarial falsification.  If two compact
contexts map to one table entry, a tag mismatch is cold/low-confidence and
cannot issue a speculative grant.  If `H<S`, distinct full source histories may
still intentionally alias before tagging; current validity checking makes that
a performance loss, never a correctness dependency.

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

## 11. Second-pass adversarial falsification and PPA proxy

### Directed sequences and recovery

The candidate-only test uses the same one-pending-event-per-source semantics,
one retire lane, and a two-cycle occurrence gap.  Predictor-disabled and
predictor-enabled runs receive the exact same deterministic sequence.  Every
one of the 81 parameter/workload runs had zero errors, zero overrun, and
`accepted == delivered`.  Fixed-window throughput was 0.500000 for every
enabled/disabled pair; prediction produced no throughput gain in this regime.

Default `H4/T16/C2` results:

| Directed case | Attempts/hits/misses | Accuracy / coverage | Mean E2E, enabled / fallback | Recovery |
| --- | ---: | ---: | ---: | ---: |
| alternating A/B | 123/123/0 | 100% / 96.1% | 1.039062 / 2.000000 | no miss |
| anti-correlated A/B/A/C | 59/59/0 | 100% / 46.1% | 1.539062 / 2.000000 | ambiguity suppresses attempts |
| cold, 16 unique sources | 0/0/0 | no attempt / 0% | 2.000000 / 2.000000 | deterministic cold fallback |
| moving hotspot dwell 1 | 119/119/0 | 100% / 93.0% | 1.070312 / 2.000000 | no miss |
| moving hotspot dwell 2 | 0/0/0 | no attempt / 0% | 2.000000 / 2.000000 | confidence never reaches threshold |
| moving hotspot dwell 4 | 119/88/31 | 73.9% / 93.0% | 1.312500 / 2.000000 | 31/31 same-cycle, 0-cycle penalty |
| moving hotspot dwell 8 | 119/104/15 | 87.4% / 93.0% | 1.187500 / 2.000000 | 15/15 same-cycle, 0-cycle penalty |
| dwell 4, affine `(5s+3)%16` | 119/88/31 | 73.9% / 93.0% | 1.312500 / 2.000000 | 31/31 same-cycle |

The anti-correlated sequence is an important accuracy caveat: confidence
hysteresis avoids wrong attempts for the ambiguous `A` context, so apparent
100% accuracy covers only 46.1% of opportunities.  Dwell 2 is a harder
falsification: its repeated source lasts too briefly to train, producing zero
coverage and zero latency benefit.

For an explicit collision, `H4/T2/C2` runs the repeating `0,1,2,3` sequence.
Contexts 0/2 and 1/3 collide.  Tags prevent a stale target from issuing:
coverage is zero, all 128 events use deterministic fallback, mean latency is two
cycles, and correctness is preserved.  The full 16-entry table learns the same
sequence with 119/119 hits and 1.070312-cycle mean latency.  Aliasing therefore
degrades performance rather than correctness.

### State/latency sweep

Raw bits count declared RTL state; Yosys FFs show state remaining after
unreachable table entries and unused metric outputs are optimized.  Test-only
five 32-bit counters are disabled for synthesis.

| Configuration | Raw predictor / total bits | Yosys FF | Sparse alternating gain | Dwell-4 / dwell-8 gain | NAND proxy | Depth |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fallback only | 0 / 25 | 25 | 0% | 0% / 0% | 984 | 61 |
| H1/T16/C2 | 133 / 160 | 44 | 48.05% | 6.25% / 20.31% | 1187 | 62 |
| H2/T16/C2 | 149 / 176 | 66 | 48.05% | 34.38% / 40.63% | 1261 | 64 |
| H4/T2/C2 | 27 / 54 | 52 | 48.05% | 12.50% / 31.25% | 1204 | 63 |
| H4/T4/C2 | 49 / 76 | 76 | 48.05% | 34.38% / 40.63% | 1284 | 64 |
| H4/T8/C2 | 93 / 120 | 120 | 48.05% | 34.38% / 40.63% | 1471 | 62 |
| H4/T16/C1 | 165 / 192 | 192 | 48.83% | 25.00% / 37.50% | 1602 | 60 |
| H4/T16/C2 | 181 / 208 | 208 | 48.05% | 34.38% / 40.63% | 1801 | 62 |
| H4/T16/C3 | 197 / 224 | 224 | 46.48% | 29.69% / 37.50% | 1853 | 62 |

Only `2^H` contexts are reachable, so Yosys trims most of nominal T16 when
H=1/2.  H4/T4/C2 matches the larger tables on these four-source directed
patterns with 76 total bits, but this is not evidence that it matches the
frozen 16-source distribution.  One-bit confidence has the only nominal depth
improvement (61 to 60) but gives up dwell-4 latency and still adds 63% NANDs.

The cycle-level toggle proxy counts transitions on source-ready and normalized
retire control/payload, not hidden internal table writes.  Relative to fallback,
default H4/T16/C2 is +6 toggles on alternating, unchanged on cold/dwell-2,
-348 on dwell-4, -156 on dwell-8, and -446 on affine dwell-4.  These values
suggest less output/control switching on longer dwell but are not a power
estimate; post-route activity-annotated power remains required.

### Frozen 46-trace predictor/fallback A/B

Both variants replayed all 46 unchanged trace SHAs and passed correctness.
Prediction did not improve fixed-window throughput in any aggregate group.

| Frozen group | Predictor throughput / fallback | Mean E2E predictor / fallback | p99 predictor / fallback | Other delta |
| --- | ---: | ---: | ---: | ---: |
| core sparse | 0.031250 / 0.031250 | 2.000000 / 2.000000 | 2 / 2 | none |
| single moving hotspot | 0.890137 / 0.890137 | 1.869243 / 2.000000 | 2 / 2 | none |
| elephant/mouse | 0.891602 / 0.891602 | 1.811713 / 2.000000 | 2 / 2 | none |
| rate-shape B1 | 0.500000 / 0.500000 | 1.016113 / 2.000000 | 2 / 2 | none |
| timing pair | 0.615234 / 0.615234 | 2.215788 / 2.216581 | 4 / 4 | none |
| rotating victim identity | 0.976074 / 0.976318 | 2.991502 / 2.988256 | 7 / 7 | predictor +1 overrun |
| phase transition | 0.521606 / 0.521606 | 5.720103 / 5.706763 | 16 / 16 | predictor fairness -0.000404 |
| uniform 1.50 | 0.999512 / 0.999512 | 7.177893 / 7.118163 | 18 / 14 | same overrun |

This separates the genuine result: speculative bypass improves mean latency on
correlated traffic, especially burst-size one, but does not move the one-lane
capacity limit and can worsen an overload latency tail.

### Local structural proxy and decision

Verilator 5.032 lint passes all nine elaborated parameter points.  Yosys 0.52
plus generic NAND/NOT ABC mapping reports default H4/T16/C2 at 208 FF, 1801
NAND, 834 NOT, 2844 total cells, and topological depth 62.  Fallback is 25 FF,
984 NAND, 432 NOT, 1441 cells, and depth 61.  This generic mapping is not a
standard-cell area/Fmax result, but it falsifies the expected critical-path
improvement at proxy level while showing roughly 2x generic cell count.

**Decision: reject the current predictor-enabled H4/T16/C2 as the A5 physical
candidate and retain deterministic fallback as the reference.**  H4/T4/C2 is
the directed-trace state Pareto point, but its depth proxy is worse (64) and it
has no demonstrated fixed-window gain, so it is not promoted either.  A future
revision would need a genuinely pipelined recovery path or a different physical
lookup implementation, still within the speculative-pregrant axis, before
requesting expensive physical qualification.

No Genus/Innovus server job was run.  If HEAD later overrides this rejection,
the exact available Genus screening command is recorded as:

```bash
# PENDING_HEAD_PPA (requires explicit approval and immutable HEAD registration)
env AER_PROJECT_ROOT="$PWD" \
  AER_TOP=a5_speculative_pregrant_ppa_top \
  AER_RTL_FILELIST="$PWD/rtl/candidates/a5_speculative_pregrant/a5_speculative_pregrant_ppa.f" \
  AER_SDC="$PWD/constraints/aer_common.sdc" \
  AER_OUTPUT_DIR="$PWD/results/runs/a5-head-pending/n16/genus" \
  AER_LIBRARY_FILE=/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib \
  AER_CLOCK_PERIOD_NS=5.000 AER_CLOCK_PORT=clk AER_RESET_PORT=rst_n \
  AER_INPUT_DELAY_NS=0.250 AER_OUTPUT_DELAY_NS=0.250 \
  AER_CLOCK_UNCERTAINTY_NS=0.100 AER_LOAD_PF=0.010 \
  AER_NUM_SOURCES=16 AER_ADDR_WIDTH=16 AER_GENUS_BIN=genus \
  scripts/drivers/genus.sh
```

`PENDING_HEAD_PPA_POST_ROUTE` is deliberately not fabricated: this repository
has no architecture-neutral Innovus driver or frozen candidate registry entry.
Before any post-route command exists, HEAD must supply both under the physical
PPA contract, including per-target resynthesis, CTS/route/extraction, setup and
hold, unconstrained-path, DRC, and activity-annotated power checks.

Reproduction:

```bash
tests/a5_speculative_pregrant/run_adversarial_sweep.py --output /tmp/a5-adversarial
tests/a5_speculative_pregrant/run_ppa_proxy.py --output /tmp/a5-ppa-proxy
tests/a5_speculative_pregrant/run_frozen_regression.py \
  --predictor-enabled 0 --output /tmp/a5-frozen-fallback \
  --trace-dir /tmp/a5-frozen-traces
```

## 12. Third-pass fundamental utility ceiling

This section adds a ceiling experiment; it does not revise the second-pass
measurements or decision.  The unchanged 46 trace SHAs were replayed through
one retire lane for four selection policies:

- **oracle next-source** predicts the source that deterministic round-robin
  would select from the current active set.  It therefore never changes the
  winner, fairness, or correctness.  Its zero-bit entry in the tables means
  “unpriced ideal information,” not implementable hardware.
- **last successor** stores the most recently observed successor separately
  for each tagged source context.  It is not global last-grant priority.
- **confidence Markov** is the existing tagged transition table with saturating
  confidence and deterministic miss/cold/alias fallback.
- **fallback** is predictor-disabled deterministic round-robin.

All 874 runs (19 configurations times 46 traces) had zero scoreboard errors
and `accepted == delivered`.  Oracle and fallback both delivered 73,878 events
after drain and both had 13,122 source overruns.  The learned predictors may
change arbitration order before deterministic recovery, so their accepted
totals differ slightly: 73,882 for last-successor and 73,876 for H4/T16/C2.
This is disclosed rather than treating dropped-at-source events as service.

### Aggregate ceiling

| Policy | State bits | Accuracy | Useful bypass / delivered | Weighted mean E2E | Gain vs fallback | Updates / useful bypass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fallback | 0 | n/a | 0% | 4.077479 | 0 | infinity |
| oracle deterministic winner | ideal/unpriced | 100% | 100% | 3.077479 | 1.000000 cycle | 1.00 |
| per-context last-successor H4/T16 | 149 | 38.17% | 12.02% | 3.957175 | 0.120672 cycle | 8.32 |
| gated Markov H4/T16/C2 | 181 | 75.86% | 11.67% | 3.965185 | 0.112358 cycle | 8.56 |

Latency gain uses each fallback trace's delivered count as the common weight,
so the learned policies' small accepted-count differences cannot inflate it.
Thus the current Markov implementation captures only 11.24% of the oracle
latency opportunity.  Its higher accuracy does not beat last-successor: the
149-bit last-successor obtains slightly more latency benefit than the 181-bit
Markov table.  Accuracy counts hits even while an output is already occupied;
only `bypass_hit = hit && !output_valid && retire_ready` can remove the output
register cycle.

Across the fixed 103,680-cycle measurement windows, fallback delivered 73,800
events and oracle delivered 73,826: throughput 0.711806 versus 0.712056.  The
absolute oracle ceiling is only 26 events, or 0.0352% relative, and does not
change the one-event/cycle single-lane service limit.  Last-successor delivered
73,800, while H4/T16/C2 delivered 73,797.  Consequently no realizable policy
demonstrates a suite-wide fixed-window throughput benefit.

For critical path, same-cycle deterministic recovery imposes a harder bound.
With predictor path delay `D_p` and fallback scan delay `D_f`, static timing is

```
D_cycle >= max(D_p, D_f)
```

for every learned predictor that can miss.  Prediction probability cannot
remove `D_f` from worst-case timing; it can only reduce event latency.  The
oracle marks 100% of events as theoretically eligible for an indexed fast
path, but its RTL computes the oracle target with the deterministic scan and is
not a physical implementation.  A real Fmax win would require isolating or
pipelining the recovery path, at which point misses recover next cycle rather
than through the present same-cycle path.

### Area/toggle recovery boundary

Let `N` be delivered events, `b` useful empty-slot bypasses, `u` predictor
updates, `q=b/N`, `E_u` the switching energy of one table update, and `V_b` the
value or energy saved by one bypass.  Ignoring leakage is already favorable to
the predictor; it can recover dynamic update cost only if

```
b * V_b > u * E_u        or        V_b / E_u > u / b.
```

Adding area/leakage `P_A` over observation time `T` tightens this to

```
q * V_b > (u/N) * E_u + P_A * T / N.
```

H4/T16/C2 performs 73,830 update opportunities for 8,623 useful bypasses, so
even before area it requires `V_b/E_u > 8.56`.  Last-successor requires 8.32.
The high-accuracy overload boundary is more decisive: all three uniform-2.0
traces have 81.9--84.0% Markov accuracy but zero useful bypasses, hence infinite
update-cost payback.  Phase-transition seeds have 72.3--74.9% accuracy but only
0.046--0.094% bypass coverage, requiring 2,150 and 1,060 update opportunities
per useful bypass.  This is the measured workload boundary where accuracy is
high but area and toggle activity cannot be recovered.

Local generic Yosys/ABC and Verilator proxies confirm the cost trend:

| Configuration | Predictor bits | NAND vs fallback | Depth | Weighted latency gain |
| --- | ---: | ---: | ---: | ---: |
| fallback | 0 | 990 / reference | 61 | 0 |
| H4/T1/C2 gated | 16 | 1151 / +16.3% | 65 | 0.009813 |
| H4/T4/C2 ungated | 49 | 1287 / +30.0% | 66 | 0.035959 |
| H4/T8/C2 gated | 93 | 1468 / +48.3% | 60 | 0.062224 |
| H4/T16/C1 gated | 165 | 1608 / +62.4% | 59 | 0.122366 |
| H4/T16/C2 gated | 181 | 1799 / +81.7% | 62 | 0.112358 |
| last-successor H4/T16 | 149 | 1434 / +44.8% | 67 | 0.120672 |

The best generic depth proxy is only 3.3% shorter (59 versus 61) while adding
62.4% NANDs, below the predeclared 10% Fmax/5% area break-even.  These are local
generic proxies, not post-route claims; no server or physical flow was run.

### Minimum Pareto search

The sweep covered H1--H4, T1/2/4/8/16, C1/C2/C3, and confidence gating on/off.
The useful state/gain frontier is:

| Configuration | Bits | Gain cycles/event | Bypass coverage | Updates/bypass |
| --- | ---: | ---: | ---: | ---: |
| H4/T1/C2 gated | 16 | 0.009813 | 0.98% | 101.84 |
| H1/T2/C2 gated | 21 | 0.017042 | 1.71% | 58.50 |
| H2/T4/C2 gated | 41 | 0.031999 | 3.21% | 31.15 |
| H4/T4/C1 ungated | 45 | 0.035526 | 3.16% | 31.67 |
| H4/T4/C2 ungated | 49 | 0.035959 | 3.20% | 31.24 |
| H3/T8/C2 gated | 85 | 0.061879 | 6.27% | 15.94 |
| H4/T8/C2 gated | 93 | 0.062224 | 6.07% | 16.47 |
| H4/T16/C1 gated | 165 | 0.122366 | 11.64% | 8.58 |

C3 raises reported accuracy but loses latency utility.  Removing gating raises
attempts while accuracy falls near 40%; at T4 it adds a small latency gain but
still requires more than 31 table updates per useful bypass.  Across predictor
families, last-successor is also Pareto-relevant: it nearly matches the best
Markov gain with 16 fewer bits, though its depth proxy is worse.  No point meets
the physical break-even gates.

### Per-trace latency ceiling

`bypass%` is useful empty-slot coverage, not raw prediction coverage.

| Trace | Fallback L | Oracle L | Last L / bypass% | Markov L / bypass% |
| --- | ---: | ---: | ---: | ---: |
| core_sparse_identity | 2.0000 | 1.0000 | 2.0000 / 0.00% | 2.0000 / 0.00% |
| core_sparse_rotate180 | 2.0000 | 1.0000 | 2.0000 / 0.00% | 2.0000 / 0.00% |
| core_simultaneous_identity | 9.5000 | 8.5000 | 9.5000 / 0.00% | 9.5000 / 0.00% |
| uniform_l0p125_s2001 | 2.0000 | 1.0000 | 1.9681 / 3.19% | 1.9960 / 0.40% |
| uniform_l0p125_s2002 | 2.0000 | 1.0000 | 1.9425 / 5.75% | 2.0000 / 0.00% |
| uniform_l0p125_s2003 | 2.0000 | 1.0000 | 1.9307 / 6.93% | 2.0000 / 0.00% |
| uniform_l0p50_s2001 | 2.0000 | 1.0000 | 1.9511 / 4.89% | 1.9990 / 0.10% |
| uniform_l0p50_s2002 | 2.0000 | 1.0000 | 1.9653 / 3.47% | 2.0000 / 0.00% |
| uniform_l0p50_s2003 | 2.0000 | 1.0000 | 1.9709 / 2.91% | 1.9981 / 0.19% |
| uniform_l0p90_s2001 | 2.0000 | 1.0000 | 1.9925 / 0.75% | 1.9995 / 0.05% |
| uniform_l0p90_s2002 | 2.0000 | 1.0000 | 1.9919 / 0.81% | 2.0000 / 0.00% |
| uniform_l0p90_s2003 | 2.0000 | 1.0000 | 1.9918 / 0.82% | 2.0000 / 0.00% |
| uniform_l1p00_s2001 | 2.0000 | 1.0000 | 2.0000 / 0.00% | 2.0000 / 0.00% |
| uniform_l1p00_s2002 | 2.0000 | 1.0000 | 2.0000 / 0.00% | 2.0000 / 0.00% |
| uniform_l1p00_s2003 | 2.0000 | 1.0000 | 2.0000 / 0.00% | 2.0000 / 0.00% |
| uniform_l1p25_s2001 | 5.1112 | 4.1112 | 4.9722 / 0.00% | 5.0580 / 0.00% |
| uniform_l1p25_s2002 | 5.0702 | 4.0702 | 5.2815 / 0.00% | 5.1420 / 0.00% |
| uniform_l1p25_s2003 | 5.2906 | 4.2906 | 5.2963 / 0.00% | 5.2675 / 0.00% |
| uniform_l1p50_s2001 | 7.0054 | 6.0054 | 7.0487 / 0.00% | 7.0146 / 0.00% |
| uniform_l1p50_s2002 | 7.2084 | 6.2084 | 7.1173 / 0.00% | 7.3247 / 0.00% |
| uniform_l1p50_s2003 | 7.1408 | 6.1408 | 6.9810 / 0.00% | 7.1943 / 0.00% |
| uniform_l2p00_s2001 | 10.2337 | 9.2337 | 9.9087 / 0.00% | 10.2337 / 0.00% |
| uniform_l2p00_s2002 | 9.9037 | 8.9037 | 10.0228 / 0.00% | 9.9494 / 0.00% |
| uniform_l2p00_s2003 | 9.7635 | 8.7635 | 9.9509 / 0.00% | 9.6900 / 0.00% |
| shape_b1 | 2.0000 | 1.0000 | 1.0083 / 99.17% | 1.0161 / 98.39% |
| shape_b4 | 3.5000 | 2.5000 | 2.7573 / 74.27% | 2.7632 / 73.68% |
| shape_b16 | 9.5000 | 8.5000 | 9.3154 / 18.46% | 9.3169 / 18.31% |
| spatial_local | 3.5000 | 2.5000 | 2.7539 / 74.61% | 2.7559 / 74.41% |
| spatial_dispersed | 3.5000 | 2.5000 | 2.7539 / 74.61% | 2.7559 / 74.41% |
| spatial_local_mirror | 3.5000 | 2.5000 | 2.7539 / 74.61% | 2.7559 / 74.41% |
| moving_hotspot_single_s3301 | 2.0000 | 1.0000 | 1.8781 / 12.19% | 1.8572 / 14.28% |
| moving_hotspot_single_s3302 | 2.0000 | 1.0000 | 1.8905 / 10.95% | 1.8812 / 11.88% |
| moving_hotspot_multi_disperse_s3301 | 2.0000 | 1.0000 | 1.9791 / 2.09% | 1.9973 / 0.27% |
| moving_hotspot_multi_row_s3301 | 2.0000 | 1.0000 | 1.9769 / 2.31% | 1.9967 / 0.33% |
| moving_hotspot_multi_column_s3301 | 2.0000 | 1.0000 | 1.9802 / 1.98% | 1.9984 / 0.16% |
| rotating_victim_identity | 2.9883 | 1.9883 | 2.9535 / 0.20% | 2.9915 / 0.02% |
| rotating_victim_affine | 2.9333 | 1.9333 | 2.9186 / 0.05% | 2.9201 / 0.00% |
| phase_transition_s3501 | 5.7314 | 4.7314 | 5.7320 / 1.08% | 5.8091 / 0.09% |
| phase_transition_s3502 | 5.6825 | 4.6825 | 5.8731 / 0.79% | 5.6323 / 0.05% |
| elephant_mouse_identity | 2.0000 | 1.0000 | 1.8478 / 15.22% | 1.8117 / 18.83% |
| elephant_mouse_affine | 2.0000 | 1.0000 | 1.8478 / 15.22% | 1.8117 / 18.83% |
| global_fanin_identity | 9.5000 | 8.5000 | 9.3242 / 17.58% | 9.3301 / 16.99% |
| retrigger_identity | 2.0000 | 1.0000 | 1.9824 / 1.76% | 1.9824 / 1.76% |
| retrigger_affine | 2.0000 | 1.0000 | 1.9824 / 1.76% | 1.9824 / 1.76% |
| timing_pair_s3901 | 2.1971 | 1.1971 | 2.1724 / 2.55% | 2.1963 / 0.08% |
| timing_pair_s3902 | 2.2358 | 1.2358 | 2.2159 / 2.21% | 2.2350 / 0.08% |

Zero-bypass overload rows can still show small positive or negative mean
changes because a correct prediction may reorder contending sources without
removing the output-register cycle.  They are not counted as fast-path utility.

### Correctness proof scope and final decision

Clocked RTL assertions check that every grant is one-hot-or-zero, every granted
source is currently valid, every miss selects exactly the deterministic
fallback winner, and the oracle never changes that winner.  In addition, the
candidate-only exhaustive model enumerates N=3, three-cycle sequences over all
arrival masks, all retire-ready patterns, and all invalid/valid predictor
targets: 262,144 cases.  It proves no fabricated or duplicated event and that
the delivered ID set equals the accepted ID set after drain.  Prediction state
therefore changes performance and ordering only; correctness never depends on
prediction.

**Decision: keep deterministic fallback and reject all measured predictor
configurations.**  Promotion requires all of the following on unchanged traces:

1. zero correctness errors and no unexplained overrun/fairness regression;
2. at least 10% realizable critical-path/Fmax improvement with recovery timing
   included, not oracle information or average hit-path timing;
3. no more than 5% area growth and positive activity-based energy/event;
4. positive fixed-window throughput or enough latency/tail gain to satisfy the
   measured area/toggle inequality on the target workload.

No measured point clears conditions 2--4.  The oracle establishes that the
remaining algorithmic latency ceiling exists, but the same-cycle fallback and
single-lane contract prevent the present table structures from monetizing it.

Reproduction (local tools only):

```bash
python3 tests/a5_speculative_pregrant/exhaustive_small_n.py
python3 tests/a5_speculative_pregrant/run_prediction_ceiling.py \
  --output /tmp/a5-prediction-ceiling \
  --trace-dir /tmp/a5-frozen-traces
python3 tests/a5_speculative_pregrant/run_ceiling_ppa_proxy.py \
  --output /tmp/a5-ceiling-ppa
```
