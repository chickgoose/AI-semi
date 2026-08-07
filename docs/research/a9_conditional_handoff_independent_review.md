# A9 conditional physical handoff independent review

Date: 2026-08-07 (Asia/Seoul)  
Reviewer: A4, read-only independent review  
Decision: **BLOCK / NOT_FROZEN** for both physical profiles

## 1. Scope and evidence snapshot

This review did not modify A9, did not use SSH/tmux, and did not run Xcelium,
Genus, Innovus, or any server flow.  It inspected the A9 worktree at:

```text
worktree: /home/chickgoose/projects/a9
branch:   agents/a9-distributed-token-fabric
HEAD:     e571e67b00eef39e60560b5cc71195ff31faafe5
```

The physical handoff was a live, uncommitted worktree at the review snapshot:

```text
 M docs/research/a9_distributed_token_fabric.md
?? docs/research/a9_optional_physical_handoff.md
?? rtl/candidates/a9_distributed_token_fabric/physical/
?? scripts/a9_physical_handoff_preflight.py
?? tests/a9/physical/
```

Consequently, `e571e67...` does **not** contain the reviewed physical tops,
filelists, eligibility TB, handoff document, preflight, or lock manifests.  A
later A9 commit is a different review object and must be re-reviewed; this
document does not pre-approve it.

## 2. Executive findings

| Check | Finding | Verdict |
| --- | --- | --- |
| Separate profile top | Static uses `a9_static_n64_timing_top`; H2 uses `a9_h2_n64_asymmetric_stall_top` | PASS at source-layout level |
| Separate synthesis filelist | Static has three ordered sources; H2 adds only `a9_neighbor_handoff_fabric.sv` and its own top | PASS at source-layout level |
| Separate capability | Prose distinguishes timing-only static from fresh-head paired-lane H2, but there is no committed machine-readable capability/lock record | BLOCK |
| N16 confusion resistance | Tops hard-code N=64/L=8/A=16/S=6 and names/comments say N64; however no manifest is present and the repository still has the unrelated default-N16 `a9.f`/binding path | BLOCK until immutable registry exists |
| Xcelium workload eligibility | Candidate-only directed TB, not the unchanged common TB/46-trace workload contract; no trace/capability/result hash | BLOCK |
| Global scan/tree/remapper | None found in the synthesizable profile source closure; topology is eight fixed linear stripes, with H2 only pairing lane `i` with `i^1` | PASS |
| Source/pin/lane boundary | Packed port widths and source/lane ordering are inspectable, but register boundary and constraint identity are not machine-frozen | BLOCK |
| SHA/commit freeze | Reviewed files are untracked at A9 HEAD and no `*.lock.json` exists | BLOCK |

The safe handoff status is therefore `PENDING_A9_IMMUTABLE_PACKAGE`, not
`READY_FOR_HEAD_XCELIUM`, and certainly not Genus/Innovus eligible.

## 3. Profile separation and N16 ambiguity

The two proposed synthesis closures are genuinely distinct:

```text
static_n64_timing.f
  a9_empty_slot_cell.sv
  a9_distributed_token_fabric.sv
  a9_static_n64_timing_top.sv

h2_n64_asymmetric_stall.f
  a9_empty_slot_cell.sv
  a9_distributed_token_fabric.sv
  a9_neighbor_handoff_fabric.sv
  a9_h2_n64_asymmetric_stall_top.sv
```

Both tops use fixed localparams, not overridable top parameters:

```text
NUM_SOURCES=64, RETIRE_LANES=8, ADDR_WIDTH=16, SOURCE_WIDTH=6
```

Thus an elaboration of either named physical top cannot silently become N16.
The H2 top also cannot silently become static, because it directly instantiates
`a9_neighbor_handoff_fabric`; the static top directly instantiates
`a9_distributed_token_fabric`.

