# Cluster2 Steal-Buffer Polarity AER

디지털 1차 설계 결과 · 2026-08-28

최종 top: `aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity`

발표의 중심 문장:

> 전통 scalar AER의 동시성·재발생·class 불균형 문제를 row bitmap,
> source-local depth-2, conditional steal로 완화하고 polarity를 lockstep으로
> 보존했다. 구조군 full50에서는 기본 Cluster2 대비 source-overrun을 95.9%
> 줄였고, 별도 최종 polarity-v1 UZH trace와 물리 구현까지 검증했다.

---

## 1. 병목을 줄이고 의미를 보존하는 AER

- AER 병목: 직렬 출력, 같은 source 재발생, class capacity 불균형
- 제안 구조: row bitmap, source-local depth-2, conditional lane steal
- payload 의미: address와 polarity를 같은 slot에서 push/pop
- full50 구조군 결과: source-overrun 0.47%
- 최종 polarity-v1 UZH 결과: 8,503 / 8,503
- 최종 top 물리 관측: 285.714 MHz fastest tested PASS

full50 106,416-event 구조군 비교와 UZH 8,503-event 최종 top 검증은 서로 다른
evidence population이다.

---

## 2. Ryu의 문제 흐름을 여섯 요구사항으로 분해

이는 Ryu 논문의 문제 흐름을 팀이 발표용 여섯 항목으로 분해한 것이며, 논문이
그대로 번호를 붙인 여섯 항목이라고 주장하지 않는다.

| 요구사항 | 이번 설계의 대응 | 상태 |
| --- | --- | --- |
| 주소 overhead | group bitmap으로 주소를 집약, link codec은 후속 | 부분 |
| bandwidth/serialization | 두 row-bitmap lane, depth-2, steal | 직접 완화 |
| arbitration latency | column별 serial 선택 대신 grouped retire | 직접 완화 |
| unfair arbitration | rotating arbiter와 idle-lane reuse | 구조 대응; 별도 fairness metric 없음 |
| source/readout timestamp mismatch | timestamp가 DUT payload에 없음 | HOLD |
| motion artifact | sensor/system-level 대응 미구현 | HOLD |

`직접 완화`는 선택된 구현 범위의 대응 메커니즘이 있다는 뜻이지, 모든 traffic과
backpressure에서 문제를 제거했다는 뜻은 아니다. polarity integrity는 Ryu 여섯
항목이 아니라 이번 프로젝트가 추가한 payload 요구사항이다.

---

## 3. scalar 출력이 loss를 만드는 과정

동시 입력 → 하나의 공유 arbitration → 한 cycle 한 event 서비스 → 대기 누적 →
grant 전 같은 source 재발생 → local full → source-overrun으로 이어진다.

common full50 106,416 offered events에서 scalar Fovea 회수 진단값:

- delivered after drain: 78,229
- source-overrun: 28,187, loss rate 26.49%
- fixed-window EPC: 0.673901

따라서 출력 표현 폭, source-local 흡수 능력, class capacity 활용을 함께 풀어야 한다.

---

## 4. 최종 통합 구조

```text
16 arrival/polarity sources
  → source별 pending count 2-entry capacity + 2 polarity slots
  → center/peripheral rotating arbitration + conditional steal
  → 2 × {valid, row, col_mask[3:0], pol_mask[3:0]}
```

- row bitmap: 선택된 한 row의 최대 네 columns를 한 lane transaction으로 표현
- 두 lane: 구조적 peak representation capacity 최대 8 events/cycle
- depth-2: grant 전에 재발생한 두 번째 occurrence 흡수
- conditional steal: 한 class가 idle이면 다른 class가 그 lane capacity 재사용
- polarity lockstep: `col_mask[col]=1`인 selected event의 polarity만 의미가 있음

주소는 source index에 내재하므로 full address FIFO가 아니라 pending count와 polarity
slot이 source별 상태다.

---

## 5. Bitmap과 depth-2의 역할

### Row bitmap

같은 row의 columns 0–3이 동시에 선택되면 네 scalar 주소를 반복하지 않고
`row + col_mask=4'b1111`로 표현한다. 한 lane transaction이 최대 네 events를
나타내므로 per-column serial arbitration 부담을 줄인다.

### Source-local depth-2

첫 event A가 grant를 기다리는 동안 같은 source에서 B가 재발생해도 두 번째
slot에 저장한다. 따라서 출력 폭 개선과 local 재발생 흡수가 서로 다른 원인을
동시에 완화한다.

---

## 6. Conditional steal과 polarity lockstep

### Conditional lane steal

center/peripheral 중 한 class가 idle이고 다른 class에 pending traffic이 있으면 idle
lane capacity를 재사용한다. 이는 class-capacity 낭비를 줄이는 메커니즘이며,
global fairness 개선율이나 starvation bound를 별도로 측정했다는 뜻은 아니다.

### Polarity lockstep

주소 선택과 polarity를 같은 source slot에서 push/pop한다. retire 시
`col_mask`에서 선택된 column에 대응하는 `pol_mask` bit만 event 의미를 가진다.
최종 UZH 검증에서 selected-event polarity mismatch는 0이었다.

---

## 7. 주소 overhead 확장

