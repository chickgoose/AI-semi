# REDRED single-edge fallback: GPDK045 legality and organizer-evidence audit

Audit date: 2026-08-19

Hardened source commit: `6fc5e167918fa4c54786c9a3abb5f60ecd8b991b`

Audited integrated commit: `a0a4eb38632245db8ff5937ea5b6c6e3f3839246`

Machine matrix: `tests/redred_single_edge_pdk_legality/legality_matrix.json`

## Verdict

**HOLD.** The repository supports designing and testing a single-edge parallel
fallback, but it does not support calling that fallback competition-legal or
release-ready.

The integrated source RTL is now present and its immutable filelist closure
passes the source-only structural checks described below. Three independent
release boundaries remain open:

1. no organizer-authored written rule or immutable transcript is tracked;
2. the exact GPDK045/GSCLIB045/GIOLIB045 payload bytes are not in this checkout;
3. no fallback mapped inventory, P&R receipt, or mapped vectorless-power
   receipt exists here.

The tracked policy selects the single-edge parallel implementation but keeps
its release on `HOLD_INCOMPLETE_MAPPED_PHYSICAL_POWER_AND_SELECTION` and
forbids borrowing P6 physical/power evidence. This audit preserves that
boundary. In particular, a mapped netlist
that happens not to contain `DFFNSRX1`, ODDR, IDDR, a clock gate, or a generated
clock would establish only a structural negative fact. **Absence of a forbidden
primitive is never organizer approval.** Organizer authority is the independent
gate `G01_ORGANIZER_PRIMARY_RULE`.

## Evidence grading used here

“Locally evidenced” is split into levels so that a recorded server observation
is not mistaken for locally available PDK bytes.

| Grade | Meaning |
|---|---|
| `LOCAL_PAYLOAD` | The exact artifact bytes are present and hashable in this checkout. |
| `REPOSITORY_RECORD` | A tracked manifest/report records a path, value, or SHA, but the referenced external bytes are absent. |
| `HISTORICAL_RESULT` | A tracked report records an earlier tool result; unavailable archives prevent current byte-level replay. |
| `TEAM_PROFILE` | The team selected the value for controlled comparison. It is not an organizer rule. |
| `ORGANIZER_PRIMARY` | Organizer-authored immutable bytes or a written reply. None are tracked for the disputed items. |

The raw source behind `docs/AI_SEMI_QNA_REDRED_GOAL_20260819.md` is described by
that document as a user-provided oral transcript. The tracked file is therefore
a careful secondary interpretation, not organizer-primary written approval.

## What is actually evidenced

### Integrated RTL source structure

The source authority is the hardened Git object
`6fc5e167918fa4c54786c9a3abb5f60ecd8b991b`, audited at integrated Git object
`a0a4eb38632245db8ff5937ea5b6c6e3f3839246`. The verifier independently reads
the two release root filelists from both objects, recursively expands the
shared generic filelist, and requires their exact, byte-identical
three-filelist/eight-source path and SHA-256 inventories. The expanded closure
includes `w2_single_edge_error_latch.sv`. A missing, added, reordered,
P6-substituted, escaped, symlink-mode, non-regular, or hash-different committed
object fails closed.

After removing comments and string literals, all seven sequential event
controls in that closure are rising-edge events on the declared `clk`/`clk_i`
clock. The verifier pins the ordered clock-event inventory for every source,
including sources with zero events, so a clock event cannot silently move
between files while preserving the total.
The source closure contains no falling-edge event, derived/gated event
expression, forwarded clock output/assignment, ODDR, IDDR, known technology
cell, vendor primitive, UDP primitive, or `always_latch` construct. This result
is recorded as:

```text
source_structure_status = PASS
mapped_structure_status = HOLD
organizer_approval_status = HOLD
claim_limit = RTL_SOURCE_ONLY_NOT_MAPPED_NOT_ORGANIZER_APPROVAL
```

This PASS is necessary source evidence only. It does not prove what Genus maps,
does not validate a clock report or SDC, does not establish real-library cell
legality, and has no path to organizer approval or release GO by itself.
The earlier baseline
`4ce4836fab1309d3468db8e660d2da9af371f784` is explicitly superseded and is
ineligible to publish this source-structure PASS.

### PDK and library identities

