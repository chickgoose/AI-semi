# Cluster2 AER → CAV → World Grid 1차 브리핑

발표일: 2026-08-28
증거 기준: `integration/cluster2-steal-buf-cav-bridge` committed evidence only

> 발표용 한 문장: pinned `cluster2_steal_buf` RTL의 sealed Xcelium
> simulation에서 관측한 8,503개 native outcome을 공식 UZH event와
> TB-side identity로 observational software join하고, 동일 원본 event
> timestamp를 쓰는 causal-CAV software baseline에서 8,420개 WORLD ray를
> 512×256 software grid로 mapping했다. 이것은 기능 확장 경로의
> scoped feasibility 증거이며 wire-complete CAV RTL, latency-quality,
> CAV/world PPA 증거가 아니다.

## 1. 문제 정의

`cluster2_steal_buf`는 16개 source의 event를 source당 depth-2 buffer에
받고, 최대 두 row/bitmap lane으로 retire하는 native AER다. 하지만
native 출력을 motion-aware CAV와 world-coordinate grid로 연결하려면
다음 문제를 분리해야 한다.

1. native AER에 없는 event identity와 원본 UZH timestamp를 어떻게
   손실 없이 다시 연결할 것인가?
2. UZH sensor time, 1 ms workload bin, CAV logical cycle, native retire cycle을
   섞지 않고 어떻게 관리할 것인가?
3. native scheduling latency가 geometry를 바꾸지 않은 채, 현재 CAV
   baseline과 world-grid 경로를 재사용할 수 있는가?

이 1차 결과의 질문은 **연결 가능성**이다. AER scheduling이 CAV
정확도나 영상 품질을 개선했는지는 측정하지 않았다.

## 2. 검증한 architecture flow

```text
official UZH shapes_rotation
  events.txt + groundtruth.txt + calib.txt
                    |
                    | exact raw/cyclemask crosswalk
                    | (pinned converter bytes는 있지만 이 runner가
                    |  converter 실행을 재현하지는 않음)
                    v
             pinned 4x4 cyclemask
                    |
                    v
 pinned cluster2_steal_buf + observational TB
          sealed Xcelium simulation
                    |
                    v
       8,503 native transport outcomes
                    |
                    | TB-side event ID + source + native occurrence
                    v
        observational software exact join
                    |
                    v
      shared occurrence-time neutral input
           /                |                \
      RAW-CAV          AER-OCC-CAV       AER-RET-CAV
           \                |                /
            same software CAV geometry replay
                    |
          8,420 WORLD + 83 SENSOR_FIXED
                    |
                    v
       512x256 software world-grid mapping
```

native boundary의 committed 계약은 16-bit `arrival`/`overrun`과 두 개의
registered `valid,row,col_mask` lane이다. 두 lane은 한 cycle에 서로 다른
legal row를 선택하며, 각 4-bit bitmap을 펼치면 최대 8 events/cycle을
표현할 수 있다. TB-side event ID는 DUT를 drive하지 않고 native AER
payload에도 없다.

세 view는 같은 event population, identity, pose semantics와 같은 geometry
object를 공유한다. `AER-RET-CAV`만 `(retire_cycle,event_id)` sidecar
presentation order와 native retire cycle/latency를 보존한다. native
lane/row/column은 sealed evidence에만 남고 public functional sidecar가
재구성하지 않는다. scorer/selector label은 이 경로의 입력이 아니다.

> 그림 1 — [population flow](assets/cluster2_cav_population_flow.svg)
> data source: [official result](../../benchmarks/redred_cluster2_cav_bridge/results/official_uzh_cluster2_cav_result.json),
> [native authority](../../benchmarks/redred_cluster2_cav_bridge/ganghee_cluster2_native_authority.json)

## 3. 정확한 결과

### Native simulation observation

| 항목 | committed 결과 | 발표 경계 |
| --- | ---: | --- |
| generated | 8,503 | pinned cyclemask population |
| delivered | 8,503 | sealed Xcelium RTL simulation observation |
| overrun | 0 | 이 one-cycle native-pulse workload에 한정 |
| simulation errors / fatals | 0 / 0 | Xcelium 23.09-s013 receipt |
| latency 1 cycle | 6,393 | observational transport sidecar |
| latency 2 cycles | 2,077 | observational transport sidecar |
| latency 3 cycles | 33 | observational transport sidecar |

### Software CAV and world-grid replay

