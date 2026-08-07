# A4 immutable physical handoff package

Status: candidate source/evidence freeze complete, 2026-08-07. Tool execution
is not authorized by this package.

## Immutable identity

The candidate source snapshot is commit
`5f07aee26a38e6369ce9b0265c58e1a89cae6034`, tree
`ec2d218ff879e794e03654ab4eef54d73277c271`, repository
`https://github.com/chickgoose/AI-semi.git`. This fifth-round commit adds only
the handoff manifest, preflight, tests, filelist, and this document. The
preflight requires the executing HEAD to descend from the source commit and
checks that every protected RTL byte still matches its frozen SHA-256. This
avoids an impossible self-reference in which a commit contains its own SHA.

The machine-readable source of truth is
[`a4_physical_handoff.json`](../../rtl/candidates/a4_quadtree_fabric/handoff/a4_physical_handoff.json).
It freezes source files, ordered filelists, tops, parameter maps, interfaces,
boundaries, capabilities, assertions, common contract/SDC hashes, and local
evidence hashes. A head-created archive must add its bundle SHA and common
library/RC/flow hashes to the external immutable registry row; placeholders in
this source manifest must never be interpreted as zero or as a default.

## Which configuration is shortlisted

The common physical contract requires the official cross-candidate table at
N=16. N=64 is a separate scaling study and cannot replace that row.

| Profile | Exact top | Status | Permitted interpretation |
| --- | --- | --- | --- |
| N=16 | `a4_quadtree_fabric` | `HOLD_FLAT` | Functionally qualified locally on frozen 46 traces; head Xcelium is pending. It is not the A4 local physical recommendation. Head may override only after the cross-candidate review and must record that override. |
| N=64 | `a4_struct_quadtree_top` | `CONDITIONAL_SHORTLIST`, currently blocked | Generic structural scale evidence only. It may become a separate physical scale study after a new immutable commit supplies N=64 common-TB binding, trace qualification, assertions, and capability profile. It is not eligible for the official N=16 table today. |

Therefore the present package authorizes no Innovus run by itself. N=16 can
enter head Xcelium immediately with the frozen native candidate, but Genus/P&R
requires an explicit head override of `HOLD_FLAT`. N=64 preflight intentionally
fails every tool stage until functional eligibility exists in a later commit.
This is the precise meaning of the conditional shortlist.

## Exact synthesis identities

| Item | N=16 native | N=64 structural scale |
| --- | --- | --- |
| top | `a4_quadtree_fabric` | `a4_struct_quadtree_top` |
| filelist | `rtl/candidates/a4_quadtree_fabric/a4_quadtree_fabric.f` | `rtl/candidates/a4_quadtree_fabric/structural/a4_structural_compare.f` |
| positional Genus parameters | `16 16` = `NUM_SOURCES ADDR_WIDTH` | `64 16` = `NUM_SOURCES EVENT_WIDTH` |
| complete parameters | `NUM_SOURCES=16, ADDR_WIDTH=16, SOURCE_WIDTH=4, AGE_WIDTH=8` | `NUM_SOURCES=64, EVENT_WIDTH=16, AGE_WIDTH=8, SOURCE_WIDTH=6`; derived payload 30 |
| defines/includes | `SYNTHESIS`; no include directory | `SYNTHESIS`; no include directory |
| retire width | event 16 + source 4 | event 16 + source 6 + age 8 |
| retire lanes | 1 | 1 |

The common historical config defaults to `aer_dut`, N=4, and a baseline
filelist. All three are wrong for A4. The current `run_stage.sh` registry also
recognizes only its historical baseline. Head must first add the frozen A4 row
to the common registry in its own integration commit; A4 does not modify that
flow here.

## Clock, reset, I/O, and register boundary

Both profiles use `clk` on the rising edge and active-low asynchronous `rst_n`.
The common SDC SHA is
`3b0c8a54c03e56062a154951ffaa479d49fe6e1acaad1130632eca189324497a`.
It false-paths reset, constrains every other input/output, and uses these
provisional screening assumptions:

