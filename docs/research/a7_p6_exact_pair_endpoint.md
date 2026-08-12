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

## Atomic 0/1/2-address scheduler contract

The normalized top-three comparison boundary is
`a7_p6_atomic_bundle_adapter`.  At each rising `ref_clk_i`, one scheduler
bundle is accepted exactly when `bundle_valid_i && bundle_ready_o` is sampled
true.

- zero addresses: valid bundle with `grant_count_i=0`; it commits as an
  atomic no-op, launches no link cell, and advances zero policy microsteps;
- singleton: valid bundle with `grant_count_i=1`, lane 0 is meaningful;
- ordered pair: valid bundle with `grant_count_i=2`, lane 0 precedes lane 1;
- count 3, or an invalid bundle with nonzero count, is a protocol error and
  cannot handshake;
- while a legal valid bundle is not ready, count and both addresses must stay
  stable through the eventual commit edge; and
- one `bundle_commit_o` accepts all valid lanes together.  There is no
  per-lane ready or per-lane scheduler commit.

`policy_microsteps_o` is zero without a commit and is exactly `grant_count_i`
on a commit.  The scheduler owns policy state and advances it by precisely
that many sequential scalar transitions.  The adapter does not inspect or
mutate scheduler policy.  A scheduler binding must use the single bundle fire
as its only policy-state write enable, so `bundle_ready_o=0` implies zero
microsteps and stable policy state.

The original `a7_p6_exact_pair_endpoint` remains the nonempty link-record
core: its `input_valid_i=0,input_count_i=0` encoding means no link cell.  The
atomic frontend maps a valid count-zero scheduler offer onto that no-cell
encoding.  This distinction closes the contract-expression gap in commit
`4dcafd8`; it does not change the P6 code word or nonempty endpoint behavior.

Ready is low during reset and through the first charged reference edge after
reset release.  Thereafter the endpoint can accept one transaction every
reference cycle.  There is no valid-edge detector: held valid produces one
transaction on every edge on which ready remains high.

The fair reference is wrapped by
`a7_p6_atomic_bundle_parallel_reference`, which uses the same atomic frontend,
reset arm, commit edge, and policy-microstep evidence.  Thus a pair can never
be counted as one scheduler acceptance on P6 and two independent lane
acceptances on the parallel reference.

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
retire buffers and is not this endpoint.  In particular, the A5 evaluator's
independent `retire_ready[1:0]` adversaries belong only behind a separately
buffered and charged link adapter.  That adapter may change its own queue/lane
state, but it must not split a scheduler bundle commit or independently
advance scheduler policy.  No such retire buffer is included or hidden in the
six-pin P6 cost.

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
violation, reset phantom state, swapped pair order, and advancing a committed
pair by only one policy microstep.  The atomic wrapper test also covers a
valid count-zero no-op, held count/addresses across reset-arm stall, whole-pair
commit, same-cycle order, drain/reset/rearm, and lockstep against the wrapped
parallel reference.

## Alignment with A5 evaluator `41c425b`

The A5 evaluator defines `accepts` as one ordered contiguous scalar prefix of
length 0, 1, or 2 and advances its oracle once for each accepted event.  The
normalized mapping is direct:

```text
A5 accepts=[]       -> grant_count=0 -> one no-op bundle commit -> 0 steps
A5 accepts=[g0]     -> grant_count=1 -> one bundle commit       -> 1 step
A5 accepts=[g0,g1]  -> grant_count=2 -> one bundle commit       -> 2 steps
```

The evaluator evidence array must be emitted from the single bundle fire; it
must not be constructed from two independently ready scheduler lanes.  A5's
independent `retire_ready` checks are downstream retirement checks, not
permission to split the scheduler acceptance boundary.

The exact `41c425b` tree was extracted read-only and its five unit tests plus
seven-mutation self-falsification suite passed.  That qualifies the evaluator,
not this endpoint as one of the three scheduler candidates.  The A7 RTL test
independently checks the normalized bundle mapping at the actual P6 and fair
parallel boundaries.

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
