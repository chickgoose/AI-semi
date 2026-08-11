`timescale 1ns/1ps

module a7_w4_icg_boundary_tb;
  localparam time HALF_PERIOD = 4ns;

  logic clock_i;
  logic enable_i;
  logic rst_n;
  logic clock_o;
  integer rise_count;
  integer fall_count;
  realtime rise_time;

  a7_w4_icg_boundary dut (.*);

  initial begin
    clock_i = 1'b0;
    forever #(HALF_PERIOD) clock_i = ~clock_i;
  end

  always @(posedge clock_o) begin
    if (clock_i !== 1'b1)
      $fatal(1, "generated rising edge not coincident with source edge");
    rise_time = $realtime;
    rise_count = rise_count + 1;
  end

  always @(negedge clock_o) begin
    if (rst_n && (clock_i !== 1'b0))
      $fatal(1, "generated falling edge not coincident with source edge");
    if (rst_n && (($realtime - rise_time) < HALF_PERIOD))
      $fatal(1, "runt gated-clock high pulse width=%0t", $realtime-rise_time);
    if (rst_n)
      fall_count = fall_count + 1;
  end

  initial begin
    clock_i = 1'b0;
    enable_i = 1'b0;
    rst_n = 1'b0;
    rise_count = 0;
    fall_count = 0;
    #1ns;
    rst_n = 1'b1;

    repeat (2) @(posedge clock_i);
    if (clock_o !== 1'b0)
      $fatal(1, "idle clock did not remain stopped");

    // Legal low-phase enable begins a four-cycle merged burst.
    @(negedge clock_i);
    enable_i = 1'b1;
    repeat (4) @(posedge clock_i);
    @(negedge clock_i);
    enable_i = 1'b0;
    @(posedge clock_i);
    #1ps;
    if ((rise_count != 4) || (fall_count != 4) || (clock_o !== 1'b0))
      $fatal(1, "merged burst mismatch rise=%0d fall=%0d", rise_count, fall_count);
    $display("A7_W4_ICG_MERGED_BURST_PASS rise=4 fall=4");

    // An enable asserted during clock high cannot create a late/runt edge.
    #1ns;
    enable_i = 1'b1;
    #(HALF_PERIOD-1ns);
    if (clock_o !== 1'b0)
      $fatal(1, "high-phase enable leaked into clock output");
    @(posedge clock_i);
    if (clock_o !== 1'b1)
      $fatal(1, "enable was not admitted at next complete cycle");

    // Deasserting during high must not truncate the active pulse.
    #1ns;
    enable_i = 1'b0;
    #(HALF_PERIOD-1ns);
    #1ps;
    if (clock_o !== 1'b0)
      $fatal(1, "complete high pulse did not close with source clock");
    @(posedge clock_i);
    #1ps;
    if (clock_o !== 1'b0)
      $fatal(1, "high-phase disable was not held for next cycle");
    $display("A7_W4_ICG_HIGH_PHASE_ENABLE_PASS");

    // The supported reset use is drained and low-phase only.
    @(negedge clock_i);
    rst_n = 1'b0;
    #1ns;
    if (clock_o !== 1'b0)
      $fatal(1, "reset did not force idle clock low");
    rst_n = 1'b1;
    repeat (2) @(posedge clock_i);
    if (clock_o !== 1'b0)
      $fatal(1, "reset leaked a clock edge");
    $display("A7_W4_ICG_RESET_IDLE_PASS");
    $display("A7_W4_ICG_BOUNDARY_PASS");
    $finish;
  end
endmodule
