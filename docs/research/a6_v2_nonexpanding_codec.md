# A6 v2 Non-Expanding Block/Raw-Bypass Study

Status: lower-bound gate passed for B=16 RTL exploration, 2026-08-07

## Requirement and frozen boundary

V2 stays on the exact address/multiplicity codec axis.  It may buffer a bounded
block, choose a token or small block dictionary representation, or transmit the
four-bit addresses literally.  It may not change the frozen occurrence trace,
source latch, scoreboard, or accepted-event semantics.  Every accepted address
must emerge once and in source-local order.  No load-adaptive datapath, routing,
grant prediction, quadtree, compactor, calendar, or token-network mechanism is
part of the proposal.

The hard v2 data bound for every selected block is

```text
transmitted_data_bits(block) <= 4 * event_count(block).
```

This is a per-block bound, not an average claim. Uniform/adversarial blocks must
take RAW and remain exactly four data bits/event.

## Why an ordinary in-band RAW escape cannot meet the bound

Let `L=4B`.  There are `2^L` possible raw B-event blocks.  A fixed-length
injective code already consumes all `2^L` L-bit strings.  A self-delimiting
prefix code assigning every block at most L bits cannot shorten even one block:
the `2^L` length-L leaves already make the Kraft sum one, and shortening any
leaf makes it exceed one.  Equivalently, a one-bit in-band RAW/COMPRESSED header
makes the RAW path `L+1`, immediately violating the bound.  Its amortized lower
bound is `1/B` bit/event: 0.25, 0.125, 0.0625, and 0.03125 for B=4/8/16/32.

Exact non-expansion therefore needs an externally observable block boundary or
an uncharged mode sideband. V2 does **not** assume a free sideband. It uses one
deliberate `link_count=0` delimiter cycle on the existing two-data-pin,
two-count-pin, ready link. The data length observed at that boundary carries the
mode, while delimiter cycles and all control toggles are charged in fixed-pin
results.

## Proposed framed representation

For a block of `n<=16` addresses:

1. RAW is their 4n-bit concatenation and has length `0 mod 4`.
2. Token candidate is subtype `0` followed by the v1 prefix stream.
3. A full 16-event block may also use subtype `1`, `k-1[3:0]`, k literal
   four-bit dictionary entries in first-use order, then 16 fixed-width indices.
   Partial blocks cannot use dictionary mode, so no event-count header exists.
4. Each compressed payload appends `p` zero bits and a two-bit `p`, choosing
   `p in 0..3` so total compressed length is `1 mod 4`.
5. The encoder selects the shortest candidate only when its final padded length
   is strictly less than 4n; otherwise it sends RAW. Token subtype uses `0` for
   one occurrence equal to previous, `101aaaa` RAW, and `110`/`111` for checked
   non-wrapping +/-1. Repeated occurrences remain individual decoded events.

On the delimiter, length `0 mod 4` is RAW and determines `n=length/4`; length
`1 mod 4` is compressed.  A compressed decoder reads the final two-bit pad
count, verifies/removes the zero pad, then decodes the subtype. Other length
residues, bad padding, invalid dictionary indices, truncated tokens, or a token
count above 16 are errors. This makes mode and event multiplicity exact without
an in-band RAW header. Reset discards an incomplete block and history; the next
token-mode address must be RAW inside its compressed payload.

The framing condition is essential: without an observable delimiter the code
is impossible under the stated bound. B=16 requires two 64-bit block buffers
for simultaneous fill/drain if one-address/cycle ingress is to be sustained,
plus at most 64 encoded bits, dictionary tags/indices, previous-address state,
and decoder block storage. A single buffer is legal but necessarily stops
ingress throughout serialization. These state and pin-cycle costs must be
included in endpoint PPA and throughput results.

## Frozen-trace entropy and run evidence

The analyzer reads all 46 frozen JSONL files (87,000 occurrences) and reports
empirical `H0(address)`, `H(address_i|address_i-1)`, equal-address repeat
fraction, run lengths, and block choices. Entropy is a lower bound for an
ideal model, not a promise that this small codec reaches it.

| Family | mean H0 | mean H1 | repeat fraction | B16 data b/e | B16 RAW-block ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| uniform | 3.989 | 3.799 | 0.054 | 4.000 | 1.000 |
| matched spatial | 2.000 | 0.811 | 0.000 | 3.562 | 0.000 |
| moving hotspot | 3.997 | 3.008 | 0.338 | 3.665 | 0.684 |
| retrigger | 3.910 | 1.439 | 0.756 | 2.656 | 0.000 |
| elephant/mouse | 1.521 | 1.443 | 0.635 | 3.097 | 0.222 |
| timing pair | 3.993 | 3.856 | 0.055 | 4.000 | 1.000 |
| phase transition | 3.997 | 3.938 | 0.043 | 4.000 | 1.000 |

Uniform, timing-pair, phase-transition, and rotating-victim traffic correctly
bypass at exactly 4 b/e. The matched local set becomes dictionary-friendly even
though it has no identical runs. Retrigger and elephant/mouse benefit from run
tokens, and moving hotspot has a smaller but still visible 7.4% mean data-bit
reduction.

## Block-length and break-even decision

All values include compressed subtype and 2..5-bit pad/footer overhead, but not
the separately charged one-cycle delimiter.

| B | all-trace weighted b/e | RAW-block ratio | retrigger | matched spatial | moving hotspot |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 3.929 | 0.943 | 3.117 | 4.000 | 3.708 |
| 8 | 3.903 | 0.894 | 2.961 | 4.000 | 3.668 |
| 16 | 3.854 | 0.792 | 2.656 | 3.562 | 3.665 |
| 32 | 3.801 | 0.784 | 2.500 | 2.781 | 3.668 |

B=8 cannot improve the required local control. B=32 improves local coding but
doubles core block storage for only 0.047 weighted b/e beyond B=16 and slightly
worsens hotspot/elephant behavior. B=16 is the smallest tested block that puts
retrigger, local, and hotspot below 4 b/e while making every uniform block RAW.
It therefore passes the pre-RTL information gate.

This is not yet an architectural win. At B=16 a block saving must also recover
one delimiter cycle and the area/toggle cost of two endpoints. On a two-data-pin
link, RAW takes 32 active cycles; a compressed block must use at most 30 data
cycles to beat RAW after its delimiter. The RTL evaluation must reject v2 if
the exact framed stream, buffering stalls, or endpoint cost erases the apparent
data-bit gains.

Machine-readable evidence is in
`reports/a6-lossless-aer-codec/v2_entropy_blocks.csv` and
`reports/a6-lossless-aer-codec/v2_entropy_summary.json`.
