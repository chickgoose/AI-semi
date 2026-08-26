// mixed_phase_always_ready에서 발견된 이상현상(generated=accepted+overrun 보존식 붕괴)
// 원인 추적용 -- tb_steal_buf_correctness.v와 동일한 shadow_cnt(2-deep) 방식으로
// trace 기반 구동을 검증, 어느 소스/사이클에서 phantom이 나는지 특정.
`timescale 1ns/1ps
module tb_steal_buf_trace_phantom_debug;
  reg [1023:0] trace_file_r;

  reg clk = 0;
  reg rst;
  reg [15:0] arrival;
  wire [15:0] overrun_w;
  wire valid0; wire [1:0] row0; wire [3:0] col_mask0;
  wire valid1; wire [1:0] row1; wire [3:0] col_mask1;

  aer_tx16_trad_rowcol_fovea_cluster2_steal_buf dut(
    .clk(clk), .rst(rst), .arrival(arrival), .overrun(overrun_w),
    .valid0(valid0), .row0(row0), .col_mask0(col_mask0),
    .valid1(valid1), .row1(row1), .col_mask1(col_mask1));

  always #5 clk = ~clk;

  integer fd, scan_ret, next_cycle, next_mask, have_next;
  integer cyc, c, i, drain_until;
  integer generated, delivered, dropped_overrun, phantom_count, error_count, collision_count;

  reg [1:0] shadow_cnt [0:15];
  reg was_overrun [0:15];

  task automatic drain_lane(input integer valid_in, input integer row_in, input [3:0] mask_in);
    integer idx;
    begin
      if (valid_in) begin
        for (c = 0; c < 4; c = c + 1) begin
          if (mask_in[c]) begin
            idx = row_in*4 + c;
            if (shadow_cnt[idx] == 2'd0) begin
              phantom_count = phantom_count + 1;
              error_count = error_count + 1;
              $display("PHANTOM cyc=%0d idx=%0d row=%0d col=%0d", cyc, idx, row_in, c);
            end else begin
              delivered = delivered + 1;
              shadow_cnt[idx] = shadow_cnt[idx] - 2'd1;
            end
          end
        end
      end
    end
  endtask

  initial begin
    rst = 1; arrival = 16'd0;
    generated = 0; delivered = 0; dropped_overrun = 0; phantom_count = 0; error_count = 0; collision_count = 0;
    for (i = 0; i < 16; i = i + 1) shadow_cnt[i] = 2'd0;
    if (!$value$plusargs("TRACE_FILE=%s", trace_file_r)) begin
      $display("MISSING +TRACE_FILE="); $finish;
    end
    fd = $fopen(trace_file_r, "r");
    if (fd == 0) begin $display("CANNOT_OPEN_TRACE %0s", trace_file_r); $finish; end
    scan_ret = $fscanf(fd, "%d %h", next_cycle, next_mask);
    have_next = (scan_ret == 2);

    @(posedge clk); #1;
    rst = 0;

    cyc = 0;
    while (have_next) begin
      arrival = 16'd0;
      while (have_next && next_cycle == cyc) begin
        for (i = 0; i < 16; i = i + 1) if (next_mask[i]) begin
          generated = generated + 1;
          arrival[i] = 1'b1;
        end
        scan_ret = $fscanf(fd, "%d %h", next_cycle, next_mask);
        have_next = (scan_ret == 2);
      end
      #1;
      for (i = 0; i < 16; i = i + 1) begin
        was_overrun[i] = 1'b0;
        if (overrun_w[i]) begin
          dropped_overrun = dropped_overrun + 1;
          was_overrun[i] = 1'b1;
          if (shadow_cnt[i] != 2'd2) begin
            error_count = error_count + 1;
            $display("BAD_OVERRUN_REPORT src=%0d shadow=%0d cyc=%0d", i, shadow_cnt[i], cyc);
          end
        end
      end

      @(posedge clk); #1;

      if (valid0 && valid1 && (row0 == row1)) begin
        collision_count = collision_count + 1;
        error_count = error_count + 1;
        $display("LANE_COLLISION cyc=%0d row=%0d", cyc, row0);
      end

      drain_lane(valid0, row0, col_mask0);
      drain_lane(valid1, row1, col_mask1);

      for (i = 0; i < 16; i = i + 1)
        if (arrival[i] && !was_overrun[i])
          shadow_cnt[i] = shadow_cnt[i] + 2'd1;

      cyc = cyc + 1;
    end

    arrival = 16'd0;
    drain_until = cyc + 15000;
    for (cyc = cyc; cyc < drain_until; cyc = cyc + 1) begin
      @(posedge clk); #1;
      drain_lane(valid0, row0, col_mask0);
      drain_lane(valid1, row1, col_mask1);
    end

    for (i = 0; i < 16; i = i + 1) begin
      if (shadow_cnt[i] != 0) begin
        error_count = error_count + 1;
        $display("DRAIN_INCOMPLETE source=%0d shadow_cnt=%0d", i, shadow_cnt[i]);
      end
    end
    if ((delivered + dropped_overrun) != generated) begin
      error_count = error_count + 1;
      $display("COUNT_MISMATCH delivered=%0d dropped=%0d sum=%0d generated=%0d",
        delivered, dropped_overrun, delivered+dropped_overrun, generated);
    end

    $display("TRACE=%0s generated=%0d delivered=%0d dropped_overrun=%0d phantom=%0d collisions=%0d",
      trace_file_r, generated, delivered, dropped_overrun, phantom_count, collision_count);
    if (error_count == 0) $display("PHANTOM_DEBUG_PASS");
    else $display("PHANTOM_DEBUG_FAIL errors=%0d", error_count);
    $fclose(fd);
    $finish;
  end
endmodule
