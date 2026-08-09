# Ganghee `cluster2_steal_buf` and the common held-valid seam

Date: 2026-08-10
Scope: read-only Ganghee snapshot at `/tmp/team-latest-aer/ganghee`; test and
documentation changes are confined to A3.  This note does not change the
previous A3 candidate rejection decisions.

## Decision

The address-only `aer_tx16_trad_rowcol_fovea_cluster2_steal_buf` can connect to
the common one-outstanding, level-valid source seam with a **zero-state
combinational admission seam**, subject to the precise contract below:

```systemverilog
assign native_arrival   = source_valid;
assign source_ready     = ~native_overrun;
assign accepted_bitmap  = source_valid & source_ready; // scoreboard observation
```

`native_overrun` must be sampled for the admission edge before the native
counter nonblocking updates.  It is a per-source rejection/not-ready indication
for the currently held common offer; it must **not** be accumulated once per
held cycle as logical `source_overrun`.  Common `source_overrun` remains an
occurrence that arrives while the common one-entry source latch is occupied.

This seam has no queue, event storage, arbitration, edge detector, payload
reconstruction, or retry state.  “Zero-state” does not mean zero cost: its
arrival fanout, 16-bit overrun inversion, ready fanout, and any timing repair are
mandatory common-ingress PPA/timing paths.  The existing common source latch
holds the logical event.  The DUT's 16 two-bit counters are candidate storage
and remain inside its PPA boundary.

The mapping is valid only for the frozen address-only boundary, at most one
occurrence per source per cycle, and an always-ready output observer.  It is not
a general arbitrary-payload or output-backpressure adapter.

## Contracts being joined

The common seam defines one pending event per source.  `source_valid[s]` remains
high and its event remains stable until an edge with
`source_valid[s] && source_ready[s]`.  A later occurrence while that latch is
occupied is the source-overrun event.

The native DUT instead exposes:

- `arrival[15:0]`, described as an occurrence pulse;
- `overrun = arrival & pending_full`;
- a two-bit counter per source with legal occupancy 0, 1, or 2; and
- two registered address-only `valid,row,col_mask` outputs.

At an edge, for source `s`, let `V` be held common valid, `F` be the native
pre-edge full predicate, and `G` be the native pre-edge grant.  With the allowed
binding:

```text
arrival = V
overrun = V & F
ready   = ~(V & F)
accept  = V & ready = V & ~F
```

Native occupancy then follows the existing RTL law:

```text
next_count = count + accept - grant
```

where a full `arrival+grant` edge rejects the offered event and decrements
2 -> 1.  The common latch therefore remains valid on that edge.  On the next
non-full edge, the same held event transfers once and the common latch clears.
There is no combinational loop: `arrival` depends only on registered/common
`source_valid`, while `ready` observes `overrun`.

Electrically, consecutive pulse cycles and a held-high bit are indistinguishable
at this synchronous bitmap port.  What prevents recounting is the admission
handshake: after the first non-full edge, common valid clears.  A full edge has
no native state increment, so retrying the same held offer creates no duplicate.

## Directed actual-RTL evidence

Test:
`tests/clean_native/aer_ganghee_steal_buf_seam_contract_tb.sv`

The test instantiates the unmodified snapshot plus `arbiter4_tree.v` and
`arbiter2.v`.  The observer decodes only valid-gated raw row/column masks.  Build
and run output is under `/tmp/a3-steal-buf-seam-contract`.

The committed runner pins and verifies all three inputs before compilation:

```text
arbiter2.v                                             25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684
arbiter4_tree.v                                        108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31
aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v        56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96
```

It independently compiles and runs both the seam counterexample TB and the raw
arrival/overrun TB, requiring their complete PASS lines with exact matching.

### Completion-ready is incorrect

The naive zero-state mapping `arrival=source_valid` with ready derived from the
registered raw output holds the same valid bit until service is observed.  The
DUT admits that bit on every intervening edge.  One logical source-4 event
produced three native outputs:

```text
SERVICE_READY_DUPLICATE outputs=3 logical_events=1
```

Thus native output is delivery, not ingress acceptance, and cannot be used as
common `source_ready` for this buffered DUT.

### Edge detector is stateful and incomplete

`arrival = source_valid & ~seen` suppresses the simple duplicate, but costs one
`seen` bit per source.  The no-contention test emitted exactly once:

```text
EDGE_STATE_SINGLE outputs=1 logical_events=1
```

