# Eight-track quantitative capacity/latency audit

Date: 2026-08-07.  Scope: read-only evidence from the current A2, A4, A6,
A7, A8, and A9 worktrees, normalized on the frozen N=16 46-trace manifest.
The rejected A5 speculative-pregrant result is fixed and is neither rerun nor
reinterpreted here.  A3 was not part of this reassignment.  No other worktree,
common benchmark/TB/trace/golden file, or server state was modified.

## 1. Common definitions

The audit follows the frozen TB rather than each architecture's preferred
internal counter.

| Quantity | Definition used here |
| --- | --- |
| declared offered rate | manifest `load`; a grouping target, not an observed count |
| realized offered rate | `generated / stim_cycles` for that exact trace |
| accepted | event transferred from the common one-entry source latch into the candidate |
| delivered | accepted logical event reconstructed at the normalized retire boundary after drain |
| fixed-window event/cycle | `measurement_delivered / measurement_cycles`; drain completions are excluded |
| event/cycle/lane | fixed-window event/cycle divided by active native retire lanes; adapter array entries tied permanently invalid are excluded |
| p50/p95/p99 | nearest-rank `delivery_cycle - occurrence_cycle` over delivered logical events only |
| max wait | maximum `accept_cycle - occurrence_cycle` among retained requests |
| overrun | a new occurrence arriving while that source's common latch is occupied |
| source capacity | 16 common source-boundary slots, one per source; reported separately from candidate-internal slots |
| retire ceiling | at most the number of retire lanes in logical events/cycle; a narrower codec link may impose a lower bottleneck |

`generated`, `accepted`, and `delivered` are counts, whereas fixed-window
throughput is a rate.  `accepted == delivered` after drain proves transport
conservation but does not prove that all offered events were admitted.  A
candidate can reduce overrun by holding more admitted events without changing
its sustainable retire rate.

## 2. Evidence boundary and extractor

The candidate-owned extractor is
`tests/a5_speculative_pregrant/extract_capacity_latency_audit.py`.  It checks
the common manifest and emits one normalized row per trace/configuration.  It
does not fill missing percentiles from averages and does not promote codec,
oracle, occupancy, or token counters to logical outcomes.

Current machine-readable coverage:

| Evidence | Rows | Precision |
| --- | ---: | --- |
| A2 adaptive dual path | 46 | exact current `/tmp` common-TB summary and event CSV |
| A4 quadtree | 46 | committed exact common-TB summary and event-run CSV |
| A6 v2 codec | 46 | committed exact counts, fixed throughput, average/max latency; p50/p95/p99 unavailable |
| A7 compactor K1/K2/K4 | 138 | exact per-run counts/tails; fixed-window rate and max-wait are exact report-group aggregates |
| A8 B4 age wheel | 46 | exact current `/tmp` common-TB summary and event CSV |
| A9 distributed L4/L1 | 0 in normalized CSV | full machine-readable 46-run directories were not retained; current report totals and named cuts only |

The normalized output contains 322 rows.  A9 is not silently reconstructed
from prose: the extractor prints `A9=no_machine_readable_46_directory`.  If a
complete A9 result directory is supplied with `--a9-dir`, the same common-TB
adapter accepts it.

## 3. Suite-wide count and fixed-window audit

Every configuration below saw the same 87,000 generated occurrences.  `FW
epc` pools the frozen measurement counters over 103,680 stimulus cycles when
those counters were retained.  A6 is reconstructed from its published
per-trace fixed-window rates and known stimulus denominators.  It is not
`delivered / total_cycles` and not post-drain delivery rate.

| Candidate | Lanes | Internal event slots | Accepted / delivered | Overrun | FW epc | FW epc/lane | Suite p50/p95/p99 | Max wait |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A2 adaptive dual path | 1 | 8 reservoir | 74,183 / 74,183 | 12,817 | 0.714188 | 0.714188 | 1 / 19 / 22 | 15 |
| A4 quadtree | 1 | 5 merge-node slots | 74,045 / 74,045 | 12,955 | 0.712934 | 0.712934 | 3 / 17 / 20 | 15 |
| A6 v2 codec | 1 | 16-event block | 24,147 / 24,147 | 62,853 | 0.221904 | 0.221904 | unavailable; weighted mean 119.84, max 173 | 66 |
| A7 prefix K1 | 1 | 1 retire slot | 73,878 / 73,878 | 13,122 | 0.711806 | 0.711806 | per-trace exact, no pooled event stream retained | 15 |
| A7 prefix K2 | 2 | 2 retire slots | 87,000 / 87,000 | 0 | 0.838821 | 0.419411 | per-trace exact, no pooled event stream retained | 7 |
| A7 prefix K4 | 4 | 4 retire slots | 87,000 / 87,000 | 0 | 0.838821 | 0.209705 | per-trace exact, no pooled event stream retained | 3 |
| A8 B4 age wheel | 1 | 1 output slot | 73,886 / 73,886 | 13,114 | 0.711883 | 0.711883 | 2 / 11 / 14 | 15 |
| A9 distributed L4 | 4 | 48: three per cell | 86,893 / 86,893 | 107 | not retained suite-wide | not retained | report-only named cuts | report-only |
| A9 distributed L1 | 1 | 48: three per cell | 74,153 / 74,153 | 12,847 | not retained suite-wide | not retained | report-only named cuts | report-only |

