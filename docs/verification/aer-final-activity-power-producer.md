# Final three-candidate activity-power producer

The final comparison roster is exactly `fovea_a7`, `a2_p6`, and `a3_p6`.
`benchmarks/physical_ppa/final_endpoint_contract.json` is the single committed
top-interface authority. Its SHA-256 is
`79d44a39f19ce29ac7437807f94965d70b239030cde2605e46384e212cbf8c43`.
The techmap manifest, Genus registry and SDC, Innovus registry, campaign, and
qualifier are all pinned consumers of that exact contract. Every consumer has
`launch_authorized=false`; this commit launches no EDA or simulation job.

The literal final interface is:

- Inputs: `ref_clk_i`, `sample_clk_i`, `rst_n`,
  `source_pending_i[15:0]`.
- Outputs: `source_accept_o[15:0]`, `link_clk_o`,
  `link_data_o[W-1:0]`, `retire_valid_o[1:0]`,
  `retire_addr0_o[3:0]`, `retire_addr1_o[3:0]`, `drain_idle_o`, and
  `protocol_error_o`.
- `W=2` for the R1 Fovea endpoint and `W=5` for both P6 endpoints.

`load_i`, `pending_i`, `source_ready_o`, and `protocol_fault_o` are forbidden
aliases. Debug remains internal. Both mapped and post-route netlists must expose
the exact candidate-specific signature before any power evidence is considered.

## Evidence and derived comparison

`produce_final_activity_power.py` does not generate or modify evidence. It
stable-reads caller-supplied, digest-addressed artifacts and fails closed. A
complete candidate row must bind its exact source closure, mapped and post-route
netlists, SDF, SPEF, simulator/SDF log, candidate-specific VCD or SAIF, resolved
scope manifest, annotation-coverage report, activity-import script and log,
native post-route power report, retired-event CSV, conservation result,
selected-routed-power binding, run manifest, exact PDK files, and exact
simulator/Genus/Innovus/power-engine executables and versions.

All candidates use the same common workload and trace hashes, test and seed,
clock period, exact `[start_cycle,end_cycle_exclusive)` measurement window,
scope root `dut`, and eligibility policy. Internal object names may differ by
implementation, but every eligible candidate object bit must be resolved and
annotated: coverage is required to be exactly 100%, with zero unresolved
objects. Each VCD/SAIF is rebased to zero and must exactly span the measurement
window. Retired-event counts and retire cycles are candidate-derived; they are
not forced equal across architectures. Each count is instead bound to its
candidate event ledger and must satisfy generated = source-overrun + accepted,
accepted = delivered = retired-event count, with no duplicate, corrupt,
phantom, late-after-drain, or error events.

The producer parses one native `PostRoute`, `1mW` power block with a non-`N.A.`
activity file. It rejects vectorless/default-0.2 reports. It derives:

- `dynamic_power_mw = internal_power_mw + switching_power_mw`
- `total_power_mw = dynamic_power_mw + leakage_power_mw`
- `energy_pj_per_retired_event = total_power_mw * clock_period_ns *
  measurement_cycles / retired_event_count`

No area, Fmax, vectorless, functional-only, or synthetic server metric is
included in the comparison artifact. Raw and buffered Kanghee `report_power`
archives remain separate vectorless diagnostics. The yZr1 receipt remains a
separate functional-loss diagnostic and its SHA is prohibited as PPA evidence.

## Current status

There is no real final-candidate post-route netlist/SDF/VCD-or-SAIF/coverage/
power evidence in the repository. Running the producer without an evidence
manifest deterministically returns `HOLD_NO_REAL_SERVER_ARTIFACTS`, an empty
row set, and `candidate_go=false`. Test fixtures exercise parser mechanics only
and return `TEST_ONLY_COMPLETE`; they are not server or RTL evidence. A complete
future measured receipt may reach `READY_FOR_W2_EVALUATION` only after an
immutable server/tool/PDK execution authority is committed. The current code
has no CLI override and returns `HOLD_UNAUTHENTICATED_SERVER_EVIDENCE` even for
complete measured-looking bytes. This producer never emits candidate GO.
The separate `qualify_final_activity_power.py` stable-reads the closed producer
receipt and its exact evidence manifest, reruns the producer, requires a
byte-identical canonical receipt, revalidates the schema and exact
three-candidate roster, binds the receipt SHA, and only declares readiness for
a later external W2 evaluator. It also never emits candidate GO.

```sh
python3 benchmarks/physical_ppa/produce_final_activity_power.py
python3 benchmarks/physical_ppa/qualify_final_activity_power.py \
  RECEIPT.json --evidence EVIDENCE.json
python3 -m unittest -v \
  benchmarks.physical_ppa.tests.test_produce_final_activity_power
```
