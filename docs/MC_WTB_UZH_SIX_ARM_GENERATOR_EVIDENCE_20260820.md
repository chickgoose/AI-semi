# MC-WTB UZH six-arm generator evidence — 2026-08-20

## 1. 판정

이 문서는 UZH `shapes_rotation` source-bound six-arm 작업의 구현, 독립 수치 oracle,
그리고 실제 A23 endpoint negative evidence를 하나의 claim ledger로 묶는다. 세 축은 서로
대체하지 않는다.

```text
PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED
PENDING_FINAL_IMPLEMENTATION_AND_TEST_HASHES

PASS_INDEPENDENT_AVAILABLE_FIVE_ARM_NUMERICAL_ORACLE
PASS_WRONG_AND_DELAYED_CONTROLS_INFORMATIVE_THIS_WINDOW

HOLD_SOURCE_BOUND_RETIRE_TIMESTAMPS
HOLD_RETIRE_WARP_MISSING_ACTUAL_RECEIPT
HOLD_OFFICIAL_SIX_ARM_GENERATOR
HOLD_COMPLETE_SIX_ARM_V2_ARTIFACT_NOT_GENERATED
HOLD_MC_WTB_REAL_DATA_BENEFIT
```

`PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED`는 strict generator/inspector, independent
geometry, deterministic package 및 fail-closed retire-input 계약의 **software 구현 범위**에만
해당한다. 최종 generator commit, source/schema/test blobs와 detached run receipt의 hashes는 이
문서 작성 시점에 아직 동결되지 않았으므로 archival hash binding은 `PENDING`이다.

Pinned official UZH source에서 `RAW`, `SENSOR_FIXED`, `MC_CORRECT`, `MC_WRONG`,
`MC_DELAYED` 다섯 available arm은 1,100 event 전체에 대해 독립 계산으로 수치 검증했다.
하지만 A23 actual RTL replay는 generated 1,100 중 81 source overrun, accepted/retired 1,019이므로
full-cohort retire authority가 아니다. 따라서 `RETIRE_WARP`를 만들 수 없고 official six-arm
artifact도 발행할 수 없다. 누락 81건의 retire time을 복사, 평균, 상수 지연, cycle 환산 또는
보간으로 채운 fake receipt는 금지한다.

이 PASS/HOLD 조합은 codec, packet/wire bits, bandwidth, throughput benefit, causal hardware,
RTL, timing, power 또는 PPA 결과가 아니다.

## 2. 근거 문서와 authority 순서

이 문서는 다음 read-only reports에서 작성했다. SHA-256는 보고서 bytes의 identity이며,
보고서 안의 source/artifact hashes를 대신하지 않는다.

| authority | 역할 | report SHA-256 |
|---|---|---|
| `/tmp/uzh-sixarm-contract-final-a2.md` | 최종 최소 API/schema/status/claim 계약 | `de65916699d5b335915f50f3dcd3837ea430a6a5d26975a0730a2c7352f05cec` |
| `/tmp/uzh-sixarm-oracle-a6.md` | production helper 비사용 five-arm 독립 수치 oracle | `ef78a53a8714373ff394086f897e55fe01c56fe464568910d5a6566e52fd9b19` |
| `/tmp/uzh-sixarm-a23-negative-a5.md` | A23 1x actual RTL 81-overrun negative acceptance recipe | `02d70c2695b6c7f1d54b1302b3edf780099d9f39b4471ed4c8f5abb7cfce6779` |

충돌 시 contract-final A2의 public grammar와 status가 우선한다. A6는 다섯 available arm의
수치 golden이고, A23는 actual endpoint completeness를 부정하는 negative authority다.

Repository의 상위 맥락은
[`MC_WTB_STAGE1_QNA_TRACEABILITY_20260820.md`](MC_WTB_STAGE1_QNA_TRACEABILITY_20260820.md),
[`MC_WTB_UZH_STAGE2_EVIDENCE_20260820.md`](MC_WTB_UZH_STAGE2_EVIDENCE_20260820.md),
[`MC_WTB_UZH_STAGE3_ADAPTER_EVIDENCE_20260820.md`](MC_WTB_UZH_STAGE3_ADAPTER_EVIDENCE_20260820.md)
의 claim 경계를 유지한다.

## 3. 고정 official-source identity와 cohort

### 3.1 UZH archive와 members

