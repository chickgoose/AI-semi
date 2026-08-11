# A6 W3 — Elias–Fano monotone-dequeue address-event batch link

> **SUPERSEDED RESULT NOTICE (W3 audit fix):** The `GO_RTL`, zero-latency-
> regression, 4.45% link-gate pass, and endpoint-PPA interpretations recorded at
> commit `ac6c0b8` are invalid.  The old model exposed EF entries progressively,
> while the RTL exposes all k entries only after the terminal beat; it also hid
> a third TX bank and failed to model the RTL's K-slot marker admission rule.
> The corrected results below replace those claims.

## Decision

The explicitly selected contract is **reference follows committed RTL**.  An
EF frame becomes decoder-visible as one k-entry push only after its terminal
beat.  Its marker is admitted only with at least K free RX slots, including a
same-edge retirement.  Raw words remain progressively visible.  Exactly two TX
batch banks total are modeled, including the bank currently being serialized.

With that contract, no window passes capacity22.  Even `window=0` regresses p95
latency on `core_simultaneous_identity` and `global_fanin_identity` from 33 to
35 cycles.  Windows 1, 2, and 4 regress 21 of 22 runs.  Final decision is
**HOLD_LATENCY_OR_LINK_GATE** and `selected_gate` is null.

No endpoint PPA claim remains.  The local synthesis numbers cover only the
standalone encoder and decoder; collector, sorter, two TX banks, ownership and
launch control, integration, and request capture are excluded.

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

The cap22 simulator charges exactly two TX batch banks total and an RX capacity
of `2K`, plus one normalized retirement per cycle.  An active serializer bank
therefore cannot silently become a third buffer.  The standalone synthesized
codec modules contain 665 registered bits (121 TX, 544 RX including the
128-bit RX FIFO), but this is explicitly not full endpoint storage.
Scoreboard-only identity/time storage is excluded from hardware and never
reconstructed for free.

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
| 0 | 24560 / 24560 | 41056 / 41056 | 24369 / 24382 | 0.100000 → 0.104455 | 2 | HOLD |
| 1 | 24560 / 24568 | 41056 / 41048 | 24369 / 24373 | 0.100000 → 0.104485 | 21 | HOLD |
| 2 | 24560 / 24575 | 41056 / 41041 | 24369 / 24371 | 0.100000 → 0.104499 | 21 | HOLD |
| 4 | 24560 / 24794 | 41056 / 40822 | 24369 / 24567 | 0.100000 → 0.105414 | 21 | HOLD |

The active-link ratio remains numerically smaller for dense EF frames, but it is
not a passing gate.  At window zero, `core_simultaneous_identity` and
`global_fanin_identity` regress p95 latency 33→35 because all 16 entries become
visible only after frame completion.  `shape_b16` improves p95 55→35 and
in-window throughput about 0.49585→0.49902, but cannot erase those regressions.
Pairwise, uniform, shape_b4, phase-transition, and mixed runs mainly select raw.
The raw/codec overrun equality at window zero is capacity loss, not codec loss.

Aggregate event/cycle at window zero is 0.438670 raw versus 0.438904 coded;
events/elapsed-pin-cycle is 0.087812 versus 0.087860.  These small improvements
also do not override the per-run latency rejection.

## RTL, cost, and verification

The dedicated RTL path contains a parameterized batch encoder, fail-closed
stream decoder with `2K` FIFO, and direct lockstep TB.  The original TB exercises raw
singletons/sparse sets, k8/k16 compressed frames, 80 deterministic random
masks, randomized output backpressure, and reset mid-frame.  Exact order and no
phantom events are asserted.  A second TB consumes an independently generated
102-cycle oracle and checks, on every cycle, batch acceptance, accepted link
beat/count/data, decoder-visible head, retirement address, and occurrence-to-
retirement latency.  It proves three dense frames plus raw fallback, terminal-
beat batch visibility, and a third marker stalled until exactly K slots exist.

Twelve model tests cover randomized N16/N64 all
cardinalities, malformed/truncated streams, refire, partial timeout, provenance,
conservation, the same-cycle anti-cross-cycle invariant, and the cycle oracle.
The candidate runner regenerates and diffs the oracle, then actually builds and
runs both TBs with both Icarus and Verilator.  All four simulations pass.

Local Yosys 0.52 structural synthesis at N16/K16 gives 4,306 generic encoder
cells and 8,130 generic decoder cells.  This includes the codec RX FIFO, but not
the collector/sorter/two-bank/control path and therefore is not endpoint PPA.
It is neither mapped area nor timing.  No server was used, and the attempted
N64 elaboration is not claimed as a qualified cost result.

Reproduction:

```sh
python3 benchmarks/clean_slate_aer/a6_w3_evaluate.py sweep \
  --max-batch 16 --output /tmp/a6_w3_sweep.json
scripts/run_a6_w3_elias_fano_checks.sh
```

The cap22 command additionally requires the frozen manifest and fresh generated
event JSONL directory.  Detailed evidence is in
`reports/a6-w3-elias-fano/{sweep_n16_n64,cap22,local_synthesis}.json`.
