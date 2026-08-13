# W2 candidate-neutral Innovus flow

This package is the common Innovus implementation boundary for every physical
candidate. It does not contain candidate RTL, candidate-specific floorplanning,
or a server result.

`run_k2_physical_innovus.sh` requires immutable netlist, SDC, IO assignment,
technology/cell LEF, the server's `slow_vdd1v0_basicCells.lib` for setup, and
`fast_vdd1v0_basicCells.lib` for hold. GPDK045 provides only one characterized
`gpdk045.tch`; its exact same path is therefore mandatory for both QRC inputs.
The two Liberty-based delay corners share one `w2_rc_shared_typical` RC corner.
An arbitrary second QRC path is rejected rather than treated as a fabricated
hold characterization. The same site, utilization, aspect ratio, margins, PG
nets/pins, ring layers and dimensions must be supplied for every candidate.
Candidate-specific values are not defaults: every physical policy input is
mandatory.

The flow creates separate slow/max/setup and fast/min/hold MMMC views over that
shared typical RC model, enables OCV with CPPR, names the standard-cell site
explicitly, rejects missing or mismatched rows, constructs and checks the PG
network, performs placement, CTS, detailed routing and extraction, and saves
both an Innovus database and post-route netlist. It reports setup, hold, reset
recovery, reset removal, placement, unconstrained paths, signal/PG
connectivity, DRC, antenna and route status. Before Innovus starts, the launcher
records the Liberty roles and SHA-256 identities plus the one shared QRC
SHA-256 in `status/TECHNOLOGY_CONTRACT`; clean qualification requires this
receipt and its explicit `shared_typical_gpdk045` accounting.

Innovus writes only `status/COMMANDS_COMPLETE`. That marker is not physical
qualification. `verify_k2_physical_innovus.py` independently rejects negative
timing slack, missing timing paths, errors/interruption, nonzero physical
counts, incomplete detailed route, missing database/netlist, failure markers,
symlinks, and pre-existing clean markers. Only then does it exclusively create
`status/FLOW_CLEAN`. Unrecognized server-report syntax fails closed and must be
added from captured report bytes rather than guessed.

## Authoritative server-format binding

The buffered format fixtures are bound to Kanghee's server archive
`ganghee-pnr-golden-20260813.tar.gz`, SHA-256
`1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.
`tests/physical_w2_innovus/ganghee_golden_pin.json` additionally pins every
archive member consumed by the tests. The default archive path is
`/tmp/ganghee-pnr-golden-20260813.tar.gz`; set
`W2_GANGHEE_GOLDEN_ARCHIVE` to use an identical archive elsewhere. A missing
or mismatched archive or member is a test failure.

The raw source is independently bound by
`tests/physical_w2_innovus/ganghee_raw_golden_pin.json` to
`ganghee-pnr-raw-golden-20260813.tar.gz`, SHA-256
`7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3`.
Its default path is `/tmp/ganghee-pnr-raw-golden-20260813.tar.gz`, overridable
only with `W2_GANGHEE_RAW_GOLDEN_ARCHIVE`. Raw and buffered kind, basename and
digest are asserted separately.

Those captured bytes anchor Innovus `v23.14-s088_1`, the proven `floorPlan -r`,
PG/ring/sroute, placement/CTS/route/extraction command spellings, Path/Slack
timing records, the timing-check warning table, and the exact clean DRC and
antenna messages. The W2 floorplan verifies that the resulting rows use the
mandatory site. Database save uses `saveDesign -mmmc2`, as prescribed by the
captured `IMPIMEX-7043` failure for the original MMMC1 `write_db` attempt.

The archive is an authoritative format/calibration source, not a clean W2
receipt. Its log contains `IMPCCOPT-2215` and `IMPIMEX-7043`; setup and hold use
the same slow view; and it has no recovery report, connectivity report, or
post-route netlist. Tests require the hardened verifier to reject those
conditions and separately prove parsing of real positive setup/hold, negative
setup/removal, DRC, antenna, and check-timing bytes.

## Raw sweep reconstruction

The raw archive contains two independently resynthesized period sweeps:

- Cluster2: 0.7, 0.8, 0.9, 1.0 and 1.3 ns. Setup WNS is respectively -0.178,
  -0.088, -0.029, 0.042 and 0.080 ns; hold WNS is 0.163, 0.160, 0.162, 0.160
  and 0.166 ns.
- Fovea: 1.2, 1.3, 1.4, 1.6 and 2.0 ns. Setup WNS is respectively 0.000,
  -0.024, 0.036, -0.003 and -0.007 ns; hold WNS is 0.058, 0.120, 0.120,
  0.125 and 0.125 ns.

Every raw `run_<period>.tcl` uses the same 45 nm floorplan/PG/place/CTS/route/
extraction recipe and selects period-specific netlist, SDC, MMMC and output
names. It emits setup with `report_timing -late`, hold with
`report_timing -early`, then check-timing, DRC and antenna reports. The actual
MMMC files reuse one slow/typical view for setup and hold, so they are
diagnostic inputs rather than the hardened two-corner W2 qualification.

All ten raw logs contain both `IMPCCOPT-2215` (disconnected clock traversal
graph) and `IMPIMEX-7043` (`write_db` cannot save the MMMC1 database). Therefore
none of the raw sweep points is a physical PASS, including points with
nonnegative setup and hold. Qualification requires a pinned-version log with
no error marker, all-zero error summaries and the normal Innovus ending, plus
content-validated nonnegative setup/hold, zero DRC, zero antenna and a complete
check-timing report. Merely finding nonempty files never qualifies a run.

This local commit does not run Innovus. No new PPA number, timing bracket, or
physical winner is claimed. A server run remains required to validate the
candidate-neutral hardened commands and produce all qualification artifacts.
