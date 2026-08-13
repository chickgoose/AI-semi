`timescale 1ns/1ps

module w2_p6_owner_vs_tech_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic input_valid = 1'b0;
  logic [1:0] input_count = 2'd0;
  logic [3:0] input_addr0 = 4'd0;
  logic [3:0] input_addr1 = 4'd0;

  logic owner_ready, tech_ready;
  logic owner_input_error, tech_input_error;
  logic owner_p6_clk, tech_p6_clk;
  logic [4:0] owner_p6_data, tech_p6_data;
  logic [1:0] owner_retire_valid, tech_retire_valid;
  logic [3:0] owner_retire_addr0, tech_retire_addr0;
  logic [3:0] owner_retire_addr1, tech_retire_addr1;
  logic owner_retire_error, tech_retire_error;
  logic owner_drain, tech_drain;

  integer checks = 0;
  integer accepted = 0;
  integer retired = 0;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

  a7_p6_exact_pair_endpoint owner (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .input_valid_i(input_valid), .input_count_i(input_count),
    .input_addr0_i(input_addr0), .input_addr1_i(input_addr1),
    .input_ready_o(owner_ready),
    .input_protocol_error_o(owner_input_error),
    .p6_clk_o(owner_p6_clk), .p6_data_o(owner_p6_data),
    .retire_valid_o(owner_retire_valid),
    .retire_addr0_o(owner_retire_addr0),
    .retire_addr1_o(owner_retire_addr1),
    .retire_protocol_error_o(owner_retire_error),
    .drain_idle_o(owner_drain)
  );

  w2_p6_exact_pair_endpoint_tech tech (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .input_valid_i(input_valid), .input_count_i(input_count),
    .input_addr0_i(input_addr0), .input_addr1_i(input_addr1),
    .input_ready_o(tech_ready),
    .input_protocol_error_o(tech_input_error),
    .p6_clk_o(tech_p6_clk), .p6_data_o(tech_p6_data),
    .retire_valid_o(tech_retire_valid),
    .retire_addr0_o(tech_retire_addr0),
    .retire_addr1_o(tech_retire_addr1),
    .retire_protocol_error_o(tech_retire_error),
    .drain_idle_o(tech_drain)
  );

  task automatic compare_all(input string phase);
    begin
      checks = checks + 1;
      if (owner_ready !== tech_ready ||
          owner_input_error !== tech_input_error ||
          owner_p6_clk !== tech_p6_clk ||
          owner_p6_data !== tech_p6_data ||
          owner_retire_valid !== tech_retire_valid ||
          owner_retire_addr0 !== tech_retire_addr0 ||
          owner_retire_addr1 !== tech_retire_addr1 ||
          owner_retire_error !== tech_retire_error ||
          owner_drain !== tech_drain)
        $fatal(1, "W2_P6_OWNER_VS_TECH_FAIL phase=%s check=%0d", phase, checks);
    end
  endtask

  always @(posedge ref_clk) begin
    if (rst_n && input_valid && owner_ready)
      accepted = accepted + input_count;
    #1 compare_all("ref-rise");
    if (owner_retire_valid[0])
      retired = retired + 1;
    if (owner_retire_valid[1])
      retired = retired + 1;
  end

  always @(negedge ref_clk) #1 compare_all("ref-fall");
  always @(posedge sample_clk) #1 compare_all("sample-rise");
  always @(negedge sample_clk) #1 compare_all("sample-fall");

  task automatic drive(input logic valid, input logic [1:0] count,
                       input logic [3:0] addr0, input logic [3:0] addr1);
    begin
      @(negedge ref_clk);
      input_valid = valid;
      input_count = count;
      input_addr0 = addr0;
      input_addr1 = addr1;
    end
  endtask

  task automatic wait_drain;
    begin
      drive(1'b0, 2'd0, 4'd0, 4'd0);
      while (!owner_drain || !tech_drain)
        @(posedge ref_clk);
      @(negedge ref_clk);
    end
  endtask

  initial begin
    // Held legal input proves reset and one-edge release arming equivalence.
    drive(1'b1, 2'd2, 4'hd, 4'h2);
    repeat (3) @(posedge ref_clk);
    @(negedge sample_clk);
    if (sample_clk !== 1'b0)
      $fatal(1, "W2_P6_RESET_PHASE_FAIL release clock is not low");
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);

    // Exhaust the complete legal 10-bit P6 code space: 16 singleton records
    // and 256 ordered pair records, all as continuous one-record-per-cycle traffic.
    for (integer first = 0; first < 16; first = first + 1)
      drive(1'b1, 2'd1, first[3:0], 4'hf);
    for (integer first = 0; first < 16; first = first + 1)
      for (integer second = 0; second < 16; second = second + 1)
        drive(1'b1, 2'd2, first[3:0], second[3:0]);

    // Gaps and changing count/address traffic.
    for (integer index = 0; index < 96; index = index + 1) begin
      if ((index % 5) == 0)
        drive(1'b0, 2'd0, 4'd0, 4'd0);
      else
        drive(1'b1, (index & 1) ? 2'd1 : 2'd2,
              index[3:0], 4'(15-index));
    end
    wait_drain();

    // Invalid shapes must have identical error and ready behavior.
    drive(1'b1, 2'd0, 4'h1, 4'h2);
    repeat (2) @(posedge ref_clk);
    drive(1'b1, 2'd3, 4'h3, 4'h4);
    repeat (2) @(posedge ref_clk);
    drive(1'b0, 2'd2, 4'h5, 4'h6);
    repeat (2) @(posedge ref_clk);
    wait_drain();

    // Legal drained reset/re-arm followed by one final pair.
    @(negedge sample_clk);
    if (sample_clk !== 1'b0)
      $fatal(1, "W2_P6_RESET_PHASE_FAIL assertion clock is not low");
    rst_n = 1'b0;
    repeat (2) @(posedge ref_clk);
    @(negedge sample_clk);
    if (sample_clk !== 1'b0)
      $fatal(1, "W2_P6_RESET_PHASE_FAIL release clock is not low");
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);
    drive(1'b1, 2'd2, 4'h3, 4'hc);
    drive(1'b0, 2'd0, 4'd0, 4'd0);
    wait_drain();

    if (accepted != retired)
      $fatal(1, "W2_P6_OWNER_VS_TECH_FAIL accepted=%0d retired=%0d",
             accepted, retired);
    $display("W2_P6_OWNER_VS_TECH_PASS checks=%0d accepted=%0d retired=%0d",
             checks, accepted, retired);
    $finish;
  end
endmodule
