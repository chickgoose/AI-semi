# A7 W6 weighted-fovea DDR composition contract

Status: SHA-pinned directed RTL evidence; full qualification and physical
qualification **HOLD**.

## Owned boundary

`a7_weighted_fovea_ddr` composes an externally supplied canonical N=16 scalar
fovea with the existing `a7_r1_candidate_endpoint`.  The top-level traffic
contract is address-only:

- `source_valid[15:0]` identifies the live source addresses.
- `source_ready[15:0]` is a one-hot-or-zero acceptance vector.
- the fovea output is exactly `valid` plus a four-bit address; there is no
  payload or reconstructed source metadata.
- final delivery is `retire_valid_o` plus `retire_addr_o` at the R1 consumer
  observation boundary.

The canonical fovea module name is macro-selected with
`A7_WEIGHTED_FOVEA_MODULE`.  Its frozen native ports are `clk`, active-high
`rst`, `req[15:0]`, `valid`, and `addr[3:0]`.  The candidate file list therefore
contains no copied team RTL.  Production evaluation must provide the canonical
module and a SHA-pinned external file list.

## Request/acceptance invariant

For the currently registered fovea result, the shell forms a combinational
one-hot `current_result_mask`.  The existing endpoint's safe-release `ready`
directly gates the request:

```
req          = endpoint_ready & source_valid & ~current_result_mask
source_ready = endpoint_ready & fovea_valid
             & current_result_mask & source_valid
```

This is stateless current-result acknowledgement.  It keeps a held level
request from being selected twice while leaving every other current source
visible to the external weighted policy.  The raw fovea result is not gated by
the source match on its path into A7: a phantom result therefore reaches the
final scoreboard and also asserts a combinational current protocol fault.  The
fault has no history state.  There is no queue,
retry state, backpressure compensation, duplicate filter, or metadata store.

The `1:5:5:1` row opportunity policy is wholly inside the external fovea.  W6
does not recreate, alter, or compensate that arbitration policy.

## Reset, timing, and drain

W6 inherits the strict phase-related synchronous R1 clock contract.  Reference
and sample clocks have the same frozen source/frequency and known phase.  This
is not an unrelated-clock CDC design and makes no 2FF synchronizer claim.  The
always-ready A7 endpoint charges its own safe-release arm bit.  The composition
adds no arm state: it gates fovea `req` directly with endpoint `ready`.  Because
endpoint `ready` is zero before the first reset-release edge, the synchronous
fovea naturally samples no request on that edge.

Reset is legal only after `drain_idle_o`.  Drain is fail-closed and includes:

- no live `source_valid`, no masked `req`, and no raw fovea `valid`;
- endpoint drain idle, no same-cycle `source_ready`, and no final retire valid;
- reset released, endpoint ready, and no current protocol fault.

The composition wrapper has zero functional sequential state.  The existing R1
TX, RX, framing, ICG technology boundary, and ref-domain retire observer remain
unchanged and fully charged.

## Candidate-only directed RTL evidence

The synthesizable `a7_weighted_fovea_weight_contract_fixture` has the native
interface and a 12-slot `1:5:5:1` aggregate row schedule.  It is explicitly
`UNIT_MODEL_ONLY`: its row sequence differs from the real canonical RTL and it
is not canonical qualification evidence.  Its regression checks:

- 120 continuous full-contention accepts with row counts `10:50:50:10`;
- after its first acceptance, all 119 remaining full-contention acceptance
  intervals are exactly one reference cycle;
- 16 one-shot addresses and exact final order/address;
- source-acceptance timestamps, output availability exactly one ref cycle
  later, and a real pre-NBA `always_ff` consumer retirement exactly two ref
  cycles later;
- direct `drain_idle_o==0` checks for a live source, same-cycle endpoint
  launch, and pending registered retire output, plus the implication
  `!endpoint_drain_idle -> !drain_idle_o` at reference and burst rise/fall
  observation edges;
