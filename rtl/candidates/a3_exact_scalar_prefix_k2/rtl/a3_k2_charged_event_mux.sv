`timescale 1ns/1ps

// Candidate-charged common event selection.  The native owner schedules only
// source addresses; preserving the accepted common event identity therefore
// requires these two N:1 muxes inside the synthesis/PPA boundary.
module a3_k2_charged_event_mux #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH  = 16
) (
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
  input  logic [3:0]            source_addr0,
  input  logic [3:0]            source_addr1,
  output logic [ADDR_WIDTH-1:0] selected_event0,
  output logic [ADDR_WIDTH-1:0] selected_event1
);
  always @* begin
`ifdef A3_K2_MUT_EVENT_LANE_SWAP
    // Mutation: cross the ordered event lanes while source identity remains
    // unchanged.  The directed common scoreboard must reject this build.
    selected_event0 = source_event_flat[source_addr1*ADDR_WIDTH +: ADDR_WIDTH];
    selected_event1 = source_event_flat[source_addr0*ADDR_WIDTH +: ADDR_WIDTH];
`else
    selected_event0 = source_event_flat[source_addr0*ADDR_WIDTH +: ADDR_WIDTH];
    selected_event1 = source_event_flat[source_addr1*ADDR_WIDTH +: ADDR_WIDTH];
`endif
  end

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "A3_K2_EVENT_MUX requires NUM_SOURCES=16");
    if (ADDR_WIDTH <= 0)
      $fatal(1, "A3_K2_EVENT_MUX requires ADDR_WIDTH>0");
  end
`endif
endmodule