This source-level separation is not yet a usable immutable registry.  There is
no `static_n64_timing.lock.json` or
`h2_n64_asymmetric_stall.lock.json`, despite the handoff prose naming both.
There is no committed candidate key/capability checksum that a head runner can
select.  The older tracked `rtl/candidates/a9_distributed_token_fabric/a9.f`
and `a9_clean_binding.sv` retain default N16 parameters and compile-time
selection for the common benchmark.  A human using “the A9 filelist” rather
than the exact future lock can therefore select the wrong boundary.  The fixed
top names reduce this risk but do not satisfy the common PPA registry contract.

## 4. Xcelium eligibility is not the common workload gate

The two Xcelium filelists are separated and the H2 list requires the H2 top.
The shared `a9_n64_physical_eligibility_tb.sv` chooses H2 only when
`A9_PHYSICAL_H2` is defined; without an immutable command lock, compiling the
H2 filelist without that define would leave the TB referencing the absent
static top.  No current manifest freezes the define or exact command.

The directed TB checks useful unit/integration properties:

- four simultaneous rounds across all 64 sources;
- source-local payload/order, duplicate and phantom rejection;
- 64 offers rotating only over sources 0--7;
- one exposed stalled-head stability case; and
- reset while occupied, with H2 migration coverage.

It does **not** run the frozen common architecture-neutral TB or the N16
46-trace manifest.  It has no common trace SHA, capability-profile SHA,
occurrence-to-delivery latency, p95/p99/max wait, fairness, source overrun,
load sweep, spatial permutation, or activity-window result.  In the static
profile it explicitly makes all lanes ready during the hotspot section, so it
does not qualify static behavior under the H2 asymmetric-stall workload.
Passing this TB may establish a directed N64 smoke check, but cannot establish
the common physical-contract stage-1 eligibility gate.

The proposed preflight also does not close this gap:

1. `--stage xcelium` validates only the package and prints a command; it does
   not check common workload identity or an eligibility result.
2. Genus-stage verification trusts JSON fields for `error_count` and
   `pass_marker`; it hashes the log but does not verify that the expected marker
   occurs in that log or that the logged command matches the manifest command.
3. `verify_package()` checks N=64 parameters but does not validate an allowed
   top/profile pair, capability fields, ports, register boundary, clock/reset,
   I/O constraints, Xcelium define/filelist, or completeness of `locked_files`.
4. Merely checking that `package_commit` names an existing commit does not prove
   that each locked file is a blob in that commit.  This is particularly unsafe
   here because the reviewed handoff files are untracked relative to the named
   commit.

## 5. Structural audit: no hidden global mechanism found

The synthesizable static closure consists of 8 independent stripes, each with
8 `a9_empty_slot_cell` instances.  Mapping is fixed and contiguous by lane,
with odd stripes reversed physically:

```text
lane = source / 8
even lane position: lane*8 + position
odd  lane position: lane*8 + (7-position)
```

Each cell arbitrates only between its registered local ingress and the previous
cell's upstream token.  There is no source-wide priority scan, prefix tree,
global arbitration tree, address remapper, crossbar, or runtime mapping table.
The loops that sum occupancy and transfers are under `ifndef SYNTHESIS` and do
not enter the physical closure.

H2 wraps the same static fabric and adds only pair-local logic:

```text
neighbor(lane) = lane ^ 1
```

A fresh, unpinned head may retire through its ready, empty paired lane.  The
wrapper adds one `pinned_q` bit per lane.  No multi-hop walk, ring/token
scheduler, global scan, tree, or remapper was found.  The linear stripe itself
is an intentional distributed token transport, not a hidden global arbiter.

This PASS is source-closure-specific.  It remains valid only if the immutable
filelists contain exactly the reviewed sources and no normalization RTL is
added later.

## 6. Physical interface and register boundary

Both fixed tops expose the same boundary:

| Direction | Signal bits |
| --- | ---: |
| Inputs excluding clock/reset | `source_valid_i` 64 + `source_event_i` 1024 + `retire_ready_i` 8 = **1096** |
| Outputs excluding clock/reset | `source_ready_o` 64 + `retire_valid_o` 8 + `retire_event_o` 128 + `retire_source_o` 48 = **248** |
| Total functional pins | **1344** |
| Retire lanes | **8** |

