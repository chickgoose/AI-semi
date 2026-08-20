# MC-WTB phase 4 완료 보고

기준일은 2026-08-20 KST이며 구현 기준은 `dev/mcwtb-phase4`의
`afa65ebc814178772717bceca7e7aeb4e0eff18b`이다. 이 보고서는 사용자가
승인한 1~4단계까지만 다룬다. phase 5 혁신 구조 구현은 시작하지 않았다.

## 결론

1~3단계 occurrence 경계와 실제 retire 증거는 고정 UZH cohort에서 PASS했다.
4단계의 실제 motion 검증 실행도 끝났지만, 현재 MC-WTB baseline의 motion
개선 가설은 입증되지 않았다. 사전등록 최종 상태는 실제 retire control이
비정보적이어서 **HOLD**이고, 별도 primary gate는 53.6% 악화 방향이라
`FAIL_NO_PREREGISTERED_BENEFIT`다. 따라서 “구현과 검증은 4단계까지 완료”지만
“motion benefit이 입증됨”은 아니다.

| 단계 | 판정 | 검증된 내용 |
| --- | --- | --- |
| 1. 손실 원인 계수 | PASS | 기존 single-pending 모델의 81개 손실이 동일 cycle·동일 source 중복 발생과 정확히 대응 |
| 2. bounded occurrence ingress | PASS | 6 lanes, source당 depth 3, all-or-none admission, sticky overflow; 같은 cycle 최대 6개 및 같은 source 최대 3개 수용 |
| 3. 실제 endpoint retire | PASS scoped | Xcelium 6.5 ns에서 1,100 generated = 1,100 accepted = 1,100 retired, missing/duplicate/overrun/protocol error 0 |
| 4. 실제 motion 개선 | HOLD, primary FAIL | six-arm 생성과 geometry oracle는 PASS했으나 retire control은 비정보적이고 `MC_CORRECT`도 proxy를 개선하지 못함 |

## 1~3단계 구현·서버 결과

RTL은 `rtl/candidates/mc_wtb_occurrence_baseline/`에 있다. occurrence payload는
dataset event ID, join sequence, 원 발생 timestamp, sensor x/y, polarity 및
causal pose source index를 보존한다. 입력은 6 lanes이고 16개 source마다 깊이
3의 FIFO를 둔다. A2 스케줄러는 cycle당 최대 두 source를 선택한다.

최종 stimulus는 발생시각보다 앞서 주입하지 않도록
`ceil((timestamp-start)/6.5 ns)`를 사용했다. 1,100 events는 642 admission
cycles에 배치됐고, cycle당 최대 6 events, 동일 source 최대 3 events였다.

확정 커밋의 로컬 바이트와 서버 업로드 바이트를 SHA-256으로 비교한 뒤 새
서버 경로에서 실행했다. A2 RTL, occurrence RTL, main/corner TB, Genus TCL,
source records, stimulus 및 manifest 8개 파일은 모두 local/server SHA-256이
일치했다. 전체 값은 machine-readable 요약에 고정했다. 이는 같은 SSH 실행
절차에서 직접 비교한 provenance이며, 별도 reviewer 또는 서버가 cryptographic
signature를 발급한 attestation은 아니다.

대용량 source/stimulus/raw/six-arm 본문은 Git에 넣지 않았으므로 이 문서와
branch만으로 구성된 self-contained portable evidence bundle은 아니다. 대신
재생성 코드, 모든 핵심 SHA-256, 서버 run root와 claim 경계를 보존했다.

- 서버: `aiasic26911@210.126.11.79`
- 최종 run root: `/home/aiasic26911/mcwtb_phase4_final_afa65eb`
- Xcelium: 23.09-s013
- main marker: `MC_WTB_OCCURRENCE_BASELINE_RTL_PASS`
- directed corner marker: `MC_WTB_OCCURRENCE_BASELINE_CORNER_PASS`
- status: `PASS ingress=1100 accepted=1100 retired=1100 last_cycle=153696 overflow=0 protocol_error=0`
- raw log SHA-256: `2956022a227106eb9b0956b5b18a184d5061fc35d13a6e962705ccdd3fe50ec9`
- independent inspection: `PASS_MC_WTB_OCCURRENCE_BASELINE_OBSERVED_RETIRE_SCOPED`
- retire receipt SHA-256: `51c2462180f364ebf5cd937e38f28eaf47138c0f1b03826820454a1091dd6ec9`

독립 스케줄 모델은 ingress cycle/lane, A2 accept cycle/lane/order,
retire=accept+1을 전부 대조한다. 1,100 identity는 모두 보존됐으며 A2 중재에
의한 global reorder는 402개였다. 이는 누락이나 source 내부 순서 위반이 아니다.
발생시각부터 mapped retire까지는 최소 13 ns, p50 17 ns, p95 24 ns, 최대
34 ns였다.

directed corner 시험은 FIFO depth-three fill, full-bank와 같은 edge의 pop
credit, old-before-new ordering, clean drain, no-pop overflow의 sticky visibility를
검증했다.

