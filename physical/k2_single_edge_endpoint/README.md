# A2/A3 single-edge complete-endpoint physical staging

This directory stages two independent GPDK045 Genus/Innovus rows for the
single-edge fallback. It does not claim physical closure today. The immutable
boundary starts at synchronous `source_pending_i`/`source_accept_o` admission
and includes scheduler state, charged buffering, link/control, synchronous
retirement, drain, and protocol-error logic. Testbench generation, coordinate
processing, pads, package, and channel are outside the charged core boundary.

The exact rows are:

| row | top | filelist |
| --- | --- | --- |
| A2 | `a2_batched_iwrr_single_edge_top` | `rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge.f` |
| A3 | `a3_exact_scalar_prefix_k2_single_edge_top` | `rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge.f` |

These are the exact names and bytes from hardened RTL source commit
`6fc5e167918fa4c54786c9a3abb5f60ecd8b991b` and integration commit
`a0a4eb38632245db8ff5937ea5b6c6e3f3839246`; their RTL trees are identical.
Each three-entry candidate list
contains its native scheduler, the exact nested
`rtl/technology/single_edge/filelists/generic.f`, and its complete top. The
nested list expands in order to the sticky protocol-error latch, TX, RX, and
endpoint files, for six hash-pinned source files per row. The latch is part of
the charged error/drain behavior; reset-before-clean-drain history is not
claimed. The validator rejects any renamed, changed,
or reordered list/source as well as multi-edge research RTL, technology
staging, constraints, netlists, and evidence by path and token.

## Clock and boundary assumptions

`constraints/single_edge_placeholder.sdc` creates exactly one primary clock on
`clk_i`. It creates no generated clock and contains no falling-edge timing
model. All non-clock inputs receive min/max input delay and input transition;
all outputs receive min/max output delay and capacitive load.

The exact SDC bytes and the 6.5 ns clock, uncertainty, min/max I/O delays,
transition, 0.01 pF load, all-left
Metal3 pin placement, and core floorplan are **team screening placeholders**.
The SDC refuses to load unless the environment explicitly supplies
`SE_CONSTRAINT_CLASS=TEAM_PLACEHOLDER_SCREENING_ONLY`. More importantly, the
contract sets `candidate_go_eligible=false`; neither a command-line override
nor an otherwise clean run can promote those values. Organizer or board-level
conditions require a new reviewed contract with external authority.

## Gates

Run the local package check without Cadence or PDK access:

```sh
python3 physical/k2_single_edge_endpoint/flow.py static \
  --output /tmp/k2-single-edge-static.json
```

`PASS_STATIC_PACKAGE` means only that the contract, exact filelists, SDC,
templates, exclusion policy, and ledger definition are internally consistent.
It records whether the integration sources are present but never interprets
source absence as physical evidence.

After the exact RTL tops have landed, `plan` hashes every source and every
package byte and emits the exact Genus/Innovus argv, cwd, environment, and a
hash for each command:

```sh
python3 physical/k2_single_edge_endpoint/flow.py plan --design a2 \
  --attempt-root /absolute/server/attempt-a2 \
  --output /absolute/server/attempt-a2/command-plan.json
```

On the licensed server only, `capture-environment` re-reads the pinned Cadence
entrypoints, runs their version probes, and re-hashes the five live GPDK045
technology files. The environment receipt records an allowlisted environment
map and its digest. Local tests do not invoke this command.

```sh
python3 physical/k2_single_edge_endpoint/flow.py capture-environment \
  --pdk-root /home/aiasic26911/gsclib045_all_v4.7/gsclib045 \
  --genus /tools/cadence/DDI231/GENUS231/bin/genus \
  --innovus /tools/cadence/DDI231/INNOVUS231/bin/innovus \
  --output /absolute/server/attempt-a2/LIVE_ENVIRONMENT.json
```

