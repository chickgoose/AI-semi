# A6 W3 — Elias–Fano monotone-dequeue address-event batch link

## Decision

The executable model finds one honest link-level pass point: `N=16`, `K=16`,
two data pins, and a **same-cycle-only** (`window=0`) source-monotone batch.  On
the frozen capacity22 manifest it preserves every accepted occurrence and
improves aggregate link efficiency from `0.100000` to `0.104447`
events/pin-cycle without p95-latency or overrun regression.  Therefore a
standalone synthesizable TX/RX and lockstep TB were built.

Bounded batching at windows 1, 2, and 4 is **HOLD**: each improves link
efficiency slightly, but regresses p95 latency against the natural zero-wait raw
reference on 18–19 of 22 runs.  The built fixed point adds no collection wait.
Deployment is also **HOLD on endpoint PPA**: local generic synthesis reports
12,436 TX+RX cells for only 4.45% aggregate pin-cycle gain.  This is an honest
structural result, not qualified server PPA.

## Exact representation and framing

The physical link has two data pins, a two-bit valid-count (`0`, `1`, or `2`),
and ready: five counted pins, excluding clock/reset for both raw and coded
references.  Raw mode emits each 4- or 6-bit source address in full two-bit
beats and has no header.  Consequently a one-bit beat cannot occur inside a raw
address.  At a raw word boundary, the one-bit value `1` is the unambiguous
Elias–Fano marker.  It is followed by a fixed `ceil(log2(K+1))`-bit count,
then:

1. `l=floor(log2(N/k))` low-bit width for nonempty batches;
2. unary-coded differences of the nondecreasing high parts, one terminating
   `1` per source;
3. the `l` low bits of every source in the same source-monotone order.

The empty partial batch is marker plus zero count and retires no event.  For a
nonempty batch the encoder chooses Elias–Fano only when its marker-inclusive
physical cycles are strictly fewer than raw; ties and adverse sets use raw.
Thus the selected representation never expands link cycles.  This raw fallback
is not a bitmap, enumerative rank, Gray code, or a repackaging of those codecs.

Decode is injective because the marker is legal only at a raw boundary, the
fixed count determines the number of high terminators, `N` and `k` determine
`l`, and exactly `k*l` low bits terminate the frame.  The decoder rejects an
illegal marker, count above K, high part beyond N, duplicate/nonmonotone or
out-of-range reconstruction, trailing bits, and truncation.  Reset discards a
partial frame and the RX FIFO; the next accepted beat begins at a raw boundary.
There is no in-band recovery after corruption, so fail-closed sticky error or
reset is required for resynchronization.

## Occurrence semantics and buffering

Occurrence cycle and TB-only identity are never link payload.  The model keeps
those provenance objects only in the scoreboard and rejoins them to the exact
decoded source.  A batch is source-unique and its wire/dequeue order is strictly
increasing source order.  A same-source refire closes the current batch before
the new occurrence is admitted; it is never coalesced.  A partial batch closes
at its declared finite deadline.  Conservation and the complete accepted
source sequence are asserted independently, so overrun is reported as capacity
loss rather than codec correctness loss.

The cap22 simulator charges two TX batch banks and an RX capacity of `2K`, plus
one normalized retirement per cycle.  For N16/K16, two address/count TX banks
would add 138 bits.  The synthesized modules contain 665 registered bits
(121 TX, 544 RX including the 128-bit RX FIFO), for 803 bits of full modeled
endpoint storage.  Scoreboard-only identity/time storage is correctly excluded
from hardware and never reconstructed for free.

## N16/N64 comparison

The full machine-readable sweep covers every `k=0..16`; selected points follow.
Bits/event include framing, and cycles are on the identical two-data-pin link.
`EF work` counts consumed unary/low symbols plus emitted occurrences; raw work
is one word per event.  Bitmap and enumerative numbers are comparison baselines
only and are not used in the implementation.