Genus 23.14-s090_1에서 GPDK045 slow 1.0 V library를 읽고 HDL elaboration 및
unresolved-reference check를 통과했다. marker는
`MC_WTB_OCCURRENCE_BASELINE_GENUS_ELABORATION_PASS`다. 이는 서버·library
호환성 smoke일 뿐 합성 QoR, mapped timing, area, power 또는 Innovus P&R
증거가 아니다. 최초 감사에서 TCL이 평탄화 server staging 경로를 전제한다는
재현성 결함을 발견해, script 위치에서 repository root를 계산하고 canonical
`rtl/candidates/...` 경로를 읽도록 수정했다. 새 서버 root
`/home/aiasic26911/mcwtb_phase4_final_afa65eb`에서 다시 PASS했으며 TCL과
로그 SHA-256은 각각 `88b87790eb012bd2bae28ee4ba6d50efd3c6908b2ed0b7b0116a01fb0c111eb5`,
`170b4eaf24fa0665ef51e17005d12db667c89208acd20c8f385812e944150f94`다.

최종 red-team에서 source/raw를 함께 바꾸는 coherent mutation이 기존 inspector를
통과하는 문제와 `0.5 ns exclusive` 표기 오류도 발견했다. production
source-record, stimulus, manifest의 고정 SHA-256을 검사하고, 실제 Git commit
객체가 존재하며 RTL·TB·prepare·inspector 핵심 파일이 그 commit과 byte-identical한지
검사하도록 강화했다. 양자화 오차는 정확히 `0.5 ns inclusive`로 수정했다. 이
수정 뒤 서버 본시험부터 receipt와 motion 평가까지 새로 생성했다.

## 4단계 실제 motion 검증

공식 UZH `shapes_rotation` 1 ms window의 1,100개 event와 바로 앞 0.25 ms의
251개 anchor event를 사용했다. 실제 endpoint retire receipt를 포함해 다음
6개 arm을 동일 event denominator로 생성했다.

- `RAW`
- `SENSOR_FIXED`
- `MC_CORRECT`
- `MC_WRONG`
- `MC_DELAYED`
- `RETIRE_WARP`

production source-bound generator와 별도 표준-library geometry oracle 12개
시험은 모두 PASS했다. six-arm artifact SHA-256은
`15671cdf943f78c9b00e8b3fb261b03fa544e1a6326234e7fb2e352cff5c442c`다.
generator spec은 이 실행에서 사용자가 승인한 정확한 byte hash로 고정했지만,
별도 외부 reviewer의 cryptographic signature를 받았다는 뜻은 아니다.
inspector mutation, PARET unit, generator native, official full-cohort 및
독립 geometry를 합친 최종 software regression은 42/42 PASS였다. 이 시험
PASS는 계산·provenance 검사의 무결성을 뜻하며 motion 성능 PASS를 뜻하지 않는다.

PARET metric은 같은 polarity의 pre-window raw anchor까지 정규화된 최근접
거리를 사용하며 낮을수록 좋다. full six-arm 생성 전에 고정한 preregistration
SHA-256은 `2d564d92460b86e7aaaadfe4c4118d3d42310520e31c9af4c1f581cd16f1f548`다.

| Arm | score |
| --- | ---: |
| RAW | 0.008140094624427259 |
| SENSOR_FIXED | 0.008140094624427259 |
| MC_CORRECT | 0.012505075395217825 |
| MC_WRONG | 0.014424897575444856 |
| MC_DELAYED | 0.03088583306687766 |
| RETIRE_WARP | 0.012505054773166185 |

주효과 `1 - MC_CORRECT/SENSOR_FIXED`는 `-0.5362321904332517`이었다. 개선이
아니라 53.6% 악화 방향이므로 primary gate는
`FAIL_NO_PREREGISTERED_BENEFIT`다. 또한 실제 endpoint latency 13~34 ns는
이 motion 속도에서 각도 차이가 너무 작아 `RETIRE_WARP` timing control도
informative하지 않았다. frozen preregistration은 이 경우 PASS나 FAIL이 아닌
HOLD를 요구하므로 전체 최종 상태는 `HOLD_RETIRE_CONTROL_UNINFORMATIVE`다.
즉 formal verdict는 HOLD이지만, 선택한 proxy에서 개선 방향의 신호는 없었다.

이 결과가 뜻하는 것은 현재 orientation-only, fixed reference, nearest-anchor
baseline이 선택한 window에서 event cloud를 안정화하지 못했다는 것이다.
world-coordinate 변환식이 틀렸다는 뜻은 아니다. `MC_WRONG` 및
`MC_DELAYED`의 geometry separation과 공식 arm hash는 독립 oracle로
확인됐다. metric을 사후에 바꾸거나 다른 window를 골라 PASS로 승격하지
않는다.

## 정확한 claim 경계와 5단계 검토 지점

현재 주장 가능한 것은 fixed cohort의 occurrence 보존, 실제 1,100/1,100
retire, six-arm geometry 생성, 그리고 사전등록 motion 검정의 HOLD 및 negative
primary 진단 결과다.
codec·압축·wire 감소·PPA·P&R·dataset generalization·novelty는 주장하지
않는다.

5단계에서는 구현 전에 다음을 함께 검토해야 한다.

1. world-space 안정화의 목표를 “직전 raw anchor 근접도”로 둘지, scene
   edge/track/voxel consistency로 둘지 문제 정의를 다시 검토한다.
2. orientation-only와 fixed reference가 충분한지, translation/depth 또는
   짧은 epoch별 reference 갱신이 필요한지 구분한다.
3. 현재 결과를 유지한 채 새 metric·window는 별도 preregistration과 독립
   holdout으로 평가한다.
4. motion 기능이 GO가 된 뒤에만 codec·tile aggregation·PPA 구조를 선택한다.

Machine-readable 요약은 `docs/MC_WTB_PHASE4_COMPLETION_20260820.json`에 있다.