The suite-wide A7 K2/K4 rate is only 0.839 because most frozen traces offer
less than one event/cycle.  It must not be used as the saturation capacity.
Likewise, A2's 305-event accepted-count advantage over A7 K1 is not a 0.3%
service-rate gain; most of it is finite reservoir admission before drain.

## 4. Saturation and burst cuts

Uniform 2.0 is the cleanest sustained service-capacity cut.  Counts are pooled
over three 2,048-cycle seeds.  A6 retains no percentile event CSV, so its first
seed's measured average/max is shown separately instead of invented tails.

| Candidate | Offered | Accepted | Overrun | FW epc | epc/lane | p50/p95/p99 | Max wait | Interpretation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A2 | 12,288 | 6,192 | 6,096 | 1.000000 | 1.000000 | 18 / 22 / 23 | 15 | one-lane service plus eight-slot backlog |
| A4 | 12,288 | 6,177 | 6,111 | 0.999023 | 0.999023 | 14 / 21 / 21 | 15 | same one-lane knee; distributed buffering only |
| A6 | 4,096 seed 2001 | 536 | 3,560 | 0.242188 | 0.242188 | unavailable; mean/max 136.43/146 | 63 | fill/serialize/parse/retire bottleneck |
| A7 K1 | 12,288 | 6,168 | 6,120 | 0.999512 | 0.999512 | 11 / 15 / 16 | 15 | one-lane reference capacity |
| A7 K2 | 12,288 | 12,288 | 0 | 1.999023 | 0.999512 | 2 / 2 / 2 | 0 | demonstrated two-event/cycle service capacity |
| A7 K4 | 12,288 | 12,288 | 0 | 1.999023 | 0.499756 | 2 / 2 / 2 | 0 | offered ceiling hides capacity above K2 |
| A8 B4 | 12,288 | 6,170 | 6,118 | 0.999512 | 0.999512 | 10 / 13 / 15 | 15 | arbitration policy, not capacity increase |
| A9 L4 | report total for three seeds | not retained by cut | 23 | 1.992513 | 0.498128 | report p95/p99 7/8 | not retained | width supplies service; topology does not beat same-L4 central |
| A9 L1 | report total for three seeds | not retained by cut | 6,032 | 0.996419 | 0.996419 | report p95/p99 142/861 | not retained | one-lane service hidden by deep distributed queueing |

Rate-shape B16 separates service width from finite burst drain.  All one-lane
non-codec candidates deliver 0.5 event/cycle because that is the offered mean.
A2's p50/p95/p99 is 8/16/16, A4 is 10/18/18, A7 K1 is 9/17/17,
A7 K2 is 5/9/9, A7 K4 is 3/5/5, and A8 is 9/17/17.  K2/K4 reduce
serialization latency in proportion to real parallel retirement; A2/A4 reduce
some admission wait with storage but cannot exceed one completion per cycle.
A6 accepts only 1,040 of 2,048, reaches 0.246094 event/cycle, and has
129.13/146 average/max latency: its block/link serialization dominates the
nominal one-lane retire interface.

## 5. Capacity versus buffering versus pipeline latency

### A2: ingress buffering plus a latency bypass

A2 has one retire lane, so its sustainable ceiling remains one event/cycle.
The eight-entry reservoir explains exactly eight fewer overruns per uniform
overload seed and the longer overload tail.  Rotating-victim identity improves
overrun 215 to 113 and fixed-window rate 0.976318 to 1.0 because the reservoir
absorbs phase/HOL pressure before the one-lane server; this is an admission
capacity gain, not service capacity above one.  Sparse p50/p95/p99 1/1/1 is a
real zero-register latency bypass, independent of the reservoir.

### A4: distributed capture, root serialization, and two pipeline stages

Five one-entry merge nodes provide geographically distributed storage, but the
root remains one lane.  Uniform saturation stays at one event/cycle.  The
rotating-victim overrun reduction 215 to 150 comes from capture/HOL placement;
its p99 worsens 7 to 12.  Sparse p50/p95/p99 3/3/3 and B16 10/18/18 expose the
registered tree pipeline.  The earlier Python model's latency is one cycle
lower and is explicitly superseded by the RTL/common-TB CSV; it is not used as
an outcome here.

