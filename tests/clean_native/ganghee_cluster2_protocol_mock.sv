// Protocol-only row/column-bitmap fixture. This is not candidate RTL.
`timescale 1ns/1ps

module ganghee_cluster2_protocol_mock (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  output logic        valid0,
  output logic [1:0]  row0,
  output logic [3:0]  col_mask0,
  output logic        valid1,
  output logic [1:0]  row1,
  output logic [3:0]  col_mask1
);
`ifdef AER_CLUSTER2_MOCK_REPEAT
  parameter int REPEAT_EACH_RESULT = 1;
`else
  parameter int REPEAT_EACH_RESULT = 0;
`endif

  logic repeat_pending;

  always_ff @(posedge clk) begin
    if (rst) begin
`ifdef AER_CLUSTER2_MOCK_RESET_FAULT
      valid0 <= 1'b1;
      row0 <= 2'd1;
      col_mask0 <= 4'b0010;
`else
      valid0 <= 1'b0;
      row0 <= '0;
      col_mask0 <= '0;
`endif
      valid1 <= 1'b0;
      row1 <= '0;
      col_mask1 <= '0;
      repeat_pending <= 1'b0;
    end else if (repeat_pending) begin
      // Deliberate native fault: retain the preceding result for one extra
      // cycle after its request has been acknowledged.
      repeat_pending <= 1'b0;
    end else begin
      valid0 <= 1'b0;
      row0 <= '0;
      col_mask0 <= '0;
      valid1 <= 1'b0;
      row1 <= '0;
      col_mask1 <= '0;
      if (req[7:4] != '0) begin
        valid0 <= 1'b1;
        row0 <= 2'd1;
        col_mask0 <= req[7:4];
      end else if (req[11:8] != '0) begin
        valid0 <= 1'b1;
        row0 <= 2'd2;
        col_mask0 <= req[11:8];
      end
      if (req[3:0] != '0) begin
        valid1 <= 1'b1;
        row1 <= 2'd0;
        col_mask1 <= req[3:0];
      end else if (req[15:12] != '0) begin
        valid1 <= 1'b1;
        row1 <= 2'd3;
        col_mask1 <= req[15:12];
      end
      if ((REPEAT_EACH_RESULT != 0) && (req != '0))
        repeat_pending <= 1'b1;
    end
  end
endmodule