Packed source event `s` is mapped from
`source_event_i[s*16 +: 16]`; lane event/source outputs use the analogous
lane-major packing.  These wrappers add no serializer, lane converter, storage,
or mapping logic.

They also add **no external boundary registers**.  They do not instantiate the
tracked `a9_phase4_synth_top`, whose structural comparison used registered
ingress, registered egress, and registered ready.  Instead, source inputs feed
the core directly; each cell registers a local ingress entry internally, and
retire outputs are driven from the final cell's registered FIFO state.  H2 adds
a combinational `retire_ready_i -> migrate -> retire_valid_o/event/source`
endpoint path plus eight `pinned_q` registers.

At N64/SOURCE_WIDTH=6, the explicit architectural state per cell is 68 bits:

```text
ingress: valid 1 + event 16 + source 6
transport FIFO: count 2 + 2*(event 16 + source 6)
tie-break phase: 1
total: 68 bits/cell
```

This yields 64*68 = **4352 core state bits** for static and **4360 bits** for H2
before synthesis optimization; the physical wrapper adds zero.  Any comparison
that assumes the phase-4 register shell, a different pin boundary, or N16 is a
different experiment.  The handoff prose's phrase “existing
ingress/output/register boundary” is too ambiguous to freeze this distinction.

Reset is asynchronous active-low inside the core.  The prose requests
synchronous deassertion by integration convention, but there is no synchronizer
in the profile closure.  Clock period, uncertainty, input/output delays, loads,
libraries, corners, and exception hashes are also absent from a lock manifest.

## 7. Independent hashes and local falsification

Hashes observed from the live A9 worktree, not from commit `e571e67...`:

| File | SHA256 |
| --- | --- |
| `a9_empty_slot_cell.sv` | `0c8c9f861d14d24149bcf7f21ec4e8d168a09f93ebdc32cfed77760deab1b843` |
| `a9_distributed_token_fabric.sv` | `94a194881fa8ab4d9d766e6498f1e796638d2876dd0c2906970a871ee55dddc1` |
| `a9_neighbor_handoff_fabric.sv` | `9b1a7c79ed07f50ccb9ba29a5dbe3a760be56abd2c60179eadc3d49d7cbfbd4f` |
| `a9_static_n64_timing_top.sv` | `be0602894eba9d8e8b4c19eac41118493e5159463f4d368dba0d2186e3ba2e5a` |
| `a9_h2_n64_asymmetric_stall_top.sv` | `0af8e0621342c84f7a916453215be0f7471228d1076e8e141c7778508b460934` |
| `static_n64_timing.f` | `01f14106efbc32b147cac702a956135b8af0d1d770e1c3b21816a2f1b262a15a` |
| `h2_n64_asymmetric_stall.f` | `44f0de3eefd4f1a319ea3d8fd4bc6a36bf342cab8d735baad48c3d480f7549af` |
| `a9_n64_physical_eligibility_tb.sv` | `87929919654da6020bfd89e887602cb3dadbf1a762f57b2c4a6b0ac9eb981036` |
| `xcelium_static_n64.f` | `a3baa80ff13ba6258ca5b23a396b9a690e97ae7c5246205eca69d031941b74dc` |
| `xcelium_h2_n64.f` | `40ea9b18e470a892d77723c0475e474bd053ee71db58e0d3c4118d5544b6878c` |

Using local Verilator 5.032 with identical lint options, both fixed physical
tops elaborated successfully.  Both reported the same non-fatal core warnings:
an occupancy-debug width expansion and reset being observed synchronously by
assertions while state flops use asynchronous reset.  A directed-TB binary
build was attempted but was inconclusive because `/tmp` had only 95 MiB free
and C++ PCH generation failed with `No space left on device`; this is not
reported as an RTL failure or a test PASS.  No A9 file was changed by these
checks.

## 8. Blocking issues and release conditions

### B1 — no immutable package commit

Commit both complete profiles, their manifests, preflight, docs, and tests.
Each manifest must name its actual containing full commit SHA (or an archived
bundle SHA), and preflight must prove every locked path is tracked at that
commit with matching blob content.  A working-tree hash beside an older commit
is not an immutable freeze.

