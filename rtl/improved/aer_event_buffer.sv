module aer_event_buffer #(
    parameter int unsigned NUM_SOURCES = 4,
    parameter int unsigned ADDR_WIDTH  = 8,
    parameter int unsigned FIFO_DEPTH = 4,
    localparam int unsigned SOURCE_WIDTH = (NUM_SOURCES > 1) ? $clog2(NUM_SOURCES) : 1,
    localparam int unsigned COUNT_WIDTH  = $clog2(FIFO_DEPTH + 1)
) (
    input  logic                                  clk_i,
    input  logic                                  rst_ni,

    input  logic [NUM_SOURCES-1:0]                src_valid_i,
    output logic [NUM_SOURCES-1:0]                src_ready_o,
    input  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] src_addr_i,

    output logic                                  event_valid_o,
    input  logic                                  event_ready_i,
    output logic [ADDR_WIDTH-1:0]                 event_addr_o,
    output logic [SOURCE_WIDTH-1:0]               event_source_o,

    output logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] occupancy_o
);

    logic [NUM_SOURCES-1:0]                 fifo_valid;
    logic [NUM_SOURCES-1:0]                 fifo_ready;
    logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] fifo_data;
    logic [NUM_SOURCES-1:0]                 grant;
    logic                                   grant_valid;
    logic [SOURCE_WIDTH-1:0]                grant_index;
    logic                                   transfer;

    genvar source;
    generate
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin : gen_source_fifo
            aer_sync_fifo #(
                .DATA_WIDTH (ADDR_WIDTH),
                .DEPTH      (FIFO_DEPTH)
            ) u_fifo (
                .clk_i       (clk_i),
                .rst_ni      (rst_ni),
                .in_valid_i  (src_valid_i[source]),
                .in_ready_o  (src_ready_o[source]),
                .in_data_i   (src_addr_i[source]),
                .out_valid_o (fifo_valid[source]),
                .out_ready_i (fifo_ready[source]),
                .out_data_o  (fifo_data[source]),
                .occupancy_o (occupancy_o[source])
            );
        end
    endgenerate

    aer_round_robin_arbiter #(
        .NUM_SOURCES (NUM_SOURCES)
    ) u_arbiter (
        .clk_i         (clk_i),
        .rst_ni        (rst_ni),
        .request_i     (fifo_valid),
        .advance_i     (transfer),
        .grant_o       (grant),
        .grant_valid_o (grant_valid),
        .grant_index_o (grant_index)
    );

    always_comb begin
        fifo_ready     = '0;
        event_valid_o  = grant_valid;
        event_addr_o   = '0;
        event_source_o = grant_index;

        if (grant_valid) begin
            event_addr_o = fifo_data[grant_index];
            fifo_ready   = grant & {NUM_SOURCES{event_ready_i}};
        end
    end

    assign transfer = event_valid_o && event_ready_i;

endmodule
