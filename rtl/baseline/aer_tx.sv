module aer_tx #(
  parameter int unsigned ADDR_WIDTH = 3,
  parameter int unsigned SOURCE_INDEX_WIDTH = 3
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  input  logic                          event_valid_i,
  output logic                          event_ready_o,
  input  logic [ADDR_WIDTH-1:0]         event_addr_i,
  input  logic [SOURCE_INDEX_WIDTH-1:0] event_source_i,

  output logic                          aer_valid_o,
  input  logic                          aer_ready_i,
  output logic [ADDR_WIDTH-1:0]         aer_addr_o,
  output logic [SOURCE_INDEX_WIDTH-1:0] aer_source_o,

  output logic                          completion_valid_o,
  output logic [SOURCE_INDEX_WIDTH-1:0] completion_source_o
);
  logic                          busy_q;
  logic [ADDR_WIDTH-1:0]         addr_q;
  logic [SOURCE_INDEX_WIDTH-1:0] source_q;

  assign event_ready_o = !busy_q;
  assign aer_valid_o   = busy_q;
  assign aer_addr_o    = addr_q;
  assign aer_source_o  = source_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q              <= 1'b0;
      addr_q              <= '0;
      source_q            <= '0;
      completion_valid_o  <= 1'b0;
      completion_source_o <= '0;
    end else begin
      completion_valid_o <= 1'b0;

      if (busy_q) begin
        if (aer_ready_i) begin
          busy_q              <= 1'b0;
          completion_valid_o  <= 1'b1;
          completion_source_o <= source_q;
        end
      end else if (event_valid_i) begin
        busy_q   <= 1'b1;
        addr_q   <= event_addr_i;
        source_q <= event_source_i;
      end
    end
  end
endmodule
