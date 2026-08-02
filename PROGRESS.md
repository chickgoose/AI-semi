# AI-semi Progress

최종 갱신: 2026-08-02

## 프로젝트 상태

- 대회: 2026 전국 대학생 AI 반도체 회로 설계 경진대회
- 트랙: **Digital 확정**
- 현재 단계: EE430 기반 A23 기능·stress·Genus PPA gate 완료 → 내부 최종 후보 확정
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
- [x] A23를 현재 내부 최종 후보로 선정

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

현재 내부 최종 후보는 FIFO를 추가하지 않은 A23 EE430 코어다. 기존의 큰 source별 FIFO
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
