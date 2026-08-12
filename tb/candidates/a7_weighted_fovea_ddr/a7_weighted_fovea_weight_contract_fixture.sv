`timescale 1ns/1ps

// UNIT_MODEL_ONLY: synthesizable contract fixture for the external scalar
// fovea interface and 1:5:5:1 aggregate row opportunity ratio.  Its row order
// is intentionally not claimed to match the canonical implementation.
module a7_weighted_fovea_weight_contract_fixture (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  output logic        valid,
  output logic [3:0]  addr
);
  logic [3:0] phase_q;
  logic [1:0] col_q [0:3];
  logic       grant_valid;
  logic [1:0] grant_row;
  logic [1:0] grant_col;
  logic [3:0] chosen_phase;
  integer offset;
  integer col_offset;
  integer phase_value;
  integer row_value;
  integer col_value;
  integer index_value;

  function automatic integer phase_row(input integer phase);
    if (phase == 0)
      phase_row = 0;
    else if (phase <= 5)
      phase_row = 1;
    else if (phase <= 10)
      phase_row = 2;
    else
      phase_row = 3;
  endfunction

  always_comb begin
    grant_valid = 1'b0;
    grant_row = '0;
    grant_col = '0;
    chosen_phase = phase_q;
    for (offset = 0; offset < 12; offset = offset + 1) begin
      phase_value = (integer'(phase_q) + offset) % 12;
      row_value = phase_row(phase_value);
      for (col_offset = 0; col_offset < 4; col_offset = col_offset + 1) begin
        col_value = (integer'(col_q[row_value]) + col_offset) % 4;
        index_value = (row_value * 4) + col_value;
        if (!grant_valid && req[index_value]) begin
          grant_valid = 1'b1;
          grant_row = 2'(row_value);
          grant_col = 2'(col_value);
          chosen_phase = 4'(phase_value);
        end
      end
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      valid <= 1'b0;
      addr <= '0;
      phase_q <= '0;
      col_q[0] <= '0;
      col_q[1] <= '0;
      col_q[2] <= '0;
      col_q[3] <= '0;
    end else begin
      valid <= grant_valid;
      if (grant_valid) begin
        addr <= {grant_row, grant_col};
        phase_q <= (chosen_phase == 11) ? 4'd0 : chosen_phase + 1'b1;
        col_q[grant_row] <= grant_col + 1'b1;
      end
    end
  end
endmodule
