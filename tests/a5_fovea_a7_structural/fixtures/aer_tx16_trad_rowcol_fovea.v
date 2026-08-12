// true_traditional(aer_tx16_trad_rowcol.v: 행-열 2단계 중재 + 열 중재기 공유,
// burst 없음) + fovea(중심와 우선순위, aer_tx16_fovea.v)의 가중 행선택을 결합.
// burst 상태기계는 전혀 없음(매 사이클 새로 행+열을 통합 중재) — 그래서 fovea 원본의
// state==IDLE 게이팅도 필요 없고, 매 사이클이 항상 "행 선택" 사이클임.
// 열 중재기는 true_traditional과 동일하게 전체가 공유하는 중재기 1개(Boahen 2000의
// √N 절감 유지) — 가중치는 행 선택에만 적용됨.
// 중재기 3개(center/periph/col) 전부 arbiter4(가변량 barrel shifter) 대신
// arbiter4_tree(2-way 공정중재기 arbiter2 3개의 이진트리, progress.md #28)를 씀 —
// round 레지스터 비트폭 버그를 걷어낸 뒤 재검증하니 true_traditional 단독보다도
// 면적/Fmax/전력 3축 다 우위(167.238um²/833.3MHz/12.89uW, #28-4).
module aer_tx16_trad_rowcol_fovea #(
  parameter WEIGHT = 5 // 중심:주변 가중치 비율 (WEIGHT:1) — 1차 제출 최종값(progress.md #26-5/#27:
                        // 중심개선 87%와 균일부하 최악지연 비용 사이 효율점, W=10보다 채택)
) (
  input         clk,
  input         rst,
  input  [15:0] req,
  output reg    valid,
  output reg [3:0] addr   // {row[1:0], col[1:0]}
);
  wire [3:0] row_req;
  assign row_req[0] = |req[3:0];
  assign row_req[1] = |req[7:4];
  assign row_req[2] = |req[11:8];
  assign row_req[3] = |req[15:12];

  localparam [3:0] CENTER_MASK = 4'b0110; // 행1,행2
  localparam [3:0] PERIPH_MASK = 4'b1001; // 행0,행3

  // round는 0..WEIGHT(WEIGHT+1개 값)를 표현해야 함 -> $clog2(WEIGHT+1)비트가 정확한
  // 최소값(예전엔 $clog2(WEIGHT+2)로 잘못 계산해서 WEIGHT=1/3/7 등에서 1비트씩 낭비
  // 했었음 — WEIGHT=5는 clog2(6)=clog2(7)=3이라 우연히 안 걸렸을 뿐). WEIGHT=0일 때
  // $clog2(1)=0이 되는 것만 최소 1비트로 방어.
  localparam RW = (WEIGHT == 0) ? 1 : $clog2(WEIGHT + 1);
  reg [RW-1:0] round; // 0..WEIGHT-1=중심 우선 / WEIGHT=주변 우선
  wire prefer_center = (round != WEIGHT[RW-1:0]);

  wire center_avail = |(row_req & CENTER_MASK);
  wire periph_avail = |(row_req & PERIPH_MASK);
  wire use_center = (prefer_center && center_avail) || (!prefer_center && !periph_avail && center_avail);
  wire use_periph = (!prefer_center && periph_avail) || (prefer_center && !center_avail && periph_avail);

  // 독립된 중재기 2개(공유 시 회전상태가 갇히는 버그 있었음 — aer_tx16_fovea.v 참고)
  wire [3:0] center_req_in = use_center ? (row_req & CENTER_MASK) : 4'b0000;
  wire [3:0] periph_req_in = use_periph ? (row_req & PERIPH_MASK) : 4'b0000;
  wire [3:0] center_gnt, periph_gnt;

  arbiter4_tree center_arb(.clk(clk), .rst(rst), .req(center_req_in), .gnt(center_gnt));
  arbiter4_tree periph_arb(.clk(clk), .rst(rst), .req(periph_req_in), .gnt(periph_gnt));

  wire [3:0] row_gnt = use_center ? center_gnt : (use_periph ? periph_gnt : 4'b0000);

  function [1:0] idx4;
    input [3:0] bits;
    begin
      if (bits[0]) idx4 = 2'd0;
      else if (bits[1]) idx4 = 2'd1;
      else if (bits[2]) idx4 = 2'd2;
      else idx4 = 2'd3;
    end
  endfunction

  reg [3:0] sel_row_cols;
  always @(*) begin
    case (idx4(row_gnt))
      2'd0: sel_row_cols = req[3:0];
      2'd1: sel_row_cols = req[7:4];
      2'd2: sel_row_cols = req[11:8];
      default: sel_row_cols = req[15:12];
    endcase
  end

  wire [3:0] col_gnt;
  arbiter4_tree col_arb(.clk(clk), .rst(rst), .req(sel_row_cols), .gnt(col_gnt));

  always @(posedge clk) begin
    if (rst) begin
      round <= {RW{1'b0}};
    end else if (|row_gnt) begin
      round <= (round == WEIGHT[RW-1:0]) ? {RW{1'b0}} : round + 1'b1;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      valid <= 1'b0;
      addr  <= 4'd0;
    end else begin
      valid <= |row_gnt;
      addr  <= {idx4(row_gnt), idx4(col_gnt)};
    end
  end
endmodule
