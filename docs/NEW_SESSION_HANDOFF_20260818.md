# AI-semi 새 대화 세션 인수인계

> 2026-08-24 update: 현재 1차 중심 통합 코드·정량 결과·검증 명령은
> `docs/REDRED_CLUSTER2_CAV_1ST_ROUND_STATUS_20260824.txt`와 이 문서의
> 14절을 먼저 적용한다. 2026-08-20 이전 내용은 운영 진입점과 역사 복구
> 기록이며, 1차 Cluster2→CAV evidence 범위에서 충돌하면 최신 절의
> PASS/HOLD 경계를 우선한다. 전체 system goal·release authority는 계속
> `contracts/redred_system_goal/active_goal.json`이며 이 갱신이 덮어쓰지 않는다.

기준 시각: 2026-08-18 KST
목적: 과거 작업을 자동 재개하기 위한 문서가 아니라, 새 대화가 검증된 핵심 사실과 운영 환경을 잃지 않고 **새 목표부터 다시 시작**하게 하는 로컬 영구 메모리다.

현재 REDRED 목표와 판정의 authority는
`contracts/redred_system_goal/active_goal.json`이다. 아래 2026-08-18 P6
복구·측정 절은 역사 자료이며 현재 endpoint, 후보 선택, release interface 또는
team release authority가 없다. 현재 구현은 single-edge endpoint이며 이것만
release-eligible interface로 선택됐다. Interface release와 최종 A2/A3 선택은
모두 HOLD다.

## 0. 새 세션이 가장 먼저 지킬 것

1. 이 문서를 끝까지 읽고 `AGENTS.md`를 따른다.
2. 이전 P&R, sweep, GLS/activity 작업을 임의로 재개하지 않는다. 사용자의 새 목표가 우선이다.
3. 실질적인 작업이면 supervisor가 a2–a9 여덟 tmux Codex에 처음부터 서로 겹치지 않는 일을 배분한다.
4. `node` 프로세스나 입력된 명령만 보고 작업 중이라고 말하지 않는다. `tmux capture-pane`에서 각 pane의 `Working`, 완료, block 상태를 확인한다.
5. 편집은 별도 worktree와 비겹치는 파일 집합을 사용한다. 팀원 RTL/TB는 사용자가 수정하라고 하지 않는 한 read-only다.
6. 기존 dirty/untracked 결과를 삭제하거나 덮어쓰지 않는다.
7. 서버 비밀번호, license 문자열, PDK 본문은 Git이나 이 문서에 저장하지 않는다.

## 1. 저장소와 보존 상태

- 주 작업 경로: `/home/chickgoose/projects/a1`
- 이 문서를 작성할 때 checkout: `integration/a7-k4-physical-candidate`
- handoff 작성 직전 기준 HEAD: `61de7fdbd3b3160d3ce91dcb3ce0a1cc5fc4d078`
- 사용자 소유로 간주하여 보존할 untracked 경로:
  - `.w2-build-artifacts/`
  - `docs/주최측_QA_문의사항.txt`
  - `results/a7-parallel-event-compactor/`
  - `results/common-multilane/`

핵심 브랜치:

| 역할 | 브랜치 | 기준 commit |
| --- | --- | --- |
| K2 디지털 최종본 | `integration/k2-digital-final` | `13c60f936fe5a265e650b4b91436ed79fc20dc91` |
| K2 물리 flow/후속 activity | `integration/k2-physical-final` | `d73b611c87340b2a480735166e2abfa0af07b2e1` |
| 현재 설명/결과 문서 | `integration/a7-k4-physical-candidate` | `61de7fdbd3b3160d3ce91dcb3ce0a1cc5fc4d078` |
| core sweep 독립 구현 | `codex/core-sweep-profiles` | `7f33baff32b36894c7d035f3d63fe822dc218713` |

디지털 복구용 bundle 기록:

- Windows 경로: `C:\Users\박준영\AI-semi\AI-semi-k2-digital-final-20260813.bundle`
- SHA-256: `5a7e71f0c09af9debfc20315bbbe52b7cc94934da49ffc9ff44f3c146e1ff4ae`
- 상세: `docs/K2_최종코드_복구방법_20260813.txt`

복구 주의:

