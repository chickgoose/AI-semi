# A2 phase-3 local proxy independent code review

Date: 2026-08-07

Reviewer: A3, read-only review of `/home/chickgoose/projects/a2`

Reviewed snapshot: A2 HEAD `9613b6b`

Scope: wrapper/core boundary, packed equivalence, Yosys 4-LUT flow, VCD scope,
toggle normalization, N=16/N=64 decision code, and `SKIP_YOSYS`. No A2 file,
common benchmark/TB, or server state was modified.

## 1. Executive disposition

The **final N=16 and N=64 reject decisions remain supported**, but two parts of
the current evidence are critical-invalid and must not be quoted:

1. The VCD parser double-counts connected wrapper/core ports and wrapper
   output/Q aliases. Removing only those port replicas changes the sparse
   A2/always-buffered toggle ratio from 81.85% to about 69.2% at N=16 and from
   82.55% to about 70.3% at N=64. Thus the recorded sparse-toggle `FAIL` flips
   to `PASS`; it is not a valid rejection reason.
2. `A2_PHASE3_SKIP_YOSYS=1` still requires a discoverable Yosys executable
   before inspecting cached JSON. Cached JSON also has no RTL/script/option
   hash check. The advertised cached-JSON rerun is therefore neither portable
   to a no-Yosys host nor protected from stale or mixed artifacts.

These faults do **not** rescue A2. Both sizes independently fail aggregate
pressure events/cycle/cell, mapped depth, and recovery-region gates. N=64 also
fails the recurrence tail gate. Those failures do not depend on VCD parsing or
`SKIP_YOSYS`.

| Review item | Verdict | Bias/result impact |
| --- | --- | --- |
| RTL ingress/retire/register boundary | **PASS at RTL/state boundary** | Same wrapper is elaborated for all three; no hidden adapter storage. |
| Post-flatten structural identity of common logic | **NOT GUARANTEED** | `flatten; opt` permits cross-boundary optimization, so common combinational cells are not preserved cell-for-cell. |
| Packed A2 versus canonical phase-2 core | **Qualified pass** | 768-cycle N16/N64 ready-stall comparison passes; randomized simulation is not formal equivalence. |
| 4-LUT mapping fairness | **Same command, narrow proxy** | No model-specific option, but it is LUT+DFF mapping, not generic ASIC cells; `-fast`/LUT4 sensitivity is unmeasured. |
| VCD scope | **CRITICAL FAIL** | Port/Q replicas are counted as separate physical transitions; sparse gate flips after minimal deduplication. |
| Toggle/event denominator | **Defined but mildly A2-favorable at N64** | Full-run toggles divide by all delivered events; A2 admits six more pressure events and amortizes fixed activity over them. |
| N16/N64 decision arithmetic | **PASS with two evidence caveats** | Boolean implementation matches the written thresholds; N16 recurrence overrun and toggle inputs are misleading. |
| `SKIP_YOSYS` path | **CRITICAL FAIL** | Unconditionally probes Yosys and trusts any nonempty cached JSON. |

Head was notified immediately when the two critical faults were found.

## 2. Files and evidence grade

Committed code reviewed:

- `rtl/candidates/a2_adaptive_dual_path/a2_phase3_physical_wrapper.sv`;
- `a2_phase3_selected_packed_core.sv` and `a2_phase3_reference_cores.sv`;
- `tests/a2/a2_phase3_physical_tb.sv` and
  `a2_phase3_packed_equiv_tb.sv`;
- `tests/a2/run_phase3_physical_proxy.sh` and both phase-3 filelists;
- `scripts/a2_phase3_physical_proxy.py`;
- preregistered gate through `0cf40b8`, implementation commit `a8e5d08`, and
  same-cycle-overrun fix `901ea0f`; and
- result report and README committed by `9613b6b`.

Generated evidence reviewed separately from committed source:

- `/tmp/a2-phase3-physical-final` JSON, logs, VCD, CSV, and decision JSON,
  which remain outside Git.

The `/tmp` JSON identifies Yosys 0.52, but the artifact set is not bound to a
Git tree or synthesis-script hash. Its six JSON timestamps precede or differ
from the final commit time, while simulation outputs were regenerated later.
The data may be consistent, but the cached flow cannot prove it.

## 3. Boundary audit

### 3.1 What is genuinely identical

