# A6 v3 Exact Codec Final Break-Even Gate

Status: comparison model frozen; v1/v2 rejection remains unchanged, 2026-08-07

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
the frozen one-pending-occurrence-per-source overrun semantics. Results will be
added only after model tests and conservation/non-expansion gates pass.