- shared Git directory는 `/home/chickgoose/projects/AI-semi/.git`이다.
- 감사 시 등록 worktree는 42개였고 그중 다수가 `/tmp` 삭제로 prunable 상태였다. `/home/chickgoose/projects/a1`~`a9` 영구 worktree는 존재했다.
- `refs/stash`는 `c7c306d2836a638a496c3389db30451a5f972f85`였다.
- 현재 문서 브랜치의 local history는 origin보다 크게 앞서 있었다. origin만으로 복구 가능하다고 가정하지 않는다.
- 위 Windows digital bundle과 아래 6.5 ns evidence archive는 감사 시 이 Linux 로컬 파일시스템에는 없었다.
- untracked result는 Git bundle에 들어가지 않는다. 별도 archive 전에 `git gc`, `git prune`, `git worktree prune`을 실행하지 않는다.

새 세션은 작업 전에 아래를 먼저 기록한다.

```bash
cd /home/chickgoose/projects/a1
git status --short --branch
git worktree list
git branch --show-current
git rev-parse HEAD
```

## 2. 보존할 역사적 P6 설계 자료 — 현재 선택 아님

### Fovea

- scalar K1, 한 cycle에 최대 1 event.
- 중심 가중 `[1,5,5,1]` 선택 의미가 가장 명확하다.
- 좁고 작지만 1-event/cycle 병목과 source overrun이 크다.

### Cluster2

- 중심/주변 두 native lane과 row bitmap으로 최대 8 occurrence/cycle을 표현한다.
- 처리량과 손실 면에서 Fovea보다 유리하지만 Fovea의 scalar weight/round/prefer-center 의미를 그대로 보존하지 않는다.
- Fovea와의 **native core-only** 비교 대상이다.

### Fovea+A7 / R1

- Fovea selector는 유지하고 scalar 4-bit address를 2-bit DDR로 보낸다.
- K1 complete endpoint, physical link 3 wires.
- Fovea의 처리량/overrun 자체는 개선하지 않고 link 폭만 줄인다.

### A2 Batched-IWRR K2 + P6

- 성능 우선 K2 설계.
- `[1,5,5,1]` 장기 aggregate service 비율은 보존하지만 exact scalar-prefix는 아니다.
- P6 6-wire link, complete endpoint에서 charged 11-bit elastic buffer를 포함한다.
- 디지털 처리량은 가장 높지만 A3보다 물리 비용이 크다.

### A3 Exact Scalar-Prefix K2 + P6

- 의미 보존 우선 K2 설계.
- 현재 pending snapshot에 Fovea scalar 선택을 두 microstep 적용한다.
- exact scalar-prefix K2이며 A2보다 작고 얕지만 처리량은 낮다.

### A4

- aggregate-only K2 연구 후보다.
- 최종 디지털 Pareto에서 A2보다 성능/비용이 불리하여 발표 주후보에서 제외했다.
- 별도 연구 결과로만 보존한다.

핵심 해석:

- A2: `Fovea aggregate 의미 + Cluster2 이상급 처리량`의 성능형 hybrid.
- A3: `Fovea exact-prefix 의미 + Cluster2급 처리량`의 의미보존형 hybrid.
- “A2/A3가 모든 면에서 Fovea/Cluster2보다 우월하다”는 주장은 틀리다. A2/A3는 더 넓고 복잡한 K2 complete endpoint다.

## 3. 공용 workload/TB 계약

공용 TB와 workload는 candidate-neutral clean-slate 기준이다. 팀원 native RTL/TB와 섞어 수정하지 않는다.

주요 경로:

- `tb/clean/aer_clean_tb.sv`
- `tb/clean/aer_bench_if.sv`
- `tb/clean/aer_clean_assertions.sv`
- `benchmarks/clean_slate_aer/`
- `docs/TEAM_COMMON_WORKLOAD_GUIDE.md`
- `docs/팀원_공용_AER_워크로드_TB_안내.txt`

알려진 frozen SHA:

- `aer_clean_tb.sv`: `27d9437a...`
- `aer_bench_if.sv`: `fbca24e7...`
- assertions: `ab3bca49...`
- full50 official manifest: `9fe40060...`
- capacity22 official manifest: `99a8bbd3...`

새 증거를 만들 때는 줄임표가 아닌 전체 SHA를 실제 파일/manifest에서 다시 읽어 기록한다.

필수 의미:

```text
generated = source_overrun + accepted
accepted = delivered          # hard-correct run
```

- source마다 pending latch는 정확히 1개다.
- pending 중 같은 source가 재발화하면 무제한 TB queue에 넣지 않고 `source_overrun`이다.
- `source_overrun`은 capacity/performance 결과이지 hard correctness 오류가 아니다.
- phantom, duplicate, corrupt, reorder, accepted-missing, illegal/X output, drain timeout은 hard failure다.
- TB-only binding은 FIFO, retry, arbitration, serializer, storage, 새로운 기능을 추가하면 안 된다.
- 없는 기능은 `SKIP_UNSUPPORTED`로 표시한다.
- `capacity22`는 full50의 exact 22-trace subset이다. 50+22를 72개의 독립 표본으로 합산하지 않는다.
- native unit tests, common workload evidence, physical PPA evidence는 서로 별도 증거다.

