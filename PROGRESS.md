# AI-semi Progress

최종 갱신: 2026-08-07

## 프로젝트 상태

- 대회: 2026 전국 대학생 AI 반도체 회로 설계 경진대회
- 트랙: **Digital 확정**
- 현재 단계: 공용 AER workload/TB 유지 → 후보 중립 평가 규격 동결 → clean-slate RTL 착수
- 1차 결과 제출: **2026-08-28**
- 2차 최종 제출: **2026-10-30**
- 발표·시상: **2026-11-28**, 곤지암리조트
- 팀 인수인계·workload 기준: [`docs/TEAM_HANDOFF_WORKLOAD.md`](docs/TEAM_HANDOFF_WORKLOAD.md)

## 완료

- [x] 최초 모집 공지 확인
- [x] AI-SEMI 후속 공지 확인
- [x] OT PDF 4페이지 분석
- [x] 최초 공지와 OT 일정 차이 정리
- [x] 공식 포스터와 신청 QR 보존
- [x] 참가팀 명단 엑셀 보존
- [x] Digital/Analog 1·2차 과제 정리
- [x] Digital 트랙 결정
- [x] 재학증명서 제출
- [x] Digital 1차 AER 착수 계획 작성
- [x] 프로젝트 장기 메모리 구성
- [x] 대회 할당 설계 서버 SSH 접속
- [x] fixed-priority baseline RTL 및 self-checking testbench 구현
- [x] buffered round-robin improved RTL 구현 및 기능 검증
- [x] 동일 snapshot·SDC·Liberty·PVT 조건의 Genus PPA 비교
- [x] improved 방식 기각 및 baseline을 main의 유일한 활성 설계로 확정
- [x] FIFO-free rotating round-robin A2와 bubble-free TX A3 독립 검증
- [x] A2/A3를 결합한 A23 EE430 코어 구현
- [x] A23 committed RTL 기능 회귀 18/18 통과
- [x] A23 독립 stress 회귀 120/120 통과
- [x] baseline/A2/A3/A23 Genus 비교 12/12 유효 run 확보
- [x] A23를 clean-slate 전환 전 당시 내부 최종 후보로 선정하고 역사적 reference로 동결
- [x] AER를 arbitrary payload bus로 보던 기존 공통 가정의 한계 확인
- [x] 기존 세 설계를 새 RTL의 base가 아닌 benchmark reference로 재분류
- [x] AER 기본 기능군과 고질적 한계 노출군을 분리한 clean benchmark 명세 작성
- [x] ready 독립 source occurrence와 1-entry source latch/overrun 모델 구현
- [x] generated/accepted/delivered 및 occurrence 기반 latency/timing distortion 분리
- [x] multi-event/cycle을 허용하는 normalized retire interface와 legacy adapter 구현
- [x] Verilator smoke 12/12, 서버 Xcelium baseline 8/8 및 A23 8/8 correctness PASS
- [x] native candidate capability profile과 저장 없는 강희 direct-coordinate TB binding 구현
- [x] 강희 원본 무수정 상태로 서버 Xcelium 공용 always-ready core workload 10/10 PASS
- [x] 팀 공용 workload/TB 사용법, workload별 검증 목적, RUN/SKIP 정책 문서화

## 2026-08-06 공용 workload/TB 결론

- 기존 shared-channel AER를 억지로 correctness FAIL시키지 않고, 기본 기능은 통과시킨
  상태에서 bandwidth saturation, source overrun, arbitration latency, starvation,
  timing distortion과 backpressure recovery를 별도 성능 축으로 노출한다.
- 한계 노출의 중심 workload는 `limit_load`, `limit_elephant_mouse`,
  `limit_global_fanin`, `limit_retrigger`, `limit_timing_fidelity`,
  `limit_backpressure_shock`이며 local/distributed burst 쌍으로 locality 편향을 막는다.
- hard correctness는 phantom/duplicate/corruption/missing/drain 실패로만 판정한다.
  source overrun, throughput plateau, 긴 latency/wait와 낮은 fairness는 구조적 한계 수치다.
- 후보는 native interface로 연결하고 TB binding은 저장·중재·retry·backpressure 기능을
  추가하지 않는다. 없는 optional 기능은 `SKIP_UNSUPPORTED`로 분리한다.