| 항목 | committed 결과 |
| --- | ---: |
| selected UZH events | 8,503 |
| pose packets | 11,883 |
| observational exact join | 8,503 |
| causal-CAV WORLD rays | 8,420 |
| fresh-ZOH fallback | 0 |
| SENSOR_FIXED bypass | 83 |
| software grid | 512 × 256 |
| quantized WORLD inputs | 8,420 |
| unique cells | 821 |
| occupied x range | 238..298 |
| occupied y range | 93..165 |
| row-major index range | 47,876..84,754 |

`SENSOR_FIXED` 83건은 WORLD로 오표기하지 않고 grid 집계에서 제외했다.
`RAW-CAV`, `AER-OCC-CAV`, `AER-RET-CAV`의 geometry digest는 모두
`3f6b09f3208582907b588ad679bd60871c694ec31eff46423b0240ceb2f15747`로
같다.

### Result identity

| 대상 | SHA-256 |
| --- | --- |
| event join | `bfbd23b607cc7d68371133e7d67da43c2302641391b4cdeac572013eaab256b2` |
| geometry | `3f6b09f3208582907b588ad679bd60871c694ec31eff46423b0240ceb2f15747` |
| retire sidecar | `c29d9b980674da62d48e3a4cb0dc26618d08a3658997a7a5e90eb15ef81b6897` |
| world grid | `f5cb124031b2a343b55a85f92902bd8b764bc865298d9de58ee86f60e49048e0` |
| official result seal | `b967c0bde609a5660abded586832355c214c907a3dd69b036d20f01fdb0ea123` |
| scoped replay receipt file | `2b64c3ea333ba60e11444a682064dba4b9b9017779393d5a9a0387a0d607cd3b` |
| scoped replay receipt seal | `f7cfd24a05e664e36a99b06ab48fd193a7ff811f078cfbcb7e10c37c598b7a3e` |

공식 외부 source와 accepted LF cyclemask를 사용한 exact golden replay도
재실행해 sanitized log와 canonical receipt로 봉인했다. 이 판정은
`PASS_LOCAL_EXACT_GOLDEN_REPLAY_NOT_SIGNED_OR_HARDWARE_ATTESTATION`이며,
서명된 제3자 attestation이나 hardware/CAV RTL/PPA/performance 증거가 아니다.

> 그림 2 — [native latency histogram](assets/cluster2_cav_latency_histogram.svg)
>
> 그림 3 — [WORLD grid full-domain bounding range](assets/cluster2_cav_world_grid_coverage.svg)
>
> 그림 3은 전체 512×256 domain 안에서 관측된 `x=238..298`,
> `y=93..165` bounding range를 보여주는 시각화이다. cell별 발생량,
> 밀도 또는 강도를 표현하는 occupancy heatmap으로 해석하지 않는다.

## 4. 서로 바꾸어 쓰면 안 되는 네 시간축

| 시간축 | 정확한 의미 | 사용처 | 금지된 해석 |
| --- | --- | --- | --- |
| `event_timestamp_ns` | 원본 UZH sensor timestamp, ns | geometry order, pose lookup, neutral CAV input | native retire time으로 대체 |
| `native_occurrence_cycle` | `int(float(timestamp_text)/0.001)`로 만든 1 ms workload bin | cyclemask/source identity, native observation join | 2 ns hardware physical timestamp로 해석 |
| `cav_occurrence_cycle` | `ceil((event_timestamp_ns-window_start_ns)*1000/6500)`인 독립 6.5 ns software logical cycle | strict-past pose visibility, current CAV replay | native occurrence/retire cycle과 혼용 |
| `retire_cycle` | 2 ns Xcelium native clock에서 관측한 retirement cycle index | `(retire_cycle,event_id)` sidecar order, latency | CAV geometry의 pose-lookup time으로 사용 |

sidecar의 `latency_cycles = retire_cycle - native_occurrence_cycle`이고,
`latency_ns = latency_cycles × 2 ns`이다. `latency_injected_timestamp_ns` 또한
`event_timestamp_ns + latency_ns`로 만든 관측용 파생값이다. 이 값의
semantics는 `TRANSPORT_LATENCY_INJECTION_NOT_PHYSICAL_REPLAY`이며, 다섯 번째
물리 시간축이나 latency-quality 실험이 아니다.

