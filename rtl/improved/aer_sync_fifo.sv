module aer_sync_fifo #(
    parameter int unsigned DATA_WIDTH = 8,
    parameter int unsigned DEPTH      = 4,
    localparam int unsigned PTR_WIDTH   = (DEPTH > 1) ? $clog2(DEPTH) : 1,
    localparam int unsigned COUNT_WIDTH = $clog2(DEPTH + 1)
) (
    input  logic                   clk_i,
    input  logic                   rst_ni,

    input  logic                   in_valid_i,
    output logic                   in_ready_o,
    input  logic [DATA_WIDTH-1:0]  in_data_i,

    output logic                   out_valid_o,
    input  logic                   out_ready_i,
    output logic [DATA_WIDTH-1:0]  out_data_o,

    output logic [COUNT_WIDTH-1:0] occupancy_o
);

    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];
    logic [PTR_WIDTH-1:0] read_ptr_q;
    logic [PTR_WIDTH-1:0] write_ptr_q;
    logic [COUNT_WIDTH-1:0] count_q;
    logic push;
    logic pop;

    assign out_valid_o = (count_q != '0);
    assign out_data_o  = mem[read_ptr_q];

    // A full FIFO can accept a replacement in the same cycle that its head
    // is consumed. An empty FIFO intentionally has no combinational bypass.
    assign in_ready_o = (count_q < DEPTH) || (out_valid_o && out_ready_i);
    assign push       = in_valid_i && in_ready_o;
    assign pop        = out_valid_o && out_ready_i;
    assign occupancy_o = count_q;

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            read_ptr_q  <= '0;
            write_ptr_q <= '0;
            count_q     <= '0;
        end else begin
            if (push) begin
                mem[write_ptr_q] <= in_data_i;
                if (write_ptr_q == DEPTH-1)
                    write_ptr_q <= '0;
                else
                    write_ptr_q <= write_ptr_q + 1'b1;
            end

            if (pop) begin
                if (read_ptr_q == DEPTH-1)
                    read_ptr_q <= '0;
                else
                    read_ptr_q <= read_ptr_q + 1'b1;
            end

            case ({push, pop})
                2'b10: count_q <= count_q + 1'b1;
                2'b01: count_q <= count_q - 1'b1;
                default: count_q <= count_q;
            endcase
        end
    end

endmodule
