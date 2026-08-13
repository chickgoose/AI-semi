# W2 candidate-neutral Innovus flow

This package is the common Innovus implementation boundary for every physical
candidate. It does not contain candidate RTL, candidate-specific floorplanning,
or a server result.

`run_k2_physical_innovus.sh` requires immutable netlist, SDC, IO assignment,
technology/cell LEF, distinct setup/hold Liberty files, and distinct setup/hold
QRC files. The same site, utilization, aspect ratio, margins, PG nets/pins,
ring layers and dimensions must be supplied for every candidate. Candidate
specific values are not defaults: every physical policy input is mandatory.

The flow creates separate max/setup and min/hold MMMC views, enables OCV with
CPPR, names the standard-cell site explicitly, rejects missing or mismatched
rows, constructs and checks the PG network, performs placement, CTS, detailed
routing and extraction, and saves both an Innovus database and post-route
netlist. It reports setup, hold, reset recovery, reset removal, placement,
unconstrained paths, signal/PG connectivity, DRC, antenna and route status.

Innovus writes only `status/COMMANDS_COMPLETE`. That marker is not physical
qualification. `verify_k2_physical_innovus.py` independently rejects negative
timing slack, missing timing paths, errors/interruption, nonzero physical
counts, incomplete detailed route, missing database/netlist, failure markers,
symlinks, and pre-existing clean markers. Only then does it exclusively create
`status/FLOW_CLEAN`. Unrecognized server-report syntax fails closed and must be
added from captured report bytes rather than guessed.

## Authoritative server-format binding

The format fixtures are bound to Kanghee's server archive
`ganghee-pnr-golden-20260813.tar.gz`, SHA-256
`1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.
`tests/physical_w2_innovus/ganghee_golden_pin.json` additionally pins every
archive member consumed by the tests. The default archive path is
`/tmp/ganghee-pnr-golden-20260813.tar.gz`; set
`W2_GANGHEE_GOLDEN_ARCHIVE` to use an identical archive elsewhere. A missing
or mismatched archive or member is a test failure.

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

This local commit does not run Innovus. No new PPA number, timing bracket, or
physical winner is claimed. A server run remains required to validate the
candidate-neutral hardened commands and produce all qualification artifacts.