## 4. 역사적 P6 디지털 결과 — superseded/noncurrent

상세 원문: `docs/K2_디지털개발_최종현황_20260813.txt`
주요 역사 receipt는 `integration/k2-digital-final` 브랜치의
`tests/a23_full_p6_replay/result.json`, `audits/a7_k2_cost_closure/result.json`,
`audits/k2_final_selection/result.json`이다. 이 자료는 재현·복구 대상으로
보존하지만 현재 single-edge 목표의 후보/interface/release 선택 authority가
없다. 현재 checkout에 없으면 파일이 사라진 것이 아니라 브랜치가 다른 것이다.

```bash
git show integration/k2-digital-final:tests/a23_full_p6_replay/result.json
git show integration/k2-digital-final:audits/k2_final_selection/result.json
```

| 후보 | full50 accepted=retired | overrun | fixed-window EPC | capacity22 accepted | capacity22 EPC |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fovea | 78,229 | 28,187 | 0.673901421 | 42,163 | 0.757866503 |
| Cluster2 | 94,157 | 12,259 | 0.811620447 | 57,802 | 1.040124568 |
| A2+P6 | 104,046 | 2,370 | 0.896281733 | 63,246 | 1.137384793 |
| A3+P6 | 93,645 | 12,771 | 0.806670806 | 57,280 | 1.030061924 |

A2/A3 actual-P6 replay는 full50 150회, reset/drain 3회, actual-RTL mutant 15개 kill, 두 캠페인 byte-identical까지 확보했다. A2 accept→retire는 고정 3 cycle, A3는 고정 2 cycle이다.

중요한 증거 경계:

- Fovea/Cluster2 회수 Xcelium 결과와 A2/A3 actual-P6 replay는 같은 frozen 수요를 사용했지만 하나의 동일 official attempt는 아니다.
- 따라서 위 표는 설계 판단에 유용하지만 단일 canonical receipt의 완전한 head-to-head로 과장하지 않는다.
- 당시 P6 digital-only 정책은 A2를 골랐지만 그 선택은 superseded/noncurrent다.
  현재 정책은 A2 primary/A3 semantic fallback 역할만 정하며 최종 A2/A3
  선택은 HOLD다.

## 5. 역사적 P6 물리 비교 경계 — 현재 endpoint 증거 아님

모든 다섯 설계를 한 PPA 표에서 순위화하지 않는다.

1. **native core-only cohort**: Fovea core vs Cluster2 core.
2. **complete-endpoint cohort**: Fovea+A7/R1 vs A2+P6 vs A3+P6.

이 역사적 P6 cohort의 정규화 외부 역할은 ref/sample clocks, reset,
16-source pending/accept, retire lanes/addresses, drain/error였다. 이는 현재
single-edge `clk_i`/synchronous active-high `rst_i`/9-wire link 경계가 아니다.
Scheduler debug 출력은 top I/O에서 숨기되 내부 실제 logic/state 비용은
포함한다. Fovea는 K1/3-wire, A2/A3는 K2/6-wire이므로 raw PPA만으로 동등
서비스 승자를 선언하지 않는다.

## 6. 실서버 물리 결과

상세 원문: `docs/k2_endpoint_physical_results_20260814.txt`

### Complete endpoint, clean 6.5 ns point

- nominal 153.846 MHz.
- source commit: `b5888526ae8edfab04b768ca5c7b00a920bcad19`
- final verifier commit: `bc61c470d75dee6adb236ca6761f32e77a250cb0`
- server result root: `/tmp/k2-pnr-b588852-6p5-final2`
- evidence archive SHA-256: `5112c2a447725532f628d5eb4dba9df0f7bd36e52040261a0582128fe3a63645`

| Candidate | Inst | Area raw | Setup WNS ns | Hold WNS ns | Routed um | Vias | I/O bits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fovea+A7/R1 | 374 | 1264.374 | +0.00955343 | +0.00382453 | 6273 | 2293 | 50 |
| A2+P6 | 992 | 2766.780 | +0.00004911 | +0.00553125 | 12070 | 6449 | 53 |
| A3+P6 | 742 | 2055.078 | +0.0142229 | +0.00657797 | 9493 | 4711 | 53 |

세 후보 모두 setup/hold/recovery/removal/gating/pulse/half-cycle timing clean, TNS 0, DRC 0, antenna 0, regular/PG connectivity 0, placement violation 0이다.

