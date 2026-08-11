`timescale 1ns/1ps

module a4_moving_block_lockstep_tb #(
  parameter int DUT_MAX_ADVANCE = 2
);
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 32;
  localparam int SOURCE_WIDTH = 4;

  logic clk;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;

  a4_moving_block_tree #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .MAX_ADVANCE(DUT_MAX_ADVANCE)
  ) dut (.*);

  always #5 clk = ~clk;

  initial begin
    string vector_path;
    integer vectors;
    integer scan_status;
    integer cycle;
    integer rst_value;
    integer sink_value;
    logic [NUM_SOURCES-1:0] valid_value;
    logic [ADDR_WIDTH-1:0] event_value [NUM_SOURCES];
    logic [NUM_SOURCES-1:0] expected_source_ready;
    integer expected_retire_valid;
    logic [SOURCE_WIDTH-1:0] expected_retire_source;
    logic [ADDR_WIDTH-1:0] expected_retire_event;

    clk = 1'b0;
    rst_n = 1'b0;
    source_valid = '0;
    retire_ready = 1'b0;
    for (int source = 0; source < NUM_SOURCES; source++)
      source_event[source] = '0;
    if (!$value$plusargs("VECTORS=%s", vector_path))
      $fatal(1, "missing +VECTORS path");
    vectors = $fopen(vector_path, "r");
    if (vectors == 0)
      $fatal(1, "cannot open vectors: %s", vector_path);

    cycle = 0;
    while (!$feof(vectors)) begin
      scan_status = $fscanf(vectors, "%d %h %d", rst_value, valid_value, sink_value);
      if (scan_status != 3)
        break;
      for (int source = 0; source < NUM_SOURCES; source++) begin
        scan_status = $fscanf(vectors, "%h", event_value[source]);
        if (scan_status != 1)
          $fatal(1, "truncated event vector at cycle %0d", cycle);
      end
      scan_status = $fscanf(vectors, "%h %d %h %h\n",
        expected_source_ready, expected_retire_valid,
        expected_retire_source, expected_retire_event);
      if (scan_status != 4)
        $fatal(1, "truncated expected vector at cycle %0d", cycle);

      @(negedge clk);
      rst_n = rst_value[0];
      source_valid = valid_value;
      retire_ready = sink_value[0];
      for (int source = 0; source < NUM_SOURCES; source++)
        source_event[source] = event_value[source];
      #1;
      if (source_ready !== expected_source_ready)
        $fatal(1, "source_ready mismatch cycle=%0d got=%h expected=%h",
          cycle, source_ready, expected_source_ready);
      if (retire_valid !== expected_retire_valid[0])
        $fatal(1, "retire_valid mismatch cycle=%0d got=%b expected=%b",
          cycle, retire_valid, expected_retire_valid[0]);
      if (expected_retire_valid != 0) begin
        if (retire_source !== expected_retire_source ||
            retire_event !== expected_retire_event)
          $fatal(1, "retire payload mismatch cycle=%0d got=%h/%h expected=%h/%h",
            cycle, retire_source, retire_event,
            expected_retire_source, expected_retire_event);
      end
      @(posedge clk);
      #1;
      cycle = cycle + 1;
    end
    $fclose(vectors);
    $display("A4_MOVING_BLOCK_LOCKSTEP_PASS cycles=%0d", cycle);
    $finish;
  end
endmodule
