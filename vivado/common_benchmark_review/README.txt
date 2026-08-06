Vivado common AER testbench review project

Windows project:
  C:\Users\박준영\AI-semi\vivado\common_benchmark_review\project\aer_common_tb_review.xpr

Primary simulation top:
  aer_clean_tb

Main files to read in Vivado:
  tb/clean/aer_clean_tb.sv
    workload generation, one-entry source model, scoreboard and metrics

  tb/clean/aer_bench_if.sv
    normalized common source/retire interface

  tb/clean/aer_clean_assertions.sv
    input/output stall stability and unknown-control checks

  tb/clean/aer_clean_mock_candidate.sv
    testbench-only round-robin smoke candidate

  tb/clean/aer_legacy_candidate_adapter.sv
    baseline/A23 ready-valid observation adapter

  tb/clean/native/aer_ganghee_native_binding.sv
    Ganghee req[15:0] to valid+addr[3:0] storage-free native binding

  tests/clean_native/aer_ganghee_native_binding_tb.sv
    Ganghee binding protocol self-test

Important:
  These are SystemVerilog .sv files, not Verilog-2001 .v files.
  Vivado 2023.2 can display and compile SystemVerilog simulation sources.
  The common benchmark's qualified simulator is Xcelium 23.09. Treat Vivado/XSim
  as a code-review and learning environment until XSim compatibility is separately
  confirmed. Python trace generation and CSV aggregation run outside Vivado.

