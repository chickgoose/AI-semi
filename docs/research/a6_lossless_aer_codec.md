# A6 Exact Lossless AER Address/Multiplicity Codec

Status: format frozen for implementation, 2026-08-07

## Scope and benchmark boundary

A6 transports the frozen N=16 coordinate-spike event stream without changing
its meaning.  The common source latch, occurrence time, and TB-only event ID
remain outside the candidate.  The candidate contains the round-robin ingress,
finite run buffer, encoder, physical link, decoder, and one-event retire lane.
Every accepted occurrence is reconstructed separately.  A run token is a link
representation, not permission to merge spikes at the normalized output.

The mandatory suite fixes polarity/type, so the codec carries the four-bit
logical source address and the binding reconstructs the frozen event identity.
It deliberately declares the optional polarity/type capability unsupported.
Extending the raw payload is parameterizable, but is not claimed by this N=16
candidate result.

## Primary literature and design position

The original AER trade is spatial pins for time-multiplexed addresses.  Qiao and
Indiveri show why parallel address width and I/O power cease to scale, and also
show that word-serial and bit-serial links pay latency/circuit overhead; their
fabricated 26-bit transceiver is therefore a physical-link reference, not a
compression result ([paper](https://arxiv.org/abs/1908.07413)).

Event-camera lossless coding confirms exploitable spatial and temporal
correlation, but most published codecs target stored `(x,y,t,p)` streams or
same-timestamp blocks.  Iqbal, Khan, and Martini compare DVS-specific spike
coding with general LZ-family codecs and explicitly report the compression-rate
versus encode/decode-speed tradeoff
([ICASSP 2020 paper](https://doi.org/10.1109/ICASSP40776.2020.9053178)).
Schiopu and Bilcu rearrange events into same-timestamp structures and use
adaptive entropy models; this is lossless and effective, but its data
reordering, model state, and block structure are a poor match for a tiny online
transport boundary
([CVPRW 2023 paper](https://openaccess.thecvf.com/content/CVPR2023W/EventVision/html/Schiopu_Entropy_Coding-Based_Lossless_Compression_of_Asynchronous_Event_Sequences_CVPRW_2023_paper.html)).
CAERPRO instead organizes a core's spikes in space and time as a binary string
and statically compresses it for a NoC; its reported codec cost and link gains
motivate charging both endpoints, but it is a batch/string protocol rather than
this occurrence-ordered streaming code
([ISCAS 2025 paper record](https://doi.org/10.1109/ISCAS56072.2025.11043210)).

Low-transition bus coding is a separate objective from fewer transmitted bits.
Chee, Colbourn, and Ling formalize off-chip energy by the number of wire
transitions and construct memoryless low-transition codes
([paper](https://arxiv.org/abs/0712.2640)).  Shifted-Gray work reports that Gray
coding is attractive for sequential addresses but its benefit falls with less
sequential traffic and decoding has area/timing cost
([paper](https://doi.org/10.1016/j.sysarc.2010.03.003)).  A6 therefore measures
actual serialized-data transitions rather than assuming that a shorter or Gray
code is automatically lower energy.

The new combination is intentionally smaller and more online than those
schemes: one previous-address dictionary entry, signed local-delta tokens,
bounded exact same-address multiplicity, and a raw escape in one prefix code.
It neither forms event frames nor changes cross-source order after arbitration,
uses no quadtree/predictive grant/adaptive routing, and never approximates or
deduplicates occurrences.  Gray coding was considered but rejected for the
first RTL: on a one-data-pin serialized link it cannot reduce simultaneous data
wire transitions, while it adds conversion logic.  Dictionary coding beyond
the one-entry previous address was rejected because N=16 index/tag state can
cost more than the saved bits and makes reset/resynchronization less local.

## Wire format

Bits are transmitted most-significant-prefix first. `A` is the four-bit source
address, `P` is the decoder's previous reconstructed address, and arithmetic is
modulo 16 only where explicitly stated.  Local deltas do **not** wrap at 0/15.

| Prefix/token | Length | Meaning and emitted occurrences |
| --- | ---: | --- |
| `0` | 1 | `SAME1`: emit one occurrence at `P` |
| `100ccc` | 6 | `RUN`: emit `unsigned(ccc)+2` occurrences at `P` (2..9) |
| `101aaaa` | 7 | `RAW`: set `P=A`; emit one occurrence at `A` |
| `110` | 3 | `DELTA+1`: require `P<15`; set `P=P+1`; emit one |
| `111` | 3 | `DELTA-1`: require `P>0`; set `P=P-1`; emit one |

After reset or resynchronization the first token must be `RAW`.  The encoder
holds at most nine consecutive equal addresses.  For a new-address run longer
than one, it emits `RAW`/`DELTA` for the first occurrence followed by `SAME1` or
`RUN` for the remaining multiplicity.  For a run equal to `P`, it emits one
`SAME1`/`RUN` token directly.  Runs beyond nine are split, preserving count.

The physical comparison uses a two-data-pin counted-valid/ready link.  A transfer
carries one or two valid prefix-stream bits and a two-bit valid-count sideband;
all four forward wires plus `ready` are charged in the fixed-pin result.  An
idle cycle is not a token.  No padding bit enters the decoder.

## Unambiguous decode and exactness proof

The codeword set is prefix-free.  `0` ends immediately.  Every other word starts
with `1`; its next two bits select exactly one of `00` (RUN), `01` (RAW), `10`
(+1), or `11` (-1), after which RUN consumes exactly three count bits and RAW
exactly four address bits.  Thus concatenation has one left-to-right parse.

Induct on parsed tokens.  The reset base has invalid history and only RAW is
legal, so both endpoints establish the same `P` and emit the same first source.
For the induction step, SAME/RUN repeat the shared `P` exactly the encoded
positive number of times, DELTA applies the same checked operation to shared
`P`, and RAW supplies the address literally.  Encoder run splitting partitions
each accepted run into positive counts whose sum is unchanged.  The decoder's
retire sequencer emits one handshake per decoded occurrence before consuming a
later decoded event.  Therefore address sequence, occurrence count, and
source-local order are invariant.  Cross-source order is the encoder's simple
round-robin acceptance order and is not subsequently reordered.

## Expansion, buffering, reset, and resynchronization

For a four-bit raw address baseline, the bounded-run best case is `6/9` bit/event
for a nine-event repeat run and the common single-repeat case is 1 bit/event.  A local
delta is 3 bits/event.  A RAW event is 7 bits/event, so worst-case expansion is
`7/4 = 1.75x`; an alternating nonlocal/random stream can approach it.  This is
why uniform random is a mandatory reject/control family.  A first non-repeated
run with repeats costs `7 + repeat-token bits`; no hidden end marker is charged.

The encoder owns one current `(address,count)` run (8 state bits at N=16), one
previous address plus valid bit, and a bounded 13-bit token serializer (worst
compound token: 7-bit RAW plus 6-bit RUN).  The decoder owns a bounded prefix
buffer, previous address/valid, and a four-bit remaining-multiplicity counter.
The top-level one-lane retire register supplies output stability.  There is no
unbounded TB or RTL queue.  Backpressure propagates from decoder to link to
encoder; a full run/serializer deasserts only the selected source's acceptance.

Active-low reset invalidates both history states, clears partial tokens, clears
run/multiplicity state, and suppresses retire valid, so pre-reset bits cannot
become a phantom event.  The base candidate assumes reset is the link
resynchronization mechanism.  An out-of-band reset discards an incomplete
token at both endpoints and forces the next token to RAW.  In-band corruption
detection/CRC is out of scope and is not claimed; without it, a dropped bit can
desynchronize any variable-length prefix stream.

## PPA boundary and area accounting

Both endpoints, the run/token state, two-bit serializer, valid-count sideband,
decoder prefix/multiplicity state, round-robin pointer, and normalized retire
register are synthesizable candidate logic.  The initial implementation report
will give endpoint register bits and local open-source synthesis cell counts.
Server Genus/Innovus PPA is explicitly not run without approval.  Final physical
comparison must include encoder **and** decoder, not quote compression logic
alone, and must compare the same clock/load/activity window.

## Metrics and fixed-pin comparison

For every frozen trace the evaluator reports token histogram, exact input and
decoded counts/SHA, compressed bits, `bits/event`, `raw_bits/compressed_bits`,
RAW escape ratio, two-pin data cycles, events/data-pin-cycle, data-wire toggles
per event, and all charged-link toggles per event.  The RTL/common TB adds
generated/overrun/accepted/delivered, occurrence-to-retire and
accept-to-retire latency, fixed-window throughput, and drain/timeout behavior.
Raw fixed-pin controls serialize each four-bit address over the same two data
pins and apply the same valid/count/ready accounting.

## Predeclared rejection criteria

A6 is rejected if any accepted occurrence is lost, duplicated, corrupted, or
source-locally reordered; reset creates a phantom; a decoder ambiguity exists;
or storage is omitted from the PPA boundary.  It is also rejected as a useful
codec candidate if any of the following holds after all 46 traces are reported:

- geometric-mean compressed bits/event is not below the four-bit raw address;
- local, retrigger, and elephant/mouse families show no link-bit benefit;
- uniform-random mean expansion exceeds the proven 1.75x bound or is hidden;
- fixed-pin events/pin-cycle fails to improve on its favorable families;
- codec-induced saturation causes more source overrun or materially worse
  end-to-end tail latency without a compensating link-energy/throughput gain;
- encoder plus decoder exceeds the candidate PPA boundary agreed for the
  clean-slate screen, or fails timing in the common flow.
