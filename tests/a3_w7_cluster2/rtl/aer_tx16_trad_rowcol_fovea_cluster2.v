// cluster(열 비트맵)를 한 단계 더 확장 -- 중심팀/주변팀이 매 사이클 "누구 차례인지"로
// 경쟁하는 대신, 아예 각자 자기 레인을 갖는다. 원래 fovea의 `round`/`prefer_center`는
// "중심과 주변이 같은 출력 슬롯 하나를 놓고 경쟁"할 때만 필요했던 장치인데, 레인이
// 두 개면 애초에 경쟁할 필요가 없어진다 -- center_arb/periph_arb는 이미 매 사이클
// 독립적으로 자기 팀의 승자 행을 계산해두고 있었으니(기존 fovea에서도 round가 어느
// 쪽을 "쓸지"만 정했을 뿐, 계산 자체는 항상 둘 다 하고 있었음), 그 결과를 두 레인으로
// 그대로 내보내기만 하면 된다.
//
// 효과: 중심은 이제 "6번 중 5번만" 서비스받는 게 아니라 자기 데이터가 있으면 매 사이클
// 무조건 자기 레인으로 나감(주변과 아예 경쟁 안 함) -- 원래 fovea보다 더 강한 형태의
// 우선권 보장이면서, 동시에 주변도 자기 레인을 그대로 가지므로 굶지 않는다. 최대
// 2행 x 4열 = 8개/cycle까지 나갈 수 있음(중심 행 하나 + 주변 행 하나, 각각 열 4개씩).
//
// 대가: 이제 "중심:주변 = 5:1" 같은 명시적 배분 비율 자체가 없어짐(WEIGHT 파라미터
// 삭제) -- 서로 안 겹치는 자원(레인)을 쓰니 "배분"이라는 개념이 성립하지 않기 때문.
// fovea의 기여였던 "제한된 자원의 의도적 재분배"가 아니라 "각자 전용 채널 보장"으로
// 성격이 바뀐 것 -- 이건 사실 다른 문제(자원 배분 -> 채널 분리)를 푸는 것이므로 별도
// 실험으로 취급해야 한다.
module aer_tx16_trad_rowcol_fovea_cluster2 (
  input         clk,
  input         rst,
  input  [15:0] req,
  output reg        valid0,   // 중심 레인
  output reg [1:0]  row0,
  output reg [3:0]  col_mask0,
  output reg        valid1,   // 주변 레인
  output reg [1:0]  row1,
  output reg [3:0]  col_mask1
);
  wire [3:0] row_req;
  assign row_req[0] = |req[3:0];
  assign row_req[1] = |req[7:4];
  assign row_req[2] = |req[11:8];
  assign row_req[3] = |req[15:12];

  localparam [3:0] CENTER_MASK = 4'b0110; // 행1,행2
  localparam [3:0] PERIPH_MASK = 4'b1001; // 행0,행3

  // round/prefer_center 없음 -- 두 팀 다 자기 요청이 있으면 항상 그 팀 중재기에 그대로 넣음.
  wire [3:0] center_req_in = row_req & CENTER_MASK;
  wire [3:0] periph_req_in = row_req & PERIPH_MASK;
  wire [3:0] center_gnt, periph_gnt;

  arbiter4_tree center_arb(.clk(clk), .rst(rst), .req(center_req_in), .gnt(center_gnt));
  arbiter4_tree periph_arb(.clk(clk), .rst(rst), .req(periph_req_in), .gnt(periph_gnt));

  function [1:0] idx4;
    input [3:0] bits;
    begin
      if (bits[0]) idx4 = 2'd0;
      else if (bits[1]) idx4 = 2'd1;
      else if (bits[2]) idx4 = 2'd2;
      else idx4 = 2'd3;
    end
  endfunction

  reg [3:0] sel_center_cols, sel_periph_cols;
  always @(*) begin
    case (idx4(center_gnt))
      2'd0: sel_center_cols = req[3:0];
      2'd1: sel_center_cols = req[7:4];
      2'd2: sel_center_cols = req[11:8];
      default: sel_center_cols = req[15:12];
    endcase
    case (idx4(periph_gnt))
      2'd0: sel_periph_cols = req[3:0];
      2'd1: sel_periph_cols = req[7:4];
      2'd2: sel_periph_cols = req[11:8];
      default: sel_periph_cols = req[15:12];
    endcase
  end

  always @(posedge clk) begin
    if (rst) begin
      valid0 <= 1'b0; row0 <= 2'd0; col_mask0 <= 4'd0;
      valid1 <= 1'b0; row1 <= 2'd0; col_mask1 <= 4'd0;
    end else begin
      valid0 <= |center_gnt;
      row0 <= idx4(center_gnt);
      col_mask0 <= sel_center_cols;

      valid1 <= |periph_gnt;
      row1 <= idx4(periph_gnt);
      col_mask1 <= sel_periph_cols;
    end
  end
endmodule
