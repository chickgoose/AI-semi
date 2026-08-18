# AI-SEMI 주최 측 Q&A 해석과 팀 REDRED 목표

기준일: 2026-08-19
근거: 2026-08-18 Q&A 세션의 사용자 제공 구술 전사와 기존 REDRED 검증 자료

## 결론

주최 측이 원하는 것은 최소 면적 회로 하나가 아니라, 팀이 문제와 사용 시나리오를 타당하게 정의하고 그 문제를 실제로 해결하는 전체 시스템을 교육용 45 nm PDK 안에서 구현한 뒤 correctness, event loss, 처리 성능, timing, area, vectorless power와 안정성을 같은 경계에서 설명하는 것이다.

REDRED의 새 목표는 다음과 같다.

> 교육용 GPDK045 범위 안에서 A2를 주후보·A3를 exact-prefix fallback으로 한 16-source complete AER endpoint를 구현하고, 동일한 팀 정의 traffic에서 accepted-event exact-once·source overrun·throughput·latency와 complete-boundary post-route timing·area·mapped vectorless power를 입증하되, P6는 서면 허용 전 HOLD하고 single-edge fallback을 별도 검증하며, known-motion world↔sensor 변환은 endpoint PPA 밖의 system demo로 검증한다.

## 답변별 결정

| 질문 | 주최 측에서 확정한 것 | 권장이나 선택 사항 | REDRED 결정 | 아직 HOLD인 것 |
|---|---|---|---|---|
| 1–3 timing·조건·power | timing 확인 필요, 공통 교육용 45 nm PDK와 그 I/O 조건 사용, power는 vectorless | post-layout simulation, 일반 범위 PVT/온도, activity/loading 민감도는 가능하면 수행 | complete endpoint의 고정 operating point timing과 동일 방법 mapped vectorless power를 별도 gate로 둔다 | 주최 측의 정확한 수치 corner/clock/I/O 표와 power-variation 채점 여부 |
| 평가 철학 | 문제 정의, 혁신성, 기능 성공, 구현 가능성, 사용할 수 있는 PPA, 안정성을 종합 평가 | 문헌 근거와 발표·녹화 demo 강화 | 임의 종합 점수 대신 correctness/loss/throughput/latency/PPA의 원자료와 Pareto 관계를 제시 | 주최 측 AER 점수식이나 pass threshold는 없음 |
| 좌표 변환 | world↔sensor 변환이 필요할 수 있고 초기에는 motion parameter가 주어진다고 가정 가능 | unknown-motion 추정은 후속 확장 | known-motion 변환은 retire 이후 software system demo로 먼저 검증 | 공식 numeric width, scale, rounding, saturation, pose 동기 규칙과 synthesizable RTL |
| 4 전체 범위 | 평가 범위는 시스템 전체이며 encoder/decoder/link bottleneck도 성능에 포함 | 핵심부터 완성하고 단계적으로 확장, 10/100 GB/s는 예시 | source admission부터 retirement까지 모든 synthesizable scheduler/buffer/TX/RX/control을 charge | real pad, package, channel을 포함한 silicon PHY 범위 |
| 5 event loss | 포착 또는 전송하지 못한 event는 system loss이며 과도한 loss는 성능 실패 | 허용 가능한 수준은 팀 시나리오로 정의 | `generated = source_overrun + accepted`, drain 후 `accepted = retired`; overrun과 accepted-event 오류를 분리 보고 | 주최 측이 정한 허용 loss threshold는 없음 |
| 6 traffic·dataset | 공식 traffic pattern과 평가 지표는 제공하지 않으며 주최 측 sensor data는 추후 제공 예정 | Zürich/UPenn 공개 dataset 참고 | full50을 candidate-neutral 팀 canonical suite로 유지하고 새 dataset은 versioned extension으로 추가 | 주최 측 dataset bytes, format, license, scenario, 전달 시점 |
| 7 DDR·clock·primitive | 교육용 45 nm PDK가 제공하는 범위 밖 사용은 제한 | 없음 | P6 standard-cell 연구 증거와 대회 허용성을 분리하고, 불허 시 single-edge fallback을 독립 검증 | P6/multi-edge의 서면 허용, real DDR I/O macro/pad, fallback P&R/power |

질문 1–3의 답변은 Q&A 전사에서 하나의 묶음으로 남아 있으므로 위 구분을 원 질문의 축에 맞춘 해석으로 사용하며, 주최 측의 문장별 공식 확답처럼 인용하지 않는다.

## 평가 경계와 지표

완전 endpoint의 시작은 synchronous `source_pending && source_accept`이고 끝은 synchronous consumer가 관측한 `retire_valid`와 retired address다. Scheduler 정책 상태, admission, charge되는 buffering, 선택 link의 TX/RX와 control, retirement, drain/error logic은 모두 포함한다. Event 발생기, testbench scoreboard, coordinate transform, motion estimation, visualization은 endpoint PPA에서 제외한다.

모든 완료 run은 다음을 기록한다.

- per-event occurrence, accept, retire identity와 cycle/order
- generated, source_overrun, accepted, retired와 hard-error counters
- fixed-window retired events/cycle와 occurrence→accept, accept→retire latency
- candidate/link/top/RTL/TB/trace/tool/command/result digest
- 같은 boundary와 조건의 post-route timing/area 및 mapped vectorless power

`source_overrun`은 입력 source의 제한된 pending capacity 때문에 발생한 system-capacity loss다. Phantom, duplicate, corruption, reorder, accepted-missing, partial retirement, X/illegal output, drain timeout, reset escape와 protocol error는 hard correctness failure다. 둘을 합쳐서 하나의 “loss” 숫자로 숨기지 않는다.

