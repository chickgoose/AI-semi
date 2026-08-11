# A6 W5: RX burst-clock to downstream core-clock boundary

## Recommendation

Implement the **strict phase-related synchronous R1 boundary** as the primary
endpoint. This is not an unrelated-clock CDC and contains no implicit or free
synchronizer. `ref_clk_i`, downstream `core_clk_i`, and `sample_clk_i` must share
the frozen source and phase contract. A7 RX commits the four-bit address and
toggles retirement at the burst/sample falling edge; the consumer captures both
at the next reference/core rising edge through a charged six-bit detector.

Digital status is **GO_RESTRICTED_PHASE_RELATED_R1_ONLY**. Arbitrary-clock CDC is
**HOLD_REQUIRES_END_TO_END_BACKPRESSURE**. The bundled-data handshake and async
FIFO alternatives are not implemented because current A7 cannot stop RX or
propagate full/acknowledge to TX. Adding a finite buffer without that path would
detect or postpone overflow, not prove losslessness.

## Primary timing and invariant

For the frozen 16 ns W4 clock contract:

1. reference/core rising edge at 0 ns admits at most one event;
2. sample/burst rising edge at 4 ns captures address `[1:0]`;
3. sample/burst falling edge at 12 ns captures `[3:2]`, publishes the complete
   address, and changes `retire_toggle` exactly once;
4. next reference/core rising edge at 16 ns observes the stable address and
   changed toggle.

RX-commit-to-core-visible latency is therefore nominally 4 ns or 0.25 core
cycles; admission-to-core-visible latency is one core cycle. Continuous traffic
delivers one event on every core edge after pipeline fill. Exactness requires no
more than one RX retirement between consecutive core edges. At R=2 or R=4, two
toggle changes may cancel before observation, so this circuit is explicitly
illegal even if a particular sparse trace happens not to alias.

The RTL contains exactly six core-clock state bits:

| State | Bits | Purpose |
|---|---:|---|
| `seen_toggle_q` | 1 | last consumed RX retirement generation |
| `core_event_addr_o` | 4 | stable downstream address-only identity |
| `core_event_valid_o` | 1 | one-core-cycle occurrence pulse |

Local generic Yosys reports one 4-bit enabled flop, two 1-bit flops, one compare,
and one mux: six state bits and five generic cells. This is a structural check,
not physical PPA.

## Fair endpoint boundary

The parallel4 comparison must use the identical six-bit consumer observation
boundary. It is not allowed to expose its RX address/toggle directly while DDR2
pays capture state.

| Link | Existing W4 link state | Common consumer state | Fixed endpoint state |
|---|---:|---:|---:|
| parallel4 | 11 | 6 | 17 |
| DDR2 | 13 | 6 | 19 |
| serial1 | 16 | 6 | 22 |

Parallel4 and DDR2 both retain an ideal R1 ceiling of one event/core cycle; DDR2
still removes two external link pins. The two-bit state difference is not a PPA
result. Upstream same-cycle collection/FIFO, clock tree, characterized ICG,
ODDR/IDDR, pads, routing, downstream logic, and STA/CDC/RDC closure remain outside
these fixed-state totals and are not free.

## Alternative comparison

| Boundary | Incremental state lower bound | First-event latency | Sustainable throughput | Lossless condition | Current A7 |
|---|---:|---|---|---|---|
| related-phase R1 detector | 6 | 0.25 core cycle after RX commit | 1 event/core | frozen phase, STA, at most one commit/core, drained reset | implemented |
| bundled-data two-phase handshake | 15 | 2--3 destination cycles; source reuse after two return-sync cycles | at equal clocks, conservatively no more than 0.25 event/core | source holds address until synchronized ack; TX admission waits | incompatible without ack-to-TX path |
| Gray-pointer async FIFO depth 2 | 31 | normally 2--3 destination cycles | up to min(write, read) rate | full backpressure or proved bounded backlog | incompatible without full-to-TX path |
| Gray-pointer async FIFO depth 4 | 47 | normally 2--3 destination cycles | up to min(write, read) rate | same as above, with four slots | incompatible without full-to-TX path |

The handshake lower bound includes a four-bit source mailbox, request toggle,
two destination request synchronizer bits, destination seen bit, five output
address/valid bits, and two source acknowledgement synchronizer bits. It does not
include reset synchronizers, constraints, or upstream admission control.

