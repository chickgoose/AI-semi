# MC-WTB Stage-1 교수 Q&A 추적성과 증거 게이트

기준일: 2026-08-20

현재 좁은 판정: **`PASS_SYNTHETIC_CAUSAL_CORE` + `PASS_HARDENING_2`**

전체 판정: **일반 Stage-1 acceptance는 `HOLD`**

## 1. 현재 판정의 정확한 범위

MC-WTB Stage-1이 현재 입증한 것은 두 가지이며, 서로 대신하지 않는다.

1. 독립 생성한 rotation-only 합성 fixture에서 source timestamp와 올바른 supplied
   pose를 사용한 software coordinate transform이 identity, wrong-valid,
   pose-permuted 대조군과 구별되는 기하 결과를 낸다.
2. 지원되는 POSIX 환경에서 parser와 SHA-256이 동일한 one-read immutable input
   bytes를 사용하고, 고정 폭 계약·parameter identity·dirfd 기반 publication 및
   cleanup failure reporting이 적대 시험을 통과한다.

커밋별 증거는 다음처럼 분리한다.

| 커밋 | 추가한 증거 | 승격하지 않는 주장 |
|---|---|---|
| `58078df` | MC-WTB Stage-1 software model, CLI, synthetic fixture, native test | RTL, codec, metric 3-D, pose estimation, real-data 효과 |
| `073fab53` | timebase, latest-pose, pose-age, polarity, field-width, provenance와 packet-key projection 경계 | parser-byte identity, immutable contract, namespace-race hardening, wire bandwidth |
| `c0d6f36` | production model과 독립된 generator/oracle 및 C0/C1/C2/C3 causal core | 넓은 mutation campaign, real-data 일반화, codec, RTL/PPA |
| `76f62f3` | public immutable blob API, exact parsed-byte hash, immutable fixed-v1 source, parameter/semantic binding, pinned POSIX dirfd publisher | crash durability, hostile filesystem transaction, 3-input atomic snapshot |
| `c863c0c` | composite cleanup error, 모든 cleanup 시도, post-rename parent/target identity 검사, late-race tests, v1 provenance alias | rollback, same-directory CAS, physical cleanup 성공 보장, wire/codec/RTL/PPA |

`76f62f3` 단독은 cleanup failure 비가시성, late-parent 성공 오보고와 v1
compatibility 문제 때문에 Hardening 2 PASS가 아니다. `PASS_HARDENING_2`는 그
commit을 ancestor로 포함하고 최종 remediation과 detached acceptance를 통과한
exact `c863c0c` 형상에 부여한다. result의 semantic ID만 보고 이 PASS나 Git
commit identity를 추론하지 않는다.

`c0d6f36`의 65×65 quarter-turn 시험에서 C0 `IDENTITY`는 8/8 exact·SSE 0,
C1 `CORRECT`는 32/32 exact·SSE 0, C2 `WRONG_VALID`는 8/32 exact·SSE 9664,
C3 `POSE_PERMUTED`는 0/32 exact·SSE 9664였다. C3의 잘못된 pose는 packet-key
projection만 보면 28→24 packets, 3612→3096 projected bits로 더 좋아진다.
따라서 projection 감소만으로 기하 정합, codec 효과 또는 wire-bit 감소를
주장할 수 없다.

현재 status ledger는 다음과 같다.

