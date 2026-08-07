`timescale 1ns/1ps

module a7_radix4_segmented_prefix_count #(
  parameter int NUM_SOURCES = 16,
  parameter int COUNT_WIDTH = $clog2(NUM_SOURCES + 1),
  parameter int NUM_SEGMENTS = NUM_SOURCES / 4
) (
  input  logic [NUM_SOURCES-1:0] request,
  output logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] inclusive_count,
  output logic [COUNT_WIDTH-1:0] total_count
);
  localparam int LOCAL_WIDTH = 3;

  wire [NUM_SEGMENTS-1:0][1:0] pair01;
  wire [NUM_SEGMENTS-1:0][1:0] pair23;
  wire [NUM_SEGMENTS-1:0][LOCAL_WIDTH-1:0] local0;
  wire [NUM_SEGMENTS-1:0][LOCAL_WIDTH-1:0] local1;
  wire [NUM_SEGMENTS-1:0][LOCAL_WIDTH-1:0] local2;
  wire [NUM_SEGMENTS-1:0][LOCAL_WIDTH-1:0] local3;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group0;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group1;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group2;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group3;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group4;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group_inclusive;
  wire [NUM_SEGMENTS-1:0][COUNT_WIDTH-1:0] group_base;

  genvar segment;
  generate
    for (segment = 0; segment < NUM_SEGMENTS; segment = segment + 1) begin : local_scan
      assign pair01[segment] =
        2'(request[4*segment]) + 2'(request[4*segment+1]);
      assign pair23[segment] =
        2'(request[4*segment+2]) + 2'(request[4*segment+3]);
      assign local0[segment] = LOCAL_WIDTH'(request[4*segment]);
      assign local1[segment] = LOCAL_WIDTH'(pair01[segment]);
      assign local2[segment] =
        LOCAL_WIDTH'(pair01[segment]) + LOCAL_WIDTH'(request[4*segment+2]);
      assign local3[segment] =
        LOCAL_WIDTH'(pair01[segment]) + LOCAL_WIDTH'(pair23[segment]);
      assign group0[segment] = COUNT_WIDTH'(local3[segment]);

      if (segment >= 1)
        assign group1[segment] = group0[segment] + group0[segment-1];
      else
        assign group1[segment] = group0[segment];
      if (segment >= 2)
        assign group2[segment] = group1[segment] + group1[segment-2];
      else
        assign group2[segment] = group1[segment];
      if (segment >= 4)
        assign group3[segment] = group2[segment] + group2[segment-4];
      else
        assign group3[segment] = group2[segment];
      if (segment >= 8)
        assign group4[segment] = group3[segment] + group3[segment-8];
      else
        assign group4[segment] = group3[segment];

      if (NUM_SEGMENTS <= 4)
        assign group_inclusive[segment] = group2[segment];
      else if (NUM_SEGMENTS <= 8)
        assign group_inclusive[segment] = group3[segment];
      else
        assign group_inclusive[segment] = group4[segment];

      if (segment == 0)
        assign group_base[segment] = '0;
      else
        assign group_base[segment] = group_inclusive[segment-1];

      assign inclusive_count[4*segment] =
        group_base[segment] + COUNT_WIDTH'(local0[segment]);
      assign inclusive_count[4*segment+1] =
        group_base[segment] + COUNT_WIDTH'(local1[segment]);
      assign inclusive_count[4*segment+2] =
        group_base[segment] + COUNT_WIDTH'(local2[segment]);
      assign inclusive_count[4*segment+3] =
        group_base[segment] + COUNT_WIDTH'(local3[segment]);
    end
  endgenerate

  assign total_count = group_inclusive[NUM_SEGMENTS-1];

  initial begin
    if ((NUM_SOURCES < 4) || (NUM_SOURCES > 64) || ((NUM_SOURCES % 4) != 0))
      $fatal(1, "A7 radix-4 scan requires NUM_SOURCES=4..64 divisible by four");
  end
endmodule
