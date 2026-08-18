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

These are the exact names and bytes from RTL authority commit
`4ce4836fab1309d3468db8e660d2da9af371f784`. Each three-entry candidate list
contains its native scheduler, the exact nested
`rtl/technology/single_edge/filelists/generic.f`, and its complete top. The
nested list expands to the committed TX, RX, and endpoint files, for five
hash-pinned source files per row. The validator rejects any renamed, changed,
or reordered list/source as well as multi-edge research RTL, technology
staging, constraints, netlists, and evidence by path and token.

## Clock and boundary assumptions

`constraints/single_edge_placeholder.sdc` creates exactly one primary clock on
`clk_i`. It creates no generated clock and contains no falling-edge timing
model. All non-clock inputs receive min/max input delay and input transition;
all outputs receive min/max output delay and capacitive load.

The 6.5 ns clock, uncertainty, I/O delays, transition, 0.01 pF load, all-left
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
  --output /absolute/server/attempt-a2/real-environment.json
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
  --environment /absolute/server/attempt-a2/real-environment.json \
  --authorization I_UNDERSTAND_THIS_LAUNCHES_REAL_GENUS

python3 physical/k2_single_edge_endpoint/flow.py execute --design a2 \
  --stage innovus --plan /absolute/server/attempt-a2/command-plan.json \
  --environment /absolute/server/attempt-a2/real-environment.json \
  --authorization I_UNDERSTAND_THIS_LAUNCHES_REAL_INNOVUS
```

These commands are documented for the licensed server; the local regression
never calls them.

## Artifact ledger and decision

After both commands complete, generate the exact-path ledger (this command does
not run or interpret Cadence):

```sh
python3 physical/k2_single_edge_endpoint/flow.py build-ledger --design a2 \
  --attempt-root /absolute/server/attempt-a2 \
  --plan /absolute/server/attempt-a2/command-plan.json \
  --output /absolute/server/attempt-a2/artifact-ledger.json
```

`artifact-ledger.json` has schema `k2_single_edge_artifact_ledger_v1`, the
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

The ledger covers Genus timing/area/intent, mapped netlist/SDC/SDF, native tool
logs, post-route setup/hold/area, `check_timing`, route, DRC, antenna, signal
and PG connectivity, post-route netlist/SDF/SPEF/database, and both native
completion markers. Qualification requires positive path counts, nonnegative
WNS, zero TNS/violations, positive routed area, native zero DRC/antenna/
connectivity evidence, no nonzero missing-clock/I/O/load or unconstrained-path
counts, one mapped primary clock, exact tops, and clean version-bound logs.

```sh
python3 physical/k2_single_edge_endpoint/flow.py qualify --design a2 \
  --attempt-root /absolute/server/attempt-a2 \
  --environment /absolute/server/attempt-a2/real-environment.json \
  --plan /absolute/server/attempt-a2/command-plan.json \
  --ledger /absolute/server/attempt-a2/artifact-ledger.json \
  --output /absolute/server/attempt-a2/qualification.json
```

Missing real receipts/artifacts produce `HOLD_MISSING_REAL_ARTIFACTS`. A fully
verified real attempt still produces
`HOLD_PLACEHOLDER_CONSTRAINT_AUTHORITY`. Therefore candidate physical GO is
impossible under this contract, including when the static preflight passes.

Run the adversarial local regression with:

```sh
tests/k2_single_edge_endpoint/run_all.sh
```
