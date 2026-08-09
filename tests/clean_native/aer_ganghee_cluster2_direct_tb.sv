`timescale 1ns/1ps

module aer_ganghee_cluster2_direct_tb;
  localparam int ADDR_WIDTH = 16;
  logic clk = 1'b0;
  logic rst;
  logic [15:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event [16];
  wire [15:0] req = source_valid;
  logic valid0;
  logic [1:0] row0;
  logic [3:0] col_mask0;
  logic valid1;
  logic [1:0] row1;
  logic [3:0] col_mask1;
  integer source;
  integer col;
  integer seen = 0;
  integer errors = 0;

  always #5 clk = ~clk;

  // The raw DUT is instantiated directly and req has no masking or adapter.
  ganghee_cluster2_protocol_mock raw_cluster2_dut (
    .clk, .rst, .req, .valid0, .row0, .col_mask0,
    .valid1, .row1, .col_mask1
  );

  task automatic observe(input logic valid,
                         input logic [1:0] row,
                         input logic [3:0] col_mask);
    logic [ADDR_WIDTH-1:0] retire_event;
    begin
      if (valid)
        for (col = 0; col < 4; col = col + 1)
          if (col_mask[col]) begin
            source = integer'(row) * 4 + col;
            retire_event = ADDR_WIDTH'(source);
            if (retire_event === source_event[source]) begin
              errors = errors + 1;
              $error("CLUSTER2_DIRECT_CANARY reconstructed free metadata source=%0d value=%h",
                     source, retire_event);
            end
            if (retire_event !== ADDR_WIDTH'(source)) begin
              errors = errors + 1;
              $error("CLUSTER2_DIRECT_CANARY address mismatch source=%0d value=%h",
                     source, retire_event);
            end
            source_valid[source] = 1'b0;
            seen = seen + 1;
          end
    end
  endtask

  initial begin
    rst = 1'b1;
    source_valid = '0;
    for (source = 0; source < 16; source = source + 1)
      source_event[source] = 16'h800f | ADDR_WIDTH'(source << 4);
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;
    source_valid = 16'b1000_0010_0101_1001;

    #1;
    observe(valid0, row0, col_mask0);
    observe(valid1, row1, col_mask1);
    #1;
    observe(valid0, row0, col_mask0);
    observe(valid1, row1, col_mask1);
    #1;
    observe(valid0, row0, col_mask0);
    observe(valid1, row1, col_mask1);

    if (source_valid != '0) begin
      errors = errors + 1;
      $error("CLUSTER2_DIRECT_CANARY requests not fully observed pending=%h",
             source_valid);
    end
    if (seen != 6) begin
      errors = errors + 1;
      $error("CLUSTER2_DIRECT_CANARY count mismatch seen=%0d", seen);
    end
    if (errors == 0)
      $display("GANGHEE_CLUSTER2_DIRECT_ANTI_RECONSTRUCTION_PASS seen=%0d", seen);
    else
      $fatal(1, "GANGHEE_CLUSTER2_DIRECT_ANTI_RECONSTRUCTION_FAIL errors=%0d", errors);
    $finish;
  end
endmodule