geometry는 `event_timestamp_ns`와 `cav_occurrence_cycle`만 참조한다. native
occurrence/retire와 latency는 geometry에 주입하지 않고 `AER-RET-CAV`의
separate observational sidecar에만 보존한다.

## 5. PASS / HOLD

| 판정 | 증거 경계 |
| --- | --- |
| **PASS** | pinned RTL bytes의 sealed Xcelium native functional observation 8,503건 |
| **PASS** | source/TB-side event ID/native occurrence의 8,503건 observational exact join |
| **PASS** | native overrun 0과 1/2/3-cycle latency histogram 보존 |
| **PASS** | 동일 원본 UZH event timestamp를 쓴 software causal-CAV replay |
| **PASS** | 8,420 WORLD ray의 512×256 software world-grid mapping |
| **PASS** | canonical result seal, 입력 hash, 30-file runtime code authority 재검증 |
| **HOLD** | wire-complete CAV/world RTL 구현과 관측 |
| **HOLD** | CAV/world Genus, STA, power, Innovus P&R |
| **HOLD** | retire timestamp를 geometry에 주입한 latency-quality 비교 |
| **HOLD** | predictor, online feedback, depth/translation/parallax 보정 |
| **HOLD** | native AER scheduling이 CAV 정확도를 높인다는 주장 |
| **HOLD** | 다른 UZH sequence와 pan/tilt 강도로의 일반화 |

Ganghee native AER의 별도 PPA/P&R 자료는 이 software-CAV official
result authority에 포함되지 않는다. 수치를 쓸 경우에는 원본 report
상대경로, RTL hash, corner/constraint, activity 조건, signoff 한계를
별도로 밝혀야 한다. native PPA를 software CAV PPA로 합쳐 말하지
않는다.

### Native PPA 현재 진단

| 대상 | 현재 말할 수 있는 범위 | 현재 HOLD |
| --- | --- | --- |
| pinned 원본 `cluster2_steal_buf` | 2.0 ns Genus mapped screening: area 700.074, setup +0.224 ns, vectorless 0.127932 mW | 원본 Innovus P&R, exact Fmax, activity power |
| 별도 polarity-extended top | 제출 패키지에 봉인된 3.5 ns Innovus observation: area 1254.114, setup/hold +0.454/+0.167 ns, vectorless 0.10738887 mW, internal DRC/antenna 0 | 원본 top으로의 귀속, exact Fmax, workload power, signoff 확대 |

polarity-extended top은 source별 2-slot polarity FIFO와 polarity I/O를 추가한
다른 RTL이며 pinned 원본과 같은 후보로 합치면 안 된다. 두 자료의 조건,
상대경로, SHA와 금지 주장은
[PPA diagnostic handoff](ganghee_cluster2_ppa_diagnostic_handoff_20260824.md)에
분리했다. 현재 `evidence/ppa/upstream_9b0d951/`에 원본 report bundle과 전체
SHA-256 manifest가 봉인되어 있으므로 polarity top의 위 제한된 관측값은
제출할 수 있다. 다만 exact Fmax, VCD/SAIF workload power, foundry signoff와
CAV/world RTL PPA는 계속 HOLD다.

## 6. 발표 역할 분담

| 담당 | 발표 범위 | 자신의 파트에서 반드시 밝힐 한계 |
| --- | --- | --- |
| Ganghee(강희) | `cluster2_steal_buf` 문제, depth-2/source 구조, 두 row/bitmap retire lane, sealed native simulation | event ID가 native payload가 아님; one-cycle pulse workload 범위; PPA/P&R는 별도 authority 필요 |
| Junyoung(준영) | UZH raw/cyclemask crosswalk → native outcome → CAV → world grid 통합, 네 시간축 | converter를 이 runner가 재실행하지 않음; retire timing은 geometry가 아닌 sidecar |
| Hyunsoo(현수) | fresh-clone 재현, official JSON/seal/digest, 그림·슬라이드 수치 교차검증 | PASS/HOLD 표현, software/RTL, simulation/chip measurement 경계 검사 |

이 표는 발표 운영을 위한 권고 분담이며 Git commit authorship의
증명으로 사용하지 않는다.

## 7. Speaker-safe claims

### 그대로 말해도 되는 표현

- “pinned Cluster2 RTL을 Xcelium에서 simulation해 8,503개 native outcome을
  sealed evidence로 보존했다.”
- “TB-side identity를 사용해 8,503개 UZH event와 native outcome을
  observational software exact join했다.”
