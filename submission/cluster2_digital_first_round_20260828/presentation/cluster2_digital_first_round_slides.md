# Cluster2 Polarity AER

디지털 1차 설계 결과 · 2026-08-28

최종 top: `aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity`

---

## 1. 제출 범위와 결론

- RTL, functional verification, synthesis, timing sweep, area, power, 동작 주파수
- 3.5 ns / 285.714 MHz에서 post-route setup·hold 통과
- functional replay: generated=delivered=8,503
- polarity를 포함한 최종 RTL과 PPA top을 일치시킴
- exact Fmax와 workload-activity power는 후속 범위

---

## 2. 설계 구조

- 16개 event source
- source별 depth-2 event/polarity storage
- 중심/주변 두 개의 row-bitmap retire lane
- cycle당 최대 8 event 표현
- polarity는 선택된 `col_mask` bit에 대응하는 `pol_mask`로 전달

---

## 3. RTL 및 기능 검증

- Xcelium 23.09-s013
- generated 8,503 / delivered 8,503 / overrun 0
- phantom 0 / duplicate 0 / drain-empty true
- raw trace와 cycle ledger를 독립 verifier로 보존성 재검증
- event identity는 TB sidecar이며 DUT payload가 아님

---

## 4. 합성 조건

- Genus 23.14-s090_1
- GPDK045 slow, 0.9 V, 125 °C
- clock uncertainty 0.100 ns
- input/output delay 0.250 ns, output load 0.010
- 3.5 ns mapped area 1156.644 / 544 cells
- Genus vectorless power 0.0505898 mW

---

## 5. Timing 최적화 sweep

| Period | Frequency | Setup | Hold | 판정 |
| ---: | ---: | ---: | ---: | --- |
| 4.5 ns | 222.222 MHz | +1.349 ns | +0.166 ns | PASS |
| 4.0 ns | 250.000 MHz | +0.849 ns | +0.166 ns | PASS |
| 3.5 ns | 285.714 MHz | +0.454 ns | +0.167 ns | PASS |
| 3.0 ns | 333.333 MHz | -0.004 ns | +0.169 ns | FAIL |

검증 동작점은 285.714 MHz이며 exact Fmax는 미확정이다.

---

## 6. Post-route area와 layout

- 3.5 ns: 596 instances, area raw 1254.114
- 3.0 ns: 599 instances, area raw 1261.980
- target utilization 0.5, aspect ratio 1.0
- internal DRC 0, antenna 0
- area report에 물리 단위가 명시되지 않아 `area raw`로 표기

---

## 7. Power

- 3.5 ns post-route total: 0.10738887 mW
- internal 0.07647953 mW
- switching 0.03088277 mW
- leakage 0.00002657 mW
- sequential/primary-input activity 0.2의 vectorless estimate
- VCD/SAIF workload power 또는 energy/event가 아님

---

## 8. 근거 한계

- timing: slow view 하나, Non-OCV, SI off
- check_timing: ideal-clock warning 1, no-drive warning 34
- power: activity file 없음
- report header: No SPEF/RCDB
- Innovus `write_db` 종료 오류가 있으나 앞선 reports/GDS는 생성됨
- foundry signoff, LVS/ERC/IR/EM, silicon measurement를 주장하지 않음

---

## 9. 최종 결론

- polarity 포함 RTL 기능 검증 완료
- 285.714 MHz clean operating point 확보
- synthesis/P&R area·timing·power 원본 report 봉인
- 333.333 MHz에서 첫 setup fail 관측
- 제출 가능한 1차 디지털 결과이며 exact Fmax와 activity-based power는 후속 과제
