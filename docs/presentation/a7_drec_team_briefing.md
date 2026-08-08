# DREC: Dual-Rank Elastic AER Compactor

팀 발표용 기술 브리핑 · 2026-08-08

> 한 문장 요약: AER 요청과 현재 사용 가능한 출력 lane을 각각 순위화하고
> 두 순위를 연결하여, 막힌 lane은 보존하면서 나머지 lane은 같은 cycle에
> 독립적으로 retire/refill하는 K-lane event compactor이다.

## 발표 전 근거 상태

- 공통 benchmark 기준: frozen N=16, 46 traces, base `ad96895`
- 후보 근거: A7 original shared-prefix K=4, branch evidence `f3520b4`
- 비교 기준: 동일 K, 동일 104-bit register state, 동일 rotation/fairness,
  동일 independent-ready/refill semantics의 replicated-selector reference
- 서버 Xcelium lockstep은 1,223 cycle, 3,761 accepted = 3,761 delivered로
  cycle-exact PASS했다.
- 동일 N=16/K=4, 동일 104-bit state와 376 functional pins, slow GSCLIB045,
  5 ns 조건의 **Genus standard-cell screening 결과는 확보했다.**
- Genus는 pre-layout screening일 뿐이다. **Innovus fixed-netlist 진단은 현재
  진행 중이며 post-route PPA/Fmax는 아직 미확정**이다. 따라서 Genus timing을
  최종 Fmax 또는 silicon 결과로 부르지 않는다.

---

## Slide 1 — 문제: K개 이벤트를 꺼내기 위해 선택기를 K번 복제해야 하는가?

기존의 단순한 K-way 구현은 회전 우선순위 선택기를 K번 연쇄하고, 앞에서
선택된 winner를 매 단계 mask한다. K가 커지면 같은 요청 집합을 반복해서
탐색하고 masking dependency가 길어진다.

DREC의 질문은 좁고 검증 가능하다.

> 하나의 shared source-rank 계산을 K개 출력이 재사용하면, 동일 기능과 동일
> 저장공간을 유지하면서 replicated selection보다 구조 비용을 줄일 수 있는가?

주의할 점:

- 다중 lane 자체가 혁신은 아니다.
- 처리율 증가는 우선 K개의 물리적 서비스 용량에서 온다.
- DREC가 증명해야 할 이점은 **같은 K에서 selection logic의 중복을 줄이는가**이다.

---

## Slide 2 — 구조: source rank와 available-lane rank를 연결한다

```text
 source pending bitmap r[i]                         lane ready/empty a[l]
 source payloads                                          │
          │                                                ▼
          ▼                                      small lane-prefix scan
 shared population-prefix scan                    available-slot rank S[l]
 P[i] -> exclusive count E[i]                              │
          │                                                │
          ▼                                                │
 rotation base b -> cyclic source rank C_b(i)              │
          │                                                │
          └──────────── match C_b(i) == S[l] ──────────────┘
                                   │
                                   ▼
                     K registered elastic retire lanes
                      ┌────────┬────────┬────────┬────────┐
                      │ lane 0 │ lane 1 │ lane 2 │ lane 3 │
                      └────────┴────────┴────────┴────────┘
                         hold if stalled / refill if free
                                   │
                                   ▼
                           completed AER events
```

핵심 식은 다음과 같다.

```text
P[i] = sum(r[0:i])
E[i] = P[i] - r[i]

C_b(i) = E[i] - E[b]                    when i >= b
       = (P[N-1] - E[b]) + E[i]         when i < b

a[l] = !valid[l] || ready[l]
S[l] = sum(a[0:l]) - a[l]
```

활성 source의 cyclic rank `C_b(i)`와 사용 가능한 lane의 slot rank `S[l]`가
같을 때 그 이벤트를 해당 lane에 배치한다. 이미 valid이고 ready가 아닌 lane은
절대 덮어쓰지 않는다. acceptance가 있었다면 rotation base는 마지막으로 받은
source 다음으로 이동한다.

---

## Slide 3 — 무엇을 정확하게 보장했는가?

### 검증된 기능