| Item | Repository evidence | Local payload? | Competition meaning |
|---|---|---:|---|
| Standard-cell archive | `gsclib045_all_v4.7.tgz`, SHA `fb15a057...8ad1` in the server audit | No | Server-provided demonstration data was observed historically; this does not select an official corner. |
| I/O archive | `giolib045_v3.3.tgz`, SHA `4bebbc57...fcdb` | No | Its existence was recorded, but no pad list, pad Liberty/LEF, or organizer endpoint rule is tracked. |
| Setup Liberty | `timing/slow_vdd1v0_basicCells.lib`, SHA `dec616b7...ffe10` | No | Repository records process `1.0`, 0.9 V, 125 C and `PVT_0P9V_125C`; team-selected setup/power view only. |
| Hold Liberty | `timing/fast_vdd1v0_basicCells.lib`, SHA `e63762d1...91560` | No | Repository records process `1.0`, 1.1 V, 0 C; the operating-condition name is not evidenced locally. |
| Technology LEF | `lef/gsclib045_tech.lef`, SHA `0310f32f...19f70` | No | Exact identity is recorded, contents unavailable locally. |
| Macro LEF | `lef/gsclib045_macro.lef`, SHA `7bb39c7a...52b2` | No | Exact identity is recorded, contents unavailable locally. |
| Extraction tech | `qrc/qx/gpdk045.tch`, SHA `a089c567...bd5` | No | One shared typical QRC was used for setup and hold; no distinct best/worst RC evidence exists. |

The strict verifier independently walks the checkout for every expected real
artifact basename. The only matching `.lib`, `.lef`, and `.tch` files are the
four explicitly declared files under `tests/k2_w2_genus/fixtures/`; unexpected
or symlinked matches fail. Their live hashes must equal the fixture hashes and
must differ from every pinned real-artifact hash. The verifier also checks that
each pinned absolute server path is absent on this host rather than trusting
the JSON `present_in_checkout` assertion. These files are parser/flow test
doubles, not GPDK045 legality evidence.

The referenced historical archives and result root were also absent at audit
time:

```text
/tmp/ganghee-pnr-raw-golden-20260813.tar.gz
/tmp/ganghee-pnr-golden-20260813.tar.gz
/tmp/endpoint-6p5-final/k2-pnr-b588852-6p5-final2-evidence-bc61c47.tar.gz
/tmp/k2-pnr-b588852-6p5-final2
```

### Cell evidence and its limit

`physical/k2_w2_server_env/contract.json` records live-server contracts for:

| Cell | Recorded role/pins | Evidence boundary |
|---|---|---|
| `TLATNTSCAX2` | ICG: `CK,E,SE,ECK` | Recorded Liberty/LEF observation and historical mapped use; exact bytes absent locally. |
| `MX2X1` | mux: `A,B,S0,Y` | Recorded observation and historical mapped use. |
| `DFFRHQX1` | rising-edge async-clear FF: `RN,CK,D,Q` | Recorded observation and buffered mapped use. This is relevant to, but does not preapprove, a rising-edge fallback. |
| `DFFNSRX1` | falling-edge FF: `Q,QN,CKN,D,SN,RN` | Recorded live observation and later P6/R1 flow contract; it is unnecessary if a fallback is genuinely rising-edge-only. |

These four cells are a P6/R1 technology-stage contract, not the complete legal
GSCLIB045 cell set and not a prescribed mapping for the fallback. A fallback
GO must inventory **every** mapped reference and check its exact function,
timing pins/arcs, supply pins, physical macro, and `CoreSite` compatibility in
both real Liberty views and the real macro LEF. Fixture-only names such as
`DFFX1` cannot satisfy that requirement. `BUFX2` is recorded as a team input
driver model, while a separate core flow had to replace mapped `BUFX2` instances
with `BUFX4` for site normalization; therefore neither cell should be assumed
physically legal without checking the selected fallback flow.

No repository evidence says ODDR or IDDR is forbidden by the organizer. They
were merely absent from the audited historical mapped inventories. Conversely,
their absence from a new fallback does not prove the fallback is approved.

### Clock, I/O, load, and corner values

