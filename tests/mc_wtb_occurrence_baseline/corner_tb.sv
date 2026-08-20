`timescale 1ps/1ps

module mc_wtb_occurrence_baseline_corner_tb;
  localparam integer PAYLOAD_W = 8;
  logic clk_i = 0;
  logic rst_i = 1;
  logic link_enable_i = 0;
  logic [5:0] ingress_valid_i = 0;
  logic [23:0] ingress_source_i = 0;
  logic [47:0] ingress_payload_i = 0;
  logic ingress_ready_o, ingress_commit_o;
  logic [1:0] accept_count_o, retire_count_o;
  logic [3:0] accept_source0_o, accept_source1_o;
  logic [3:0] retire_source0_o, retire_source1_o;
  logic [7:0] accept_payload0_o, accept_payload1_o;
  logic [7:0] retire_payload0_o, retire_payload1_o;
  logic overflow_o, protocol_error_o, drain_idle_o;
  integer accept_index = 0;
  integer retire_index = 0;
  logic [7:0] expected [0:3];

  mc_wtb_occurrence_baseline_top #(.PAYLOAD_W(PAYLOAD_W)) dut (.*);

  always #5000 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    #1;
    if (!rst_i && accept_count_o != 0) begin
      if (accept_count_o != 1 || accept_source0_o != 0 ||
          accept_payload0_o !== expected[accept_index])
        $fatal(1, "accept old-before-new mismatch index=%0d", accept_index);
      accept_index = accept_index + 1;
    end
    if (!rst_i && retire_count_o != 0) begin
      if (retire_count_o != 1 || retire_source0_o != 0 ||
          retire_payload0_o !== expected[retire_index])
        $fatal(1, "retire old-before-new mismatch index=%0d", retire_index);
      retire_index = retire_index + 1;
    end
  end

  initial begin
    expected[0] = 8'h11;
    expected[1] = 8'h22;
    expected[2] = 8'h33;
    expected[3] = 8'h44;
    repeat (3) @(posedge clk_i);
    @(negedge clk_i);
    rst_i = 0;

    // Fill source 0 to depth three while service is disabled.
    ingress_valid_i = 6'b000111;
    ingress_source_i = 24'd0;
    ingress_payload_i[7:0] = expected[0];
    ingress_payload_i[15:8] = expected[1];
    ingress_payload_i[23:16] = expected[2];
    #1;
    if (!ingress_ready_o)
      $fatal(1, "depth-three fill unexpectedly rejected");
    @(posedge clk_i);
    @(negedge clk_i);

    // Full-bank same-edge pop credit must accept the fourth occurrence while
    // exposing the pre-edge old head, never the replacement payload.
    link_enable_i = 1;
    ingress_valid_i = 6'b000001;
    ingress_payload_i = 48'd0;
    ingress_payload_i[7:0] = expected[3];
    #1;
    if (!ingress_ready_o)
      $fatal(1, "full+pop replacement credit was rejected");
    @(posedge clk_i);
    @(negedge clk_i);
    ingress_valid_i = 0;
    ingress_payload_i = 0;
    while (retire_index != 4) begin
      @(posedge clk_i);
      #1;
      if (overflow_o || protocol_error_o)
        $fatal(1, "unexpected sticky error in old-before-new test");
      @(negedge clk_i);
    end
    @(posedge clk_i);
    #1;
    if (accept_index != 4 || !drain_idle_o)
      $fatal(1, "directed queue did not cleanly drain");

    // A full bank without a pop rejects the complete offered batch and makes
    // the loss visible through the sticky overflow flag.
    rst_i = 1;
    link_enable_i = 0;
    @(posedge clk_i);
    @(negedge clk_i);
    rst_i = 0;
    ingress_valid_i = 6'b000111;
    ingress_source_i = {6{4'd1}};
    ingress_payload_i = 48'h000000_030201;
    @(posedge clk_i);
    @(negedge clk_i);
    ingress_valid_i = 6'b000001;
    ingress_payload_i = 48'h000000_000004;
    #1;
    if (ingress_ready_o)
      $fatal(1, "full bank without pop incorrectly reported ready");
    @(posedge clk_i);
    #1;
    if (!overflow_o)
      $fatal(1, "rejected occurrence did not set sticky overflow");

    $display("MC_WTB_OCCURRENCE_BASELINE_CORNER_PASS");
    $finish;
  end
endmodule