All three `MODEL` choices are inside one
`a2_phase3_physical_wrapper`. The wrapper provides:

- `NUM_SOURCES` 16-bit ingress event registers and valid bits;
- identical overwrite/refill semantics
  `source_ready = !ingress_valid || core_source_ready`;
- one registered retire event/source/valid stage;
- identical elastic retire readiness
  `core_retire_ready = !retire_valid_q || retire_ready_i`; and
- the same packed input/output pins.

The mapped state counts corroborate the boundary:

| N | Expected shared wrapper state | Flat total | Always-buffered total | A2 total |
| ---:| ---:| ---:| ---:| ---:|
| 16 | 293 | 297 | 630 | 636 |
| 64 | 1,111 | 1,117 | 1,482 | 1,488 |

Flat adds only the RR base. Always-buffered adds the B4/D16 payload/source
store, pointers, count, and RR base. A2 adds six surviving control bits over
always-buffered; the `QUIET_CYCLES=1` quiet register is optimized away. This
shows that the equal-capacity reference is not receiving free adapter storage.

The wrapper necessarily adds one ingress capture before every core and one
retire capture after it. Consequently the observed sparse latency is three
cycles for A2/flat and four for always-buffered. This is consistent with the
intended direct-versus-queued distinction behind a common boundary.

### 3.2 What is not structurally locked

The Yosys script runs `flatten; opt` before mapping. The tool may propagate
ready/valid logic across the wrapper/core seam and optimize equivalent-looking
boundary equations differently for each selected model. Therefore these
claims should be separated:

- **valid:** the RTL function, pins, architectural registers, and handshakes
  are identical;
- **not established:** the same named common combinational cell set survives
  in every mapped JSON.

A hard structural equality claim would require hierarchy/keep boundaries or a
reported common-cell subtraction. The current flow instead makes a reasonable
whole-design comparison; it just cannot say the boundary cells are physically
identical after optimization.

### 3.3 Packed mirror equivalence

The packed core fixes B4/D16/E4/X0/Q1 and mirrors the canonical unpacked-array
core. `a2_phase3_packed_equiv_tb` compares ready, retire valid, event, and source
both before and after edges for 768 cycles at N=16 and N=64 with randomized
requests and retire stalls. Both logged runs pass.

This is meaningful cycle-by-cycle evidence, but it is bounded random
simulation, not exhaustive or formal equivalence. It also compares the two A2
cores directly, not all three wrapped models. The physical TB separately checks
transport conservation for the wrapped models. No mismatch was found, but the
word “proved” in the committed report should be replaced by “tested for the
stated sequence.”

## 4. 4-LUT mapping audit

The executed command is the same for all N/model points:

```text
proc; flatten; opt; memory_map; opt; techmap; opt;
abc -fast -lut 4; clean; write_json
```

The JSON cell populations confirm LUT mapping. For example, N=16 contains:

| Design | `$lut` | sequential cells | reported total |
| --- | ---:| ---:| ---:|
| A2 | 5,360 | 636 | 5,996 |
| flat RR | 724 | 297 | 1,021 |
| always-buffered | 4,348 | 630 | 4,978 |

Thus `generic_cells` is actually “4-input LUTs plus mapped FF cells.” It is not
a technology-neutral gate count or an ASIC cell count. `logic_depth` is LUT
levels, and `memory_map` deliberately expands both B4/D16 stores into FF/mux
logic instead of testing SRAM inference. The expansion is symmetric between
A2 and always-buffered; it is deliberately favorable to flat RR, which the
gate does not require A2 to beat.

There is no hidden per-model option and no direct A2 favoritism. There are,
however, two reproducibility/interpretation limitations:

1. The originally frozen `0cf40b8` text said only “generic ABC mapping.” Commit
   `a8e5d08` changed it to 4-LUT mapping while adding the implementation. The
   script additionally uses `-fast`, which the gate document does not state.
   The committed report says an earlier simple-gate N64 attempt was discarded
   for runtime. This is a disclosed methodological amendment, not literally an
   exact mapping option frozen at `0cf40b8`.
2. No non-fast LUT4, LUT6, generic-gate, or alternative ABC seed/script
   sensitivity is reported. Behavioral arithmetic and long procedural
   priority scans can map quite differently across these choices. The observed
   A2 penalty is large and appears structurally plausible, but must be called a
   single FPGA-style local proxy.

