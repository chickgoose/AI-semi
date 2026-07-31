`timescale 1ns/1ps

module aer_baseline_payload_tb;
  localparam int unsigned NUM_SOURCES = 4;
  localparam int unsigned ADDR_WIDTH = 16;
  localparam int unsigned SOURCE_WIDTH = 2;

  logic clk;
  logic rst_n;
  logic [NUM_SOURCES-1:0] in_valid;
  logic [NUM_SOURCES-1:0] in_ready;
  logic [ADDR_WIDTH-1:0] in_addr [NUM_SOURCES];
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_addr;
  logic [SOURCE_WIDTH-1:0] out_src;

  int unsigned consumed_count;
  logic [ADDR_WIDTH-1:0] consumed_addr [0:7];
  logic [SOURCE_WIDTH-1:0] consumed_src [0:7];

  aer_dut #(
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

  always #5 clk = ~clk;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      consumed_count <= 0;
    end else if (out_valid && out_ready) begin
      consumed_addr[consumed_count] <= out_addr;
      consumed_src[consumed_count] <= out_src;
      consumed_count <= consumed_count + 1;
    end
  end

  task automatic send_event(
    input int unsigned source,
    input logic [ADDR_WIDTH-1:0] address
  );
    int unsigned timeout;
    begin
      @(negedge clk);
      in_addr[source] = address;
      in_valid[source] = 1'b1;
      timeout = 0;
      do begin
        @(posedge clk);
        timeout++;
        if (timeout >= 30) begin
          $fatal(1, "timeout sending source %0d address %h", source, address);
        end
      end while (!in_ready[source]);
      @(negedge clk);
      in_valid[source] = 1'b0;
      in_addr[source] = '0;
    end
  endtask

  task automatic wait_for_output_count(input int unsigned expected_count);
    int unsigned timeout;
    begin
      timeout = 0;
      while ((consumed_count < expected_count) && (timeout < 80)) begin
        @(negedge clk);
        timeout++;
      end
      if (consumed_count != expected_count) begin
        $fatal(1, "expected %0d outputs, got %0d", expected_count, consumed_count);
      end
    end
  endtask

  initial begin
    clk = 1'b0;
    rst_n = 1'b0;
    in_valid = '0;
    out_ready = 1'b1;
    for (int source = 0; source < NUM_SOURCES; source++) begin
      in_addr[source] = '0;
    end

    repeat (2) @(negedge clk);
    rst_n = 1'b1;

    send_event(2, 16'hA201);

    fork
      send_event(3, 16'hB303);
      send_event(1, 16'hB101);
    join

    wait_for_output_count(3);
    if (consumed_src[0] != 2 || consumed_addr[0] != 16'hA201 ||
        consumed_src[1] != 1 || consumed_addr[1] != 16'hB101 ||
        consumed_src[2] != 3 || consumed_addr[2] != 16'hB303) begin
      $fatal(1, "payload or fixed-priority ordering mismatch");
    end

    // Fill the receiver while the consumer is blocked and verify that both
    // address and source sideband remain stable throughout the stall.
    @(negedge clk);
    out_ready = 1'b0;
    send_event(0, 16'hC001);
    while (!out_valid) begin
      @(negedge clk);
    end
    repeat (3) begin
      @(negedge clk);
      if (!out_valid || out_addr != 16'hC001 || out_src != 0) begin
        $fatal(1, "output changed during backpressure");
      end
    end
    out_ready = 1'b1;

    wait_for_output_count(4);
    if (consumed_src[3] != 0 || consumed_addr[3] != 16'hC001) begin
      $fatal(1, "stalled event was corrupted");
    end

    $display("PASS: baseline payload and source-sideband smoke test");
    $finish;
  end
endmodule
