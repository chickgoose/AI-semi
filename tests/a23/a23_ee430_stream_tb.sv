`timescale 1ns/1ps

module a23_ee430_stream_tb;
  parameter int NUM_SOURCES = 4;
  parameter int ADDR_WIDTH = 16;
  parameter int STREAM_EVENTS = 64;
  parameter int TIMEOUT_CYCLES = 1000;
  localparam int SOURCE_WIDTH = aer_pkg::index_width(NUM_SOURCES);

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  logic [NUM_SOURCES-1:0] in_valid;
  logic [NUM_SOURCES-1:0] in_ready;
  logic [ADDR_WIDTH-1:0] in_addr [NUM_SOURCES];
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_addr;
  logic [SOURCE_WIDTH-1:0] out_src;

  integer cycle_count;
  integer accepted_count;
  integer emitted_count;
  integer previous_accept_cycle;
  integer previous_emit_cycle;
  integer first_emit_cycle;
  integer last_emit_cycle;
  logic [ADDR_WIDTH-1:0] expected_addr [STREAM_EVENTS];

  a23_ee430_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) dut (
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(in_valid),
    .in_ready(in_ready),
    .in_addr(in_addr),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_addr(out_addr),
    .out_src(out_src)
  );

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cycle_count = 0;
      accepted_count = 0;
      emitted_count = 0;
      previous_accept_cycle = -1;
      previous_emit_cycle = -1;
      first_emit_cycle = -1;
      last_emit_cycle = -1;
    end else begin
      cycle_count = cycle_count + 1;

      if (in_valid[0] && in_ready[0]) begin
        if (accepted_count >= STREAM_EVENTS) begin
          $fatal(1, "stream accepted an unexpected extra event");
        end
        if ((previous_accept_cycle >= 0) &&
            ((cycle_count - previous_accept_cycle) != 1)) begin
          $fatal(1, "input II is not one at event %0d", accepted_count);
        end
        expected_addr[accepted_count] = in_addr[0];
        previous_accept_cycle = cycle_count;
        accepted_count = accepted_count + 1;
      end

      if (out_valid && out_ready) begin
        if (emitted_count >= accepted_count) begin
          $fatal(1, "duplicate or unexpected stream output");
        end
        if ((out_src !== '0) ||
            (out_addr !== expected_addr[emitted_count])) begin
          $fatal(1, "stream reorder/source/payload error at event %0d",
                 emitted_count);
        end
        if ((previous_emit_cycle >= 0) &&
            ((cycle_count - previous_emit_cycle) != 1)) begin
          $fatal(1, "output II is not one at event %0d", emitted_count);
        end
        if (first_emit_cycle < 0) begin
          first_emit_cycle = cycle_count;
        end
        previous_emit_cycle = cycle_count;
        last_emit_cycle = cycle_count;
        emitted_count = emitted_count + 1;
      end
    end
  end

  initial begin : run_test
    integer source;
    integer sent;
    integer timeout;

    in_valid = '0;
    out_ready = 1'b1;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      in_addr[source] = '0;
    end

    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    sent = 0;
    in_valid[0] = 1'b1;
    in_addr[0] = ADDR_WIDTH'(16'h1000);
    while (sent < STREAM_EVENTS) begin
      @(posedge clk);
      if (in_ready[0]) begin
        sent = sent + 1;
      end
      @(negedge clk);
      if (sent < STREAM_EVENTS) begin
        in_addr[0] = ADDR_WIDTH'(16'h1000 + sent);
      end else begin
        in_valid[0] = 1'b0;
        in_addr[0] = '0;
      end
    end

    timeout = 0;
    while ((emitted_count < STREAM_EVENTS) &&
           (timeout < TIMEOUT_CYCLES)) begin
      @(negedge clk);
      timeout = timeout + 1;
    end
    if (timeout >= TIMEOUT_CYCLES) begin
      $fatal(1, "stream drain timeout");
    end
    if ((accepted_count != STREAM_EVENTS) ||
        (emitted_count != STREAM_EVENTS)) begin
      $fatal(1, "stream missing event accepted=%0d emitted=%0d",
             accepted_count, emitted_count);
    end
    if ((last_emit_cycle - first_emit_cycle + 1) != STREAM_EVENTS) begin
      $fatal(1, "stream steady throughput is not 1 event/cycle");
    end

    $display("A23_STREAM_PASS sources=%0d accepted=%0d emitted=%0d input_ii=1 output_ii=1 throughput=1.000000",
      NUM_SOURCES, accepted_count, emitted_count);
    $finish;
  end
endmodule
