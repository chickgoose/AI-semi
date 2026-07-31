# AI-semi Progress

최종 갱신: 2026-07-31

## 프로젝트 상태

- 대회: 2026 전국 대학생 AI 반도체 회로 설계 경진대회
- 트랙: **Digital 확정**
- 현재 단계: 자료 정리 및 방향 결정 완료 → 1차 AER 사양 정의 착수
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

## Digital 1차 공식 과제

Bio-mimic Neuron을 위한 AER(Address-Event Representation) 통신 방식을 설계한다.

- 기존 AER 정보 전달 방식 분석
- 문제점 도출
- 개선 방법 및 개선 설계 방향 제시
- RTL 구현
- Synthesis 및 Timing 최적화
- Area, Power, 동작 Frequency 결과 제출

## 현재 설계 전략

먼저 비교 기준이 되는 기본 AER RTL과 self-checking testbench를 만든다.

1. Fixed-priority arbiter로 기준 설계 구현
2. Round-robin arbiter로 starvation과 공정성 개선
3. FIFO를 추가해 burst 이벤트 손실과 backpressure 대응
4. 동일 workload에서 latency, throughput, fairness, frequency, area, power 비교
5. 합성 결과에 따라 계층형 arbiter 검토

## 다음 작업

### P0 — 구현 전 필수

- [ ] Digital 트랙 제출이 공식 접수됐는지 확인
- [ ] 참가팀 명단에서 팀 상태 확인
- [ ] 온라인 설계 환경 접근 권한 확인
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

- [ ] Round-robin arbiter 구현
- [ ] FIFO 버퍼 구현
- [ ] 단일·동시·burst·backpressure 테스트
- [ ] Starvation 및 누락·중복 검증
- [ ] 기준/개선 설계 합성
- [ ] PPA 및 최대 주파수 비교
- [ ] 결과 분석과 설계 선택

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
