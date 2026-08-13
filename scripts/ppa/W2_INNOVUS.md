# W2 tech-staged Innovus flow

This directory defines the candidate-neutral physical implementation boundary
for exactly three final compositions, in fixed order:

1. `fovea_a7` / `w2_fovea_r1_physical_staging_top`
2. `a2_p6` / `w2_a2_p6_physical_staging_top`
3. `a3_p6` / `w2_a3_p6_physical_staging_top`

The tracked registry is intentionally
`blocked_missing_committed_techmap_manifest`. No Innovus process can start
until one committed `w2-physical-staging-v2` / `GO_FOR_SERVER_STAGING`
manifest, its Git blob
SHA, and committed R1/P6 authority manifests replace the null registry pins.
Owner-generic `k2_w2_*`, native/debug A2/A3, and standalone link tops are
always forbidden.

## Exact boundary and producer gates

Every final top has only these common ports:

- inputs: `ref_clk_i`, `sample_clk_i`, `rst_n`,
  `source_pending_i[15:0]`;
- outputs: `source_accept_o[15:0]`, `retire_valid_o[1:0]`,
  `retire_addr0_o[3:0]`, `retire_addr1_o[3:0]`, `drain_idle_o`, and
  `protocol_error_o`;
- link outputs: `link_clk_o` and `link_data_o[1:0]` for R1, or
  `link_data_o[4:0]` for P6.

`load_i`, `pending_i`, `source_ready_o`, `protocol_fault_o`, and extra debug
ports are rejected even if all affected artifacts are consistently rehashed.

The endpoint link hierarchy is accounted separately from the flattened whole
top. Preserved endpoint leaves are R1 `1 TLATNTSCAX2 / 2 MX2X1 / 2 DFFRHQX1 /
5 DFFNSRX1` and P6 `1 / 5 / 5 / 12`. Cell-specific stable prefixes are
`w2_ep_icg_`, `w2_ep_mux_`, `w2_ep_pos_`, and `w2_ep_neg_`. A bound pre-map
connectivity map must match the mapped names and pins. Scheduler, observer,
buffer, and automatically inserted posedge FF/MUX/ICG cells remain visible in
the independently recorded whole-top inventory and may increase its counts.
DFFNSRX1 is globally exact only when the bound provenance explicitly proves no
other negedge state. Endpoint DFFNSRX1 must use `CKN=link_clk_o`, `RN=rst_n`,
and `SN=1'b1`; SDFF/scan cells are forbidden.

The only accepted producer kind and receipt schema are both literally
`k2_w2_genus_exact_three_endpoint_receipt_v3`. The authenticated screening receipt
plus its Innovus handoff, endpoint connectivity map, and mapped-functional
receipt. The mapped gate must compare staged RTL against the mapped netlist by
vendor-model simulation (SDF when available) or formal LEC. It requires exact
accept/retire/order/conservation/error/reset/drain checks over held-pending R1
and ordered/back-to-back P6 scenarios. Model, log, netlist, and optional SDF
hashes are receipt-bound. Genus remains a mapped timing screen: vectorless
power and physical PPA stay on HOLD. Slow Liberty is the synthesis/timing
input; fast Liberty, macro LEF, and the shared QRC are authenticated downstream
handoff provenance.

## Technology and timing contract

Innovus is pinned to `/tools/cadence/DDI231/bin/innovus`, SHA-256
`41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa`,
version `23.14-s088_1`. Setup uses the slow 0.9 V/125 C Liberty and hold uses
the fast 1.1 V/0 C Liberty. GPDK045 has one characterized
`gpdk045.tch`, so setup and hold deliberately share that exact typical RC
file; an arbitrary second QRC is rejected. Exact Liberty, technology LEF,
macro LEF, QRC, and tool hashes are checked against a
`PROVEN_ENVIRONMENT` receipt and rechecked from the execution descriptor.

The strict R1/P6 SDC templates keep ref/sample/generated clocks phase-related,
constrain both DDR edges, fixed IO drive/load, gating setup/hold, pulse width,
and reset recovery/removal. No reset false path is allowed. All three runs use
one period and the same site, utilization, aspect ratio, margin, PG/ring, IO,
and activity-window policy. Power requires scoped SAIF/VCD activity; vectorless
reports cannot qualify.

## Launch and qualification

Only the plan CLI is public:

```sh
python3 -B scripts/ppa/run_k2_physical_innovus_plan.py \
  --plan /absolute/path/to/innovus-plan.json --validate-only

python3 -B scripts/ppa/run_k2_physical_innovus_plan.py \
  --plan /absolute/path/to/innovus-plan.json --execute
```

Exactly one of `--validate-only` and `--execute` is accepted. The internal
shell cannot be enabled by an environment sentinel: it requires the temporary,
read-only plan-owned execution descriptor and SHA, then rechecks every
netlist/SDC/Genus/handoff/map/functional/environment/activity hash plus the
live server tool and technology files. Each run retains that descriptor and
SHA in its result directory.

The flow performs floorplan/site-row validation, OCV/CPPR, PG connection and
ring routing, placement, CTS, detailed routing, extraction, setup/hold and
recovery/removal, clock-gating setup/hold, pulse-width, and scoped DDR
half-cycle timing, check_timing, checkDesign `-all`, placement,
signal/PG connectivity, DRC, antenna, area, annotated power, database save,
post-route `saveNetlist`, SDF, and SPEF. Tcl writes only `COMMANDS_COMPLETE`.
The independent verifier creates `FLOW_CLEAN` exclusively after clean tool
termination, nonnegative WNS, zero TNS and violations, nonzero timing path
counts, zero no_drive/no_load/unconstrained/checkDesign/placement/connectivity/
DRC/antenna counts, completed route, and retained output checks.

The raw and buffered Kanghee archives remain report-format authorities only.
The raw SHA is
`7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3`;
the buffered SHA is
`1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.
Raw logs contain known Innovus errors and actual check_timing `no_drive=18`,
so they are negative calibration evidence, never PASS/PPA evidence.
No clean native checkDesign fixture exists in those archives. The first server
attempt must therefore retain the native bytes for calibration and remains
fail-closed if the all-class grammar is not recognized; this commit does not
claim a calibrated server PASS.

Local tests (fake/synthetic execution only; no Innovus launch):

```sh
python3 -m unittest \
  tests/physical_w2_innovus/test_w2_innovus.py \
  tests/physical_w2_innovus/test_w2_innovus_plan.py -v
```

The CLI compatibility test reads exact Genus provider commit
`63e98ccf189f992c443a05ba28f63b610bbc3f9f` and confirms the supported
`--hold-library`, `--cell-lef`, and `--shared-qrc` options and absence of the
unsupported `--activity-receipt`. No server launch or physical result is
claimed here.
