`timescale 1ns/1ps

package aer_k2_binding_pkg;
  // The common K2 seam is deliberately fixed.  Candidate policy remains
  // outside this package; only the atomic offer/transport contract is shared.
  localparam int K2_RETIRE_LANES = 2;
  localparam int K2_LINK_ENTRIES = 2;
  localparam int K2_COUNT_WIDTH  = 2;

  typedef enum logic [1:0] {
    K2_READY_ALWAYS      = 2'd0,
    K2_READY_UNIFORM     = 2'd1,
    K2_READY_INDEPENDENT = 2'd2
  } k2_ready_capability_e;

  // A2 and A3 make only the uniform-ready common-suite claim.  The charged
  // link is safe under other ready patterns, but that does not promote the
  // candidates into an optional independently-ready capability suite.
  localparam k2_ready_capability_e K2_COMMON_READY_CAPABILITY =
      K2_READY_UNIFORM;

  function automatic logic k2_count_is_legal(input logic [1:0] count);
    k2_count_is_legal = (count != 2'd3);
  endfunction

  function automatic logic k2_ready_is_uniform(input logic [1:0] ready);
    k2_ready_is_uniform = (ready[0] == ready[1]);
  endfunction

  // Direct, unbuffered atomic-offer readiness.  Count one uses lane 0;
  // count two commits only as a complete ordered bundle.
  function automatic logic k2_atomic_offer_ready(
      input logic [1:0] count,
      input logic [1:0] ready
  );
    case (count)
      2'd0: k2_atomic_offer_ready = 1'b1;
      2'd1: k2_atomic_offer_ready = ready[0];
      2'd2: k2_atomic_offer_ready = ready[0] && ready[1];
      default: k2_atomic_offer_ready = 1'b0;
    endcase
  endfunction
endpackage