After actual RTL prefill made source 4 full, its one-shot pulse observed
`overrun=16'h0010`.  A simultaneous old-event grant freed capacity, but `seen`
suppressed any retry while common valid remained held.  Only the eight admitted
prefill events emerged; the ninth logical event was lost:

```text
EDGE_STATE_FULL_LOSS prefill_admitted=8 outputs=8
```

An edge detector alone is therefore neither a free binding nor a correct one.
Making it correct requires retry/acceptance state and turns it into ingress RTL.

### Stateless admission mapping is correct

The same full setup was tested with `arrival=source_valid` and
`source_ready=~edge_overrun`.  On the full edge the event remained held.  On the
next non-full edge it transferred and valid cleared.  Drain produced all eight
prefill admissions plus the new event exactly once:

```text
STATELESS_ADMISSION_RETRY prefill_admitted=8 outputs=9
GANGHEE_STEAL_BUF_SEAM_CONTRACT_PASS
```

## Accounting required by the 50/22 traces

For each clock edge and source:

1. Present the common pending bit directly on `native_arrival`.
2. Sample `native_overrun` at the admission edge, before native NBA updates.
3. Record common acceptance iff `source_valid & ~native_overrun`.
4. Clear the common pending latch only for that accepted bitmap.
5. Treat a new trace occurrence while that common latch remains occupied as
   `source_overrun`; do not count repeated native overrun levels as new events.
6. Decode delivery only from each actual `valid,row,col_mask` lane.  Pop the
   oldest observational event ID for that source; this scoreboard state must
   never feed or retain DUT input.

The required conservation equations remain:

```text
generated = common_source_overrun + common_pending + accepted
accepted  = delivered + native_pending       // before drain
accepted  = delivered                        // after complete drain
```

The frozen generator rejects duplicate occurrences for one source in one cycle,
so the 16-bit level/pulse vector is sufficient.  If that trace rule changes, a
single bitmap bit cannot represent multiplicity and the mapping must be rejected
or charged for an ingress structure.

`native_overrun` is combinational and can change after the edge as occupancy is
updated.  Sampling after `#1` is invalid: the direct-native test saw seven false
new-full indications and hid six real full+grant rejections.  Sampling inside an
unskewed `@(posedge)` process happened to observe the old value under the pinned
Verilator scheduler, but that active-region ordering is not portable evidence.
The committed TBs instead drive at negedge and snapshot one time unit before the
next posedge.  A reusable simulator-independent harness should use a SystemVerilog
clocking-block input sampled with `#1step`, or another explicit pre-edge monitor
phase.  A post-NBA sample is never the admission decision.

## PPA boundary and rejection conditions

Under the allowed mapping there are no seam state bits, but the combinational
common-ingress path is not free.  Candidate/common-ingress PPA and timing must
include at least:

- `source_valid` fanout into all native arrival/counter admission cones;
- 16 overrun inversions and their `source_ready` fanout/load;
- the full `pending_full -> overrun -> source_ready` timing path and any buffers,
  replication, or timing repair needed to close it;
- all 16 x 2-bit native pending counters;
- both arbitration trees and steal selection logic;
- registered output lanes.

The seam becomes a stateful, charged synthesizable ingress block if any of the
following is added:

- per-source `seen`, pulse-arm, retry, or acceptance-history bits;
- an occurrence FIFO or a second pending latch outside the native counters;
- output-derived admission or replay scheduling;
- payload/event-ID storage used to reconstruct arbitrary events; or
- sink-ready buffering, since the native output has no backpressure port.

If a downstream flow insists that `arrival` must be a literal one-cycle pulse
and refuses the held-offer interpretation, the seam is **not** directly
compatible.  The minimum correct ingress must then retain one outstanding offer
and its retry/admission status per source; those state bits, pulse generation,
full/retry control, and timing paths are synthesizable candidate RTL and belong
inside PPA.  Reusing the common source latch through the zero-state combinational
admission seam is the only state-free composition established here.

## Tool note

Verilator 5.032 compiled the SHA-pinned unmodified snapshot with
`--gate-stmts 0`, required
because its optimizer misclassifies the `arbiter2` cross-bit continuous
assignment.  Remaining snapshot warnings are the two 4-bit-to-16-bit shifts in
`granted_bitmap`, unused grant bits, missing timescales, and the same arbiter
`UNOPTFLAT` warning.  They did not change the directed result but remain RTL/tool
portability questions rather than binding features.
