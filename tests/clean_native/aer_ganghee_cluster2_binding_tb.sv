`timescale 1ns/1ps

module aer_ganghee_cluster2_binding_tb #(
  parameter int REPEAT_EACH_RESULT = 0
);
  localparam int ADDR_WIDTH = 16;
  logic clk = 1'b0;
  integer lane;
  integer acknowledgements = 0;
  integer retirements = 0;
  integer delivered = 0;
  integer phantoms = 0;
  integer outstanding = 0;
  integer masked_raw_results = 0;
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
    .RETIRE_LANES(8)
  ) binding(bench);

  always @(posedge clk) begin
    if (bench.rst_n) begin
      if (binding.cluster2_valid0 || binding.cluster2_valid1) begin
        if (!binding.cluster2_req[5])
          masked_raw_results = masked_raw_results + 1;
        else begin
          errors = errors + 1;
          $error("CLUSTER2_BINDING raw result did not mask held req");
        end
      end
      if (bench.source_valid[5] && bench.source_ready[5]) begin
        acknowledgements = acknowledgements + 1;
        outstanding = outstanding + 1;
        bench.source_valid[5] <= 1'b0;
      end
      for (lane = 0; lane < 8; lane = lane + 1)
        if (bench.retire_valid[lane]) begin
          retirements = retirements + 1;
          if ((bench.retire_source[lane] !== 4'd5) ||
              (bench.retire_event[lane] !== ADDR_WIDTH'(5))) begin
            errors = errors + 1;
            $error("CLUSTER2_BINDING retirement is not raw row/column address lane=%0d", lane);
          end
          if (bench.retire_event[lane] === bench.source_event[5]) begin
            errors = errors + 1;
            $error("CLUSTER2_BINDING reconstructed metadata canary");
          end
          if (outstanding > 0) begin
            outstanding = outstanding - 1;
            delivered = delivered + 1;
          end else begin
            // Expected only for the fault mock: raw retirement is deliberately
            // not hidden by current request/acknowledgement bookkeeping.
            phantoms = phantoms + 1;
          end
        end
    end
  end

  initial begin
    bench.rst_n = 1'b0;
    bench.source_valid = '0;
    bench.source_event[5] = 16'hde55;
    bench.retire_ready = '1;
    repeat (2) @(posedge clk);
    @(negedge clk);
    bench.rst_n = 1'b1;
    bench.source_valid[5] = 1'b1;

    repeat (8) @(posedge clk);
    @(negedge clk);
    if ((acknowledgements != 1) || (delivered != 1) || (outstanding != 0)) begin
      errors = errors + 1;
      $error("CLUSTER2_BINDING held ack mismatch ack=%0d delivered=%0d outstanding=%0d",
             acknowledgements, delivered, outstanding);
    end
    if (REPEAT_EACH_RESULT == 0) begin
      if ((retirements != 1) || (phantoms != 0) ||
          (masked_raw_results != 1)) begin
        errors = errors + 1;
        $error("CLUSTER2_BINDING normal mismatch retire=%0d phantom=%0d masked=%0d",
               retirements, phantoms, masked_raw_results);
      end
      if (errors == 0)
        $display("GANGHEE_CLUSTER2_BINDING_HELD_ACK_PASS ack=1 retire=1 phantom=0");
    end else begin
      if ((retirements != 2) || (phantoms != 1) ||
          (masked_raw_results != 2)) begin
        errors = errors + 1;
        $error("CLUSTER2_BINDING repeat mismatch retire=%0d phantom=%0d masked=%0d",
               retirements, phantoms, masked_raw_results);
      end
      if (errors == 0)
        $display("GANGHEE_CLUSTER2_BINDING_PHANTOM_VISIBLE_PASS ack=1 retire=2 phantom=1");
    end
    if (errors != 0)
      $fatal(1, "GANGHEE_CLUSTER2_BINDING_FAIL errors=%0d", errors);
    $finish;
  end
endmodule
