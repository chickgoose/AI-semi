// Port adapter for the original N=256 dual-level arbiter testbench. A23 is a
// flat rotating round-robin arbiter, so GROUP_SIZE is accepted and reported
// but does not select a hierarchy or alter the qualified arbitration policy.
module dual_level_arbiter #(
  parameter int unsigned NUM_SOURCES = 256,
  parameter int unsigned GROUP_SIZE = 16,
  parameter int unsigned INDEX_WIDTH = aer_pkg::index_width(NUM_SOURCES)
) (
  input  logic                   clk,
  input  logic                   rst_n,
  input  logic [NUM_SOURCES-1:0] req,
  input  logic                   advance,
  output logic [NUM_SOURCES-1:0] grant
);
  logic grant_valid;
  logic [INDEX_WIDTH-1:0] grant_index;

  a23_ee430_arbiter #(
    .NUM_SOURCES(NUM_SOURCES),
    .INDEX_WIDTH(INDEX_WIDTH)
  ) u_a23_arbiter (
    .clk_i(clk),
    .rst_ni(rst_n),
    .req_i(req),
    .advance_i(advance),
    .grant_onehot_o(grant),
    .grant_valid_o(grant_valid),
    .grant_index_o(grant_index)
  );

  initial begin
    $display("[A23_COMPAT] dual_level_arbiter adapter uses flat A23 RR; NUM_SOURCES=%0d GROUP_SIZE=%0d informational-only",
             NUM_SOURCES, GROUP_SIZE);
  end
endmodule
