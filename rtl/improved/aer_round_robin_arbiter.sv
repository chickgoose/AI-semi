module aer_round_robin_arbiter #(
    parameter int unsigned NUM_SOURCES = 4,
    localparam int unsigned INDEX_WIDTH = (NUM_SOURCES > 1) ? $clog2(NUM_SOURCES) : 1
) (
    input  logic                         clk_i,
    input  logic                         rst_ni,
    input  logic [NUM_SOURCES-1:0]       request_i,
    input  logic                         advance_i,
    output logic [NUM_SOURCES-1:0]       grant_o,
    output logic                         grant_valid_o,
    output logic [INDEX_WIDTH-1:0]       grant_index_o
);

    logic [INDEX_WIDTH-1:0] priority_q;
    logic                   locked_q;
    logic [INDEX_WIDTH-1:0] locked_index_q;

    integer offset;
    integer candidate;

    always_comb begin
        grant_o       = '0;
        grant_valid_o = 1'b0;
        grant_index_o = '0;
        candidate     = 0;

        if (locked_q) begin
            grant_o[locked_index_q] = 1'b1;
            grant_valid_o           = 1'b1;
            grant_index_o           = locked_index_q;
        end else begin
            for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
                candidate = int'(priority_q) + offset;
                if (candidate >= NUM_SOURCES)
                    candidate = candidate - NUM_SOURCES;

                if (!grant_valid_o && request_i[candidate]) begin
                    grant_o[candidate] = 1'b1;
                    grant_valid_o      = 1'b1;
                    grant_index_o      = candidate[INDEX_WIDTH-1:0];
                end
            end
        end
    end

    // Once a valid grant is backpressured, lock it so the selected payload
    // remains stable even if other requests arrive. Requests must remain set
    // until their grant advances; the FIFO-based wrapper guarantees this.
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            priority_q     <= '0;
            locked_q       <= 1'b0;
            locked_index_q <= '0;
        end else begin
            if (locked_q) begin
                if (advance_i) begin
                    locked_q <= 1'b0;
                    if (locked_index_q == INDEX_WIDTH'(NUM_SOURCES-1))
                        priority_q <= '0;
                    else
                        priority_q <= locked_index_q + 1'b1;
                end
            end else if (grant_valid_o) begin
                if (advance_i) begin
                    if (grant_index_o == INDEX_WIDTH'(NUM_SOURCES-1))
                        priority_q <= '0;
                    else
                        priority_q <= grant_index_o + 1'b1;
                end else begin
                    locked_q       <= 1'b1;
                    locked_index_q <= grant_index_o;
                end
            end
        end
    end

endmodule
