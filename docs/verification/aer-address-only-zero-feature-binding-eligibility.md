# Address-Only Zero-Feature Binding Eligibility

Status: **eligibility-accounting HOLD CLOSED** against schema v2 at `1acc9c2`,
2026-08-10. Candidate execution remains unverified, and the governing full-link
qualification itself remains on HOLD pending complete evidence.

## Purpose and authority

This document fixes the minimum functional-binding rule used to decide whether
the current Hyeonsu address-only AER candidates can enter the common
qualification without receiving functionality from the testbench. It records a
read-only audit of `/tmp/team-latest-aer/hyeonsu` and its Ganghee dependencies in
`/tmp/team-latest-aer/ganghee`.

The snapshot has no Git metadata, candidate manifest, native harness, or common
qualification result. Therefore `latest` means only the files present in that
snapshot at the audit time. The SHA-256 values below bind the observations to
those files; they do not establish a clean commit or release provenance.

This revision closes the PPA-accounting ambiguity left by eligibility commit
`d6bc597`: structural functional bindability and physical charging are separate
decisions, and a positive result in the former never waives the latter.

This is a functional eligibility contract, not a claim that any candidate
passed simulation, regression, CDC signoff, synthesis, or PPA qualification.
The governing and latest physical boundary/accounting contract is
[`aer-address-only-full-link-qualification.md`](aer-address-only-full-link-qualification.md).
Its schema-v2 requirements take precedence over any use of “zero-feature” or
“free” in this document. Closing this eligibility HOLD does not release a
candidate for ranking.

## Zero-feature binding contract

