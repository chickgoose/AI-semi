module a23_ee430_core #(
  parameter int unsigned NUM_SOURCES = aer_pkg::DEFAULT_NUM_SOURCES,
  parameter int unsigned ADDR_WIDTH = 16,
  parameter int unsigned SOURCE_INDEX_WIDTH = aer_pkg::index_width(NUM_SOURCES)
) (
  input  logic clk_i,
  input  logic rst_ni,

  input  logic [NUM_SOURCES-1:0] src_valid_i,
  output logic [NUM_SOURCES-1:0] src_ready_o,
  input  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] src_addr_i,

  output logic event_valid_o,
  input  logic event_ready_i,
  output logic [ADDR_WIDTH-1:0] event_addr_o,
  output logic [SOURCE_INDEX_WIDTH-1:0] event_source_o
);
  logic [NUM_SOURCES-1:0] grant_onehot;
  logic grant_valid;
  logic [SOURCE_INDEX_WIDTH-1:0] grant_index;
  logic input_handshake;

  logic tx_event_ready;
  logic [ADDR_WIDTH-1:0] selected_addr;
  logic aer_valid;
  logic aer_ready;
  logic [ADDR_WIDTH-1:0] aer_addr;
  logic [SOURCE_INDEX_WIDTH-1:0] aer_source;

  a23_ee430_arbiter #(
    .NUM_SOURCES(NUM_SOURCES),
    .INDEX_WIDTH(SOURCE_INDEX_WIDTH)
  ) u_arbiter (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .req_i(src_valid_i),
    .advance_i(input_handshake),
    .grant_onehot_o(grant_onehot),
    .grant_valid_o(grant_valid),
    .grant_index_o(grant_index)
  );

  always_comb begin
    selected_addr = '0;
    if (grant_valid) begin
      selected_addr = src_addr_i[grant_index];
    end
  end

  assign src_ready_o     = grant_onehot & {NUM_SOURCES{tx_event_ready}};
  assign input_handshake = grant_valid && tx_event_ready;

  a23_ee430_tx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_INDEX_WIDTH)
  ) u_tx (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .event_valid_i(grant_valid),
    .event_ready_o(tx_event_ready),
    .event_addr_i(selected_addr),
    .event_source_i(grant_index),
    .aer_valid_o(aer_valid),
    .aer_ready_i(aer_ready),
    .aer_addr_o(aer_addr),
    .aer_source_o(aer_source),
    .completion_valid_o(),
    .completion_source_o()
  );

  // Reuse the baseline one-entry elastic RX. Its simultaneous pop/refill path
  // forwards one event per cycle after pipeline fill without another FIFO.
  aer_rx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_INDEX_WIDTH)
  ) u_rx (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .aer_valid_i(aer_valid),
    .aer_ready_o(aer_ready),
    .aer_addr_i(aer_addr),
    .aer_source_i(aer_source),
    .event_valid_o(event_valid_o),
    .event_ready_i(event_ready_i),
    .event_addr_o(event_addr_o),
    .event_source_o(event_source_o)
  );

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES < 1) begin
      $fatal(1, "NUM_SOURCES must be at least one");
    end
    if (ADDR_WIDTH < 1) begin
      $fatal(1, "ADDR_WIDTH must be at least one");
    end
  end
`endif
endmodule
