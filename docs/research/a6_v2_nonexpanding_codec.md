# A6 v2 Non-Expanding Block/Raw-Bypass Study

Status: RTL evaluated and rejected as an end-to-end candidate, 2026-08-07

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

## Synthesizable implementation and exact gates

The candidate RTL implements the frozen B=16 format in both directions. The
encoder incrementally forms RAW and token streams while collecting the block
dictionary; its choice is the fixed strict minimum of the three final lengths.
It does not observe offered load or select a different datapath by workload. A
constant 16-cycle idle timeout flushes the final partial block. The decoder
classifies only at the charged delimiter, validates residue/footer/padding and
dictionary bounds, parses one symbol per cycle, and retires every decoded
occurrence separately.

The finite storage bound is 64 raw bits, 64 retained token bits, 64 dictionary
literal bits, 64 dictionary-index bits, and a 64-bit serializer at the encoder;
the decoder has a 64-bit receive buffer, 64-bit decoded-output buffer, and
64-bit dictionary store. Counters, history, arbiters, and error state are in
addition. Local generic synthesis reports 624 endpoint flip-flop bits, which is
consistent with these buffers and control state. This single-block
implementation deasserts ingress while preparing, sending, parsing, and
retiring; sustaining one accepted address/cycle would require ping-pong
encoder and decoder buffers and would increase the state bound further.

Two independent gates passed:

- 320-event RTL round-trip with random source gaps and decoder backpressure;
- malformed compressed `SAME` before history, which raises sticky
  `decode_error` and emits no event.

Across all 46 trace runs, an independent software decoder reconstructed the
global accepted-address sequence from the observed RTL link: zero mismatch,
zero expanding blocks, and 24,147 accepted equals 24,147 decoded. Reset clears
both histories and discards an incomplete block. A delimiter restores block
alignment, but a malformed block may invalidate inter-block history; sticky
error therefore requires an external reset before normal service resumes. No
silent automatic resynchronization is claimed.

## Actual 46-trace link and system result

The following numbers are measured from the candidate-only replacement and
the unchanged common scoreboard. `b/e` counts data bits, `escape` is the RAW
block fraction, `ev/pin-cycle` divides accepted events by five physical pins
times data-plus-delimiter cycles, and toggles include both data and count/ready
controls. A compact RAW comparator on the same two data pins takes two cycles
per event, hence 0.100 event/pin-cycle; its sequence-dependent toggle proxy is
shown separately.

| Family | accepted/offered | overrun | b/e | escape | ev/pin-cycle | codec/raw toggle/e | avg latency | throughput |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| retrigger | 733/1,024 | 28.4% | 3.434 | 0.043 | 0.1104 | 1.842/2.014 | 88.7 | 0.179 |
| local/dispersed | 1,404/4,608 | 69.5% | 3.566 | 0.033 | 0.1065 | 2.753/1.840 | 106.1 | 0.216 |
| moving hotspot | 2,578/9,111 | 71.7% | 3.944 | 0.879 | 0.0981 | 2.258/1.996 | 108.1 | 0.238 |
| elephant/mouse | 1,018/3,654 | 72.1% | 3.912 | 0.785 | 0.0987 | 2.188/2.018 | 100.2 | 0.238 |
| timing pair | 1,029/2,538 | 59.5% | 3.854 | 0.600 | 0.0999 | 2.449/2.021 | 122.2 | 0.237 |
| rate shape | 3,120/6,144 | 49.2% | 3.566 | 0.005 | 0.1066 | 2.616/2.004 | 129.4 | 0.246 |
| uniform | 10,232/44,621 | 77.1% | 3.701 | 0.326 | 0.1025 | 2.547/2.002 | 122.7 | 0.223 |

Overall accepted-stream data is 3.705 b/e with a 0.350 RAW-block fraction, but
this number must not be mistaken for offered-stream compression: only 24,147
of 87,000 offered occurrences were accepted and 62,853 overran at the frozen
source boundary. In particular, the information-level arbitrary-uniform test
is exactly 4.000 b/e and 100% RAW. The lower b/e seen for accepted high-load
uniform traces is selection after 77.1% overrun, not a codec advantage and not
evidence against the adversarial RAW bound.

Delimiter cost reverses small bit savings: moving hotspot and elephant/mouse
fall below 4 data b/e yet achieve less than the RAW comparator's 0.100
event/pin-cycle. Toggle energy also usually worsens; only retrigger reduces the
measured toggle proxy. Worst physical expansion is a one-event RAW partial
block: two data cycles plus one delimiter versus two RAW cycles, or 1.5x link
and pin cycles, even though its data length remains exactly four bits.

## Endpoint cost and rejection

Local Yosys 0.52 generic synthesis, used only as a structural screen, gives:

| Boundary | generic cells | flip-flop bits |
| --- | ---: | ---: |
| encoder | 11,765 | 373 |
| decoder | 4,378 | 251 |
| encoder + decoder | 16,143 | 624 |
| full candidate including RR ingress/binding top | 16,565 | 628 |

This is roughly 12.5x the v1 full candidate's 1,330 generic cells. It includes
both endpoints and all codec buffers; the passive measurement observer is
excluded. These are not signoff PPA numbers. Per instruction, no server,
Genus, Innovus, or remote synthesis was run.

V2 is therefore **rejected** despite satisfying exact round-trip and the
per-selected-block data non-expansion theorem. It misses the useful-candidate
bar for three independent reasons: 72.2% aggregate overrun, 80--130-cycle
typical latency from fill/serialize/parse/retire, and endpoint area/toggle cost
far larger than the modest link savings. Hotspot also fails to beat RAW in
fixed-pin throughput after delimiter cost.

The measured break-even conditions for reconsideration are explicit: at least
B=16 is needed for the tested local dictionary case; each full block must use
at most 60 data bits to beat RAW's 32 two-bit cycles after one delimiter; one
address/cycle ingress requires at least ping-pong 64-bit buffers at both
endpoints; and either a separately budgeted framing pin or a delimiter is
logically necessary for the no-expansion guarantee. A future implementation
would additionally need a much smaller dictionary/index datapath and a
decoupled decoder retirement path. Those changes expand the state/area budget
and do not change the counting impossibility for a free in-band escape.

Actual per-run evidence is in
`reports/a6-lossless-aer-codec/v2_rtl_trace_metrics.csv`, aggregate evidence in
`reports/a6-lossless-aer-codec/v2_rtl_summary.json`, and local synthesis counts
in `reports/a6-lossless-aer-codec/v2_local_synthesis.json`.