- 46 common traces를 K=1/2/4로 실행한 138/138 run에서 scoreboard error 0
- accepted event의 post-drain loss, duplicate, corruption, phantom output 0
- N=16의 65,536개 request bitmap을 K=1/2/4 각각 exhaustive 검사
- independent-ready adversarial test에서 stalled lane의 valid/event/source 안정성,
  다른 lane의 진행, source 중복 배치 방지와 conservation 통과
- 서버 Xcelium에서 prefix K=4와 equal-state replicated K=4를 동시에 구동한
  randomized lockstep 검증이 PASS했다. 1,223 cycle 동안
  ready/valid/event/source가 cycle-exact하게 일치했고,
  3,761 accepted = 3,761 delivered로 drain conservation 통과
- all-ready service opportunity에서 persistent source는 최대 `ceil(N/K)`번의
  service cycle 안에 acceptance된다는 bound를 확인

### 반드시 공개할 계약 제한

DREC의 native 계약은 **source당 동시에 outstanding event가 하나**인 모델이다.
앞서 accept된 같은 source의 event가 stalled lane에 남아 있을 때 다음 occurrence를
또 accept하지 않는다. 따라서 이 검증만으로 arbitrary-depth per-source queue나
일반적인 모든 backpressure 환경을 지원한다고 주장할 수 없다.

또한 frozen 46-trace suite는 sink-always-ready가 공통 조건이다. 독립 lane stall은
candidate 전용 adversarial qualification을 통과했지만, 아직 frozen common
multi-lane stall workload와 동등한 sign-off 결과는 아니다.

---

## Slide 4 — 정직한 결과: K=2는 실패했고 K=4에서 처음 교차했다

### 동일 상태량 generic structural comparison

| N | K | DREC prefix gates / depth | Replicated gates / depth | State bits | 판정 |
| ---: | ---: | ---: | ---: | ---: | --- |
| 16 | 1 | 3,689 / 130 | 2,304 / 67 | 41 | reject |
| 16 | 2 | 4,299 / 133 | 3,733 / 133 | 62 | **reject: gates +15.2%** |
| 16 | 4 | 5,592 / 139 | 6,729 / 248 | 104 | **generic proxy crossover** |

N=16/K=4에서는 replicated reference 대비 generic gates가 16.9% 적고,
generic topological depth가 44.0% 낮다. 그러나 이는 Yosys `techmap; opt`
이후의 1-bit generic cell/depth proxy이며 standard-cell area/Fmax가 아니다.

별도의 Yosys/ABC generic mapping에서도 DREC 5,272 combinational cells/depth
113, replicated 5,542/depth 161로 방향이 유지됐다. 효과 크기는 각각 4.9%,
29.8%로 줄었으므로 발표에서는 이를 “두 mapper에서 유지된 구조 가설”로만
설명하고 Genus 결과로 표현하지 않는다.

### 서버 Genus standard-cell screening

동일한 N=16/K=4, 104-bit state, 376 functional pins, slow GSCLIB045 library와
5 ns target을 사용했다.

| 항목 | DREC prefix | Equal-state replicated | screening 해석 |
| --- | ---: | ---: | --- |
| mapped area | 5,826.569 um² | 7,928.415 um² | DREC −26.510% |
| combinational cells | 3,089 | 4,407 | DREC −29.907% |
| 5 ns setup WNS | 0 ns | −1.0435 ns | DREC만 200 MHz target 충족 |
| screening frequency | 200.000 MHz | 165.467 MHz | pre-layout estimate only |
| vectorless total power | 0.550772 mW | 0.739729 mW | DREC −25.544%, screening only |
| latch / unresolved / error | 0 / 0 / 0 | 0 / 0 / 0 | synthesis integrity check 통과 |

이 결과는 Yosys에서 보인 K=4 crossover가 target standard-cell mapping에서도
유지됐다는 **GO 근거**다. 다만 power는 activity-annotated post-route power가
아닌 vectorless estimate이며, replicated의 165.467 MHz도 post-route
demonstrated Fmax가 아니다. 최종 PPA/Fmax 주장은 Innovus route, setup/hold,
unconstrained-path 확인과 per-target resynthesis 이후에만 가능하다.

