# 설계 서버 환경 조사 체크리스트

이 문서는 인증 정보 없이 실행 가능한 조사 순서와 기록 양식이다. 비밀번호,
라이선스 문자열, PDK·표준셀 원본은 저장소에 기록하거나 복사하지 않는다.
서버의 기본 셸이 `csh`이므로 영구 설정을 변경하기 전에 공식 안내를 확인한다.

## 1. 확인된 기본 환경

`a1`의 2026-07-31 읽기 전용 조사 결과를 반영했다.

- hostname: `snu.polaris.09`
- OS: CentOS 7, Linux `3.10.0-1160.el7.x86_64`
- login shell: `/bin/csh`
- 환경 초기화: `~/control_digi.cshrc`
- home filesystem: 조사 시점 약 3.4 TB 여유

새 셸마다 다음을 실행한다.

```csh
setenv TERM xterm
source ~/control_digi.cshrc
rehash
```

작업 디렉터리는 역할별로 분리한다.

```text
~/AI-semi/a1
~/AI-semi/a2
~/AI-semi/integration
```

## 2. 추가 환경 및 권한 조사

서버에서 아래 읽기 전용 명령을 순서대로 실행하고 결과 중 민감 경로는 마스킹해
기록한다.

```csh
hostname
uname -a
date
echo $SHELL
pwd
id
df -h .
quota -s
printenv
```

- [ ] 할당 작업 디렉터리와 쓰기 가능 위치
- [ ] 용량/파일 수 제한과 임시 디렉터리 정책
- [ ] 배치 스케줄러 종류 및 사용 의무
- [ ] 로그인 노드에서 EDA 실행 가능 여부
- [ ] 결과 보존 기간과 백업 정책

`printenv` 결과는 라이선스 서버나 비밀값을 포함할 수 있으므로 원문을 커밋하지
않고, 필요한 변수의 **이름과 설정 여부만** 기록한다.

## 3. EDA 도구와 버전

공식 환경 설정 스크립트를 먼저 찾고 관리자 안내에 나온 방식으로 source한다.
도구 이름을 추측해 환경 파일을 변경하지 않는다. 후보 명령은 다음과 같다.

```csh
which vcs
which xrun
which questa
which iverilog
which verilator
which dc_shell
which genus
which yosys
which primetime
which tempus
which innovus
which voltus
which verdi
```

발견한 각 실행 파일에 공식 `-version`, `-V`, 또는 `-help` 옵션을 사용한다.

| 용도 | 도구/실행 파일 | 버전 | 환경 설정 방법 | 라이선스 확인 | 공식 예제 |
| --- | --- | --- | --- | --- | --- |
| Simulation | Xcelium `xrun` | `23.09-s013` | `control_digi.cshrc` | 실행 확인 필요 | TBD |
| Lint | TBD | TBD | TBD | TBD | TBD |
| Synthesis | Genus | `23.14-s090_1` | `control_digi.cshrc` | checkout 성공 | TBD |
| STA | Tempus | `23.14-s089_1` | `control_digi.cshrc` | 실행 확인 필요 | TBD |
| Power | Voltus | `23.14-s089_1` | `control_digi.cshrc` | 실행 확인 필요 | TBD |
| P&R | Innovus | `23.14-s088_1` | `control_digi.cshrc` | 실행 확인 필요 | TBD |

`dc_shell`, `vcs`, `pt_shell`은 설정된 PATH에서 발견되지 않았다. Genus는 batch
startup과 라이선스 checkout이 성공했다. 공식 예제와 공식 실행 옵션은 여전히 TBD다.

## 4. PDK·표준셀·corner

관리자가 제공한 문서/예제에서 논리 라이브러리(`.lib`/`.db`), 물리 라이브러리,
RC tech file의 위치를 확인한다. 전체 PDK를 재귀 검색하거나 로컬로 복사하지 않는다.

| 항목 | 확인 값 |
| --- | --- |
| 공정/노드 및 PDK release | GPDK045 (세부 release TBD) |
| 표준셀 library/revision | `gsclib045_all_v4.7` |
| 합성 target/link library | config의 `AER_LIBRARY_FILE` |
| STA library set | TBD |
| 비교용 PVT corner | provisional slow, 0.9 V / 125°C |
| 공식 평가 corner | TBD |
| RC corner | TBD |
| power용 library/activity 형식 | TBD |
| 권장 wire-load 또는 physical flow | TBD |

