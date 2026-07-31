module fixed_priority_arbiter #(
  parameter int unsigned NUM_SOURCES = aer_pkg::DEFAULT_NUM_SOURCES,
  parameter int unsigned INDEX_WIDTH = aer_pkg::index_width(NUM_SOURCES)
) (
  input  logic [NUM_SOURCES-1:0] req_i,
  output logic [NUM_SOURCES-1:0] grant_onehot_o,
  output logic                   grant_valid_o,
  output logic [INDEX_WIDTH-1:0] grant_index_o
);
  integer source_index;

  always_comb begin
    grant_onehot_o = '0;
    grant_valid_o  = 1'b0;
    grant_index_o  = '0;

    for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
      if (!grant_valid_o && req_i[source_index]) begin
        grant_onehot_o[source_index] = 1'b1;
        grant_valid_o                = 1'b1;
        grant_index_o                = source_index;
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
