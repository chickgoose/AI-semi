# A3 bubble-free TX experiment

최종 갱신: 2026-08-02

브랜치: `experiment/a3-bubble-free-tx`

## 범위와 가설

이 실험은 fixed-priority arbitration과 baseline RX를 그대로 사용하고 TX refill 동작만
변경한다. 기존 TX는 occupied 상태에서 현재 이벤트가 RX로 전달되는 edge에도 upstream
ready를 내리지 않아 다음 이벤트를 한 cycle 뒤에 받는다. 후보 TX는 다음 조건으로
1-entry register를 elastic하게 운용한다.

```text
event_ready = !tx_full || rx_ready
```

따라서 현재 TX 이벤트의 completion과 새 입력 acceptance가 같은 edge에 발생하면 새
address/source가 기존 TX register를 즉시 대체한다. `rx_ready=0`인 backpressure 동안에는
`event_ready=0`이므로 TX의 valid/address/source는 변하지 않는다.

Baseline RTL은 수정하지 않았다. 후보는 `rtl/experiments/a3_bubble_free/`에 별도 module
이름으로 추가했고 baseline의 `fixed_priority_arbiter`와 `aer_rx`를 직접 재사용했다.

## 로컬 기능 검증

원격 설계 서버에는 접속하거나 파일을 전송하지 않았다. 로컬 Verilator 5.032로 공통
scoreboard/SVA 회귀를 실행했다.

```bash
scripts/run_sim.sh baseline
scripts/run_sim.sh a3-bubble-free
```

모든 workload에서 accepted와 emitted가 같고 errors는 0이었다. Scoreboard는 누락, 중복,
source 내부 reorder와 payload/source corruption을 검사하고, SVA와 scoreboard는 stalled
output의 valid/address/source 안정성을 검사한다.

| Workload | Baseline latency | Candidate latency | Baseline throughput | Candidate throughput | Candidate errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| single | 2.0000 | 2.0000 | 0.492308 | 0.941176 | 0 |
| simultaneous | 2.0000 | 2.0000 | 0.498054 | 0.984615 | 0 |
| burst | 2.0000 | 2.0000 | 0.499220 | 0.993789 | 0 |
| backpressure | 3.5156 | 5.0000 | 0.397516 | 0.397516 | 0 |

Backpressure workload의 output rate는 sink가 제한하므로 두 설계가 같다. 후보 latency가
더 크게 보이는 이유는 baseline이 이벤트를 upstream request 상태에 오래 두는 반면 후보는
TX가 비는 즉시 더 일찍 acceptance하기 때문이다. 후보의 max latency 5 cycle은 baseline과
같고, max request/pending wait는 241에서 240 cycle로 변했다.

공통 backpressure worker는 workload 의미를 유지하면서 Verilator도 실행할 수 있도록
named-fork `disable` 대신 종료 flag와 `join`을 사용하게 바꿨다.

## Initiation interval 전용 비교

`tests/a3/a3_bubble_free_compare_tb.sv`는 baseline과 후보 full DUT에 각각 source 0의
연속 이벤트 64개를 공급한다. 각 input/output handshake cycle과 payload를 독립 queue에
기록한다.

```bash
scripts/run_a3_bubble_free_compare.sh
```

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Accepted/emitted/errors | 64/64/0 | 64/64/0 |
| Average latency | 2.0000 cycles | 2.0000 cycles |
| Input initiation interval | 2 cycles | 1 cycle |
| Output initiation interval | 2 cycles | 1 cycle |
| Post-fill steady throughput | 0.503937 event/cycle | 1.000000 event/cycle |

후보의 event 1~63은 모두 직전 event 다음 cycle에 수락되고 출력됐다. 이 검사는 누락,
중복, reorder, address corruption과 source-sideband corruption 발생 시 즉시 실패한다.

## Register와 mux 구조 비교

로컬 Yosys 0.52에서 `ADDR_WIDTH=16`, `SOURCE_INDEX_WIDTH=2`로 TX module만 `proc; opt;
stat` 처리했다. 이것은 GPDK045/Genus PPA 결과가 아니라 generic RTL 구조 비교다.

| Yosys generic cell | Baseline TX | Candidate TX | 변화 |
| --- | ---: | ---: | ---: |
| State bits | 22 | 22 | 0 |
| Sequential vector cells (`$adff` + `$adffe`) | 5 | 5 | 0 |
| `$mux` | 5 | 2 | -3 |
| `$logic_and` | 0 | 2 | +2 |
| `$logic_or` | 0 | 1 | +1 |
| Total generic cells | 14 | 12 | -2 |

22 state bits은 TX full flag 1, address 16, source 2와 양쪽 설계에 동일하게 존재하는
completion valid/source 3비트다. Core에서 completion 출력은 연결하지 않으므로 full-design
synthesis에서는 이 3비트가 제거될 수 있다. 핵심 비교 결과는 후보가 새 payload storage나
mux bank를 추가하지 않고 ready/refill 제어 논리만 바꾼다는 점이다.

## 결론과 다음 단계

후보는 baseline과 같은 2-cycle latency, 같은 fixed-priority 정책과 같은 저장 용량으로
unconstrained output에서 post-fill 1 event/cycle을 달성했다. 로컬 기능 후보로는 유지할
가치가 있다.

아직 확인하지 않은 항목:

- Genus mapped area, timing/Fmax와 power
- ready 경로가 arbiter 선택 경로에 추가하는 timing 영향
- 공식 AER interface/testbench 및 공식 PVT/SDC/activity 조건

이 브랜치에서는 원격 서버 측정과 main 병합을 수행하지 않는다.
