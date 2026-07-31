module aer_rx #(
  parameter int unsigned ADDR_WIDTH = 3
) (
  input  logic                  clk_i,
  input  logic                  rst_ni,

  input  logic                  aer_valid_i,
  output logic                  aer_ready_o,
  input  logic [ADDR_WIDTH-1:0] aer_addr_i,

  output logic                  event_valid_o,
  input  logic                  event_ready_i,
  output logic [ADDR_WIDTH-1:0] event_addr_o
);
  logic                  full_q;
  logic [ADDR_WIDTH-1:0] addr_q;

  assign aer_ready_o   = !full_q || event_ready_i;
  assign event_valid_o = full_q;
  assign event_addr_o  = addr_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      full_q <= 1'b0;
      addr_q <= '0;
    end else if (aer_ready_o) begin
      if (aer_valid_i) begin
        full_q <= 1'b1;
        addr_q <= aer_addr_i;
      end else if (event_ready_i) begin
        full_q <= 1'b0;
      end
    end
  end
endmodule
