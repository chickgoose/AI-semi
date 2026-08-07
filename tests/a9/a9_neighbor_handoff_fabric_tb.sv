`timescale 1ns/1ps

module a9_neighbor_handoff_fabric_tb;
  localparam int NUM_SOURCES = 4;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 4;
  localparam int SOURCE_WIDTH = 2;
  localparam int MAX_EVENTS = 128;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [RETIRE_LANES-1:0] retire_valid;
  logic [RETIRE_LANES-1:0] retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] retire_source [RETIRE_LANES];
  logic [ADDR_WIDTH-1:0] expected [NUM_SOURCES][MAX_EVENTS];
  integer expected_head [NUM_SOURCES];
  integer expected_tail [NUM_SOURCES];
  integer accepted_count;
  integer delivered_count;
  integer migrated_count;
  integer source_index;
  integer lane_index;
  integer sequence_index;
  integer decoded_source;
  integer timeout;

  a9_neighbor_handoff_fabric #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .source_valid_i(source_valid),
    .source_ready_o(source_ready),
    .source_event_i(source_event),
    .retire_valid_o(retire_valid),
    .retire_ready_i(retire_ready),
    .retire_event_o(retire_event),
    .retire_source_o(retire_source)
  );

  always #5 clk = ~clk;

  function automatic logic [ADDR_WIDTH-1:0] make_event(
    input integer source_id,
    input integer sequence_id
  );
    make_event = ADDR_WIDTH'((source_id << 8) | sequence_id);
  endfunction

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      accepted_count = 0;
      delivered_count = 0;
      migrated_count = 0;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        expected_head[source_index] = 0;
        expected_tail[source_index] = 0;
      end
    end else begin
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        if (source_valid[source_index] && source_ready[source_index]) begin
          expected[source_index][expected_tail[source_index]] =
            source_event[source_index];
          expected_tail[source_index] = expected_tail[source_index] + 1;
          accepted_count = accepted_count + 1;
          source_valid[source_index] = 1'b0;
        end
      end
      for (lane_index = 0; lane_index < RETIRE_LANES;
           lane_index = lane_index + 1) begin
        if (retire_valid[lane_index] && retire_ready[lane_index]) begin
          decoded_source = retire_source[lane_index];
          if (expected_head[decoded_source] >= expected_tail[decoded_source])
            $fatal(1, "HANDOFF phantom/duplicate source=%0d", decoded_source);
          if (retire_event[lane_index] !==
              expected[decoded_source][expected_head[decoded_source]])
            $fatal(1, "HANDOFF reorder source=%0d expected=%h actual=%h",
                   decoded_source,
                   expected[decoded_source][expected_head[decoded_source]],
                   retire_event[lane_index]);
          expected_head[decoded_source] = expected_head[decoded_source] + 1;
          delivered_count = delivered_count + 1;
          if (lane_index == (decoded_source ^ 1))
            migrated_count = migrated_count + 1;
          else if (lane_index != decoded_source)
            $fatal(1, "HANDOFF non-neighbor source=%0d lane=%0d",
                   decoded_source, lane_index);
        end
      end
    end
  end

  task automatic reset_dut;
    begin
      rst_n = 1'b0;
      source_valid = '0;
      retire_ready = '0;
      repeat (3) @(negedge clk);
      rst_n = 1'b1;
      @(negedge clk);
    end
  endtask

  task automatic offer_one(input integer source_id, input integer sequence_id);
    begin
      @(negedge clk);
      source_event[source_id] = make_event(source_id, sequence_id);
      source_valid[source_id] = 1'b1;
      wait (!source_valid[source_id]);
    end
  endtask

  task automatic offer_mask(input logic [NUM_SOURCES-1:0] mask,
                            input integer sequence_id);
    integer source_id;
    begin
      @(negedge clk);
      for (source_id = 0; source_id < NUM_SOURCES;
           source_id = source_id + 1) begin
        if (mask[source_id]) begin
          source_event[source_id] = make_event(source_id, sequence_id);
          source_valid[source_id] = 1'b1;
        end
      end
      wait ((source_valid & mask) == '0);
    end
  endtask

  task automatic drain;
    begin
      retire_ready = '1;
      timeout = 0;
      while ((accepted_count != delivered_count) && timeout < 500) begin
        @(negedge clk);
        timeout = timeout + 1;
      end
      if (accepted_count != delivered_count)
        $fatal(1, "HANDOFF drain timeout accepted=%0d delivered=%0d",
               accepted_count, delivered_count);
      repeat (3) @(negedge clk);
      if (retire_valid != '0)
        $fatal(1, "HANDOFF late phantom valid=%b", retire_valid);
    end
  endtask

  initial begin
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1)
      source_event[source_index] = '0;

    // Single-stripe hotspot: stalled lane 0 hands fresh heads only to lane 1.
    reset_dut();
    $display("HANDOFF_TEST single_stripe_hotspot");
    retire_ready = 4'b1110;
    for (sequence_index = 0; sequence_index < 8; sequence_index++)
      offer_one(0, sequence_index);
    drain();
    $display("HANDOFF hotspot migrated_count=%0d rtl_migrations=%0d",
             migrated_count, dut.debug_migrations_q);
    if (migrated_count == 0)
      $fatal(1, "HANDOFF expected hotspot migrations");

    // Once exposed under stall, a pinned head may not disappear sideways.
    reset_dut();
    $display("HANDOFF_TEST pinned_stability");
    retire_ready = 4'b0000;
    offer_one(0, 20);
    repeat (5) @(negedge clk);
    if (!retire_valid[0])
      $fatal(1, "HANDOFF expected stalled home head");
    retire_ready = 4'b0010;
    repeat (5) @(negedge clk);
    if (!retire_valid[0] || (delivered_count != 0))
      $fatal(1, "HANDOFF pinned head migrated after presentation");
    retire_ready = 4'b0001;
    drain();
    if (migrated_count != 0)
      $fatal(1, "HANDOFF pinned case unexpectedly migrated");

    // Moving hotspot and alternating paired stripes with rotating stalls.
    reset_dut();
    $display("HANDOFF_TEST moving_and_alternating");
    for (sequence_index = 0; sequence_index < 16; sequence_index++) begin
      retire_ready = (sequence_index[0]) ? 4'b0101 : 4'b1010;
      offer_one((sequence_index % NUM_SOURCES), 40 + sequence_index);
    end
    for (sequence_index = 0; sequence_index < 8; sequence_index++) begin
      retire_ready = (sequence_index[0]) ? 4'b1001 : 4'b0110;
      offer_mask(4'b0011, 70 + sequence_index);
    end
    drain();

    // All-stripe saturation: all four native stripes remain independently busy.
    reset_dut();
    $display("HANDOFF_TEST all_stripe_saturation");
    retire_ready = '1;
    for (sequence_index = 0; sequence_index < 16; sequence_index++) begin
      offer_mask(4'b1111, 90 + sequence_index);
    end
    drain();

    $display("A9_NEIGHBOR_HANDOFF_PASS accepted=%0d delivered=%0d migrations=%0d",
             accepted_count, delivered_count, migrated_count);

    // Reset clears pins and every underlying occupied token.
    retire_ready = '0;
    offer_mask(4'b1111, 120);
    repeat (3) @(negedge clk);
    rst_n = 1'b0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    source_valid = '0;
    retire_ready = '1;
    repeat (8) @(negedge clk);
    if (retire_valid != '0)
      $fatal(1, "HANDOFF post-reset phantom");

    $display("A9_NEIGHBOR_HANDOFF_RESET_PASS");
    $finish;
  end

  initial begin
    #300000;
    $fatal(1, "HANDOFF global timeout");
  end
endmodule