- two legal address-6 occurrences separated by full drain and a quiet interval;
- reset release R0 pre-NBA requires endpoint ready, fovea request, source ready,
  and launch all zero; R0 post-NBA requires endpoint ready; the first live
  source acceptance is exactly R2;
- a fully retired pre-reset `P={1,4,9,14}` epoch and disjoint post-reset
  `Q={0,5,10,15}` epoch, with exact membership, uniqueness, count, and stale-P
  exclusion;
- legal full drain, reset quiescence, re-arming, and four post-reset events;
- one-hot acceptance and exact `accepted=available=retired=146`, with no
  duplicate, phantom, loss, reorder, or protocol fault.

A separate expected-fail fixture emits a stale address `a` result while all
`source_valid` bits remain zero.  The negative test requires zero
`source_ready`, a combinational protocol fault, drain low, and the raw phantom
address reaching final retirement.  It then terminates nonzero with the exact
`A7_W6_STALE_NO_LIVE_NEGATIVE_CAUGHT` diagnostic; a zero exit is failure.

The SHA-pinned directed runner also compiles and runs five isolated mutants.
The baseline and every mutant must compile successfully; each mutant must then
exit nonzero with its unique diagnostic: a full-contention bubble, early R0
arm, late R0 arm, removed endpoint-drain term, or suppressed second address-6
grant.  Any mutant that passes, fails compilation, or emits a different
diagnostic fails the gate.

Reproduce the self-contained unit run:

```
scripts/run_a7_weighted_fovea_ddr.sh
```

Run the same scoreboard against the actual canonical three-file source set:

```
scripts/run_a7_weighted_fovea_ddr_qualification.sh
```

The runner prefers the repository-local
`tests/a5_fovea_a7_structural/fixtures` directory when present, then falls back
to the read-only sibling A5 fixture directory.  `A7_W6_CANONICAL_DIR` explicitly
overrides both.

The SHA-pinned directed runner (retaining the historical
`run_a7_weighted_fovea_ddr_qualification.sh` filename) requires and hashes
exactly `arbiter2.v`,
`arbiter4_tree.v`, and `aer_tx16_trad_rowcol_fovea.v`.  Frozen SHA-256 values
are respectively
`25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684`,
`108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31`,
and `353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e`.
Any missing or mismatched blob fails before compile.  This path may print only
`A7_W6_SHA_PINNED_DIRECTED_RTL_PASS`; that marker means the named directed RTL
tests passed against the pinned sources, not full functional or physical
qualification.  The model runner can print only `A7_W6_UNIT_MODEL_ONLY_PASS`.

The exact runner suppresses only Verilator `UNOPTFLAT` for the canonical nested
`arbiter2` grant equations; source hashes prevent modifying those read-only
files to add tool pragmas.  All other warnings and errors remain fail-closed.

Both runners record source/tool hashes, refuse output overwrite, require every
directed PASS sentinel plus the exact expected-fail diagnostic, and check that
common benchmarks/TB and existing R1 RTL have no diff from the W6 base commit.
The SHA-pinned path also rejects any untracked execution input or any execution
input whose worktree content differs from `git_head`; the registry therefore
cannot label dirty directed sources with an unrelated commit.
The SHA-pinned runner additionally requires
Yosys `hierarchy -check`, process lowering, and `check -assert` on the complete
canonical+composition+R1 hierarchy, plus a nonempty JSON structural netlist
containing the composition top.  Tool version output must identify Verilator
and Yosys; a no-op executable cannot manufacture the hierarchy PASS marker.
The protected-diff override accepts only the two frozen source/integration base
commits and rejects arbitrary or non-ancestor revisions.  Neither mode is
full50, full functional qualification, or physical qualification.

## Evidence boundary

RTL simulation establishes the logical phase contract only.  ICG/DDR cell
mapping, generated-clock STA, half-cycle rise/fall constraints, duty/skew,
recovery/removal, CDC/RDC, pin loading, activity, energy/event, PVT, and P&R are
not established by W6.  Full-link PPA remains **HOLD** until the previously
frozen trusted physical experiment gates are independently satisfied.
