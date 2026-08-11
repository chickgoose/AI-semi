`timescale 1ns/1ps

module a6_w5_phase_related_rx_boundary_tb;
  logic core_clk_i = 1'b0;
  logic core_reset_i = 1'b1;
  logic [3:0] retire_addr_i = '0;
  logic retire_toggle_i = 1'b0;
  logic [3:0] core_event_addr_o;
  logic core_event_valid_o;
  integer delivered = 0;

  a6_w5_phase_related_rx_boundary dut (.*);

  always #8ns core_clk_i = ~core_clk_i;

  task automatic commit_and_check(input logic [3:0] address);
    // Model the frozen W4 relationship: RX falling-edge commit is 4 ns before
    // the related downstream/ref core rising edge.
    @(negedge core_clk_i);
    #4ns;
    retire_addr_i = address;
    retire_toggle_i = ~retire_toggle_i;
    @(posedge core_clk_i);
    #1ps;
    if (!core_event_valid_o || core_event_addr_o !== address)
      $fatal(1, "R1 capture mismatch expected=%0h valid=%0b actual=%0h",
             address, core_event_valid_o, core_event_addr_o);
    delivered = delivered + 1;
  endtask

  initial begin
    // Exercise the synchronous reset with the source-side toggle known zero.
    repeat (2) @(posedge core_clk_i);
    #1ps;
    if (core_event_valid_o !== 1'b0)
      $fatal(1, "valid asserted during reset");
    core_reset_i = 1'b0;

    // Back-to-back R1 commits, repeated addresses, and all N16 identities.
    for (integer address = 0; address < 16; address = address + 1)
      commit_and_check(address[3:0]);
    commit_and_check(4'h7);
    commit_and_check(4'h7);

    // An idle core edge must clear the one-cycle valid pulse without changing
    // the last delivered identity.
    @(posedge core_clk_i);
    #1ps;
    if (core_event_valid_o !== 1'b0 || core_event_addr_o !== 4'h7)
      $fatal(1, "idle behavior mismatch");

    if (delivered != 18)
      $fatal(1, "delivery count mismatch: %0d", delivered);
    $display("A6_W5_PHASE_RELATED_R1_PASS delivered=%0d state_bits=6", delivered);
    $finish;
  end
endmodule