| Assumption | Frozen value |
| --- | ---: |
| screening period | 5.000 ns; every later period must be explicit |
| input/output delay | 0.250 / 0.250 ns |
| clock uncertainty | 0.100 ns |
| output load | 0.010 pF |
| driver cell | empty until common library policy is frozen |
| provisional corner | `PVT_0P9V_125C` |
| expected library basename | `slow_vdd1v0_basicCells.lib` |

N=16 has no ingress storage inside the native top: the frozen common TB owns
one pending latch per source outside PPA. Four leaf slots and the root/output
slot are inside PPA, giving two registered transport levels. Its output is the
root one-entry ready/valid register.

N=64 structural scale includes 64 one-entry ingress slots inside PPA because
the fourth-round comparison charged identical ingress state to tree and flat.
It then has 21 radix-4 slots including the root/output, with three registered
levels after ingress. This different boundary is another reason N=64 cannot be
silently substituted for N=16 official evidence.

Functional pins excluding clock/reset are 310 for N=16 and 1184 for the N=64
structural profile. Power, events/pin-cycle, and routing comparisons must use
the appropriate complete boundary rather than counting only the retire link.

## Unsupported and unproven capabilities

N=16 supports the mandatory one-lane clean workload and native sink
backpressure. It does not support multi-lane retirement. Its storage-free
simulation binding is not synthesis RTL and adds no arbitration, retry, or
buffering.

N=64 has no frozen common-TB binding, N=64 deterministic trace manifest, event
scoreboard qualification, or qualified capability profile. Multi-lane retire
is absent, polarity/type interpretation is unqualified, and padding is not part
of this exact N=64 power-of-four top. Structural Verilator lint and Yosys mapping
do not satisfy the common-TB eligibility gate.

## Expected assertions and stage records

Before N=16 Xcelium is recorded PASS, the head result record must contain the
exact assertion list from the manifest:

- zero errors and accepted equals delivered after drain;
- no loss, duplicate, phantom, corruption, or source-local reorder;
- node child-ready is onehot-or-zero;
- a full stalled node acknowledges no child;
- stalled output valid/event/source/age is stable;
- reset creates no phantom retirement; and
- all 46 frozen manifest runs emit PASS.

Every downstream preflight consumes a content-checked JSON record with schema
`a4-head-stage-record-v1`. Required identity fields are stage, profile,
candidate key, source commit, top, synthesis-filelist SHA, and PASS status.
Xcelium adds the exact `assertions_passed` list. Genus adds unresolved count 0,
empty-module count 0, explicit period, and common tool-config SHA. The caller
also supplies the record-file SHA, preventing a changed record from being used
under an old command transcript.

## Read-only preflight

Package integrity after checking out the handoff commit:

```bash
python3 tests/a4/physical_handoff_preflight.py \
  --profile n16 --check-package-only --require-clean

python3 tests/a4/physical_handoff_preflight.py \
  --profile n64 --check-package-only --require-clean
```

N=16 Xcelium identity check, before the head runs the frozen 46 suite:

```bash
python3 tests/a4/physical_handoff_preflight.py \
  --profile n16 --stage xcelium \
  --top a4_quadtree_fabric \
  --filelist rtl/candidates/a4_quadtree_fabric/a4_quadtree_fabric.f \
  --num-sources 16 --addr-width 16 \
  --tb-top aer_clean_tb --tb-filelist tests/a4/clean_tb.f \
  --candidate-filelist tb/filelists/a4_quadtree_fabric.f \
  --properties tests/a4/a4_quadtree_properties.sv \
  --trace-manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json
```

Only after the immutable Xcelium PASS record exists may the head preflight a
Genus screen. All paths/hashes below are mandatory; placeholders are not valid:

```bash
python3 tests/a4/physical_handoff_preflight.py \
  --profile n16 --stage genus --override-local-decision \
  --top a4_quadtree_fabric \
  --filelist rtl/candidates/a4_quadtree_fabric/a4_quadtree_fabric.f \
  --num-sources 16 --addr-width 16 \
  --clock-port clk --reset-port rst_n \
  --sdc constraints/aer_common.sdc --defines SYNTHESIS --period-ns 5.000 \
  --input-delay-ns 0.250 --output-delay-ns 0.250 \
  --clock-uncertainty-ns 0.100 --output-load-pf 0.010 \
  --corner <COMMON_PVT_RC_KEY> \
  --library-file <ABSOLUTE_LIBERTY> --library-sha256 <SHA256> \
  --tool-config <ABSOLUTE_HEAD_CONFIG> --tool-config-sha256 <SHA256> \
  --synthesis-mode genus_screening \
  --xcelium-pass-record <ABSOLUTE_XCELIUM_PASS_JSON> \
  --xcelium-pass-record-sha256 <SHA256> --emit-env
```

Innovus diagnostic or final preflight additionally requires the Genus PASS
record and RC tech file. `fixed_netlist` is diagnostic only;
`per_target_resynthesis` is the final comparison mode. Each target period must
rerun Genus and Innovus independently:

```bash
python3 tests/a4/physical_handoff_preflight.py \
  --profile n16 --stage innovus --override-local-decision \
  --top a4_quadtree_fabric \
  --filelist rtl/candidates/a4_quadtree_fabric/a4_quadtree_fabric.f \
  --num-sources 16 --addr-width 16 \
  --clock-port clk --reset-port rst_n \
  --sdc constraints/aer_common.sdc --defines SYNTHESIS --period-ns <EXPLICIT_PERIOD_NS> \
  --input-delay-ns 0.250 --output-delay-ns 0.250 \
  --clock-uncertainty-ns 0.100 --output-load-pf 0.010 \
  --corner <COMMON_PVT_RC_KEY> \
  --library-file <ABSOLUTE_LIBERTY> --library-sha256 <SHA256> \
  --tool-config <ABSOLUTE_HEAD_CONFIG> --tool-config-sha256 <SHA256> \
  --rc-tech-file <ABSOLUTE_QRC_TECH> --rc-tech-sha256 <SHA256> \
  --synthesis-mode per_target_resynthesis \
  --xcelium-pass-record <ABSOLUTE_XCELIUM_PASS_JSON> \
  --xcelium-pass-record-sha256 <SHA256> \
  --genus-pass-record <ABSOLUTE_GENUS_PASS_JSON> \
  --genus-pass-record-sha256 <SHA256>
```

The preflight does not invoke any tool. A PASS only means the candidate identity,
stage order, explicit assumptions, local files, and supplied head records are
consistent. The head must still use the common candidate registry and physical
contract. Current common `run_stage.sh` has no Innovus stage, so no runnable
Innovus command is claimed until the head provides the common driver.

Attempting N=64 `--stage xcelium`, `genus`, or `innovus` currently returns
`A4_PREFLIGHT_FAIL n64_blocked_pending_new_immutable_common_tb_qualification`.
That failure is required and must not be bypassed by changing the top to N=16,
using defaults, or editing the manifest in place.

## Frozen local evidence hashes

The manifest records and the preflight verifies:

- frozen-46 Verilator summary SHA
  `9ee8638b11f54b66dc53e523569e3fe15e049d09c164e61c10937930ee5d7c0d`;
- compressed 87,000-row event evidence SHA
  `306b7dd66a2b508e1a3417fc6982cf39850fe277b6e062a36dc10687efbc0baa`;
- structural gate CSV SHA
  `9b216529c4fc792df40600fb8ba43fbf800c1497d30f0849ea08f513d40a17e8`;
- structural shortlist comparison SHA
  `2fe2501909701f30fee0e765770dd4b8aa995b0e852e6b0852c3652c05f4c9a7`;
  and
- topology/mapping bracket SHA
  `79a5e089f5fb16481ffc1f71927bb9cd5917f7bfad9b0a07d6316ae5e3c6cf6c`.

Server execution, common registry/flow edits, and physical numbers remain head
owned. This package makes wrong-top/default execution fail early; it does not
grant itself permission to run the tools.