Area report에는 단위가 명시되지 않는다. 해당 Innovus/library 관례상 um²로 해석하지만 발표에는 `area raw` 또는 이 caveat를 붙인다.

한 common trace의 diagnostic power:

| Candidate | Total mW | Direct VCD coverage |
| --- | ---: | ---: |
| Fovea+A7/R1 | 0.06442312 | 12.5313% |
| A2+P6 | 0.17939317 | 6.35386% |
| A3+P6 | 0.13864122 | 8.27943% |

이 전력값은 low/unequal direct coverage와 propagated/default activity를 사용한 **provisional diagnostic**이다. signoff power, full50 평균, 정밀 power ranking이 아니다.

### Complete endpoint timing 관측

- 6.5 ns: 세 후보 모두 clean PASS.
- 대화 중 수행한 5.7 ns fresh P&R에서는 세 후보 모두 setup recovery가 닫히지 않아 FAIL로 관측됐다.
  - Fovea setup WNS `-0.0845961 ns`
  - A2 setup WNS `-0.100518 ns`
  - A3 setup WNS `-0.075951 ns`
- 그러나 이 5.7 ns 결과의 immutable local archive/receipt는 현재 checkout에서 확인되지 않았다. 따라서 새 세션은 6.5 ns를 clean qualified operating point로만 유지하고, 5.7 ns bytes를 회수·검증하기 전에는 정식 Fmax bracket으로 발표하지 않는다.

### Core-only same-flow reference

5.0 ns clean reference:

| Core | Area raw | Setup WNS ns | Hold WNS ns | Power |
| --- | ---: | ---: | ---: | --- |
| Fovea | 310.536 | +0.943599 | +0.00124423 | 0.01796521 mW, vectorless |
| Cluster2 | 191.520 | +3.01774 | +0.00452548 | 0.01269190 mW, vectorless |

같은 flow에서 4.0, 3.5, 2.2 ns도 둘 다 PASS했다. 1.8 ns는 사용자가 중단시켜 증거가 아니다. 그러므로 둘 다 454.5 MHz 이상의 passing point만 있고 first-fail bracket은 없다.

사용자가 별도로 전달한 강희 결과(예: Fovea 714–769 MHz, Cluster2 1053–1111 MHz)는 다른 workload/flow 가능성이 있는 외부 자료다. same-flow 표에 섞지 않는다.

## 7. 미완료/재개 금지 항목

아래는 “다음에 반드시 해야 할 일”이 아니다. 새 목표가 요구할 때만 재개한다.

- Post-route SDF GLS activity producer는 `integration/k2-physical-final`의 `ef23641`, `ebe0544`, `80b9d03`, `d73b611`에 구현돼 있다. 현재 checkout에 경로가 없으면 `git show integration/k2-physical-final:physical/k2_postroute_activity/run_postroute_activity.py`로 확인한다.
- endpoint SDF GLS의 common-TB 기능/path-delay annotation은 진전이 있었지만 strict SAIF unknown-state/duration/log gate까지 완전한 authoritative power receipt는 발행하지 못했다.
- full50/capacity22 전체 activity/power는 없다.
- exact Fmax, silicon signoff, CDC/RDC, mid-flight reset abort/flush는 미검증이다.
- 서버 `/tmp`와 로컬 `/tmp` 결과는 재부팅/정리로 사라질 수 있다. 존재를 가정하지 말고 먼저 확인한다.
- 과거 실패 P&R, stale runner, false-pass fixture, 21-pane 감독 체계를 그대로 되살리지 않는다.

## 8. 서버 접근 메모리

실제로 접근한 endpoint:

- account: `aiasic26911`
- IP: `210.126.11.79`
- remote shell: `/bin/csh`
- home: `/home/aiasic26911`
- environment: `~/control_digi.cshrc`

`snu.polaris.09`는 서버 내부 hostname으로 기록돼 있지만 로컬 DNS에서 해석되지 않았던 적이 있다. 새 세션은 IP를 기본으로 사용한다.

새 연결:

```bash
ssh aiasic26911@210.126.11.79
```

비밀번호는 사용자가 허용한 대화/입력에서만 사용하고 저장하지 않는다. 기존 ControlMaster socket은 재사용 가능할 때만 쓴다.

```bash
test -S /tmp/k2-pnr-ssh3.sock
ssh -F /dev/null -S /tmp/k2-pnr-ssh3.sock -o BatchMode=yes \
  aiasic26911@210.126.11.79 'hostname; pwd'
```

socket이 없거나 죽었으면 새 인증이 필요하다. 권한 창을 피하려고 보안을 우회하지 않는다.

