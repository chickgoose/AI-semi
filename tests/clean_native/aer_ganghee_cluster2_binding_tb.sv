`timescale 1ns/1ps

module aer_ganghee_cluster2_binding_tb;
  localparam int ADDR_WIDTH = 16;
  logic clk = 1'b0;
  integer lane;
  integer acknowledgements = 0;
  integer retirements = 0;
  integer phantoms = 0;
  integer errors = 0;

  always #5 clk = ~clk;

  aer_bench_if #(
    .NUM_SOURCES(16),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(8)
  ) bench(clk);

  aer_ganghee_cluster2_binding #(
    .NUM_SOURCES(16),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(8),
    .FIFO_DEPTH(0)
  ) binding(bench);

  // Observe the normalized seam as soon as the repeated raw bitmap becomes
  // combinationally visible. The binding's posedge fail-closed assertion may
  // terminate the simulator before a clocked monitor can print this evidence.
  always @(negedge clk) begin
    if (bench.rst_n && (|bench.retire_valid) && !(|bench.source_ready))
      $display("GANGHEE_CLUSTER2_RAW_PHANTOM_VISIBLE");
  end

  always @(posedge clk) begin
    if (bench.rst_n) begin
      if (bench.source_valid[5] && bench.source_ready[5]) begin
        acknowledgements = acknowledgements + 1;
        bench.source_valid[5] <= 1'b0;
      end
      for (lane = 0; lane < 8; lane = lane + 1)
        if (bench.retire_valid[lane]) begin
          retirements = retirements + 1;
          if ((bench.retire_source[lane] !== 4'd5) ||
              (bench.retire_event[lane] !== ADDR_WIDTH'(5))) begin
            errors = errors + 1;
            $error("CLUSTER2_BINDING raw row/column address mismatch lane=%0d", lane);
          end
          if (bench.retire_event[lane] === bench.source_event[5]) begin
            errors = errors + 1;
            $error("CLUSTER2_BINDING reconstructed TB metadata");
          end
          if (!bench.source_ready[5]) begin
            phantoms = phantoms + 1;
            $display("GANGHEE_CLUSTER2_RAW_PHANTOM_VISIBLE retire=%0d", retirements);
          end
        end
    end
  end

  initial begin
    bench.rst_n = 1'b0;
    bench.source_valid = '0;
    // Canary: this must never become the delivered mandatory event.
    bench.source_event[5] = 16'hde55;
    bench.retire_ready = '1;
    repeat (2) @(posedge clk);
    @(negedge clk);
    bench.rst_n = 1'b1;
    bench.source_valid[5] = 1'b1;

    repeat (8) @(posedge clk);
    if (errors != 0)
      $fatal(1, "GANGHEE_CLUSTER2_BINDING_FAIL errors=%0d", errors);
`ifdef AER_CLUSTER2_MOCK_REPEAT
    // The production binding itself must already have rejected this fault.
    $fatal(1, "GANGHEE_CLUSTER2_REPEAT_FAULT unexpectedly survived retire=%0d phantom=%0d",
           retirements, phantoms);
`else
    if ((acknowledgements != 1) || (retirements != 1) || (phantoms != 0))
      $fatal(1, "GANGHEE_CLUSTER2_BINDING count mismatch ack=%0d retire=%0d phantom=%0d",
             acknowledgements, retirements, phantoms);
    $display("GANGHEE_CLUSTER2_BINDING_PASS ack=1 retire=1 phantom=0");
`endif
    $finish;
  end
endmodule