- 강희 원본 direct-coordinate RTL은 수정 없이 always-ready core 10/10 PASS했다.
  `limit_elephant_mouse` 128/272 overrun, `limit_retrigger` 128/256 overrun,
  simultaneous/global-fan-in 최대 latency 17 cycle로 기존 단일-lane 한계도 관측했다.
- 최종 후보 비교는 DUT `ready`와 독립적으로 사전 생성되는 deterministic trace 경로를
  사용한다. 사용법과 공용 파일 위치는
  [`docs/TEAM_COMMON_WORKLOAD_GUIDE.md`](docs/TEAM_COMMON_WORKLOAD_GUIDE.md)에 정리했다.

## 2026-08-07 평가 방식 결론

- 위 공용 workload, logical event 의미, 1-entry source model, scoreboard와 hard correctness
  판정은 유지한다. 특정 후보를 유리하게 만들기 위해 trace나 PASS/FAIL 의미를 바꾸지 않는다.
- A23 전용 `baseline/A2/A3/A23` Genus 표는 과거 내부 실험으로만 보존한다. 새 공식 비교의
  후보는 강희 fovea, 현수 최종 RTL, 준영 clean-slate RTL이며 A23는 최종 P&R 대상이 아니다.
- 후보별 SHA, top, filelist, 파라미터, source 수, native pin, retire lane과 지원 capability를
  먼저 동결한다. 기능을 보충하는 synthesizable adapter가 필요하면 후보 RTL과 PPA 경계에
  포함한다.
- 성능 수치는 RTL 이름에 따라 상수로 넣지 않고 동일 deterministic trace에서 실제
  event/cycle, latency tail, fairness, timing error와 saturation knee를 측정한다.
- PPA는 동일 library/PVT/RC/SDC, clock-gating 정책, I/O delay/load, floorplan/utilization,
  tool effort와 activity window로 비교한다. Genus는 screening, Innovus fixed-netlist sweep은
  진단, period별 재합성+P&R만 최종 구조 비교로 구분한다.
- 결과는 (1) 동일 주파수에서 area/power/energy per event 효율과 (2) 후보별 post-route
  demonstrated Fmax bracket 및 `event/cycle x clock`을 나눠 보고한다. native link 폭이 다르면
  event/pin-cycle도 함께 보고한다.
- 강희의 현재 `2.0 ns PASS, 1.5 ns FAIL` 결과는 fixed-netlist post-route 기준
  `[500, 666.7) MHz` 진단 bracket이다. exact 500 MHz 또는 세 후보 공통 최종 점수로 쓰지 않는다.
- 공식 배점이 나오기 전에는 임의의 단일 가중합 점수를 만들지 않고 correctness gate 이후
  throughput, latency, area, power, energy/event, pin 효율의 Pareto 비교로 선택한다.

## 2026-08-07 AER 병목 coverage 재감사 및 A2–A9 공통 기준

- “특정 구조가 어떤 workload에서 잘 나온다”는 사실은 편향이 아니라 정당한 구조적 장점이다.
  편향은 다른 중요한 AER 병목을 누락하거나, 후보별 입력/측정 경계가 다르거나, adapter가
  후보 기능을 공짜로 대신할 때 발생한다.
- 기존 spatial locality, global fan-in, elephant/mouse, retrigger 시험은 유지하고 다음의
  독립 병목을 추가했다: same-mean 1/4/16-way temporal burst, matched local/dispersed 4-way
  contention, moving single/multiple hotspot, row/column/dispersed hotspot layout, rotating
  victim, sparse→near-saturation→overload→post-sparse→drain, cross-source timing pair,
  0.125~2.0 event/cycle load sweep.
- 공통 screening 입력은 `manifest.neutrality-n16.json`의 exact N=16 always-ready 46 traces다.
  built-in SV test는 smoke/calibration으로만 사용하며 최종 후보 순위 근거로 쓰지 않는다.
- 46개 trace의 event count, achieved mean load, peak events/cycle, report group, SHA256를
  `fixtures/neutrality_n16_golden.json`에 동결했다. generator가 한 trace라도 바꾸면
  neutrality self-test가 실패한다.
