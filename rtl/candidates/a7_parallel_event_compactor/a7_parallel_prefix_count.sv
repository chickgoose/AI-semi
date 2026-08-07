`timescale 1ns/1ps

module a7_parallel_prefix_count #(
  parameter int NUM_SOURCES = 16,
  parameter int COUNT_WIDTH = $clog2(NUM_SOURCES + 1)
) (
  input  logic [NUM_SOURCES-1:0] request,
  output logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] inclusive_count,
  output logic [COUNT_WIDTH-1:0] total_count
);
  wire [COUNT_WIDTH-1:0] stage0 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage1 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage2 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage3 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage4 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage5 [NUM_SOURCES];
  wire [COUNT_WIDTH-1:0] stage6 [NUM_SOURCES];

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
      if (source >= 16)
        assign stage5[source] = stage4[source] + stage4[source-16];
      else
        assign stage5[source] = stage4[source];
      if (source >= 32)
        assign stage6[source] = stage5[source] + stage5[source-32];
      else
        assign stage6[source] = stage5[source];
    end
  endgenerate

  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : output_stage
      if (NUM_SOURCES <= 16)
        assign inclusive_count[source] = stage4[source];
      else if (NUM_SOURCES <= 32)
        assign inclusive_count[source] = stage5[source];
      else
        assign inclusive_count[source] = stage6[source];
    end
  endgenerate

  if (NUM_SOURCES <= 16)
    assign total_count = stage4[NUM_SOURCES-1];
  else if (NUM_SOURCES <= 32)
    assign total_count = stage5[NUM_SOURCES-1];
  else
    assign total_count = stage6[NUM_SOURCES-1];

  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A7 prefix requires NUM_SOURCES >= 1");
    if (NUM_SOURCES > 64)
      $fatal(1, "A7 scaling prefix implementation supports NUM_SOURCES <= 64");
  end
endmodule
