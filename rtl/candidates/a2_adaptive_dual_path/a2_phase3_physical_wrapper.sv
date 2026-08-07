`timescale 1ns/1ps

module a2_phase3_physical_wrapper #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int MODEL = 0,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic clk_i,
  input  logic rst_ni,
  input  logic [NUM_SOURCES-1:0] source_valid_i,
  output logic [NUM_SOURCES-1:0] source_ready_o,
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_i,
  output logic retire_valid_o,
  input  logic retire_ready_i,
  output logic [ADDR_WIDTH-1:0] retire_event_o,
  output logic [SOURCE_WIDTH-1:0] retire_source_o
);
  logic [NUM_SOURCES-1:0] ingress_valid;
  logic [NUM_SOURCES*ADDR_WIDTH-1:0] ingress_event;
  logic [NUM_SOURCES-1:0] core_source_ready;
  logic core_retire_valid;
  logic core_retire_ready;
  logic [ADDR_WIDTH-1:0] core_retire_event;
  logic [SOURCE_WIDTH-1:0] core_retire_source;
  logic retire_valid_q;
  logic [ADDR_WIDTH-1:0] retire_event_q;
  logic [SOURCE_WIDTH-1:0] retire_source_q;
  integer source;

  always_comb begin
    source_ready_o = ~ingress_valid | core_source_ready;
    core_retire_ready = ~retire_valid_q | retire_ready_i;
    retire_valid_o = retire_valid_q;
    retire_event_o = retire_event_q;
    retire_source_o = retire_source_q;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ingress_valid <= '0;
      ingress_event <= '0;
      retire_valid_q <= 1'b0;
      retire_event_q <= '0;
      retire_source_q <= '0;
    end else begin
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        case ({source_valid_i[source] && source_ready_o[source],
               ingress_valid[source] && core_source_ready[source]})
          2'b10, 2'b11: begin
            ingress_valid[source] <= 1'b1;
            ingress_event[source*ADDR_WIDTH +: ADDR_WIDTH] <=
              source_event_i[source*ADDR_WIDTH +: ADDR_WIDTH];
          end
          2'b01: ingress_valid[source] <= 1'b0;
          default: begin end
        endcase
      end
      if (core_retire_ready) begin
        retire_valid_q <= core_retire_valid;
        if (core_retire_valid) begin
          retire_event_q <= core_retire_event;
          retire_source_q <= core_retire_source;
        end
      end
    end
  end

  generate
    if (MODEL == 0) begin : g_a2
      a2_phase3_selected_packed_core #(
        .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)
      ) core (
        .clk_i(clk_i), .rst_ni(rst_ni),
        .source_valid_i(ingress_valid), .source_ready_o(core_source_ready),
        .source_event_i(ingress_event), .retire_valid_o(core_retire_valid),
        .retire_ready_i(core_retire_ready), .retire_event_o(core_retire_event),
        .retire_source_o(core_retire_source)
      );
    end else if (MODEL == 1) begin : g_flat
      a2_phase3_flat_rr_core #(
        .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)
      ) core (
        .clk_i(clk_i), .rst_ni(rst_ni),
        .source_valid_i(ingress_valid), .source_ready_o(core_source_ready),
        .source_event_i(ingress_event), .retire_valid_o(core_retire_valid),
        .retire_ready_i(core_retire_ready), .retire_event_o(core_retire_event),
        .retire_source_o(core_retire_source)
      );
    end else begin : g_always
      a2_phase3_always_buffered_core #(
        .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
        .RESERVOIR_DEPTH(16), .BANK_COUNT(4)
      ) core (
        .clk_i(clk_i), .rst_ni(rst_ni),
        .source_valid_i(ingress_valid), .source_ready_o(core_source_ready),
        .source_event_i(ingress_event), .retire_valid_o(core_retire_valid),
        .retire_ready_i(core_retire_ready), .retire_event_o(core_retire_event),
        .retire_source_o(core_retire_source)
      );
    end
  endgenerate
endmodule
