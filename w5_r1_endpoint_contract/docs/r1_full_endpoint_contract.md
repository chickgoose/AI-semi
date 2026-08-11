# Address-only N16 R1 full-endpoint contract

Status: frozen W5 candidate-neutral contract.  This document and its executable
model define the qualification target; passing the model's mutation tests does
not qualify a candidate.

## Boundary and identity

The logical input is `core_valid`, `core_ready`, and `core_addr[3:0]`.  An event
is accepted only on a core-clock rising edge where reset is inactive and
`core_valid && core_ready` is true.  Its complete DUT-visible identity is the
four-bit N16 source address.  There is no event payload, type, sequence number,
occurrence tag, or polarity field.

Standard ready-valid semantics are mandatory:

- every qualifying posedge handshake is a new occurrence;
- `core_valid` may remain high indefinitely, and after a successful handshake
  the producer may present a new address for the next posedge;
- if `core_valid && !core_ready`, valid and address must remain stable until a
  later handshake; and
- a valid-edge detector or one-shot must not suppress legal back-to-back R1
  handshakes.  Such state is relevant only at R>1 cross-rate boundaries or for
  a level-request protocol, neither of which is this contract.

The checker may attach an occurrence ID and timestamp to each handshake, but
that is TB-only causal-credit state.  It cannot enter the endpoint, select a
retirement, repair a wire error, or distinguish same-address occurrences in
hardware.

## Exact R1 frame state machine

One core period contains these ordered boundaries:

1. On reset release, the first `t=C` reference edge sets a charged one-bit arm;
   ready is still low and no handshake occurs on that edge.
2. At an armed `t=C`, sample ready-valid.  A handshake snapshots `core_addr`
   into the TX address register and enables exactly one frame.
3. `t=C+1/4`: the forwarded DDR clock rises and the receiver samples
   `addr[1:0]`.
4. `t=C+3/4`: the forwarded clock falls, the receiver samples `addr[3:2]`,
   commits raw `{high,low}`, and changes the raw toggle exactly once.
5. At `t=C+1`, a charged phase-related observer samples the raw address/toggle,
   makes registered address/valid available, and may simultaneously accept the
   next ready-valid event.  There is no 2FF CDC or uncharged reconstruction.
6. At `t=C+2`, the always-ready synchronous consumer samples the prior-cycle
   registered address/valid in the pre-NBA region and retires exactly once.

For every legal non-reset-epoch acceptance there must be exactly one frame
rise, one frame fall/raw commit, one output availability, and one synchronous
consumer retirement.  There may be no duplicate, phantom, drop, reorder, or
output reconstructed from a scoreboard credit.  The endpoint contains no
boundary FIFO or skid slot.  Up to two TB causal credits can overlap because one
is in the charged observer output stage while the next occupies the frame stage;
this scoreboard pipeline is not hidden endpoint storage.

The canonical charged DDR endpoint accounting is launch arm 1 + TX address and
frame enable 5 + generic ICG enable latch 1 + RX low/raw-address/raw-toggle 7 +
ref-domain observer seen-toggle/address/valid 6 = **20 bits**.  The equal-boundary
parallel reference is **18 bits**.  An implementation may use more, but every
additional qualifier, clock-gating latch, synchronizer, decoder, or control bit
is functional state inside its PPA boundary.  The Python monitor's causal credit
and phase bookkeeping are not implementation state.

## Reset and mid-frame behavior

Normative traffic uses reset-after-drain: before reset assertion,
accepted=launched=raw-committed=available=consumer-retired, the observer valid
has been sampled, and `drain_idle` is true with the forwarded clock low.  The
drain guard is fail-closed for same-cycle launch, active frame/clock, unobserved
raw toggle, and registered valid pending synchronous consumption.  During reset,
ready is low, the forwarded frame clock is quiet, and no retirement occurs.
Legal release occurs with both source clocks low after a sample-clock falling
edge.  The first reference edge only arms ready; a valid/address held across
that stall handshakes at the following edge.

A mid-frame reset is invalid input behavior, not a supported cancel/preserve
transaction.  The negative test must see `drain_idle==0`, require no phantom or
replayed retirement, and perform a subsequent legal reset before new traffic.
The generic gated-clock model may truncate a high pulse; it does not prove pulse
integrity, recovery/removal, or a delivery guarantee for the in-flight event.
Those are physical HOLD rather than a silent functional PASS.

## Latency and parallel reference

Acceptance time is the qualifying core posedge.  DDR launch is at +1/4 and raw
DDR address/toggle commit is at +3/4 core cycle.  Registered endpoint
address/valid becomes available at +1.  An always-ready synchronous consumer
samples it at +2, which is architectural retirement.  End-to-end occurrence
latency is `occurrence-to-acceptance wait + 2`; availability latency is reported
separately as +1.  Fixed-window delivered/retired counts use synchronous
consumption, not launch, raw commit, or availability.

The parallel reference starts at the same accepted boundary and exposes
`strobe + addr[3:0]`: five functional pins.  It uses the identical one-bit arm,
gated link-clock boundary, raw toggle/address, and six-bit ref observer, for 18
charged bits and the same +1 output-availability latency.  The R1 DDR endpoint
uses three pins and 20 charged bits.  Both make output available at +1 and reach
synchronous consumer retirement at +2.  Both must see the identical accepted
event sequence; DDR cannot create throughput or erase core overrun.  The +3/4
value is only the DDR raw-link commit and must not be compared with a full
parallel consumer boundary.