- 각 trace는 같은 source/cycle 중복을 generator 단계에서 금지한다. local/dispersed pair는
  event 시각·개수·rank 수요가 같고 peak 4-way contention만 공간 배치가 다르다.
- throughput은 candidate-dependent drain을 제외한 고정 stimulus window completion/cycle이다.
  CSV에 measurement count/window를 함께 기록하고 aggregator가 계산 일치를 검증한다.
- raw delivered-count Jain fairness는 ranking에서 제외했다. active offered source별 demand-
  normalized service fairness, minimum source ratio, live-demand zero-service window를 사용하며
  모두 무서비스이면 fairness는 1.0이 아니라 N/A다.
- phase와 timing pair는 TB-only trace relation으로 별도 분석한다. overrun이 있는 recovery는
  lossless recovery와 구분하고 backlog와 cumulative loss를 함께 보고한다.
- 서버 Xcelium 23.09에서 common mock binding으로 실제 trace 경로를 재검증했다. sparse는
  16/16 delivery·0 error, phase transition은 3139 generated·1017 overrun·0 transport error와
  `recovery_lossless=false`, timing pair는 1259 generated·6 overrun·2 dropped pair와 평균
  0.4603 cycle pair-gap error를 보고했다. CSV fixed-window counter와 aggregator도 일치했다.
- 서버의 Siemens Python wrapper가 `python3 -c` 문자열의 따옴표를 제거하는 문제를 발견해
  두 runner의 inline Python을 제거했다. 검증된 trace 변환기 출력에서 report group을 받아
  별도 `AER_TRACE_NAME` 우회 없이 Xcelium PASS를 재확인했다.
- 이 suite는 8개 clean-slate architecture agent의 공통 screening 기준으로 사용할 수 있다.
  다만 최종 심사용 완전 freeze 전에는 reset regression, multi-lane positive fixture,
  12~16 seed finalist saturation confidence, fixed-pin PPA, 실제 후보별 Xcelium 실행이 남아 있다.
- 상세 근거: `docs/verification/aer-bottleneck-coverage-audit.md`,
  `docs/TEAM_COMMON_WORKLOAD_GUIDE.md`.

## 2026-08-07 clean-slate wave 1 및 DREC 대표 후보

- 서로 겹치지 않는 8개 구조를 동일한 frozen workload/TB와 사전 stop/go 기준으로
  검토했다. 실패 결과도 삭제하지 않고 각 agent branch와
  `docs/research/wave1-eight-track-final-report.md`에 보존했다.
- N=16 공통 조건에서 물리 screening 자격을 얻은 유일한 후보는 A7 original
  shared-prefix K=4이다. 발표명은 **DREC (Dual-Rank Elastic AER Compactor)**로 정했다.
- DREC는 source pending bitmap을 한 번 cyclic rank로 만들고, ready/empty output lane도
  별도로 rank화한 뒤 같은 rank끼리 연결한다. stalled lane은 보존하면서 나머지 lane은
  독립적으로 retire/refill한다.
- 선행 prefix scan, parallel-prefix/m-select round robin, elastic register 자체를 새 발명으로
  주장하지 않는다. 기여 범위는 cyclic source rank와 available-lane rank를 independent-ready
  AER transport에 결합하고 동일 상태량 reference로 손익분기점을 검증한 것이다.
- 로컬 재검증:
  - N=16 request bitmap 65,536개를 K=1/2/4 각각 전수 PASS
  - K=4 prefix/reference independent-stall PASS, 다른 lane 671회 진행
  - randomized cycle-lockstep PASS: 1,223 cycle, accepted=delivered=3,761, drain=6
  - common 46-trace aggregate 87개 row가 same-K prefix/reference에서 동일
  - common workload/TB frozen 4개 파일은 base `ad96895`와 byte-identical
- Yosys generic 구조 합성은 기존 결과를 정확히 재현했다. N=16/K=2는 prefix가
  4,299 gate로 replicated 3,733보다 커서 기각했고, K=4에서 처음으로 prefix
  5,592/depth 139 대 replicated 6,729/depth 248의 crossover가 나타났다. 동일 state는
  104 bit다. 이는 generic proxy이며 standard-cell PPA 승리가 아니다.
