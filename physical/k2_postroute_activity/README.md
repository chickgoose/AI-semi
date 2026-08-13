# Post-route common activity producer

This producer runs the same pinned `mixed_phase_always_ready_identity` common
trace on the exact Innovus post-route netlist and SDF for each of the five
physical candidate IDs: `fovea`, `cluster2`, `fovea_a7`, `a2_p6`, and `a3_p6`.
It does not run Genus or Innovus and does not modify candidate or team RTL.

The raw SDF gate simulation runs directly at one supported physical period
(5.0, 5.7, or 6.5 ns). The producer first verifies the frozen common TB hash,
then deterministically materializes a copy in which only the timescale and its
single reference-clock delay are changed. The endpoint sample-clock phase is
materialized from the same period registry. The workload, scoreboard, reset,
measurement, and drain logic remain byte-for-byte unchanged. Running directly
at the target period is required because arbitrary post-route cell-delay
timestamps cannot safely be rescaled after simulation. The exact measurement
window is only rebased to zero; SDF gate delays are never rounded or warped.
The result is rejected on a trace hash mismatch, top/SDF
mismatch, Xcelium or SDF error/warning, common-TB error or conservation failure,
retirement-ledger mismatch, unexpected hierarchy, nonzero unknown-state
residence, inexact timestamp transform, or existing output path.

Example (paths are illustrative and must name one completed physical row):

```sh
python3 -B physical/k2_postroute_activity/run_postroute_activity.py \
  --candidate a3_p6 \
  --netlist /absolute/run/netlist/w2_a3_p6_physical_staging_top.postroute.v \
  --sdf /absolute/run/netlist/w2_a3_p6_physical_staging_top.postroute.sdf \
  --model /absolute/pdk/verilog/slow_basicCells.v \
  --trace /absolute/full50/mixed_phase_always_ready_identity.events.jsonl \
  --run-manifest /absolute/full50/mixed_phase_always_ready_identity.manifest.json \
  --period-ns 6.5 --xrun /absolute/path/to/xrun \
  --output /absolute/new/activity-a3-p6
```

`activity-receipt.json` binds the candidate/top, exact VCD scope and window,
common summary and per-event evidence, independent retirement ledger, tool,
vendor models, post-route netlist/SDF, producer sources, every generated
activity artifact, and an `innovus_power_input` block ready to copy into a
physical campaign descriptor. The VCD scope for all five rows is exactly
`aer_clean_tb.candidate.dut`; the Innovus window starts at 0 ns and ends at the
receipt's target-period `window.end_ns`.

The local tests are non-EDA:

```sh
tests/k2_postroute_activity/run_all.sh
```
