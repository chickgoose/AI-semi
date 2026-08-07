`timescale 1ns/1ps

module a5_speculative_pregrant_core_tb;
  localparam int NUM_SOURCES = 4;
  localparam int ADDR_WIDTH = 8;
  localparam int SOURCE_WIDTH = 2;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [NUM_SOURCES-1:0] source_ready;
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [31:0] attempts;
  logic [31:0] hits;
  logic [31:0] misses;
  logic [31:0] confidence_fallbacks;
  logic [31:0] fairness_fallbacks;
  integer accepted [NUM_SOURCES];
  integer delivered [NUM_SOURCES];
  integer source_index;
  integer cycle;

  always #5 clk = ~clk;

  a5_speculative_pregrant_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .MAX_PREDICT_STREAK(3)
  ) dut (
    .clk,
    .rst_n,
    .source_valid,
    .source_event,
    .source_ready,
    .retire_valid,
    .retire_ready,
    .retire_event,
    .retire_source,
    .prediction_attempts(attempts),
    .prediction_hits(hits),
    .prediction_misses(misses),
    .confidence_fallbacks,
    .fairness_fallbacks
  );

  always @(posedge clk) begin
    if (rst_n) begin
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1)
        if (source_valid[source_index] && source_ready[source_index])
          accepted[source_index] = accepted[source_index] + 1;
      if (retire_valid && retire_ready) begin
        if (retire_event !== source_event[retire_source])
          $fatal(1, "payload mismatch source=%0d", retire_source);
        delivered[retire_source] = delivered[retire_source] + 1;
      end
    end
  end

  initial begin
    rst_n = 1'b0;
    retire_ready = 1'b1;
    source_valid = '0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      source_event[source_index] = ADDR_WIDTH'(8'h40 + source_index);
      accepted[source_index] = 0;
      delivered[source_index] = 0;
    end
    repeat (3) @(negedge clk);
    rst_n = 1'b1;

    // Learn the 0->1->0 transition pattern.
    for (cycle = 0; cycle < 16; cycle = cycle + 1) begin
      @(negedge clk);
      source_valid = (cycle[0]) ? 4'b0001 : 4'b0010;
    end

    // Keep source 0 hot while source 3 is continuously pending.  The bounded
    // hit streak must eventually force deterministic fallback service.
    @(negedge clk);
    source_valid = 4'b1001;
    repeat (12) @(negedge clk);

    // Exercise an output stall; the completed payload must remain stable.
    retire_ready = 1'b0;
    repeat (3) @(negedge clk);
    retire_ready = 1'b1;
    @(negedge clk);
    source_valid = '0;
    repeat (4) @(negedge clk);

    if (hits == 0)
      $fatal(1, "predictor produced no hit on alternating pattern");
    if (attempts != hits + misses)
      $fatal(1, "attempt accounting mismatch");
    if (fairness_fallbacks == 0)
      $fatal(1, "bounded prediction streak never forced fallback");
    if (accepted[3] == 0)
      $fatal(1, "continuously pending source 3 was starved");
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1)
      if (accepted[source_index] != delivered[source_index])
        $fatal(1, "conservation mismatch source=%0d accepted=%0d delivered=%0d",
          source_index, accepted[source_index], delivered[source_index]);

    $display("A5_SPECULATIVE_PREGRANT_CORE_PASS attempts=%0d hits=%0d misses=%0d confidence_fallbacks=%0d fairness_fallbacks=%0d",
      attempts, hits, misses, confidence_fallbacks, fairness_fallbacks);
    $finish;
  end
endmodule
