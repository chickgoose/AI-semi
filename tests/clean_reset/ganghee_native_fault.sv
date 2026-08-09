// Test-only native-protocol fault source for fail-closed binding checks.
// +NATIVE_RESET_FAULT asserts valid during native reset; without it, valid is
// asserted after reset while no request exists, creating a native phantom.
module ganghee_native_fault (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  output logic        valid,
  output logic [3:0]  addr
);
  logic reset_fault;

  initial reset_fault = $test$plusargs("NATIVE_RESET_FAULT");

  always_comb begin
    addr = '0;
    if (reset_fault)
      valid = rst;
    else
      valid = !rst;
  end
endmodule
