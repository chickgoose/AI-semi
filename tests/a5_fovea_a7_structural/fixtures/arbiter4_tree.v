// arbiter4(가변량 barrel shifter 기반 4-way round-robin)의 대체 후보 — 2-way 공정
// 중재기(arbiter2) 3개를 이진 트리로 묶어서 4-way 공정 중재를 구현. (0,1) 페어와
// (2,3) 페어를 각각 독립적으로 공정하게 뽑고, 그 두 "그룹 대표" 사이에서 다시
// 공정하게 고른다 — 매 단계가 1비트 상태만 쓰는 arbiter2라 arbiter4의 가변폭
// 시프터가 아예 없음. 전역적으로 완벽히 동일한 회전 순서는 아니지만(각 레벨이
// 지역적으로만 공정), 각 레벨이 기아를 안 만드므로 전체도 유한한 시간 내 반드시
// 서비스됨(로버스트니스는 tb_arbiter4_tree_robustness.v로 검증).
module arbiter4_tree(
  input        clk,
  input        rst,
  input  [3:0] req,
  output [3:0] gnt
);
  wire [1:0] gnt_lo, gnt_hi; // (0,1) 페어, (2,3) 페어 내부 승자(해당 그룹이 뽑히면 유효)
  wire       req_lo = |req[1:0];
  wire       req_hi = |req[3:2];

  arbiter2 pair_lo(.clk(clk), .rst(rst), .req(req[1:0]), .gnt(gnt_lo));
  arbiter2 pair_hi(.clk(clk), .rst(rst), .req(req[3:2]), .gnt(gnt_hi));

  wire [1:0] group_gnt; // group_gnt[0]=lo그룹 선택, group_gnt[1]=hi그룹 선택
  arbiter2 top(.clk(clk), .rst(rst), .req({req_hi, req_lo}), .gnt(group_gnt));

  assign gnt[1:0] = group_gnt[0] ? gnt_lo : 2'b00;
  assign gnt[3:2] = group_gnt[1] ? gnt_hi : 2'b00;
endmodule