| N | k | raw b/e, cycles | bitmap b/e, cycles | enumerative b/e, cycles | selected EF b/e, mean cycles | raw escape | EF decoder work |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 0 | —, 0 | —, 9 | —, 4 | —, 4 | 0 | 0 |
| 16 | 1 | 4.000, 2 | 17.000, 9 | 10.000, 6 | 4.000, 2 | 1.0 | 1.0 |
| 16 | 2 | 4.000, 4 | 8.500, 9 | 6.500, 7 | 4.000, 4 | 1.0 | 2.0 |
| 16 | 4 | 4.000, 8 | 4.250, 9 | 4.250, 9 | 4.000, 8 | 1.0 | 4.0 |
| 16 | 8 | 4.000, 16 | 2.125, 9 | 2.500, 11 | 3.591, 14.961 | 0.0 | 30.725 |
| 16 | 16 | 4.000, 32 | 1.063, 9 | 0.375, 4 | 2.313, 19 | 0.0 | 63.0 |
| 64 | 1 | 6.000, 3 | 65.000, 33 | 12.000, 7 | 6.000, 3 | 1.0 | 1.0 |
| 64 | 4 | 6.000, 12 | 16.250, 33 | 6.500, 14 | 6.000, 12 | 1.0 | 4.0 |
| 64 | 8 | 6.000, 24 | 8.125, 33 | 4.875, 20 | 5.572, 22.912 | 0.0 | 30.574 |
| 64 | 16 | 6.000, 48 | 4.063, 33 | 3.438, 28 | 4.286, 34.910 | 0.0 | 62.582 |

Elias–Fano breaks even only around k=8 for these K/framing choices.  It loses
badly to the forbidden-for-this-track bitmap/enumerative references on dense
sets, which sharply limits novelty and the attainable Pareto point.

## Frozen capacity22 result

The manifest digest is fail-closed at
`99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62`;
all generated event traces and their SHA-256 digests are recorded in the JSON.

| window | delivered raw/EF | overrun raw/EF | delivered during stimulus raw/EF | events/pin-cycle raw→EF | latency-regression runs | gate |
|---:|---:|---:|---:|---:|---:|:---:|
| 0 | 24577 / 24577 | 41039 / 41039 | 24370 / 24387 | 0.100000 → 0.104447 | 0 | PASS |
| 1 | 24577 / 24577 | 41039 / 41039 | 24370 / 24371 | 0.100000 → 0.104463 | 18 | HOLD |
| 2 | 24577 / 24601 | 41039 / 41015 | 24370 / 24383 | 0.100000 → 0.104514 | 19 | HOLD |
| 4 | 24577 / 24847 | 41039 / 40769 | 24370 / 24588 | 0.100000 → 0.105436 | 19 | HOLD |

At window zero only three dense families change: `core_simultaneous_identity`
and `global_fanin_identity` improve p95 latency 33→20 cycles, while `shape_b16`
improves 55→20 and in-window throughput about 0.49585→0.50000.  Their local
link efficiency is 0.10000→0.16842 events/pin-cycle.  Pairwise, uniform sweeps,
shape_b4, phase-transition, and mixed runs select raw and remain unchanged.
The 41,039 overruns are identical raw/codec capacity loss, not dropped or
coalesced decoded events.

## RTL, cost, and verification

The dedicated RTL path contains a parameterized batch encoder, fail-closed
stream decoder with `2K` FIFO, and direct lockstep TB.  The TB exercises raw
singletons/sparse sets, k8/k16 compressed frames, 80 deterministic random
masks, randomized output backpressure, and reset mid-frame.  Exact order and no
phantom events are asserted.  Eleven model tests cover randomized N16/N64 all
cardinalities, malformed/truncated streams, refire, partial timeout, provenance,
conservation, and the same-cycle anti-cross-cycle invariant.  Icarus and
Verilator both pass the lockstep simulation.

Local Yosys 0.52 structural synthesis at N16/K16 gives 4,306 generic encoder
cells and 8,130 generic decoder cells.  This includes both endpoints and the RX
FIFO, but is neither mapped area nor timing.  No server was used.  The large
combinational set-to-bitstream construction makes this proof RTL unsuitable as
a PPA candidate despite its link gate, and the attempted N64 elaboration is not
claimed as a qualified cost result.

Reproduction:

```sh
python3 benchmarks/clean_slate_aer/a6_w3_evaluate.py sweep \
  --max-batch 16 --output /tmp/a6_w3_sweep.json
scripts/run_a6_w3_elias_fano_checks.sh
```

The cap22 command additionally requires the frozen manifest and fresh generated
event JSONL directory.  Detailed evidence is in
`reports/a6-w3-elias-fano/{sweep_n16_n64,cap22,local_synthesis}.json`.
