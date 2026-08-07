# A6 v3 Exact Codec Final Break-Even Gate

Status: final design-space gate complete; no v3 RTL added, 2026-08-07

## Scope and fair comparator

This final study changes neither the v1 nor v2 result. It explores only fixed
exact-codec points `B={4,8,16,32}` and physical data width `W={1,2,4}`. Every
codec block retains the v2 non-expanding selector, so its data length is never
greater than the four-bit RAW block. There is no load observation, adaptive
path, event drop/coalescing, reservoir, predictor, or mechanism from another
track.

At each point both codec and RAW reference receive the same optimistic
ping-pong transport:

- two four-bit event banks at the encoder and two at the decoder;
- one externally visible delimiter cycle per block;
- `W` data pins, `ceil(log2(W+1))` valid-count pins, and one ready pin;
- one accepted event/cycle ingress and one exact occurrence/cycle retirement;
- zero-cycle codec selection and decoding, an intentional lower bound;
- fixed `B`-cycle partial-block timeout, with the final block flushed at drain.

The equalized storage charge is `16B+10` bits for each design point: `16B` for
four ping-pong banks across both endpoints and ten bits reserved equally for
history/state. RAW is charged the otherwise unused allowance. Codec logic is
still a strict superset of RAW framing/control because it must compute candidate
lengths and reconstruct compressed symbols; the model does not pretend that
equal storage makes that logic free.

## Analytical break-even conditions

A full block with `L` selected data bits consumes

```text
S(B,W,L) = ceil(L/W) + 1 delimiter cycles.
```

It improves fixed-pin efficiency over equal RAW only when
`ceil(L/W) < ceil(4B/W)`. Ping-pong can sustain one accepted event/cycle only
when `S<=B`; therefore the codec needs `L<=W(B-1)`. RAW always has
`ceil(4B/W)+1>B` for W<=4, so no finite B RAW point can sustain a permanent
one-event/cycle stream while a delimiter is charged. W=4 codec can cross the
condition by saving at least one four-bit word; W=1/2 require much stronger
compression. Arbitrary uniform blocks select `L=4B`, so no tested point can
guarantee zero overrun at unit offered rate.

The simulator applies these fixed rules to all 46 frozen traces and preserves
the frozen one-pending-occurrence-per-source overrun semantics.

## Aggregate 46-trace matrix

All 87,000 offered occurrences were evaluated at all 12 points. `pin` is the
complete forward-data/count plus ready budget, `store` is the identical charged
encoder+decoder storage, and latency is occurrence-to-retirement cycles. The
codec is given zero-cycle encode/decode and in-place buffers; these are
optimistic lower bounds, not an RTL timing claim.

| B/W | pin | store (bits) | RAW accepted | RAW overrun | RAW ev/pin-cyc | RAW lat | codec accepted | codec overrun | codec ev/pin-cyc | codec lat | codec b/e |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4/1 | 3 | 74 | 23,292 | 73.2% | 0.07806 | 71.0 | 23,428 | 73.1% | 0.07886 | 70.3 | 3.958 |
| 4/2 | 5 | 74 | 41,211 | 52.6% | 0.08843 | 32.4 | 41,418 | 52.4% | 0.08918 | 32.2 | 3.951 |
| 4/4 | 8 | 74 | 65,011 | 25.3% | 0.09931 | 13.6 | 65,345 | 24.9% | 0.10001 | 13.4 | 3.915 |
| 8/1 | 3 | 138 | 23,988 | 72.4% | 0.08044 | 99.5 | 24,832 | 71.5% | 0.08342 | 95.1 | 3.853 |
| 8/2 | 5 | 138 | 43,519 | 50.0% | 0.09366 | 46.4 | 44,223 | 49.2% | 0.09557 | 45.5 | 3.883 |
| 8/4 | 8 | 138 | 69,748 | 19.8% | 0.11048 | 20.7 | 69,890 | 19.7% | 0.11202 | 20.6 | 3.882 |
| 16/1 | 3 | 266 | 24,774 | 71.5% | 0.08195 | 160.3 | 26,445 | 69.6% | 0.08894 | 147.6 | 3.681 |
| 16/2 | 5 | 266 | 44,942 | 48.3% | 0.09683 | 77.3 | 46,348 | 46.7% | 0.10142 | 72.0 | 3.784 |
| 16/4 | 8 | 266 | 71,993 | 17.2% | 0.11745 | 37.3 | 72,056 | 17.2% | 0.12099 | 36.9 | 3.827 |
| 32/1 | 3 | 522 | 25,624 | 70.5% | 0.08264 | 280.9 | 26,935 | 69.0% | 0.08890 | 262.2 | 3.717 |
| 32/2 | 5 | 522 | 45,821 | 47.3% | 0.09841 | 139.2 | 48,039 | 44.8% | 0.10540 | 129.4 | 3.716 |
| 32/4 | 8 | 522 | 73,026 | 16.1% | 0.12114 | 71.9 | 73,106 | 16.0% | 0.12738 | 70.4 | 3.771 |

