# A7 W6 weighted-fovea DDR composition contract

Status: digital RTL candidate; physical qualification **HOLD**.

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

## Candidate-only regression

The synthesizable `a7_weighted_fovea_weight_contract_fixture` has the native
interface and a 12-slot `1:5:5:1` aggregate row schedule.  It is explicitly
`UNIT_MODEL_ONLY`: its row sequence differs from the real canonical RTL and it
is not canonical qualification evidence.  Its regression checks:

- 120 continuous full-contention accepts with row counts `10:50:50:10`;
- 16 one-shot addresses and exact final order/address;
- legal full drain, reset quiescence, re-arming, and four post-reset events;
- one-hot acceptance and exact `accepted=delivered=140`, with no duplicate,
  phantom, loss, reorder, or protocol fault.

Reproduce the self-contained unit run:

```
scripts/run_a7_weighted_fovea_ddr.sh
```

Run the same scoreboard against the actual canonical three-file source set:

```
A7_W6_CANONICAL_DIR=/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures \
scripts/run_a7_weighted_fovea_ddr_qualification.sh
```

The qualification runner requires and hashes exactly `arbiter2.v`,
`arbiter4_tree.v`, and `aer_tx16_trad_rowcol_fovea.v`.  Frozen SHA-256 values
are respectively
`25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684`,
`108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31`,
and `353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e`.
Any missing or mismatched blob fails before compile.  Only this path may print
`A7_W6_EXACT_CANONICAL_QUALIFICATION_PASS`; the model runner can print only
`A7_W6_UNIT_MODEL_ONLY_PASS`.

The exact runner suppresses only Verilator `UNOPTFLAT` for the canonical nested
`arbiter2` grant equations; source hashes prevent modifying those read-only
files to add tool pragmas.  All other warnings and errors remain fail-closed.

Both runners record source/tool hashes, refuse output overwrite, require every
exact PASS sentinel, and check that common benchmarks/TB and existing R1 RTL
have no diff from the W6 base commit.  The exact runner additionally requires
Yosys `hierarchy -check`, process lowering, and `check -assert` on the complete
canonical+composition+R1 hierarchy.  Neither mode is physical qualification.

## Qualification boundary

RTL simulation establishes the logical phase contract only.  ICG/DDR cell
mapping, generated-clock STA, half-cycle rise/fall constraints, duty/skew,
recovery/removal, CDC/RDC, pin loading, activity, energy/event, PVT, and P&R are
not established by W6.  Full-link PPA remains **HOLD** until the previously
frozen trusted physical experiment gates are independently satisfied.
