`timescale 1ns/1ps

module aer_ganghee_cluster2_direct_tb #(
  parameter int REPEAT_EACH_RESULT = 0
);
  localparam int ADDR_WIDTH = 16;
  logic clk = 1'b0;
  logic rst;
  logic [15:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event [16];
  logic [15:0] current_result_mask;
  wire [15:0] req = source_valid & ~current_result_mask;
  logic valid0;
  logic [1:0] row0;
  logic [3:0] col_mask0;
  logic valid1;
  logic [1:0] row1;
  logic [3:0] col_mask1;
  integer source;
  integer col;
  integer raw_seen = 0;
  integer acknowledgement_count = 0;
  integer phantom_count = 0;
  integer masked_result_edges = 0;
  integer errors = 0;

  always #5 clk = ~clk;

  ganghee_cluster2_protocol_mock #(
    .REPEAT_EACH_RESULT(REPEAT_EACH_RESULT)
  ) raw_cluster2_dut (
    .clk, .rst, .req, .valid0, .row0, .col_mask0,
    .valid1, .row1, .col_mask1
  );

  always_comb begin
    current_result_mask = '0;
    for (col = 0; col < 4; col = col + 1) begin
      if (valid0 && col_mask0[col])
        current_result_mask[(integer'(row0) * 4) + col] = 1'b1;
      if (valid1 && col_mask1[col])
        current_result_mask[(integer'(row1) * 4) + col] = 1'b1;
    end
  end

  task automatic observe_raw(input logic valid,
                             input logic [1:0] row,
                             input logic [3:0] col_mask);
    logic [ADDR_WIDTH-1:0] retire_event;
    begin
      if (valid)
        for (integer observed_col = 0; observed_col < 4;
             observed_col = observed_col + 1)
          if (col_mask[observed_col]) begin
            source = integer'(row) * 4 + observed_col;
            retire_event = ADDR_WIDTH'(source);
            raw_seen = raw_seen + 1;
            if (!req[source])
              masked_result_edges = masked_result_edges + 1;
            else begin
              errors = errors + 1;
              $error("CLUSTER2_DIRECT req was not masked on result source=%0d", source);
            end
            if (retire_event === source_event[source]) begin
              errors = errors + 1;
              $error("CLUSTER2_DIRECT reconstructed free metadata source=%0d value=%h",
                     source, retire_event);
            end
            if (source_valid[source]) begin
              acknowledgement_count = acknowledgement_count + 1;
              source_valid[source] <= 1'b0;
            end else begin
              // This mirrors the common scoreboard: unmasked raw retirement
              // with no accepted event is a visible phantom/duplicate.
              phantom_count = phantom_count + 1;
            end
          end
    end
  endtask

  always @(posedge clk) begin
    if (!rst) begin
      observe_raw(valid0, row0, col_mask0);
      observe_raw(valid1, row1, col_mask1);
    end
  end

  initial begin
    rst = 1'b1;
    source_valid = '0;
    for (source = 0; source < 16; source = source + 1)
      source_event[source] = 16'h800f | ADDR_WIDTH'(source << 4);
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;
    // Six level requests remain asserted until the current raw result is
    // acknowledged.  Two native output groups cover both result ports.
    source_valid = 16'b1000_0010_0101_1001;

    repeat (10) @(posedge clk);
    @(negedge clk);
    if (source_valid != '0) begin
      errors = errors + 1;
      $error("CLUSTER2_DIRECT requests not fully acknowledged pending=%h",
             source_valid);
    end
    if (acknowledgement_count != 6) begin
      errors = errors + 1;
      $error("CLUSTER2_DIRECT held request ack mismatch actual=%0d",
             acknowledgement_count);
    end
    if (REPEAT_EACH_RESULT == 0) begin
      if ((raw_seen != 6) || (phantom_count != 0) ||
          (masked_result_edges != 6)) begin
        errors = errors + 1;
        $error("CLUSTER2_DIRECT held result mismatch raw=%0d phantom=%0d masked=%0d",
               raw_seen, phantom_count, masked_result_edges);
      end
      if (errors == 0)
        $display("GANGHEE_CLUSTER2_HELD_ACK_PASS raw=6 ack=6 phantom=0 masked=6");
    end else begin
      if ((raw_seen != 12) || (phantom_count != 6) ||
          (masked_result_edges != 12)) begin
        errors = errors + 1;
        $error("CLUSTER2_DIRECT repeat visibility mismatch raw=%0d phantom=%0d masked=%0d",
               raw_seen, phantom_count, masked_result_edges);
      end
      if (errors == 0)
        $display("GANGHEE_CLUSTER2_PHANTOM_VISIBLE_PASS raw=12 ack=6 phantom=6 masked=12");
    end
    if (errors != 0)
      $fatal(1, "GANGHEE_CLUSTER2_DIRECT_FAIL errors=%0d", errors);
    $finish;
  end
endmodule
