`timescale 1ns/1ps

module aer_ganghee_steal_buf_seam_contract_tb;
  localparam logic [1:0] RAW_MODE = 2'd0;
  localparam logic [1:0] SERVICE_MODE = 2'd1;
  localparam logic [1:0] EDGE_MODE = 2'd2;
  localparam logic [1:0] ADMISSION_MODE = 2'd3;

  logic clk = 1'b0;
  logic rst;
  logic [1:0] mode;
  logic [15:0] raw_arrival;
  logic [15:0] seam_valid;
  logic [15:0] edge_seen;
  wire [15:0] arrival;
  wire [15:0] overrun;
  wire valid0;
  wire [1:0] row0;
  wire [3:0] col_mask0;
  wire valid1;
  wire [1:0] row1;
  wire [3:0] col_mask1;
  logic [15:0] decoded;
  logic [15:0] edge_overrun;
  logic [15:0] pre_output;
  integer errors = 0;
  integer outputs;
  integer index;

  always #5 clk = ~clk;

  assign arrival = (mode == RAW_MODE) ? raw_arrival :
                   (mode == EDGE_MODE) ? (seam_valid & ~edge_seen) :
                   seam_valid;

  aer_tx16_trad_rowcol_fovea_cluster2_steal_buf dut (
    .clk(clk), .rst(rst), .arrival(arrival), .overrun(overrun),
    .valid0(valid0), .row0(row0), .col_mask0(col_mask0),
    .valid1(valid1), .row1(row1), .col_mask1(col_mask1)
  );

  always_comb begin
    decoded = 16'b0;
    if (valid0)
      decoded[(integer'(row0) * 4) +: 4] |= col_mask0;
    if (valid1)
      decoded[(integer'(row1) * 4) +: 4] |= col_mask1;
  end

  task automatic reset_case;
    begin
      @(negedge clk);
      rst = 1'b1;
      mode = RAW_MODE;
      raw_arrival = 16'b0;
      seam_valid = 16'b0;
      edge_seen = 16'b0;
      repeat (2) @(posedge clk);
      #1;
      outputs = 0;
      rst = 1'b0;
    end
  endtask

  task automatic seam_edge;
    logic [15:0] accepted_now;
    begin
      // Inputs are stable by negedge.  Observe the combinational seam one time
      // unit before posedge, independent of active/NBA process ordering.
      @(negedge clk);
      #4;
      pre_output = decoded;
      edge_overrun = overrun;
      accepted_now = 16'b0;
      if (mode == SERVICE_MODE)
        accepted_now = seam_valid & pre_output;
      else if (mode == EDGE_MODE)
        accepted_now = seam_valid & pre_output;
      else if (mode == ADMISSION_MODE)
        accepted_now = seam_valid & ~edge_overrun;
      @(posedge clk);
      #1;
      outputs += $countones(decoded);
      if (mode == EDGE_MODE)
        edge_seen |= arrival;
      seam_valid &= ~accepted_now;
      if (mode == EDGE_MODE)
        edge_seen &= seam_valid;
      $display("mode=%0d arrival=%h edge_overrun=%h pre_output=%h post_output=%h held=%h seen=%h",
               mode, arrival, edge_overrun, pre_output, decoded,
               seam_valid, edge_seen);
    end
  endtask

  task automatic raw_edge(input logic [15:0] pulse_bits);
    begin
      @(negedge clk);
      raw_arrival = pulse_bits;
      #4;
      edge_overrun = overrun;
      @(posedge clk);
      #1;
      outputs += $countones(decoded);
      $display("prefill arrival=%h edge_overrun=%h output=%h",
               pulse_bits, edge_overrun, decoded);
    end
  endtask

  task automatic finish_drain(input integer cycles);
    begin
      @(negedge clk);
      raw_arrival = 16'b0;
      seam_valid = 16'b0;
      for (index = 0; index < cycles; index = index + 1) begin
        @(posedge clk);
        #1;
        outputs += $countones(decoded);
      end
    end
  endtask

  task automatic prefill_target_full;
    begin
      mode = RAW_MODE;
      raw_edge(16'h0111);
      raw_edge(16'h0111);
      raw_edge(16'h0111);
      // Eight of these nine pulses were admitted. Source 4 is now full.
    end
  endtask

  initial begin
    rst = 1'b1;
    mode = RAW_MODE;
    raw_arrival = 16'b0;
    seam_valid = 16'b0;
    edge_seen = 16'b0;

    // If completion is mistaken for admission, held-valid is recounted before
    // the first registered output returns.
    reset_case();
    mode = SERVICE_MODE;
    seam_valid = 16'h0010;
    repeat (5) seam_edge();
    finish_drain(2);
    if (outputs != 3) begin
      errors += 1;
      $error("service-ready case expected three outputs for one event, got %0d",
             outputs);
    end
    $display("SERVICE_READY_DUPLICATE outputs=%0d logical_events=1", outputs);

    // A one-shot suppresses the duplicate, but consumes one bit of history for
    // every source.
    reset_case();
    mode = EDGE_MODE;
    seam_valid = 16'h0010;
    repeat (4) seam_edge();
    finish_drain(2);
    if (outputs != 1) begin
      errors += 1;
      $error("edge case expected one output, got %0d", outputs);
    end
    $display("EDGE_STATE_SINGLE outputs=%0d logical_events=1", outputs);

    // When its only pulse meets a full counter, the one-shot remains armed and
    // cannot retry the held logical event after a simultaneous grant frees room.
    reset_case();
    prefill_target_full();
    mode = EDGE_MODE;
    raw_arrival = 16'b0;
    seam_valid = 16'h0010;
    edge_seen = 16'b0;
    seam_edge();
    if (edge_overrun != 16'h0010) begin
      errors += 1;
      $error("edge-full case did not hit target full overrun=%h", edge_overrun);
    end
    repeat (3) seam_edge();
    finish_drain(3);
    if (outputs != 8) begin
      errors += 1;
      $error("edge-full case admitted a ninth event unexpectedly outputs=%0d",
             outputs);
    end
    $display("EDGE_STATE_FULL_LOSS prefill_admitted=8 outputs=%0d", outputs);

    // The stateless admission mapping retries while full, then transfers once
    // on the first edge at which the native counter is not full.
    reset_case();
    prefill_target_full();
    mode = ADMISSION_MODE;
    raw_arrival = 16'b0;
    seam_valid = 16'h0010;
    seam_edge();
    if (edge_overrun != 16'h0010 || seam_valid != 16'h0010) begin
      errors += 1;
      $error("admission retry did not retain held event overrun=%h held=%h",
             edge_overrun, seam_valid);
    end
    seam_edge();
    if (edge_overrun != 16'h0000 || seam_valid != 16'h0000) begin
      errors += 1;
      $error("admission retry did not transfer exactly once overrun=%h held=%h",
             edge_overrun, seam_valid);
    end
    finish_drain(4);
    if (outputs != 9) begin
      errors += 1;
      $error("admission case expected nine conserved outputs, got %0d", outputs);
    end
    $display("STATELESS_ADMISSION_RETRY prefill_admitted=8 outputs=%0d", outputs);

    if (errors == 0)
      $display("GANGHEE_STEAL_BUF_SEAM_CONTRACT_PASS");
    else
      $fatal(1, "GANGHEE_STEAL_BUF_SEAM_CONTRACT_FAIL errors=%0d", errors);
    $finish;
  end
endmodule