The depth implementation itself correctly cuts paths at sequential outputs and
counts combinational driver levels. The fanout implementation does not exclude
clock/reset. At N=16 the reported maxima 636/630/297 are exactly the respective
FF counts and belong to clock/reset, so that fanout gate does not measure data
or control fanout. Excluding clock/reset gives A2 and always-buffered the same
observed maximum of 356, so the `PASS` is unchanged. At N=64 the maximum is an
RR-base data/control bit (2,371 versus 2,334), and the gate also passes.

## 5. VCD and toggle audit

### 5.1 Intended scope

`$dumpvars(0, dut)` records the complete wrapper and selected core. Reset
activity is excluded by `$dumpoff/$dumpon`; clock/reset and the three top-level
inputs are excluded in the parser. The parser correctly ignores parameters,
real values, unknown transitions, and `$var integer` loop variables. Both A2
and always-buffered expose identical-size simulation-only packed storage
shadows because Icarus does not dump their unpacked banks.

The VCD remains enabled throughout stimulus and drain. The numerator therefore
represents accepted work plus drain, and the denominator is total delivered
after drain. That is internally consistent for an energy/event-style proxy,
though it differs from the fixed-window numerator used by EPCC.

### 5.2 Critical duplicate-net error

Icarus emits separately identified VCD variables for several physically
connected or trivially assigned signals. The parser counts each identifier:

- `ingress_valid/event` and the core's `source_valid_i/source_event_i` ports;
- core outputs and wrapper `core_*` wires;
- `retire_*_q` and combinational wrapper output copies; and
- core combinational output variables plus their wrapper wires.

These are not separate post-synthesis capacitive nodes. They are RTL/VCD
representation duplicates. A minimal audit parser retained all real internal
register/combinational/storage-shadow identifiers but excluded core port
replicas and wrapper output aliases. Results were:

| N | Workload | Design | Current toggles | Port-deduplicated toggles |
| ---:| --- | --- | ---:| ---:|
| 16 | sparse | A2 | 1,384 | 690 |
| 16 | sparse | always | 1,691 | 997 |
| 64 | sparse | A2 | 1,523 | 761 |
| 64 | sparse | always | 1,845 | 1,083 |
| 16 | hotspot + recurrence | A2 | 7,782 | 5,082 |
| 16 | hotspot + recurrence | always | 7,634 | 4,862 |
| 64 | hotspot + recurrence | A2 | 12,805 | 8,151 |
| 64 | hotspot + recurrence | always | 12,345 | 7,694 |

Using the unchanged delivered-event denominators:

| Gate ratio A2 / always | Current | Port-deduplicated | Gate |
| --- | ---:| ---:| --- |
| N16 sparse | 81.85% | 69.2% | changes **FAIL → PASS** |
| N64 sparse | 82.55% | 70.3% | changes **FAIL → PASS** |
| N16 pressure | 101.94% | about 104.5% | remains PASS (≤110%) |
| N64 pressure | 101.63% | about 103.8% | remains PASS (≤110%) |

This is not a proposed replacement power model; it is a counterexample proving
that the current gate depends on VCD alias representation. A sound rerun should
count mapped-net activity, or define one canonical RTL signal per physical
function and validate the scope against the mapped JSON. Until then, no sparse
or pressure power conclusion should be promoted.

### 5.3 Denominator bias

`toggle_per_delivered` divides full stimulus-plus-drain transitions by every
delivered event. N16 A2 and always deliver the same 204 pressure events. N64 A2
delivers 297 while always delivers 291, so A2 amortizes common/fixed activity
over 2.1% more work. This mildly favors A2 rather than causing its rejection.
A companion toggles/cycle and toggles/offered-occurrence metric would expose
the sensitivity. `max(1, delivered)` is harmless for the present nonempty
workloads but would conceal a zero-delivery denominator in a future trace.

## 6. Workload and decision-gate audit

### 6.1 N16 recurrence duplicates

The recurrence generator uses six indices with stride four:

```text
(base + index*4) % NUM_SOURCES, index=0..5
```

At N=16, indices 4 and 5 repeat indices 0 and 1 in the same cycle. Commit
`901ea0f` correctly changed `offer()` to count an already asserted
`source_valid` as overrun. The result is 72 deterministic same-cycle duplicate
source offers across 36 bursts. Those 72 account for **all** reported N16 A2
and always-buffered recurrence overruns. They are a generator/source-interface
collision, not a reservoir-capacity difference.