The FIFO lower bound includes four-bit payload memory, local binary and Gray
pointers in both domains, two complete two-flop crossed-pointer synchronizers,
four registered output bits, valid, full, and empty. Depth 2 costs 31 bits; depth
4 costs 47. A real implementation can require additional memory/output, reset,
almost-full, CDC constraint, or ECC state.

Most importantly, neither alternative can be attached losslessly to the current
A7 RX alone. `retire_toggle_o` has no ready input. A mailbox can be overwritten
before acknowledge, and an async FIFO can fill. Correct future variants must
propagate mailbox ready or FIFO full to TX admission across the entire link.

## full50/capacity22 replay

The executable model serializes the same address-only occurrence stream through
the link and counts RX commits between downstream core edges. It models the
toggle parity failure exactly: two unseen changes deliver zero, while three
changes expose only the final occurrence.

| Suite | R | Events | Exact runs | Toggle-alias losses | Worst commits/core | Finite-trace FIFO depth LB | Rounded FIFO/state LB |
|---|---:|---:|---:|---:|---:|---:|---:|
| full50 | 1 | 106416 | 50/50 | 0 | 1 | 3 | 4 / 47 bits |
| full50 | 2 | 106416 | 24/50 | 56452 | 2 | 4612 | 8192 / 32887 bits |
| full50 | 4 | 106416 | 24/50 | 55272 | 4 | 5135 | 8192 / 32887 bits |
| capacity22 | 1 | 65616 | 22/22 | 0 | 1 | 3 | 4 / 47 bits |
| capacity22 | 2 | 65616 | 3/22 | 49512 | 2 | 4612 | 8192 / 32887 bits |
| capacity22 | 4 | 65616 | 3/22 | 48332 | 4 | 5135 | 8192 / 32887 bits |

R1 is exact after the link has serialized the events. This does not make the
unmodeled upstream same-cycle collector free: official multi-occurrence cycles
still require the staging already identified by W4. The finite-trace FIFO column
assumes a one-event/core reader and charges two core cycles before the synchronized
write pointer becomes visible. It is only a capacity lower bound for those pinned
traces. An unrelated clock can drift, and R>1 has a sustained writer-rate
advantage, so no finite number in this table proves arbitrary losslessness.

The lower alias loss at some R=4 aggregates is a parity artifact, not improved
correctness: more even-sized groups disappear completely.

## Reset and RDC contract

The boundary deliberately uses synchronous `core_reset_i`; it adds no unchecked
asynchronous reset crossing. Lossless reset requires this ordered sequence:

1. stop admission and drain the link;
2. assert A7 reset (`rst_n=0`) only while the burst clock is low;
3. assert `core_reset_i` for at least one core rising edge;
4. release A7 reset while core reset remains asserted, establishing source
   address/toggle zero;
5. release `core_reset_i` synchronously;
6. admit no new event until that core edge completes.

One-sided reset, reset with traffic in flight, source toggle reset while the
consumer remains live, or changing the clock relationship is outside the
lossless contract. A later asynchronous FIFO must independently synchronize
reset release in both domains and specify what happens to in-flight entries.

## Exact inclusion boundary and remaining qualification

Included:

- the six-bit related-phase detector RTL;
- one-cycle valid/address output semantics;
- continuous 18-event RTL lockstep including all N16 identities and repeated
  same-address occurrences;
- full50/capacity22 executable alias/FIFO-capacity model;
- Icarus and Verilator simulation plus generic Yosys six-state-bit check.

Required but excluded/non-free:

- generated/related-clock STA proving the 4 ns bundled-data path and hold margin;
- clock skew, CTS, PVT, recovery/removal and physical implementation;
- A7 TX/RX, upstream collector/FIFO and common scoring integration;
- any unrelated-clock synchronizer, mailbox acknowledgement, FIFO, full
  backpressure, or reset synchronizer.

Machine report SHA-256 is
`7bcf6795bd23f79d87bdcc7577b771e1999a8fc7c26fadb87aaf25735c8f98ae`.
Run `scripts/run_a6_w5_cdc_checks.sh` for fresh trace generation, provenance
validation, Python tests, exact replay, dual-simulator RTL checks, and the Yosys
state-bit assertion. A7 and common files are read only.
