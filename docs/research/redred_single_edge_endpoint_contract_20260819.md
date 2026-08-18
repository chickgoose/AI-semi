# REDRED A2/A3 single-edge endpoint architecture contract

Date: 2026-08-19
Normative RTL: commit `7286913f9f1dc771bde13fa51124b0d31aedd068`

This document describes the RTL bytes in commit `7286913` only. The two
complete tops are:

- `a2_batched_iwrr_single_edge_top`
- `a3_exact_scalar_prefix_k2_single_edge_top`

Both begin at the synchronous 16-source pending/accept boundary and end at an
always-ready synchronous two-lane retirement boundary. Both instantiate the
same `w2_single_edge_exact_pair_endpoint`, comprising a registered TX and a
registered RX clocked only by rising edges of the same `clk_i`. There is no
DDR behavior, falling-edge state, forwarded or generated clock, clock gate,
or vendor primitive in the new transport RTL.

## Atomic record and 9-wire encoding

One accepted scheduler bundle is one indivisible ordered record with count one
or two. Lane 0 is older/first; lane 1 exists only for count two. No lane has an
independent ready or commit.

The physical-functional link vector is exactly nine data wires:

```text
{link_valid, link_addr0[3:0], link_addr1[3:0]}
```

Its legal encoding is:

| Semantic record | `link_valid` | `link_addr0` | `link_addr1` |
|---|---:|---:|---:|
| idle | 0 | 0 | 0 |
| singleton `a` | 1 | `a` | `a` |
| ordered pair `(a,b)`, `a != b` | 1 | `a` | `b` |

The semantic alphabet contains exactly:

```text
1 idle + 16 singletons + (16 * 15) ordered distinct pairs = 257 records.
```

The legal wire alphabet also contains exactly one valid-zero codeword plus all
256 valid-one address combinations. Equality maps those 16 combinations to
singletons; inequality maps the other 240 combinations to ordered pairs.
Therefore the table is a bijection between the 257 semantic records and the
257 legal codewords. The other 255 of the 512 possible 9-bit values have
`link_valid=0` with nonzero address payload and are illegal. Nine is the
information-theoretic minimum width because `ceil(log2(257)) = 9`.

On a singleton, the TX ignores `input_addr1_i` and emits a second copy of
`input_addr0_i`. The RX converts that equality code back to
`retire_valid_o=2'b01`, `retire_addr0_o=a`, and a zeroed
`retire_addr1_o`. A distinct pair converts to `retire_valid_o=2'b11` with
both addresses in their original order.

## Edge and latency contract

All handshakes and retirement are defined at rising edges. Let `E[N]` be the
rising edge on which a synchronous consumer samples the pre-NBA values. A
registered output assigned at `E[N]` is visible during the cycle after that
edge and is consumed by another synchronous block at `E[N+1]`.

The reusable transport behaves as follows:

1. On endpoint commit edge `E[N]`, TX registers the encoded link cell.
2. On `E[N+1]`, RX samples that TX cell and registers the decoded retire
   record.
3. The always-ready synchronous retire boundary consumes that registered
   record on `E[N+2]`.

Thus endpoint input commit to synchronous retirement is exactly two cycles.
The retire values are visible after `E[N+1]`; observing them after that edge
must not be mislabeled as a same-edge synchronous transfer.

### A3 latency

A3's owner offer is already registered. Its `endpoint_commit` is also the
source acceptance indication used by `source_accept_o` and
`accept_count_o`.

| Edge | A3 action for record R |
|---|---|
| `E[N]` | R accepts atomically; TX registers R |
| `E[N+1]` | RX samples R; registered retire R becomes visible after the edge |
| `E[N+2]` | synchronous retire boundary consumes R |

A3 source accept to retire is therefore exactly two cycles.

### A2 latency and charged buffer

A2's offer is combinational and its policy update depends on `bundle_ready`.
The A2 top consequently places one charged atomic register between scheduler
acceptance and the common endpoint. This prevents an endpoint-validation path
from forming a scheduler combinational loop.

| Edge | A2 action for record R |
|---|---|
| `E[N]` | R accepts at the source boundary and enters the A2 bundle buffer |
| `E[N+1]` | endpoint consumes the buffer; TX registers R |
| `E[N+2]` | RX samples R; registered retire R becomes visible after the edge |
| `E[N+3]` | synchronous retire boundary consumes R |

A2 source accept to retire is therefore exactly three cycles.

## Capacity and back-to-back behavior

The TX and RX each process one complete record per rising edge. With legal
input and `link_enable_i=1`, the transport admits one atomic record every
cycle. Consecutive records may keep link valid and retire valid asserted on
consecutive cycles; each edge denotes a new record even when valid does not
fall between records.

