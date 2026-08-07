# A2 RTL skeleton

Planned implementation units:

- `a2_adaptive_dual_path_core.sv`: synthesizable arbitration, sparse bypass,
  two-bank reservoir, mode controller, and normalized native pins;
- `a2_adaptive_dual_path_binding.sv`: storage-free connection to
  `aer_bench_if`;
- `a2_adaptive_dual_path.f`: candidate-only RTL file list.

The binding is deliberately absent from the initial skeleton commit so the
incomplete core cannot be selected by the common benchmark accidentally.
Implementation and directed tests follow in the functional commit.
