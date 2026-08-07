# A2 RTL skeleton

Implementation units:

- `a2_adaptive_dual_path_core.sv`: synthesizable arbitration, sparse bypass,
  two-bank reservoir, mode controller, and normalized native pins;
- `a2_adaptive_dual_path_binding.sv`: storage-free connection to
  `aer_bench_if`;
- `a2_adaptive_dual_path.f`: candidate-only RTL file list.
- `a2_benchmark.f`: common normalized interface/assertions/TB plus the A2
  private replacement cell; it intentionally excludes the historical common
  compatibility adapter.

`scripts/run_a2_adaptive_dual_path.sh` is the only common-benchmark entry point.
It consumes the frozen TB and prepared traces without editing either one.
Both Xcelium and local Verilator use the canonical common assertion source in
`a2_benchmark.f`. Icarus is used only for the native directed unit test because
it cannot parse the common interface modport/SVA form.

The binding is a combinational port map only. The candidate profile deliberately
marks output backpressure unsupported: queued heads are stall-stable, but the
initial zero-queue bypass is qualified only under the mandatory always-ready
contract.
