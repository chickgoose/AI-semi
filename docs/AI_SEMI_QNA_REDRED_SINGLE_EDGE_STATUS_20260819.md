# REDRED 주최 측 Q&A 기반 single-edge 목표와 현재 판정

기준일: 2026-08-19

## 확정 해석

주최 측은 최소 PPA 숫자 하나보다 문제 정의, 기능 성공, 실제 구현 가능성,
시스템 전체 경계의 loss·처리 성능·PPA와 안정성을 함께 본다. Timing 확인은
필요하며 post-layout simulation과 일반 범위 PVT 검토는 가능하면 수행한다.
교육용 45 nm PDK와 그 I/O/load 조건을 공통으로 쓰고, 전력의 기본 평가는
vectorless다. 공식 traffic과 단일 점수식은 제공하지 않으며, 주최 측 sensor
data는 추후 제공 예정이다. 공개 dataset은 별도 참고 자료다. PDK가 제공하지
않는 multi-edge/DDR/vendor primitive는 대회 사용 근거로 삼을 수 없다.

따라서 REDRED의 실행 목표는 다음과 같다.

> 교육용 GPDK045에서 주최 측이 허용하는 셀·클록 구조만으로 16-source
> pending/accept부터 retire까지의 완전한 AER 디지털 endpoint를 구성하고,
> A2를 aggregate-weighted 성능 주후보·A3를 exact scalar-prefix 의미
> fallback으로 두어 동일 조건의 팀 canonical synthetic 및 제공·공개 데이터
> campaign에서 accepted-event exact-once, source overrun·throughput·latency,
> post-route timing·area와 vectorless power를 입증한다. Known-motion
> world↔sensor 좌표 변환은 endpoint PPA와 분리된 system demo로 검증한다.

## 고정 평가 경계

- 현재 구현 경계는 하나의 `clk_i` 상승 에지 도메인, synchronous active-high
  `rst_i`, synchronous admission backpressure인 `link_enable_i`, 16-source
  pending/accept, 9-wire `{valid,addr0,addr1}` single-edge link와 ordered
  two-lane retire다. Generated/gated/forwarded clock은 없다. Reset은 in-flight
  state를 지우므로 clean `drain_idle_o`를 먼저 확인한 drain-before-reset만
  qualification 범위다.
- Endpoint PPA에는 scheduler, source admission, charge되는 buffer, TX/RX,
  retirement, drain/error logic을 모두 포함한다.
- Event generator, scoreboard, visualization, coordinate transform와 motion
  estimator는 endpoint PPA에서 제외한다.
- `generated = source_overrun + accepted`, bounded drain 후
  `accepted = retired`를 별도로 검증한다.
- `source_overrun`은 capacity/performance loss다. Phantom, duplicate,
  corruption, reorder, accepted-missing, reset escape와 protocol error는 hard
  correctness failure다.
- Release 후보는 posedge single shared clock과 일반 standard-cell 구조다.
  `audits/k2_final_selection/`의 P6/multi-edge A2 선택은 superseded/noncurrent
  역사적 연구 비교이며 현재 후보/interface/release 선택 authority가 없다.
  Single-edge parallel 구현만 release-eligible interface로 선택했지만 release와
  최종 A2/A3 선택은 HOLD다.

## 현재 bounded evidence

- Hardened source `6fc5e167…`, integration `a0a4eb386…`의 9-wire single-edge
  complete endpoint를 구현했다. Directed smoke는 257 legal wire states와
  64 back-to-back records/96 events, disable/resume 및 sticky-error/clean-drain을
  통과했다.
- 팀 정의 synthetic full50 실제 RTL 결과는 A2 `generated=106,416`,
  `overrun=2,370`, `accepted=retired=104,046`, fixed-window
  `0.896281733 event/cycle`, accept→retire 3 cycles이다. A3는
  `overrun=12,771`, `accepted=retired=93,645`, `0.806670806 event/cycle`,
  2 cycles이다.