| 항목 | 상태 | 정확한 의미 |
|---|---|---|
| 독립 합성 rotation causal core | **`PASS_SYNTHETIC_CAUSAL_CORE`** | 고정된 C0/C1/C2/C3 fixture와 oracle에 한정 |
| Hardening 2 | **`PASS_HARDENING_2`** | `76f62f3` + `c863c0c` 및 final detached acceptance 범위에 한정 |
| 일반 Stage-1 acceptance | **`HOLD`** | real pose-joined data, codec/wire와 broad campaign이 미완료 |
| 넓은 mutation/discrimination campaign | `HOLD_NOT_RUN` | `PASS_SYNTHETIC_CAUSAL_DISCRIMINATION`이 아님 |
| UZH event+pose+calibration 결합 | `HOLD_MISSING_ARTIFACT` | real geometry evidence 없음 |
| MVSEC importer와 6-DoF/depth profile | `HOLD_NOT_IMPLEMENTED` | initial rotation profile의 대체물이 아님 |
| 실제 packet codec/decoder와 wire accounting | `HOLD_NOT_IMPLEMENTED` | 현재 129-bit 수치는 lossy packet-key projection |
| Stage-2A supplied-pose RTL | `HOLD_NOT_STARTED` | fixed-point, finite storage, packet grammar 미확정 |
| MC-WTB complete-endpoint 45 nm PPA | `HOLD_NO_COHORT` | 기존 A2/A3 endpoint PPA와 혼합 금지 |
| Samsung data/importer | `HOLD_UNSUPPORTED_FORMAT` | bytes, format, license, authority 미수령 |
| Feedback pose estimator | `FUTURE_STAGE2B` | supplied-pose RTL 이후 별도 gate |

## 2. 근거 권위, confidence와 출처 규칙

| 근거 | confidence | 사용 규칙 |
|---|---|---|
| 교수 Q&A Q1–Q3 사용자 재확인 정정본 | **HIGH** | 문제 정의, 4단계 평가, supplied-motion 순서, full-system, rate/loss, PDK의 최상위 근거 |
| 교수 Q&A Q4–Q6 자동 STT 복원 | **LOW/MEDIUM** | 보조 방향으로만 사용하고 공식 수치·규격의 단독 근거로 쓰지 않음 |
| `docs/AI_SEMI_QNA_REDRED_GOAL_20260819.md` | **TEAM INTERPRETATION** | 교수 발언과 팀 결정·PASS/HOLD를 구분 |
| P1/P4/P5 reviewed notes | **REVIEWED LITERATURE NOTES** | 선행 메커니즘과 claim boundary에만 사용; 교수 Q&A 발언으로 인용 금지 |
| UZH/MVSEC 공식 페이지 | **PRIMARY DATASET SOURCE** | format, sensor, calibration, ground truth, license의 권위 |
| 현재 local importer/projection receipt | **LOCAL SCOPED EVIDENCE** | pinned bytes의 변환·보존만 입증; official/canonical/replay 승격 금지 |
| commits와 committed tests | **REPOSITORY EVIDENCE** | exact 구현/fixture/test가 실행한 계약만 입증 |

교수 Q&A는 P1, P4, P5를 논문·특허 번호나 제목으로 직접 지명하지 않았다.
세 자료는 팀이 Q&A 이후 선정한 인접 선행자료다. 따라서 “교수가 이 논문을
보라고 했다”, “교수가 이 논문의 해법을 요구했다”와 같은 귀속은 금지한다.
교수 Q&A 근거는 사용자 제공 transcript의 Q1–Q3 재확인본과 Q4–Q6 자동 STT
복원본이며, 원 transcript 자체는 이 repository에 포함돼 있지 않다. 따라서 이
문서는 verbatim transcript authority가 아니라 confidence를 표시한 팀 trace이고,
정확한 발화 인용이 필요하면 원본을 별도로 재확인해야 한다.

직접 확인할 공식·원 출처 링크는 다음과 같다.