The plan resolves each committed Tcl template to an absolute path, records the
exact entrypoint/argv/cwd/environment, gives the environment and entire command
separate hashes, and keeps the execution decision false. Capture stdout/stderr
as `genus/tool.log` and `innovus/tool.log`. Do not edit or overwrite an attempt:
receipts are exclusive-create and all evidence is re-hashed at qualification.

The server runner requires a literal per-stage authorization, revalidates the
live environment immediately before launch, creates the stage directory only
once, captures the native log, and writes a sealed execution receipt. Innovus
also requires the bound successful Genus receipt:

```sh
python3 physical/k2_single_edge_endpoint/flow.py execute --design a2 \
  --stage genus --plan /absolute/server/attempt-a2/command-plan.json \
  --environment /absolute/server/attempt-a2/LIVE_ENVIRONMENT.json \
  --authorization I_UNDERSTAND_THIS_LAUNCHES_REAL_GENUS

python3 physical/k2_single_edge_endpoint/flow.py execute --design a2 \
  --stage innovus --plan /absolute/server/attempt-a2/command-plan.json \
  --environment /absolute/server/attempt-a2/LIVE_ENVIRONMENT.json \
  --authorization I_UNDERSTAND_THIS_LAUNCHES_REAL_INNOVUS
```

These commands are documented for the licensed server; the local regression
never calls them.

Execution receipts use schema `k2_single_edge_execution_receipt_v3`. Exit-zero
runs classify their complete artifact manifest as
`BOUND_COMPLETE_EXIT_ZERO_STAGE_MANIFEST`. A nonzero run may leave safely
collected diagnostic files in its exclusive stage directory, but the receipt
uses `UNBOUND_NONZERO_EXIT_FILES_NOT_LEDGER_ELIGIBLE`, carries an empty artifact
manifest, and cannot enter ledger construction or qualification. This makes
the failure files' lack of provenance binding explicit; it does not promote
them to evidence.

## Artifact ledger and decision

After both commands complete, generate the exact-path ledger (this command does
not run or interpret Cadence):

```sh
python3 physical/k2_single_edge_endpoint/flow.py build-ledger --design a2 \
  --attempt-root /absolute/server/attempt-a2 \
  --plan /absolute/server/attempt-a2/command-plan.json \
  --output /absolute/server/attempt-a2/artifact-ledger.json
```

`artifact-ledger.json` has schema `k2_single_edge_artifact_ledger_v2`, the
design/top, contract and command-plan hashes, a self-hash, and exactly one row
per required role and exact relative path in `contract.json`. Every row has
only:

```json
{
  "role": "drc",
  "path": "innovus/reports/drc.rpt",
  "sha256": "<64 lowercase hex>",
  "size_bytes": 123,
  "producer_command_sha256": "<hash from command-plan.json>"
}
```

The ledger is derived from both exact, stage-specific execution-receipt
manifests. Each receipt binds design, top, canonical attempt root, exact stage
command/environment/log, exit zero, all stage artifacts, and (for Innovus) the
exact upstream Genus receipt and mapped inputs. Genus roles may not carry the
Innovus command hash or vice versa. The ledger covers Genus timing/area/intent,
QoR/power/clocks, mapped netlist/SDC/SDF, native tool
logs, post-route setup/hold/area, `check_timing`, route, DRC, antenna, signal
and PG connectivity, post-route netlist/SDF/SPEF/database, and both native
completion markers. Qualification requires positive path counts, nonnegative
WNS, zero TNS/violations, positive routed area, native zero DRC/antenna/
connectivity evidence, no nonzero missing-clock/I/O/load or unconstrained-path
counts, one mapped primary clock, exact tops, and clean version-bound logs.
The Innovus template appends one exact version/top/kind/context marker to each
setup, hold, area, `check_timing`, DRC, antenna, signal-connectivity, and
PG-connectivity report. Qualification validates native commented identity
headers separately, strips comments before interpreting diagnostic claims,
and rejects missing, duplicate, foreign, fatal, incomplete, or contradictory
report context.

