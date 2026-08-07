`timescale 1ns/1ps

module a9_empty_slot_cell_tb;
  localparam int ADDR_WIDTH = 8;
  localparam int SOURCE_WIDTH = 3;

  logic clk = 1'b0;
  logic rst_n;
  logic local_valid;
  logic local_ready;
  logic [ADDR_WIDTH-1:0] local_event;
  logic [SOURCE_WIDTH-1:0] local_source;
  logic upstream_valid;
  logic upstream_ready;
  logic [ADDR_WIDTH-1:0] upstream_event;
  logic [SOURCE_WIDTH-1:0] upstream_source;
  logic downstream_valid;
  logic downstream_ready;
  logic [ADDR_WIDTH-1:0] downstream_event;
  logic [SOURCE_WIDTH-1:0] downstream_source;

  integer accepted_local;
  integer accepted_upstream;
  integer delivered;
  logic stalled_last_cycle;
  logic [ADDR_WIDTH-1:0] stalled_event;
  logic [SOURCE_WIDTH-1:0] stalled_source;

  a9_empty_slot_cell #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .local_valid_i(local_valid),
    .local_ready_o(local_ready),
    .local_event_i(local_event),
    .local_source_i(local_source),
    .upstream_valid_i(upstream_valid),
    .upstream_ready_o(upstream_ready),
    .upstream_event_i(upstream_event),
    .upstream_source_i(upstream_source),
    .downstream_valid_o(downstream_valid),
    .downstream_ready_i(downstream_ready),
    .downstream_event_o(downstream_event),
    .downstream_source_o(downstream_source)
  );

  always #5 clk = ~clk;

  initial begin
    #5000;
    $fatal(1, "cell test timeout local_ready=%0b upstream_ready=%0b count=%0d",
           local_ready, upstream_ready, dut.fifo_count_q);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      accepted_local <= 0;
      accepted_upstream <= 0;
      delivered <= 0;
      stalled_last_cycle <= 1'b0;
      stalled_event <= '0;
      stalled_source <= '0;
    end else begin
      if (local_valid && local_ready)
        accepted_local <= accepted_local + 1;
      if (upstream_valid && upstream_ready)
        accepted_upstream <= accepted_upstream + 1;
      if (downstream_valid && downstream_ready)
        delivered <= delivered + 1;

      if (stalled_last_cycle && downstream_valid && !downstream_ready) begin
        if ((downstream_event !== stalled_event) ||
            (downstream_source !== stalled_source))
          $fatal(1, "stalled output changed");
      end
      stalled_last_cycle <= downstream_valid && !downstream_ready;
      if (downstream_valid && !downstream_ready) begin
        stalled_event <= downstream_event;
        stalled_source <= downstream_source;
      end
    end
  end

  task automatic reset_cell;
    begin
      rst_n = 1'b0;
      local_valid = 1'b0;
      upstream_valid = 1'b0;
      downstream_ready = 1'b0;
      repeat (3) @(negedge clk);
      rst_n = 1'b1;
      @(negedge clk);
      if (downstream_valid)
        $fatal(1, "phantom output after reset");
    end
  endtask

  task automatic send_local(
    input logic [ADDR_WIDTH-1:0] event_value,
    input logic [SOURCE_WIDTH-1:0] source_value
  );
    integer accepted_before;
    begin
      accepted_before = accepted_local;
      local_event = event_value;
      local_source = source_value;
      local_valid = 1'b1;
      while (accepted_local == accepted_before)
        @(negedge clk);
      local_valid = 1'b0;
    end
  endtask

  task automatic send_upstream(
    input logic [ADDR_WIDTH-1:0] event_value,
    input logic [SOURCE_WIDTH-1:0] source_value
  );
    integer accepted_before;
    begin
      accepted_before = accepted_upstream;
      upstream_event = event_value;
      upstream_source = source_value;
      upstream_valid = 1'b1;
      while (accepted_upstream == accepted_before)
        @(negedge clk);
      upstream_valid = 1'b0;
    end
  endtask

  initial begin
    local_event = '0;
    local_source = '0;
    upstream_event = '0;
    upstream_source = '0;
    reset_cell();

    $display("CELL_TEST sparse");

    // Sparse local transport.
    downstream_ready = 1'b1;
    send_local(8'h11, 3'd1);
    wait (downstream_valid);
    if ((downstream_event != 8'h11) || (downstream_source != 3'd1))
      $fatal(1, "sparse local payload corrupt");
    @(posedge clk);
    @(negedge clk);

    // Fill transport while stalled and verify stable output.
    $display("CELL_TEST fill and stall");
    downstream_ready = 1'b0;
    send_upstream(8'h21, 3'd2);
    send_upstream(8'h22, 3'd2);
    repeat (4) @(negedge clk);
    downstream_ready = 1'b1;
    repeat (5) @(negedge clk);
    if (accepted_local + accepted_upstream != delivered)
      $fatal(1, "conservation mismatch before reset accepted=%0d delivered=%0d",
             accepted_local + accepted_upstream, delivered);

    // Reset from a nonempty state must discard old state and recover quietly.
    $display("CELL_TEST reset recovery");
    downstream_ready = 1'b0;
    send_local(8'h31, 3'd3);
    $display("CELL_TEST reset recovery local queued count=%0d", dut.fifo_count_q);
    send_upstream(8'h32, 3'd4);
    $display("CELL_TEST reset recovery upstream queued count=%0d", dut.fifo_count_q);
    repeat (2) @(negedge clk);
    rst_n = 1'b0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    downstream_ready = 1'b1;
    repeat (5) @(negedge clk);
    if (downstream_valid)
      $fatal(1, "pre-reset event escaped after reset");

    // Continuous contention: both producers must make progress.
    $display("CELL_TEST contention");
    local_event = 8'h40;
    local_source = 3'd5;
    upstream_event = 8'h80;
    upstream_source = 3'd6;
    local_valid = 1'b1;
    upstream_valid = 1'b1;
    repeat (24) begin
      @(negedge clk);
    end
    local_valid = 1'b0;
    upstream_valid = 1'b0;
    repeat (12) @(negedge clk);
    if ((accepted_local < 8) || (accepted_upstream < 8))
      $fatal(1, "contention made insufficient progress local=%0d upstream=%0d",
             accepted_local, accepted_upstream);
    if (accepted_local + accepted_upstream != delivered)
      $fatal(1, "final conservation mismatch accepted=%0d delivered=%0d",
             accepted_local + accepted_upstream, delivered);

    $display("A9_EMPTY_SLOT_CELL_PASS local=%0d upstream=%0d delivered=%0d",
             accepted_local, accepted_upstream, delivered);
    $finish;
  end
endmodule
