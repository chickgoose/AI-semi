# Baseline에서 A23까지의 개선 과정과 정량 비교

최종 갱신: 2026-08-02

이 문서는 fixed-priority baseline에서 출발해 큰 FIFO 기반 개선형을 기각하고,
EE430에서 사용한 arbitration·cycle stealing·forwarding 개념을 저비용으로 다시 적용하여
A23 최종 후보에 도달한 과정을 구조, 기능, 공정성, 처리율, PPA 관점에서 정리한다.

## 1. 출발점: fixed-priority baseline

Baseline datapath는 다음과 같다.

```text
4 sources
  -> combinational fixed-priority arbiter
  -> one-entry TX register
  -> one-entry elastic RX register
  -> output
```

가장 낮은 index의 valid source가 항상 이긴다. 조합 arbiter라 arbitration state가 없고
작고 빠르지만, source 0이 valid를 계속 유지하면 source 1~3은 무기한 기다릴 수 있다.
실제 starvation 전용 테스트에서도 source 0은 20회를 서비스받는 동안 source 3은 0회였다.

Baseline TX의 upstream ready는 `!busy_q`다. TX가 현재 event를 RX로 넘기는 cycle에도
`busy_q`는 아직 1이므로 다음 event를 같은 edge에 받을 수 없다. 따라서 downstream이
항상 ready여도 accept와 complete가 번갈아 발생해 initiation interval이 2 cycle이고
정상상태 상한은 0.5 event/cycle이다.

Baseline의 장점과 한계는 명확하다.

- 장점: 최소 state, 가장 단순한 arbitration, 높은 합성 Fmax, 낮은 raw area/power.
- 한계: bounded fairness 없음, 높은 index source starvation 가능.
- 한계: TX completion과 refill을 같은 edge에 하지 못해 매 event 사이 bubble 발생.
- 한계: burst를 내부에서 별도 흡수하지 않으므로 producer가 ready까지 valid/payload를
  유지해야 한다.

## 2. 첫 개선 시도: source별 FIFO + round-robin

첫 improved 설계는 source별 FIFO와 round-robin을 결합했다. 기능적으로는 starvation을
없애고, 여러 source의 burst를 동시에 받아 저장하며, simulation throughput을 약
1 event/cycle까지 높였다. 그러나 `4 sources x FIFO depth 4`의 payload memory,
read/write pointer, occupancy counter와 arbitration logic이 모두 추가됐다.

| 지표 | Baseline | FIFO round-robin | 변화 |
| --- | ---: | ---: | ---: |
| Cell area | 432.288 um2 | 2805.084 um2 | +548.9% |
| 추정 Fmax | 762.486 MHz | 368.406 MHz | -51.7% |
| Vectorless power | 0.0535469 mW | 0.1757540 mW | +228.2% |

이 구조는 기능과 burst 흡수 능력은 좋았지만 PPA 비용이 너무 컸다. 여기서 얻은 핵심
교훈은 “fairness 자체가 비싼 것이 아니라, fairness를 위해 큰 분산 저장공간까지 항상
추가할 필요는 없다”는 점이다. 이후 개선은 FIFO를 제거하고 필요한 제어 state만 남기는
방향으로 바뀌었다.

## 3. A2: FIFO-free rotating round-robin

A2는 EE430의 DMA cycle-stealing/rotating ownership 아이디어처럼 마지막으로 승인한
source 다음부터 검색한다. 추가 state는 `ceil(log2(NUM_SOURCES))` bit priority pointer
하나뿐이다. N=4에서는 2 bit다.

```text
fixed priority              rotating round-robin
0 -> 1 -> 2 -> 3 우선순위    last grant 다음 source부터 wrap 검색
state 없음                  priority pointer 2 bit 추가
```

pointer는 단순히 clock마다 움직이지 않고 실제 input ready/valid handshake가 발생한
edge에서만 이동한다. downstream stall이나 completion-only cycle에는 고정되므로 event를
받지 않은 source가 차례를 잃지 않는다. downstream이 계속 진행되고 N개 source가 계속
요청한다면 source별 service gap은 최대 N번의 input handshake로 제한된다.

