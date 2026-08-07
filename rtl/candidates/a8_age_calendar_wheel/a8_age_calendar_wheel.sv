`timescale 1ns/1ps

module a8_age_calendar_wheel #(
  parameter int NUM_SOURCES   = 16,
  parameter int ADDR_WIDTH    = 16,
  parameter int BUCKET_CYCLES = 4,
  parameter int EPOCH_COUNT   = 8,
  parameter int SOURCE_WIDTH  = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int EPOCH_WIDTH   = (EPOCH_COUNT <= 1) ? 1 : $clog2(EPOCH_COUNT)
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
  logic [NUM_SOURCES-1:0] tracked_unused;
  logic [EPOCH_WIDTH-1:0] epoch_unused;
  logic [SOURCE_WIDTH-1:0] selected_source;
  integer source_index;

  a8_age_calendar_wheel_arbiter #(
    .NUM_SOURCES(NUM_SOURCES),
    .BUCKET_CYCLES(BUCKET_CYCLES),
    .EPOCH_COUNT(EPOCH_COUNT),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .EPOCH_WIDTH(EPOCH_WIDTH)
  ) scheduler (
    .clk(clk),
    .rst_n(rst_n),
    .request(source_valid),
    .advance(1'b1),
    .grant(grant),
    .tracked_debug(tracked_unused),
    .epoch_debug(epoch_unused)
  );

  always_comb begin
    source_ready = grant;
    selected_source = 0;
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
        retire_source <= SOURCE_WIDTH'(selected_source);
      end
    end
  end
endmodule
