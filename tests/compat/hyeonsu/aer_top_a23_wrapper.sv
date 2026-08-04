// Thin name/shape adapter from Hyeonsu's aer_top contract to the qualified
// A23 DUT. TX_MODE and ARB_MODE are accepted for source compatibility only;
// they never select alternate logic or change A23 semantics.
module aer_top #(
  parameter int TX_MODE = 2,
  parameter int ARB_MODE = 0
) (
  input  logic                                             clk,
  input  logic                                             rst_n,
  input  logic [aer_pkg::NUM_SOURCES-1:0]                  in_valid,
  output logic [aer_pkg::NUM_SOURCES-1:0]                  in_ready,
  input  logic [aer_pkg::NUM_SOURCES-1:0]
               [aer_pkg::ADDR_WIDTH-1:0]                  in_addr,
  output logic                                             out_valid,
  input  logic                                             out_ready,
  output logic [aer_pkg::ADDR_WIDTH-1:0]                   out_addr,
  output logic [aer_pkg::SRC_WIDTH-1:0]                    out_src
);
  localparam int unsigned NUM_SOURCES = aer_pkg::NUM_SOURCES;
  localparam int unsigned ADDR_WIDTH = aer_pkg::ADDR_WIDTH;
  localparam int unsigned SRC_WIDTH = aer_pkg::SRC_WIDTH;

  logic [ADDR_WIDTH-1:0] unpacked_in_addr [NUM_SOURCES];

  // Kept visible because the original reset-mid-contention workload checks
  // dut.grant hierarchically. For A23 a granted event is the actual input
  // ready/valid handshake, which is the state-advance contract under test.
  logic [NUM_SOURCES-1:0] grant;
  assign grant = in_valid & in_ready;

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source++) begin : gen_unpack_address
      assign unpacked_in_addr[source] = in_addr[source];
    end
  endgenerate

  a23_ee430_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SRC_WIDTH)
  ) u_a23 (
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(in_valid),
    .in_ready(in_ready),
    .in_addr(unpacked_in_addr),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_addr(out_addr),
    .out_src(out_src)
  );

  initial begin
    $display("[A23_COMPAT] aer_top wrapper fixed to A23; TX_MODE=%0d ARB_MODE=%0d are compatibility-only",
             TX_MODE, ARB_MODE);
  end
endmodule