A2 단독 설계는 baseline TX를 유지했기 때문에 bubble은 남고 처리율은 0.5 event/cycle이다.
즉 이 단계는 throughput 개선이 아니라 starvation 제거와 bounded fairness 확보를
독립적으로 검증한 단계다.

| N=4 Genus 지표 | Baseline | A2 | Baseline 대비 |
| --- | ---: | ---: | ---: |
| Area | 432.288 | 481.946 | +11.49% |
| Fmax | 762.486 MHz | 738.225 MHz | -3.18% |
| Power | 0.0535469 mW | 0.0600751 mW | +12.19% |
| Throughput | 0.5 | 0.5 | 변화 없음 |
| Throughput/area | 0.001156636 | 0.001037461 | -10.30% |

공정성을 pointer 2 bit와 선택 logic으로 얻었지만 bubble이 그대로라 PPA 정규화 이득은
없었다. 따라서 A2만으로는 최종안이 되지 않고 A3의 datapath 개선과 결합해야 했다.

## 4. A3: bubble-free TX forwarding

A3는 EE430 CPU pipeline의 forwarding과 DMA cycle stealing 관점으로 TX의 완료 cycle을
다음 event capture에도 재사용한다.

```text
Baseline TX ready = !full
A3 TX ready       = !full || downstream_ready
```

RX가 현재 TX event를 받는 edge에 다음 input을 TX register로 바로 refill한다. completion과
refill이 동시에 발생하면 refill이 full/address/source state 갱신에서 우선한다. 별도 FIFO나
payload register를 추가하지 않고 기존 TX register의 빈 cycle을 제거한 것이다.

결과적으로 pipeline fill 뒤 input과 output handshake가 매 cycle 가능해졌다.

- initiation interval: 2 -> 1 cycle, 50% 감소.
- 정상상태 throughput: 0.5 -> 1.0 event/cycle, 100% 증가.
- TX/RX payload storage 개수: 변화 없음.
- arbitration: fixed priority 유지, 따라서 starvation 문제는 아직 남음.

| N=4 Genus 지표 | Baseline | A3 | Baseline 대비 |
| --- | ---: | ---: | ---: |
| Area | 432.288 | 433.656 | +0.32% |
| Fmax | 762.486 MHz | 752.615 MHz | -1.29% |
| Power | 0.0535469 mW | 0.0620979 mW | +15.97% |
| Throughput | 0.5 | 1.0 | +100% |
| Area/event-cycle | 864.576 | 433.656 | -49.84% |
| Power/event-cycle | 0.1070938 | 0.0620979 | -42.02% |
| Throughput/area | 0.001156636 | 0.002305975 | +99.37% |

A3는 raw power가 증가하지만 거의 같은 면적과 Fmax에서 처리율을 두 배로 만들어 순수
throughput-normalized PPA가 가장 좋다. 다만 fixed priority이므로 공정성 목표를 만족하는
최종안으로 단독 채택할 수는 없었다.

## 5. A23: A2 fairness + A3 throughput

A23는 두 독립 개선을 다음 순서로 결합한다.

```text
sources
  -> FIFO-free rotating round-robin       (A2: bounded fairness)
  -> bubble-free one-entry TX             (A3: same-edge refill)
  -> baseline one-entry elastic RX
  -> output
```

큰 FIFO, quota, aging, predictor, event cache는 추가하지 않았다. baseline과 A23의 유효
TX/RX storage는 38 bit로 동일하고 A23은 N=4에서 priority pointer 2 bit만 더해 40 bit다.
공용 interface의 합성 전 unconnected completion register까지 세면 41 -> 43 bit다.

Genus mapped 결과에서도 sequential cell은 38 -> 40개(+5.26%), combinational cell은
79 -> 95개(+20.25%), 전체 leaf cell은 117 -> 135개(+15.38%)였다. cell 종류와 크기가
달라 실제 cell area 증가는 10.68%였다.

