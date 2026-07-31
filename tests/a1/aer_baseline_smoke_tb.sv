`timescale 1ns/1ps

module aer_baseline_smoke_tb;
  localparam int unsigned NUM_SOURCES = 8;
  localparam int unsigned ADDR_WIDTH = 3;

  logic                   clk;
  logic                   rst_n;
  logic [NUM_SOURCES-1:0] source_req;
  logic [NUM_SOURCES-1:0] source_ack;
  logic                   event_valid;
  logic                   event_ready;
  logic [ADDR_WIDTH-1:0]  event_addr;

  int unsigned consumed_count;
  logic [ADDR_WIDTH-1:0] consumed_addr [0:7];

  aer_baseline_top #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .source_req_i(source_req),
    .source_ack_o(source_ack),
    .event_valid_o(event_valid),
    .event_ready_i(event_ready),
    .event_addr_o(event_addr)
  );

  always #5 clk = ~clk;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      consumed_count <= 0;
    end else if (event_valid && event_ready) begin
      consumed_addr[consumed_count] <= event_addr;
      consumed_count <= consumed_count + 1;
    end
  end

  task automatic wait_for_ack(input int unsigned source);
    int unsigned timeout;
    begin
      timeout = 0;
      while (!source_ack[source] && timeout < 20) begin
        @(negedge clk);
        timeout++;
      end
      if (!source_ack[source]) begin
        $fatal(1, "timeout waiting for source %0d acknowledge", source);
      end
      source_req[source] = 1'b0;
      @(negedge clk);
    end
  endtask

  initial begin
    clk            = 1'b0;
    rst_n          = 1'b0;
    source_req     = '0;
    event_ready    = 1'b1;

    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    // Single source request.
    source_req[3] = 1'b1;
    wait_for_ack(3);

    // Simultaneous requests must service source 1 before source 5.
    source_req[1] = 1'b1;
    source_req[5] = 1'b1;
    wait_for_ack(1);
    wait_for_ack(5);

    // The one-entry receiver may acknowledge one event while the sink is
    // blocked, but it must preserve that event until backpressure is removed.
    event_ready   = 1'b0;
    source_req[2] = 1'b1;
    wait_for_ack(2);
    repeat (3) @(negedge clk);
    if (!event_valid || event_addr != 2) begin
      $fatal(1, "receiver did not preserve source 2 during backpressure");
    end
    event_ready = 1'b1;

    repeat (2) @(negedge clk);
    if (consumed_count != 4) begin
      $fatal(1, "expected 4 consumed events, got %0d", consumed_count);
    end
    if (consumed_addr[0] != 3 || consumed_addr[1] != 1 ||
        consumed_addr[2] != 5 || consumed_addr[3] != 2) begin
      $fatal(1, "unexpected event order: %0d %0d %0d %0d",
             consumed_addr[0], consumed_addr[1],
             consumed_addr[2], consumed_addr[3]);
    end

    $display("PASS: baseline AER smoke test");
    $finish;
  end
endmodule