원격 초기화(csh):

```csh
setenv TERM xterm
source ~/control_digi.cshrc
rehash
```

확인된 도구:

- Xcelium `23.09-s013`
- Genus `23.14-s090_1`
- Innovus `23.14-s088_1`
- Tempus/Voltus `23.14-s089_1`
- GPDK045, slow Liberty header는 0.9 V / 125 C (`PVT_0P9V_125C`)

주의:

- login shell이 csh라 bash용 loop/redirection을 직접 붙이면 깨질 수 있다. 복잡한 작업은 검토된 script를 전송하거나 명시적으로 bash를 사용한다.
- 서버 공용 repo의 과거 위치는 `~/AI-semi/integration`이다. live bytes와 commit/hash를 먼저 확인하고 “최신”이라고 가정하지 않는다.
- PDK/license 설정은 Git에 복사하지 않는다.

## 9. 표준 tmux 구조

현재 표준은 **supervisor 1개 + a2–a9 8개 worker**다. 21-pane 파일은 과거 레이아웃 기록일 뿐 기본값이 아니다.

생성:

```bash
cd /home/chickgoose/projects/a1
scripts/bootstrap_codex_team_tmux.sh
```

기본 session 이름은 `ai-semi`; 변경하려면 첫 인자로 준다.

```bash
scripts/bootstrap_codex_team_tmux.sh my-session
```

worker Codex까지 대기 상태로 실행하려면 다음을 사용한다.

```bash
scripts/bootstrap_codex_team_tmux.sh ai-semi --launch-workers
```

구성:

- window 0 `supervisor`: head Codex/통합/최종 판단.
- window 1 `agents`: a2–a9, tiled 8 panes.
- 기본 script는 안전하게 shell pane만 만들며 각 pane의 cwd를 `/home/chickgoose/projects/a2`~`a9`로 분리한다. `--launch-workers`를 명시하면 각 독립 worktree에서 interactive Codex를 시작하지만, 과거 task는 자동 재개하지 않는다.

점검:

```bash
tmux list-windows -t ai-semi
tmux list-panes -t ai-semi:agents -F '#{pane_index}|#{pane_title}|#{pane_current_command}'
tmux capture-pane -p -t ai-semi:agents.0 -S -80
```

attach:

```bash
tmux attach-session -t ai-semi
```

두 터미널에서 창을 독립적으로 보고 싶으면 grouped session을 먼저 만든다. 원 session에 `:window`를 붙여 직접 attach해 다른 client의 active window를 바꾸지 않는다.

```bash
tmux new-session -d -t ai-semi -s ai-semi-supervisor
tmux select-window -t ai-semi-supervisor:supervisor
tmux new-session -d -t ai-semi -s ai-semi-agents
tmux select-window -t ai-semi-agents:agents
```

작업 배분 원칙:

- a2–a9 모두에게 같은 “검증”을 복제하지 않는다.
- 목표 자체를 독립 산출물로 쪼갠다: architecture, RTL, common TB binding, receipt, physical boundary, server execution, evidence audit, adversarial integration처럼 겹치지 않게 한다.
- 구현자는 자기 worktree만 편집하고 다른 pane은 read-only review를 한다.
- supervisor만 통합/merge/최종 GO-HOLD를 결정한다.
- 유용한 병렬 일이 8개보다 적으면 억지 일을 만들지 않는다. 다만 독립 분해 가능한 실질 작업에서는 a2–a9를 기본으로 사용한다.

## 10. 과거에 반복된 실패를 피하는 규칙

- “서버에서 돌아감”과 “qualified PASS”를 구분한다.
- Genus area/power는 post-route PPA/Fmax가 아니다.
- fixed-netlist period 변경은 최종 Fmax 비교가 아니다. 최종 비교는 period별 fresh synthesis+P&R이 필요하다.
- output/TB wrapper가 storage/arbitration을 추가하면 candidate 비용/의미가 바뀐다.
- 동일 외부 역할과 load가 아닌 top들의 raw PPA를 순위화하지 않는다.
- ready/valid는 synchronous edge에서 판정한다. off-edge combinational ready를 accept로 세지 않는다.
- NBA 이후 값을 같은 edge의 synchronous consumer가 본 것처럼 세지 않는다.
- phase 종료 시 pending request를 강제로 0으로 지워 source withdrawal을 만들지 않는다.
- `capacity22`를 22개 추가 독립 표본처럼 합산하지 않는다.
- simulator/tool가 정상 종료해도 timing/DRC/connectivity/activity coverage가 실패하면 HOLD다.
- low/unequal direct VCD coverage의 propagated/default power를 signoff ranking으로 부르지 않는다.
- tmux pane에 명령이 보이거나 `node`가 떠 있는 것만으로 일하고 있다고 말하지 않는다.
- audit가 implementation을 계속 재설계해 서버 실행을 무기한 늦추지 않도록, 시작 전에 최소 성공 정의와 stop rule을 고정한다.

