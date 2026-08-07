`timescale 1ns/1ps

// Calibration-only RR reference with the same single-lane output register.
module a8_rr_reference #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                    clk,
  input  logic                    rst_n,
  input  logic [NUM_SOURCES-1:0]  source_valid,
  input  logic [ADDR_WIDTH-1:0]   source_event [NUM_SOURCES],
  output logic [NUM_SOURCES-1:0]  source_ready,
  output logic                    retire_valid,
  output logic [ADDR_WIDTH-1:0]   retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source
);
  logic [NUM_SOURCES-1:0] grant;
  logic [SOURCE_WIDTH-1:0] selected_source;
  integer source_index;

  a8_rr_reference_arbiter #(
    .NUM_SOURCES(NUM_SOURCES), .SOURCE_WIDTH(SOURCE_WIDTH)
  ) scheduler (
    .clk(clk), .rst_n(rst_n), .request(source_valid), .advance(1'b1),
    .grant(grant)
  );

  always_comb begin
    source_ready = grant;
    selected_source = '0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1)
      if (grant[source_index])
        selected_source = SOURCE_WIDTH'(source_index);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      retire_valid <= 1'b0;
      retire_event <= '0;
      retire_source <= '0;
    end else begin
      retire_valid <= |grant;
      if (|grant) begin
        retire_event <= source_event[selected_source];
        retire_source <= selected_source;
      end
    end
  end
endmodule
