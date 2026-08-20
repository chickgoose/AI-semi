`timescale 1ns/1ps

module mc_wtb_motion_qualifier #(
  parameter integer DISP_W = 16,
  parameter integer MID_TO_LOW_Q = 192,
  parameter integer LOW_TO_MID_Q = 256,
  parameter integer HIGH_TO_MID_Q = 384,
  parameter integer MID_TO_HIGH_Q = 512,
  parameter integer MIN_DWELL_EPOCHS = 2
) (
  input  logic                  clk_i,
  input  logic                  rst_i,
  input  logic                  epoch_valid_i,
  input  logic                  pose_reliable_i,
  input  logic                  profile_authorized_i,
  input  logic [DISP_W-1:0]     displacement_q_i,
  output logic [1:0]            motion_class_o,
  output logic                  warp_enable_o,
  output logic                  tile_enable_o,
  output logic                  safe_bypass_o,
  output logic                  class_changed_o
);
  localparam logic [1:0] CLASS_UNRELIABLE = 2'd0;
  localparam logic [1:0] CLASS_LOW        = 2'd1;
  localparam logic [1:0] CLASS_MID        = 2'd2;
  localparam logic [1:0] CLASS_HIGH       = 2'd3;
  localparam integer DWELL_W = (MIN_DWELL_EPOCHS <= 1) ? 1 :
                               $clog2(MIN_DWELL_EPOCHS + 1);

  logic [1:0] class_q, candidate_q, desired_class;
  logic [DWELL_W-1:0] candidate_count_q;

  initial begin
    if (!(MID_TO_LOW_Q < LOW_TO_MID_Q &&
          LOW_TO_MID_Q <= HIGH_TO_MID_Q &&
          HIGH_TO_MID_Q < MID_TO_HIGH_Q &&
          MIN_DWELL_EPOCHS >= 1))
      $fatal(1, "invalid MC-WTB motion qualification parameters");
  end

  always_comb begin
    desired_class = class_q;
    case (class_q)
      CLASS_LOW: begin
        if (displacement_q_i >= MID_TO_HIGH_Q)
          desired_class = CLASS_HIGH;
        else if (displacement_q_i >= LOW_TO_MID_Q)
          desired_class = CLASS_MID;
      end
      CLASS_MID: begin
        if (displacement_q_i <= MID_TO_LOW_Q)
          desired_class = CLASS_LOW;
        else if (displacement_q_i >= MID_TO_HIGH_Q)
          desired_class = CLASS_HIGH;
      end
      CLASS_HIGH: begin
        if (displacement_q_i <= MID_TO_LOW_Q)
          desired_class = CLASS_LOW;
        else if (displacement_q_i <= HIGH_TO_MID_Q)
          desired_class = CLASS_MID;
      end
      default: begin
        if (displacement_q_i < LOW_TO_MID_Q)
          desired_class = CLASS_LOW;
        else if (displacement_q_i >= MID_TO_HIGH_Q)
          desired_class = CLASS_HIGH;
        else
          desired_class = CLASS_MID;
      end
    endcase
  end

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      class_q <= CLASS_UNRELIABLE;
      candidate_q <= CLASS_UNRELIABLE;
      candidate_count_q <= {DWELL_W{1'b0}};
      class_changed_o <= 1'b0;
    end else begin
      class_changed_o <= 1'b0;
      if (epoch_valid_i) begin
        if (!pose_reliable_i || !profile_authorized_i) begin
          class_changed_o <= class_q != CLASS_UNRELIABLE;
          class_q <= CLASS_UNRELIABLE;
          candidate_q <= CLASS_UNRELIABLE;
          candidate_count_q <= {DWELL_W{1'b0}};
        end else if (desired_class == class_q) begin
          candidate_q <= desired_class;
          candidate_count_q <= {DWELL_W{1'b0}};
        end else if (desired_class != candidate_q) begin
          candidate_q <= desired_class;
          if (MIN_DWELL_EPOCHS == 1) begin
            class_q <= desired_class;
            candidate_count_q <= {DWELL_W{1'b0}};
            class_changed_o <= 1'b1;
          end else begin
            candidate_count_q <= {{(DWELL_W-1){1'b0}}, 1'b1};
          end
        end else if (candidate_count_q + 1'b1 >= MIN_DWELL_EPOCHS) begin
          class_q <= desired_class;
          candidate_count_q <= {DWELL_W{1'b0}};
          class_changed_o <= 1'b1;
        end else begin
          candidate_count_q <= candidate_count_q + 1'b1;
        end
      end
    end
  end

  always_comb begin
    motion_class_o = class_q;
    warp_enable_o = class_q == CLASS_MID || class_q == CLASS_HIGH;
    tile_enable_o = class_q == CLASS_HIGH;
    safe_bypass_o = !warp_enable_o;
  end
endmodule