## 11. 새 세션 시작 프롬프트

복사용 파일은 `docs/NEW_SESSION_START_PROMPT.txt`다. 새 대화에 그 파일의 내용을 그대로 붙여 넣거나 아래를 사용한다.

```text
/home/chickgoose/projects/a1/AGENTS.md와
/home/chickgoose/projects/a1/docs/NEW_SESSION_HANDOFF_20260818.md를 먼저 끝까지 읽어.

이전 P&R/sweep/activity 작업을 자동으로 재개하지 말고, handoff의 검증된 사실과
GO/HOLD 경계만 로컬 메모리로 유지해. 기존 dirty/untracked 파일과 팀원 RTL/TB는
건드리지 마. 비밀번호나 license 정보도 저장하지 마.

tmux 기본 운영은 supervisor 1개 + a2~a9 여덟 worker다. substantial task이면
처음부터 목표 자체를 서로 겹치지 않는 8개 작업으로 분해하고, 편집은 별도
worktree로 격리해. 각 pane은 capture-pane으로 실제 Working 여부를 확인하고
supervisor만 통합과 최종 판단을 해. 21-pane 레이아웃은 과거 기록이지 기본값이 아니다.

먼저 현재 Git branch/HEAD/status/worktree, tmux session, SSH ControlMaster socket과
서버 접근 가능 여부를 읽기 전용으로 확인해. 그 다음 handoff에서 기억한 핵심을
짧게 요약하되, 과거 작업을 시작하지 말고 내가 이번 새 세션에서 제시하는 목표를
기준으로 계획을 세워.
```

그 뒤 같은 메시지 아래에 새 목표를 적는다.

예:

```text
이번 새 목표: [여기에 새 목표를 한 문장으로 작성]
```

## 12. 참고 문서 우선순위

1. `contracts/redred_system_goal/active_goal.json` — 현재 목표와 GO/HOLD authority.
2. `docs/AI_SEMI_QNA_REDRED_GOAL_20260819.md` — 현재 목표 해설과 증거 경계.
3. `docs/NEW_SESSION_HANDOFF_20260818.md` — 운영 진입점과 역사 복구 기록.
4. `docs/NEW_SESSION_START_PROMPT.txt` — 새 대화에 붙여 넣을 시작문.
5. `AGENTS.md` — 1+8 운영과 안전 규칙.
6. `docs/K2_디지털개발_최종현황_20260813.txt` — 역사적 P6 디지털 상세.
7. `docs/k2_endpoint_physical_results_20260814.txt` — 역사적 P6/R1 physical 결과.
8. `docs/K2_최종코드_복구방법_20260813.txt` — 디지털 bundle 복구.
9. `docs/팀원_공용_AER_워크로드_TB_안내.txt` — common TB 의미.
10. `docs/server-audit-a1.md` — 서버/도구 기록.
11. `docs/tmux-workflow.md` — tmux 운용 상세.

`docs/K2_물리검증_실서버_결과_20260813.txt`와 `docs/tmux_all_agents_layout_20260814.txt`는 중간/과거 기록이다. 현재 최종 판정보다 우선하지 않는다.

## 13. 2026-08-19 주최 측 Q&A 이후 새 목표

주최 측 Q&A의 사실·권장·팀 자율·미확정 사항과 REDRED release gate는
`docs/AI_SEMI_QNA_REDRED_GOAL_20260819.md`에 정리했다. 이 절은 앞의
2026-08-18 복구 기록을 수정하지 않는 후속 결정이다.

핵심 결정:

- A2는 성능 주후보, A3는 held-pending exact-prefix 의미 fallback이다.
- 현재 구현 경계는 하나의 `clk_i` posedge, synchronous active-high `rst_i`,
  synchronous `link_enable_i`, 16-source pending/accept, 9-wire single-edge link,
  ordered two-lane retire를 포함하는 complete endpoint다. Reset은 clean
  drain 뒤에만 assert하는 qualification 범위다.
- 팀 정의 canonical traffic을 유지하며 주최 측 dataset은 versioned extension이다.
- timing과 같은 boundary의 mapped vectorless power를 필수 증거로 둔다.
- P6 digital/6.5 ns 자료는 superseded/noncurrent 역사 reference이고 현재 선택
  authority가 없다. Single-edge parallel 구현만 release-eligible interface다.
