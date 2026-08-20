# MC-WTB UZH Stage-3 adapter evidence (2026-08-20)

## 1. 결론

Stage-3는 pinned UZH `shapes_rotation` 1 ms source window의 pose-join 결과를
orientation-only reference geometry로 변환하고, 모든 occurrence를 정확히 하나의
disposition으로 보존하는 software adapter baseline을 닫았다.

```text
status            PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED
promotion_status  HOLD_MC_WTB_REAL_DATA_BENEFIT
```

이 PASS는 source-bound offline geometry/disposition 변환에만 해당한다. MC-WTB의
real-data benefit, bottleneck 개선, codec/wire bandwidth, causal hardware, clock
alignment, RTL, timing, power 및 PPA는 아직 증명하지 않았다.

통합 전 기준은 `c6a89039987134a028bc12b1f22ecdf29fd78291`이다. Stage-3
adapter/control 코드와 두 test suite는
`bc92beb17a1d50315ebcd6c68af05627e61637e9`에 통합했고, source binding 네 개를
주지 않으면 full gate가 fail-closed하도록 만든 release-runner commit은
`6359c9689eb01f8e5c573c4499c1dd9032b1bb8e`이다.
외부 worktree와 inherited `PYTHONPATH` override까지 제거해 현재 checkout만 검사하도록
고정한 commit은 `66c78314e602943a2adff06ece4c9fd9d1cf4d20`이다.

## 2. 고정 입력과 provenance

Official source-bound pose-join package는 `/tmp/uzh-posejoin-c6a`를 사용했다.

```text
events.txt SHA-256             d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda
groundtruth.txt SHA-256        bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb
calib.txt SHA-256              ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd
pose-join receipt SHA-256      85c182e1daa2f380dffa34a559ae2093835b1052c3d9d9a7f5a1f014a9974f87
pose-join COMPLETE SHA-256     c7692b20dc7d1f305a723cff695b9b794421fdfd39d6a021a17876c56d155756
join spec raw SHA-256          04a81a809164556f744e55b075b94cbc7e2042ccb714e0e03fab8d4aa55a177e
```

Adapter `inspect(result, pose_join, spec)`는 source와 spec을 필수로 받고, upstream
package를 다시 검사한 뒤 전체 adapter artifact와 receipt semantics를 재계산한다.
Source/spec 없는 inspection은 scoped PASS를 반환할 수 없다.

## 3. Official 1 ms 결과

Selection은 `41.321 <= t < 41.322 s`이며 source order와 raw occurrence identity를
보존한다.

```text
input joined events                 1,100
output dispositions                 1,100
WORLD_REFERENCE_EVENT               1,094
RAW_ESCAPE_GEOMETRIC_OOF                6
RAW_BYPASS_INVALID_GEOMETRY             0
dropped / duplicate / reordered         0 / 0 / 0
behind-reference                         0
invalid-distortion                        0
```

여섯 geometric OOF dataset index는 다음과 같다.

```text
13856524  13856654  13856794  13857092  13857160  13857171
```

`1,094/1,100`은 accuracy, success rate, compression rate가 아니다. 이 값은 해당
source window에서 continuous image bounds 안으로 들어온 orientation-only geometry
disposition 수일 뿐이다.

Follow-up artifact `/tmp/uzh-adapter-final-a6-run`의 identity는 다음과 같다.

```text
events_mc_wtb_adapter.jsonl  1,812,702 bytes
SHA-256                      a8a78cab40e8679cd98b50d78cda5df5c93e55ec100227862c0ad1b611bf599a
receipt.json SHA-256         f34655799be9b29d82774cf3210f4f870eb396024cdf18f69bb4e48c6bda0197
COMPLETE.json SHA-256        7919657165b5a44696ee34e5d5f1bdab22a21ee2f09f0f97078ae99284ac7b25
```

## 4. 독립 numerical oracle

Production helper를 golden으로 재사용하지 않은 독립 quaternion/SLERP/radtan 계산과
1,100행을 전수 대조했다.

```text
canonical projection rows       1,100
canonical projection bytes      774,412
row mismatches                  0
byte equality                   true
corrected SHA-256               5b63662ad305d3a6ec5705c3b2958dc1078a437abda7301c38d06d276f3ca2aa
```

초기 oracle의 `dbe90d...43be` hash는 forward-radtan tangential term의 Python
부동소수점 결합 순서를 production source와 다르게 펼친 오류였다. Corrected oracle은
`c6a8903`의 실제 연산 tree를 따르며 production과 모든 row와 field가 같다. 잘못된
초기 hash는 acceptance 값으로 사용하지 않는다.

