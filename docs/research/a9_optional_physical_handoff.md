# A9 immutable optional physical handoff

Date: 2026-08-07. Status: candidate-owned preparation only; no server run and
no common-flow change. Independent review `30b63e0` initially found the live
worktree ineligible; this package addresses its B1--B5 release conditions but
does not self-approve a run. The local decision at `e571e67` remains controlling:
always-ready A9 is rejected, static N=64 is only a timing-first conditional
experiment, and H2 is only an asymmetric-independent-stall conditional
experiment. Neither package is an N=16 default shortlist.

## Two experiments, two capabilities

The packages are deliberately non-substitutable.

| Contract | Static timing experiment | H2 stall experiment |
| --- | --- | --- |
| manifest | `static_n64_timing.lock.json` | `h2_n64_asymmetric_stall.lock.json` |
| synthesis top | `a9_static_n64_timing_top` | `a9_h2_n64_asymmetric_stall_top` |
| frozen parameters | N=64, L=8, A=16, S=6 | N=64, L=8, A=16, S=6 |
| purpose | test whether local depth can overcome its cell/state cost when the same-L central path misses timing | test H2 only under persistent lane-0 stall with the paired lane idle |
| positive capability | static distributed transport; no ready-to-valid dependency | one-hop handoff of a fresh, unpinned head to its paired idle lane |
| controlling negative | no always-ready balance or PPA win is claimed | zero always-ready gain; partner load and pinning can suppress migration |

Each top has fixed-width packed physical ports and exposes no parameter that can
silently turn it into N=16. Flattening is wire-only. Inside that wrapper it
instantiates the exact `a9_phase4_synth_top` registered shell used for the N=64
generic comparison: source-valid/event and retire-ready are registered before
the core, and source-ready plus every retire output are registered after it.
Thus the static, H2, and future same-L central physical comparison must all use
the same boundary; using the older direct-core wrapper is a different identity.
Exact source order and hashes are in its lock manifest. The source contract is
one occurrence per source per cycle, source-local ordering, synchronous
ready/valid, and independent retire-ready per lane. Reset is asynchronous
active-low and discards occupied state; deassertion must be synchronous to
`clk_i` in the integration environment.

The external interface is identical in width, but this does not make the
capabilities interchangeable:

```text
clk_i:1, rst_ni:1
source_valid_i:64, source_ready_o:64, source_event_i:1024
retire_valid_o:8, retire_ready_i:8, retire_event_o:128,
retire_source_o:48
```

The provisional constraint envelope is also exact: rising-edge `clk_i`, 5.000
ns period, 0.100 ns uncertainty, 0.250 ns input delay, 0.250 ns output delay,
and 0.010 pF load on every non-clock output. These values are an experiment
contract, not a technology claim. Changing one creates a new manifest. The
library, PVT corner, RC tech, tool version, run Tcl, SDC, and physical settings
cannot be guessed locally; they must be frozen by SHA256 in a head-approved
`site_freeze.json` before Genus is eligible.

The functional pin count is 1,344 (1,096 inputs and 248 outputs, excluding
clock/reset). The registered shell is part of synthesis and costs 1,344 state
bits at N=64/L=8; the wire-only packed normalization costs zero state. This is
the exact phase-4 comparison boundary, not the zero-register wrapper reviewed
in the initial A4 snapshot.

Unsupported in both packages: N=16 shortlist use, asynchronous ingress,
multicast, more than one occurrence per source per cycle, global remap or
crossbar, fixed-stripe balance claims, CDC qualification, DFT signoff, and any
signed-off area/power/timing conclusion. Static additionally does not promise
progress for a source whose home retire lane remains stalled. H2 additionally
does not promise an escape when the paired lane is occupied, or after an event
has been pinned by output backpressure.

## Xcelium eligibility gate

The candidate gate is the unchanged common `aer_clean_tb` interface,
assertions, scoreboard, and frozen 46-run N=16 neutrality manifest. Static uses
the distributed common binding; H2 uses the diffusive binding and its exact
compile define. This N=16 run is architectural eligibility evidence only. It
does not promote either N=64 profile, does not become an N=16 shortlist, and
does not turn the N=64 diagnostic into a ranking result.