### workload 수치와 올바른 해석

| K | uniform 2.0 throughput | lane utilization | 해석 |
| ---: | ---: | ---: | --- |
| 1 | 0.9995 event/cycle | 100% | 1-lane capacity |
| 2 | 1.9990 event/cycle | 100% | offered load를 모두 처리 |
| 4 | 1.9990 event/cycle | 50% | workload 상한 때문에 K=2 이상을 입증 못함 |

- 같은 K에서 prefix와 replicated는 87개 aggregate row의 기능·성능 metric이
  모두 동일했다.
- 따라서 throughput/fairness는 K lanes와 공통 policy의 결과이며, prefix가
  replicated보다 알고리즘적으로 빠르다는 증거가 아니다.
- K=4는 4개의 22-signal retire lane, 총 88 retire signals를 노출한다. 향후
  물리 비교에서는 네 endpoint, output load, pin/floorplan 및 routing 비용을
  모두 과금해야 한다.

---

## Slide 5 — 혁신성의 정확한 경계와 Stop/Go 결정

### 선행기술

- prefix sum/scan과 compaction은 Blelloch 및 GPU scan 연구의 기존 기술이다.
- parallel-prefix round-robin arbitration도 기존 기술이다.
- 2013 m-select RR은 첫 `m`개 active request를 prefix logic으로 선택하므로
  “prefix multi-grant를 처음 발명했다”는 주장을 직접 반박한다.
- elastic ready/valid output register도 표준적인 구현 기술이다.

### 우리가 발표할 수 있는 기여

> 하나의 cyclic source-rank를 독립적으로 사용 가능한 lane의 rank와 결합하여,
> stalled registered lane을 보존하면서 나머지를 compact refill하는 synchronous
> AER 구현을 만들고, exact contract 검증과 equal-state reference를 통해
> N=16/K=4의 구조적 crossover를 측정했다.

즉 “새로운 prefix arbiter”가 아니라 **AER independent-ready 문제에 특화한
dual-rank elastic composition과 그 손익분기 검증**이 기여다.

### 다음 단계의 Stop/Go

**현재 판정: Genus screening GO — Innovus fixed-netlist 진단 진행 중.
최종 채택 또는 최종 PPA 승리는 아님.**

1. **완료:** original prefix K=4와 equal-state replicated K=4를 동일
   104-bit state, 376 functional-pin boundary로 비교했다.
2. **완료:** 서버 Xcelium lockstep에서 cycle-exact equivalence와
   3,761 accepted = delivered를 확인했다.
3. **완료:** 동일 slow GSCLIB045/5 ns Genus screening에서 DREC의 area,
   combinational-cell, timing과 vectorless-power 이점이 유지됐다.
4. **진행 중:** 동일 netlist의 Innovus fixed-netlist 진단으로 placement/routing 후
   timing 한계를 찾는다. 이 단계는 디버깅·bracketing용이며 최종 Fmax가 아니다.
5. route 실패, setup/hold violation, unconstrained path 또는 endpoint/pin 비용으로
   crossover가 사라지면 **STOP**한다. 진단이 유지될 때만 period별 Genus 재합성과
   complete Innovus P&R을 수행하고, 그 결과로 최종 판정한다.

### 발표에서 금지할 표현

- “최초의 GPU-style hardware compactor”, “새로운 parallel-prefix arbiter”
- “최초의 bitmap-to-K selector”, “새로운 multi-grant round robin”
- “4x throughput”, “K=4가 K=2보다 빠르다”
- generic proxy만으로 “area/Fmax/power/PPA 승리”
- Genus의 200/165.467 MHz를 “post-route 실측 Fmax”로 표현
- vectorless power를 “실제 workload energy/event”로 표현
- 88 retire signals와 네 endpoint를 제외한 비용 비교
- one-outstanding/source 제한을 생략한 “일반적인 backpressure-complete AER”

---

## 2분 발표 대본

안녕하세요. 제가 제안하는 구조는 DREC, Dual-Rank Elastic AER Compactor입니다.

