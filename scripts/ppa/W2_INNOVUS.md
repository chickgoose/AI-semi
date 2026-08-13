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

This local commit has static and synthetic-report tests only. No Innovus run,
PPA number, timing bracket, or physical winner is claimed.
