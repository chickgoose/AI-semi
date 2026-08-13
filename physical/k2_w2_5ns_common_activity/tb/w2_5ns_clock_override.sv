`timescale 1ns/1ps

// TB-only clock authority.  The frozen common TB remains byte-identical; this
// bound module replaces only its testbench clock for the 5.0 ns campaign.
module w2_5ns_clock_override;
  logic ref_clk_5ns = 1'b0;

  initial begin
    force aer_clean_tb.clk = ref_clk_5ns;
    forever #2.5 ref_clk_5ns = ~ref_clk_5ns;
  end
endmodule

bind aer_clean_tb w2_5ns_clock_override w2_5ns_clock_override_i();
