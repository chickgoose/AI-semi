// cluster2_steal_buf에 극성(polarity) 확장. 원본은 소스당 pending_cnt(2-deep 포화카운터,
// "몇 개 밀려있는지"만 셈 -- 신원 없음)만 있어서, 같은 소스에 극성이 다른 이벤트 2개가
// 동시에 대기할 수 있는 이 구조에서는 latch 하나로 부족함(§94에서 순정 cluster2엔 latch
// 하나로 충분했던 것과 다름 -- steal_buf는 진짜 2-deep 저장소가 있어서).
//
// §93에서 테스트벤치 shadow용으로 만든 event-ID 2-deep FIFO(fifo_id0/1, push=arrival,
// pop=grant, front-first)와 정확히 같은 구조를 이번엔 실제 RTL 레지스터로 둠 --
// pending_cnt와 나란히 갱신되는 pol_fifo0/pol_fifo1(소스당 1비트씩, 2슬롯).
// pending_cnt의 4가지 케이스({arrival&&!full, granted})와 정확히 대응:
//   2'b00: 변화 없음
//   2'b01(grant만): front(pol_fifo0) 출력 후 pop -- depth==2였으면 pol_fifo1이 새 front로
//   2'b10(arrival만): back(현재 depth 위치)에 push -- depth 0이면 pol_fifo0, 1이면 pol_fifo1
//   2'b11(둘 다, 이 케이스는 항상 old depth==1): pop한 자리(pol_fifo0)에 새 도착 극성을
//     바로 채움(순증감 0과 동일한 논리) -- 이번 사이클 출력은 옛 pol_fifo0(대기 중이던 것)
//
// 출력(pol_mask0/1)은 col_mask0/1과 완전히 같은 방식(현재 상태를 조합논리로 select한 뒤
// 함께 레지스터)으로 만들어서, "이 grant가 실제로 나른 극성"이 정확히 정렬됨.
module aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity (
  input         clk,
  input         rst,
  input  [15:0] arrival,
  input  [15:0] polarity_in,  // arrival[i]=1인 소스에 대해서만 의미 있음
  output [15:0] overrun,
  output reg        valid0,
  output reg [1:0]  row0,
  output reg [3:0]  col_mask0,
  output reg [3:0]  pol_mask0,
  output reg        valid1,
  output reg [1:0]  row1,
  output reg [3:0]  col_mask1,
  output reg [3:0]  pol_mask1
);
  reg [1:0] pending_cnt [0:15];
  reg pol_fifo0 [0:15]; // front(가장 오래된, 다음에 나갈 극성)
  reg pol_fifo1 [0:15]; // back(더 최근 도착)
  integer pc_k;

  wire [15:0] pending_gt0;
  wire [15:0] pending_full;
  wire [15:0] pol_front_bus;
  genvar gk;
  generate
    for (gk = 0; gk < 16; gk = gk + 1) begin: gt0
      assign pending_gt0[gk] = (pending_cnt[gk] != 2'd0);
      assign pending_full[gk] = (pending_cnt[gk] == 2'd2);
      assign pol_front_bus[gk] = pol_fifo0[gk];
    end
  endgenerate
  assign overrun = arrival & pending_full;

  wire [3:0] row_req;
  assign row_req[0] = |pending_gt0[3:0];
  assign row_req[1] = |pending_gt0[7:4];
  assign row_req[2] = |pending_gt0[11:8];
  assign row_req[3] = |pending_gt0[15:12];

  wire center_r1 = row_req[1];
  wire center_r2 = row_req[2];
  wire periph_r0 = row_req[0];
  wire periph_r3 = row_req[3];
  wire center_idle = ~(center_r1 | center_r2);
  wire periph_idle = ~(periph_r0 | periph_r3);
  wire steal_to_periph = center_idle & periph_r0 & periph_r3;
  wire steal_to_center = periph_idle & center_r1 & center_r2;

  localparam [3:0] CENTER_MASK = 4'b0110;
  localparam [3:0] PERIPH_MASK = 4'b1001;
  wire [3:0] center_req_in = row_req & CENTER_MASK;
  wire [3:0] periph_req_in = row_req & PERIPH_MASK;
  wire [3:0] center_gnt, periph_gnt;

  arbiter4_tree center_arb(.clk(clk), .rst(rst), .req(center_req_in), .gnt(center_gnt));
  arbiter4_tree periph_arb(.clk(clk), .rst(rst), .req(periph_req_in), .gnt(periph_gnt));

  reg lane0_valid_c;
  reg [1:0] lane0_row_c;
  reg [3:0] lane0_cols_c;
  reg [3:0] lane0_pol_c;
  always @(*) begin
    if (steal_to_center) begin
      lane0_valid_c = 1'b1; lane0_row_c = 2'd1;
      lane0_cols_c = pending_gt0[7:4];   lane0_pol_c = pol_front_bus[7:4];
    end else if (~center_idle) begin
      lane0_valid_c = 1'b1;
      lane0_row_c  = center_gnt[1] ? 2'd1 : 2'd2;
      lane0_cols_c = center_gnt[1] ? pending_gt0[7:4]   : pending_gt0[11:8];
      lane0_pol_c  = center_gnt[1] ? pol_front_bus[7:4] : pol_front_bus[11:8];
    end else if (steal_to_periph) begin
      lane0_valid_c = 1'b1; lane0_row_c = 2'd0;
      lane0_cols_c = pending_gt0[3:0];   lane0_pol_c = pol_front_bus[3:0];
    end else begin
      lane0_valid_c = 1'b0; lane0_row_c = 2'd0; lane0_cols_c = 4'd0; lane0_pol_c = 4'd0;
    end
  end

  reg lane1_valid_c;
  reg [1:0] lane1_row_c;
  reg [3:0] lane1_cols_c;
  reg [3:0] lane1_pol_c;
  always @(*) begin
    if (steal_to_periph) begin
      lane1_valid_c = 1'b1; lane1_row_c = 2'd3;
      lane1_cols_c = pending_gt0[15:12]; lane1_pol_c = pol_front_bus[15:12];
    end else if (~periph_idle) begin
      lane1_valid_c = 1'b1;
      lane1_row_c  = periph_gnt[0] ? 2'd0 : 2'd3;
      lane1_cols_c = periph_gnt[0] ? pending_gt0[3:0]    : pending_gt0[15:12];
      lane1_pol_c  = periph_gnt[0] ? pol_front_bus[3:0]  : pol_front_bus[15:12];
    end else if (steal_to_center) begin
      lane1_valid_c = 1'b1; lane1_row_c = 2'd2;
      lane1_cols_c = pending_gt0[11:8];  lane1_pol_c = pol_front_bus[11:8];
    end else begin
      lane1_valid_c = 1'b0; lane1_row_c = 2'd0; lane1_cols_c = 4'd0; lane1_pol_c = 4'd0;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      valid0 <= 1'b0; row0 <= 2'd0; col_mask0 <= 4'd0; pol_mask0 <= 4'd0;
      valid1 <= 1'b0; row1 <= 2'd0; col_mask1 <= 4'd0; pol_mask1 <= 4'd0;
    end else begin
      valid0 <= lane0_valid_c; row0 <= lane0_row_c; col_mask0 <= lane0_cols_c; pol_mask0 <= lane0_pol_c;
      valid1 <= lane1_valid_c; row1 <= lane1_row_c; col_mask1 <= lane1_cols_c; pol_mask1 <= lane1_pol_c;
    end
  end

  wire [15:0] granted_bitmap =
    (lane0_valid_c ? (lane0_cols_c << (lane0_row_c*4)) : 16'd0) |
    (lane1_valid_c ? (lane1_cols_c << (lane1_row_c*4)) : 16'd0);

  always @(posedge clk) begin
    if (rst) begin
      for (pc_k = 0; pc_k < 16; pc_k = pc_k + 1) begin
        pending_cnt[pc_k] <= 2'd0; pol_fifo0[pc_k] <= 1'b0; pol_fifo1[pc_k] <= 1'b0;
      end
    end else begin
      for (pc_k = 0; pc_k < 16; pc_k = pc_k + 1) begin
        case ({arrival[pc_k] && !pending_full[pc_k], granted_bitmap[pc_k]})
          2'b10: begin
            pending_cnt[pc_k] <= pending_cnt[pc_k] + 2'd1;
            if (pending_cnt[pc_k] == 2'd0) pol_fifo0[pc_k] <= polarity_in[pc_k];
            else pol_fifo1[pc_k] <= polarity_in[pc_k];
          end
          2'b01: begin
            pending_cnt[pc_k] <= pending_cnt[pc_k] - 2'd1;
            pol_fifo0[pc_k] <= pol_fifo1[pc_k]; // depth==2였으면 back이 새 front로 시프트
          end
          2'b11: begin
            pending_cnt[pc_k] <= pending_cnt[pc_k]; // 순증감 0(§ 원본과 동일)
            pol_fifo0[pc_k] <= polarity_in[pc_k]; // old depth==1 보장 -- pop한 자리에 새 도착 채움
          end
          default: pending_cnt[pc_k] <= pending_cnt[pc_k];
        endcase
      end
    end
  end
endmodule