## PPA boundary

Count all synthesizable TX, launch qualification, clock gating, forwarded-clock
generation, RX sampling, toggle/valid generation, reset, and decoding logic.
Count all functional pins and all state bits.  Generated-clock constraints,
clock-gating checks, setup/hold, routed clock behavior, dynamic/leakage power,
area, and energy/event are part of the endpoint result.  No queue, payload,
one-shot, synchronizer, or decoder may be supplied by an uncharged binding.

## Frozen GO/HOLD gates

Functional GO requires all of the following; otherwise it is HOLD:

- zero contract violations in actual-RTL full-endpoint tests, including all 16
  addresses, legal continuous-valid changing-address traffic, stalled-held
  valid through the reset arm, back-to-back frames, reset-after-drain, and the
  invalid-mid-frame no-phantom negative test;
- accepted = launched = raw-committed = available = consumer-retired with
  causal order and zero phantom, duplicate, drop, or hidden reconstruction;
- R1 delivered throughput exactly equals the parallel boundary, with no added
  queue, no payload bits, exactly three DDR versus five parallel functional
  pins, raw DDR commit +3/4, output availability +1, synchronous consumption
  +2, and zero latency delta to the equal-boundary parallel reference; and
- declared state equals all endpoint state and is at least 20 DDR/18 parallel
  bits, including arm, ICG latch, raw RX, and phase observer.

Physical GO additionally requires post-route setup WNS >= 0, hold WNS >= 0,
and characterized forwarded-clock/clock-gating qualification with the complete
endpoint included.  RTL simulation, generic mapping, or an uncharacterized
gated-clock expression leaves physical status HOLD.

Adoption GO additionally requires two economic limits to be immutable before
measurement: maximum area penalty per saved functional pin and maximum
energy/event penalty relative to the parallel reference.  The endpoint must
save exactly two pins and meet both limits.  Missing limits or an exceeded
limit is HOLD; pin reduction alone cannot imply an economic win.  The executable
`qualify()` function implements these thresholds without supplying a favorable
default budget.

R>1, level-request capture, extra launch one-shots, queues, arbitrary payloads,
and scoreboard-assisted output reconstruction are outside this frozen R1
contract and require separate charged architectures.

## Exact A7 `42377ca` cross-check

Cross-check target: A7 commit
`42377ca81340951bfcd453b3bd664e673091f9f3`.  This is an evidence comparison,
not an A2 claim that A7 has passed common or physical qualification.
The earlier `ca1a209` is explicitly excluded because its drain guard omitted
same-cycle launch and pending registered valid, and its scoreboard conflated
output availability with synchronous consumer retirement.

| field | exact A7 evidence | W5 disposition |
|---|---|---|
| ready-valid | `launch_fire=valid&&ready`; continuous valid may change address after every handshake; no valid-edge state | compatible |
| reset release | one charged `reset_release_armed_q`; first release edge cannot handshake | initial W5 mismatch, corrected and now compatible |
| raw DDR phase | rise +1/4 samples low; fall +3/4 commits high/raw toggle | compatible |
| normalized observer | charged `seen_toggle + addr[3:0] + valid` = 6 bits; output available +1 | initial W5 omission corrected; availability compatible |
| synchronous sink | no retire-ready/backpressure; always-ready consumer samples registered output at +2 | initial W5 +1 retirement mismatch corrected; +1 availability/+2 consumption now distinct |
| reset/drain | legal only at `drain_idle` with forwarded clock low; guard includes launch, frame, raw-toggle difference, and pending valid; mid-frame assertion invalid and may truncate | initial W5 and superseded A7 drain gaps corrected; no-phantom negative test compatible, physical pulse/recovery remains HOLD |
| parallel reference | identical arm/ICG/raw/observer boundary, five pins, 18 bits, +1 availability/+2 consumption | initial zero-state/zero-delay reference mismatch corrected |
| DDR charged boundary | three pins; 1 arm + 5 TX + 1 ICG + 7 RX + 6 observer = 20 bits | initial 12-bit undercount corrected |
| PPA status | charged generic proxy 29 cells/20 bits versus parallel 27/18, both depth 7; no characterized ICG, clock tree, half-cycle STA, PVT, or power | functional structure is comparable; physical and adoption remain HOLD |

The exact RTL locations are
`rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv`,
`a7_r1_retire_observer.sv`, and the two endpoint tops at that commit.  W5 does
not import, wrap, or modify them.

The final exact commit was independently archived to `/tmp` and replayed with
Verilator 5.032.  Nominal, same-cycle admission reset block, +1 availability,
pending-valid reset block, +2 consumer retirement, continuous-valid changing-
address, back-to-back, gapped, reset-arm/held-valid, drain-reset, invalid-mid-
frame no-phantom, and exact-once/order/address checks passed.  Its exact Yosys
script reproduced DDR `3 pins / 29 charged cells / 20 bits / depth 7` and
parallel `5 pins / 27 charged cells / 18 bits / depth 7`, both physical HOLD.
This does not provide common-workload, post-route, power, or adoption
qualification.
