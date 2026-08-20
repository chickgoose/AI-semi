`timescale 1ns/1ps

module mc_wtb_motion_qualifier_tb;
  logic clk_i = 0;
  logic rst_i = 1;
  logic epoch_valid_i = 0;
  logic pose_reliable_i = 0;
  logic profile_authorized_i = 0;
  logic [7:0] displacement_q_i = 0;
  logic [1:0] motion_class_o;
  logic warp_enable_o, tile_enable_o, safe_bypass_o, class_changed_o;

  mc_wtb_motion_qualifier #(
    .DISP_W(8), .MID_TO_LOW_Q(30), .LOW_TO_MID_Q(40),
    .HIGH_TO_MID_Q(70), .MID_TO_HIGH_Q(80), .MIN_DWELL_EPOCHS(2)
  ) dut (.*);

  always #5 clk_i = ~clk_i;

  task automatic epoch(input logic reliable, input logic [7:0] displacement);
    begin
      @(negedge clk_i);
      pose_reliable_i = reliable;
      displacement_q_i = displacement;
      epoch_valid_i = 1;
      @(posedge clk_i);
      #1;
      epoch_valid_i = 0;
    end
  endtask

  initial begin
    repeat (2) @(posedge clk_i);
    @(negedge clk_i);
    rst_i = 0;
    #1;
    if (motion_class_o != 0 || !safe_bypass_o || warp_enable_o || tile_enable_o)
      $fatal(1, "reset must fail safe");

    epoch(1, 8'd90);
    if (motion_class_o != 0) $fatal(1, "unauthorized profile enabled motion");
    profile_authorized_i = 1;

    epoch(1, 8'd10);
    if (motion_class_o != 0) $fatal(1, "LOW enabled before dwell");
    epoch(1, 8'd10);
    if (motion_class_o != 1 || !safe_bypass_o) $fatal(1, "LOW route mismatch");
    epoch(1, 8'd50);
    epoch(1, 8'd50);
    if (motion_class_o != 2 || !warp_enable_o || tile_enable_o)
      $fatal(1, "MID route mismatch");
    epoch(1, 8'd90);
    epoch(1, 8'd90);
    if (motion_class_o != 3 || !warp_enable_o || !tile_enable_o)
      $fatal(1, "HIGH route mismatch");
    epoch(0, 8'd90);
    if (motion_class_o != 0 || !safe_bypass_o || warp_enable_o)
      $fatal(1, "unreliable pose did not bypass immediately");
    $display("MC_WTB_MOTION_QUALIFIER_RTL_PASS");
    $finish;
  end
endmodule
