`timescale 1ns/1ps

module a3_homeostatic_inhibition_tb;
  localparam int N = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int SOURCE_WIDTH = 4;
  localparam int BOUND = 35;

  logic clk = 1'b0;
  logic rst_n;
  logic [N-1:0] source_valid;
  logic [N-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [N];
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  integer service_count [N];
  integer last_service [N];
  integer max_gap [N];
  integer cycle_count;
  integer source_index;
  integer accepted_source;
  logic [ADDR_WIDTH-1:0] held_event;
  logic [SOURCE_WIDTH-1:0] held_source;

  always #5 clk = ~clk;

  a3_homeostatic_inhibition #(
    .NUM_SOURCES(N),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) dut (
    .clk(clk),
    .rst_n(rst_n),
    .source_valid(source_valid),
    .source_ready(source_ready),
    .source_event(source_event),
    .retire_valid(retire_valid),
    .retire_ready(retire_ready),
    .retire_event(retire_event),
    .retire_source(retire_source)
  );

  always @(posedge clk) begin
    if (rst_n) begin
      cycle_count = cycle_count + 1;
      accepted_source = -1;
      for (source_index = 0; source_index < N;
           source_index = source_index + 1) begin
        if (source_valid[source_index] && source_ready[source_index]) begin
          if (accepted_source >= 0)
            $fatal(1, "A3 accepted multiple sources in one cycle");
          accepted_source = source_index;
          service_count[source_index] = service_count[source_index] + 1;
          if (last_service[source_index] >= 0 &&
              (cycle_count-last_service[source_index]) > max_gap[source_index])
            max_gap[source_index] = cycle_count-last_service[source_index];
          last_service[source_index] = cycle_count;
        end
      end
      if (retire_valid && retire_ready &&
          (retire_event !== {{(ADDR_WIDTH-SOURCE_WIDTH){1'b0}}, retire_source}))
        $fatal(1, "A3 retire identity mismatch source=%0d event=%0h",
               retire_source, retire_event);
    end
  end

  task automatic apply_reset;
    begin
      rst_n = 1'b0;
      source_valid = '0;
      retire_ready = 1'b1;
      repeat (3) @(posedge clk);
      @(negedge clk);
      rst_n = 1'b1;
      @(posedge clk);
    end
  endtask

  initial begin
    for (source_index = 0; source_index < N;
         source_index = source_index + 1) begin
      source_event[source_index] = ADDR_WIDTH'(source_index);
      service_count[source_index] = 0;
      last_service[source_index] = -1;
      max_gap[source_index] = 0;
    end
    cycle_count = 0;

    apply_reset();

    // Permanent fan-in must enter the high-activity homeostatic state and
    // service every source inside the analytical opportunity bound.
    @(negedge clk);
    source_valid = '1;
    repeat (160) @(posedge clk);
    #1;
    if (dut.homeostasis != 15)
      $fatal(1, "A3 homeostasis failed to saturate under fan-in: %0d",
             dut.homeostasis);
    for (source_index = 0; source_index < N;
         source_index = source_index + 1) begin
      if (service_count[source_index] == 0)
        $fatal(1, "A3 starved persistent source %0d", source_index);
      if (max_gap[source_index] > BOUND)
        $fatal(1, "A3 bound exceeded source=%0d gap=%0d bound=%0d",
               source_index, max_gap[source_index], BOUND);
    end

    // The output register, not the binding, must hold payload during a stall.
    @(negedge clk);
    source_valid = '0;
    @(negedge clk);
    source_valid = 16'h0080;
    #1;
    if (!source_ready[7])
      $fatal(1, "A3 did not expose ready for isolated source 7 ready=%0h selected=%0d valid=%0h active=%0d phase=%0d slot=%0b",
             source_ready, dut.selected_source, source_valid, dut.active_count,
             dut.phase, dut.output_slot_available);
    @(posedge clk);
    @(negedge clk);
    source_valid[7] = 1'b0;
    retire_ready = 1'b0;
    #1;
    held_event = retire_event;
    held_source = retire_source;
    repeat (4) begin
      @(posedge clk);
      #1;
      if (!retire_valid || retire_event !== held_event ||
          retire_source !== held_source || (|source_ready))
        $fatal(1, "A3 output changed or accepted input while stalled valid=%0b event=%0h/%0h source=%0d/%0d ready=%0h",
               retire_valid, retire_event, held_event, retire_source,
               held_source, source_ready);
    end
    retire_ready = 1'b1;
    repeat (3) @(posedge clk);

    $display("A3_HOMEOSTATIC_RTL_PASS bound=%0d homeostasis=%0d", BOUND,
             dut.homeostasis);
    $finish;
  end
endmodule
