# A23 EE430 FIFO-free round-robin + bubble-free TX core

검증일: 2026-08-02

브랜치: `integration/a23-ee430-core`

기준과 입력 실험:

- `main`: `9c0d044`
- A2 FIFO-free round-robin: `856b7f9`
- A3 bubble-free TX: `c8f422d`

## 목적과 범위

A2에서 검증한 bounded-fair rotating priority와 A3에서 검증한 same-edge TX refill을
하나의 최소-state AER datapath로 결합한다.

```text
sources
  -> FIFO-free rotating round-robin arbiter
  -> bubble-free one-entry TX register
  -> baseline one-entry elastic RX
  -> output
```

A2/A3 커밋 전체를 cherry-pick하지 않고 검증된 arbiter와 TX 의미만 새 module 이름으로
옮겼다. `rtl/baseline/**`는 수정하지 않았고 `aer_rx`는 복사하지 않고 filelist에서 baseline
모듈을 직접 재사용한다. quota, aging, source별/shared FIFO는 추가하지 않았다.

이 단계는 로컬 RTL 기능 통합이다. 원격 설계 서버 배포, Genus 합성/PPA, `main` merge와
push는 수행하지 않았다.

## EE430 동작 대응

### Rotating arbitration

`priority_q`는 다음 TX refill 기회를 먼저 볼 source를 나타낸다. 조합 arbiter는
`priority_q`부터 높은 index까지 검색한 뒤 0으로 wrap한다. 3-source처럼 source 수가
2의 거듭제곱이 아닌 경우에도 존재하는 source만 검색한다.

priority는 `grant_valid && tx_event_ready`, 즉 실제 input handshake가 발생한 edge에서만
선택 source 다음으로 이동한다. downstream completion만 발생했거나 stall된 cycle에는
priority를 갱신하지 않는다.

### Cycle-stealing / forwarding

TX ready는 다음과 같다.

```text
tx_input_ready = !tx_full || rx_ready
```

RX가 현재 TX event를 받는 edge에 arbiter의 다음 event를 TX register로 동시에 refill한다.
completion과 refill이 겹치면 refill이 full/address/source state 갱신에서 우선한다. 따라서
pipeline fill 뒤에는 추가 FIFO 없이 매 cycle input과 output handshake가 가능하다.

RX가 stall되면 `rx_ready=0`이고 occupied TX의 input ready도 0이 된다. 이때 TX valid,
address와 source state는 갱신되지 않는다. baseline elastic RX도 output stall 동안 valid와
payload/source를 유지한다.

## 상태 비용

baseline TX와 A23 TX의 payload storage 폭은 같다. A23이 추가하는 arbitration state는
`ceil(log2(NUM_SOURCES))` bit priority pointer 하나뿐이다. 4-source, 16-bit address에서는
기존 유효 TX/RX storage 38 bits에 priority 2 bits가 추가되어 40 bits가 된다.

공용 TX interface의 unconnected completion valid/source register까지 RTL 그대로 세면
baseline 41 bits, A23 43 bits다. full-design synthesis에서는 이 unconnected 3-bit logic이
제거될 것으로 예상한다. 실제 mapped area, Fmax와 power는 아직 측정하지 않았다.

## 공통 회귀

도구: 로컬 Verilator 5.032, `NUM_SOURCES=4`, `ADDR_WIDTH=16`.

baseline과 A23 모두 single, simultaneous, burst, backpressure에서 accepted와 emitted가
같고 errors가 0이었다. 공통 scoreboard/SVA가 missing, duplicate, source 내부 reorder,
address/source corruption과 stalled output stability를 검사했다.

| Workload | 설계 | Accepted/Emitted | Avg/Max latency | Throughput | Fairness | Max wait |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| single | baseline | 32/32 | 2.0000/2 | 0.492308 | 0.250000 | 1 |
| single | A23 | 32/32 | 2.0000/2 | 0.941176 | 0.250000 | 1 |
| simultaneous | baseline | 128/128 | 2.0000/2 | 0.498054 | 1.000000 | 192 |
| simultaneous | A23 | 128/128 | 2.0000/2 | 0.984615 | 1.000000 | 3 |
| burst | baseline | 320/320 | 2.0000/2 | 0.499220 | 0.833333 | 576 |
| burst | A23 | 320/320 | 2.0000/2 | 0.993789 | 0.833333 | 3 |
| backpressure | baseline | 128/128 | 3.5156/5 | 0.397516 | 1.000000 | 241 |
| backpressure | A23 | 128/128 | 5.0000/5 | 0.397516 | 1.000000 | 9 |

backpressure에서는 sink duty cycle이 처리율을 제한해 throughput이 동일하다. A23 평균
latency가 5 cycle로 보이는 것은 input을 더 일찍 TX/RX에 accept한 뒤 sink를 기다리기
때문이다. 최대 latency는 두 설계 모두 5 cycle이다.

## 지속 stream과 contention parameter 회귀

`scripts/run_a23_ee430_checks.sh`는 source 수 1, 3, 4 각각에 대해 전용 stream과
contention test를 실행한다.

| Sources | Stream accepted/emitted | Input II | Output II | Throughput | Contention accepted/emitted | Max service gap / bound |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 64/64 | 1 | 1 | 1.000000 | 32/32 | 1/1 |
| 3 | 64/64 | 1 | 1 | 1.000000 | 96/96 | 3/3 |
| 4 | 64/64 | 1 | 1 | 1.000000 | 128/128 | 4/4 |

stream test는 모든 연속 input/output handshake 간격이 1인지 검사한다. contention test는
모든 source의 valid를 계속 유지하고 전체 input handshake 순번으로 source별 service gap을
측정한다. 두 테스트 모두 source별 reference queue로 missing, duplicate, corruption과
reorder를 검사하며 모든 조합이 PASS했다.

bounded service는 downstream이 진행되어 input handshake가 발생한다는 조건의 bound다.
sink가 무한정 stall하면 wall-clock cycle 기준 서비스 시간을 보장할 수 없다.

## 재현 명령

```bash
scripts/self_check.sh
scripts/run_sim.sh baseline
scripts/run_sim.sh a23-ee430
scripts/run_a23_ee430_checks.sh
git diff --check
```

simulation output 경로는 `AER_SIM_OUT`으로 저장소 밖에 지정할 수 있다. 이번 검증 생성물은
`/tmp/a23-ee430-sim`에 두었고 commit하지 않는다.

## 남은 timing/PPA 위험

- `out_ready -> RX ready -> TX ready -> src_ready` combinational backpressure 경로가 있다.
- input valid에서 rotating arbitration, grant index, source address mux를 지나 TX D input까지
  이어지는 조합 경로가 fixed-priority baseline보다 길 수 있다.
- priority 비교를 포함한 두-pass selection이 non-power-of-two 지원에는 단순하지만 mapped
  area와 critical path는 Genus 결과로 확인해야 한다.
- same-edge refill은 storage를 늘리지 않지만 ready fanout과 control logic power가 늘 수 있다.
- 공식 clock/I/O constraint, PVT와 activity가 없으므로 현재 throughput은 cycle-level 기능
  결과이며 실제 Fmax나 power 개선을 뜻하지 않는다.
- FIFO가 없으므로 producer는 ready가 올라올 때까지 valid/address를 유지해야 하며 burst를
  내부 저장하는 기능은 없다.

다음 gate는 새 source snapshot과 별도 run ID를 사용해 baseline/A23을 동일한 공식 또는
팀 고정 SDC, Liberty, PVT와 activity 조건으로 합성하는 것이다. 기존 PPA run을 덮어쓰지
않는다.