- 별도 branch `integration/a7-k4-physical-candidate`, commit `1cdb1da`를 원격에 보존했다.
  발표 자료는 `docs/presentation/a7_drec_team_briefing.md`, 검증 기록은
  `docs/experiments/a7-drec-qualification.md`다.
- immutable Genus bundle과 전체 source archive를 `/tmp`에 생성·재추출 검증했다.
  서버는 공개키 비대화형 인증이 거부되어 Xcelium/Genus 실행이 남아 있다. 비밀번호나
  라이선스 정보는 저장하지 않았다.
- 다음 gate: 서버 Xcelium → 동일 Liberty/PVT/SDC Genus. standard-cell crossover가
  유지될 때만 4 endpoints와 88 retire signals를 모두 포함해 Innovus P&R한다. 실패하면
  A7을 무기한 보완하지 않고 A4 N=64 scaling 또는 신규 hierarchical K-grant merge를
  별도 가설로 검토한다.

## 설계 환경 접속 상태

- 서버: `210.126.11.79`
- 할당 계정: `aiasic26911`
- 접속 상태: SSH 인증 및 원격 셸 접속 완료
- 원격 셸: `csh`
- 다음 확인: 공식 AER 인터페이스/testbench, 평가 PVT·제약·power 방식, 제출 절차

### 재접속 절차

1. 터미널에서 아래 명령을 실행한다.

   ```bash
   ssh aiasic26911@210.126.11.79
   ```

2. 비밀번호는 대회 공지의 값을 입력한다. 입력 문자는 보이지 않는 것이 정상이며, 비밀번호는 저장소에 기록하지 않는다.
3. 원격 프롬프트가 보이면 접속 성공이다.
4. 종료는 `exit`를 사용한다. 다음 접속도 같은 명령으로 반복한다.

## Digital 1차 공식 과제

Bio-mimic Neuron을 위한 AER(Address-Event Representation) 통신 방식을 설계한다.

- 기존 AER 정보 전달 방식 분석
- 문제점 도출
- 개선 방법 및 개선 설계 방향 제시
- RTL 구현
- Synthesis 및 Timing 최적화
- Area, Power, 동작 Frequency 결과 제출

## 현재 설계 전략

### 2026-08-04 clean-slate 전환

새 구조는 강희 ROW/COL, 준영 A23, 현수 rotation-priority 중 하나를 가져와 확장하지
않는다. 세 설계는 기존 AER의 기능 및 한계를 교정하는 read-only reference로만 사용한다.
먼저 주소=이벤트 의미, source occurrence, workload trace, scoreboard, pin/PPA 비용 경계를
고정한 뒤 새 RTL을 처음부터 설계한다. 기준 문서는
[`docs/verification/aer-clean-benchmark-spec.md`](docs/verification/aer-clean-benchmark-spec.md)다.

아래 A23 결과는 clean-slate 전환 전까지 확보한 역사적 비교 기준이며 폐기하지 않는다.

clean-slate 전환 전 내부 최종 후보였던 설계는 FIFO를 추가하지 않은 A23 EE430 코어다. 기존의 큰 source별 FIFO
round-robin 실험은 기각 상태를 유지하지만, A23는 rotating round-robin pointer와
same-edge TX refill만 추가해 bounded fairness와 정상상태 1 event/cycle을 함께 달성한다.

| 지표 | baseline | A3 bubble-free | A23 combined |
| --- | ---: | ---: | ---: |
| Cell area | 432.288 um2 | 433.656 um2 | 478.458 um2 |
| 추정 Fmax | 762.486 MHz | 752.615 MHz | 670.961 MHz |
| Vectorless power | 0.0535469 mW | 0.0620979 mW | 0.0700068 mW |
| 정상상태 throughput | 0.5 event/cycle | 1.0 event/cycle | 1.0 event/cycle |

A23는 baseline 대비 raw area 10.68%, vectorless power 30.74%가 증가하지만 두 배의
처리율을 반영하면 area/event-cycle은 44.66%, power/event-cycle은 34.63% 감소한다.
A3가 순수 PPA는 더 좋지만 fixed priority starvation bound가 없어서 bounded fairness까지
포함하는 현재 목표에는 A23를 선택한다. 상세 근거는
`docs/experiments/a23-final-candidate.md`에 보존한다.

## 다음 작업