```text
local archive /tmp/uzh-shapes_rotation.zip
archive SHA-256
  56aade6bf53dcf73e8fe40905ccac8385cd7606bc9a85103bf2c9f9045117551

events.txt
  d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda
groundtruth.txt
  bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb
calib.txt
  ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd
```

### 3.2 Source-bound pose join과 adapter

| object | SHA-256 |
|---|---|
| pose-join `events_pose_join.jsonl` | `a49b7d813fde313bfbcc27526e337c7268ab11803a19898feee8f27afc576796` |
| pose-join `poses.jsonl` | `4461d867e8adc8daaeb089fc739613ee7c89ac2f32c825de561ba88ff83ca0c1` |
| pose-join `calibration.json` | `bf718266f210e0bf7d64ff31b1fb4d125f905b0f67d6070976bdaf25ec450cdb` |
| pose-join `receipt.json` | `85c182e1daa2f380dffa34a559ae2093835b1052c3d9d9a7f5a1f014a9974f87` |
| pose-join `COMPLETE.json` | `c7692b20dc7d1f305a723cff695b9b794421fdfd39d6a021a17876c56d155756` |
| join spec raw bytes | `04a81a809164556f744e55b075b94cbc7e2042ccb714e0e03fab8d4aa55a177e` |
| adapter `events_mc_wtb_adapter.jsonl` | `a8a78cab40e8679cd98b50d78cda5df5c93e55ec100227862c0ad1b611bf599a` |
| adapter `receipt.json` | `f34655799be9b29d82774cf3210f4f870eb396024cdf18f69bb4e48c6bda0197` |
| adapter `COMPLETE.json` | `7919657165b5a44696ee34e5d5f1bdab22a21ee2f09f0f97078ae99284ac7b25` |

`official_uzh_source_input=true`는 위 qualified source chain을 뜻한다. Downstream generated
artifact가 UZH가 배포한 official artifact라는 뜻은 아니므로
`generated_artifact_official_uzh=false`를 유지한다.

### 3.3 Equal-ID cohort

```text
source window                     [41,321,000,000, 41,322,000,000) ns
event count                       1,100
dataset_event_index               13,856,250 .. 13,857,349
join_sequence_index               0 .. 1,099
polarity 0 / 1                    674 / 426
adjacent timestamp tie extras     458

ordered decimal ID + LF SHA-256
  0eb870ed84539b786d8944330d0618509b7e331eab4ca4b4bba21bc51c3e44f0
compact JSON ID array + LF SHA-256
  3bfedeb52763572d42d285b5b7483356f5156e535e657ecb67b0f1f7cf2a90ac
```

어떤 arm도 OOF, invalid, accepted subset 또는 retire subset을 이유로 이 분모를 줄일 수 없다.

## 4. Generator implementation scoped PASS

### 4.1 Public contract

Companion generator는 existing pose join, adapter 및 controls evaluator를 수정하지 않고 다음
API를 제공한다.

```python
generate(pose_join_dir, join_spec_path, adapter_dir,
         retire_receipt_path, generator_spec_path, result_dir) -> dict

inspect(result_dir, pose_join_dir, join_spec_path, adapter_dir,
        retire_receipt_path, generator_spec_path) -> dict
```

동결된 주요 schema/status는 다음과 같다.

```text
generator spec       redred.uzh_mc_wtb_controls.generator_spec/v1
retire stream        redred.uzh_mc_wtb_controls.retire_stream/v1
retire record        redred.uzh_mc_wtb_controls.retire_record/v1
generator receipt    redred.uzh_mc_wtb_controls.generator_receipt/v1
generator completion redred.uzh_mc_wtb_controls.generator_completion/v1

production status    PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED
fixture status       PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE
implementation       PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED
promotion            HOLD_MC_WTB_REAL_DATA_BENEFIT
```

Canonical controls preregistration은 다음 exact bytes에 묶인다.

```text
parameter_set_id UZH-S2-CONTROLS-8X8-1MS-V2
SHA-256          db7f5d5f9dc2c055c6f8430bc14ce268c02b37c4085f4bfd92b1d871c25f2f58
```

Production parameter set은 reference timestamp `41,321,000,000 ns`, delayed delta
`4,998,186 ns`, exact 1,100 IDs와 위 source/adapter/preregistration pins를 실행 전에 고정한다.

### 4.2 Required six-arm semantics

