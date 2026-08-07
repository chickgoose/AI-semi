`timescale 1ns/1ps

module a2_phase3_packed_equiv_tb #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
);
  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event_array [NUM_SOURCES];
  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_packed;
  logic retire_ready;
  logic [63:0] lfsr;
  logic [NUM_SOURCES-1:0] array_ready;
  logic [NUM_SOURCES-1:0] packed_ready;
  logic array_retire_valid;
  logic packed_retire_valid;
  logic [ADDR_WIDTH-1:0] array_retire_event;
  logic [ADDR_WIDTH-1:0] packed_retire_event;
  logic [SOURCE_WIDTH-1:0] array_retire_source;
  logic [SOURCE_WIDTH-1:0] packed_retire_source;
  integer cycle;
  integer source;
  integer errors;

  always #5 clk = ~clk;

  a2_phase2_selected_core #(
    .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)
  ) array_core (
    .clk_i(clk), .rst_ni(rst_n), .source_valid_i(source_valid),
    .source_ready_o(array_ready), .source_event_i(source_event_array),
    .retire_valid_o(array_retire_valid), .retire_ready_i(retire_ready),
    .retire_event_o(array_retire_event),
    .retire_source_o(array_retire_source)
  );

  a2_phase3_selected_packed_core #(
    .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)
  ) packed_core (
    .clk_i(clk), .rst_ni(rst_n), .source_valid_i(source_valid),
    .source_ready_o(packed_ready), .source_event_i(source_event_packed),
    .retire_valid_o(packed_retire_valid), .retire_ready_i(retire_ready),
    .retire_event_o(packed_retire_event),
    .retire_source_o(packed_retire_source)
  );

  task automatic compare_outputs;
    begin
      if ((array_ready !== packed_ready) ||
          (array_retire_valid !== packed_retire_valid) ||
          (array_retire_event !== packed_retire_event) ||
          (array_retire_source !== packed_retire_source)) begin
        $error("A2_PHASE3_PACKED_MISMATCH cycle=%0d", cycle);
        errors = errors + 1;
      end
    end
  endtask

  initial begin
    rst_n = 1'b0;
    source_valid = '0;
    source_event_packed = '0;
    retire_ready = 1'b1;
    lfsr = 64'hd1b54a32d192ed03;
    errors = 0;
    cycle = 0;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      source_event_array[source] = '0;
    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    for (cycle = 0; cycle < 768; cycle = cycle + 1) begin
      @(negedge clk);
      lfsr = {lfsr[62:0], lfsr[63] ^ lfsr[62] ^ lfsr[60] ^ lfsr[59]};
      source_valid = '0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if ((cycle % 17) < 5)
          source_valid[source] = lfsr[source % 64] &&
                                 (((source + cycle) % 3) == 0);
        source_event_array[source] = ADDR_WIDTH'((source << 10) | cycle);
        source_event_packed[source*ADDR_WIDTH +: ADDR_WIDTH] =
          ADDR_WIDTH'((source << 10) | cycle);
      end
      retire_ready = lfsr[0] | lfsr[1];
      #1;
      compare_outputs();
      @(posedge clk);
      #1;
      compare_outputs();
    end
    if (errors == 0) begin
      $display("A2_PHASE3_PACKED_EQUIV_PASS n=%0d cycles=%0d", NUM_SOURCES,
               cycle);
      $finish;
    end
    $fatal(1, "A2_PHASE3_PACKED_EQUIV_FAIL errors=%0d", errors);
  end
endmodule