기존에 한 cycle에 K개 이벤트를 선택하려면 보통 회전 우선순위 선택기를 K번
연쇄하고, 앞 단계 winner를 다음 단계에서 mask합니다. 이 방식은 K가 커질수록
같은 요청 집합을 반복해서 탐색하고 선택 의존성이 길어집니다. DREC는 모든
source의 pending bitmap을 한 번 prefix-scan해서 현재 rotation 기준의 cyclic
rank를 만듭니다. 동시에 ready이거나 비어 있는 output lane만 작은 prefix로
순위화합니다. 그리고 source rank 0, 1, 2, 3을 available lane rank 0, 1, 2,
3에 연결합니다.

이 구성의 장점은 어떤 lane이 stall되어도 그 register는 안정적으로 유지하면서
다른 lane은 독립적으로 retire하고 같은 cycle에 refill할 수 있다는 점입니다.
공통 46개 trace를 K=1, 2, 4로 실행한 138개 run에서 오류와 event loss가 없었고,
N=16의 모든 65,536개 request bitmap 및 별도 independent-stall 검증도 통과했습니다.

결과를 과장하지 않는 것이 중요합니다. K=2에서는 DREC가 4,299 generic gate로
replicated reference의 3,733보다 15.2% 커서 실패했습니다. 반면 K=4에서는 동일한
104-bit state를 사용하면서 5,592 대 6,729 gate, depth 139 대 248로 처음 구조적
crossover가 나타났습니다. 하지만 현재 workload는 최대 2 event/cycle만 제공하므로
K=4 throughput은 K=2와 같은 1.999이고 lane utilization은 50%입니다. 따라서 이는
4배 throughput 주장이 아니라, K가 커질 때 shared rank 계산이 복제된 selection
비용을 상쇄하기 시작한다는 결과입니다.

prefix scan, compaction, m-select round robin 자체는 선행기술입니다. 저희의 좁고
방어 가능한 기여는 cyclic source rank와 available-lane rank를 independent-ready
AER 계약에 결합하고, 동일 상태량 reference로 손익분기점을 측정한 것입니다.
서버 Xcelium lockstep도 1,223 cycle, 3,761 accepted와 delivered가 일치해
통과했습니다. 동일 slow GSCLIB045, 5 ns의 Genus screening에서는 DREC가
5,826.569 제곱마이크로미터, replicated가 7,928.415였고, DREC만 200 MHz target의
WNS 0을 만족했습니다. vectorless power도 0.550772 대 0.739729 밀리와트였습니다.
하지만 이것은 pre-layout screening입니다. 현재 Innovus fixed-netlist 진단을
진행 중이며, 네 lane과 376 functional pins, endpoint와 배선 비용을 포함한
post-route 결과가 나오기 전에는 최종 PPA나 Fmax 승리를 주장하지 않겠습니다.

---

## 발표 자료에 둘 선행기술 링크

- [Blelloch, Prefix Sums and Their Applications](https://www.cs.cmu.edu/~guyb/papers/Ble93.pdf)
- [Ugurdag and Baskirt, Fast Parallel Prefix Logic Circuits for n-to-n Round-Robin Arbitration](https://doi.org/10.1016/j.mejo.2012.04.005)
- [Ugurdag, Temizkan, and Goren, Generating Fast Logic Circuits for m-Select n-Port Round Robin Arbitration](https://doi.org/10.1109/VLSI-SoC.2013.6673286)
- [Merrill and Garland, Single-pass Parallel Prefix Scan with Decoupled Look-back](https://research.nvidia.com/sites/default/files/pubs/2016-03_Single-pass-Parallel-Prefix/nvr-2016-002.pdf)

## 저장소 내부 근거

- `docs/research/a7_parallel_event_compactor.md`
- `reports/a7-parallel-event-compactor/results.md`
- `reports/a7-parallel-event-compactor/adversarial-scaling.md`
- `reports/a7-parallel-event-compactor/adversarial-structural.csv`
- `docs/research/wave1-eight-track-final-report.md`
- `docs/verification/aer-physical-ppa-contract.md`
