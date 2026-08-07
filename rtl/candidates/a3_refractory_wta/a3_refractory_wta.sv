`timescale 1ns/1ps

module a3_refractory_wta #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                         clk,
  input  logic                         rst_n,
  input  logic [NUM_SOURCES-1:0]       source_valid,
  output logic [NUM_SOURCES-1:0]       source_ready,
  input  logic [ADDR_WIDTH-1:0]        source_event [NUM_SOURCES],
  output logic                         retire_valid,
  input  logic                         retire_ready,
  output logic [ADDR_WIDTH-1:0]        retire_event,
  output logic [SOURCE_WIDTH-1:0]      retire_source
);
  // The complete policy state: one prior winner and one global refractory bit.
  logic last_valid;
  logic [SOURCE_WIDTH-1:0] last_winner;
  logic refractory;

  logic [NUM_SOURCES-1:0] last_onehot;
  logic [NUM_SOURCES-1:0] alternative_request;
  logic [NUM_SOURCES-1:0] eligible_request;
  logic escape_active;
  logic output_slot_available;
  logic grant_valid;
  integer selected_source;
  integer source_index;

  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A3 refractory WTA requires NUM_SOURCES >= 1");
    if (NUM_SOURCES > (1 << SOURCE_WIDTH))
      $fatal(1, "A3 refractory WTA SOURCE_WIDTH is too small");
  end

  always_comb begin
    last_onehot = '0;
    if (last_valid && (integer'(last_winner) < NUM_SOURCES))
      last_onehot[integer'(last_winner)] = 1'b1;
    alternative_request = source_valid & ~last_onehot;
    escape_active = last_valid && refractory &&
                    source_valid[integer'(last_winner)] &&
                    (|alternative_request);
    eligible_request = escape_active ? alternative_request : source_valid;

    // Binary inputs have equal WTA magnitude.  This is deliberately a fixed
    // deterministic encoder; last_winner only excludes one refractory winner
    // and is not used as an RR scan origin.
    selected_source = -1;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      if ((selected_source < 0) && eligible_request[source_index])
        selected_source = source_index;
    end

    output_slot_available = !retire_valid || retire_ready;
    grant_valid = output_slot_available && (selected_source >= 0);
    source_ready = '0;
    if (grant_valid)
      source_ready[selected_source] = 1'b1;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      retire_valid <= 1'b0;
      retire_event <= '0;
      retire_source <= '0;
      last_valid <= 1'b0;
      last_winner <= '0;
      refractory <= 1'b0;
    end else begin
      if (output_slot_available) begin
        if (selected_source >= 0) begin
          retire_valid <= 1'b1;
          retire_event <= source_event[selected_source];
          retire_source <= SOURCE_WIDTH'(selected_source);
          last_valid <= 1'b1;
          last_winner <= SOURCE_WIDTH'(selected_source);
          refractory <= 1'b1;
        end else begin
          retire_valid <= 1'b0;
          // An idle opportunity is the only decay in this one-bit abstraction.
          refractory <= 1'b0;
        end
      end
    end
  end
endmodule
