# A2 FIFO-free round-robin experiment

검증일: 2026-08-02

브랜치: `experiment/a2-fifo-free-round-robin`

## 목표와 범위

fixed-priority baseline의 지속 경합 starvation을 per-source FIFO 없이 줄이는 저비용
후보를 평가한다. 이 실험은 로컬 RTL 기능 비교만 수행한다. 원격 설계 서버 배포,
Genus 합성, PPA 측정, `main` 병합은 범위에 포함하지 않는다.

## 구조

후보는 baseline의 registered TX와 elastic RX를 그대로 공유한다. fixed-priority
arbiter만 `aer_a2_rr_arbiter`로 바꾸고, 마지막으로 수락된 source의 다음 source부터
검색한다.

- 기존 `rtl/baseline/**` 변경 없음
- source별 또는 shared FIFO 없음
- grant lock 없음
- 추가 순차 상태: `priority_q` 한 개
- priority는 선택 이벤트가 TX에 실제 수락될 때만 이동
- output backpressure 중 payload 안정성은 baseline TX/RX가 유지
- 기존 `aer_dut` ready/valid, address, source sideband 의미 유지

4-source, 16-bit address 구성의 architecturally relevant storage 예상치는 다음과 같다.

| 구성 | TX/RX 상태 | arbiter 상태 | 합계 |
| --- | ---: | ---: | ---: |
| fixed-priority baseline | 38 bits | 0 bits | 38 bits |
| FIFO-free round-robin | 38 bits | 2 bits | 40 bits |

38 bits는 TX/RX의 valid/full, address, source register를 센 값이다. 공용 `aer_tx`의
미사용 completion sideband register까지 RTL 그대로 세면 두 설계가 각각 41 bits와
43 bits이며, 합성에서는 unconnected logic으로 제거될 것으로 예상한다. 따라서 후보의
순차 storage 증가는 절대값 2 bits, 유효 datapath 기준 약 5.26%다. rotating select의
조합논리 비용과 실제 Fmax/area/power는 서버 합성 전에는 확정할 수 없다.

## bounded-service 판정

`starvation` workload는 source 0~3의 valid를 동시에 계속 유지한다. wall-clock cycle이
아니라 전체 input handshake 순번을 기준으로 각 source의 연속 서비스 간격을 계산한다.
모든 source가 포화된 경우 각 source가 `NUM_SOURCES=4`번의 input handshake 안에 한 번
서비스되지 않으면 즉시 실패한다.

후보 결과:

- source별 32 events, 총 128 accepted/emitted
- missing, duplicate, corruption, reorder 0
- source별 서비스 수 32로 동일
- 최대 서비스 간격 4 input handshakes, bound 4 충족
- Jain fairness 1.0

기존 baseline starvation test에서는 같은 지속 경합 조건에서 source 0이 20회,
source 3이 0회 수락되어 starvation이 재현됐다.

## 로컬 회귀 결과

도구: Verilator 5.032, `NUM_SOURCES=4`, `ADDR_WIDTH=16`, source당 기본 32 events.

| Workload | 설계 | Accepted/Emitted | Errors | Avg/Max latency | Throughput | Jain fairness | Max wait |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single | baseline | 32/32 | 0 | 2.0000/2 | 0.492308 | 0.250000 | 1 |
| single | candidate | 32/32 | 0 | 2.0000/2 | 0.492308 | 0.250000 | 1 |
| simultaneous | baseline | 128/128 | 0 | 2.0000/2 | 0.498054 | 1.000000 | 192 |
| simultaneous | candidate | 128/128 | 0 | 2.0000/2 | 0.498054 | 1.000000 | 7 |
| burst | baseline | 320/320 | 0 | 2.0000/2 | 0.499220 | 0.833333 | 576 |
| burst | candidate | 320/320 | 0 | 2.0000/2 | 0.499220 | 0.833333 | 7 |
| backpressure | baseline | 128/128 | 0 | 3.5156/5 | 0.397516 | 1.000000 | 241 |
| backpressure | candidate | 128/128 | 0 | 3.5156/5 | 0.397516 | 1.000000 | 10 |
| starvation | candidate | 128/128 | 0 | 2.0000/2 | 0.498054 | 1.000000 | 7 |

Jain fairness는 최종 source별 완료 개수만 사용하므로 모든 finite source가 결국 drain된
`simultaneous`와 `backpressure`에서는 baseline도 1.0이다. 이 값만으로 bounded fairness를
판정하지 않고 max wait와 지속 경합 service gap을 함께 사용한다.

후보는 baseline과 같은 TX/RX를 사용하므로 latency와 throughput은 동일하다. 개선점은
대기시간과 bounded fairness이며, throughput의 0.5 event/cycle 제한은 TX bubble을 별도로
제거하지 않는 한 그대로 남는다.

## 실행 방법

```bash
scripts/self_check.sh
scripts/run_sim.sh baseline
scripts/run_sim.sh a2_round_robin
```

`run_sim.sh a2_round_robin`은 공통 4개 workload와 `starvation`을 실행한다. 로컬에
Xcelium, Icarus 또는 Verilator 중 하나가 필요하다. 이번 결과는 저장소 밖 `/tmp`에
임시 설치한 Verilator로 생성했으며 generated simulation output은 commit하지 않는다.

## 결론과 다음 gate

기능 관점에서는 채택 가능한 저비용 후보다. 2-bit priority state만으로 baseline의
지속 경합 starvation을 bounded service로 바꿨고 기존 workload의 latency/throughput을
악화시키지 않았다. 다만 rotating combinational select의 PPA는 측정하지 않았으므로,
원격 실행 승인을 받은 다음 동일 snapshot/SDC/Liberty/PVT 조건의 별도 run ID로 baseline과
비교해야 최종 채택 여부를 판단할 수 있다.