- [ ] 평가에 사용할 공식 corner와 추가 검증 corner 구분
- [ ] worst setup / worst hold / power corner 확인
- [ ] 면적 단위, 전력 단위와 leakage 포함 여부 확인
- [ ] clock gating, multi-Vt, buffer/inverter 사용 제한 확인

서버 home에는 `gsclib045_all_v4.7.tgz`와 `giolib045_v3.3.tgz`가 확인됐다.
표준셀 archive는 원본을 보존한 채 한 번만 압축 해제한다.

```text
gsclib045_all_v4.7.tgz SHA-256: fb15a057bc783e6b0b2b223261bb51ca170c27a62d33cb44dd4c91808d498ad1
giolib045_v3.3.tgz     SHA-256: 4bebbc571333b396a340dd6f47a365bc012d293392268f523c21eb5dcbdafcdb
```

```csh
cd ~
tar -tzf gsclib045_all_v4.7.tgz | head
tar -xzf gsclib045_all_v4.7.tgz
```

초기 비교 library 후보는 아래 파일이다. 파일명의 `vdd1v0`과 달리 Liberty header의
operating condition은 0.9 V / 125°C이므로 header를 재확인한다.

```text
gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
```

서버의 `scripts/config.local.sh`에서만 `AER_STD_CELL_ROOT`와
`AER_LIBRARY_FILE`을 설정한다. PDK 원본과 절대 경로는 커밋하지 않는다.

## 5. 공식 예제와 실행법

- [ ] 공식 예제의 README와 디렉터리 구조
- [ ] RTL file list 형식과 SystemVerilog 지원 옵션
- [ ] 합성 Tcl/명령, 산출 netlist·SDC·SDF
- [ ] STA Tcl/명령, setup/hold 및 unconstrained path 검사
- [ ] Power Tcl/명령, VCD/SAIF 생성 구간과 toggle 전파 방식
- [ ] 라이선스/큐 오류 시 재실행 규칙

Xcelium 회귀와 Genus 논리 합성 driver는 저장소에 준비되어 있다. Tempus·Voltus의
공식 입력 및 실행법은 아직 확인되지 않았으므로 driver는 TBD다.

```bash
scripts/run_sim.sh baseline
scripts/run_sim.sh improved
scripts/run_ppa.sh
```

Genus driver는 mapped netlist/SDC와 area/timing/power/QoR report를 만들고 동일한
`metrics.tsv`로 정규화한다. power activity Tcl을 사용하지 않으면 power 값은
vectorless estimate이므로 workload 기반 결과와 구분해 기록한다.

## 6. SDC 확인

- [ ] 공식 clock port/name, 목표 주기와 waveform
- [ ] input/output delay 기준
- [ ] clock uncertainty/transition/latency
- [ ] driving cell과 output load
- [ ] async reset false path의 허용 여부
- [ ] false/multicycle path의 공식 정의
- [ ] 최대 fanout/transition/capacitance
- [ ] 모든 endpoint가 constrained인지 확인하는 명령

확정값은 서버 전용 config에서 `AER_CLOCK_*`, `AER_*_DELAY_NS`,
`AER_DRIVER_CELL`, `AER_LOAD_PF`로 설정한다. baseline과 improved에는 반드시 같은
`constraints/aer_common.sdc`와 값을 사용한다.

## 7. 제출 구조

| 항목 | 공식 요구사항 |
| --- | --- |
| 제출 위치/방식 | TBD |
| 마감 시각/시간대 | TBD |
| 디렉터리/압축 파일명 | TBD |
| 필수 RTL/top/module name | TBD |
| TB/vector 포함 여부 | TBD |
| 필수 reports (area/timing/power) | TBD |
| 실행 스크립트/README | TBD |
| 허용 파일 형식/용량 | TBD |
| 재현 명령과 clean rule | TBD |

제출 전 새 디렉터리에서 압축을 풀어 공식 명령으로 재현하고, 절대 경로·사용자명·
라이선스·PDK 파일이 포함되지 않았는지 검사한다.
