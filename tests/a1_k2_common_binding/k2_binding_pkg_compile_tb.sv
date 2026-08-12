`timescale 1ns/1ps

module k2_binding_pkg_compile_tb;
  import aer_k2_binding_pkg::*;

  initial begin
    if (K2_RETIRE_LANES != 2 || K2_LINK_ENTRIES != 2 || K2_COUNT_WIDTH != 2)
      $fatal(1, "K2 package shape changed");
    if (K2_COMMON_READY_CAPABILITY != K2_READY_UNIFORM)
      $fatal(1, "K2 common ready capability is not uniform-ready");
    if (!k2_count_is_legal(2'd0) || !k2_count_is_legal(2'd1) ||
        !k2_count_is_legal(2'd2) || k2_count_is_legal(2'd3))
      $fatal(1, "K2 legal-count helper mismatch");
    if (!k2_ready_is_uniform(2'b00) || !k2_ready_is_uniform(2'b11) ||
        k2_ready_is_uniform(2'b01) || k2_ready_is_uniform(2'b10))
      $fatal(1, "K2 uniform-ready helper mismatch");
    if (!k2_atomic_offer_ready(2'd0, 2'b00) ||
        !k2_atomic_offer_ready(2'd1, 2'b01) ||
        k2_atomic_offer_ready(2'd1, 2'b10) ||
        !k2_atomic_offer_ready(2'd2, 2'b11) ||
        k2_atomic_offer_ready(2'd2, 2'b01) ||
        k2_atomic_offer_ready(2'd3, 2'b11))
      $fatal(1, "K2 atomic-ready helper mismatch");
    $display("K2_BINDING_PKG_COMPILE_PASS");
    $finish;
  end
endmodule