### B2 — no executable common-workload eligibility

Do not label the directed N64 TB as the common Xcelium eligibility gate.  Freeze
one of these explicitly:

- an unchanged common-TB/native-binding run with the exact workload manifest,
  capability profile, trace/result/log hashes, and metrics required by the PPA
  contract; or
- a documented `UNSUPPORTED/SKIP` for the common N16 gate, leaving these N64
  runs as non-ranking conditional diagnostics only.

H2's asymmetric-stall capability additionally needs a frozen workload that
exercises independent lane backpressure and proves migration coverage without
weakening stalled-output stability.

### B3 — incomplete profile/capability lock

Provide two machine-readable manifests with distinct candidate keys, tops,
synthesis filelists, Xcelium filelists/defines/pass markers, capabilities,
unsupported cases, parameters, pin counts, lane counts, register boundary,
normalization RTL, and common-contract/capability hashes.  Reject generic
`a9.f`, `a9_clean_binding.sv`, N16, or the other profile at preflight.

### B4 — boundary and constraints are not frozen

State explicitly that the physical shell has zero added registers and differs
from `a9_phase4_synth_top`; freeze the 1344 functional-pin boundary, eight
independent retire-ready lanes, async-low reset behavior, clock edge/period,
uncertainty, I/O delays, loads, libraries, PVT/RC, SDC, and normalization cost.
Any registered shell or lane serializer is a new candidate identity.

### B5 — preflight is not sufficiently fail-closed

In addition to hashing current files, preflight must validate profile-specific
schema values and prove commit membership.  Xcelium evidence must bind the
exact command, define, filelist, common workload/trace/capability hashes, and
log; the verifier must confirm the expected marker and absence of errors from
the locked log rather than trust hand-written JSON fields.  Genus must remain
blocked until that evidence exists.  Innovus remains blocked behind exact
Genus netlist/SDC hashes and the common physical contract.

Until B1--B5 are resolved and independently re-reviewed, neither profile is
eligible for head Xcelium, Genus, or Innovus execution.  The static and H2
source structures may be retained as conditional experiments, but they must
not appear in the N16 default shortlist or any controlled PPA ranking.

---

## 9. Addendum — committed-package re-review

Re-review date: 2026-08-07 (Asia/Seoul)

A9 review object: `f1467a83a949b129e86dfced161d74f8a2cb7094`

A9 branch: `agents/a9-distributed-token-fabric`

A9 worktree at start and finish: clean

Disposition: **two critical OPEN items; do not advance to physical execution**

This addendum supersedes the initial live-worktree observations only where a
blocker is explicitly marked `FIXED`.  No A9 file was modified and no server,
Xcelium, Genus, or Innovus process was run.

### 9.1 Blocker status

| Original blocker | Status | Re-review finding |
| --- | --- | --- |
| B1 — immutable package commit | **FIXED** | Both package manifests pass independent package preflight. Every declared synthesis/common source and supporting generator/TB/runner/preflight/doc is checked against both the clean working file and its blob in package commit `b438d6bc65ef21e0baf7edb798a0fd8663d140b1`; each manifest is tracked and byte-identical to A9 HEAD. |
| B2 — executable common-workload eligibility | **OPEN — CRITICAL** | The unchanged common N16 TB/assertions/scoreboard and all 46 run names are now selected, but evidence is not bound to canonical frozen trace contents, and the common gate bypasses the registered physical shell. Details are in §9.4. |
| B3 — profile/capability lock | **FIXED** | Static and H2 have distinct candidate keys, capabilities, tops, synthesis filelists/defines, common implementations/defines, unsupported cases, and fixed N64/L8/A16/S6 identities. Preflight rejects an unknown/cross-profile identity and generic N16 use is explicitly diagnostic-only. |
| B4 — physical boundary freeze/equality | **FIXED** | Both N64 tops now instantiate the same `a9_phase4_synth_top` registered shell and only add identical wire-only packing. Port/lane/register/clock/reset/IO assumptions are locked. This is a deliberate new identity relative to the zero-shell-register wrappers in the initial review. |
| B5 — fail-closed evidence validation | **OPEN — CRITICAL** | Commit/source closure, profile schema, exact commands, per-run PASS markers, metrics `errors=0`, external evidence location, and default blocking are improved. Nevertheless, a fully fabricated evidence directory with non-trace payloads is accepted as Xcelium PASS because submitted artifact hashes are self-authenticating rather than canonical. |

