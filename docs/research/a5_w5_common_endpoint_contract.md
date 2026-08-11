# A5 W5 common-workload endpoint contract

Status: **DIGITAL COMMON REPLAY PASS / PHYSICAL HOLD**.  A5 materializes the
production A7 RTL only from the exact pinned Git object and uses its own
candidate-neutral TB/driver.  It never executes the mutable A7 worktree.
`ca1a209` is an explicit negative control: it reports idle while a launch or
registered retirement remains pending and is not a qualifying endpoint.  The
qualified commit is `42377ca81340951bfcd453b3bd664e673091f9f3`.

## Frozen workload boundary

`w5_common_endpoint_runner.py prepare` reads blobs from common commit
`47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be`, not the common worktree.  It checks
the generator-v4 script, official policy, full50/capacity22 manifests, exact run
name order, and every generated trace SHA.  The measured smoke result is:

| suite | runs | serialized accepted cohort |
|---|---:|---:|
| full50 | 50 | 106,416 events |
| capacity22 | 22 | 65,616 events |

Events are stably serialized in trace order with
`launch=max(occurrence, previous_launch+1)`.  This serialization is a TB workload
boundary, not candidate hardware.  The endpoint sees only the 4-bit address.
`presentation_index`, generator event ID, occurrence cycle, and launch cycle are
observer sidecars and must never enter DUT pins or synthesized state.  The
always-ready primary endpoints must accept every event, so the parallel and DDR
measurements have the identical occurrence-ID cohort by construction.

## R1 handshake and clocks

R1 is ordinary ready-valid.  Every ref/core positive edge with `valid && ready`
accepts one frame.  Continuous `valid` with a new address on every accepted cycle
is legal back-to-back traffic and must not be edge-suppressed.  A transaction's
address is required to remain stable only while `valid && !ready`.  The primary
qualification uses an always-ready sink; any delayed accept is a failure, not a
different traffic sample.

The primary clocks are strict phase-related synchronous clocks derived from the
frozen source.  This contract makes no unrelated-CDC or 2FF-synchronizer claim.
DDR transmit commits at burst fall.  A charged `seen_toggle` detector makes the
registered retire toggle/address available on the next ref rise.  A distinct
always-ready `always_ff` consumer samples those producer outputs in the pre-NBA
region and retires on the following ref edge.  Post-edge inspection of the
newly-written producer register is not retirement.  The parallel endpoint uses
the identical registered-consumer boundary.  `drain_idle` must be low whenever
`launch_fire` or registered `retire_valid` is high.  Backpressure,
unrelated clocks, and FIFO/handshake CDC are future variants and cannot be
reported as this primary comparison.

## Required pinned A7 endpoint bundle

The runner's built-in contract `A5_BUILTIN_PINNED_A7_W5` lists every production
A7 endpoint RTL path and SHA, with endpoints `parallel_r1_full` and
`ddr_r1_full`.  A commit whose blobs do not match those immutable hashes fails
before compilation.  The contract declares:

- address as the sole DUT-visible event field and presentation index as a
  TB-only observer field;
- the ready-valid, phase-related clock, always-ready, and common consumer-retire
  contracts encoded by the runner;
- DDR burst-fall commit plus charged `seen_toggle`, and the fair parallel
  next-ref-rise boundary;
- mutually exclusive toggle groups `[data, control, clock]`.

The A5 production driver emits the exact endpoint commit, generated bundle
manifest SHA, driver SHA, boundary-index SHA, compile-log SHA, binary SHA, and
simulator executable identity/SHA.  Each run artifact is rebound to
endpoint/suite/name, trace SHA, and boundary SHA.  Missing, extra, duplicate,
swapped, stale, reordered, lost, phantom, or incompletely drained artifacts fail
closed.  Moving repository HEAD after the pinned commit does not affect replay.

## Metrics and reset

For each endpoint and suite the evaluator reports accepted and delivered count,
fixed-window delivered (`retire_tick < stim_cycles*4`), and occurrence-to-retire
mean/p50/p95/p99/max in quarter-cycle ticks.  It separately sums data, control,
and clock bit transitions and divides each by delivered events.  Toggle scopes
must be mutually exclusive synthesized endpoint signals; TB serializer and
observer sidecars are excluded.  Data covers address/data-path state, control
covers protocol/toggle/FSM state, and clock covers clock-net transitions.

Every run has two initial reset cycles.  A result must directly attest no retire
during reset, no phantom completion after reset, and observed state clear.  This
is an initial-reset qualification only; it does not claim a mid-traffic flush
contract.

The exact production replay result is recorded in
`docs/research/results/a5_w5_common_endpoint_summary.json`.  Both endpoints
accepted and delivered the same 106,416 full50 and 65,616 capacity22 occurrence
IDs.  Fixed-window delivery was respectively 85,049 and 44,520 for both.  Since
encoding does not change the stable single-lane queue or common synchronous
consumer boundary, occurrence-to-retire latency is identical: full50
p50/p95/p99/max is 268/15,216/19,472/20,536 ticks and capacity22 is
1,996/17,256/19,880/20,536 ticks.

The deterministic signal-transition proxy begins after the reset-release arming
edge and covers traffic through complete drain.  It includes the qualifier's
ready/fire nets during traffic; its one state bit is charged in the structural
counts, while the reset transition itself is reported through the separate
reset contract rather than amortized into traffic toggles.  Full50
data/control/clock toggles per delivered event are 7.785/2.832/7.130 for
parallel and 11.241/2.832/7.130 for DDR; capacity22 values are
8.162/2.234/6.642 and 11.410/2.234/6.642.  These are RTL/interface transition
counts, not power.  Owner same-flow structural counts are DDR 3 pins, 20 state
bits, 29 charged functional cells versus parallel 5 pins, 18 bits, 27 cells.
Producer availability is one cycle and registered synchronous consumption is
two cycles.  Physical ICG/clock tree/DDR cells, routed capacitance, STA and PVT
remain HOLD.

## Commands and sentinels

```sh
python3 tests/a5_w5_common_endpoint/w5_common_endpoint_runner.py prepare \
  --output /new/unique/boundary

python3 tests/a5_w5_common_endpoint/w5_common_endpoint_runner.py evaluate \
  --boundary-root /new/unique/boundary \
  --endpoint-repo /home/chickgoose/projects/a7 \
  --endpoint-commit EXACT_40_HEX_FIXED_W5_COMMIT \
  --output /new/unique/a5-w5-evaluation.json
```

Preparation exits zero with `A5_W5_BOUNDARY_READY_NOT_ENDPOINT_PASS`; that is not
an endpoint result.  Evaluation alone may print `A5_W5_ENDPOINT_EVALUATION_PASS`.
Absent/stale commits and all contract mismatches exit 2 with
`A5_W5_FAIL_CLOSED`.  Existing boundary and result paths are never overwritten.

The fake driver under `tests/.../fixtures` validates plumbing only and is
explicitly not RTL or a performance model.
