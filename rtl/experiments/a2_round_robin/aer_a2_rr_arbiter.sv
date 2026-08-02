module aer_a2_rr_arbiter #(
  parameter int unsigned NUM_SOURCES = aer_pkg::DEFAULT_NUM_SOURCES,
  parameter int unsigned INDEX_WIDTH = aer_pkg::index_width(NUM_SOURCES)
) (
  input  logic                   clk_i,
  input  logic                   rst_ni,
  input  logic [NUM_SOURCES-1:0] req_i,
  input  logic                   advance_i,
  output logic [NUM_SOURCES-1:0] grant_onehot_o,
  output logic                   grant_valid_o,
  output logic [INDEX_WIDTH-1:0] grant_index_o
);
  // The only state added over the fixed-priority baseline. It identifies the
  // first source considered by the next arbitration decision.
  logic [INDEX_WIDTH-1:0] priority_q;

  integer source_index;

  always_comb begin
    grant_onehot_o = '0;
    grant_valid_o  = 1'b0;
    grant_index_o  = '0;

    // First scan from priority_q to the highest source, then wrap to zero.
    // This avoids a FIFO, lock register, and variable modulo datapath.
    for (source_index = 0;
         source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      if (!grant_valid_o &&
          (INDEX_WIDTH'(source_index) >= priority_q) &&
          req_i[source_index]) begin
        grant_onehot_o[source_index] = 1'b1;
        grant_valid_o                = 1'b1;
        grant_index_o                = INDEX_WIDTH'(source_index);
      end
    end
    for (source_index = 0;
         source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      if (!grant_valid_o &&
          (INDEX_WIDTH'(source_index) < priority_q) &&
          req_i[source_index]) begin
        grant_onehot_o[source_index] = 1'b1;
        grant_valid_o                = 1'b1;
        grant_index_o                = INDEX_WIDTH'(source_index);
      end
    end
  end

  // Rotate only when the transmitter actually accepts the selected event.
  // No lock is needed: while the transmitter is busy all source ready signals
  // are low, and producers retain valid/address until a handshake.
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      priority_q <= '0;
    end else if (advance_i && grant_valid_o) begin
      if (grant_index_o == INDEX_WIDTH'(NUM_SOURCES - 1)) begin
        priority_q <= '0;
      end else begin
        priority_q <= grant_index_o + 1'b1;
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES < 1) begin
      $fatal(1, "NUM_SOURCES must be at least one");
    end
  end
`endif
endmodule
