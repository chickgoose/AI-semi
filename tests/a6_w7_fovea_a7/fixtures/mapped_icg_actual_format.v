module a7_weighted_fovea_ddr;
  wire sample_clk_i;
  wire gate_enable;
  wire burst_clk_o;
  TLATNCAX2 endpoint_tx_clock_boundary_characterized_icg (
    .CK(sample_clk_i), .E(gate_enable), .ECK(burst_clk_o)
  );
endmodule
