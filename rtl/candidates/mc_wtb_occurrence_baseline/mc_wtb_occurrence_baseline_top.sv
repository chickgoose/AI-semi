`timescale 1ns/1ps

module mc_wtb_occurrence_baseline_top #(
  parameter integer PAYLOAD_W = 102,
  parameter integer INGRESS_LANES = 6
) (
  input  logic                           clk_i,
  input  logic                           rst_i,
  input  logic                           link_enable_i,
  input  logic [INGRESS_LANES-1:0]       ingress_valid_i,
  input  logic [INGRESS_LANES*4-1:0]     ingress_source_i,
  input  logic [INGRESS_LANES*PAYLOAD_W-1:0] ingress_payload_i,
  output logic                           ingress_ready_o,
  output logic                           ingress_commit_o,
  output logic [1:0]                     accept_count_o,
  output logic [3:0]                     accept_source0_o,
  output logic [3:0]                     accept_source1_o,
  output logic [PAYLOAD_W-1:0]           accept_payload0_o,
  output logic [PAYLOAD_W-1:0]           accept_payload1_o,
  output logic [1:0]                     retire_count_o,
  output logic [3:0]                     retire_source0_o,
  output logic [3:0]                     retire_source1_o,
  output logic [PAYLOAD_W-1:0]           retire_payload0_o,
  output logic [PAYLOAD_W-1:0]           retire_payload1_o,
  output logic                           overflow_o,
  output logic                           protocol_error_o,
  output logic                           drain_idle_o
);
  localparam integer FIFO_DEPTH = 3;
  logic [1:0] count_q [0:15];
  logic [1:0] head_q [0:15];
  logic [1:0] tail_q [0:15];
  logic [PAYLOAD_W-1:0] payload_mem [0:15][0:FIFO_DEPTH-1];

  logic [15:0] req;
  logic [1:0] scheduler_count;
  logic [3:0] scheduler_source0;
  logic [3:0] scheduler_source1;
  logic [15:0] scheduler_bitmap;
  logic scheduler_idle;
  logic scheduler_ready;
  logic scheduler_commit;
  logic [2:0] enqueue_count [0:15];
  logic [2:0] effective_capacity [0:15];
  logic any_ingress;
  logic all_empty;
  logic shape_error;
  logic [15:0] expected_scheduler_bitmap;

  integer req_source_index;
  integer enqueue_source_index;
  integer enqueue_lane_index;
  integer state_source_index;
  integer state_lane_index;
  integer state_prior_lane;
  integer same_source_offset;

  function automatic logic [1:0] ptr_advance(
      input logic [1:0] pointer,
      input logic [2:0] amount
  );
    begin
      case (amount)
        3'd0, 3'd3: ptr_advance = pointer;
        3'd1: ptr_advance = (pointer == 2'd2) ? 2'd0 : pointer + 2'd1;
        3'd2: ptr_advance = (pointer == 2'd0) ? 2'd2 : pointer - 2'd1;
        default: ptr_advance = 2'd0;
      endcase
    end
  endfunction

  always_comb begin
    req = 16'd0;
    all_empty = 1'b1;
    for (req_source_index = 0; req_source_index < 16;
         req_source_index = req_source_index + 1) begin
      req[req_source_index] = (count_q[req_source_index] != 2'd0);
      if (count_q[req_source_index] != 2'd0)
        all_empty = 1'b0;
    end
  end

  a2_batched_iwrr_k2 scheduler (
    .clk(clk_i),
    .rst(rst_i),
    .req(req),
    .grant_count(scheduler_count),
    .grant_addr0(scheduler_source0),
    .grant_addr1(scheduler_source1),
    .grant_bitmap(scheduler_bitmap),
    .bundle_ready(scheduler_ready),
    .drain_idle(scheduler_idle)
  );

  assign scheduler_ready = !rst_i && link_enable_i && !shape_error;
  assign scheduler_commit = scheduler_ready && (scheduler_count != 2'd0);

  always_comb begin
    any_ingress = |ingress_valid_i;
    for (enqueue_source_index = 0; enqueue_source_index < 16;
         enqueue_source_index = enqueue_source_index + 1)
      enqueue_count[enqueue_source_index] = 3'd0;
    for (enqueue_lane_index = 0; enqueue_lane_index < INGRESS_LANES;
         enqueue_lane_index = enqueue_lane_index + 1) begin
      if (ingress_valid_i[enqueue_lane_index])
        enqueue_count[ingress_source_i[enqueue_lane_index*4 +: 4]] =
            enqueue_count[ingress_source_i[enqueue_lane_index*4 +: 4]] + 3'd1;
    end

    ingress_ready_o = !rst_i;
    for (enqueue_source_index = 0; enqueue_source_index < 16;
         enqueue_source_index = enqueue_source_index + 1) begin
      effective_capacity[enqueue_source_index] =
          3'd3 - {1'b0, count_q[enqueue_source_index]};
      if (scheduler_commit && scheduler_bitmap[enqueue_source_index])
        effective_capacity[enqueue_source_index] =
            effective_capacity[enqueue_source_index] + 3'd1;
      if (enqueue_count[enqueue_source_index] >
          effective_capacity[enqueue_source_index])
        ingress_ready_o = 1'b0;
    end
    ingress_commit_o = any_ingress && ingress_ready_o;
  end

  always_comb begin
    expected_scheduler_bitmap = 16'd0;
    if (scheduler_count != 2'd0)
      expected_scheduler_bitmap[scheduler_source0] = 1'b1;
    if (scheduler_count == 2'd2)
      expected_scheduler_bitmap[scheduler_source1] = 1'b1;
    shape_error = (scheduler_count == 2'd3) ||
                  ((scheduler_count == 2'd2) &&
                   (scheduler_source0 == scheduler_source1)) ||
                  (scheduler_bitmap != expected_scheduler_bitmap);
  end

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      for (state_source_index = 0; state_source_index < 16;
           state_source_index = state_source_index + 1) begin
        count_q[state_source_index] <= 2'd0;
        head_q[state_source_index] <= 2'd0;
        tail_q[state_source_index] <= 2'd0;
      end
      retire_count_o <= 2'd0;
      accept_count_o <= 2'd0;
      accept_source0_o <= 4'd0;
      accept_source1_o <= 4'd0;
      accept_payload0_o <= {PAYLOAD_W{1'b0}};
      accept_payload1_o <= {PAYLOAD_W{1'b0}};
      retire_source0_o <= 4'd0;
      retire_source1_o <= 4'd0;
      retire_payload0_o <= {PAYLOAD_W{1'b0}};
      retire_payload1_o <= {PAYLOAD_W{1'b0}};
      overflow_o <= 1'b0;
      protocol_error_o <= 1'b0;
    end else begin
      retire_count_o <= accept_count_o;
      retire_source0_o <= accept_source0_o;
      retire_source1_o <= accept_source1_o;
      retire_payload0_o <= accept_payload0_o;
      retire_payload1_o <= accept_payload1_o;
      accept_count_o <= scheduler_commit ? scheduler_count : 2'd0;
      accept_source0_o <= scheduler_commit ? scheduler_source0 : 4'd0;
      accept_source1_o <= (scheduler_commit && scheduler_count == 2'd2) ?
                          scheduler_source1 : 4'd0;
      accept_payload0_o <= scheduler_commit ?
                           payload_mem[scheduler_source0][head_q[scheduler_source0]] :
                           {PAYLOAD_W{1'b0}};
      accept_payload1_o <= (scheduler_commit && scheduler_count == 2'd2) ?
                           payload_mem[scheduler_source1][head_q[scheduler_source1]] :
                           {PAYLOAD_W{1'b0}};

      if (any_ingress && !ingress_ready_o)
        overflow_o <= 1'b1;
      if (shape_error || (scheduler_commit &&
          ((scheduler_count == 2'd0) ||
           (count_q[scheduler_source0] == 2'd0) ||
           ((scheduler_count == 2'd2) &&
            (count_q[scheduler_source1] == 2'd0)))))
        protocol_error_o <= 1'b1;

      for (state_source_index = 0; state_source_index < 16;
           state_source_index = state_source_index + 1) begin
        if (scheduler_commit && scheduler_bitmap[state_source_index])
          head_q[state_source_index] <= ptr_advance(head_q[state_source_index], 3'd1);
        if (ingress_commit_o && enqueue_count[state_source_index] != 3'd0)
          tail_q[state_source_index] <= ptr_advance(tail_q[state_source_index],
                                                    enqueue_count[state_source_index]);
        case ({ingress_commit_o ? enqueue_count[state_source_index] : 3'd0,
               (scheduler_commit && scheduler_bitmap[state_source_index])})
          4'b0000: count_q[state_source_index] <= count_q[state_source_index];
          4'b0001: count_q[state_source_index] <= count_q[state_source_index] - 2'd1;
          4'b0010: count_q[state_source_index] <= count_q[state_source_index] + 2'd1;
          4'b0011: count_q[state_source_index] <= count_q[state_source_index];
          4'b0100: count_q[state_source_index] <= count_q[state_source_index] + 2'd2;
          4'b0101: count_q[state_source_index] <= count_q[state_source_index] + 2'd1;
          4'b0110: count_q[state_source_index] <= count_q[state_source_index] + 2'd3;
          4'b0111: count_q[state_source_index] <= count_q[state_source_index] + 2'd2;
          default: begin
            count_q[state_source_index] <= count_q[state_source_index];
            protocol_error_o <= 1'b1;
          end
        endcase
      end

      if (ingress_commit_o) begin
        for (state_lane_index = 0; state_lane_index < INGRESS_LANES;
             state_lane_index = state_lane_index + 1) begin
          if (ingress_valid_i[state_lane_index]) begin
            same_source_offset = 0;
            for (state_prior_lane = 0; state_prior_lane < state_lane_index;
                 state_prior_lane = state_prior_lane + 1) begin
              if (ingress_valid_i[state_prior_lane] &&
                  ingress_source_i[state_prior_lane*4 +: 4] ==
                  ingress_source_i[state_lane_index*4 +: 4])
                same_source_offset = same_source_offset + 1;
            end
            payload_mem[ingress_source_i[state_lane_index*4 +: 4]]
                       [ptr_advance(tail_q[ingress_source_i[state_lane_index*4 +: 4]],
                                    same_source_offset)] <=
                ingress_payload_i[state_lane_index*PAYLOAD_W +: PAYLOAD_W];
          end
        end
      end
    end
  end

  assign drain_idle_o = all_empty && scheduler_idle &&
                        (accept_count_o == 2'd0) && (retire_count_o == 2'd0) &&
                        !overflow_o &&
                        !protocol_error_o;
endmodule
