# A7 reusable P6 exact-pair endpoint contract

Status: **digital RTL evidence only**.  Energy, mapped DDR/ICG cells, CDC/RDC,
PVT timing, CTS/routing, signal integrity, and physical PPA are **HOLD**.

## Owned boundary and intended users

`a7_p6_exact_pair_endpoint` is the common scheduler-neutral link endpoint for
all three new N=16 K2 experiments.  It deliberately carries no scheduler ID and
neither instantiates nor ranks a scalar-prefix, replicated, or weighted
two-row policy.  Each scheduler must first reduce its result to the same atomic
input contract, so endpoint cost is common rather than credited differently to
a scheduler.  A raw row/column mask that can expose more than two addresses in
one cycle is outside this K2 boundary and must not be silently truncated.

The candidate, reference, tests, model, runner, and report are isolated under:

- `rtl/candidates/a7_p6_exact_pair_endpoint/`;
- `tests/a7_p6_exact_pair_endpoint/`; and
- `reports/a7-p6-exact-pair-endpoint/`.

No frozen common benchmark, team RTL, native binding, or scheduler RTL is
modified.

## Atomic 0/1/2-address input contract

At each rising `ref_clk_i`, one scheduler transaction is accepted exactly when
`input_valid_i && input_ready_o` is sampled true.

- zero addresses: `input_valid_i=0`, `input_count_i=0`;
- singleton: `input_valid_i=1`, `input_count_i=1`, lane 0 is meaningful;
- ordered pair: `input_valid_i=1`, `input_count_i=2`, lane 0 precedes lane 1;
- count 3, valid with count 0, or invalid with nonzero count is a protocol
  error and cannot handshake;
- while valid and not ready, count and both addresses must remain stable; and
- one handshake accepts the entire singleton or pair atomically.

Ready is low during reset and through the first charged reference edge after
reset release.  Thereafter the endpoint can accept one transaction every
reference cycle.  There is no valid-edge detector: held valid produces one
transaction on every edge on which ready remains high.

## P6 word and physical signals

Each accepted transaction occupies one ten-bit DDR cell:

```text
word[9]    pair flag
word[8]    reserved zero
word[7:4]  first address
word[3:0]  second address for a pair, zero for a singleton
```

The low five bits travel on the forwarded-clock rising edge and the high five
bits on its falling edge.  The falling edge commits one record.  The receiver
emits lane-valid `01` for a singleton or `11` for a pair in the same reference
cycle, preserving lane 0/lane 1 order.  Back-to-back records merge into a
continuous forwarded clock while retaining one rise/fall cell per scheduler
cycle.

P6 therefore charges **six physical link signals**: five DDR data wires plus
one forwarded clock.  The fair parallel reference uses the identical input
guard and ref-domain observation latency, and charges **ten signals**: two
four-bit address buses, one pair flag, and one forwarded strobe.

## Why there is no Q2

The source produces at most one atomic K2 transaction per scheduler cycle and
the link consumes one transaction per link cycle at `R=1`.  Once armed, there
is no rate mismatch or serialization bubble to absorb.  A Q2 cannot raise
throughput; it would only add state and conceal a valid/ready violation.

Accordingly, the endpoint reports `queue_state_bits=0`.  Reset-arm and illegal
transactions are handled by ready-low backpressure.  Output backpressure is
not supported: the declared receiver boundary is always-ready.  A product that
requires an independently stalled consumer must add and charge explicit
retire buffers and is not this endpoint.

## Reset and drain

Reset is asserted only after `drain_idle_o` while the forwarded clock is low.
It clears the frame, partial symbol, raw commit toggle, observer state, and
registered retirement.  Reset in the middle of an accepted frame may truncate
the physical clock and is not a delivery/flush contract.

`drain_idle_o` means there is no same-cycle launch, active frame/clock,
unobserved raw commit, or registered retire valid.  An external transaction
that has not handshaken is not internal accepted work.

## Evidence and qualification boundary

The independent Python model exhausts all 16 singleton and 256 ordered-pair
code words.  RTL lockstep exhausts the same code space at the raw P6 pins and
compares final retirement against the equal-latency parallel reference.
Expected-fail mutations cover accepting a third address, early-ready stall
violation, reset phantom state, and swapped pair order.

The frozen 46 traces are regenerated to `/tmp`, projected through a
deterministic rotating K2 source-latch seam, replayed through RTL, and checked
against the independent model.  The projection is a neutral endpoint stimulus,
not a scheduler-ranking result.

Run locally with explicit tools when they are not on `PATH`:

```sh
VERILATOR=/path/to/verilator YOSYS=/path/to/yosys \
  tests/a7_p6_exact_pair_endpoint/run_all.sh
```

Yosys cells/state/depth are generic structural proxies.  No server Genus,
Innovus, extracted activity, energy/event, half-cycle STA, or P&R result exists.
No claim of physical 6-pin closure may be made from this digital evidence.