The common source model owns at most one pending occurrence per logical source
and holds its request until the candidate returns that source identity. A native
result is an implicit acknowledgement. The binding may mask every acknowledged
source before the next candidate sampling edge, as demonstrated by the existing
stateless Ganghee binding in
[`aer_ganghee_native_binding.sv`](../../tb/clean/native/aer_ganghee_native_binding.sv#L36).

A zero-feature **functional TB** binding may contain only:

- port rename, reset-polarity conversion, constant tie, static slice,
  permutation, concatenation, or zero extension;
- direct connection of a native `ready`, `ack`, `valid`, or request pin;
- combinational acknowledgement and masking of a source identity already
  returned by the native candidate; and
- stateless expansion of a complete native row/column bitmap for functional
  scoreboard observation. For a valid row `r`, each set `col_mask[c]` is the
  logical occurrence at address `4*r+c`.

The binding must contain no register, latch, memory, queue, retry, replay,
duplicate filter, output lock, arbitration, ordering repair, history-dependent
decoder, repeat reconstruction, CDC state, or candidate-specific pending-event
model. It may not acknowledge an event by probing an internal non-port signal.
It may not convert the common held-request source into an unacknowledged
one-cycle pulse.

“Zero-feature” means that the TB adds no state or recovery capability; it does
not mean zero physical cost. Runtime acknowledgement qualification, address or
row/column decode, acknowledged-request masking, bitmap expansion, and retire
fanout may be used in the functional seam to determine structural eligibility,
but every equivalent gate and path is **mandatory charged logic** in ranked
PPA. The TB binding module itself remains excluded as required by schema v2;
the equivalent physical acknowledgement/decoder/normalizer logic must appear
under the synthesis top, be declared as the appropriate physical feature, map
1:1 to a `charged_blocks` entry, and carry hierarchy/evidence hashes.

Only literal port rename, static bit permutation/slice/concatenation, constant
tie, and zero extension of an already recovered address remain free wiring.
If a native bitmap is frozen as the complete receiver boundary, all bitmap pins
are counted and no scalar-RX claim is allowed. If scalar logical completions are
the receiver boundary, the bitmap expander and fanout are charged RX or
normalizer RTL. Functional TB logic is correctness evidence only and never
area, timing, power, or full-link completeness evidence.

## Eligibility classes

- **BINDABLE_UNVERIFIED**: a zero-feature binding is structurally possible for
  the mandatory sink-always-ready profile. No common execution has been run.
- **CURRENTLY_WIRED_PATH**: an in-repository binding and runner shape exists for
  the native ports and required retire capacity. This still does not mean the
  audited snapshot was compiled or run.
- **STRUCTURALLY_ELIGIBLE_NOT_WIRED**: the native timing permits a stateless
  functional binding, but the repository has no matching binding/runner today.
- **SKIP_BACKPRESSURE**: the candidate has no native sink stall mechanism; the
  optional backpressure suite must be skipped rather than emulated.
- **NOT_COMMON_QUALIFIABLE**: the current top cannot obey the common held-request
  occurrence contract without forbidden binding functionality or has an
  independently fatal native handshake defect.
- **PPA_REQUIRED**: any runtime combinational or stateful logic needed by the
  physical path must be present inside the candidate synthesis/activity/PPA
  boundary. A functionally equivalent TB expression does not make it free.
- **DECODER_INCOMPLETE**: a transmitted representation is not a complete
  address event and no synthesizable receiver is present.

## Current eligibility matrix

| RTL top | Native result | Minimum binding | Sink policy | Candidate-owned state/PPA requirement | Decision |
| --- | --- | --- | --- | --- | --- |
| Ganghee raw fovea reference | one complete 4-bit address | combinational returned-address acknowledge and next-edge request mask | no ready | arbiter state and native output registers are charged | **BINDABLE_UNVERIFIED**, **SKIP_BACKPRESSURE**; reference, not a Hyeonsu candidate |
| Ganghee raw cluster2 reference | two complete `(row, col_mask)` event sets | combinational bitmap acknowledge/mask and scoreboard expansion | no ready | both arbiter trees and native output registers are charged | **BINDABLE_UNVERIFIED**, **SKIP_BACKPRESSURE**; reference anchor |
| `aer_4lane_rowsplit` | four complete row-indexed column bitmaps | lane index supplies row; expand and acknowledge all set bits | no ready; outputs overwrite each cycle | all native output registers and four-lane pins are charged | **BINDABLE_UNVERIFIED**, **SKIP_BACKPRESSURE** |
| `aer_adaptive2lane` | two complete `(row, col_mask)` event sets | expand and acknowledge returned bits | no ready | all credit state, selection logic, and output registers are charged | **BINDABLE_UNVERIFIED**, **SKIP_BACKPRESSURE** |
| `aer_fovea_buffered` | complete scalar address after a two-slot buffer | output handshake is too late to prevent repeated capture of the still-held input request | native output ready/valid; full causes drop | the two-slot buffer, pointers, occupancy, and overrun logic are **PPA_REQUIRED** | **NOT_COMMON_QUALIFIABLE** |
| `aer_cluster2_buffered` | two complete bitmap event sets after per-lane buffers | output handshake is too late to prevent repeated capture | per-lane ready/valid; full causes drop | both two-slot buffers and overrun logic are **PPA_REQUIRED** | **NOT_COMMON_QUALIFIABLE** |
| `aer_cluster2_serialized` | one complete bitmap event set selected from two buffered lanes | inherits repeated capture; additionally, selected output can change while stalled as occupancy changes | ready pin exists, but the output selection is not locked during stall | both buffers, occupancy compare, tie toggle, and serialization mux are **PPA_REQUIRED** | **NOT_COMMON_QUALIFIABLE** |
| `aer_cluster2_gals` | two complete bitmap event sets at the read domain | FIFO pop acknowledgement is too late to prevent repeated write-domain capture | per-lane read ready; FIFO full causes drop | FIFO memory, binary/Gray pointers, full/empty logic, synchronizers, both clocks/resets, and CDC constraints are **PPA_REQUIRED** | **NOT_COMMON_QUALIFIABLE**; CDC qualification also unverified |
| `aer_cluster2_bundled_async` | two complete bundled `(row, col_mask)` event sets | req/ack wiring itself is legal, but the preceding buffered ingress has already captured repeated occurrences | four-phase req/ack; buffer full causes drop | lane buffers and both handshake FSMs are **PPA_REQUIRED** | **NOT_COMMON_QUALIFIABLE** |
| `aer_cluster2_redundancy` | full row/mask buses remain physically present; `repeat` tags an equal consecutive value | the wrapper adds a second registered stage after raw cluster2, so the held request can enter twice before its first visible acknowledgement | no ready | previous-valid/row/mask history, repeat generation, output registers, and the full address buses are **PPA_REQUIRED** | **NOT_COMMON_QUALIFIABLE** |
| isolated `predictive_delta_encoder` | delta and bit-count measurement, not a complete framed link | no legal binding can reconstruct occurrences without per-source history and framing | no complete link handshake | encoder history plus a real stateful decoder/framer would be **PPA_REQUIRED** | **DECODER_INCOMPLETE**, excluded from qualification |

The two raw Ganghee rows are reference anchors needed to explain which wrappers
preserve or break the existing stateless acknowledgement timing. They are not
claimed as new Hyeonsu candidates.

## Structural eligibility versus current wiring

The retire capacity below counts logical address occurrences, not native words.
Fixed source-indexed functional slots avoid adding a dynamic compactor.

| Candidate | Maximum logical retirements/cycle | Minimum functional retire capacity | Structural status | Current repository wiring |
| --- | ---: | ---: | --- | --- |
| Ganghee raw fovea | **1** | 1 | **BINDABLE_UNVERIFIED** | **CURRENTLY_WIRED_PATH**: scalar `valid/addr` binding and `RETIRE_LANES=1` runner path exist; the audited snapshot was not run |
| Ganghee raw cluster2 | **8**: two selected rows × four columns | 8 fixed native-lane/column slots | **BINDABLE_UNVERIFIED** | **STRUCTURALLY_ELIGIBLE_NOT_WIRED**: existing binding accepts only scalar `valid/addr` |
| `aer_adaptive2lane` | **8**: two selected rows × four columns | 8 fixed native-lane/column slots | **BINDABLE_UNVERIFIED** | **STRUCTURALLY_ELIGIBLE_NOT_WIRED** |
| `aer_4lane_rowsplit` | **16**: four rows × four columns | 16 fixed row/column slots | **BINDABLE_UNVERIFIED** | **STRUCTURALLY_ELIGIBLE_NOT_WIRED** |

The current specialized runner fixes `RETIRE_LANES=1` and compiles the scalar
Ganghee binding; see `scripts/run_ganghee_native_benchmark.sh:118-128`. That
binding requires the scalar native ports and rejects any retire-lane count other
than one at `tb/clean/native/aer_ganghee_native_binding.sv:17-25`, `:47-53`, and
`:78-82`. No repository binding instantiates raw cluster2, rowsplit, or adaptive
at this snapshot. Supporting their native maximum is plumbing still to be
implemented, not an executed qualification result.

For all four rows, the functional binding may use a fixed slot mapping and copy
the one live pending event into scoreboard-only fields. In physical
qualification, however, the ack decode, request mask, bitmap-to-source decode,
and fanout just described must be included and charged under the governing
full-link contract. The optional sink-backpressure suite remains
`SKIP_BACKPRESSURE`; the binding may not add storage to enable it.

## Evidence for the decisions

### One-stage native outputs can use the stateless acknowledgement seam

The raw fovea and raw cluster2 compute from the currently presented request and
register the result once. See
`/tmp/team-latest-aer/ganghee/aer_tx16_trad_rowcol_fovea.v:76-92` and
`/tmp/team-latest-aer/ganghee/aer_tx16_trad_rowcol_fovea_cluster2.v:73-86`.
The rowsplit top likewise registers the current row bitmap directly at
`/tmp/team-latest-aer/hyeonsu/aer_4lane_rowsplit.sv:40-55`. The adaptive top
registers its combinational grants and selected bitmaps at
`/tmp/team-latest-aer/hyeonsu/aer_adaptive2lane.sv:60-85`. Their returned bits
can therefore be masked before the next sampling edge without binding state.
The corresponding physical ack decode, mask, and fanout remain charged logic.

These candidates still lack native output backpressure. Rowsplit overwrites its
outputs each cycle, and adaptive arbitration/credit advances without a sink
handshake. Their backpressure capability is unsupported, not supplied by the
binding.

### Delayed acknowledgement makes the buffered family ineligible

`lane_buffer2` explicitly has no push-side ready, accepts every `push_valid`
while not full, and drops a push at full:
`/tmp/team-latest-aer/hyeonsu/lane_buffer2.sv:12-23` and `:48-54`.
The fovea and cluster2 wrappers connect registered raw results directly to that
unconditional push interface at
`/tmp/team-latest-aer/hyeonsu/aer_fovea_buffered.sv:29-49` and
`/tmp/team-latest-aer/hyeonsu/aer_cluster2_buffered.sv:54-91`.

Consequently, a common request remains asserted while the first result moves
through the buffer. The raw core samples that same pending occurrence again
before the pop-side result can acknowledge it. Fixing this requires an ingress
acknowledgement port, gating inside the candidate, or duplicate/retry state; none
may be added by the binding.

Serialized and bundled-async instantiate the buffered top at
`/tmp/team-latest-aer/hyeonsu/aer_cluster2_serialized.sv:53-69` and
`/tmp/team-latest-aer/hyeonsu/aer_cluster2_bundled_async.sv:56-72`, so they
inherit the same defect. The serialized selection is also recomputed from live
valid/occupancy at `aer_cluster2_serialized.sv:77-90`; raw pushes can change
occupancy while external `ready` is low, so a stable stalled output is not
established by the current RTL.

### GALS is real candidate state, not a free CDC binding

The GALS top unconditionally writes each raw lane unless its FIFO is full and
reports a drop rather than backpressuring the core at
`/tmp/team-latest-aer/hyeonsu/aer_cluster2_gals.sv:39-64`. Its `async_fifo`
contains memory and binary/Gray pointers (`async_fifo.sv:29-40`), write/read
domain state (`:46-69`, `:81-101`), and two-flop pointer synchronizers
(`:71-78`, `:103-110`). All of that is candidate RTL and must be present in PPA.
The snapshot provides no common dual-clock harness, reset-coordination contract,
CDC constraints, or CDC signoff result.

### Redundancy does not need a decoder, but still misses the binding contract

The redundancy top compares each registered raw result against candidate-owned
history and then registers another output at
`/tmp/team-latest-aer/hyeonsu/aer_cluster2_redundancy.sv:38-84`. On a repeat it
holds the complete row/mask buses rather than removing them (`:63-76`). Thus a
stateful external repeat decoder is not required for this exact RTL, and the
physical address pins must still be counted.

However, the extra registered stage delays the observable acknowledgement by an
additional edge. The same held input occurrence can already occupy both raw and
wrapper stages. Combinationally ignoring `repeat` is not a valid repair because
it cannot distinguish that duplicate capture from a later, legal occurrence at
the same address. The earlier read-only report that classified this candidate as
stateless-bindable is superseded by this corrected pipeline analysis.

### A representation without a receiver is not an eligible address event

The isolated predictive delta file identifies itself as an experimental demo,
not a submitted path, and states that decoder, framing, and pin accounting are
absent at `/tmp/team-latest-aer/hyeonsu/predictive_delta_encoder.sv:4-21`.
Per-source history is visible at `:51-52`. It remains outside the candidate set
unless a real synthesizable TX-link-RX implementation is added and charged.

## Verification state

No common workload, trace regression, native functional test, held-request
counterexample, backpressure test, dual-clock CDC test, synthesis, activity run,
or PPA flow was executed for these rows. Every positive eligibility decision is
therefore **BINDABLE_UNVERIFIED**, not PASS.

The audit performed compile/lint-only probes, which are not functional
execution:

- Verilator 5.032 `--lint-only` accepted rowsplit and adaptive. The Ganghee
  `arbiter2` family produced `UNOPTFLAT` warnings or a Verilator internal
  circular-logic error at `arbiter2.v:14`; this is an unresolved tool-flow
  compatibility blocker, not proof of a functional loop.
- Icarus 12 compile-only elaborated rowsplit, GALS, redundancy, and, with
  concurrent assertions disabled, the fovea-buffered, cluster2-buffered,
  serialized, and bundled-async tops. Its frontend did not accept adaptive's
  unpacked parameter-array syntax. No generated image was simulated.

A candidate may move from **BINDABLE_UNVERIFIED** to common qualification only
after a candidate-native harness demonstrates exact-once delivery under the
held-request contract without adding forbidden binding state. Candidates marked
**NOT_COMMON_QUALIFIABLE** require RTL/interface correction and a new snapshot;
a more capable TB binding cannot change their status.

## Audited file identities

| File | SHA-256 |
| --- | --- |
| `ganghee/aer_tx16_trad_rowcol_fovea.v` | `353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e` |
| `ganghee/aer_tx16_trad_rowcol_fovea_cluster2.v` | `97151241b642d5db1c5974233439dfcea14c4ec325b1d3e91c9caa9d4917c44a` |
| `ganghee/arbiter2.v` | `25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684` |
| `ganghee/arbiter4_tree.v` | `108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31` |
| `hyeonsu/aer_fovea_buffered.sv` | `682762c4658a5c40d4280cff5144f3a09ccf3d7989deb16f8da39f18b5e72b7f` |
| `hyeonsu/aer_cluster2_buffered.sv` | `08e1a83547cbaba15d5fa4b5e4719ef7564c5300c0dc10777e3645b0c897cd49` |
| `hyeonsu/aer_4lane_rowsplit.sv` | `0d885534a7e7422f61fd5b28b3e41c6ed046b4a450aa251e7125bfdaf2abed0a` |
| `hyeonsu/aer_cluster2_serialized.sv` | `f813d00b7ed1d087084b956630f20f34a95d0de548198587ee914445a87e5550` |
| `hyeonsu/aer_adaptive2lane.sv` | `07219f3587b73dee7d75c43c7b57f2229ca058bac9cd608f6c31e93fc33e41be` |
| `hyeonsu/aer_cluster2_gals.sv` | `8d6559362ee3a4a0742dea535ce7bd92a54e075ad21a05d226c09e274b9e0ba4` |
| `hyeonsu/aer_cluster2_bundled_async.sv` | `e5954f589ce39ecd7eda4d7f19d5313cb2b96ad14374b6a6f0da27ad5a215335` |
| `hyeonsu/aer_cluster2_redundancy.sv` | `0bf430cf8d649c091a8222914f94ceeac5c103a045bff62700dcaba798cb9be9` |
| `hyeonsu/lane_buffer2.sv` | `fdeb1e4dce7bbf26215ae83a2b9a3528919562ac3fe265489ca60e32c01b4f27` |
| `hyeonsu/async_fifo.sv` | `b94b58cc9588f9660b6286463ccb44c20f72be0a79719720fc2b82e6e98126b2` |
| `hyeonsu/adaptive_priority_arbiter.sv` | `0a17e6a248e8dfe6633e8bef3c958e95431e49dbc7d29fa68037254b621a61da` |
| `hyeonsu/predictive_delta_encoder.sv` | `f2f5066ed55b06966e4d9a999b266a05e3271e6f413ccb518b78301cc4137f10` |
