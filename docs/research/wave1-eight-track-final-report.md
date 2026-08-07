# AER clean-slate 8-track wave-1 final report

Date: 2026-08-07  
Frozen benchmark base: `ad96895`  
Head branch: `integration/clean-slate-evaluation`

## 1. 결론

Wave 1에서는 A2--A9가 서로 다른 핵심 원리를 독립 branch/worktree에서
연구하고, candidate 전용 RTLㆍbindingㆍrunner만 추가했다. 공용 46-trace
manifest, golden fixture, common TB와 common runner는 모든 branch에서
`ad96895` 대비 변경하지 않았다.

현재 N=16 공통 조건에서 physical screening에 올릴 수 있는 후보는
**A7 original parallel-prefix, K=4** 하나다. 이것도 최종 채택안이 아니라,
동일 Kㆍ동일 register state의 replicated-selector reference와 공통
Xcelium/Genus/Innovus flow로 비교할 자격만 얻었다.

- A2는 최종 독립검수 수정 후 N=16/N=64 모두 reject다.
- A3의 homeostatic inhibition과 refractory-WTA salvage는 모두 reject다.
- A4는 N=16 `HOLD_FLAT`, N=64만 별도 scaling physical hypothesis다.
- A5 speculative pregrant는 oracle utility ceiling과 비용 gate에서 reject다.
- A6 exact codec v1/v2/v3는 end-to-end 효율 gate에서 reject다.
- A7 K=2와 radix-4 K2 rescue는 reject, original K=4만 screening eligible이다.
- A8 calendar wheel B1/B2/B4/B8은 현재 RTL 기준 reject다.
- A9는 현재 always-ready shortlist에서 reject이며 optional physical package도
  exact registered-boundary 검증 전까지 `NOT_ELIGIBLE`로 강제 차단됐다.

서로 다른 source count나 lane count는 직접 순위화하지 않는다. 따라서 A4
N=64 scaling hypothesis와 A9 N=64 diagnostic은 A7 N=16/K4의 경쟁 후보가
아니며 별도 실험이다.

## 2. 후보별 최종 판정

| Track | 핵심 방법 | 최종 evidence commit | 검증된 장점 | 가장 강한 반증 | Wave-1 판정 |
| --- | --- | --- | --- | --- | --- |
| A2 | adaptive sparse bypass + B-way burst reservoir | `b749b6e` | sparse direct path와 burst admission; phase-2에서 finite overrun 감소 | 동일-boundary phase-3에서 N16 always-buffered 대비 같은 throughput에 20.45% LUT4+FF premium; N64는 39.5% cell 증가 대비 1.6--2.9% throughput 증가. EPCCㆍdepthㆍrecovery 세 gate 모두 실패 | N16/N64 reject |
| A3 | leaky membrane/homeostatic inhibition | `80d8d43` | bounded arithmetic, N=4 exhaustive conservation, starvation-safe parameter region | RR과 fairness/settling 이득이 거의 같지만 policy toggle이 약 1.97--12.28x | reject |
| A3 salvage | global refractory winner-take-all | `6bad03a` | 6-bit policy state, transport correctness, RR 대비 toggle 감소 | N=16 persistent contention에서 14 sources zero-service, censored max-wait 512 cycles | hard reject |
| A4 | radix-4 spatial elastic quadtree | `5f07aee`; handoff `4aea1f9` | N64 local structural gate에서 tree depth/fanout/wire-span proxy 개선; N16/46 RTL correctness | N16은 extra state/pipeline과 wire/fanout gate를 회수하지 못함; N64 RTL common qualification과 routed PPA 미완료 | N16 hold flat; N64 conditional scaling hypothesis |
| A5 | confidence-based safe speculative pregrant | `66c76c3` | prediction이 correctness를 소유하지 않음; N=3 262,144-case exhaustive safety | 현실 predictor가 oracle latency utility의 11.24%만 포착; best depth 3.3% 개선에 NAND 62.4% 증가, 10% Fmax/5% area gate 실패 | reject predictor; keep fallback only |
| A6 | exact lossless AER codec | v2 `db3c6e1`; v3 `3d65dae` | exact round-trip, v2 block별 non-expanding raw escape | v2 72.2% aggregate overrun, 80--130-cycle latency, endpoint state/cell cost; B/W 12-point v3 optimistic matrix도 simultaneous Pareto 0 | reject all codec generations |
| A7 | shared parallel-prefix K-lane compactor | base `2219040`; rescue `f3520b4` | K2가 offered 2.0에서 1.999 event/cycle; original K4가 replicated K4보다 5,592 vs 6,729 gates, depth 139 vs 248 | K2 original 4,299 vs replicated 3,733 gates; radix-4 K2는 3,307 gates로 줄지만 depth 149 vs 133. Frozen load ceiling 2.0이라 K4 throughput은 K2보다 높게 입증되지 않음 | original N16/K4 only: eligible for physical screening |
| A8 | O(1) age calendar wheel | `4b92f59` | B8 sequential-state toggle은 N16/32/64에서 11.64/14.04/15.55 flips/cycle로 완만히 증가 | B8 max-wait와 local depth가 exact-age보다 나쁘고, B4 N64 toggle은 exact를 초과; unrolled select cone이 state 이득을 상쇄 | reject current RTL |
| A9 | distributed empty-slot/token fabric + H2 handoff | local gate `e571e67`; final block `99644e8` | local logic depth N16/N64에서 10으로 일정; H2는 asymmetric-stall opportunity를 안전하게 handoff | same-L central보다 N16 cells/state/latency 열세; H2 always-ready migration/gain 0; common evidence와 exact registered physical boundary 결속 미완료 | current shortlist reject; optional package NOT_ELIGIBLE |