## 후보와 현재 증거

- A2는 persistent all-four-row demand에서 `[1,5,5,1]` weighted opportunity를 제공하는 성능 주후보다. Sparse fallback에는 debt/catch-up이 없고 exact scalar prefix를 보장하지 않는다.
- A3는 held pending snapshot에 canonical scalar selection 두 단계를 적용하는 exact-prefix 의미 fallback이다. Shared link legality나 공통 증거 실패를 자동으로 해결하는 fallback은 아니다.
- A4는 actual-P6 회귀에 남아 있는 연구 비교 대상이지 release 후보가 아니다.
- 고정 Verilator와 고정 RTL/TB/trace로 actual-P6 full50 150회, reset 3회, mutation 15회를 재현했다. Digital RTL은 GO지만 이 receipt 자체의 physical 및 CDC/RDC 상태는 HOLD다.
- 기존 6.5 ns, 153.846 MHz 결과는 Fovea+A7/R1, A2+P6, A3+P6 standard-cell endpoint의 고정 reference point다. Exact Fmax, real DDR pad/PHY signoff 또는 대회 P6 허용을 뜻하지 않는다.

## Release gate

1. `CANONICAL_DIGITAL`: 동일 full50 trace와 TB/window/tool로 A2/A3 actual RTL을 실행하고 accepted-event exact-once와 loss conservation을 event 단위로 확인한다.
2. `INTERFACE`: P6는 주최 측 서면 허용과 PDK cell legality가 모두 있어야 선택할 수 있다. 그 전에는 HOLD이며 single-edge fallback도 독립적인 digital/P&R/power evidence 전까지 HOLD다.
3. `PHYSICAL`: 선택된 동일 endpoint boundary에서 timing, area, DRC, antenna, connectivity를 검증한다. 6.5 ns reference를 Fmax로 부르지 않는다.
4. `VECTORLESS_POWER`: 동일 complete endpoint, GPDK045/PVT/clock/I/O 조건에서 실제 Genus mapped vectorless artifact를 검증한다. VCD/SAIF activity 결과와 다른 evidence class다.
5. `CDC_RDC`: phase-related clock/reset 구조의 독립 검증 전까지 final system release를 막는다.
6. `DATASET_EXTENSION`: 주최 측 또는 공개 dataset은 format, license, source mapping, immutable digest가 갖춰진 뒤 추가한다. 부재가 팀 canonical digital 작업을 막지는 않는다.
7. `COORDINATE`: supplied pose를 쓰는 strict post-retire demo를 먼저 검증한다. Numeric contract와 RTL/PPA는 별도 후속 gate다.

## 금지할 과장

- Genus 합성 면적·전력만으로 post-route 또는 Fmax를 주장하지 않는다.
- core-only PPA와 complete endpoint PPA를 같은 순위로 섞지 않는다.
- `capacity22`를 full50 외의 22개 추가 실행으로 합산하지 않는다.
- 10/100 GB/s 예시를 주최 측 목표 bandwidth로 쓰지 않는다.
- full50을 공식 또는 주최 측 dataset이라고 부르지 않는다.
- P6 standard-cell timing PASS를 organizer legality, ODDR/IDDR pad, package/channel signoff로 확대하지 않는다.
- coordinate out-of-FOV를 AER transport loss로 분류하지 않는다.

## 실행 순서

1. A2/A3 canonical actual-RTL campaign과 receipt provenance를 닫는다.
2. 주최 측에 P6/multi-edge 허용과 정확한 공통 PDK corner/clock/I/O 표를 서면 확인한다.
3. P6와 single-edge fallback 중 release interface를 확정하고 선택 interface만 새로 complete-boundary P&R/vectorless/CDC 검증한다.
4. 주최 측 dataset이 오면 기존 full50을 바꾸지 않고 versioned extension campaign으로 추가한다.
5. known-motion coordinate demo를 system presentation에 연결하고, numeric contract가 정해진 뒤에만 RTL/PPA 편입을 검토한다.

## 2026-08-19 통합 검증 상태

전용 통합 branch `integration/redred-system-goal`에서 다음을 확인했다.

- 새 dataset/coordinate/campaign/vectorless/contract 도구의 집중 회귀 108개가 모두 PASS했다.
- 고정 actual-P6 재현은 package commit `88b972233de0db6ca73c27ad450c62eb97190e55`에서 PASS했다. 실제 실행 수는 full50 150회, reset 3회, actual-RTL mutation 15회이며 결과 JSON SHA-256은 `5cb973816fc7f2e71edf299cd9ba5c33e883aae390731c0f455d3a004562a8dd`다.
- A2 full50은 generated 106,416, accepted/retired 104,046, source overrun 2,370, fixed-window 0.896281733 event/cycle, accept-to-retire 3 cycle이다.
- A3 full50은 generated 106,416, accepted/retired 93,645, source overrun 12,771, fixed-window 0.806670806 event/cycle, accept-to-retire 2 cycle이다.
- 위 재현이 허용하는 판정은 `digital_RTL=GO`뿐이다. P6 organizer legality, selected-interface physical, CDC/RDC, single-edge fallback, real-server mapped vectorless power는 계속 HOLD다.
- local captured-byte dataset 변환과 known-pose coordinate 실행은 각각 importer 및 `SYNTHETIC_DEMO` 검증일 뿐 공식 dataset 또는 canonical system evidence가 아니다.
- 일부 기존 physical/Genus 회귀는 소스 실패가 아니라 `/tmp`의 세 개 golden archive가 없는 상태라 재현 HOLD다.
