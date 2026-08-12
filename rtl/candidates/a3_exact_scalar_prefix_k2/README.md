# A3 Exact-Scalar-Prefix-K2

Status: **local model/Icarus/Yosys GO; physical timing, independent-lane
backpressure, Xcelium, and formal proof HOLD**.

This directory is an isolated N=16 address-only candidate.  It does not alter
the frozen common benchmark, manifests, Cluster2, or team RTL.

## Frozen common K2 binding

The production common path is entirely candidate-local:

- `rtl/a3_k2_ordered_2entry_adapter.sv` is the charged two-entry ordered
  transport.  At the common default widths it stores two 4-bit source
  identities, two 16-bit accepted events, and a two-bit occupancy count.
- `rtl/a3_k2_charged_event_mux.sv` contains the two explicit 16:1 common-event
  identity muxes required to convert the address-only owner into the common
  payload observation.  The muxes are inside the candidate/PPA filelist.
- `rtl/a3_k2_common_wrapper.sv` connects the registered atomic owner offer to
  that transport.  `source_ready` names exactly the one or two addresses on an
  offer-accept edge; a count-two offer is never partially admitted.
- `a3_clean_binding.sv` is the storage-free `aer_legacy_candidate_adapter`
  compatibility cell used by the frozen common TB.  Its compatibility-only
  `FIFO_DEPTH` parameter is fixed at zero and fails elaboration otherwise.
- `files.f` is the root-relative common compile filelist for
  `NUM_SOURCES=16`, `RETIRE_LANES=2` runs.

The common binding advertises uniform retire ready only: `2'b00` stalls and
`2'b11` retires; any mixed ready value fails closed in simulation.  The charged
adapter always presents its oldest entry on retire lane 0, and lane 1 is valid
only when both buffered entries retire on the same edge.  Its count-one state
is independently checked to ignore lane-1 ready.  Sink movement changes only
adapter state; owner policy advances only when the complete registered offer
fits.

Focused Icarus and Verilator tests are in `tb/ordered_adapter_tb.sv` and
`tb/common_binding_tb.sv`.  The latter instantiates the actual
`aer_bench_if.candidate` modport and verifies registered owner latency, exact
atomic source readiness, stalled-new-pending refill, same-address retrigger
coexistence, event-data holding, reset/drain, and quiet output.  The
`test_common_binding.py` gate also requires nonuniform-ready and nonzero-depth
guards plus separate event-lane and source-lane swap mutations to fail.

## Boundary and commit contract

`source_pending[15:0]` is the current one-outstanding-per-address request
bitmap.  The candidate publishes one registered ordered bundle containing
`grant_count` (`0`, `1`, or `2`) plus two address fields:

- `lane0` is scalar grant `g0` when `grant_count >= 1`;
- `lane1` is `g1` when `grant_count == 2`, the next canonical scalar Fovea grant after
  masking `g0` and applying every `g0` Fovea/team/column RR transition; and
- `grant_count != 0 && bundle_ready` atomically commits the complete offer and
  advances policy by exactly `grant_count` scalar microsteps.

There is deliberately no lane-specific ready.  Partial acceptance would leave
no unique canonical state to commit: committing through `g0` would invalidate
the held `g1`, while committing through `g1` would falsely retire `g0`.
During a stall the complete bundle, committed policy state, and saved
post-bundle state remain stable.  Once an address has entered the registered
bundle it is reserved; it need not remain in `source_pending`.  On commit the
refill logic masks both committed addresses, which prevents a held old level
from being immediately selected again.

The bitmap carries no occurrence identity.  Consequently, clearing an old
event and admitting a new same-address event on the same edge cannot be
distinguished; the common boundary must classify it as overrun/retry or present
the retrigger after rearm.

## Architecture

The combinational selector is two replicated canonical scalar-grant stages:

```text
committed or reserved state -> scalar selector g0 -> explicit next state
                                    | mask g0
                                    v
                               scalar selector g1 -> saved post-bundle state
```

Each selector directly instantiates the canonical equations in functions:
Fovea `round=0..5`, independent center/peripheral `arbiter4_tree` state, and
the shared column `arbiter4_tree` state.  This is lookahead replication, not a
population-count/rank or shared-prefix compactor.

Registered state totals 34 bits:

- 12 committed policy bits: round plus three 3-bit arbiter-tree states;
- 12 saved post-bundle policy bits; and
- 10 registered output bits: a two-bit count and two four-bit addresses.

Reset initializes every `arbiter2.last_gnt` equivalent to one and Fovea round
to zero, matching the pinned canonical scalar RTL.

## Verification

`oracle.py` is an independent scalar-fold implementation.  It does not import
the W7 model.  The runner creates expected state/output vectors from that
oracle and compares every RTL cycle, including internal committed policy state.

Directed qualification covers:

- 120 persistent committed bundles and exact row opportunities
  `[20,100,100,20]`, normalized `1:5:5:1`;
- sparse peripheral-only work-conserving fallback;
- stable registered outputs and state over a four-cycle stall;
- atomic release/refill, mid-stall reset, and clean drain;
- no replay without rearm and a same-address later retrigger; and
- stale-count, duplicate-mask, and omitted-state-advance mutations.

Frozen-v4 replay SHA-checks the existing generator and both existing manifests,
generates traces only in system temporary storage, and runs the independent
oracle and RTL in exact lockstep for all full50 and capacity22 traces.  The
candidate always uses atomic-ready during this trace replay; stall semantics
are covered by the directed lockstep and direct RTL tests.  Manifest sink modes
are not reinterpreted as independent lane ready.  A transport needing
independent lane stalls belongs in a separate buffered link adapter downstream;
its drain state must not mutate scheduler policy.

## Reproduction

```sh
python3 rtl/candidates/a3_exact_scalar_prefix_k2/run.py \
  --output rtl/candidates/a3_exact_scalar_prefix_k2/evidence/results.json

cd rtl/candidates/a3_exact_scalar_prefix_k2
python3 -B -m unittest -v \
  test_candidate.py test_cross_validation.py test_common_binding.py
```

The runner fails closed if Icarus, VVP, or Yosys is absent, if a frozen SHA or
manifest relation differs, if compilation has an unexpected diagnostic, if
any cycle differs, if a mutation escapes, or if synthesis metrics cannot be
parsed.  `A3_K2_IVERILOG`, `A3_K2_VVP`, and `A3_K2_YOSYS` may select explicit
caller-owned executables.

## Claim limits

- Evidence is local Python 3, Icarus, and generic Yosys/ABC only.  Xcelium and
  formal verification were not run.
- The 45-cell generic topological path crosses replicated lookahead and is not
  a physical delay or achieved Fmax.  There is no standard-cell area, power,
  placement, routing, or clock result.
- Only atomic bundle backpressure is supported.  There is no proof or claim of
  safe independent per-lane acceptance.
- The A5 v1 diagnostic uses a separately synthesized and charged two-entry
  ordered-link adapter. It is not part of scheduler semantics or scheduler PPA,
  and its evidence remains HOLD. The exporter preserves registered owner-offer
  latency and binds owner commit/oracle identities.
- The address-only boundary does not preserve payload, polarity, event type,
  or occurrence identity.
- The frozen replay assumes one pending occurrence per source and an
  always-accepting atomic output.  Directed stalls do not constitute exhaustive
  temporal proof.

See [evidence/report.md](evidence/report.md) and machine-readable
`evidence/results.json` for the frozen receipt.