Overall status remains `BLOCK`.  B1, B3, and B4 are closed; B2 and B5 must be
closed before either candidate is eligible for Genus or Innovus.  A genuine
head-run Xcelium log would be useful evidence, but the current verifier cannot
distinguish it from the counterexample below and therefore cannot authorize the
next stage.

### 9.2 Package locks and committed-source/hash checks

The two current manifest hashes are:

```text
static manifest SHA256:
b9a156d63faf0e41dde61c3db8348d75453e9bf990c7e22fa0f62c12807367fd

H2 manifest SHA256:
512da47c6634f3d5bb95082c4b129852705f9b1e8dade9024ed11be9028b29ff
```

Both manifests name source-package commit
`b438d6bc65ef21e0baf7edb798a0fd8663d140b1`, which is an ancestor of reviewed
HEAD `f1467a83...`.  This is coherent: `b438d6b` freezes the source/doc/preflight
closure, while the later `f1467a8` commit relocks the two manifests to those
committed blob hashes.  The manifests themselves are not circularly included in
their `locked_files`; instead preflight requires each selected manifest to be a
tracked file matching its HEAD blob.

Independent executions produced:

```text
A9_PACKAGE_LOCK_PASS a9_static_n64_timing_diagnostic
A9_PACKAGE_LOCK_PASS a9_h2_n64_asymmetric_stall_conditional
```

The package verifier checks a set-equal complete closure, rejects duplicate
paths, hashes every working file, retrieves the same path from the named commit,
and hashes that committed blob.  As a spot check, the current and committed
`a9_phase4_synth_top.sv` both hash to:

```text
eaf3dbc0c60725bce9f8e179745fe2616183dd6a220c5ca75141d5b9d75b7028
```

This resolves the initial untracked-source and stale-commit defect.

### 9.3 External boundary equality

Both fixed physical tops expose exactly:

```text
N=64, retire lanes=8, address width=16, source width=6
functional inputs=1096, functional outputs=248, total functional pins=1344
clk_i rising edge; rst_ni asynchronous assertion/synchronous-deassert contract
period=5.0 ns, uncertainty=0.1 ns
input/output delay=0.25 ns, output load=0.01 pF
```

Both instantiate `a9_phase4_synth_top` with identical parameters. Static selects
the distributed core with `A9_YOSYS`; H2 additionally selects the diffusive core
with `A9_PHASE4_DIFFUSIVE`. The outer wrappers only unpack/pack source/lane bits.
The common registered shell contains 1,344 boundary state bits:

```text
ingress: source_valid 64 + source_event 1024 + retire_ready 8 = 1096
egress:  source_ready 64 + retire_valid 8 + retire_event 128
         + retire_source 48 = 248
```

This establishes static/H2 physical-boundary equality. It does **not** by itself
establish ready/valid functional correctness of that shell; that remains part
of B2.

### 9.4 Critical OPEN B2 — common eligibility does not prove the frozen job

The new common gate is materially better than the old directed TB. It fixes the
top at `aer_clean_tb`, N16/L4/A16, uses the unchanged common interface,
assertions and scoreboard, and locks all 46 names from
`manifest.neutrality-n16.json`. Static selects `distributed`; H2 selects
`diffusive` with `A9_NEIGHBOR_HANDOFF`. The verifier requires one log and
metrics/event-metrics/prepared-trace/run-manifest set per expected run.

Two independent gaps remain.

First, the verifier hashes each `prepared_trace` and per-run manifest only
against the SHA written in the submitted `xcelium_eligibility.json`. It neither
regenerates canonical traces from the locked generator+suite manifest nor
parses the per-run manifest to compare its trace SHA/configuration with the
frozen run. It also does not compare prepared-trace contents to a canonical
hash. Therefore the asserted run name and a clean scoreboard result do not
prove that the named frozen workload was executed.