| Value set | Recorded values | Classification |
|---|---|---|
| Inherited complete-endpoint 6.5 ns profile | ref period 6.5 ns, ref waveform `[0,3.25]`, sample waveform `[1.625,4.875]`, uncertainty 0.25 ns; input/output/reset min/max 0.10/0.50 ns; input transition 0.05 ns; output load 0.01 pF; drive cell `BUFX2` | `TEAM_PROFILE` plus an inherited P6/R1 historical result. Explicitly not final competition rules and not fallback evidence. |
| Earlier 5 ns exploration | period 5.0 ns, uncertainty 0.10 ns, input/output delay 0.25 ns, load 0.01 pF | `TEAM_PROFILE`; controlled synthesis comparison only. |
| Setup corner | slow Liberty, process 1.0, 0.9 V, 125 C, `PVT_0P9V_125C` | `REPOSITORY_RECORD`; exact external payload absent; not organizer-selected. |
| Hold corner | fast Liberty, process 1.0, 1.1 V, 0 C | `REPOSITORY_RECORD`; exact external payload absent; operating-condition name and organizer selection unproven. |
| RC | same `gpdk045.tch` for setup and hold | `REPOSITORY_RECORD`; disclosed typical-only limitation, not multi-corner signoff. |

The audited hardened fallback source declares one shared functional clock
(`clk_i` at the complete tops and link/error latch, `clk` inside the unchanged
schedulers), with
rising-edge state only and no `sample_clk_i` or `link_clk_o` in the release
filelist closure. None of the inherited numeric clock values can be silently
applied to it. A fallback-specific SDC and mapped clock report must still bind
the exact clock and all data/reset paths. The organizer must separately pin or
accept the numeric period, waveform, uncertainty, drive/transition, min/max
I/O/reset delays, and output load.

## Organizer constraints: attributed versus missing

The tracked Q&A interpretation attributes these requirements to the organizer:

- stay within the supplied educational 45 nm PDK;
- use common PDK and I/O conditions, check timing, and report vectorless power;
- assess the whole synthesizable communication system, including link
  bottlenecks; and
- do not rely on primitives outside the provided PDK range.

The same tracked document explicitly leaves these unresolved:

- exact GPDK045 release, threshold-voltage/cell subset, setup/hold/power/RC
  corners, and whether GIOLIB pads are mandatory;
- exact clock period/waveform/uncertainty and reset constraints;
- exact input drive or transition, input/output/reset delays, output load, and
  load-variation scoring;
- written P6/multi-edge/generated/gated-clock permission; and
- single-edge fallback port/width/clock/pad approval and submission format.

Because the primary transcript or written response is not tracked, even the
first list remains an attributed interpretation for release-audit purposes.
It is useful design guidance, but it is not sufficient to flip G01 to GO.

## Machine-checkable HOLD/GO matrix

The authoritative matrix is
`tests/redred_single_edge_pdk_legality/legality_matrix.json`; its verifier
uses duplicate-key rejection and exact keys at every structured boundary. It
rejects path traversal and symlinks, binds repository evidence authority, reads
the audited RTL directly from regular Git blobs, expands actual committed
filelists, scans the entire source closure, and enforces an `ALL` expression:

```text
SINGLE_EDGE_RELEASE_GO = G01 && G02 && G03 && G04 && G05
                       && G06 && G07 && G08 && G09
```

| Gate | Current | GO evidence required |
|---|---|---|
| G01 organizer primary rule | HOLD | Organizer-authored immutable rule/written reply covering topology, PDK/cells, clock, I/O/load, and boundary. |
| G02 real PDK bytes | HOLD | Exact live SHA matches plus retained strict `PROVEN_SERVER_ENV` receipt. |
| G03 official library/corner | HOLD | Organizer-selected setup, hold, RC, and power views/conditions. |
| G04 mapped cell legality | HOLD | Fallback mapped inventory; every cell verified in real slow/fast Liberty and macro LEF. |
| G05 single-edge structure | HOLD (`hardened RTL source PASS`; mapped/clock-report HOLD) | The byte-identical `6fc5e16`/`a0a4eb3` RTL closures are source-clean; mapped netlist and clock reports must independently prove the same declared active edge and absence of undeclared edge/clock primitives. The superseded `4ce4836` baseline is not PASS authority. |
| G06 canonical digital | GO (`PASS_SCOPED_NATIVE_CAMPAIGN`) | The current goal-policy pin verifies the native synthetic/public publication, exact-once/order/accounting, semantic seal, and campaign-scoped A2 recommendation. Its nonrelease claim boundary cannot satisfy mapped PDK, P&R, power, CDC/RDC, final selection, or release gates. |
| G07 official clock/I/O/load | HOLD | Organizer-pinned/accepted numeric clock, uncertainty, drive/transition, delays, reset, and load. |
| G08 post-route | HOLD | Fallback-specific P&R/timing/DRC/antenna/connectivity receipt at the chosen conditions. |
| G09 vectorless power | HOLD | Fallback-specific mapped complete-endpoint vectorless receipt at the official profile. |

