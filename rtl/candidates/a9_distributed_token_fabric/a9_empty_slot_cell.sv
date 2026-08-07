`timescale 1ns/1ps

module a9_empty_slot_cell #(
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = 4
) (
  input  logic                    clk_i,
  input  logic                    rst_ni,

  input  logic                    local_valid_i,
  output logic                    local_ready_o,
  input  logic [ADDR_WIDTH-1:0]   local_event_i,
  input  logic [SOURCE_WIDTH-1:0] local_source_i,

  input  logic                    upstream_valid_i,
  output logic                    upstream_ready_o,
  input  logic [ADDR_WIDTH-1:0]   upstream_event_i,
  input  logic [SOURCE_WIDTH-1:0] upstream_source_i,

  output logic                    downstream_valid_o,
  input  logic                    downstream_ready_i,
  output logic [ADDR_WIDTH-1:0]   downstream_event_o,
  output logic [SOURCE_WIDTH-1:0] downstream_source_o,
  output logic [1:0]              transport_occupancy_o
);
  logic ingress_valid_q;
  logic [ADDR_WIDTH-1:0] ingress_event_q;
  logic [SOURCE_WIDTH-1:0] ingress_source_q;

  logic [1:0] fifo_count_q;
  logic [ADDR_WIDTH-1:0] fifo_event_q [0:1];
  logic [SOURCE_WIDTH-1:0] fifo_source_q [0:1];
  logic prefer_local_q;

  logic fifo_has_space;
  logic choose_upstream;
  logic choose_local;
  logic take_upstream;
  logic take_local;
  logic take_any;
  logic send_head;
  logic accept_local_input;
  logic [ADDR_WIDTH-1:0] selected_event;
  logic [SOURCE_WIDTH-1:0] selected_source;

  assign downstream_valid_o = (fifo_count_q != 0);
  assign downstream_event_o = fifo_event_q[0];
  assign downstream_source_o = fifo_source_q[0];
  assign transport_occupancy_o = fifo_count_q;
  assign send_head = downstream_valid_o && downstream_ready_i;

  // Conservative credit deliberately depends only on registered occupancy.
  // This keeps ready from becoming a combinational chain through the stripe.
  assign fifo_has_space = (fifo_count_q < 2);

  always_comb begin
    choose_upstream = 1'b0;
    choose_local = 1'b0;
    if (fifo_has_space) begin
      if (upstream_valid_i && ingress_valid_q) begin
        choose_local = prefer_local_q;
        choose_upstream = !prefer_local_q;
      end else if (upstream_valid_i) begin
        choose_upstream = 1'b1;
      end else if (ingress_valid_q) begin
        choose_local = 1'b1;
      end
    end
  end

  assign upstream_ready_o = fifo_has_space &&
                            (!ingress_valid_q || choose_upstream);
  assign take_upstream = upstream_valid_i && upstream_ready_o;
  assign take_local = ingress_valid_q && choose_local;
  assign take_any = take_upstream || take_local;

  always_comb begin
    if (take_local) begin
      selected_event = ingress_event_q;
      selected_source = ingress_source_q;
    end else begin
      selected_event = upstream_event_i;
      selected_source = upstream_source_i;
    end
  end

  // The source-local ingress entry may be consumed and replaced on one edge.
  assign local_ready_o = !ingress_valid_q || take_local;
  assign accept_local_input = local_valid_i && local_ready_o;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ingress_valid_q <= 1'b0;
      ingress_event_q <= '0;
      ingress_source_q <= '0;
      fifo_count_q <= 0;
      fifo_event_q[0] <= '0;
      fifo_event_q[1] <= '0;
      fifo_source_q[0] <= '0;
      fifo_source_q[1] <= '0;
      prefer_local_q <= 1'b0;
    end else begin
      case ({accept_local_input, take_local})
        2'b10, 2'b11: begin
          ingress_valid_q <= 1'b1;
          ingress_event_q <= local_event_i;
          ingress_source_q <= local_source_i;
        end
        2'b01: ingress_valid_q <= 1'b0;
        default: begin end
      endcase

      if (upstream_valid_i && ingress_valid_q && fifo_has_space && take_any)
        prefer_local_q <= !prefer_local_q;

      case ({send_head, take_any})
        2'b01: begin
          if (fifo_count_q == 0) begin
            fifo_event_q[0] <= selected_event;
            fifo_source_q[0] <= selected_source;
          end else begin
            fifo_event_q[1] <= selected_event;
            fifo_source_q[1] <= selected_source;
          end
          fifo_count_q <= fifo_count_q + 1'b1;
        end
        2'b10: begin
          if (fifo_count_q == 2) begin
            fifo_event_q[0] <= fifo_event_q[1];
            fifo_source_q[0] <= fifo_source_q[1];
          end
          fifo_count_q <= fifo_count_q - 1'b1;
        end
        2'b11: begin
          if (fifo_count_q == 1) begin
            fifo_event_q[0] <= selected_event;
            fifo_source_q[0] <= selected_source;
          end else begin
            // A full FIFO never advertises credit, so count==2 cannot take.
            fifo_event_q[0] <= fifo_event_q[1];
            fifo_source_q[0] <= fifo_source_q[1];
            fifo_event_q[1] <= selected_event;
            fifo_source_q[1] <= selected_source;
          end
        end
        default: begin end
      endcase
    end
  end

`ifndef SYNTHESIS
  always_ff @(posedge clk_i) begin
    if (rst_ni) begin
      assert (fifo_count_q <= 2)
        else $fatal(1, "A9_CELL occupancy overflow count=%0d", fifo_count_q);
      assert (!(take_upstream && take_local))
        else $fatal(1, "A9_CELL accepted two producers");
      assert (!send_head || (fifo_count_q != 0))
        else $fatal(1, "A9_CELL dequeue while empty");
      assert (!take_any || fifo_has_space)
        else $fatal(1, "A9_CELL enqueue without empty slot");
    end
  end
`endif
endmodule
