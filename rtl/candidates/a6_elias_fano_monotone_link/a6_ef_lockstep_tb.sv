`timescale 1ns/1ps

module a6_ef_lockstep_tb;
  localparam int NUM_SOURCES = 16;
  localparam int MAX_BATCH = 16;
  localparam int ADDRESS_WIDTH = 4;
  localparam int COUNT_WIDTH = 5;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic batch_valid;
  logic batch_ready;
  logic [COUNT_WIDTH-1:0] batch_count;
  logic [MAX_BATCH*ADDRESS_WIDTH-1:0] batch_sources;
  logic [1:0] link_count;
  logic [1:0] link_data;
  logic link_ready;
  logic event_valid;
  logic event_ready;
  logic [ADDRESS_WIDTH-1:0] event_address;
  logic encode_error;
  logic decode_error;
  logic encoded_ef;

  integer expected [0:4095];
  integer expected_head;
  integer expected_tail;
  integer delivered;
  integer seed;
  integer test_index;
  integer source;
  integer count;
  integer mask;
  integer timeout;

  always #1 clk = ~clk;

  a6_ef_batch_encoder encoder (
    .clk(clk), .rst_n(rst_n),
    .batch_valid(batch_valid), .batch_ready(batch_ready),
    .batch_count(batch_count), .batch_sources(batch_sources),
    .link_count(link_count), .link_data(link_data), .link_ready(link_ready),
    .encode_error(encode_error), .encoded_ef_observe(encoded_ef)
  );

  a6_ef_batch_decoder decoder (
    .clk(clk), .rst_n(rst_n),
    .link_count(link_count), .link_data(link_data), .link_ready(link_ready),
    .event_valid(event_valid), .event_ready(event_ready),
    .event_address(event_address), .decode_error(decode_error)
  );

  task automatic send_mask(input integer requested_mask);
    integer local_count;
    integer local_source;
    integer local_index;
    begin
      local_count = 0;
      batch_sources = '0;
      for (local_source = 0; local_source < NUM_SOURCES;
           local_source = local_source + 1)
        if (requested_mask[local_source]) begin
          batch_sources[local_count*ADDRESS_WIDTH +: ADDRESS_WIDTH] =
            local_source[ADDRESS_WIDTH-1:0];
          local_count = local_count + 1;
        end
      batch_count = local_count[COUNT_WIDTH-1:0];
      batch_valid = 1'b1;
      while (!batch_ready)
        @(posedge clk);
      @(posedge clk);
      for (local_index = 0; local_index < local_count;
           local_index = local_index + 1) begin
        expected[expected_tail] =
          batch_sources[local_index*ADDRESS_WIDTH +: ADDRESS_WIDTH];
        expected_tail = expected_tail + 1;
      end
      @(negedge clk);
      batch_valid = 1'b0;
      batch_count = '0;
      batch_sources = '0;
    end
  endtask

  always @(negedge clk) begin
    if (!rst_n)
      event_ready <= 1'b0;
    else begin
      seed = (seed * 1103515245 + 12345) & 32'h7fffffff;
      event_ready <= (seed[3:0] != 4'h0);
    end
  end

  always @(posedge clk) begin
    if (rst_n && event_valid && event_ready) begin
      if (expected_head >= expected_tail)
        $fatal(1, "A6 EF phantom event %0d", event_address);
      if (event_address !== expected[expected_head][ADDRESS_WIDTH-1:0])
        $fatal(1, "A6 EF mismatch index=%0d expected=%0d actual=%0d",
               expected_head, expected[expected_head], event_address);
      expected_head = expected_head + 1;
      delivered = delivered + 1;
    end
    if (rst_n && (encode_error || decode_error))
      $fatal(1, "A6 EF unexpected codec error encode=%0d decode=%0d",
             encode_error, decode_error);
  end

  initial begin
    batch_valid = 1'b0;
    batch_count = '0;
    batch_sources = '0;
    event_ready = 1'b0;
    expected_head = 0;
    expected_tail = 0;
    delivered = 0;
    seed = 32'h6003;

    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    send_mask(16'h8000); // raw singleton escape
    send_mask(16'h8421); // raw sparse batch
    send_mask(16'h00ff); // Elias--Fano, low-width one
    send_mask(16'hffff); // Elias--Fano, low-width zero

    for (test_index = 0; test_index < 80; test_index = test_index + 1) begin
      seed = (seed * 1103515245 + 12345) & 32'h7fffffff;
      mask = seed[15:0];
      if (mask == 0)
        mask = 1 << (test_index % NUM_SOURCES);
      send_mask(mask);
    end

    timeout = 0;
    while ((expected_head != expected_tail) && (timeout < 20000)) begin
      @(posedge clk);
      timeout = timeout + 1;
    end
    if (expected_head != expected_tail)
      $fatal(1, "A6 EF lockstep drain timeout pending=%0d",
             expected_tail - expected_head);

    // Reset in the middle of a full compressed frame.  Reset defines link
    // resynchronization and must discard the accepted pre-reset batch.
    send_mask(16'hffff);
    repeat (3) @(posedge clk);
    rst_n = 1'b0;
    expected_head = expected_tail;
    repeat (3) @(posedge clk);
    rst_n = 1'b1;
    @(negedge clk);
    send_mask(16'h0f0f);

    timeout = 0;
    while ((expected_head != expected_tail) && (timeout < 2000)) begin
      @(posedge clk);
      timeout = timeout + 1;
    end
    if (expected_head != expected_tail)
      $fatal(1, "A6 EF post-reset drain timeout");
    repeat (8) @(posedge clk);
    if (event_valid)
      $fatal(1, "A6 EF late phantom after drain");
    $display("A6_EF_LOCKSTEP_PASS delivered=%0d", delivered);
    $finish;
  end
endmodule