The B=16/W=2 overlap lower bound illustrates what ping-pong can and cannot
recover. Relative to measured v2 RTL it raises accepted events from 24,147 to
46,348 and lowers weighted latency from 119.8 to 72.0 cycles. Most of that is
removal of v2's single-buffer and sequential-codec stalls: an equal ping-pong
RAW endpoint already accepts 44,942 with 77.3-cycle latency. Compression's
incremental contribution is only 1,406 acceptances and 5.3 cycles, before any
real encode/decode delay or logic cost is restored.

## Family behavior at the strongest-width points

W=4 is the storage/latency/pin-efficiency frontier for each tested B in this
optimistic storage-only model. Favorable families do compress, but uniform and
timing-pair controls remain RAW-like as required.

| Family | B16/W4 RAW→codec ev/pin-cyc | B16/W4 RAW→codec latency | B32/W4 codec b/e | B32/W4 overrun |
| --- | ---: | ---: | ---: | ---: |
| retrigger | 0.1176→0.1616 | 48.5→43.9 | 2.500 | 0.0% |
| local/dispersed | 0.1176→0.1250 | 35.5→34.5 | 2.781 | 0.0% |
| moving hotspot | 0.1176→0.1257 | 33.3→32.5 | 3.667 | 0.0% |
| elephant/mouse | 0.1176→0.1433 | 33.2→31.3 | 3.142 | 0.0% |
| timing pair | 0.1176→0.1176 | 36.8→36.8 | 4.000 | 0.7% |
| rate shape | 0.1176→0.1224 | 39.5→38.8 | 3.605 | 0.0% |
| uniform | 0.1174→0.1175 | 38.5→38.4 | 3.982 | 25.3% |

The small apparent uniform gain is caused by candidate-dependent accepted
subsequences after overrun. The arbitrary-block gate still maps every
unfavorable uniform block to exactly `4B` RAW bits; it makes no average-only
expansion claim.

## Irrecoverable overrun and endpoint-cost proof

An additional upper bound removes the link, framing, block fill, encoder,
decoder, and retirement stalls entirely while retaining the frozen single
ingress and one pending occurrence per source. Even this ideal lane accepts
only 73,878/87,000 and overruns 13,122 (15.08%). Therefore no B/W choice or
ping-pong overlap in this track can eliminate overrun. B32/W4 reaches 73,106,
only 772 below that bound; its remaining 16.0% overrun is not primarily a codec
problem that another block size can solve.

Endpoint-cost dominance is also impossible under the equal comparator. RAW can
use exactly the same banks, count pins, ready, delimiter, and ping-pong control
while omitting token/dictionary selection, history update, compressed parsing,
and error checks. Thus codec endpoint logic is a strict superset of RAW at every
point. Equalized storage ties the `74/138/266/522`-bit storage term but cannot
make the extra logic zero-area or zero-energy. The prior B16 RTL measurement
(16,143 generic endpoint cells and 624 FF bits) further shows that the optimistic
266-bit in-place storage bound is not a plausible cost win.

Within codec-only storage/latency/pin metrics, `B4/W4`, `B8/W4`, `B16/W4`, and
`B32/W4` form a tradeoff frontier: larger B monotonically buys pin efficiency
while increasing storage and latency. None simultaneously minimizes endpoint
cost and latency while maximizing event/pin-cycle. Against its equal RAW
reference, every codec point has strictly higher endpoint logic cost; hence no
point simultaneously passes event/pin-cycle, latency, and endpoint-cost.

## Final decision and required conditions

No fixed-format v3 RTL variant is added. This is not a claim that compression
never saves link cycles; the matrix shows where it does. It is the narrower
result requested by this gate: no tested fixed `(B,W)` is an unambiguous
end-to-end Pareto pass once equal RAW framing/storage and endpoint logic are all
charged.

A future point would need all of the following before RTL is justified:

1. full-block `L<=W(B-1)` often enough to hide the delimiter and sustain the
   encoder pipeline;
2. enough buffering or more than one ingress/retire lane to address the 15.08%
   irreducible single-lane overrun floor—outside this codec-only scope;
3. measured encoder+decoder logic no greater than the equal RAW endpoint, which
   is structurally impossible for a nontrivial exact compressor unless some
   other charged system logic is removed;
4. a specified workload weighting that accepts the explicit latency/storage
   trade along the W=4 frontier rather than claiming simultaneous dominance.

Machine-readable results are
`reports/a6-lossless-aer-codec/v3_break_even_matrix.json` and the complete
1,104-row trace/RAW/codec table is
`reports/a6-lossless-aer-codec/v3_break_even_trace_points.csv`.