### 5.1 기능과 공정성 개선

| 항목 | Baseline | A23 | 개선 결과 |
| --- | ---: | ---: | --- |
| 정상상태 input/output II | 2 | 1 | 50% 감소 |
| 정상상태 throughput | 0.5 | 1.0 | 100% 증가 |
| 지속 경합 service bound | 없음 | `<= NUM_SOURCES` | starvation 제거 |
| N=4 측정 service gap | 무한 가능 | 4 | bounded |
| 정상 downstream latency | 2 cycles | 2 cycles | 유지 |
| 유효 TX/RX payload storage | 38 bit | 38 bit | 증가 없음 |
| arbitration state | 0 bit | 2 bit | 최소 state 추가 |

N=1/3/4에서 측정 service gap은 정확히 1/3/4였고, all-source contention의 Jain fairness는
1.0이었다. 단, Jain fairness만으로 baseline starvation을 판별할 수는 없다. finite test가
끝날 때 각 source의 총 출력 수가 같아도 중간 대기시간은 매우 길 수 있으므로 service gap과
max wait를 함께 봐야 한다.

### 5.2 실제 workload 비교

| Workload | Baseline throughput | A23 throughput | 변화 | Baseline max wait | A23 max wait | 대기 감소 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single | 0.492308 | 0.941176 | +91.18% | 1 | 1 | 0% |
| simultaneous | 0.498054 | 0.984615 | +97.69% | 192 | 3 | 98.44% 감소 |
| burst | 0.499220 | 0.993789 | +99.07% | 576 | 3 | 99.48% 감소 |
| backpressure | 0.397516 | 0.397516 | 동일 | 241 | 9 | 96.27% 감소 |

single/simultaneous/burst의 throughput이 정확히 2배가 아닌 것은 pipeline fill/drain cycle을
측정 window에 포함했기 때문이다. 긴 steady-state stream에서는 정확히 1 event/cycle을
확인했다. backpressure workload는 sink duty cycle이 병목이므로 두 설계의 출력 throughput이
같다. 그 경우에도 A23은 producer event를 더 일찍 TX/RX에 받아 max wait를 크게 줄였다.

Backpressure 평균 latency는 baseline 3.5156 cycle에서 A23 5.0 cycle로 늘었다. 이는 A23이
event를 늦게 받는 대신 빨리 accept하여 내부에서 sink를 기다리기 때문이다. 최대 latency는
둘 다 5 cycle이었다. 즉 입력 수용 관점은 개선됐지만 sink가 막힌 시간을 없앤 것은 아니다.

### 5.3 PPA와 처리율 정규화

조건은 Genus 23.14-s090_1, `slow_vdd1v0_basicCells.lib`, `PVT_0P9V_125C`, 5 ns clock,
동일 I/O delay/load, medium effort다.

| N=4 지표 | Baseline | A2 | A3 | A23 |
| --- | ---: | ---: | ---: | ---: |
| Area (um2) | 432.288 | 481.946 | 433.656 | 478.458 |
| Fmax 추정 (MHz) | 762.486 | 738.225 | 752.615 | 670.961 |
| Total power (mW) | 0.0535469 | 0.0600751 | 0.0620979 | 0.0700068 |
| Throughput | 0.5 | 0.5 | 1.0 | 1.0 |
| Area/event-cycle | 864.576 | 963.892 | 433.656 | 478.458 |
| Power/event-cycle | 0.1070938 | 0.1201502 | 0.0620979 | 0.0700068 |
| Throughput/area | 0.001156636 | 0.001037461 | 0.002305975 | 0.002090048 |

A23를 baseline과 비교하면:

- raw area: +10.68%.
- vectorless power: +30.74%.
- 추정 Fmax: -12.00%.
- throughput: +100%.
- area/event-cycle: -44.66%, 즉 처리 event 기준 면적 효율 개선.
- power/event-cycle: -34.63%, 즉 처리 event 기준 power 효율 개선.
- throughput/area: +80.70%.