Promotion rules are fail-closed:

- The goal-policy pin is an immutable snapshot that references the prior
  published legality package. It is not a recursive assertion that the current
  package bytes equal that prior SHA, and policy validity is not evidence or
  release qualification.
- P6/R1 digital, timing, P&R, or power artifacts cannot satisfy a fallback gate.
- A no-ODDR/no-IDDR/no-negedge result can be attached only to G05/G04; it has
  no evidentiary path to G01.
- Environment preflight GO validates exact bytes and tool/library contracts,
  not organizer approval, mapping correctness, timing closure, or power.
- Overall status is GO if and only if every required gate is GO.

Run the local verifier with:

```bash
bash tests/redred_single_edge_pdk_legality/run_all.sh
```

The runner also executes mutation tests for duplicate/unknown keys, hardened
source/integrated commit authority and stale-baseline substitution, exact
per-source posedge inventory, source/hash substitution, P6 filelist borrowing,
source-PASS promotion, repository/external/fixture identity changes, traversal/symlinks,
undeclared PDK-like checkout files, filelist cycles/options, opposite-edge
state, derived/gated/forwarded clocks, ODDR/IDDR/technology/vendor primitives,
UDP primitives, and latches.

## Precise missing artifacts and live verification

The JSON matrix is also the missing-artifact manifest. It records the exact
expected server path and SHA for both archives and for slow/fast Liberty,
technology/macro LEF, and QRC. A strict live-server environment check is:

```bash
python3 physical/k2_w2_server_env/preflight.py \
  --contract physical/k2_w2_server_env/contract.json \
  --raw-archive /tmp/ganghee-pnr-raw-golden-20260813.tar.gz \
  --buffered-archive /tmp/ganghee-pnr-golden-20260813.tar.gz \
  --pdk-root /home/aiasic26911/gsclib045_all_v4.7/gsclib045 \
  --genus /tools/cadence/DDI231/GENUS231/bin/genus \
  --innovus /tools/cadence/DDI231/INNOVUS231/bin/innovus \
  --xrun /tools/cadence/XCELIUMMAIN2309/tools.lnx86/inca/bin/64bit/xrun \
  --output /tmp/redred-single-edge-server-environment.json

python3 physical/k2_w2_server_env/require_go_receipt.py \
  --contract physical/k2_w2_server_env/contract.json \
  --receipt /tmp/redred-single-edge-server-environment.json
```

Before that command can pass, the two golden archives must be restored with
their pinned hashes. It validates the standard-cell environment recorded by
the existing contract; it does **not** validate GIOLIB contents. GIOLIB still
requires a separate retained inventory containing its archive SHA, selected
pad Liberty/LEF/model hashes, pad names/pins/supplies/sites, and the organizer
rule that says whether those pads are required or allowed.

Before release, retain at minimum these additional immutable artifacts:

1. organizer rule/reply bytes and SHA;
2. fallback interface/clock/width manifest and SDC SHA, bound to the pinned
   RTL/filelist inventory already recorded here;
3. the already-published native canonical digital receipt and per-event results
   must remain hash-pinned and unchanged;
4. mapped netlist, mapping log, complete cell inventory, and real Liberty/LEF
   cross-check receipt;
5. fallback-specific Genus/Innovus reports and P&R qualification receipt; and
6. fallback-specific mapped vectorless report/receipt at the selected corner,
   clock, I/O, and load values.

Until those artifacts satisfy all nine gates, the only defensible conclusion
is: **single-edge fallback development may proceed; competition legality and
release remain HOLD.**
