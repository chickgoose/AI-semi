`timescale 1ns/1ps

module a9_distributed_token_fabric_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 4;
  localparam int SOURCE_WIDTH = 4;
  localparam int MAX_ACCEPTED = 256;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [RETIRE_LANES-1:0] retire_valid;
  logic [RETIRE_LANES-1:0] retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] retire_source [RETIRE_LANES];

  logic [ADDR_WIDTH-1:0] expected [NUM_SOURCES][MAX_ACCEPTED];
  integer expected_head [NUM_SOURCES];
  integer expected_tail [NUM_SOURCES];
  integer accepted_count;
  integer delivered_count;
  integer source_index;
  integer lane_index;
  integer stimulus_sequence;
  integer decoded_source;
  logic stalled_last [RETIRE_LANES];
  logic [ADDR_WIDTH-1:0] stalled_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] stalled_source [RETIRE_LANES];

  a9_distributed_token_fabric #(
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
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        expected_head[source_index] = 0;
        expected_tail[source_index] = 0;
      end
      for (lane_index = 0; lane_index < RETIRE_LANES;
           lane_index = lane_index + 1)
        stalled_last[lane_index] = 1'b0;
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
        if (stalled_last[lane_index] && retire_valid[lane_index] &&
            !retire_ready[lane_index] &&
            ((retire_event[lane_index] !== stalled_event[lane_index]) ||
             (retire_source[lane_index] !== stalled_source[lane_index])))
          $fatal(1, "fabric stalled output changed lane=%0d", lane_index);

        stalled_last[lane_index] =
          retire_valid[lane_index] && !retire_ready[lane_index];
        if (retire_valid[lane_index] && !retire_ready[lane_index]) begin
          stalled_event[lane_index] = retire_event[lane_index];
          stalled_source[lane_index] = retire_source[lane_index];
        end

        if (retire_valid[lane_index] && retire_ready[lane_index]) begin
          decoded_source = retire_source[lane_index];
          if ((decoded_source < 0) || (decoded_source >= NUM_SOURCES))
            $fatal(1, "fabric illegal retire source=%0d", decoded_source);
          if (expected_head[decoded_source] >= expected_tail[decoded_source])
            $fatal(1, "fabric phantom source=%0d", decoded_source);
          if (retire_event[lane_index] !==
              expected[decoded_source][expected_head[decoded_source]])
            $fatal(1, "fabric reorder/corrupt source=%0d expected=%h actual=%h",
                   decoded_source,
                   expected[decoded_source][expected_head[decoded_source]],
                   retire_event[lane_index]);
          expected_head[decoded_source] = expected_head[decoded_source] + 1;
          delivered_count = delivered_count + 1;
        end
      end
    end
  end

  task automatic reset_fabric;
    begin
      rst_n = 1'b0;
      source_valid = '0;
      retire_ready = '0;
      repeat (3) @(negedge clk);
      rst_n = 1'b1;
      retire_ready = '1;
      @(negedge clk);
      if (retire_valid != '0)
        $fatal(1, "fabric phantom after reset");
    end
  endtask

  task automatic offer_all(input integer sequence_id);
    integer s;
    begin
      @(negedge clk);
      for (s = 0; s < NUM_SOURCES; s = s + 1) begin
        source_event[s] = make_event(s, sequence_id);
        source_valid[s] = 1'b1;
      end
      wait (source_valid == '0);
    end
  endtask

  task automatic offer_one(
    input integer source_id,
    input integer sequence_id
  );
    begin
      @(negedge clk);
      source_event[source_id] = make_event(source_id, sequence_id);
      source_valid[source_id] = 1'b1;
      wait (!source_valid[source_id]);
    end
  endtask

  task automatic drain;
    integer timeout;
    begin
      timeout = 0;
      while ((delivered_count != accepted_count) && (timeout < 1000)) begin
        @(negedge clk);
        timeout = timeout + 1;
      end
      if (timeout >= 1000)
        $fatal(1, "fabric drain timeout accepted=%0d delivered=%0d valid=%b",
               accepted_count, delivered_count, retire_valid);
      repeat (4) @(negedge clk);
      if (retire_valid != '0)
        $fatal(1, "fabric late phantom after drain");
    end
  endtask

  initial begin
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1)
      source_event[source_index] = '0;

    reset_fabric();

    // Global fan-in plus repeated simultaneous rounds exercise every cell.
    $display("FABRIC_TEST fanin");
    offer_all(0);
    offer_all(1);
    offer_all(2);
    $display("FABRIC_TEST fanin accepted=%0d delivered=%0d", accepted_count,
             delivered_count);
    drain();

    // Sink stall fills distributed slots; reset must clear every stripe.
    $display("FABRIC_TEST reset");
    retire_ready = '0;
    offer_all(3);
    repeat (5) @(negedge clk);
    rst_n = 1'b0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    retire_ready = '1;
    source_valid = '0;
    repeat (8) @(negedge clk);
    if (retire_valid != '0)
      $fatal(1, "fabric pre-reset event escaped");

    // Counters reset with the fabric; stress one source at one event/cycle and
    // interleave a second source on the same fixed stripe.
    $display("FABRIC_TEST hotspot");
    for (stimulus_sequence = 0; stimulus_sequence < 24;
         stimulus_sequence = stimulus_sequence + 1) begin
      offer_one(5, stimulus_sequence);
    end
    for (stimulus_sequence = 0; stimulus_sequence < 12;
         stimulus_sequence = stimulus_sequence + 1) begin
      offer_one(4, stimulus_sequence);
      offer_one(7, stimulus_sequence);
    end
    drain();

    $display("A9_DISTRIBUTED_TOKEN_FABRIC_PASS accepted=%0d delivered=%0d",
             accepted_count, delivered_count);
    $finish;
  end

  initial begin
    #200000;
    $fatal(1, "fabric test global timeout");
  end
endmodule