이 1,100-row full-coordinate 비교는 이번 개발 중 수행한 독립 one-off diagnostic이며,
현재 repository에 같은 canonical hash를 재생성하는 committed regression suite는 없다.
Committed independent official test가 지속적으로 보장하는 범위는 1,100-event 보존,
1,094/6/0 partition, exact six OOF ID/continuous coordinates, 그리고 synthetic
transform/SLERP/radtan oracle이다. 따라서 위 full-row hash를 상시 CI gate 또는
dataset 일반화 증거로 표현하지 않는다.

## 5. 실행한 gate

### Integrated adapter gate

다음 한 명령이 native 5 tests와 independent 9 tests를 서로 다른 module 이름으로
모두 실행한다.

```bash
REDRED_UZH_POSE_JOIN_PACKAGE=/tmp/uzh-posejoin-c6a \
REDRED_RUN_UZH_ADAPTER_OFFICIAL=1 \
REDRED_UZH_JOINED_ROOT=/tmp/uzh-posejoin-c6a \
REDRED_UZH_JOIN_SPEC=benchmarks/redred_uzh_shapes_pose_join/join_spec.json \
PYTHONDONTWRITEBYTECODE=1 \
bash tests/redred_uzh_mc_wtb_adapter/run_all.sh
```

결과: **14/14 PASS, 0 skip**.

필수 source binding이 하나라도 없거나
`REDRED_RUN_UZH_ADAPTER_OFFICIAL=1`이 아니면 `run_all.sh`는 exit 2로 종료한다.
따라서 partial/skip 실행을 full release PASS로 오인할 수 없다.

### Six-arm control evaluator

Exact arm set은 `RAW`, `SENSOR_FIXED`, `MC_CORRECT`, `MC_WRONG`, `MC_DELAYED`,
`RETIRE_WARP`다.

```bash
bash tests/redred_uzh_mc_wtb_controls/run_all.sh
```

결과: **10/10 PASS**. 이는 supplied normalized records의 equal-ID six-arm evaluator
contract에 대한 scoped PASS다. Official pose-join/adapter에서 이 six-arm record를
생성하는 source-bound generator는 아직 없으므로 official control evaluation은 HOLD다.

### Relevant regressions

```text
MC-WTB Stage-1 model/hardening                 28/28 PASS
UZH geometry with pinned external members      19/19 PASS
MC-WTB causality                               10/10 PASS
Known-motion coordinate contract               27/27 PASS
UZH source projection                           8/8 PASS
UZH source-preserving pose join                10/10 PASS
System-goal policy + contract                   36/36 PASS
```

Pose-join full-byte test는 23,126,288-event source member를 실제로 읽어 실행했다.
위 9개 suite는 총 162 tests이며 system-goal policy는 의도대로
`evidence_qualified=false`, `release_qualified=false`를 유지했다.

## 6. Machine-readable claim boundary

Adapter header와 receipt는 다음을 명시한다.

```text
offline_future_bracket_slerp       true
future_pose_lookahead_required     true
causal_hardware_claimed            false
clock_alignment_validated          false
orientation_only                   true
translation_preserved_not_applied  true
depth_or_plane_model_applied       false
codec_or_wire_benefit_claimed      false
rtl_timing_power_or_ppa_claimed    false
```

Reference와 event pose는 future-right bracket을 포함한 offline shortest-arc SLERP를
사용한다. 따라서 이 결과를 zero-lookahead causal hardware 증거로 바꾸어 말하면 안 된다.
또한 concurrent same-UID source-package swap 및 mutable network filesystem은 현재
inspection threat model 밖이다.

## 7. 현재 PASS와 다음 HOLD

현재 PASS:

- pinned source/spec에 결박된 pose join → rotation geometry 변환
- source occurrence exact-once 보존과 세 disposition의 배타적 partition
- frame direction, shortest-arc SLERP, radtan 및 exact six OOF의 독립 numerical 검증
- source/spec 없는 PASS 거부, output/source/spec tamper 회귀시험
- normalized six-arm evaluator의 equal-ID, no-filtering 및 claim boundary

현재 HOLD:

- official 1,100 occurrences에서 six control arms를 만드는 source-bound generator
- correct/wrong/delayed/retire controls의 official full-cohort 결과
- tile/time sweep 및 MC-WTB real-data benefit 판정
- packet/codec/wire bits, throughput, latency 및 loss
- causal pose delivery, CDC/clock alignment, RTL 및 45 nm PPA

다음 구현 단계는 adapter를 수정하는 것이 아니라 별도 companion generator로 official
six-arm records를 생성하고, 동일 occurrence cohort와 provenance를 controls evaluator에
결박하는 것이다. 그 결과가 나오기 전에는 혁신성 또는 병목 개선을 수치로 주장하지
않는다.