## 3. 독립검수로 수정된 사항

### A2 phase-3 activity/result correction

A3 독립검수 `3133a29`가 VCD alias 중복, clock/reset fanout, same-source
duplicate arrival 해석과 unsafe cached-Yosys 경로를 발견했다. A2는 이를
`b749b6e`에서 수정하고 full-clean rerun을 수행했다.

- toggle과 fanout은 reject basis에서 제거했다.
- N16 recurrence의 72 duplicate overruns를 storage 능력 증거에서 분리했다.
- cached JSON은 RTL/script/options hash가 없으므로 `SKIP_YOSYS`를 거부한다.
- corrected reject basis는 `pressure_epcc`, `lut_depth`, `recovery_region`뿐이다.
- targeted와 full-clean aggregate는 byte-identical하며 N16/N64 reject는
  그대로다.

### A7 rescue reproduction

A8 독립검수 `3ca3397`이 committed structural CSV를 독립 Yosys 재실행으로
동일 SHA-256까지 재현했다. Original prefix는 base evidence와 분리됐고,
segmented/replicated는 동일 port와 register-bit boundary를 사용한다.

- N16/K2 reject와 K4 area/depth trade-off는 수치상 타당하다.
- N32/N64 random backpressure는 single-seed 2,048-cycle regression이므로
  sign-off 증거로 사용하지 않는다.

### A9 handoff fail-closed

A4 review `30b63e0`, `7f27539`는 common PASS가 direct replacement core만
검사하고 exact registered physical shell은 별도 elaboration한다는 점과,
evidence 결속이 충분하지 않음을 확인했다. A9는 `99644e8`에서 두 profile을
명시적으로 `NOT_ELIGIBLE`로 바꿨다.

- Xcelium/Genus/Innovus preflight는 모두 exit 3이다.
- 해제 조건은 canonical trace를 exact registered physical boundary로 replay,
  evidence binding 강화, 독립 재검수다.
- package와 실패 history는 삭제하지 않는다.

## 4. 혁신성ㆍ선행기술 판정

Wave 1의 구조를 "처음 발명한 알고리즘"으로 발표하면 안 된다. A3의 primary-
source audit `6660b48`에 따라 방어 가능한 기여 범위는 다음과 같다.

- A2: bypass/FWFT, banked buffering, occupancy threshold와 hysteresis는 기존
  기술이다. 새 주장은 strict-order direct retire + B-way tail admission +
  level/delta/dwell control을 이 AER contract에서 조합하고 반증한 점으로
  제한한다.
- A4: AER arbiter tree, radix-4 RR tree와 elastic register는 선행기술이다.
  새 주장은 full-identity synchronous elastic merge와 topology/permutation
  falsification을 함께 수행한 integration/scaling hypothesis로 제한한다.