```text
RAW           no-pose raw ray and raw locality
SENSOR_FIXED  occurrence-pose correct ray/status, raw locality
MC_CORRECT    occurrence-pose correct ray and projected locality
MC_WRONG      exact correct relative rotation의 transpose control
MC_DELAYED    pose lookup at occurrence - 4,998,186 ns
RETIRE_WARP   pose lookup only at externally observed per-ID retire_timestamp_ns
```

Independent quaternion order는 xyzw, pose는 camera-to-world `T_WC`, interpolation은 normalized
shortest-arc SLERP다. Translation은 보존하되 depth/plane 없이 ray에 적용하지 않는다. Continuous
bounds를 rounding 전에 판정하고 in-FOV pixel은 `floor(value+0.5)`를 사용한다.

Required output inventory는 정확히 다음 세 파일이다.

```text
controls_six_arm.jsonl
receipt.json
COMPLETE.json
```

Inspector는 original pose join, join spec, adapter, retire receipt, generator spec과 canonical
preregistration으로 artifact/receipt/evaluator result를 다시 계산해야 한다. Package 내부 hash만
일관되게 고친 rewrite는 PASS가 아니다. Missing/invalid retire input은 partial five-arm artifact나
placeholder `RETIRE_WARP`를 발행하지 않고 fail-closed해야 한다.

### 4.3 Pending final hash binding

다음 값은 implementation-scoped PASS의 archival identity를 닫기 전에 반드시 추가한다. 현재
값을 추정하거나 개발 중 worktree hash로 대신하지 않는다.

| required final identity | 현재 상태 |
|---|---|
| integrated generator commit | `PENDING_FINAL_HASH` |
| generator implementation source | `PENDING_FINAL_HASH` |
| generator spec/retire/receipt/completion schemas | `PENDING_FINAL_HASHES` |
| native and independent test sources/runner | `PENDING_FINAL_HASHES` |
| clean test command, exact pass/skip count and detached log | `PENDING_FINAL_RECEIPT` |
| synthetic fixture output/receipt/COMPLETE | `PENDING_FINAL_HASHES` |

이 pending은 software 의미 범위의 PASS를 official execution PASS로 승격하지 못하게 하는 release
gate다. Actual official retire receipt와 production six-arm hashes는 구현 hash와도 별개의 external
HOLD다.

## 5. 다섯 available official-source arms의 독립 수치 검증

A6 계산기 `/tmp/uzh-sixarm-oracle-calc-a6.py`는 Python standard library만 사용하고 repository
module 또는 production geometry helper를 import하지 않았다.

```text
oracle script SHA-256
  ce0b8a68d1d9faed0d813f1cb33c4e826e3487e47f90b60569cc4d3b7fb4835e
```

모든 stream은 source order 1,100 rows이며 signed Q12
`sign(v)*floor(abs(v)*10^12+0.5)`와 compact sorted-key ASCII JSONL로 canonicalize했다.

| stream | rows | bytes | SHA-256 |
|---|---:|---:|---|
| RAW | 1,100 | 578,088 | `9eff30df05a770cee5930929faa9816a5235cb4e8a6b29c185379e38535b03c2` |
| SENSOR_FIXED | 1,100 | 797,101 | `9009a43c69da4537169e8145935c777259196f04e248c2de85f1d5bb632c8771` |
| MC_CORRECT | 1,100 | 794,910 | `39529955f2565be311b44f45e3d5012a5906bcde7efa3d8de5ce44c07189a189` |
| MC_WRONG | 1,100 | 792,721 | `3ed987fa2fa239b3bd0ec1c520392dd4edff250de19e34ae3e7804d2878bde32` |
| MC_DELAYED | 1,100 | 794,925 | `9389abf2ecaba4d922511f153703fe1e9547f4da912c2c9c6c599a45747c3df3` |
| AVAILABLE_FIVE_COMBINED | 1,100 | 2,893,109 | `55566cdc189c3519f56ac8d648a74c7b33bb003067e0b1c53c62b404a89cfe2a` |

`AVAILABLE_FIVE_COMBINED`는 richer five-arm oracle grammar의 hash다. Full six-arm artifact 또는
controls evaluator output hash가 아니다.

### 5.1 Status counts

| arm | in-FOV | outside | behind | invalid |
|---|---:|---:|---:|---:|
| RAW | 1,100 | 0 | 0 | 0 |
| SENSOR_FIXED | 1,094 | 6 | 0 | 0 |
| MC_CORRECT | 1,094 | 6 | 0 | 0 |
| MC_WRONG | 1,094 | 6 | 0 | 0 |
| MC_DELAYED | 1,089 | 11 | 0 | 0 |

