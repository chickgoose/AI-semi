// Standalone smoke-test model. This is not the competition RTL.
module aer_mock_dut #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic clk,
  input  logic rst_n,
  input  logic [NUM_SOURCES-1:0] in_valid,
  output logic [NUM_SOURCES-1:0] in_ready,
  input  logic [ADDR_WIDTH-1:0] in_addr [NUM_SOURCES],
  output logic out_valid,
  input  logic out_ready,
  output logic [ADDR_WIDTH-1:0] out_addr,
  output logic [SOURCE_WIDTH-1:0] out_src
);
  logic [SOURCE_WIDTH-1:0] rr_start;
  integer offset;
  integer candidate;
  integer selected;

  always_comb begin
    selected = -1;
    for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
      candidate = rr_start + offset;
      if (candidate >= NUM_SOURCES)
        candidate = candidate - NUM_SOURCES;
      if ((selected < 0) && in_valid[candidate])
        selected = candidate;
    end

    in_ready = '0;
    out_valid = (selected >= 0);
    out_addr = '0;
    out_src = '0;
    if (selected >= 0) begin
      out_addr = in_addr[selected];
      out_src = SOURCE_WIDTH'(selected);
      in_ready[selected] = out_ready;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rr_start <= '0;
    end else if (out_valid && out_ready) begin
      if (out_src == NUM_SOURCES-1)
        rr_start <= '0;
      else
        rr_start <= out_src + 1'b1;
    end
  end
endmodule
