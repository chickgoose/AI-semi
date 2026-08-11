# A5 W5 exact serialized-link replay contract

Status: **EXACT SERIALIZED-LINK REPLAY PASS; CAPACITY/POWER HOLD**.  This is not
a common-suite service-capacity, bounded-ingress, power, or energy
qualification.  A5 materializes the production A7 endpoint RTL from exact Git
object `42377ca81340951bfcd453b3bd664e673091f9f3`; it never executes the mutable
A7 worktree.  Superseded `ca1a209` is retained only as a negative control for
its launch/pending-output `drain_idle` defect.

## Workload transformation and scope limit

The runner reads generator-v4 blobs from common commit
`47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be`, checking generator, policy,
official manifests, exact name order, and every trace SHA.  Capacity22's 22
runs are a strict name subset of full50, not an independent workload universe.

The address-only events are fed through an **unbounded TB serializer** in stable
trace order:

```text
launch = max(occurrence, previous_launch + 1)
```

This serializer is neither DUT hardware nor a realizable bounded ingress
claim.  Its worst observed queue is 5,133 events and maximum
occurrence-to-launch wait is 5,132 cycles.  It launches 21,306 full50 events
and 21,064 capacity22 events only after their original stimulus windows have
ended.  Consequently fixed-window delivery and total occurrence latency cannot
be presented as native common-workload capacity or endpoint latency.

The endpoint sees only the four-bit address.  Presentation index, generator ID,
occurrence cycle, and launch cycle remain TB-only observer sidecars and never
enter DUT ports or synthesized state.  Parallel and DDR receive the exact same
occurrence/address order; that same-address cohort result remains valid.

## R1 endpoint boundary

Every `valid && ready` ref-clock posedge accepts one frame.  Continuous valid
with a new address each accepted cycle is legal; valid-edge suppression is a
failure.  Address stability is required only while `valid && !ready`.  The
primary clocks are phase-related synchronous clocks from the frozen source, not
an unrelated-clock or 2FF CDC claim.

DDR commits at burst fall.  Its charged seen-toggle observer makes registered
output available one cycle after launch.  A real `always_ff` sink samples the
producer's pre-NBA output and consumes exactly two cycles after launch.  The
parallel reference uses the same consumer boundary.  `drain_idle` must remain
low during `launch_fire` and while registered `retire_valid` is pending.

## Direct reset probe

Every replay run now performs an actual second reset after complete drain.  The
TB holds reset for two cycles, checks quiet outputs, releases and observes three
quiet ref cycles, then sends address `0xa`.  It asserts zero retirement during
reset, zero stale/phantom retirement during the quiet epoch, and exact-once
delivery of only the post-reset sentinel.  These are direct endpoint assertions,
not a driver-authored PASS sentinel.  Mid-frame reset remains outside contract.

## Reported metrics

Latency is split rather than conflated:

- occurrence-to-launch: entirely the unbounded TB serializer wait;
- launch/accept-to-retire: endpoint plus registered consumer, exactly 8 ticks
  or two cycles for both endpoints;
- total: the sum of those two components.

Full50 occurrence-to-launch p50/p95/p99/max is
260/15,208/19,464/20,528 ticks; total is
268/15,216/19,472/20,536.  Capacity22 values are
1,988/17,248/19,872/20,528 and 1,996/17,256/19,880/20,536.  Both endpoints
deliver all serialized events after drain, while fixed-window delivery is
85,049 for full50 and 44,520 for capacity22.  Those values describe only this
serialized replay.

Activity is named an **RTL/interface value-transition proxy**, never energy.
It is split into shared input data/control/base clocks and endpoint-only
internal data/control/link clock.  The shared part is identical by construction
and is not attributed to an encoding.  Endpoint-internal transitions/event are:

| suite | endpoint | internal data | internal control | link clock |
|---|---|---:|---:|---:|
| full50 | parallel | 5.838 | 2.642 | 2.000 |
| full50 | DDR | 9.295 | 2.642 | 2.000 |
| capacity22 | parallel | 6.122 | 2.186 | 2.000 |
| capacity22 | DDR | 9.369 | 2.186 | 2.000 |

These counters are deterministic signal transition proxies, not power.  They
exclude the TB serializer/observer.  Characterized ICG/clock tree/DDR cells,
routed capacitance, STA, PVT, and power remain HOLD.

## Fail-closed provenance and canonical result

The built-in bundle pins every A7 RTL blob plus immutable A5 production-driver
and harness hashes.  Each attempt binds runner, driver, harness, endpoint
manifest, boundary index, simulator executable, compile log, binary, run
artifact, trace, and boundary hashes.  Mutation tests cover backlog metadata,
run identity, edge suppression, post-NBA mismeasurement, reset evidence,
transition schema, stale endpoint commit, and the real `ca1a209` negative RTL.

Attempt compile/binary hashes may include temporary build paths.  They are kept
in attempt output but deliberately excluded from the stored canonical machine
summary.  The path-independent canonical metrics are in
`docs/research/results/a5_w5_common_endpoint_summary.json`; no unreproducible
full-output hash is claimed.

```sh
python3 tests/a5_w5_common_endpoint/w5_common_endpoint_runner.py prepare \
  --output /new/unique/boundary
python3 tests/a5_w5_common_endpoint/w5_common_endpoint_runner.py evaluate \
  --boundary-root /new/unique/boundary \
  --endpoint-repo /home/chickgoose/projects/a7 \
  --endpoint-commit 42377ca81340951bfcd453b3bd664e673091f9f3 \
  --output /new/unique/serialized-link-replay.json
```

Preparation prints `A5_W5_BOUNDARY_READY_NOT_ENDPOINT_PASS`.  Evaluation alone
may print `A5_W5_EXACT_SERIALIZED_LINK_REPLAY_PASS`; all stale/missing/mutated inputs exit
2 with `A5_W5_FAIL_CLOSED`.  Existing output paths are never overwritten.