MC_CORRECT/SENSOR_FIXED six OOF IDs는 다음과 같다.

```text
13856524 13856654 13856794 13857092 13857160 13857171
```

OOF는 transport loss가 아니며 clamp/drop하지 않는다. Arm-local OOF intersection으로 분모를
축소해도 안 된다.

### 5.2 Control discrimination

MC_CORRECT ray 기준 angular separation은 다음과 같다.

| arm | p50 deg | p95 deg | max deg | `>0.05°` events |
|---|---:|---:|---:|---:|
| RAW | 0.232158 | 0.472287 | 0.518082 | 967 |
| SENSOR_FIXED | 0 | 0 | 0 | 0 |
| MC_WRONG | 0.464315 | 0.944574 | 1.036164 | 1,039 |
| MC_DELAYED | 2.410391 | 2.512468 | 2.557571 | 1,100 |

따라서 wrong-direction과 exact delayed control은 이 1 ms window에서 informative하다. Exact delay를
±1 ns 바꾸면 1,100개 ray와 coordinate Q12가 모두 달라졌고, ZOH 및 occurrence-bracket 재사용
mutant도 검출됐다. 이는 기하 control의 판별력이며 bandwidth/benefit 판정은 아니다.

Production adapter와 MC_CORRECT의 사후 cross-check는 status mismatch `0/1100`, rounded pixel
mismatch `0/1100`, 최대 continuous component 차이 x `1.1368683772161603e-13 px`,
y `7.105427357601002e-14 px`였다. 독립 oracle을 production output에서 복사한 golden으로
바꾸지는 않았다.

## 6. A23 actual endpoint evidence는 negative blocker다

### 6.1 Frozen evidence identity

| object/member | SHA-256 |
|---|---|
| `public_projected_export.tar.gz` | `7eb025d9ba6de3dcd538311e75b11b55c51439ba9fc8fbf747213af1577053e0` |
| archive `MANIFEST.json` | `a7092f0f7c45f7bff895d55d2ec0c58b67f1f9d44d21efb029e3b85a5b23c987` |
| `public_projected_result.json` | `c6172d39d476c1db0733b1952613e9f17d2b0849e8b398b33ee66bb6e24d30da` |
| `public_projected_publication.json` | `3e12686de29459bbe8f2d292ca23892281e9760e9fbe6f65d979bc43a259c725` |
| input `projected_events.jsonl` | `b38d5946d2817905ef5471db7cf0df3d8cf92df4bb21678aed859e64a6e61d95` |
| input `receipt.json` | `257dadc09916ac8fa47056b77d1ab4fbecbde25e42855d7f3ddbb94ed7246807` |
| input `COMPLETE.json` | `f1bb58811a419dbb16bca522b9806176d466a268144502f0fb2342b3d762d230` |
| input `trace_1x.jsonl` | `c02aa20d8dc6cb2b85a500648e91f320d05f1f7e3b2d6e11d7189550b639ec94` |

Relevant A2/A3 1x members:

| owner/member | SHA-256 |
|---|---|
| A2 `events.csv` | `78c172b273f273fcd79fb5cae8a4b26c139855f063c7623f1e8f99c8a64d0794` |
| A2 `summary.csv` | `7a3365033f1ac826194be76c0fe84b5a8c2d64572e3280ee11ab648180981f3c` |
| A2 `simulation.log` | `a5123cf844c1cd0cc52ffc6d5c61b8021cd4228215322c45db383a491bc7af6e` |
| A3 `events.csv` | `5543207ef7e3ef972115a628657574fe6ecbb493f1f8b56c4c7c182402df4d2c` |
| A3 `summary.csv` | `90d6009f159fd590300c0b815cbb400845d0551f894e52961fea450e707db214` |
| A3 `simulation.log` | `e405c554a404c779df5175c8ac8816df802346dbdfe4d01d627a401b4a2a3b42` |

### 6.2 Exact 1x accounting

A2와 A3의 exact summary row는 동일한 accounting을 갖는다.

```text
a2,public_projected_1x,1100,81,1019,1019,1018,153693,153701,317,0,0
a3,public_projected_1x,1100,81,1019,1019,1018,153693,153701,317,0,0

columns:
owner,trace,generated,source_overrun,accepted,retired,fixed_window_retired,
fixed_window_cycles,observation_cycles,count2_commits,reset_test,pre_reset_clean_drain
```

