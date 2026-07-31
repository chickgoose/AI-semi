module aer_dut #(
    parameter int unsigned NUM_SOURCES = 4,
    parameter int unsigned ADDR_WIDTH  = 16,
    parameter int unsigned FIFO_DEPTH = 4,
    localparam int unsigned SOURCE_WIDTH = (NUM_SOURCES > 1) ? $clog2(NUM_SOURCES) : 1,
    localparam int unsigned COUNT_WIDTH  = $clog2(FIFO_DEPTH + 1)
) (
    input  logic                         clk,
    input  logic                         rst_n,
    input  logic [NUM_SOURCES-1:0]       in_valid,
    output logic [NUM_SOURCES-1:0]       in_ready,
    input  logic [ADDR_WIDTH-1:0]        in_addr [NUM_SOURCES],
    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [ADDR_WIDTH-1:0]        out_addr,
    output logic [SOURCE_WIDTH-1:0]      out_src
);

    logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] packed_in_addr;
    logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] occupancy;

    genvar source;
    generate
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin : gen_pack_address
            assign packed_in_addr[source] = in_addr[source];
        end
    endgenerate

    aer_event_buffer #(
        .NUM_SOURCES (NUM_SOURCES),
        .ADDR_WIDTH  (ADDR_WIDTH),
        .FIFO_DEPTH  (FIFO_DEPTH)
    ) u_event_buffer (
        .clk_i          (clk),
        .rst_ni         (rst_n),
        .src_valid_i    (in_valid),
        .src_ready_o    (in_ready),
        .src_addr_i     (packed_in_addr),
        .event_valid_o  (out_valid),
        .event_ready_i  (out_ready),
        .event_addr_o   (out_addr),
        .event_source_o (out_src),
        .occupancy_o    (occupancy)
    );

    // occupancy is deliberately kept inside the wrapper for debug/PPA
    // observation without changing the common a3 DUT interface.

endmodule
