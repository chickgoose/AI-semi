# Cluster2 polarity-v1 디지털 1차 결과물

제출 기준일: 2026-08-28  
최종 top: `aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity`

이 디렉터리는 디지털 1차 제출 요구사항인 RTL, synthesis, timing 최적화,
area, power, 동작 frequency를 한 묶음으로 보존한다.

## 제출 수치

| 항목 | 제출값 | 근거 경계 |
| --- | ---: | --- |
| 기능 검증 | generated/delivered 8,503/8,503 | Xcelium 23.09-s013 RTL simulation |
| 오류 | overrun/phantom/duplicate 0/0/0 | drain 후 empty |
| 합성 면적 | 1156.644, 544 cells | Genus 3.5 ns mapped report; report 원단위 |
| P&R 면적 | 1254.114, 596 instances | Innovus 3.5 ns report; report 원단위 |
| setup/hold slack | +0.454/+0.167 ns | Innovus 3.5 ns |
| 검증 동작 주파수 | 285.714 MHz | 3.5 ns clean point |
| 첫 faster fail | 333.333 MHz | 3.0 ns setup slack -0.004 ns |
| post-route power | 0.10738887 mW | vectorless/default activity 0.2 |
| DRC/antenna | 0/0 | internal tool reports, signoff 아님 |

`285.714 MHz`는 검증된 동작점이다. exact Fmax는 측정하지 않았으며,
관측 sweep bracket은 `[285.714, 333.333) MHz`다. 전력은 VCD/SAIF 기반
workload power가 아니라 sequential/primary-input default activity 0.2를 사용한
vectorless 추정값이다.

## 구성

- `source/rtl/`: 최종 RTL과 arbiter dependency
- `source/tb/`: polarity native observational TB와 runner
- `source/testdata/`: 고정 polarity 입력 trace
- `evidence/functional/`: source authority, release receipt, ledger, 원시 evidence archive
- `evidence/functional/full50_steal_buf/`: 기본 Cluster2 대비 steal_buf의
  공식 50-workload 손실 비교 TB·원시 결과·범위 설명
- `evidence/ppa/upstream_9b0d951/`: 4.5/4.0/3.5/3.0 ns Genus/Innovus sweep 원본
- `presentation/`: 발표 대본, 근거 문서, 그림 및 생성된 PPTX
- `tools/`: PPTX 생성 및 패키지 검증 도구
- `PROVENANCE.json`: source commit과 고정 hash
- `SHA256SUMS`: 패키지 전체 파일 checksum

Ganghee upstream provenance는 세 역할로 분리한다. `44f8918...`은 최종
RTL/native run, `58c132f...`은 ledger 재현 도구, `f2f93a8...`은 manifest
receipt다. Exact manifest는
`evidence/functional/uzh_shapes_rotation_patch.polarity_manifest.json`에
그대로 포함하며 SHA-256은 `df7ecc74...a02fa`다. 이 과정에서 polarity JSONL
bytes는 바뀌지 않았고 SHA-256은 `518a2a5b...e9cd3`이다.

## 무결성 재검증

패키지 루트 또는 저장소 루트에서 아래 verifier만 실행한다. 이 명령은 파일을
수정하지 않는다.

```bash
python3 submission/cluster2_digital_first_round_20260828/tools/verify_submission.py
```

이 PASS는 checksum, manifest, 고정 hash, raw PPA 수치, archive/PPTX 구조를
검사한다. Xcelium, Genus 또는 Innovus를 다시 실행하거나 signoff를 증명하지 않는다.

PPTX를 의도적으로 수정한 뒤 재생성·재봉인할 때만 다음 명령을 사용한다.

```bash
python3 -m pip install -r submission/cluster2_digital_first_round_20260828/tools/requirements.txt
python3 submission/cluster2_digital_first_round_20260828/tools/build_presentation.py
python3 submission/cluster2_digital_first_round_20260828/tools/verify_submission.py --write-manifest
```

아래 release gate는 독립 제출 패키지 내부 명령이 아니라 원 통합 저장소에서
실행하는 명령이다.

```bash
tests/redred_cluster2_cav_polarity_release/run_all.sh
```

독립 패키지에서 TB를 다시 돌리는 명령은 `source/tb/README.md`에 있다.
`evidence/functional/polarity_v1_source.f`도 원 통합 저장소 경로를 고정한 권위
파일이며, standalone 합성에는 `source/rtl/polarity_v1_synth.f`를 사용한다.

Genus/Innovus Tcl은 upstream 실행을 그대로 보존했기 때문에 원래 `rtl/`,
`syn/pnr/` 레이아웃과 `/home/aiasic26911/...` PDK/library 경로를 참조한다.
따라서 이 패키지는 raw PPA 결과를 독립 검증할 수 있지만, 기술 파일 없이
합성/P&R을 어느 환경에서나 재실행하는 portable tool bundle은 아니다.

## 발표 시 금지 표현

- `285.714 MHz`를 exact Fmax라고 부르지 않는다.
- `0.10738887 mW`를 workload/VCD/SAIF power라고 부르지 않는다.
- internal DRC/antenna 0을 foundry signoff, LVS, ERC, IR/EM 또는 SI/OCV
  signoff로 확대하지 않는다.
- CAV/world software 결과를 CAV/world RTL PPA로 합치지 않는다.
