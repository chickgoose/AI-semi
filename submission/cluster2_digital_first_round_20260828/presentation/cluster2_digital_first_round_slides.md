# Cluster2 Steal-Buffer Polarity AER

디지털 1차 설계 결과 · 2026-08-28
최종 top: `aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity`

발표의 중심은 기존 AER 병목을 여러 RTL 메커니즘으로 완화하고, 기능·물리
구현·CAV 확장 가능성을 함께 검증한 과정이다. steal-buffer 비교는 이 전체
흐름 안에서 source 재발생과 traffic 불균형을 해결한 핵심 정량 근거다.

---

## 1. 전체 이야기

- AER의 병목을 출력 직렬화, source 재발생, traffic 불균형, polarity 정렬로 구체화
- depth-2 FIFO, row bitmap, conditional steal, polarity lockstep을 각각 대응
- 최종 polarity RTL은 UZH trace 8,503개를 손실·polarity mismatch 없이 전달
- 285.714 MHz post-route clean point와 CAV software 확장 경로까지 확인

---

## 2. AER 병목 정의

Ryu가 정리한 여섯 한계는 주소 overhead, bandwidth, arbitration latency,
unfairness, timestamp, motion artifact다. 이번 1차 RTL은 이 중 bandwidth,
arbitration latency, unfairness를 직접 개선하고 주소 overhead와 motion
artifact를 부분 대응했다. timestamp 보존은 후속 범위다.

이를 구현 관점에서 네 병목으로 구체화했다.

1. 여러 source가 동시에 발생하지만 출력은 직렬화된다.
2. grant 전에 같은 source가 재발생하면 local full로 overrun이 생긴다.
3. center/peripheral traffic이 한쪽에 몰리면 고정 lane 처리력이 논다.
4. 주소와 polarity를 따로 저장·pop하면 event 의미가 어긋날 수 있다.

---

## 3. 기존 scalar Fovea에서 손실이 생기는 과정

- 동시 입력이 공유 arbitration의 한 출력 순서를 기다린다.
- full50 106,416 events 중 78,229 accepted, 28,187 overrun
- fixed-window throughput 0.673901 EPC
- 발생률이 서비스율을 넘으면 직렬 출력이 source-local loss로 연결된다.

---

## 4. 제안 구조

- 16개 source마다 depth-2 event/polarity FIFO
- center/peripheral 두 row-bitmap retire lane
- 선택된 한 row에서 최대 4개 column을 bitmap으로 표현
- 두 lane 합계 cycle당 최대 8 events retire
- 출력 계약: `row + col_mask[3:0] + pol_mask[3:0]`

---

## 5. 병목별 RTL 해법

| 병목 | RTL 해법 | 직접 효과 |
| --- | --- | --- |
| 1 event/cycle | 두 row-bitmap lane | 최대 8 events/cycle |
| source 재발생 | source별 depth-2 FIFO | 두 번째 event 흡수 |
| class 쏠림 | conditional lane steal | idle 처리력 재사용 |
| address–polarity 분리 | lockstep FIFO | polarity mismatch 방지 |

주소 overhead 후속안은 별도다. row-trim은 기본 Cluster2에서 link bit
14.29%를 줄이지만 steal_buf의 확장 row 범위에는 안전하게 적용할 수 없다.
repeat-flag는 steal_buf에 적용 가능하며 link bit 15.61% 감소를 확인했다.

---

## 6. 정량 개선

동일한 공식 full50 106,416-event 기준:

- scalar Fovea → 기본 Cluster2: accepted 78,229 → 94,157 (+20.4%)
- scalar Fovea → 기본 Cluster2: overrun 28,187 → 12,259 (−56.5%)
- scalar Fovea → 기본 Cluster2: EPC 0.6739 → 0.8116 (+20.4%)
- 기본 Cluster2 → steal_buf: loss 12,259 → 502
- loss rate 11.52% → 0.47%, 손실 수 기준 24.42배 감소

마지막 비교는 architecture-family full50 결과이며, 다음 장의 최종 polarity-v1
UZH 8,503-event 검증과 분모를 섞지 않는다.

---

## 7. 실제 RTL 시뮬레이션

- simulator: Xcelium 23.09-s013
- TB: `redred_cluster2_polarity_v1_native_observational_tb.sv`
- UZH trace: generated 8,503 / delivered 8,503 / overrun 0
- phantom 0 / duplicate 0 / polarity mismatch 0 / drain-empty true
- cycle 4162에서 두 lane이 네 events를 동시에 retire하는 실제 ledger 확인
- raw trace와 cycle ledger를 독립 verifier로 재검증, order violations 0

---

## 8. 합성·P&R·Timing·Power

- Genus 23.14-s090_1, Innovus, GPDK045 slow 0.9 V 125 °C
- 3.5 ns / 285.714 MHz: setup +0.454 ns, hold +0.167 ns PASS
- 3.0 ns / 333.333 MHz: setup −0.004 ns로 첫 faster FAIL
- post-route area raw 1254.114, 596 instances
- post-route vectorless power 0.10738887 mW, default activity 0.2
- internal DRC 0 / antenna 0

285.714 MHz는 fastest tested PASS이지 exact Fmax가 아니다. power도
VCD/SAIF workload power가 아니라 vectorless estimate다.

---

## 9. CAV 확장성

주소-only legacy bridge에서 UZH events와 pose를 exact identity로 join했다.

- input/join: 8,503 / 8,503
- WORLD: 8,420 → 512×256 grid, 821 cells
- SENSOR_FIXED bypass: 83
- geometry는 occurrence time, retire cycle은 latency sidecar로 보존

이는 CAV software functional extension 검증이다. 최종 polarity-v1과 독립된
경로이며 CAV RTL/PPA를 주장하지 않는다.

---

## 10. 결론과 2차 과제

- 네 RTL 병목을 FIFO, bitmap, steal, polarity lockstep으로 각각 완화
- 최종 RTL에서 8,503 / 8,503 보존, 285.714 MHz post-route PASS
- 별도 CAV software 경로에서 8,503 events 전수 분기 검증

2차 과제는 repeat-flag 통합, polarity→CAV full replay와 CAV RTL화,
activity-based power, exact Fmax 탐색이다. row-trim은 기본 Cluster2 전용으로
유지하며 steal_buf에 직접 적용하지 않는다.