The N16 pressure-overrun gate still computes correctly and still passes, but
`72 = 72` is not positive evidence that the two reservoirs absorbed an
architectural overload. With one legal occurrence per source per cycle, both
would report zero recurrence overrun on the unique four-source portion. N64's
six stride-four sources are unique and its 27 versus 30 recurrence overruns are
meaningful.

### 6.2 Gate-code correctness

The Python decision code implements the written inequalities accurately and
evaluates N=16 and N=64 independently:

- functional checks cover all 24 design/size/workload rows and now require
  `generated = accepted + overrun` plus `accepted = delivered`;
- pressure sums use hotspot and recurrence only;
- aggregate EPCC weights both equal-length pressure windows correctly;
- tail checks both pressure rows against always and flat+16;
- ratios are not rounded before comparison; and
- `recovery_region` requires an individual pressure EPCC point at least as good
  as flat or always.

The surviving reject evidence is:

| Independent gate | N16 | N64 | VCD-independent basis |
| --- | --- | --- | --- |
| Pressure EPCC ≥98% of always | FAIL, 83.0% | FAIL, about 73.2% | fixed-window delivery and mapped LUT+FF count |
| Depth ≤125% of always | FAIL, 137.8% | FAIL, 146.9% | mapped LUT topology |
| Recovery region exists | FAIL | FAIL | no pressure workload beats either reference in EPCC |
| Tail | PASS | FAIL | N64 recurrence p99 46 versus always 45 |

Even if both toggle gates are removed and the N16 overrun equality is treated
as non-evidence, `all(gates.values())` remains false for both sizes.

### 6.3 Current result-document inconsistency

The committed result table lists N16 recurrence toggle/event as
42.11/33.19/42.01. Current `activity.csv` and VCD counts give approximately
38.99/32.28/39.35. The aggregate decision JSON uses the latter data. This looks
like a stale table row and does not alter the machine decision, but it must be
corrected before the result document is committed.

## 7. `SKIP_YOSYS` reproduction audit

The shell script executes these checks unconditionally:

```text
command -v "$IVERILOG"
command -v "$VVP"
command -v "$YOSYS"
```

Only later does it test `SKIP_YOSYS==1 && -s JSON`. A controlled invocation
with valid placeholder Icarus/VVP commands, a deliberately absent Yosys name,
`SKIP_YOSYS=1`, and the complete existing output directory exits 1 before any
JSON reuse. Therefore the skip flag does not do what its name implies on a
host lacking Yosys.

Even when all tools are installed, each nonempty JSON is accepted without
checking:

- Yosys version;
- Git commit or RTL hashes;
- elaborated N/model parameters;
- the exact `abc -fast -lut 4` script; or
- coherence among the six JSON files.

The JSON embeds its creator version and source line locations, but the reuse
code does not validate them. The direct Python analyzer can recompute CSV/JSON
from cached artifacts without Yosys, yet the advertised shell reproduction
path cannot. Cached results must be considered manually curated evidence, not
self-authenticating reproduction.

## 8. Required disposition

No A2 code change is made by this audit. The current evidence should be handled
as follows:

1. Keep the overall **REJECT N16 / REJECT N64** decision because independent
   EPCC/depth/recovery gates fail.
2. Remove sparse toggle failure from the rejection rationale and label all
   present toggle conclusions invalid pending a canonical net scope.
3. Describe N16 recurrence overrun equality as a same-cycle duplicate-source
   artifact, not equal-capacity absorption evidence.
4. Label cell/depth results specifically as Yosys-0.52 fast 4-LUT+FF proxies;
   do not call them generic cells, ASIC area, or timing.
5. Treat the 4-LUT choice as a documented post-preregistration amendment and
   preserve the discarded-run history.
6. Do not claim `SKIP_YOSYS` portability or artifact reproducibility without
   tool-check restructuring and provenance validation.
7. Correct the stale N16 recurrence toggle row before committing the A2 result
   report in a follow-up commit.

This review found no hidden adapter storage, no lane/endpoint mismatch, no
decision-inequality coding error, and no evidence that the two critical faults
were used to turn an otherwise passing A2 point into the final reject.