A3 can accept and launch one owner record per cycle. A2 first fills its
11-bit bundle buffer; in steady state the old buffered record launches while
the next complete scheduler record replaces it on the same edge. The buffer
adds latency but no steady-state record bubble. Capacity is therefore one
record/cycle and, because a record contains one or two events, at most two
events/cycle. Retirement is always-ready: there is no retire-ready input,
receiver queue, retry, or partial-pair flow control.

## `link_enable_i` behavior

`link_enable_i` is synchronous admission backpressure, not a clock-enable or
clock gate.

- In the reusable TX it makes `input_ready_o=0`, so no new endpoint commit is
  made while disabled.
- A TX cell launched before disable still reaches RX and retirement. On a
  disabled cycle with no commit, TX writes the legal idle cell; RX continues
  clocking normally.
- A3 feeds the disabled ready back to its registered owner. The unaccepted A3
  offer remains held; it is not part of accepted-event conservation until a
  later enabled commit.
- A2 forces `scheduler_ready=0` while disabled. An already accepted record in
  the A2 pre-TX bundle buffer remains there and cannot drain until re-enabled.
  An unaccepted scheduler offer may also be held by A2 without advancing its
  policy.

Consequently, lowering `link_enable_i` does not by itself guarantee complete
top-level drain. To quiesce losslessly, keep the link enabled until the
complete top asserts `drain_idle_o`, then disable it.

## Exact RTL state-bit inventory

These counts are source-level sequential bits in commit `7286913`, before any
synthesis optimization. Combinational wires and the externally owned
`source_pending_i` storage are not counted.

### Common single-edge transport: 21 bits

| State | Bits |
|---|---:|
| TX sticky protocol error | 1 |
| TX registered link valid | 1 |
| TX registered link addresses | 8 |
| RX registered retire valid | 2 |
| RX registered retire addresses | 8 |
| RX sticky protocol error | 1 |
| **Total** | **21** |

### A2 complete top: 54 bits

| State | Bits |
|---|---:|
| A2 token cursor | 4 |
| Four A2 two-bit row pointers | 8 |
| A2 hold and hold-two flags | 2 |
| A2 held ordered addresses | 8 |
| A2 charged buffer valid/count/addresses | 11 |
| Common single-edge transport | 21 |
| **Total** | **54** |

The A2 owner subtotal is 22 bits and the wrapper-owned charged buffer is
`1 + 2 + 4 + 4 = 11` bits.

### A3 complete top: 55 bits

| State | Bits |
|---|---:|
| A3 live center/peripheral/column/round policy states | 12 |
| A3 held bundle post-center/post-peripheral/post-column/post-round states | 12 |
| A3 registered grant count and two ordered addresses | 10 |
| Common single-edge transport | 21 |
| **Total** | **55** |

The A3 owner subtotal is 34 bits. Its registered owner offer is already
charged owner state, so the A3 wrapper adds no extra pre-TX buffer.

## Reset, drain, and accepted-event conservation

`rst_i` is active-high and synchronous everywhere in the new tops and
transport. State changes only when reset is sampled at a rising edge.

On a sampled reset edge:

- A2 resets its policy/hold state, clears its 11-bit bundle buffer, and clears
  TX, RX, retire, and sticky transport errors.
- A3 resets its canonical policy and registered offer state and clears TX, RX,
  retire, and sticky transport errors.
- no new source or endpoint commit occurs because ready is suppressed while
  `rst_i=1`.

Reset is an abort, not a drain. Any event already accepted into the A2 buffer,
TX stage, or RX/retire stage is discarded. A3 has no accepted pre-TX buffer,
but accepted TX/RX work is likewise discarded. Therefore
`accepted == retired` is guaranteed across reset only when the environment
waits for complete-top `drain_idle_o=1` before asserting reset. The environment
must also clear or reset its external pending latches; this RTL does not own
them.

The reusable endpoint declares drain solely when TX link valid is zero and RX
retire valid is zero. The complete tops strengthen that condition:

- A2 additionally requires owner `scheduler_idle`, no buffered record, and
  zero current scheduler count. A2 `scheduler_idle` includes zero external
  pending bits and no held offer.
- A3 additionally requires zero external pending bits and zero registered
  owner grant count.

Sticky `protocol_error_o` is not part of either drain equation, so a drained
endpoint may still report an earlier error. After a sampled reset edge, drain
is true only if the external `source_pending_i` bitmap is also zero.

## Protocol errors

The common TX recognizes these scheduler-side shape errors:

- count zero with either input address nonzero;
- count two with equal addresses;
- count three.

Such an input receives no endpoint-ready/commit. Count one is legal for every
`input_addr0_i`; its unused `input_addr1_i` is deliberately ignored. TX error
is visible immediately from the combinational shape check and becomes sticky
after a non-reset sampling edge.

The RX recognizes exactly one wire-side malformed class: link valid zero with
either address nonzero. It emits no retirement for that cell and sets a sticky
error. Every valid-one address combination is a legal singleton or pair, so a
legal-to-legal bit corruption cannot be detected by this encoding.

