# Cluster2 Steal-Buffer Polarity AER

디지털 1차 설계 결과 · 2026-08-28

## 1. AER 손실을 24.42배 줄인 구조

- 기본 Cluster2: 11.52% loss
- conditional steal + source별 depth-2 FIFO
- Cluster2 steal_buf: 0.47% loss
- 공식 50-workload 동일 106,416-event 기준

## 2. Ryu의 전통 AER 6문제에 대한 대응

1. 주소 오버헤드: 부분 대응, row bitmap; 2차 repeat-flag 후보
2. 공유채널 대역폭: N=16에서 두 lane, 최대 8 events/cycle
3. 중재 지연: row batching과 병렬 retire로 감소
4. 중재 불공정: 분리 arbiter와 conditional steal로 감소
5. timestamp 왜곡: 고부하 해법 미완료, HOLD
6. motion artifact: 같은 row/다른 lane 동시성은 보존, 고부하 HOLD

## 3. 기본 Cluster2와 steal_buf

- 기본 Cluster2는 source당 pending 1개여서 빠른 재발화가 overrun으로 이어짐
- steal_buf는 source별 depth-2 FIFO로 두 번째 event를 흡수
- idle lane을 반대 class가 조건부로 빌려 traffic imbalance를 완화
- 12,259 loss → 502 loss

## 4. 최종 RTL 구조

- 16 source × depth-2 event/polarity FIFO
- center/peripheral row-bitmap retire lane 두 개
- cycle당 최대 8 events
- 출력: `row + col_mask[3:0] + pol_mask[3:0]`

## 5. 병목별 RTL 메커니즘

- 직렬화 → two row-bitmap lanes
- source 재발화 → depth-2/source
- class 쏠림 → conditional lane steal
- 주소/polarity 분리 → lockstep polarity FIFO

## 6. 공식 50-workload 정량 비교

| 구조 | 손실률 | 손실 건수 |
| --- | ---: | ---: |
| Fovea | 26.49% | 28,187 |
| Cluster2 | 11.52% | 12,259 |
| Cluster2 + steal | 10.89% | 11,593 |
| Cluster2 + steal_buf | 0.47% | 502 |

Cluster2 대비 손실 건수 24.42배 감소, 상대 감소율 95.9%.

## 7. 실제 시뮬레이터와 TB

- Simulator: Xcelium 23.09-s013
- full50 비교: `tb_steal_buf_trace_phantom_debug.v`, 50/50 PASS
- 최종 polarity: `redred_cluster2_polarity_v1_native_observational_tb.sv`
- trace → source FIFO → arbiter/steal → row/col/pol retire → raw cycle ledger
- Python 독립 verifier: generated 8,503 = delivered 8,503 + overrun 0
- phantom/duplicate/polarity mismatch 0, drain empty

## 8. 물리 구현

- GPDK045 slow, 0.9 V, 125 °C
- 3.5 ns / 285.714 MHz setup +0.454 ns, hold +0.167 ns
- P&R area raw 1254.114, 596 instances
- vectorless power 0.10738887 mW
- 3.0 ns / 333.333 MHz setup −0.004 ns

## 9. CAV 확장성

- legacy address-only track: 8,503/8,503 exact join
- WORLD 8,420, SENSOR_FIXED 83
- 512×256 grid의 821 cells
- polarity full replay, CAV RTL과 CAV PPA는 HOLD

## 10. 2차 확장 계획

- steal_buf + repeat-flag: 검증 후보 link bits −15.61%
- polarity→CAV full replay와 wire-complete RTL
- timestamp jitter 해법, VCD/SAIF power, exact Fmax
- row-trim −14.29%는 기본 Cluster2 전용이며 steal_buf에는 적용 불가
