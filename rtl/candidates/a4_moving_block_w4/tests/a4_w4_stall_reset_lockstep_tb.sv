`timescale 1ns/1ps

module a4_w4_stall_reset_lockstep_tb #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 32,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
);
  logic clk;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat;
  logic retire_ready;

  logic [NUM_SOURCES-1:0] base_ready;
  logic base_retire_valid;
  logic [ADDR_WIDTH-1:0] base_retire_event;
  logic [SOURCE_WIDTH-1:0] base_retire_source;
  logic [NUM_SOURCES-1:0] norm_ready;
  logic norm_retire_valid;
  logic [ADDR_WIDTH-1:0] norm_retire_event;
  logic [SOURCE_WIDTH-1:0] norm_retire_source;
  logic [NUM_SOURCES-1:0] shared_ready;
  logic shared_retire_valid;
  logic [ADDR_WIDTH-1:0] shared_retire_event;
  logic [SOURCE_WIDTH-1:0] shared_retire_source;
  logic [NUM_SOURCES-1:0] enable_ready;
  logic enable_retire_valid;
  logic [ADDR_WIDTH-1:0] enable_retire_event;
  logic [SOURCE_WIDTH-1:0] enable_retire_source;

  always_comb begin
    for (int source = 0; source < NUM_SOURCES; source++)
      source_event_flat[source*ADDR_WIDTH +: ADDR_WIDTH] = source_event[source];
  end

  a4_moving_block_tree #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .MAX_ADVANCE(2)) frozen (
      .clk, .rst_n, .source_valid, .source_ready(base_ready), .source_event,
      .retire_valid(base_retire_valid), .retire_ready,
      .retire_event(base_retire_event), .retire_source(base_retire_source));
  a4_w4_frozen_normalized #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH))
    normalized (
      .clk, .rst_n, .source_valid, .source_ready(norm_ready), .source_event_flat,
      .retire_valid(norm_retire_valid), .retire_ready,
      .retire_event(norm_retire_event), .retire_source(norm_retire_source));
  a4_w4_shared_clearance #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH))
    shared (
      .clk, .rst_n, .source_valid, .source_ready(shared_ready), .source_event_flat,
      .retire_valid(shared_retire_valid), .retire_ready,
      .retire_event(shared_retire_event), .retire_source(shared_retire_source));
  a4_w4_shared_clearance_local_enable #(.NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)) local_enable (
      .clk, .rst_n, .source_valid, .source_ready(enable_ready), .source_event_flat,
      .retire_valid(enable_retire_valid), .retire_ready,
      .retire_event(enable_retire_event), .retire_source(enable_retire_source));

  always #5 clk = ~clk;

  task automatic check_equal(input integer cycle);
    begin
      if ({norm_ready, norm_retire_valid, norm_retire_event, norm_retire_source} !==
          {base_ready, base_retire_valid, base_retire_event, base_retire_source})
        $fatal(1, "normalized mismatch cycle=%0d", cycle);
      if ({shared_ready, shared_retire_valid, shared_retire_event, shared_retire_source} !==
          {base_ready, base_retire_valid, base_retire_event, base_retire_source})
        $fatal(1, "STYLE1 mismatch cycle=%0d", cycle);
      if ({enable_ready, enable_retire_valid, enable_retire_event,
           enable_retire_source} !==
          {base_ready, base_retire_valid, base_retire_event, base_retire_source})
        $fatal(1, "STYLE2 mismatch cycle=%0d", cycle);
    end
  endtask

  initial begin
    string vector_path;
    integer vectors;
    integer scan_status;
    integer cycle;
    integer rst_value;
    integer sink_value;
    logic [NUM_SOURCES-1:0] valid_value;
    logic [ADDR_WIDTH-1:0] event_value [NUM_SOURCES];
    logic [NUM_SOURCES-1:0] expected_ready;
    integer expected_retire_valid;
    logic [SOURCE_WIDTH-1:0] expected_retire_source;
    logic [ADDR_WIDTH-1:0] expected_retire_event;

    clk = 1'b0;
    rst_n = 1'b0;
    source_valid = '0;
    retire_ready = 1'b0;
    for (int source = 0; source < NUM_SOURCES; source++)
      source_event[source] = '0;
    if (!$value$plusargs("VECTORS=%s", vector_path)) $fatal(1, "missing vectors");
    vectors = $fopen(vector_path, "r");
    if (vectors == 0) $fatal(1, "cannot open vectors: %s", vector_path);
    cycle = 0;
    while (!$feof(vectors)) begin
      scan_status = $fscanf(vectors, "%d %h %d", rst_value, valid_value, sink_value);
      if (scan_status != 3) break;
      for (int source = 0; source < NUM_SOURCES; source++) begin
        scan_status = $fscanf(vectors, "%h", event_value[source]);
        if (scan_status != 1) $fatal(1, "truncated vector cycle=%0d", cycle);
      end
      scan_status = $fscanf(vectors, "%h %d %h %h\n", expected_ready,
        expected_retire_valid, expected_retire_source, expected_retire_event);
      if (scan_status != 4) $fatal(1, "truncated expected cycle=%0d", cycle);

      @(negedge clk);
      rst_n = rst_value[0];
      source_valid = valid_value;
      retire_ready = sink_value[0];
      for (int source = 0; source < NUM_SOURCES; source++)
        source_event[source] = event_value[source];
      #1;
      check_equal(cycle);
      if (base_ready !== expected_ready)
        $fatal(1, "model ready mismatch cycle=%0d", cycle);
      if (base_retire_valid !== expected_retire_valid[0])
        $fatal(1, "model retire_valid mismatch cycle=%0d", cycle);
      if (base_retire_valid &&
          (base_retire_source !== expected_retire_source ||
           base_retire_event !== expected_retire_event))
        $fatal(1, "model retire item mismatch cycle=%0d", cycle);

      @(posedge clk);
      #1;
      cycle = cycle + 1;
    end
    $fclose(vectors);
    if (base_retire_valid || source_valid != '0)
      $fatal(1, "vector ended before externally visible drain");
    $display("A4_W4_STALL_RESET_PASS n=%0d cycles=%0d", NUM_SOURCES, cycle);
    $finish;
  end
endmodule