- UZH 공개 자료의 동일 1,100 events를 1×/64×/256×로 재타이밍한 실제 RTL
  여섯 실행도 exact conservation을 만족했다. 256×에서 A2는
  906 accepted/194 overrun, A3는 817 accepted/283 overrun이었다. 이는
  `PUBLIC_PROJECTED_EXTENSION`이며 공식 대회 traffic이 아니다.
- Synthetic v2는 서로 독립적인 두 campaign에서 합계 424개 actual-RTL
  process(200 full50, 4 reset, 4 activation, 16 mutant, 200 ordinal observer)를
  실행했다. 1,520개 payload의 12,279,031-byte closed archive를 재개봉하고
  byte-for-byte 재봉인했으며, 200개 same-cycle order sidecar와 semantic
  reproduction을 독립 재계산했다. Result/export/publication SHA-256은 각각
  `7a4a8a3f…70554`, `b3a2a695…79786`, `95ddce39…0931`이다.
- Public projected v2도 두 번 재현했고, 80-entry closed export, exact
  accept/retire ordinal, raw archive integrity와 non-circular Git provenance를
  독립 검증했다. 최종 14개 fail-closed 회귀가 통과했다. 이 PASS는 여전히
  noncanonical·nonofficial public extension 범위뿐이다.
- Producer-native schema를 그대로 검증하는 별도 native pipeline이 synthetic
  full50와 public projected 결과를 hash-pinned adapter로 결합했다. 팀 정책이
  허용한 synthetic gate 세 곳만 승격했고, public 1×/64×/256×는 독립 표본
  1개인 동일 retiming family로 유지했다. 봉인 결과는 campaign 범위에서
  `A2_PRIMARY`이며 raw/semantic SHA-256은 각각
  `9ffd3219…7963`/`789703a6…593c`다. 전체 campaign 회귀 108개와 별도
  adversarial 보안 회귀 13개가 통과했다.
- Source-level single-posedge CDC/RDC와 RTL source-structure PDK 검사는
  bounded PASS다. Supplied-rotation coordinate software demo도 synthetic 범위
  안에서 PASS다.

## 현재 HOLD

- Hardened synthetic와 public projected 결과는 bounded actual-RTL PASS이며,
  별도 native pipeline에서는 팀 canonical/public campaign 범위의 A2 추천까지
  닫혔다. 다만 이를 release/최종 selection으로 승격하지 않는다. 기존 generic
  campaign-v3 sealed-tuple 경로는 producer-native publication envelope,
  member path, CSV/result schema가 다르므로 계속
  `HOLD_SCHEMA_INCOMPATIBLE_UNBOUND`다. Native pipeline은 별도 버전의
  slot-specific consumer이며 generic v3 자료를 relabel하거나 lossy repack하지
  않는다.
- Organizer-authoritative GPDK045 corner/clock/I/O/load 수치와 mapped cell
  legality가 없다.
- Hardened single-edge A2/A3의 실제 Genus/Innovus P&R, post-route timing,
  DRC/antenna/connectivity 및 mapped vectorless power가 없다.
- Physical/vectorless local 도구는 self-sealed 또는 malformed artifact가
  `GO`가 되지 않도록 HOLD-only다. 이는 실제 EDA 결과가 아니다.
- 최종 A2/A3 선택과 team release는 동일 조건 P&R·power, organizer PDK/I/O
  authority, 보존된 canonical evidence와 선택 interface의 최종 CDC/RDC가
  닫힐 때까지 HOLD다.

현재 설계 판단은 `A2 primary / A3 exact-prefix fallback`이다. A3는 exact
scalar-prefix가 요구되거나 A2 고유 gate가 실패하고 A3가 그 gate를 독립적으로
통과할 때만 대체한다. Shared interface, PDK, CDC/RDC 또는 증거 실패를 A3로
우회하지 않는다.
