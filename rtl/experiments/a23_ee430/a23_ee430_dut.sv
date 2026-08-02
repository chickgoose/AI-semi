module a23_ee430_dut #(
  parameter int unsigned NUM_SOURCES = 4,
  parameter int unsigned ADDR_WIDTH = 16,
  parameter int unsigned SOURCE_WIDTH = aer_pkg::index_width(NUM_SOURCES)
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
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] packed_in_addr;

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source++) begin : gen_pack_address
      assign packed_in_addr[source] = in_addr[source];
    end
  endgenerate

  a23_ee430_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_WIDTH)
  ) u_core (
    .clk_i(clk),
    .rst_ni(rst_n),
    .src_valid_i(in_valid),
    .src_ready_o(in_ready),
    .src_addr_i(packed_in_addr),
    .event_valid_o(out_valid),
    .event_ready_i(out_ready),
    .event_addr_o(out_addr),
    .event_source_o(out_src)
  );
endmodule
