`timescale 1ns/1ps

module a7_parallel_prefix_count #(
  parameter int NUM_SOURCES = 16,
  parameter int COUNT_WIDTH = $clog2(NUM_SOURCES + 1)
) (
  input  logic [NUM_SOURCES-1:0] request,
  output logic [COUNT_WIDTH-1:0] inclusive_count [NUM_SOURCES],
  output logic [COUNT_WIDTH-1:0] total_count
);
  wire [COUNT_WIDTH-1:0] stage0 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage1 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage2 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage3 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage4 [NUM_SOURCES];

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : input_stage
      assign stage0[source] = COUNT_WIDTH'(request[source]);
      if (source >= 1)
        assign stage1[source] = stage0[source] + stage0[source-1];
      else
        assign stage1[source] = stage0[source];
      if (source >= 2)
        assign stage2[source] = stage1[source] + stage1[source-2];
      else
        assign stage2[source] = stage1[source];
      if (source >= 4)
        assign stage3[source] = stage2[source] + stage2[source-4];
      else
        assign stage3[source] = stage2[source];
      if (source >= 8)
        assign stage4[source] = stage3[source] + stage3[source-8];
      else
        assign stage4[source] = stage3[source];
    end
  endgenerate

  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : output_stage
      assign inclusive_count[source] = stage4[source];
    end
  endgenerate

  assign total_count = stage4[NUM_SOURCES-1];

  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A7 prefix requires NUM_SOURCES >= 1");
    if (NUM_SOURCES > 16)
      $fatal(1, "A7 frozen prefix implementation supports NUM_SOURCES <= 16");
  end
endmodule