- P1 특허: [US 9,934,557 B2](https://patents.google.com/patent/US9934557B2/en)
- P4 발표자료: [Hyunsurk Eric Ryu, Industrial DVS Design: Key Features and Applications, CVPRW 2019](https://rpg.ifi.uzh.ch/docs/CVPR19workshop/CVPRW19_Eric_Ryu_Samsung.pdf)
- P5 논문: [Suh et al., ISCAS 2020, DOI 10.1109/ISCAS45731.2020.9180436](https://doi.org/10.1109/ISCAS45731.2020.9180436)
- UZH: [The Event-Camera Dataset and Simulator](https://rpg.ifi.uzh.ch/davis_data.html)
- MVSEC: [official overview](https://daniilidis-group.github.io/mvsec/) 및 [official data format](https://daniilidis-group.github.io/mvsec/data_format/)

P1은 등록특허의 claim과 명세서 embodiment를 분리한다. P4는 저자 workshop
slide deck이지 peer-reviewed paper가 아니다. P5 DOI는 IEEE proceedings record의
서지 권위를 제공하지만, 공개 presentation에서 확인한 회로·사진·표를 논문 본문
전체를 직접 검토한 것처럼 인용하지 않는다. 로컬 literature note도 이 원자료를
대체하지 않는다.

## 3. 교수 Q&A 전 축 반영표

`S1`은 software Stage-1, `DX`는 dataset extension, `S2A`는 supplied-pose RTL,
`S2B`는 estimator, `PG`는 MC-WTB complete-endpoint 45 nm physical gate다.

| Q&A 축 | confidence | 단계 | 규범적 결정과 현재 상태 |
|---|---|---|---|
| Timing은 회로 완성의 필수 조건 | HIGH | S2A/PG | timing 자체는 필수다. 동일 complete boundary의 post-route setup/hold를 요구하는 것은 팀의 더 엄격한 gate다. |
| Post-layout simulation | HIGH | PG | `NOT_RUN_OPTIONAL`: 가능하면 수행하는 가산 evidence이며 timing signoff의 대체나 교수의 강제 조건으로 쓰지 않는다. |
| PVT | HIGH | PG | `NOT_RUN_OPTIONAL` 또는 별도 팀 gate다. 수행 시 승인된 nominal/commercial 범위를 쓰며 automotive harsh 범위를 교수 요구로 만들지 않는다. |
| 공통 45 nm PDK와 I/O delay/load | HIGH | S2A/PG | exact release, Liberty/LEF/QRC, corner, transition/delay/load를 receipt에 고정한다. 미확정이면 HOLD다. |
| Vectorless power와 activity sensitivity | HIGH | PG | mapped/post-route vectorless가 기본이며 VCD/SAIF sensitivity는 별도 선택 evidence다. |
| 발표·녹화·공개 비교 | HIGH | 전 단계 | 제출·비교 process 정보다. claim, 실패 envelope, command, digest를 공개하되 이것만으로 기술 PASS를 만들지 않는다. |
| 진짜 문제와 background를 먼저 정의 | HIGH | S1 | 병목 1·5·6을 분리하고 claim→metric→falsifier를 연결한다. |
| AER/system 기준과 reference 선정은 open | HIGH | S1/DX | 팀이 semantics, loss, link, baseline을 고정하되 organizer 규격이라고 부르지 않는다. |
| 평가 1: 문제 정의 | HIGH | S1 | supplied-pose pure rotation과 source-time boundary를 한 문장으로 고정한다. |
| 평가 2: 혁신성·성공성 | HIGH | S1/DX | raw, sensor-fixed, no-warp, retire-time, ideal-float 및 equal-bit/equal-loss 비교가 필요하다. 현재 HOLD다. |
| 평가 3: 구현 가능성과 usable PPA | HIGH | S2A/PG | fixed-point, legal memory, conflict, buffers, codec/link/decoder를 모두 charge한다. 현재 HOLD다. |
| 평가 4: 시스템 안정성 | HIGH | S1/S2A/PG | stale pose, LUT race, wrap, hot bank, overflow, stall, reset, packet fault를 주입한다. 일부 software만 PASS다. |
| world↔sensor tilt/pan/rotation | HIGH | S1/DX/S2A | frame, direction, time convention을 machine-bind하고 첫 profile은 calibrated pure rotation이다. |
| supplied motion부터 시작 | HIGH | S1/DX/S2A | ground-truth/supplied pose를 먼저 쓰며 active pose/LUT effective time을 명시한다. |
| scenario simulation 후 feedback estimation | HIGH | S1→S2B | speed, density, pose-age sweep을 먼저 닫고 estimator는 별도 accuracy/latency/uncertainty/PPA gate다. |
| encoder/decoder/serializer/buffer의 full-system 범위 | HIGH | S2A/PG | source/admission부터 RX/decoder/retire까지 complete endpoint로 평가하되 핵심부터 단계 확장한다. |
| 문제·동작 타당성이 세부 최적화보다 우선 | HIGH | 전 단계 | projection ratio가 좋아도 geometry, loss, queue, decoder, energy가 나쁘면 실패다. |
| 10/100 Gbps link 시나리오 | HIGH | S1/S2A/PG | 둘 다 **예시**일 뿐 target·요구치·pass threshold가 아니다. 팀이 events/s, burst, bits/event, width, clock, load를 정한다. |
| loss가 failure인지 metric인지 팀이 정의 | HIGH | S1/S2A/PG | exact profile의 accepted-event loss는 hard fail, source overrun은 capacity metric, lossy profile은 별도 명명한다. |
| 10 Mevent/s에서 90% loss 예시 | HIGH | S1/PG | generated, overrun, accepted, retired와 분모를 모두 쓰며 retired-only 수치로 숨기지 않는다. |
| 공식 traffic 부재 | HIGH | DX | full50, UZH, MVSEC를 competition official/canonical traffic이라고 부르지 않는다. |
| Samsung sensor data·표현 설명 제공 예정 | HIGH | DX | bytes와 문서가 오기 전 schema, polarity, sensor, license, official 지위를 추정하지 않는다. |
| UZH/UPenn 공개 dataset 참고 | HIGH | DX/S2B | 교수는 기관만 언급했다. 특정 UZH sequence와 MVSEC는 팀 선정이며, UZH rotation은 첫 supplied-pose 확장, MVSEC는 후속 6-DoF 후보로 분리한다. |
| 교육용 45 nm PDK와 primitive 제한 | HIGH | S2A/PG | 특정 primitive가 제한될 수 있다는 경고다. 절대 금지로 귀속하지 않고 library availability를 확인하며 ordinary one-posedge 대안을 기본으로 둔다. |
| Analog photocurrent·threshold·저조도/noise | LOW | 범위 밖 | digital MC-WTB claim 밖이며 stimulus/threshold 명시 원칙만 참고한다. |
| 고정 PPA target 없음·같은 function 비교 | MEDIUM | PG | 임의 area/power 수치를 교수 target으로 만들지 않고 동일 function/boundary Pareto로 비교한다. |
| 16/32/64-bit bus trade-off | MEDIUM | S2A/PG | width에 따른 pins, load, throughput, serializer/decoder와 power를 함께 sweep한다. |
| 느린→빈번 random load와 fixed bandwidth | MEDIUM | DX/PG | independent density, burst, hot-tile sweep과 최대 지속 event rate를 보고한다. |
| 과거 연구의 background 문제를 공략 | MEDIUM | S1 | P1/P4/P5를 팀 선정 baseline으로 삼되 broad novelty와 교수의 명시 지명 주장은 금지한다. |
| sensor 직접 입력 가정 | LOW/MEDIUM | S2A/PG | occurrence/capture boundary를 포함하며 post-retire stream만으로 final-system 주장을 하지 않는다. |
| polarity ON/OFF만, intensity 제외 | LOW/MEDIUM | S1/DX/S2A | polarity를 보존하고 intensity는 현 profile에서 제외하되 이 STT만을 유일한 권위로 삼지 않는다. |

Q&A 단위는 **10 Gbps와 100 Gbps**다. decimal line rate로 각각 1.25 GB/s와
12.5 GB/s지만 이는 단위 환산일 뿐 payload 처리율이 아니다. framing, coding,
control, idle, CRC, padding을 제외한 유효 event rate는 별도로 계산한다. 두 수치는
모두 `TEAM_DEFINED_LINK_SCENARIO`의 예시이며 organizer target이 아니다.

교수의 네 평가 순서에 대한 현재 총괄은 다음과 같다.

| 평가 순서 | 현재 판정 | 이유 |
|---|---|---|
| 1. Problem definition | `PASS_SCOPED / HOLD_GENERAL` | pure-rotation supplied-pose 질문은 고정됐지만 application, traffic, loss/quality threshold는 미완성 |
| 2. Innovation·successful solution | `HOLD` | synthetic causal witness는 있으나 production mutation과 equal-semantics full-system baseline 비교가 없음 |
| 3. Implementability·PPA·system scale | `HOLD` | bounded RTL, codec/decoder/link와 MC-WTB complete 45 nm cohort가 없음 |
| 4. Stability | `PASS_HARDENING_2 / HOLD_SYSTEM` | file/contract/publication hardening은 scoped PASS지만 event pipeline, queue, CDC/reset/wrap, feedback 안정성은 미검증 |

## 4. Stage-1 문제 정의와 현재 claim boundary

검증할 전체 질문은 다음과 같다.

> calibrated pure-rotation sensor에서 source/capture timestamp와 supplied pose가
> 주어졌을 때, event를 rotation-only reference ray tile로 warp하고 실제
> packet/decoder로 운반하면 동일 admitted event와 동일 loss/error 계약에서 raw와
> sensor-fixed 기준보다 total wire bits와 world-aligned error를 함께 개선하는가?

현재 PASS는 이 전체 질문의 coordinate-transform causal core와 hardening에만
해당한다.

- `world`는 metric 3-D point가 아니라 rotation-only reference ray/image다.
- coordinate out-of-FOV는 transport loss가 아니다.
- source array에서 이미 잃은 occurrence time이나 readout skew는 post-retire warp로
  복원되지 않는다.
- pose estimation, translation/depth, lens distortion, moving-object compensation,
  sensor global hold는 현재 구현과 claim 밖이다.
- motion compensation, event warping, coordinate transform, pose estimation의 최초
  발명을 주장하지 않는다.
- 현재 logical accounting은 packet-key projection 비교이지 codec·file size·link
  traffic 측정이 아니다.

## 5. P1/P4/P5 선행 경계와 재사용 가능한 메커니즘

| 근거 | 원 자료가 지지하는 것 | MC-WTB에 재사용 가능한 것 | 금지되는 확장 |
|---|---|---|---|
| P1 US9934557 | DVS event representation, confidence filtering, transform/matching; pose fusion·SLAM은 명세서 embodiment 수준 | supplied transform/pose를 적용하는 좌표 정합을 알려진 상위 문제의 부분집합으로 취급 | embodiment를 등록 청구항 선행성으로 확대, transform·pose estimation broad novelty, 특허가 RTL/PPA를 입증했다는 주장 |
| P4 Ryu 2019 workshop slides | individual AER, group addressing, unfair arbitration, generation/readout mismatch, motion artifact, global hold/sequential scan, remaining bandwidth issue | 병목 1·5·6의 팀 인과 taxonomy와 raw/sensor-fixed/readout-time baseline | peer-reviewed paper로 호칭, 심사 의도 추정, MC-WTB가 global hold나 capture artifact를 제거했다는 주장 |
| P5 Suh 2020 IEEE record + reviewed presentation evidence | 1280×960 fabricated sensor, in-pixel storage, sequential readout, global event holding, GIDL-suppressed reset, 2.5-Gbps 4-lane MIPI와 measured comparison | sensor 해법과 post-capture software 해법을 분리하고 complete-system 비용을 charge하는 기준 | 발표자료를 논문 본문 전체로 호칭, sensor·analog·MIPI PPA 전용, motion artifact elimination 또는 codec 선행성 주장 |

P5가 직접 보고한 것은 sensor 구성, sequential/global-hold 기능, interface와 측정
결과다. “MIPI 대역폭이 sequential scan의 비효율을 흡수했다”와 같은 인과 설명은
팀의 분석적 추론이지 P5 저자의 직접 결론이 아니므로 그대로 논문 claim으로
인용하지 않는다.

병목별 허용 조건은 다음과 같다.

- 병목 1: fixed binary packet과 decoder, metadata, escape, control, framing까지 포함한
  실제 total wire bits와 queue pressure가 같은 admitted set에서 감소해야 개선이다.
- 병목 5: arbitration/readout 이전 source/capture timestamp가 event identity와
  원자적으로 보존되어 decoder까지 전달돼야 한다. accept/retire time 재명명은
  해결이 아니다.
- 병목 6: 정확한 supplied pose, calibration, scene 가정에서 post-capture
  ego-rotation의 world-aligned error가 baseline보다 줄 때만 부분 개선이다.
  global hold, moving object, depth/translation이나 기존 snapshot skew까지 해결했다고
  말할 수 없다.

## 6. `PASS_HARDENING_2`의 보장과 한계

`76f62f3`과 `c863c0c`의 합성 계약 및 final detached acceptance에 근거해
Hardening 2는 PASS다. 허용되는 정확한 설명은 다음과 같다.

- known-motion의 public immutable `InputBlob`이 regular input을 한 번 읽고,
  parser와 SHA-256이 그 동일 bytes를 소비한다. 각 파일은 독립 snapshot이며
  세 파일의 원자적 snapshot은 아니다.
- nested fixed-v1 width source는 immutable이고 validator와 accounting이 공유한다.
  반환 JSON 결과는 mutable module state를 공유하지 않는다.
- `max_pose_age_ns`, parameter set, semantic model identity와 known-motion blob API
  identity가 결과 계약에 결합된다. 이는 binary·commit cryptographic attestation이
  아니다.
- 필수 dirfd, no-follow, same-dir rename 기능을 지원하는 POSIX에서 final parent
  directory를 고정하고 그 안에 temp를 생성한다. 지원 기능이 없으면 weak path
  fallback 없이 실패한다. final parent symlink는 의도적으로 거부한다.
- commit-time no-follow lstat에서 보이는 input-inode hardlink, symlink,
  non-regular target을 거부한다. 검사 뒤 같은-directory writer가 만든 alias entry는
  pinned rename이 entry 자체를 교체하므로 rename을 다른 directory로 redirect하거나
  alias victim content를 변경하지 않는다.
- rename 뒤 현재 parent identity와 pinned target inode를 재검사한다. 늦은 parent
  redirect나 관찰 가능한 target replacement는 성공으로 반환하지 않는다.
- cleanup 중 temp close, unlink, parent close는 앞선 cleanup 실패와 무관하게 모두
  시도하며 composite `InterfaceError`가 primary error, cleanup details, temp name과
  close uncertainty를 보존한다.

다음 한계도 PASS 문구와 항상 함께 유지한다.

- rename 후 검증 실패 때 결과가 이동된 pinned directory에 이미 존재할 수 있다.
  rollback을 하지도 주장하지도 않는다.
- cleanup syscall 자체가 실패하면 temp가 남거나 FD close 상태가 불확실할 수 있다.
  물리적 cleanup 성공이 아니라 잔류 상태의 명시적 보고를 보장한다.
- same-directory hostile writer에 대한 CAS, exclusion 또는 final check 이후 race 방어를
  주장하지 않는다.
- `O_NOFOLLOW`는 final parent component에 적용된다. `parent.mkdir`와 ancestor path
  resolution은 일반 path 연산이며, 전체 ancestor chain의 symlink-free confinement나
  Linux `openat2(RESOLVE_BENEATH/NO_SYMLINKS)`를 제공하지 않는다.
- main-path temp close가 이미 실패한 경우 ambiguous numeric FD를 blind retry하지
  않는다. cleanup의 일반 `Exception` 수집을 모든 `BaseException` 처리 보장으로
  확대하지 않는다.
- file/directory `fsync`가 없으므로 atomic visibility만 제공하고 crash durability는
  제공하지 않는다.
- network/custom/hostile filesystem의 transaction semantics나 모든 namespace race를
  검증했다는 주장은 금지한다.

`input_provenance.stability_scope`는 v1 호환 alias로 유지되며
`snapshot_scope`와 같은 독립-snapshot 문구를 가진다. public
`LOGICAL_BIT_FORMAT`은 read-only mapping, `UNSUPPORTED_FEATURES`는 tuple이라는
compatibility change도 유지한다.

## 7. Dataset 역할과 승격 조건

### UZH DAVIS

공식 UZH Event-Camera Dataset의 `shapes_rotation`은 첫 real supplied-rotation
후보다. Stage-1 승격 artifact는 original event identity/coordinate, immutable
source/member digest, camera calibration, pose sample identity, timestamp/frame
convention, interpolation rule, pose age, stale/missing/OOF counters와 zero-drop
receipt를 포함해야 한다. 현재 local 4×4 projection이나 작은 byte window는
original-geometry replay가 아니며 real evidence로 승격하지 않는다.

`dynamic_rotation`은 moving-object red-team으로 분리하고, translation/6-DoF는
rotation-only profile의 PASS가 아니라 expected-reject 또는 future profile이다.

### MVSEC

MVSEC는 stereo event data, IMU와 pose/depth 계열 ground truth를 제공하는 후속
6-DoF·estimator 후보이다. official data-format 계약에 맞춘 streaming importer,
calibration/rectification, timestamp와 pose 의미를 먼저 고정해야 한다. 초기
pure-rotation supplied-pose gate를 대체하지 않으며 S2B estimator와 depth-aware
extension에 사용한다. 교수는 Q&A에서 UPenn을 기관 수준으로 언급했을 뿐 MVSEC를
특정 dataset으로 지명하지 않았으며, MVSEC 선택은 팀의 후속 설계 결정이다.

### Samsung

교수 Q&A는 sensor data와 표현 설명의 향후 제공을 말했지만, 현재 bytes나 authority를
제공한 것은 아니다. 다음을 모두 받기 전 `HOLD_UNSUPPORTED_FORMAT`이다.

1. immutable source bytes와 digest
2. 공식 record/field/timebase/polarity/sensor 설명
3. acquisition identity와 license/redistribution 조건
4. official/canonical designation의 서면 범위
5. importer schema와 zero-drop/conservation receipt
6. versioned extension 및 actual replay receipt

제공자의 소속이나 P4/P5 sensor 계보만으로 format, license, semantics 또는 대회
official 지위를 추정하지 않는다.

## 8. 다음 software, data, RTL, 45 nm gate

### G1 — Software transport·causal gate

source-time warp, bounded world-tile accumulator, multiplicity/time residual, RAW escape,
고정 binary packet grammar와 decoder를 구현한다. raw event, sensor-fixed tile,
source-time no-warp, retire-time warp, ideal floating-point warp를 동일 admitted set과
equal-bit/equal-loss 계약으로 비교한다.

PASS 조건은 independent mutation/discrimination campaign과 사전 고정한
rotation, density, burst, pose-age, boundary envelope에서 actual total wire bits와
world-aligned error를 동시에 개선하는 것이다. 그 전까지 일반 Stage-1은 HOLD다.

### G2 — Real data gate

official URL/license와 immutable member digest, original event/calibration/pose
identity, frame/time convention, interpolation, stale/missing/OOF counters와 zero-drop
conservation을 갖춘 UZH pose-join artifact를 만든다. synthetic와 real evidence class를
분리한다. MVSEC와 Samsung은 각 promotion 조건 전에는 포함하지 않는다.

### G3 — Stage-2A supplied-pose RTL gate

RTL 전에 image/tile size, fixed-point range/round/saturation/error, timestamp/version
width, pose/LUT size와 effective-time switching, lanes, banks/ports, simultaneous-write
policy, finite accumulator/residual/RAW/packet FIFO, packet/link/decoder rate와 legal
memory fallback을 수치로 고정한다.

PASS 조건은 bit-exact software equivalence, source timestamp/address/polarity atomic
capture, overflow/RAW behavior, packet decode, worst-case service/loss가 ordinary
one-posedge legal cells에서 bounded한 것이다. qualified SRAM이 없으면 sensor-scale
profile은 HOLD하고 flop-only demonstrator와 혼합하지 않는다.

### G4 — Stage-2B feedback estimator gate

supplied-pose RTL 이후에만 feedback estimator를 추가한다. UZH/MVSEC에서 accuracy,
latency, update rate, uncertainty, stale/failure와 on-chip PPA를 평가한다. off-chip이면
interface bandwidth, latency와 failure를 complete boundary에 charge한다.

### G5 — MC-WTB complete-endpoint 45 nm gate

source/admission→timestamp/pose/LUT→warp/tile banks→residual/RAW FIFO→packetizer/link
→RX/decoder/retire/error의 동일 boundary를 사용한다. 기존 A2/A3 scheduler endpoint,
core-only 또는 diagnostic PPA를 MC-WTB cohort에 더하거나 전용하지 않는다.

PASS에는 accepted exact-once 또는 별도 lossy profile, generated/overrun/accepted/
retired conservation, burst·maximum sustainable rate·latency·queue age, 분리된 loss,
organizer-authorized GPDK045와 legal cells/memory/common I/O receipt, post-route
setup/hold·area·DRC·antenna·connectivity·congestion, mapped vectorless power 및
CDC/RDC·pose/config atomicity·reset/drain·stall/overflow·packet-fault 안정성이 모두
필요하다.

## 9. 정확한 claim 금지 목록

다음 또는 동등한 주장은 해당 gate 전까지 금지한다.

- `PASS_SYNTHETIC_CAUSAL_DISCRIMINATION`
- 일반 `PASS_STAGE1`, real-data generalization 또는 real pose-joined PASS
- actual wire bandwidth/bitrate reduction 또는 codec benefit
- codec/reversible transport, lossless original-event reconstruction
- RTL/PPA readiness, synthesizable/45 nm ready, MC-WTB complete-endpoint PPA PASS
- 병목 1·5·6의 완전 해결 또는 motion artifact elimination
- metric 3-D world reconstruction, translation/depth 또는 moving-object 지원
- feedback pose estimation 구현
- sensor global hold, in-pixel capture, GIDL 또는 analog sensor 문제 해결
- 10/100 Gbps가 organizer target, 요구치 또는 pass threshold라는 주장
- full50, UZH, MVSEC, Samsung이 competition official/canonical traffic이라는 주장
- Samsung format, license, sensor semantics 또는 authority의 추정
- 기존 A2/A3 PPA를 MC-WTB PPA로 전용하거나 core-only와 complete endpoint를 혼합한 순위
- `PASS_HARDENING_2`를 crash durability, rollback, hostile same-directory CAS,
  cleanup syscall 성공, 세 input의 atomic snapshot 또는 모든 filesystem의 race-safe
  transaction으로 확대하는 주장
- 교수가 P1/P4/P5를 직접 지명·권고했거나 MC-WTB 해법을 요구했다는 주장
- motion compensation, event warping, coordinate transform 또는 pose estimation의
  최초/broad novelty 주장

현재 허용되는 한 줄 결론은 다음과 같다.

> `MC-WTB Stage-1 = PASS_SYNTHETIC_CAUSAL_CORE + PASS_HARDENING_2; HOLD general Stage-1 acceptance, real pose-joined data, actual codec/wire benefit, RTL, and MC-WTB complete-endpoint 45 nm PPA.`
