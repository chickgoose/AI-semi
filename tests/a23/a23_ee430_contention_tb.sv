`timescale 1ns/1ps

module a23_ee430_contention_tb;
  parameter int NUM_SOURCES = 4;
  parameter int ADDR_WIDTH = 16;
  parameter int EVENTS_PER_SOURCE = 32;
  parameter int TIMEOUT_CYCLES = 2000;
  localparam int SOURCE_WIDTH = aer_pkg::index_width(NUM_SOURCES);
  localparam int TOTAL_EVENTS = NUM_SOURCES * EVENTS_PER_SOURCE;

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

  integer accept_ordinal;
  integer emitted_count;
  integer accepted_by_source [NUM_SOURCES];
  integer emitted_by_source [NUM_SOURCES];
  integer last_service_ordinal [NUM_SOURCES];
  integer max_service_gap;
  integer previous_emit_cycle;
  integer cycle_count;
  logic [ADDR_WIDTH-1:0] expected_addr [NUM_SOURCES][EVENTS_PER_SOURCE];

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

  function automatic logic [ADDR_WIDTH-1:0] make_event(
    input integer source,
    input integer event_index
  );
    make_event = ADDR_WIDTH'((source << (ADDR_WIDTH/2)) ^ event_index);
  endfunction

  always @(posedge clk or negedge rst_n) begin : monitor
    integer source;
    integer ready_count;
    integer service_gap;
    if (!rst_n) begin
      accept_ordinal = 0;
      emitted_count = 0;
      max_service_gap = 0;
      previous_emit_cycle = -1;
      cycle_count = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        accepted_by_source[source] = 0;
        emitted_by_source[source] = 0;
        last_service_ordinal[source] = 0;
      end
    end else begin
      cycle_count = cycle_count + 1;
      ready_count = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (in_valid[source] && in_ready[source]) begin
          ready_count = ready_count + 1;
          if (accepted_by_source[source] >= EVENTS_PER_SOURCE) begin
            $fatal(1, "source %0d accepted an extra event", source);
          end
          accept_ordinal = accept_ordinal + 1;
          service_gap = accept_ordinal - last_service_ordinal[source];
          if (service_gap > max_service_gap) begin
            max_service_gap = service_gap;
          end
          if (service_gap > NUM_SOURCES) begin
            $fatal(1,
              "source %0d service gap %0d exceeds bound %0d",
              source, service_gap, NUM_SOURCES);
          end
          last_service_ordinal[source] = accept_ordinal;
          expected_addr[source][accepted_by_source[source]] = in_addr[source];
          accepted_by_source[source] = accepted_by_source[source] + 1;
        end
      end
      if (ready_count > 1) begin
        $fatal(1, "multiple sources accepted on one edge");
      end

      if (out_valid && out_ready) begin
        if (out_src >= NUM_SOURCES) begin
          $fatal(1, "illegal output source %0d", out_src);
        end
        if (emitted_by_source[out_src] >= accepted_by_source[out_src]) begin
          $fatal(1, "duplicate or unexpected output for source %0d", out_src);
        end
        if (out_addr !== expected_addr[out_src][emitted_by_source[out_src]]) begin
          $fatal(1, "contention reorder/payload error for source %0d", out_src);
        end
        if ((previous_emit_cycle >= 0) &&
            ((cycle_count - previous_emit_cycle) != 1)) begin
          $fatal(1, "contention output II is not one");
        end
        previous_emit_cycle = cycle_count;
        emitted_by_source[out_src] = emitted_by_source[out_src] + 1;
        emitted_count = emitted_count + 1;
      end
    end
  end

  initial begin : run_test
    integer source;
    integer timeout;

    in_valid = '0;
    out_ready = 1'b1;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      in_addr[source] = '0;
    end

    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    in_valid = '1;

    while (accept_ordinal < TOTAL_EVENTS) begin
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        in_addr[source] = make_event(source, accepted_by_source[source]);
      end
      @(negedge clk);
    end
    in_valid = '0;

    timeout = 0;
    while ((emitted_count < TOTAL_EVENTS) &&
           (timeout < TIMEOUT_CYCLES)) begin
      @(negedge clk);
      timeout = timeout + 1;
    end
    if (timeout >= TIMEOUT_CYCLES) begin
      $fatal(1, "contention drain timeout");
    end

    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      if ((accepted_by_source[source] != EVENTS_PER_SOURCE) ||
          (emitted_by_source[source] != EVENTS_PER_SOURCE)) begin
        $fatal(1,
          "source %0d missing event accepted=%0d emitted=%0d expected=%0d",
          source, accepted_by_source[source], emitted_by_source[source],
          EVENTS_PER_SOURCE);
      end
    end
    if (max_service_gap > NUM_SOURCES) begin
      $fatal(1, "maximum service gap exceeded bound");
    end

    $display("A23_CONTENTION_PASS sources=%0d accepted=%0d emitted=%0d output_ii=1 max_service_gap=%0d bound=%0d",
      NUM_SOURCES, accept_ordinal, emitted_count,
      max_service_gap, NUM_SOURCES);
    $finish;
  end
endmodule