- Single-edge actual-RTL synthetic와 public projected extension은 각 bounded
  semantics 범위에서 PASS다. Source/elaborated single-posedge CDC/RDC는 외부
  입력이 primary clock에 synchronous라는 범위에서 PASS다. RTL source-structure
  PDK 검사는 source-only PASS다.
- Producer-native synthetic/public adapter와 aggregate pipeline은 team-canonical
  campaign 범위의 `A2_PRIMARY` 추천을 닫았다. Generic campaign-v3는 schema
  incompatible UNBOUND 상태지만 successor native pipeline의 별도 prerequisite가
  아니다. 이후 single-edge A2/A3 실제 Genus/Innovus post-route diagnostic,
  mapped/post-route structural CDC 검사와 동일 environment snapshot cohort는
  확보됐다. 다만 producer-authenticated freshness, organizer-approved
  mapped/PDK legality와 constraints, source→mapped semantic binding, 공식 최종
  A2/A3 선택 또는 team release는 닫지 않는다.
- known-motion 좌표 변환은 endpoint PPA 밖의 post-retire system demo로 먼저 검증한다.

새 세션의 한 문장 목표는 `docs/NEW_SESSION_START_PROMPT.txt`에 기록했다.

통합 상태:

- Hardened single-edge actual RTL의 팀 synthetic와 public projected 실행은
  bounded PASS이며 native aggregate는 campaign-scoped A2 추천을 봉인했다.
  P6 receipt의 `digital_RTL=GO`는 역사적 P6-only 범위이며
  현재 후보/interface/final selection authority가 아니다.
- Single-edge source CDC/RDC와 RTL source-structure PDK evidence는 각각
  synchronous-input/source-only 범위에서 PASS다.
- policy verifier는 native publication/result/seal과 정책 구조를 검증하지만
  physical/final release GO authority는 아니다.
- Single-edge interface는 선택됐고 real-server P&R/post-route timing,
  vectorless power, structural mapped/post-route CDC는 diagnostic PASS다.
  Mapped/organizer legality authority, authenticated freshness, semantic
  equivalence, final selected-interface CDC/RDC, 공식 A2/A3 선택과 team
  release는 HOLD다.

## 14. 2026-08-24 1차 중심 Cluster2→CAV 통합 체크포인트

사용자의 최신 발표 방향 결정은 다음과 같다.

- 1차 발표 중심 결과는 강희의 `cluster2_steal_buf` AER이다.
- 기존 causal-CAV baseline은 2차 world-coordinate 기능으로 이어지는
  보조 functional-extension evidence다.
- predictor, online feedback, depth/translation/parallax와 CAV/world RTL·PPA는
  이번 1차 범위에서 HOLD다.
- “확장성 보장” 또는 “wire-level 호환성 입증”이 아니라
  “event-identity 수준 software functional-extension feasibility 확인”으로
  발표한다.

공개 검토 authority:

- repository: `https://github.com/chickgoose/AI-semi.git`
- branch: `integration/cluster2-steal-buf-cav-bridge`
- final first-round evidence-package checkpoint:
  `f5109974236d297b5b60b0f1c18aecc4c1d184e6`
- team status: `docs/REDRED_CLUSTER2_CAV_1ST_ROUND_STATUS_20260824.txt`
- presentation briefing:
  `docs/presentation/cluster2_cav_first_round_briefing.md`
- claim/evidence matrix:
  `docs/presentation/cluster2_cav_evidence_matrix_20260824.md`
- Ganghee native PPA diagnostic handoff:
  `docs/presentation/ganghee_cluster2_ppa_diagnostic_handoff_20260824.md`
- official result:
  `benchmarks/redred_cluster2_cav_bridge/results/official_uzh_cluster2_cav_result.json`
- official runner:
  `benchmarks/redred_cluster2_cav_bridge/official_functional_run.py`

검증된 실제 경계:

- 입력은 hash-pinned UZH DAVIS240C `shapes_rotation` events, ground truth,
  calibration이다.
- pinned 4×4 cyclemask와 converter bytes는 각각 존재하지만, 이번 official
  runner는 source-to-cyclemask converter 실행을 재현하지 않는다.
- native transport는 pinned `cluster2_steal_buf` RTL bytes를 observational TB와
  Cadence Xcelium으로 시뮬레이션한 sealed trace다. 칩 측정이나 post-route
  simulation이 아니다.
- TB-side event ID는 AER wire payload가 아니다. 8,503건 join은 observational
  software exact join이다.