A second exact Xcelium command elaborates the fixed N=64 registered physical
top and its profile-specific source closure. It is an elaboration identity
check, not a substitute for the common scoreboard. H2's 25--100% asymmetric
stall results remain the already frozen local phase-4 diagnostic; because that
workload is not a frozen common trace, it cannot independently open Genus.

A head-approved environment must archive tool version, both exact commands and
their command logs, the N=64 elaboration log, and all 46 common logs with SHA256 in
`xcelium_eligibility.json`. The preflight opens every log, requires the actual
`AER_CLEAN_TEST_PASS <run>` marker, and rejects fatal/fail/nonzero-error text;
it also hashes each prepared trace, per-run manifest, metrics CSV, and
event-metrics CSV, then parses the metrics scoreboard's `errors=0`. It does not
trust a JSON `pass_marker` field. The elaboration log must contain
`A9_PROFILE_ELAB_PASS <top>` and no Xcelium error. Evidence from the other
profile, another command, or an in-repository untracked path is rejected.

`scripts/a9_physical_handoff_preflight.py MANIFEST --stage xcelium` never runs
Xcelium. Without external head-approved evidence it exits 3. Genus remains
blocked until both common-workload and profile-elaboration evidence pass.

## Genus preflight

Before any Genus invocation, the preflight requires:

1. the entire worktree is clean, the manifest is tracked at HEAD, every
   candidate/common source and filelist is present in the declared package
   commit, and both committed-blob and working-file SHA256 match;
2. exact-top Xcelium eligibility with zero errors and the expected pass marker;
3. explicit head approval plus SHA256 locks for Genus version, run Tcl, SDC,
   every timing library, the named PVT corner, and an exact copy of the
   manifest clock/reset/IO/load contract;
4. the manifest clock/reset/IO delay/output load applied without wildcard
   broadening; and
5. a separate same-boundary, same-L centralized reference lock before any
   comparative ranking.

The subsequent Genus evidence must show `check_design=PASS`, zero unresolved
references, zero unconstrained endpoints, zero inferred latches, and locked
mapped-netlist, emitted-SDC, and log hashes. A report without those fields is
diagnostic only. Static is evaluated for timing first; H2 must additionally
report the ready-to-valid path and use activity from its specified stall
workload rather than vectorless power as an official result.

## Innovus preflight

Innovus is ineligible until the exact Genus evidence passes. The head-approved
site freeze must additionally lock the Innovus version, run Tcl, constraint
file, timing/LEF libraries, PVT/RC corner, floorplan/core dimensions, utilization,
aspect ratio, pin placement, power grid, CTS, routing, extraction, and output
load interpretation. Netlist and SDC hashes must match Genus evidence. Any
period, corner, utilization, floorplan, source, or top change is a new run
identity and must not reuse evidence.

The result set must include setup/hold, unconstrained-path count, DRC/connectivity,
area, sequential/combinational split, clock/reset fanout treatment, routed wire
length, congestion, and power methodology. H2 must preserve the 0% stall
control (expected delivered gain: zero) and separately use 25/50/75/100% lane-0
stall windows with paired lane 1 ready and idle, one event/cycle rotating over
sources 0--7 for 1,000 measured cycles. Migration coverage, delivered gain,
added toggles/event, and ready-to-valid cost are mandatory disclosures.

## Fail-closed use

The preflight only hashes files, interrogates Git read-only, and validates
external evidence. It never calls Xcelium, Genus, Innovus, or a workload.
Dirty/untracked repository state blocks even package validation, and evidence
must live outside the repository so it cannot masquerade as a manifest input.
`--stage genus` without exact Xcelium and site-freeze evidence exits 3;
`--stage innovus` additionally requires exact Genus evidence. This repository
intentionally contains no server result and no common-flow adapter. The package
is an optional handoff, not authorization to run it.