- A7: 2013년 m-select parallel-prefix RR 논문이 broad algorithm을 직접
  선행한다. 새 주장은 cyclic source-rank와 available-lane rank를 독립
  ready lane에 결합한 AER 구현, exact contract 검증, N16/K4 equal-state
  crossover 측정으로 제한한다.

가장 강한 공통 기여는 새로운 이름보다 **동일 AER correctness contract에서
서로 다른 mechanism-matched reference를 만들고, 사전 break-even을 통과하지
못한 아이디어를 실제로 기각한 평가 방법**이다.

## 5. 비교 경계

- Common seam은 physical serial link가 아니다. A6 이외 후보는 native
  source array를 노출하므로 link-only event/pin-cycle로 직접 순위화할 수 없다.
- Whole-native functional pins는 현재 static extraction 기준 single-lane
  A2/A3/A4/A5 310, ready 없는 A8 309, four-lane A7/A9 376이다. A6는 semantic
  boundary 44, literal observation ports 포함 50, internal link만 5 pins다.
- A7 K4는 반드시 replicated K4와 같은 lane/pin/register state로 비교한다.
- A2/A4/A9의 finite overrun 감소는 ingress/transport storage 효과일 수 있다.
  steady service capacity와 구분한다.
- 서로 다른 Yosys alphabet, wrapper, VCD scope의 절대 cell/depth/toggle 수는
  후보 간 순위에 사용하지 않는다. 같은 committed flow 안의 candidate/reference
  delta만 local evidence다.

## 6. 다음 물리 실험

### Stage P1: N16/K4 A7 screening

1. `f3520b4` branch에서 original prefix K4와 equal-state replicated K4를 freeze.
2. 동일 46 canonical traces, four retire lanes, identical IO/load/clock/SDC로
   Xcelium correctness를 다시 통과.
3. 같은 Liberty/PVT에서 Genus screening. Same-frequency area/power와
   maximum-demonstrated timing table를 분리.
4. Generic crossover가 사라지거나 common correctness가 실패하면 중단.
5. 통과할 때만 period별 재합성 후 Innovus P&R. K4의 88 retire signals와
   endpoint/load를 모두 과금.

### Stage P2: A4 N64 scaling experiment

A7의 경쟁 순위가 아니라 별도 scaling study다. 먼저 N64 exact RTL이 common
conservation/progress gate를 통과해야 한다. 그 뒤 flat N64와 동일 boundary로
Genus/P&R을 실행한다. N16은 `HOLD_FLAT`이므로 제출 후보로 승격하지 않는다.

### Excluded

- A2/A3/A5/A6/A8: predeclared local gate failure로 physical run하지 않는다.
- A9: `99644e8`의 `NOT_ELIGIBLE`을 해제하기 전 physical run하지 않는다.
- A7 segmented K2: rescue failed. Original K2도 structural break-even 실패다.

## 7. Branch and audit index

| Worktree | Branch | Final/head evidence |
| --- | --- | --- |
| `/home/chickgoose/projects/a2` | `agents/a2-adaptive-dual-path` | `b749b6e` |
| `/home/chickgoose/projects/a3` | `agents/a3-homeostatic-inhibition` | architecture `6bad03a`; audits `77bf691`, `6660b48`, `3133a29` |
| `/home/chickgoose/projects/a4` | `agents/a4-quadtree-fabric` | `5f07aee`, `4aea1f9`; A9 reviews `30b63e0`, `7f27539` |
| `/home/chickgoose/projects/a5` | `agents/a5-speculative-pregrant` | `66c76c3`; audits `991f164`, `9f6874b` |
| `/home/chickgoose/projects/a6` | `agents/a6-lossless-aer-codec` | `3d65dae`; pin audit `7331047` |
| `/home/chickgoose/projects/a7` | `agents/a7-parallel-event-compactor` | `f3520b4` |
| `/home/chickgoose/projects/a8` | `agents/a8-age-calendar-wheel` | `4b92f59`; audits `6b27fcd`--`30c1f1a`, `3ca3397` |
| `/home/chickgoose/projects/a9` | `agents/a9-distributed-token-fabric` | local `e571e67`; final blocked `99644e8` |

No candidate branch is merged by this report. The head selects physical
experiments first; any later integration branch must repeat correctness,
46-trace screening and physical comparison from scratch.