- “세 view는 같은 occurrence-time geometry를 사용하고,
  `AER-RET-CAV`만 retirement latency를 sidecar로 가진다.”
- “software CAV replay에서 8,420개 WORLD ray와 83개 SENSOR_FIXED
  bypass를 구분했다.”
- “8,420개 WORLD ray를 512×256 software grid의 821개 unique cell에
  mapping했다.”
- “이 결과는 event-identity 수준의 software functional extension
  feasibility를 보인다.”

### 금지하거나 HOLD를 붙여야 하는 표현

- “실제 칩에서 측정했다” → **금지:** Xcelium RTL simulation observation이다.
- “converter부터 CAV까지 전체를 재실행했다” → **금지:** converter
  bytes와 cyclemask는 pinned되었지만 converter execution은 재현하지 않았다.
- “event ID를 AER wire로 전송했다” → **금지:** TB-side observational
  identity이다.
- “wire-level CAV interface compatibility를 입증했다” → **HOLD:** software
  join과 wire-complete RTL은 다르다.
- “retire timestamp를 쓰니 품질이 좋아졌다” → **HOLD:** retire timing은
  geometry에 주입하지 않았다.
- “CAV/world RTL, memory, PPA를 구현했다” → **HOLD:** 현재는
  software ray/grid mapping이다.
- “AER scheduling이 CAV accuracy를 개선했다” → **HOLD:** 세 view의
  geometry가 의도적으로 같다.
- “다른 sequence와 motion에도 일반화된다” → **HOLD:** 이 결과는
  `shapes_rotation` selected population에 한정된다.

## 8. 슬라이드 구성 권고

1. 문제 — AER retire와 motion geometry의 시간·identity 계약을 분리한다.
2. native 구조 — 16 sources, depth-2/source, two row/bitmap lanes.
3. 전체 흐름 — UZH/cyclemask → sealed native observation → observational
   join → software CAV → world grid.
4. 네 시간축 — sensor ns / 1 ms workload bin / 6.5 ns CAV logical cycle /
   native retire cycle.
5. 결과 — 8,503 join, 8,420 WORLD, 83 bypass, latency histogram, 821 cells.
6. 증거 경계 — PASS/HOLD 표와 speaker-safe claims.
7. 후속 — wire-complete CAV/world RTL, latency-quality experiment, PPA,
   predictor/feedback.

## 9. Committed evidence index

- [1차 상태 문서](../REDRED_CLUSTER2_CAV_1ST_ROUND_STATUS_20260824.txt)
- [발표 claim evidence matrix](cluster2_cav_evidence_matrix_20260824.md)
- [Ganghee native PPA diagnostic handoff](ganghee_cluster2_ppa_diagnostic_handoff_20260824.md)
- [bridge 계약과 범위](../../benchmarks/redred_cluster2_cav_bridge/README.md)
- [official canonical result](../../benchmarks/redred_cluster2_cav_bridge/results/official_uzh_cluster2_cav_result.json)
- [scoped local exact-replay receipt](../../benchmarks/redred_cluster2_cav_bridge/results/official_uzh_cluster2_cav_replay_receipt.json)
- [sanitized exact-replay log](../../benchmarks/redred_cluster2_cav_bridge/results/official_uzh_cluster2_cav_replay_receipt.log)
- [official runner](../../benchmarks/redred_cluster2_cav_bridge/official_functional_run.py)
- [native source/interface authority](../../benchmarks/redred_cluster2_cav_bridge/ganghee_cluster2_native_authority.json)
- [sealed Xcelium receipt](../../benchmarks/redred_cluster2_cav_bridge/server_native_observation_receipt.json)
- [sealed evidence bundle](../../benchmarks/redred_cluster2_cav_bridge/evidence/server_native_observation_ca446aa.tgz)
- [raw/cyclemask crosswalk](../../benchmarks/redred_cluster2_cav_bridge/source_crosswalk.py)
- [official functional source](../../benchmarks/redred_cluster2_cav_bridge/functional_source.py)
- [functional assay and three views](../../benchmarks/redred_cluster2_cav_bridge/functional_assay.py)
- [dual-time sidecar](../../benchmarks/redred_cluster2_cav_bridge/transport_time.py)
- [world-grid quantizer](../../benchmarks/redred_cluster2_cav_bridge/world_grid.py)

---

최종 발표 규칙: **simulation은 simulation으로, software는 software로,
observational identity는 wire payload와 분리해 말한다.**