각 `events.csv`는 1,100 data rows지만 partition은 다음과 같다.

```text
retired          1,019
source_overrun      81
```

81개 overrun row는 `accept_cycle=-1`, `retire_cycle=-1`이며 actual retire timestamp가 없다.
첫 overrun은 `tb_event_id=4`, occurrence cycle 155이고 마지막은 `tb_event_id=1062`,
occurrence cycle 148001이다.

```text
ordered 81 overrun tb_event_id + LF SHA-256
  7f34301de8d30371002037531176d137ea57f6d5c1a21f7194a3e6038edd8fe5

ordered 81 mapped missing dataset_event_index + LF SHA-256
  39a4cbfb41c909973c0e6de171b07071a751841f52b48776164be50044e320ab
missing ID count/first/last
  81 / 13856254 / 13857312
```

`accepted==retired==1019`는 accepted subset의 clean drain을 말할 뿐 generated cohort 1,100의
exact-once retirement를 말하지 않는다. Fixed observation window에서는 1,018만 retired되었다.

### 6.3 Required rejection and no-fake-receipt rule

Official production acceptance invariant는 다음에서 완화할 수 없다.

```text
source cohort = accepted = retired = 1,100
source_overrun = missing = duplicate = reordered = 0
all 1,100 IDs have externally observed retire timestamps
all timestamps are bound to the UZH source epoch in ns
```

A23를 full retire receipt로 제출하거나 1,019 rows만 남기면 count/order/ID gate에서 reject해야 한다.
1,100 rows를 유지해도 81개의 `-1`은 actual timestamp가 아니며 reject다. 다음 변환은 모두
fake receipt이므로 금지한다.

- missing retire를 occurrence timestamp로 복사
- `occurrence + constant/average latency`
- 전/후 이웃 retire time의 forward/back fill
- A2/A3 retained subset의 union 또는 더 좋은 owner 선택
- compressed cycle에 period/window offset을 적용해 source-epoch ns로 재명명
- summary accounting을 1,100/1,100으로 고쳐 쓰기
- A23 또는 임의 SHA label만 적고 raw artifact semantics를 재검증하지 않기

Generator는 partial/HOLD artifact를 발행하지 말고 result directory 생성 전 실패하거나 private
staging을 완전히 cleanup해야 한다. `RETIRE_WARP` row, canonical hash 및 official six-arm output
hash는 실제 1,100-event receipt가 생기기 전까지 **존재하지 않는다**.

## 7. Q&A relevance

교수 Q&A의 발언 원문을 이 문서가 직접 인용하는 것은 아니다. 아래는 repository의
Q&A traceability가 정리한 HIGH-confidence 축에 대한 팀 evidence mapping이다.

| Q&A 축 | 이 evidence가 답하는 범위 | 남은 경계 |
|---|---|---|
| supplied motion부터 시작 | Pinned UZH pose와 occurrence timestamp로 correct/wrong/delayed five-arm geometry를 독립 계산했다. | Pose estimator와 feedback loop는 후속 단계다. |
| world↔sensor pan/tilt/rotation convention | xyzw `T_WC`, current-to-reference direction, shortest-arc SLERP, radtan 및 continuous bounds를 machine-bound했다. | Translation/depth/plane과 general 6-DoF reconstruction은 범위 밖이다. |
| 혁신성·successful solution은 대조군으로 입증 | RAW/SENSOR_FIXED/MC_WRONG/MC_DELAYED를 같은 1,100 IDs로 보존했고 wrong/delayed가 수치상 informative함을 보였다. | RETIRE_WARP가 없어 exact six-arm evaluator 및 real-data benefit 판정은 HOLD다. |
| loss의 분모와 failure를 팀이 명시 | A23는 generated 1,100, overrun 81, accepted/retired 1,019를 모두 공개한다. Retired subset만으로 100% conservation을 주장하지 않는다. | Zero-overrun 1,100-event endpoint receipt가 필요하다. |
| encoder/decoder/serializer/buffer를 포함한 full-system | Generator contract는 occurrence부터 per-ID retire authority까지 요구하며 missing retire에서 fail-closed한다. | Actual endpoint producer, codec/decoder/link, causal clocks 및 complete boundary 측정은 미완료다. |
| 구현 가능성과 usable PPA | Strict standard-library companion generator의 software 구현 범위는 scoped PASS다. | Supplied-pose RTL, legal buffers, timing, power와 45 nm complete-endpoint PPA는 별도 HOLD다. |
| 문제 타당성이 projection 감소보다 우선 | Five-arm geometry와 actual event accounting을 먼저 고정하고, packet-key/locality나 압축 수치를 benefit으로 승격하지 않는다. | Equal-bit/equal-loss codec 및 wire accounting이 필요하다. |
| 발표·공개 비교의 재현성 | Source, cohort, oracle 및 negative A23 hashes/counts를 이 문서에 공개했다. | Pending final generator/test/run hashes가 채워져야 implementation archival evidence가 완결된다. |

