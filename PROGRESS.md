# AI-semi Progress

최종 갱신: 2026-08-01

## 프로젝트 상태

- 대회: 2026 전국 대학생 AI 반도체 회로 설계 경진대회
- 트랙: **Digital 확정**
- 현재 단계: baseline/improved 구현·검증·동일 조건 PPA 비교 완료 → baseline 최종 채택
- 1차 결과 제출: **2026-08-28**
- 2차 최종 제출: **2026-10-30**
- 발표·시상: **2026-11-28**, 곤지암리조트

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

## 설계 환경 접속 상태

- 서버: `210.126.11.79`
- 할당 계정: `aiasic26911`
- 접속 상태: SSH 인증 및 원격 셸 접속 완료
- 원격 셸: `csh`
- 다음 확인: EDA 도구·버전, PDK·표준셀 라이브러리, 작업 디렉터리·권한, 공식 실행/제출 절차

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

최종 채택 설계는 fixed-priority baseline이다. buffered round-robin 방식은
기능·공정성은 개선됐지만 동일 조건의 탐색 합성에서 PPA가 모두 악화되어 기각했다.

| 지표 | baseline | improved | 판정 |
| --- | ---: | ---: | --- |
| Cell area | 432.288 um2 | 2805.084 um2 | baseline 채택 |
| Fmax | 762.485703 MHz | 368.405541 MHz | baseline 채택 |
| Total power | 0.053546900 mW | 0.175754000 mW | baseline 채택 |
| TNS | 0 ns | 0 ns | 동률 |

측정 run은 `ppa-20260801-pvt0p9v125c-5ns`, integration snapshot은
`22dab24d81572814514f069359b2029a288d6019`이다. improved 소스는 `a2` 브랜치와
Git 이력에 보존하고, 측정 과정과 기각 근거는 `docs/tasks/a2.md`에 보존한다.

## 다음 작업

### P0 — 구현 전 필수

- [ ] Digital 트랙 제출이 공식 접수됐는지 확인
- [ ] 참가팀 명단에서 팀 상태 확인
- [x] 온라인 설계 환경 접근 권한 확인
- [ ] 공정/PDK/표준셀 라이브러리 확인
- [ ] 합성·STA·Power 도구와 버전 확인
- [ ] 공식 테스트벤치와 제출 형식 확인

### P1 — 사양 및 기준 모델

- [ ] 이벤트 소스 개수와 주소 폭 결정
- [ ] Request/Acknowledge handshake 결정
- [ ] 동시 이벤트 처리와 backpressure 정책 결정
- [ ] 파라미터화된 AER 인터페이스 작성
- [ ] Fixed-priority arbiter 구현
- [ ] AER transmitter/receiver 구현
- [ ] Scoreboard 기반 self-checking testbench 구현

### P2 — 개선 및 측정

- [x] Round-robin arbiter 구현
- [x] FIFO 버퍼 구현
- [x] 단일·동시·burst·backpressure 테스트
- [x] Starvation 및 누락·중복 검증
- [x] 기준/개선 설계 합성
- [x] PPA 및 최대 주파수 비교
- [x] 결과 분석과 baseline 선택

## 아직 확인되지 않은 사항

- PPA 정확한 배점과 가중치
- 평가 공정과 operating corner
- AER의 정확한 인터페이스 및 테스트 벡터
- 제출 링크, 파일명과 디렉터리 형식
- 최종 참가 40팀 선발 기준과 발표 시점

## 참고 문서

장기 프로젝트 자료는 Windows 로컬 경로에 보관한다.

- `C:\Users\박준영\AI-semi\memory\PROJECT_MEMORY.md`
- `C:\Users\박준영\AI-semi\docs\Digital_1차_착수계획.md`
- `C:\Users\박준영\AI-semi\docs\대회_정보_총정리.md`
- `C:\Users\박준영\AI-semi\docs\실행_체크리스트.md`
