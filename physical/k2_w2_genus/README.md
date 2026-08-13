# K2 W2 final tech-staged Genus flow

The final server execution registry is `designs.json`. It accepts exactly three
complete compositions, in this order:

1. technology-staged Fovea + A7/R1
2. technology-staged A2 + P6
3. technology-staged A3 + P6

The registry is `ready` and byte-binds the canonical
`k2_w2_tech_staged_compositions_v1` manifest published at commit
`7f149e043a740c032e2cd22b3ed1d6876b6670ce`. The published manifest has status
`READY_FOR_GENUS_AND_INNOVUS`, names source commit
`07f2413f07357fa1ef34c48fc74c32d238873c30`, and has SHA-256
`923c898e883f535547aa6eee309ecc7270e9c431e872667561c1902afc55279b`.
The runner separately verifies the publication blob and every staged file
against the source commit. An owner-generic or native-debug top is never used
as a fallback.

## Required final boundary

All three staged tops must expose exactly the same inputs:

- `ref_clk_i`, `sample_clk_i`, `rst_n`
- `source_pending_i[15:0]`

They must also expose exactly the same non-link outputs:

- `source_accept_o[15:0]`
- `retire_valid_o[1:0]`
- `retire_addr0_o[3:0]`, `retire_addr1_o[3:0]`
- `drain_idle_o`, `protocol_error_o`

All three also expose `link_clk_o`; R1 has `link_data_o[1:0]`, while P6 has
`link_data_o[4:0]`. These are the only inherent width differences. The aliases
`load_i`, `pending_i`, `source_ready_o`, `protocol_fault_o`, `burst_*`, and
`p6_*` are forbidden at the final top.
No scheduler/debug output, padding, normalized-away link pin, or extra port is
accepted. The runner parses the actual staged top's ANSI port declaration and
checks the exact name/direction/width set rather than trusting manifest claims.

Execution requires a HEAD that contains both the source and publication
commits. The runner verifies the manifest, each gsclib045 filelist, every HDL
source and included technology header against HEAD and the exact source commit.
It consumes the manifest's literal `common_ports`, `designs`, endpoint leaf
contracts, technology authorities, source hashes, test policy, and consumer
contract. Generic wrapper source paths and all named generic, component and
native-debug tops are forbidden.

The shared manifest also pins the complete endpoint technology inventory. R1
requires exactly 1 `TLATNTSCAX2`, 2 `MX2X1`, 2 `DFFRHQX1`, and 5
`DFFNSRX1`; P6 requires exactly 1, 5, 5, and 12 respectively. The negative-edge
cells are the four/ten address-or-closing-state bits plus the commit toggle,
not merely the serialized data width.

## Diagnostic registries

These remain available only for diagnostics and are explicitly ineligible for
final execution or ranking:

- `diagnostic_designs.json`: raw Fovea/Cluster2 and owner-generic wrappers;
- `component_diagnostics.json`: native A2/A3, standalone P6 and native-debug
  A2/P6 and A3/P6 inventory;
- `../k2_w2_tops/designs.json`: owner-generic composition wrappers.

Raw and buffered golden archives remain tool/report/source authorities, while
the yZr1 functional archive remains non-official loss-only evidence and is
forbidden as PPA evidence.

## Launch behavior

`run_goal_cohort.py` creates an exclusive attempt root, records the exact three
commands, and publishes a cohort result
only after all three mapped Genus receipts, endpoint connectivity maps, and
mapped staged-vs-netlist functional gates pass. Any manifest, commit, source,
tool, or evidence mismatch exits nonzero rather than rendering or running
commands for substitute tops.

Execution additionally requires a byte-bound `PROVEN_SERVER_ENV` receipt. The
Genus mapping run consumes the slow setup Liberty only. Fast hold Liberty,
macro LEF, and the shared typical QRC are verified environment and downstream
Innovus provenance inputs; they are not relabeled as Genus MMMC consumption.
Each passing screening receipt hashes the mapped netlist, mapped SDF, mapped
SDC, endpoint leaf hierarchy/pin map, SDF-annotated functional transcript,
vendor functional models, and every timing/area report. The functional hook
must use the Xcelium executable authenticated by `PROVEN_SERVER_ENV`; a hash-
only smoke result cannot pass. Common workloads are never synthesized into
this boundary; scoped workload activity may be attached later as a separately
bound SAIF artifact.

The existing omitted-selector behavior remains the 5.0 ns cohort; 5.7 ns and
6.5 ns launches must explicitly pass `--timing-cohort
three_endpoint_5p7ns` or `--timing-cohort three_endpoint_6p5ns`.
`--timing-cohort three_endpoint_5p0ns` is also accepted explicitly.
`timing_cohorts.json` keeps all profiles side by side and pins each relaxed
profile's
reference/sample period, reference
waveform `[0.0, 2.85]`, phase-shifted sample waveform `[1.425, 4.275]`, reset
release waveform `[2.85, 4.275]`, and every min/max I/O/reset delay and related
timing environment value. The selected profile and manifest hashes are embedded
in the materialized input SDC, launch plan, attempt manifest, Innovus handoff,
per-endpoint receipt, and three-endpoint publication. A missing, unknown, or
mutated selection fails before any tool launch.

Run local contract, provenance, archive and mutation tests with:

```sh
tests/k2_w2_genus/run_all.sh
```