A23를 A3와 비교하면 fairness의 비용은 area +10.33%, power +12.74%, 추정 Fmax
-10.85%, throughput/area -9.36%다. A3가 PPA만 보면 가장 좋지만, 이 비용을 지불함으로써
A23는 fixed-priority starvation을 bounded round-robin service로 바꾼다.

A23를 A2와 비교하면 area는 오히려 0.72% 작고 throughput은 두 배다. power는 16.53%
증가하고 추정 Fmax는 9.11% 낮지만, throughput/area는 101.46% 높다. 즉 A2의 fairness
state가 이미 존재하는 상황에서는 bubble-free refill을 결합하는 편이 훨씬 효율적이다.

## 6. 검증 강도 개선

설계만 개선한 것이 아니라 검증 환경도 baseline smoke 수준에서 크게 확장했다.

- 공통 scoreboard: missing, duplicate, source-local reorder, address/source corruption.
- 전역 FIFO scoreboard: source 사이의 global reorder까지 검사.
- accepted-emitted와 물리 TX+RX occupancy 일치, 범위 0~2 검사.
- grant onehot0 및 priority가 실제 input handshake 때만 변하는지 검사.
- random valid/address/backpressure, alternating ready, unequal burst 검사.
- 30~96 cycle full stall 후 drain/refill, full 상태 triple handshake 검사.
- reset flush, valid-held reset release, 첫 post-reset handshake 검사.
- Icarus와 Verilator 교차검증, N=1/3/4 parameter 검증.

최종 qualification 결과는 functional 18/18, stress 120/120, 기존 stream/contention 6/6,
Genus 12/12 PASS다. stress는 960개 phase 실행에서 FAIL/FATAL 0이었다.

## 7. 최종 해석

A23는 모든 절대 지표가 baseline보다 좋아진 설계가 아니다. raw area, raw power와 추정
Fmax는 손해를 본다. 대신 대회 과제에서 설명 가능한 두 문제를 최소 state로 직접 해결했다.

1. fixed priority starvation -> handshake 기반 rotating round-robin으로 bounded fairness.
2. TX pipeline bubble -> same-edge forwarding/refill로 1 event/cycle.

따라서 발표에서는 “PPA를 희생해 기능을 추가했다”보다 다음과 같이 설명하는 것이 정확하다.

> 큰 FIFO 기반 개선형의 PPA 실패를 분석한 뒤, 저장공간을 늘리는 대신 EE430의
> arbitration, DMA cycle-stealing, pipeline forwarding 원리를 적용했다. 그 결과 payload
> storage 증가는 없이 priority state 2 bit만 추가하여 starvation bound를 확보하고,
> steady-state throughput을 두 배로 높였다. raw PPA 비용을 포함해도 throughput-normalized
> area 효율은 44.66%, power 효율은 34.63%, throughput/area는 80.70% 개선됐다.

## 8. 아직 확정할 수 없는 부분

- power는 vectorless 추정이므로 VCD/SAIF 기반 결과가 아니다.
- Fmax는 5 ns 합성 WNS 역산값이며 period sweep 또는 post-route 결과가 아니다.
- 공식 AER interface, reset 계약, workload, PVT와 점수 가중치가 나오면 순위가 달라질 수
  있다.
- reset 중 ready가 1인 현재 동작은 기능 오류가 아니라 미확정 interface 계약 문제다.
- shared 1~2 entry buffer는 공식 burst workload에서 가치가 확인될 때만 새 브랜치에서
  A23와 비교한다.

상세 원자료:

- [A23 구조 및 기능 비교](a23-ee430-core.md)
- [Genus PPA 비교](a23-ee430-genus-comparison.md)
- [최종 후보 qualification](a23-final-candidate.md)
- [독립 stress 검증](../verification/a23-stress-report.md)
