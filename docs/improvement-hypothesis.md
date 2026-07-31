# 개선형 AER 설계 가설

## 기준 설계와 문제 가설

비교 기준은 버퍼가 없는 fixed-priority arbiter로 가정한다. 이 구조는 구현이 작지만 다음 문제가 예상된다.

- 낮은 우선순위 source는 높은 우선순위의 연속 요청 때문에 무기한 대기할 수 있다.
- receiver가 멈춘 동안 source가 다음 이벤트를 유지할 수 없다면 이벤트가 누락된다.
- 여러 source의 burst가 한 번에 도착하면 단일 출력의 순간 처리율을 초과한다.
- source 쪽 backpressure가 없거나 저장 공간이 없으면 손실 여부를 인터페이스 밖에서 보장해야 한다.

## 개선 가설

1. 승인된 source 다음부터 검색하는 round-robin 정책은 지속적으로 요청하는 source가 `N`개일 때, receiver가 전송을 계속 수락한다는 조건에서 한 source의 승인 간격을 최대 `N`번의 전송으로 제한한다.
2. source별 FIFO는 source 간 head-of-line blocking을 피하고, 각 source에서 `FIFO_DEPTH`개까지의 미처리 이벤트를 보존한다.
3. 출력 `ready/valid` backpressure와 grant lock을 함께 사용하면 receiver 정지 중에도 `event_valid`, `event_addr`, `event_source`가 안정적으로 유지되어 중복 pop이나 잘못된 source 전환을 막는다.
4. full FIFO가 같은 cycle에 pop될 때 push도 허용하면 포화 상태에서도 불필요한 입력 bubble 없이 source당 최대 1 event/cycle을 받을 수 있다.

FIFO는 무한 burst를 흡수할 수 없다. FIFO가 full이고 해당 source가 같은 cycle에 선택·소비되지 않으면 `src_ready`를 낮춘다. 따라서 이벤트 무손실의 최종 조건은 source가 `src_valid && !src_ready` 동안 주소와 valid를 유지하는 것이다.

## 예상 trade-off와 측정 항목

개선형은 source별 저장 비트와 pointer/count, round-robin 상태 때문에 기준 설계보다 area와 clock power가 증가한다. 대신 burst 손실 가능성, 최악 fairness, receiver backpressure 대응이 개선될 것으로 예상한다.

동일 workload에서 다음을 비교한다.

- 수락 이벤트 수와 출력 이벤트 수 및 순서: 누락·중복 여부
- sustained throughput과 burst 종료 후 drain 시간
- source별 평균·최대 latency와 승인 횟수 분포
- receiver backpressure 비율별 입력 stall 횟수
- 합성 area, power, critical path, 최대 frequency

## 계층형 arbiter 검토

현재 flat round-robin은 모든 request를 한 번에 검색한다. `NUM_SOURCES`가 커져 arbitration mux가 critical path가 되면 source를 소규모 group으로 나누고, group 내부와 group 사이에 각각 round-robin을 적용하는 2단 구조를 검토한다. 다만 계층별 pointer 때문에 flat 방식과 정확히 같은 순서는 아니며, group-level fairness와 grant lock을 별도로 검증해야 한다. source 수와 timing 결과가 확정되기 전에는 면적·검증 부담을 피하기 위해 구현하지 않는다.
