`timescale 1ns/1ps

// Optional transport adapter for evaluators that expose two independently
// stalled retire pins.  The scheduler itself still sees exactly one atomic
// bundle_ready.  Lane 1 cannot retire around lane 0; no link state feeds back
// into scheduler policy except the all-or-nothing capacity indication.
module a4_pcck2_ordered_link_adapter (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [15:0] source_valid,
  output logic [15:0] source_ready,
  output logic [1:0]  retire_valid,
  input  logic [1:0]  retire_ready,
  output logic [7:0]  retire_addr,
  output logic        drain_idle
);
  logic [1:0] scheduler_grant_count;
  logic [7:0] scheduler_grant_addr;
  logic scheduler_bundle_ready;
  logic scheduler_drain_idle;

  logic [1:0] queue_count_q;
  logic [3:0] queue_addr_q [0:1];
  logic [1:0] retire_count;
  logic [1:0] remaining_count;
  logic [1:0] free_after_retire;

  a4_paired_cortical_column_k2 scheduler (
    .clk(clk),
    .rst_n(rst_n),
    .source_valid(source_valid),
    .source_ready(source_ready),
    .grant_count(scheduler_grant_count),
    .grant_addr(scheduler_grant_addr),
    .bundle_ready(scheduler_bundle_ready),
    .drain_idle(scheduler_drain_idle)
  );

  always_comb begin
    retire_count = 2'd0;
    if ((queue_count_q != 0) && retire_ready[0]) begin
      retire_count = 2'd1;
      if ((queue_count_q == 2) && retire_ready[1])
        retire_count = 2'd2;
    end
    remaining_count = queue_count_q - retire_count;
    free_after_retire = 2'd2 - remaining_count;
    scheduler_bundle_ready = scheduler_grant_count <= free_after_retire;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      queue_count_q <= '0;
      queue_addr_q[0] <= '0;
      queue_addr_q[1] <= '0;
    end else begin
      case (retire_count)
        2'd0: begin
          queue_count_q <= queue_count_q;
          queue_addr_q[0] <= queue_addr_q[0];
          queue_addr_q[1] <= queue_addr_q[1];
        end
        2'd1: begin
          queue_count_q <= queue_count_q - 2'd1;
          queue_addr_q[0] <= queue_addr_q[1];
          queue_addr_q[1] <= '0;
        end
        default: begin
          queue_count_q <= '0;
          queue_addr_q[0] <= '0;
          queue_addr_q[1] <= '0;
        end
      endcase

      if (scheduler_bundle_ready && (scheduler_grant_count != 0)) begin
        case (remaining_count)
          2'd0: begin
            queue_addr_q[0] <= scheduler_grant_addr[3:0];
            if (scheduler_grant_count == 2)
              queue_addr_q[1] <= scheduler_grant_addr[7:4];
          end
          2'd1: queue_addr_q[1] <= scheduler_grant_addr[3:0];
          default: begin end
        endcase
        queue_count_q <= remaining_count + scheduler_grant_count;
      end
    end
  end

  assign retire_valid[0] = queue_count_q != 0;
  assign retire_valid[1] = queue_count_q == 2;
  assign retire_addr[3:0] = queue_addr_q[0];
  assign retire_addr[7:4] = queue_addr_q[1];
  assign drain_idle = scheduler_drain_idle && (queue_count_q == 0);

`ifndef SYNTHESIS
  logic [31:0] committed_count_q;
  logic [31:0] retired_count_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      committed_count_q <= '0;
      retired_count_q <= '0;
    end else begin
      committed_count_q <= committed_count_q +
                           (scheduler_bundle_ready ?
                            integer'(scheduler_grant_count) : 0);
      retired_count_q <= retired_count_q + integer'(retire_count);
      assert (queue_count_q <= 2);
      assert (!(retire_valid[1] && !retire_valid[0]));
      assert (retired_count_q <= committed_count_q);
      if (retire_valid[0] && !retire_ready[0])
        assert (retire_count == 0);
    end
  end
`endif
endmodule
