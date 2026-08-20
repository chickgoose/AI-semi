`timescale 1ns/1ps

// Lossless route-change control primitive.  The block freezes admission,
// drains the acceptance-time route, and changes route/epoch only at a clean
// boundary.  It does not implement the sparse or tile datapaths themselves.
module mc_wtb_epoch_route_interlock #(
  parameter integer EPOCH_W = 16
) (
  input  logic                 clk_i,
  input  logic                 rst_i,

  input  logic                 request_valid_i,
  output logic                 request_ready_o,
  input  logic [1:0]           requested_route_i,
  input  logic [EPOCH_W-1:0]   requested_epoch_i,
  input  logic                 pose_reliable_i,
  input  logic                 profile_authorized_i,

  input  logic                 ingress_valid_i,
  output logic                 ingress_ready_o,
  output logic                 routed_ingress_valid_o,
  input  logic                 routed_ingress_ready_i,

  input  logic                 transport_empty_i,
  input  logic                 route_adapters_empty_i,
  input  logic                 transport_healthy_i,

  output logic [1:0]           active_route_o,
  output logic [EPOCH_W-1:0]   active_epoch_o,
  output logic [2:0]           route_enable_o,
  output logic                 epoch_commit_o,
  output logic                 transition_busy_o,
  output logic                 protocol_error_o
);
  localparam logic [1:0] ROUTE_BYPASS = 2'd0;
  localparam logic [1:0] ROUTE_SPARSE = 2'd1;
  localparam logic [1:0] ROUTE_TILE   = 2'd2;
  localparam logic [1:0] STATE_RUN   = 2'd0;
  localparam logic [1:0] STATE_DRAIN = 2'd1;
  localparam logic [1:0] STATE_ERROR = 2'd2;
  localparam logic [1:0] STATE_FAULT_WAIT = 2'd3;

  logic [1:0] state_q;
  logic [1:0] pending_route_q;
  logic [EPOCH_W-1:0] pending_epoch_q;
  logic requested_route_legal;
  logic [1:0] sanitized_route;
  logic clean_empty;
  logic active_epoch_valid_q;

  always_comb begin
    requested_route_legal = requested_route_i == ROUTE_BYPASS ||
                            requested_route_i == ROUTE_SPARSE ||
                            requested_route_i == ROUTE_TILE;
    if (!pose_reliable_i || !profile_authorized_i || !requested_route_legal)
      sanitized_route = ROUTE_BYPASS;
    else
      sanitized_route = requested_route_i;
    clean_empty = transport_empty_i && route_adapters_empty_i;
  end

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      state_q <= STATE_RUN;
      active_route_o <= ROUTE_BYPASS;
      active_epoch_o <= {EPOCH_W{1'b0}};
      active_epoch_valid_q <= 1'b0;
      pending_route_q <= ROUTE_BYPASS;
      pending_epoch_q <= {EPOCH_W{1'b0}};
      epoch_commit_o <= 1'b0;
      protocol_error_o <= 1'b0;
    end else begin
      epoch_commit_o <= 1'b0;
      case (state_q)
        STATE_RUN: begin
          if (!transport_healthy_i) begin
            protocol_error_o <= 1'b1;
            state_q <= STATE_ERROR;
          end else if (!pose_reliable_i || !profile_authorized_i) begin
            if (active_epoch_valid_q && &active_epoch_o) begin
              protocol_error_o <= 1'b1;
              state_q <= STATE_ERROR;
            end else begin
              pending_route_q <= ROUTE_BYPASS;
              pending_epoch_q <= active_epoch_valid_q ? active_epoch_o + 1'b1 : {EPOCH_W{1'b0}};
              state_q <= STATE_DRAIN;
            end
          end else if (request_valid_i) begin
            pending_route_q <= sanitized_route;
            pending_epoch_q <= requested_epoch_i;
            if (!requested_route_legal)
              protocol_error_o <= 1'b1;
            if (active_epoch_valid_q && requested_epoch_i <= active_epoch_o) begin
              protocol_error_o <= 1'b1;
              state_q <= STATE_ERROR;
            end else
              state_q <= STATE_DRAIN;
          end
        end
        STATE_DRAIN: begin
          if (!transport_healthy_i) begin
            protocol_error_o <= 1'b1;
            state_q <= STATE_ERROR;
          end else if (!pose_reliable_i || !profile_authorized_i) begin
            pending_route_q <= ROUTE_BYPASS;
            if (clean_empty) begin
              active_route_o <= ROUTE_BYPASS;
              active_epoch_o <= pending_epoch_q;
              active_epoch_valid_q <= 1'b1;
              epoch_commit_o <= 1'b1;
              state_q <= STATE_FAULT_WAIT;
            end
          end else if (clean_empty) begin
            active_route_o <= pending_route_q;
            active_epoch_o <= pending_epoch_q;
            active_epoch_valid_q <= 1'b1;
            epoch_commit_o <= 1'b1;
            state_q <= STATE_RUN;
          end
        end
        STATE_FAULT_WAIT: begin
          if (!transport_healthy_i) begin
            protocol_error_o <= 1'b1;
            state_q <= STATE_ERROR;
          end else if (pose_reliable_i && profile_authorized_i) begin
            state_q <= STATE_RUN;
          end
        end
        default: begin
          // No forced flush or route change after a transport fault.
          state_q <= STATE_ERROR;
        end
      endcase
    end
  end

  always_comb begin
    request_ready_o = !rst_i && state_q == STATE_RUN && transport_healthy_i &&
                      pose_reliable_i && profile_authorized_i;
    transition_busy_o = state_q != STATE_RUN;
    ingress_ready_o = !rst_i && state_q == STATE_RUN && transport_healthy_i &&
                      pose_reliable_i && profile_authorized_i &&
                      !request_valid_i && routed_ingress_ready_i;
    routed_ingress_valid_o = !rst_i && state_q == STATE_RUN && transport_healthy_i &&
                             pose_reliable_i && profile_authorized_i &&
                             !request_valid_i && ingress_valid_i;
    case (active_route_o)
      ROUTE_BYPASS: route_enable_o = 3'b001;
      ROUTE_SPARSE: route_enable_o = 3'b010;
      ROUTE_TILE:   route_enable_o = 3'b100;
      default:      route_enable_o = 3'b001;
    endcase
  end
endmodule