The mapped SDC parser accepts exactly eight commands: one named primary clock
with the 6.5 ns period and `{0.0 3.25}` rising/falling waveform, uncertainty,
min/max input and output delays, input transition, and output load. Collections
must enumerate exactly `clk_i`, every non-clock input, and every output as
appropriate. Extra clocks, commands, values, collections, generated clocks,
or timing exceptions are rejected even when placed after a semicolon. Mapped
and routed netlists must expose exactly the contract port order, directions,
and widths, with no extras, plus cell connections touching at least one boundary
input and at least one boundary output. This is structural screening, not
equivalence.

Native diagnostic parsing follows emitted Cadence forms rather than
fixture-only summaries. Innovus timing requires sequential `Path N: MET` rows
and one `Slack Time` per path, checked against machine WNS at the report's
printed precision; foreign path classes, any `VIOLATED`/no-slack text, and
unpaired or malformed slack rows reject. Tool logs and reports also reject
nonzero forms such as `Error=10` and `10 errors`, plus fatal prose even when a
clean footer remains. `check_timing` requires the exact Innovus generator,
design, and command header, the clean `ideal_clock_waveform` inventory, and
the exact `se_primary_clk`/`se_setup_view` ideal-clock row. Missing-constraint,
unconstrained, detail, unknown-warning, and contradictory forms reject. DRC,
antenna, and connectivity bind the native header plus appended context marker
and accept the clean native sentinel without inventing an extra zero-count
line; an explicit total, when present, must be uniquely zero. Genus reports
require their exact generator version and top, with native timing and area
structures. The Innovus driver catches setup and hold closure failures
separately, collects the remaining independently safe post-route reports, and
then exits nonzero without writing `COMMANDS_COMPLETE`.

```sh
python3 physical/k2_single_edge_endpoint/flow.py qualify --design a2 \
  --attempt-root /absolute/server/attempt-a2 \
  --environment /absolute/server/attempt-a2/LIVE_ENVIRONMENT.json \
  --plan /absolute/server/attempt-a2/command-plan.json \
  --ledger /absolute/server/attempt-a2/artifact-ledger.json \
  --output /absolute/server/attempt-a2/qualification.json
```

Missing receipts/artifacts and internally admissible self-sealed bundles both
produce `HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE`. The package deliberately has
no GO branch. Its local self-hashes establish diagnostic byte relationships,
not producer
identity: organizer-owned constraints and a producer-held out-of-band
signature/MAC or equivalent immutable server authority are both absent. The
qualification output therefore says `producer_authenticated=false`, never
calls caller-writable fixture data real server evidence, and independently
retains the placeholder-constraint promotion blocker. Successful parser output
is named `diagnostic_metrics_only`, and the boolean
`diagnostic_artifact_checks_completed` cannot be interpreted as verification,
signoff, provenance, equivalence, or candidate GO.

The live tool pin covers the configured Cadence wrapper bytes and version
output, not the wrapper's complete downstream executable/shared-library
closure. Filesystem reads reject lexical source/artifact symlinks, hardlinks,
ancestor symlinks, and pre-open identity swaps, but this portable Python flow
does not provide a kernel-enforced immutable attempt filesystem. These limits
are reasons for the unconditional unauthenticated HOLD.

After both rows independently produce diagnostic HOLD receipts, an optional
cohort binder requires them to name the exact same environment-snapshot hash:

```sh
python3 physical/k2_single_edge_endpoint/flow.py bind-cohort \
  --a2-qualification /absolute/server/attempt-a2/qualification.json \
  --a3-qualification /absolute/server/attempt-a3/qualification.json \
  --output /absolute/server/k2-single-edge-cohort.json
```

This binds only caller-self-sealed diagnostic bytes. It emits
`freshness_verified=false`, `comparison_ready=false`, and
`candidate_physical_go=false`; a controlled runner, trusted freshness source,
producer-held authentication, and an organizer-controlled captured real-report
corpus remain external blockers. Local parser fixtures do not substitute for
that corpus or alter HOLD.

Run the adversarial local regression with:

```sh
tests/k2_single_edge_endpoint/run_all.sh
```
