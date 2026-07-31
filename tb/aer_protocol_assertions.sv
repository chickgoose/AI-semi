module aer_protocol_assertions #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16
) (aer_if.monitor bus);
  default clocking cb @(posedge bus.clk); endclocking
  default disable iff (!bus.rst_n);

  ap_stalled_output_stable: assert property (
    bus.out_valid && !bus.out_ready
    |=> bus.out_valid && $stable(bus.out_addr) && $stable(bus.out_src)
  ) else $error("ASSERT output changed while stalled");

  ap_output_control_known: assert property (
    !$isunknown({bus.out_valid, bus.out_ready})
  ) else $error("ASSERT unknown output handshake control");

  ap_output_payload_known: assert property (
    bus.out_valid |-> !$isunknown({bus.out_addr, bus.out_src})
  ) else $error("ASSERT unknown output payload");

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : source_checks
      ap_input_control_known: assert property (
        !$isunknown({bus.in_valid[source], bus.in_ready[source]})
      ) else $error("ASSERT unknown input handshake control source=%0d", source);

      ap_input_payload_known: assert property (
        bus.in_valid[source] |-> !$isunknown(bus.in_addr[source])
      ) else $error("ASSERT unknown input payload source=%0d", source);
    end
  endgenerate
endmodule
