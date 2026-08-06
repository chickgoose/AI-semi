// Protocol-only test fixture.  This is not Ganghee RTL.  It deliberately
// samples req on the active edge and registers valid/addr so a request left
// high through the following sampling edge produces a duplicate result.
`timescale 1ns/1ps

module ganghee_native_protocol_mock (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  output logic        valid,
  output logic [3:0]  addr
);
  integer source;
  logic sampled_valid;
  logic [3:0] sampled_addr;
  logic found;

  always_comb begin
    sampled_valid = 1'b0;
    sampled_addr = '0;
    found = 1'b0;
    if (!rst) begin
      for (source = 0; source < 16; source = source + 1) begin
        if (!found && req[source]) begin
          sampled_valid = 1'b1;
          sampled_addr = 4'(source);
          found = 1'b1;
        end
      end
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      valid <= 1'b0;
      addr <= '0;
    end else begin
      valid <= sampled_valid;
      addr <= sampled_addr;
    end
  end
endmodule