Second, the common scoreboard compiles `a9_clean_binding.sv`, which instantiates
the direct core. The physical synthesis closure instead instantiates the
stateful `a9_phase4_synth_top`. The N64 physical profile receives elaboration
only; no scoreboard drives its registered ready/valid boundary. The phase-4
source itself labels that shell “Structural-comparison shell only.” Its ingress,
ready and egress registers can change handshake timing and backpressure
behavior, so direct-core common PASS plus physical-top elaboration is not a
functional equivalence proof.

B2 closes only when both conditions are met:

1. regenerate every run from the locked manifest/generator in a temporary
   verifier-owned directory and compare canonical run-manifest, trace, and
   prepared-trace SHA/content (or validate equivalent frozen canonical hashes);
2. run the common scoreboard through the exact registered boundary used for
   physical synthesis at a supported parameterization, or supply a checked
   formal/sequential equivalence proof that covers ready/valid stalls, loss,
   duplicate, ordering, reset, and added latency.

### 9.5 Critical OPEN B5 — accepted forged-evidence counterexample

A local adversarial evidence directory was generated outside the repository.
For every one of the 46 exact run names it contained:

```text
log:            AER_CLEAN_TEST_PASS <expected-name>
metrics CSV:    test=<expected-name>, errors=0
event CSV:      literal NOT_A_REAL_EVENT_CSV
prepared trace: literal NOT_A_FROZEN_TRACE
run manifest:   {}
```

The JSON record contained the hashes of those bogus files, the manifest's exact
command strings, `head_approved=true`, and a nonempty claimed Xcelium version.
No simulator was invoked. Both current profile validators accepted it:

```text
static forged evidence exit=0: A9_XCELIUM_ELIGIBILITY_PASS
H2 forged evidence exit=0:     A9_XCELIUM_ELIGIBILITY_PASS
```

This is a direct falsification of fail-closed workload evidence, not merely a
theoretical concern. The verifier proves internal consistency of claimant-
supplied hashes, not provenance or frozen-trace identity.

The narrower PASS-marker hardening does work. After replacing the first log by
`AER_CLEAN_TEST_PASS_WRONG_NAME`, recomputing its submitted SHA, and rerunning
preflight, it correctly returned exit 3:

```text
A9_PREFLIGHT_BLOCKED: common log lacks clean PASS: core_sparse_identity
```

Thus “log PASS-marker validation” is `FIXED`, while B5 as a whole remains
`OPEN — CRITICAL` because a marker plus self-authenticated artifacts is still
accepted without actual workload provenance. In addition to the B2 remedies,
the evidence schema should bind an exit status and tool invocation transcript
to each run and validate result/event CSV schemas rather than only their
existence.

### 9.6 Blocked-by-default behavior

For both manifests, package-only validation exits 0. Explicit Xcelium and Genus
stages without an external evidence directory both fail closed with exit 3:

```text
A9_PREFLIGHT_BLOCKED: --evidence-dir is required; no run is authorized
```

`--stage genus` additionally re-runs Xcelium evidence validation and requires a
head-approved external `site_freeze.json` whose constraint contract exactly
matches the manifest, with hashed run Tcl, constraints, libraries, tool version,
and PVT corner. Innovus further requires Genus evidence and the physical/RC
freeze. This default-blocking behavior is `FIXED`.

It does not mitigate the forged-evidence counterexample: once the weak Xcelium
record is accepted, only the separate site-freeze check stands between it and
Genus eligibility. Therefore the required immediate head action is:

```text
KEEP A9 STATIC/H2 GENUS AND INNOVUS BLOCKED.
DO NOT TREAT CURRENT A9_XCELIUM_ELIGIBILITY_PASS AS SUFFICIENT EVIDENCE.
RE-REVIEW AFTER B2/B5 CANONICAL-TRACE AND REGISTERED-BOUNDARY FIXES.
```