The complete tops OR transport errors with owner-boundary checks:

- A2 checks count three, an equal-address count-two offer, disagreement between
  scheduler bitmap and ordered-address-derived bitmap, and committed policy
  microstep disagreement.
- A3 checks count three, an equal-address count-two offer, and committed policy
  microstep disagreement.

Transport TX/RX errors are sticky until reset. The wrapper-only owner checks
and microstep comparison are combinational indications. In particular, A2's
scheduler-shape indication is diagnostic and is not in its scheduler-ready
equation; the implementation assumes the committed A2 owner is legal. A
bitmap-only A2 owner fault is not repaired by the transport. Thus
`protocol_error_o` is neither a CRC nor a universal recovery/fail-closed
guarantee.

## Endpoint PPA boundary and pins

The charged complete-endpoint PPA boundary includes:

- the selected A2 or A3 scheduler and all its policy/held-offer state;
- A2's 11-bit charged bundle buffer when A2 is measured;
- single-edge TX encode/register/error logic;
- single-edge RX register/decode/error logic;
- accept, retire, protocol-error, and drain control logic.

The external event generator, external per-source pending latches, retire
consumer, testbench/scoreboard, coordinate processing, pads, package, channel,
and external clock-generation/distribution network are outside this RTL
boundary unless a later physical top explicitly adds and charges them.

At the implemented link seam there are nine functional data nets and zero
forwarded-clock nets. `clk_i` is one separate shared top-level clock input used
by scheduler, TX, and RX; there is no link clock output and no `sample_clk_i`.
Each complete RTL top has 19 input bits (16 pending plus clock, reset, and link
enable) and 47 output bits, of which nine are the exposed link data outputs.

The current endpoint internally connects those nine TX outputs back to its RX
inputs while also exposing them at the top. It does not instantiate independent
receive pads. A physically separated implementation would require nine TX
signal terminals and nine RX signal terminals for the data conductors, plus
whatever shared-clock terminals and distribution the system architecture
requires. Those pad-terminal and clock-tree counts are not implemented or
qualified here; the phrase "9-wire link" refers only to the nine functional
data conductors.

## Mutation qualification checklist

The following mutations must be killed before issuing an independent canonical
single-edge digital receipt. The oracle is the ordered queue of source
acceptance records, not aggregate counts alone.

| Mutation | Required fault injection | Required detection |
|---|---|---|
| drop | suppress a committed TX valid cell or one RX retire record | accepted head remains missing; drain/conservation or timeout fails |
| duplicate | replay a TX cell or emit the same RX record twice | duplicate/phantom retirement and queue/count mismatch |
| swap | exchange pair addresses in TX, link, or RX | first ordered-address mismatch, even though the bitmap/count is unchanged |
| equality decode | invert/bypass equality or stop duplicating a singleton address | singleton becomes a pair or distinct pair becomes a singleton; count/identity mismatch |
| reset phantom | retain/seed TX valid, RX retire valid, or payload across reset | nonzero post-reset retire/link activity or phantom queue entry |
| stall | accept while disabled, lose/overwrite an A2 buffered record or A3 held offer, or prevent a previously launched TX cell from completing | disabled-cycle acceptance/launch, payload-instability, resume-order, or eventual-drain failure |

The stall campaign must separately cover (1) disable before any acceptance,
(2) A2 disable with its charged buffer occupied, (3) A3 disable with a held but
unaccepted owner offer, and (4) disable after TX launch. It must verify that
case 2 waits for re-enable, case 3 does not count as accepted, and case 4 still
retires.

## Explicit nonclaims and remaining HOLDs

Commit `7286913` and its focused smoke test establish a synthesizable
single-edge RTL implementation and directed functional evidence only. They do
not establish:

- canonical full50/reset/mutation qualification for this interface;
- formal proof of exact-once delivery or exhaustive corruption detection;
- PDK legality, mapped cell inventory, synthesis area, timing, Fmax, or power;
- placement/routing, DRC, antenna, connectivity, recovery/removal, or clock
  skew closure;
- real pads, separate TX/RX macros, package/channel integrity, simultaneous
  switching, or I/O loading;
- CDC/RDC signoff or safe operation with unrelated clocks (the contract is one
  shared synchronous clock only);
- a lossless mid-flight reset, receiver backpressure, retry, CRC/ECC, or fault
  recovery;
- preservation of identity under a wire corruption that changes one legal
  codeword into another legal codeword;
- A2 scalar-prefix equivalence or any expansion of A3's held-snapshot
  exact-prefix claim;
- permission to borrow P6 digital, physical, or power evidence;
- competition release-interface selection, physical GO, PDK GO, or system GO.

The 9-wire cardinality result is a functional coding-width fact. It is not a
minimum-area, minimum-power, minimum-pad, or physically qualified interface
claim. The 54/55 state-bit inventories are RTL accounting facts, not mapped
flop, gate-equivalent, or post-route area results.