### A6: serialization throughput, not compression-ratio throughput

A6's 16-event block is storage, not a 16-event retire lane.  The candidate
stalls ingress while it fills, encodes, sends data plus delimiter, parses, and
retires.  Its maximum fixed-window result is about 0.246 event/cycle and only
24,147/87,000 occurrences are accepted.  The 3.705 accepted-stream data bits
per event and entropy results are link-internal statistics.  They cannot replace
the logical outcome of 62,853 overruns and roughly 120-cycle mean E2E latency.

### A7: the only demonstrated service-capacity increase

K2 is a true service-width result: on sustained uniform 2.0 it completes
1.999 event/cycle, admits every event, has p99=2, and preserves roughly one
event/cycle/lane.  K4 improves simultaneous/B16 drain tails but the suite never
offers above two event/cycle; therefore capacity above K2 is unmeasured.  The
prefix network and equal-contract replicated selector have identical outcomes
at the same K.  Prefix-vs-replicated gate/depth is a structural result, not an
additional throughput result.

### A8: arbitration-order latency only

A8 has one registered output slot and one retire lane.  Age buckets change
which held source wins, improving rotating-victim and phase tails, but uniform
capacity remains one event/cycle.  B1 versus B4 quantization is a scheduler
resolution trade: it can reduce wait/tail and also change which later source
occurrence overruns.  It does not add storage or service lanes.

### A9: physical width plus deep distributed buffering

A9 contains an ingress entry and two transport entries in each of 16 cells,
48 internal event slots total.  L4 can approach two event/cycle on a trace that
offers two, but its 0.498 event/cycle/lane and the same-L4 centralized
reference's 1.993001 event/cycle show no service-efficiency gain from the
distributed mechanism.  L1's p99=861 at uniform 2.0 is accumulated queueing in
the 48-slot path, not hidden bandwidth.  Fixed stripes can also turn placement
imbalance into overrun despite four output lanes.

## 6. Internal/oracle metric misuse audit

| Track/metric | Correct use | Incorrect outcome interpretation |
| --- | --- | --- |
| A2 occupancy, mode, reservoir depth | explain admission and tail tradeoff | call eight extra admitted events sustained service capacity |
| A4 local model and node occupancy | pre-RTL/topology diagnosis | use the model's one-cycle-lower latency after RTL qualification |
| A5 oracle next-source result | upper-bound unavailable information | report oracle accuracy/one-cycle bypass as implementable candidate gain |
| A6 entropy, b/e, RAW ratio, link event/pin-cycle | codec/link efficiency on the accepted subset | substitute for offered-stream acceptance or fixed-window logical event/cycle |
| A7 prefix count/rank and gate proxy | implementation structure at fixed K | count prefix work or K=4 nominal width as delivered throughput |
| A8 epoch/bucket/exact-age state | scheduler resolution and ordering | treat age precision or TB-only deadlines as more service capacity |
| A9 occupied-slot fraction, empty-slot RTT, lane-service fraction | internal utilization/topology cost | infer useful throughput without delivered event/cycle and lane normalization |

The current track reports generally disclose these limitations.  The audit's
correction is mainly one of cross-track alignment: outcome rankings must use
logical delivered events at the same normalized boundary, not the most
favorable native counter from each architecture.

## 7. Conclusions

1. **Service capacity:** only A7 K2 demonstrates a higher common-trace service
   knee, approximately two logical events/cycle.  A9 L4 gets similar raw width
   but not better per-lane service or latency than its same-width centralized
   reference.  No trace establishes A7 K4 capacity beyond K2.
2. **Ingress/internal buffering:** A2, A4, and A9 reduce selected overruns or
   delay loss by storing more events.  Their longer overload tails reveal the
   queued work.  Buffering is useful but is not a higher retire rate.
3. **Serialization/pipeline latency:** A4 adds registered tree latency; A6 is
   dominated by block/link serialization; A9 L1 is dominated by distributed
   queue traversal.  A2 sparse bypass removes one register cycle, and A7 width
   reduces burst serialization.
4. **Policy-only effects:** A8 changes latency/fairness ordering around the
   same one-lane ceiling.  Its age resolution is not capacity.
5. **Evidence gap:** A9 needs preserved per-trace summary/event CSV before it
   can occupy all normalized columns.  Report-only values are retained as such,
   not reverse-engineered.

Reproduction is local and read-only with respect to the six source worktrees:

```bash
python3 tests/a5_speculative_pregrant/extract_capacity_latency_audit.py \
  --workspace-parent /home/chickgoose/projects \
  --output /tmp/eight-track-capacity-latency.csv
```

No server simulation or PPA command is part of this audit.
