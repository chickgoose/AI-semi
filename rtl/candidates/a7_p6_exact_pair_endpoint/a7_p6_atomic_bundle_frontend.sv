`timescale 1ns/1ps

// Normalizes the frozen scheduler boundary onto the nonempty-record endpoint.
// There is one handshake for the whole ordered 0/1/2-grant bundle.  In
// particular, this module has no per-lane ready or per-lane commit signal.
module a7_p6_atomic_bundle_frontend (
  input  logic       bundle_valid_i,
  input  logic [1:0] grant_count_i,
  input  logic [3:0] grant_addr0_i,
  input  logic [3:0] grant_addr1_i,
  input  logic       endpoint_ready_i,
  input  logic       endpoint_protocol_error_i,
  output logic       bundle_ready_o,
  output logic       bundle_commit_o,
  output logic [1:0] policy_microsteps_o,
  output logic       bundle_protocol_error_o,
  output logic       endpoint_valid_o,
  output logic [1:0] endpoint_count_o,
  output logic [3:0] endpoint_addr0_o,
  output logic [3:0] endpoint_addr1_o
);
  logic bundle_shape_error;

  // A valid count-zero offer is a legal atomic no-op.  An invalid cycle has
  // no grants.  Count three is outside K2 and fails closed.
  assign bundle_shape_error = (grant_count_i == 2'd3) ||
                              (!bundle_valid_i &&
                               (grant_count_i != 2'd0));

  assign endpoint_valid_o = bundle_valid_i &&
                            ((grant_count_i == 2'd1) ||
                             (grant_count_i == 2'd2));
  assign endpoint_count_o = endpoint_valid_o ? grant_count_i : 2'd0;
  assign endpoint_addr0_o = grant_addr0_i;
  assign endpoint_addr1_o = grant_addr1_i;

  assign bundle_protocol_error_o = bundle_shape_error ||
                                   endpoint_protocol_error_i;
  assign bundle_ready_o = endpoint_ready_i && !bundle_shape_error &&
                          !endpoint_protocol_error_i;
  assign bundle_commit_o = bundle_valid_i && bundle_ready_o;

`ifdef A7_P6_MUTATE_PARTIAL_PAIR_COMMIT
  assign policy_microsteps_o = bundle_commit_o ?
                               ((grant_count_i == 2'd2) ? 2'd1 :
                                grant_count_i) : 2'd0;
`else
  assign policy_microsteps_o = bundle_commit_o ? grant_count_i : 2'd0;
`endif
endmodule
