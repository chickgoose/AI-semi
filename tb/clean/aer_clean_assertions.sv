module aer_clean_assertions #(
  parameter int NUM_SOURCES  = 4,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.monitor bench);
  default clocking cb @(posedge bench.clk); endclocking
  default disable iff (!bench.rst_n);

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : source_rule
      logic source_was_stalled;
      logic [ADDR_WIDTH-1:0] stalled_source_event;

      always @(posedge bench.clk or negedge bench.rst_n) begin
        if (!bench.rst_n) begin
          source_was_stalled <= 1'b0;
          stalled_source_event <= '0;
        end else begin
          if (source_was_stalled && bench.source_valid[source] &&
              !bench.source_ready[source] &&
              (bench.source_event[source] !== stalled_source_event))
            $error("CLEAN_ASSERT source changed during continuous stall source=%0d",
                   source);
          source_was_stalled <=
            bench.source_valid[source] && !bench.source_ready[source];
          if (bench.source_valid[source] && !bench.source_ready[source])
            stalled_source_event <= bench.source_event[source];
        end
      end

      ap_source_control_known: assert property (
        !$isunknown({bench.source_valid[source], bench.source_ready[source]})
      ) else $error("CLEAN_ASSERT unknown source handshake source=%0d", source);
    end
  endgenerate

  genvar lane;
  generate
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin : retire_rule
      logic retire_was_stalled;
      logic [ADDR_WIDTH-1:0] stalled_retire_event;
      logic [SOURCE_WIDTH-1:0] stalled_retire_source;

      always @(posedge bench.clk or negedge bench.rst_n) begin
        if (!bench.rst_n) begin
          retire_was_stalled <= 1'b0;
          stalled_retire_event <= '0;
          stalled_retire_source <= '0;
        end else begin
          if (retire_was_stalled && bench.retire_valid[lane] &&
              !bench.retire_ready[lane] &&
              ((bench.retire_event[lane] !== stalled_retire_event) ||
               (bench.retire_source[lane] !== stalled_retire_source)))
            $error("CLEAN_ASSERT completed event changed during continuous stall lane=%0d",
                   lane);
          retire_was_stalled <=
            bench.retire_valid[lane] && !bench.retire_ready[lane];
          if (bench.retire_valid[lane] && !bench.retire_ready[lane]) begin
            stalled_retire_event <= bench.retire_event[lane];
            stalled_retire_source <= bench.retire_source[lane];
          end
        end
      end

      ap_retire_event_known: assert property (
        bench.retire_valid[lane] |->
          !$isunknown({bench.retire_event[lane], bench.retire_source[lane]})
      ) else $error("CLEAN_ASSERT unknown completed event lane=%0d", lane);
    end
  endgenerate
endmodule
