module aer_baseline_top #(
  parameter int unsigned NUM_SOURCES = aer_pkg::DEFAULT_NUM_SOURCES,
  parameter int unsigned SOURCE_INDEX_WIDTH = aer_pkg::index_width(NUM_SOURCES),
  parameter int unsigned ADDR_WIDTH = SOURCE_INDEX_WIDTH
) (
  input  logic                   clk_i,
  input  logic                   rst_ni,

  input  logic [NUM_SOURCES-1:0] source_req_i,
  output logic [NUM_SOURCES-1:0] source_ack_o,

  output logic                   event_valid_o,
  input  logic                   event_ready_i,
  output logic [ADDR_WIDTH-1:0]  event_addr_o
);
  logic                         arb_grant_valid;
  logic [SOURCE_INDEX_WIDTH-1:0] arb_grant_index;

  logic [ADDR_WIDTH-1:0] tx_event_addr;
  logic                  tx_aer_valid;
  logic                  tx_aer_ready;
  logic [ADDR_WIDTH-1:0] tx_aer_addr;
  logic [SOURCE_INDEX_WIDTH-1:0] tx_aer_source;
  logic                  tx_completion_valid;
  logic [SOURCE_INDEX_WIDTH-1:0] tx_completion_source;

  fixed_priority_arbiter #(
    .NUM_SOURCES(NUM_SOURCES),
    .INDEX_WIDTH(SOURCE_INDEX_WIDTH)
  ) u_arbiter (
    .req_i(source_req_i),
    .grant_onehot_o(),
    .grant_valid_o(arb_grant_valid),
    .grant_index_o(arb_grant_index)
  );

  always_comb begin
    tx_event_addr = '0;
    tx_event_addr[SOURCE_INDEX_WIDTH-1:0] = arb_grant_index;
  end

  aer_tx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_INDEX_WIDTH)
  ) u_tx (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .event_valid_i(arb_grant_valid),
    .event_ready_o(),
    .event_addr_i(tx_event_addr),
    .event_source_i(arb_grant_index),
    .aer_valid_o(tx_aer_valid),
    .aer_ready_i(tx_aer_ready),
    .aer_addr_o(tx_aer_addr),
    .aer_source_o(tx_aer_source),
    .completion_valid_o(tx_completion_valid),
    .completion_source_o(tx_completion_source)
  );

  aer_rx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_INDEX_WIDTH)
  ) u_rx (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .aer_valid_i(tx_aer_valid),
    .aer_ready_o(tx_aer_ready),
    .aer_addr_i(tx_aer_addr),
    .aer_source_i(tx_aer_source),
    .event_valid_o(event_valid_o),
    .event_ready_i(event_ready_i),
    .event_addr_o(event_addr_o),
    .event_source_o()
  );

  always_comb begin
    source_ack_o = '0;
    if (tx_completion_valid && (tx_completion_source < NUM_SOURCES)) begin
      source_ack_o[tx_completion_source] = 1'b1;
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (ADDR_WIDTH < SOURCE_INDEX_WIDTH) begin
      $fatal(1, "ADDR_WIDTH must fit every source index");
    end
  end
`endif
endmodule
