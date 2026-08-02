module a23_ee430_tx #(
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
  logic                          full_q;
  logic [ADDR_WIDTH-1:0]         addr_q;
  logic [SOURCE_INDEX_WIDTH-1:0] source_q;

  logic accept_event;
  logic complete_event;

  // A3's bubble-free refill is the EE430 cycle-stealing/forwarding point:
  // when RX accepts the resident event, upstream may replace it on the same
  // edge. If RX stalls, ready drops and valid/payload/source state is held.
  assign event_ready_o  = !full_q || aer_ready_i;
  assign accept_event   = event_valid_i && event_ready_o;
  assign complete_event = full_q && aer_ready_i;

  assign aer_valid_o  = full_q;
  assign aer_addr_o   = addr_q;
  assign aer_source_o = source_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      full_q              <= 1'b0;
      addr_q              <= '0;
      source_q            <= '0;
      completion_valid_o  <= 1'b0;
      completion_source_o <= '0;
    end else begin
      completion_valid_o <= complete_event;
      if (complete_event) begin
        completion_source_o <= source_q;
      end

      // Refill wins when completion and acceptance share the same edge.
      if (accept_event) begin
        full_q   <= 1'b1;
        addr_q   <= event_addr_i;
        source_q <= event_source_i;
      end else if (complete_event) begin
        full_q <= 1'b0;
      end
    end
  end
endmodule