### P0 — 구현 전 필수

- [ ] Digital 트랙 제출이 공식 접수됐는지 확인
- [ ] 참가팀 명단에서 팀 상태 확인
- [x] 온라인 설계 환경 접근 권한 확인
- [x] 서버 제공 GPDK045/표준셀 library inventory와 탐색 Liberty 확인
- [x] Xcelium/Genus/Innovus/Tempus/Voltus 도구와 버전 확인
- [ ] 공식 평가 PDK/PVT/clock/power 조건 확인
- [ ] 공식 테스트벤치와 제출 형식 확인

### P1 — 사양 및 기준 모델

- [x] 팀 내부 비교값으로 source 4개와 address 16 bit 결정(공식값 TBD)
- [x] 내부 ready/valid handshake 결정(공식 request/ack 규격 TBD)
- [x] 동시 이벤트와 backpressure 정책 결정
- [x] 파라미터화된 AER 인터페이스 작성
- [x] Fixed-priority arbiter 구현
- [x] AER transmitter/receiver 구현
- [x] Scoreboard 기반 self-checking testbench 구현
- [x] 기존 ready/valid 계약을 AER 자체가 아닌 legacy adapter로 격리
- [x] clean-slate logical event와 normalized completion 계약 초안 구현
- [x] deterministic JSONL+manifest를 검증·변환해 공통 SV source model에 연결
- [x] per-event p50/p95/p99/deadline/sliding-window service-gap 지표 연결
- [x] 46-run N=16 병목 coverage/anti-specialization exact-trace suite와 golden SHA 동결
- [x] demand-normalized fairness, fixed-window throughput, phase/timing analyzer 구현
- [x] Genus screening과 Innovus post-route를 분리한 PPA/Fmax 판정 계약 구현
- [ ] 세 최종 후보의 SHA/top/filelist/parameter/native-interface manifest 동결
- [ ] 공용 TB에 동일 trace 기반 activity window와 후보 중립 성능 결과 export 연결
- [ ] fixed-pin serializer/decoder의 구체적 pin 수·codec PPA 조건 freeze

### P2 — 개선 및 측정

- [x] Round-robin arbiter 구현
- [x] FIFO 버퍼 구현
- [x] 단일·동시·burst·backpressure 테스트
- [x] Starvation 및 누락·중복 검증
- [x] 기준/개선 설계 합성
- [x] PPA 및 최대 주파수 비교
- [x] 결과 분석과 baseline 선택
- [x] 저비용 RR 및 bubble-free 구조 독립 구현·검증
- [x] A23 통합과 동일 조건 4-way PPA 비교
- [x] 기능·stress·PPA qualification 완료
- [ ] 강희 fovea와 현수 최종 RTL을 동일 N=16 후보 중립 Genus screening에 등록
- [ ] 세 최종 후보를 동일 조건의 period별 재합성+Innovus P&R로 비교
- [ ] 준영 clean-slate RTL을 공용 correctness gate부터 처음부터 구현
- [ ] 공식 workload 수령 후 shared 1~2 entry buffer의 필요성 재평가

## 아직 확인되지 않은 사항

- PPA 정확한 배점과 가중치
- 평가 공정과 operating corner
- AER의 정확한 인터페이스 및 테스트 벡터
- 제출 링크, 파일명과 디렉터리 형식
- 최종 참가 40팀 선발 기준과 발표 시점

## 참고 문서

팀원이 작업 경위, 서버 제공물, 자체 workload, 평가 출처와 다음 할 일을 한 번에
파악하려면 먼저 아래 문서를 읽는다.

- [`docs/TEAM_HANDOFF_WORKLOAD.md`](docs/TEAM_HANDOFF_WORKLOAD.md)
- [`docs/experiments/a23-final-candidate.md`](docs/experiments/a23-final-candidate.md)

장기 프로젝트 자료는 Windows 로컬 경로에 보관한다.

- `C:\Users\박준영\AI-semi\memory\PROJECT_MEMORY.md`
- `C:\Users\박준영\AI-semi\docs\Digital_1차_착수계획.md`
- `C:\Users\박준영\AI-semi\docs\대회_정보_총정리.md`
- `C:\Users\박준영\AI-semi\docs\실행_체크리스트.md`