따라서 이 단계가 Q&A 전체 질문에 주는 답은 다음과 같다.

> Supplied-pose rotation geometry는 pinned real source의 다섯 available arm에서 구현 가능하고
> control-discriminative하다는 scoped evidence가 있다. 그러나 full-system 성공을 판단하는 actual
> retire boundary는 A23 1x에서 81 events가 source overrun되어 불완전하다. 이 결손을 숨기지 않고
> RETIRE_WARP, official six-arm artifact, bandwidth/benefit 및 PPA를 HOLD하는 것이 현재의 정확한 답이다.

10/100 Gbps는 Q&A의 link scenario 예시이지 이 문서의 target이나 PASS threshold가 아니다. 이
문서는 packet framing/coding/CRC/idle을 계산하지 않았으므로 event/s 또는 wire benefit으로 환산하지
않는다.

## 8. Promotion checklist

### Implementation evidence hash closure

- [ ] Integrated generator commit과 source/schema hashes 고정
- [ ] Native/independent test blobs와 runner hash 고정
- [ ] Clean detached run의 command, pass/skip count 및 log/receipt hash 고정
- [ ] Synthetic fixture package 세 파일의 size/SHA 고정

위 네 항목은 `PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED`의 archival completion에만 필요하다.

### Official RETIRE_WARP / six-arm promotion

- [ ] Generator와 독립된 producer의 approved `OBSERVED_ENDPOINT_RUN` receipt
- [ ] Exact 1,100 source IDs/order/occurrence timestamps
- [ ] 모든 ID의 `accepted_count=retired_count=1`
- [ ] `source_overrun=missing=duplicate=reordered=0`
- [ ] Per-ID observed retire time과 retire-clock→UZH source-epoch ns mapping evidence
- [ ] Producer implementation/config/run/raw-log identities와 reviewed exact receipt SHA
- [ ] No interpolation, cycle relabeling, subset union 또는 companion-generated timestamps
- [ ] Full six-arm artifact/receipt/COMPLETE hashes와 all-input recomputing inspector PASS

이 checklist가 닫혀도 `HOLD_MC_WTB_REAL_DATA_BENEFIT`는 자동 해제되지 않는다. Geometry gate,
tile locality, equal-bit/equal-loss codec, wire accounting, latency/loss, causal RTL 및 PPA는 각각
별도 evidence gate다.

## 9. 허용·금지 claim 요약

허용:

- Generator software implementation은 scoped PASS이며 final archival hashes는 pending이다.
- Pinned official UZH source input에서 five available arms 1,100건을 독립 수치 검증했다.
- 이 window에서 wrong-direction 및 delayed controls는 informative하다.
- A23 A2/A3 1x actual replay는 각각 1,019 accepted events를 1,019 retire했지만 generated
  1,100 중 81 source overrun이 있다.

금지:

- Available-five combined hash를 official six-arm artifact hash로 부르기
- A23를 full 1,100 retire receipt 또는 official `RETIRE_WARP` authority로 사용하기
- Missing 81 retire times를 추정해 no-loss/full-cohort output 만들기
- `1,094/1,100` in-FOV disposition을 accuracy, success 또는 compression ratio로 부르기
- Geometry/locality를 packet, bandwidth, codec, benefit, RTL 또는 PPA evidence로 승격하기
- Pending implementation hashes를 임의 worktree 값으로 채우기

최종 현재 상태는 다음과 같다.

```text
IMPLEMENTATION  PASS_SCOPED_PENDING_FINAL_HASHES
FIVE_ARM        PASS_NUMERICAL_ORACLE_ON_PINNED_OFFICIAL_SOURCE_INPUT
RETIRE_WARP     HOLD_NO_VALID_FULL_COHORT_ACTUAL_RECEIPT
SIX_ARM OUTPUT  HOLD_NOT_GENERATED
BENEFIT/PPA     HOLD_NOT_EVALUATED
```