- CAV와 512×256 world grid는 software다. CAV/world RTL이 아니다.

공식 pinned 결과:

- selected events 8,503; poses 11,883; native exact join 8,503; overrun 0
- causal-CAV WORLD rays 8,420; fresh-ZOH 0; SENSOR_FIXED bypass 83
- retire latency histogram: 1 cycle 6,393; 2 cycles 2,077; 3 cycles 33
- software grid: 8,420 quantized events, 821 unique cells
- result seal:
  `caf75dc9add39273ba410521a8aaff6dfec4ec5eb7a290d55581b81d58374309`

네 시간축을 혼용하지 않는다.

1. `event_timestamp_ns`: 원본 UZH sensor time
2. `native_occurrence_cycle`: converter 규칙의 1 ms workload bin
3. `cav_occurrence_cycle`: 6.5 ns software logical cycle
4. `retire_cycle`: Xcelium native retirement observation

Geometry는 원본 event timestamp와 software CAV cycle만 사용한다. Native
retire cycle/latency는 관측 sidecar일 뿐이며, latency-quality 결과가 아니다.

최종 fresh-clone 검증:

- 공개 branch history가 위 `160e7dc...`를 ancestor로 포함
- 당시 clean fresh clone에서 bridge suite 144개 중 141 PASS, 3
  environment-gated SKIP. 이후 presentation-assets 회귀 5개와 replay-receipt
  회귀 4개가 추가되어 현재 suite는 153개 중 149 PASS, 1 known non-blocking
  FAIL, 3 environment-gated SKIP. 따라서 suite 자체 판정은 FAILED이며,
  알려진 단일 실패는 별도의 release 판단에서 비차단 이슈로 관리함
- tracked sealed bundle의 CRLF cyclemask를 문서 절차로 명시적으로 LF로
  canonicalize하고 hash를 확인한 뒤, 공식 8,503-event golden replay PASS
- 위 exact replay는
  `benchmarks/redred_cluster2_cav_bridge/results/official_uzh_cluster2_cav_replay_receipt.json`
  과 sanitized log에 봉인됐다. 판정은
  `PASS_LOCAL_EXACT_GOLDEN_REPLAY_NOT_SIGNED_OR_HARDWARE_ATTESTATION`이며
  Python 3.8 runtime, hardware/CAV RTL/PPA/performance는 HOLD다.
- 최종 통합 worktree와 public remote ref 일치

다음 작업 전에 먼저 위 team status 문서를 끝까지 읽는다. 강희 native AER의
별도 PPA/P&R 자료를 사용할 때는 원본 report authority, RTL hash,
corner/constraints, activity 조건과 signoff 한계를 함께 제시하며 software CAV
PPA로 합치지 않는다.

## 15. 2026-08-25 종료 전 저장 체크포인트

- `integration/cluster2-steal-buf-cav-bridge`의 evidence-package checkpoint
  `f5109974236d297b5b60b0f1c18aecc4c1d184e6`는 public origin에 push됐고,
  public fresh clone에서 HEAD와 remote ref가 일치했다.
- fresh clone bridge suite는 153개 중 149 PASS, 1 known non-blocking FAIL,
  3 environment-gated SKIP였다. Suite 자체 판정은 FAILED이며, 알려진 단일
  실패는 별도의 release 판단에서 비차단 이슈로 관리됐다.
  공식 UZH source와 accepted LF cyclemask를 켠 8,503-event exact golden replay
  1개도 별도로 PASS했다.
- presentation SVG 3개는 committed generator로 재생성한 뒤 byte 변경이 없었고,
  최종 red-team 재감사는 GO였다.
- 다음 세션은 predictor/feedback/depth 개발을 자동 재개하지 않는다. 1차 발표
  중심은 pinned `cluster2_steal_buf`이고 software CAV/world는 보조 feasibility다.
  우선 team status, briefing, evidence matrix를 읽고 사용자가 지정하는 다음
  발표 작업을 수행한다.
- pinned 원본 `cluster2_steal_buf`에는 Genus mapped screening만 release 후보로
  검토할 수 있고, 별도 polarity-extended top의 Innovus 관측치를 원본에 귀속하지
  않는다. Native PPA release authority와 CAV/world RTL·PPA는 계속 HOLD다.
- 종료 당시 tmux `ai-semi:3` agents window는 a2~a9 여덟 pane의 4-column ×
  2-row layout이었다. 전원 종료 후에는 session 자체가 사라지므로
  `scripts/bootstrap_codex_team_tmux.sh ai-semi --launch-workers`로 재생성하고
  `tmux capture-pane`으로 실제 worker 상태를 확인한다.
