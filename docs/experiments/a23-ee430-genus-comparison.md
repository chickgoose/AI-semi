# A23 EE430 Genus PPA comparison

## 결론

bounded round-robin fairness가 필요한 최종 후보로는 A23를 추천한다. `NUM_SOURCES=4`에서 A23는 A3보다 면적 10.33%, vectorless power 12.74%를 더 쓰고 추정 Fmax가 10.85% 낮지만, A2의 bounded fairness와 A3의 1 event/cycle 처리율을 함께 제공한다. 순수 PPA만 평가하고 starvation을 허용한다면 A3가 가장 효율적이다.

A23는 baseline 대비 raw area가 10.68%, power가 30.74% 증가한다. 그러나 처리율을 정규화하면 area/event-cycle은 44.66%, power/event-cycle은 34.63% 감소하고 throughput/area는 80.70% 증가한다.

## 비교 고정 조건

| 설계 | 고정 커밋 | 합성 top | 정상상태 처리율 |
|---|---|---|---:|
| baseline | `9c0d044` | `aer_dut` | 0.5 event/cycle |
| A2 round-robin | `856b7f9` | `aer_a2_round_robin_dut` | 0.5 event/cycle |
| A3 bubble-free | `c8f422d` | `a3_bubble_free_dut` | 1.0 event/cycle |
| A23 combined | `57d17e6` | `a23_ee430_dut` | 1.0 event/cycle |

네 top은 이름만 다르고 `clk`, `rst_n`, source별 ready/valid/address, output ready/valid/address/source의 동일 인터페이스 계약을 사용한다. 모두 `ADDR_WIDTH=16`, 동일 `NUM_SOURCES`, 동일 SDC와 라이브러리, Genus medium generic/map/opt effort로 합성했다.

- Genus: `23.14-s090_1`
- Library: `slow_vdd1v0_basicCells.lib`, `PVT_0P9V_125C`, 0.9 V, 125 C
- Clock: 5.000 ns, uncertainty 0.100 ns
- I/O: input/output delay 0.250 ns, output load 0.010 pF
- Power: Genus vectorless estimate; activity-annotated signoff power가 아님
- Fmax: `1 / (5 ns - WNS)`로 계산한 mapped-netlist 추정치
- Cell count: mapped leaf cell을 Liberty의 `ff`/`latch` 정의로 sequential/combinational 분류

전체 고정값과 해시는 [`run-manifest.txt`](../../reports/a23-ee430-genus/run-manifest.txt), 원본 요약값은 [`comparison.tsv`](../../reports/a23-ee430-genus/comparison.tsv)에 있다.

## 대표 결과: NUM_SOURCES=4

| 설계 | Area (um2) | Seq / Comb | WNS (ns) | Fmax (MHz) | Total / Dyn / Leak power (mW) | Throughput |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 432.288 | 38 / 79 | +3.6885 | 762.486 | 0.0535469 / 0.0535374 / 0.000009512 | 0.5 |
| A2 round-robin | 481.946 | 40 / 99 | +3.6454 | 738.225 | 0.0600751 / 0.0600645 / 0.000010674 | 0.5 |
| A3 bubble-free | 433.656 | 38 / 77 | +3.6713 | 752.615 | 0.0620979 / 0.0620884 / 0.000009525 | 1.0 |
| A23 combined | 478.458 | 40 / 95 | +3.5096 | 670.961 | 0.0700068 / 0.0699962 / 0.000010563 | 1.0 |

모든 설계가 5 ns constraint를 여유 있게 만족했다. A23의 가장 느린 경로는 `u_core/u_rx/full_q_reg/CK`에서 `u_core/u_tx/full_q_reg/SE`로 이어지는 RX-to-TX forwarding/control 경로다. 따라서 다음 timing 최적화 우선순위는 bubble-free 교체 경로의 scan-enable/control cone이며, round-robin 선택 경로와 결합될 때의 논리 깊이를 물리 합성에서 다시 확인해야 한다.

## 처리율 정규화: NUM_SOURCES=4

| 설계 | Area / (event/cycle) | Power / (event/cycle) | Throughput / area |
|---|---:|---:|---:|
| baseline | 864.576 | 0.1070938 | 0.001156636 |
| A2 round-robin | 963.892 | 0.1201502 | 0.001037461 |
| A3 bubble-free | 433.656 | 0.0620979 | 0.002305975 |
| A23 combined | 478.458 | 0.0700068 | 0.002090048 |

A23는 A2와 비교하면 raw area가 0.72% 작고 처리율이 두 배여서 throughput/area가 101.46% 높다. A3는 fixed priority라 bounded service를 제공하지 않으므로, 이 PPA 우위만으로 A23의 대체재가 되지는 않는다.

## NUM_SOURCES 확장 확인

| Sources | 설계 | Area (um2) | WNS (ns) | Fmax (MHz) | Total power (mW) |
|---:|---|---:|---:|---:|---:|
| 1 | baseline / A2 | 313.614 | +3.8468 | 867.152 | 0.0396307 |
| 1 | A3 / A23 | 312.930 | +3.6741 | 754.205 | 0.0433658 |
| 3 | baseline | 396.378 | +3.7740 | 815.661 | 0.0496492 |
| 3 | A2 | 437.760 | +3.7184 | 780.275 | 0.0575251 |
| 3 | A3 | 395.352 | +3.6821 | 758.783 | 0.0554797 |
| 3 | A23 | 436.050 | +3.5150 | 673.401 | 0.0662592 |

`NUM_SOURCES=1`에서는 arbitration 선택이 사라져 baseline=A2, A3=A23으로 합성됐다. non-power-of-two인 3에서도 네 설계가 모두 제약을 만족해 parameterization과 wrap 구조의 합성 가능성을 확인했다.

## 품질 검사와 실패 기록

유효한 12개 run 모두 PASS했으며 mapped latch cell, inferred-latch warning, combinational-loop warning, multi-driver warning, unresolved reference, empty module, Error/Fatal line이 각각 0이었다. 대표 critical path 시작점/끝점은 원본 TSV에 모두 보존했다.

첫 시도 `ppa-a23-ee430-20260802-pvt0p9v125c-5ns`는 SDC가 요구하는 환경변수를 runner가 export하지 않아 설계당 SDC-202 오류 15개와 unconstrained timing을 만들었다. 이 run은 결과에서 완전히 제외했다. runner에 고정 clock/reset/I/O 값을 export하고, WNS/Fmax가 `N/A`이거나 Error/Fatal/unresolved가 있으면 FAIL로 판정하도록 수정한 뒤 `validation-n4`와 `supplemental-n1-n3`를 새 디렉터리에서 실행했다.

## 한계와 다음 확인

- power는 vectorless 상대 비교이므로 기능 시뮬레이션 VCD/SAIF 기반 power로 재확인해야 한다.
- Fmax는 현재 5 ns 합성 결과의 WNS에서 역산한 값이며, period sweep이나 place-and-route achievable frequency가 아니다.
- wireload 기반 논리 합성이므로 A23의 RX-to-TX control 경로는 배치·배선 후 악화될 수 있다.
- 기능 RTL은 이 브랜치에서 수정하지 않았고, 생성 netlist와 전체 Genus 작업 디렉터리는 commit하지 않았다.