최종 제출 RTL은 `row + col_mask + pol_mask` 방식이다. 아래 수치는 별도
address-only codec 실험이며 최종 polarity link 절감률이 아니다.

- row-trim: 기본 non-stealing Cluster2에서 address bits 14.29% 감소
  - steal이 lane의 reachable row를 확장하므로 현재 decoder를 steal_buf에 적용하면
    row가 손상될 수 있다.
- repeat-flag: steal_buf address tuple 실험에서 bits 15.61% 감소
  - 최종 polarity link에 통합하려면 address-only repeat 또는 whole-tuple repeat
    grammar를 정하고 polarity·reset·동기화를 end-to-end 재검증해야 한다.

따라서 2차 통합 후보는 repeat-flag이고 row-trim은 기본 Cluster2 전용 결과다.

---

## 8. full50 source-overrun 개선

같은 50-workload, 같은 106,416 offered-event denominator:

| 구조군 evidence | Delivered after drain | Source-overrun | Loss rate |
| --- | ---: | ---: | ---: |
| scalar Fovea | 78,229 | 28,187 | 26.49% |
| base Cluster2 | 94,157 | 12,259 | 11.52% |
| non-polarity Cluster2 steal_buf | 105,914 | 502 | 0.47% |

기본 Cluster2 → steal_buf:

- source-overrun 12,259 → 502
- 95.9% relative reduction
- 남은 loss count는 기본 Cluster2의 1/24.42
- 48/50 traces는 overrun 0
- worst trace는 497/9,228, 약 5.39%

이는 architecture-family diagnostic이다. full50 steal_buf는 non-polarity top이고,
final polarity-v1 UZH 결과와 하나의 head-to-head campaign으로 합치지 않는다.

---

## 9. Xcelium TB와 실제 retire cycle

- simulator: Xcelium 23.09-s013
- TB: `redred_cluster2_polarity_v1_native_observational_tb.sv`
- input: UZH `shapes_rotation`의 4×4 patch, 1 ms bins, 8,503 events
- raw cycle ledger를 별도 verifier가 다시 해석

실제 cycle 4162:

```text
lane0: valid=1 row=2 col_mask=0x6 pol_mask=0x0 → 2 selected events
lane1: valid=1 row=0 col_mask=0x1 pol_mask=0x1 → 1 selected event
합계: 3 events retire
```

최종 결과:

- generated 8,503 = delivered 8,503 + overrun 0
- phantom 0, duplicate 0, selected-event polarity mismatch 0
- drain 후 empty

이는 selected source/polarity sequence의 기능 보존 증거다. 모든 traffic에서
무손실이거나 동일 source·동일 polarity event의 독립 ID 순서를 입증한 것은 아니다.

---

## 10. Synthesis·Timing·Area·Power

환경: Genus 23.14-s090_1, Innovus 23.14-s088_1, GPDK045 slow 0.9 V,
125 °C. 각 period에서 Genus generic→map→opt와 Innovus place→CTS→route를
별도로 수행했다.

- 3.5 ns Genus mapped: area raw 1156.644, 544 cells
- 3.5 ns Innovus: area raw 1254.114, 596 instances
- 285.714 MHz: setup +0.454 ns, hold +0.167 ns PASS
- 333.333 MHz: setup −0.004 ns로 첫 faster FAIL
- post-route vectorless power 0.10738887 mW, default activity 0.2
- internal DRC 0, antenna 0

285.714 MHz는 fastest tested passing observation이지 exact Fmax가 아니다.
area 단위는 report에 명시되지 않았으며 power는 workload VCD/SAIF power가 아니다.
단일 slow Non-OCV view, SI off, no SPEF/RCDB인 internal flow로 signoff 주장이 아니다.

---

## 11. CAV software 확장성

최종 polarity-v1과 별개인 sealed legacy address-only software path:

```text
UZH events + pose
  → TB-side observational identity join 8,503 / 8,503
  → causal-CAV occurrence-time geometry
  ├─ WORLD 8,420 → 512×256 grid, 821 cells
  └─ SENSOR_FIXED bypass 83
```

event ID는 RTL payload로 전달된 값이 아니다. geometry는 occurrence time을 쓰고
retire cycle은 latency sidecar로 보존한다. 입증 범위는 software functional
extension feasibility이며 polarity→CAV replay, wire-complete CAV RTL/PPA,
CAV 정확도 향상은 HOLD다.

---

## 12. 결론과 2차 과제

1차 결과:

- 구조: row bitmap + depth-2 + conditional steal + polarity lockstep
- full50 구조군: base Cluster2 대비 source-overrun 95.9% 감소
- 최종 polarity-v1: UZH 4×4 patch 8,503 / 8,503, selected polarity mismatch 0
- 물리 구현: 285.714 MHz fastest tested PASS
- 별도 CAV software path: 8,503 events 전수 분기

2차 과제:

1. repeat-flag를 polarity grammar와 함께 최종 top에 통합·재검증
2. source timestamp를 포함한 polarity→CAV full replay
3. wire-complete CAV RTL과 동일 boundary PPA
4. workload activity power와 energy/event
5. exact Fmax 탐색 및 signoff 수준 corner 확대

row-trim은 기본 Cluster2 전용으로 유지하며 steal_buf에 직접 적용하지 않는다.
