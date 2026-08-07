# A2 RTL skeleton

Implementation units:

- `a2_adaptive_dual_path_core.sv`: synthesizable arbitration, sparse bypass,
  parameterized banked reservoir, mode controller, and normalized native pins;
- `a2_adaptive_dual_path_binding.sv`: storage-free connection to
  `aer_bench_if`;
- `a2_adaptive_dual_path.f`: candidate-only RTL file list.
- `a2_benchmark.f`: common normalized interface/assertions/TB plus the A2
  private replacement cell; it intentionally excludes the historical common
  compatibility adapter.

`scripts/run_a2_adaptive_dual_path.sh` is the only common-benchmark entry point.
It consumes the frozen TB and prepared traces without editing either one.
Its `A2_RESERVOIR_DEPTH`, `A2_BANK_COUNT`, `A2_ENTER_LEVEL`, `A2_EXIT_LEVEL`,
and `A2_QUIET_CYCLES` compile overrides default to the phase-1 point. Phase 2's
selected point is B4/D16/E4/X0/Q1. `a2_phase2_selected_core.sv` fixes that
selection for head-controlled synthesis without changing the common driver.
Both Xcelium and local Verilator use the canonical common assertion source in
`a2_benchmark.f`. Icarus is used only for the native directed unit test because
it cannot parse the common interface modport/SVA form.

The binding is a combinational port map only. The candidate profile deliberately
marks output backpressure unsupported: queued heads are stall-stable, but the
initial zero-queue bypass is qualified only under the mandatory always-ready
contract.

Phase-3 local physical proxies use `a2_phase3_physical_wrapper.sv` to give A2,
flat RR, and equal-capacity always-buffered cores identical elastic ingress and
registered retire boundaries. `tests/a2/run_phase3_physical_proxy.sh` performs
candidate-only Yosys 4-LUT mapping, functional/VCD regression, and keep/reject
analysis; all generated artifacts default to `/tmp`.
