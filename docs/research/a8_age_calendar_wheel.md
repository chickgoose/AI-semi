# A8 O(1) Age Calendar-Wheel Scheduler

Status: phase-2 RTL/scaling audit complete, 2026-08-07

## 1. 연구 질문과 경계

A8의 질문은 `NUM_SOURCES`개의 age counter를 매 cycle 갱신하지 않고도
오래 기다린 AER source를 우선 service할 수 있는가이다. DUT가 보는 정보는
`source_valid`, 안정적으로 유지되는 `source_event`, 그리고 sink handshake뿐이다.
trace의 occurrence cycle, TB-only event ID, relation ID, deadline은 DUT 입력이나
payload가 아니다. 특히 A8은 deadline scheduler가 아니며 deadline miss는 결과를
분석하는 TB metric일 뿐이다.

제안 구조는 source가 처음 pending으로 관측된 cycle을 작은 modulo calendar
class로 한 번만 기록한다. 시간 진행은 단 하나의 global phase/epoch pointer로
표현하고, service는 가장 오래된 nonempty epoch부터 수행한다. source별 state를
매 cycle 증가시키는 aging counter와, RR pointer만 둔 arbiter는 이 가설의
구현으로 인정하지 않는다.

## 2. 선행 연구에서 가져오는 것과 가져오지 않는 것

- Varghese와 Lauck의 timing wheel은 제한된 범위의 timer에 대해 circular
  buffer를 사용하면 start/stop/maintenance가 O(1)이 될 수 있음을 보였고,
  더 긴 범위에는 hashing 또는 여러 해상도의 wheel이 필요하다고 설명했다
  ([SOSP 1987 원문](https://www.cs.columbia.edu/~nahum/w6998/papers/sosp87-timing-wheels.pdf)).
  A8은 timer expiration이 아니라 pending request의 age ordering에 이 원리를
  적용한다.
- Brown의 calendar queue는 simulation future-event set을 여러 bucket으로
  나누고 평균 O(1) queue operation을 목표로 했지만, 분포와 overflow 처리가
  성능의 전제임을 함께 보여 준다
  ([CACM 1988 원문](https://www.cs.odu.edu/~cmo/classes/old/cs475sp05/papers/brown88.pdf)).
  따라서 A8은 “calendar”라는 이름만 빌리지 않고 wrap horizon과 collision
  조건을 명시적으로 증명한다.
- Abts와 Weisser는 Cray XT의 packet age를 arbitration에 넣어 global fairness와
  latency variance를 개선하는 방법을 보고했다
  ([SC 2007 저자 페이지](https://research.google/pubs/age-based-packet-arbitration-in-large-k-ary-n-cubes/)).
  Lee 등은 정확한 age 유지가 packet overhead, age update, arbitration complexity를
  만든다고 지적하고 hop count 기반 근사를 연구했다
  ([PACT 2010 원문](https://people.csail.mit.edu/leejw/pact10_age.pdf)).
  A8은 hop-count 근사 대신 첫 관측 epoch를 보존하되 source별 increment를 없앤다.
- Kim과 Shin의 hardware EDF scheduler는 finite deadline representation을 위한
  deadline folding과 병렬 비교를 사용했다
  ([RTSS 1997 원문](https://rtcl.eecs.umich.edu/rtclweb/assets/publications/1997/kim1997scalablehardware.pdf)).
  modulo timestamp 비교가 finite hardware에서 유용하다는 근거지만, A8에는
  외부 deadline이 없으므로 이를 occurrence-derived age ordering으로만 제한한다.
- Sharma 등의 programmable calendar queue는 시간에 따라 priority가 변하는
  packet scheduling을 calendar abstraction으로 구현하고 delay/fairness 정책을
  평가했다
  ([NSDI 2020 원문](https://www.usenix.org/conference/nsdi20/presentation/sharma)).
  A8의 고정 폭 RTL은 programmable policy가 아니라 oldest-class-first 한 가지다.
- Boahen의 fabricated AER link 연구는 높은 throughput에서 queueing latency가
  inter-event timing을 왜곡하며, timing error 요구가 capacity보다 낮은 offered
  load를 요구할 수 있음을 분석·측정했다
  ([IEEE TCAS-I 2004 원문](https://web.stanford.edu/group/brainsinsilicon/documents/04_journ_IEEEtcs_AERChanIII.pdf)).
  이것이 A8에서 평균 throughput만이 아니라 pair gap, latency tail, sparse
  latency를 같이 보는 이유다.

## 3. Timestamp와 bucket encoding

기본 N=16 후보의 제안 parameter는 다음과 같다.

```text
BUCKET_CYCLES = 4
EPOCH_COUNT   = 8                 // power of two
HORIZON       = 32 cycles
epoch         = floor(cycle / BUCKET_CYCLES) mod EPOCH_COUNT
tag[s]        = source s가 처음 pending으로 관측된 epoch
tracked[s]    = tag[s]가 유효함
```

각 source가 처음 보일 때 `tag[s] <- epoch`를 한 번 수행한다. valid가 ready보다
먼저 올라온 뒤 여러 cycle 유지되어도 `tracked[s]`가 이미 1이면 tag를 다시
쓰지 않는다. tag storage는 source당 `log2(EPOCH_COUNT)` bit이고 age 자체는
`(epoch - tag[s]) mod EPOCH_COUNT`로 해석한다. global time transition은 작은
phase counter와 epoch pointer 하나만 갱신한다.

논리적인 `bucket_nonempty[e]`는 `tracked/tag`와 이번 cycle의 fresh request를
decode해 만든 epoch bitmap이다. oldest nonempty epoch를 먼저 고르고, 같은
bucket 안에서는 rotating tie pointer로 source를 선택한다. 이 pointer는 같은
quantized class의 결정적 편향을 막는 tie-break일 뿐 A8의 핵심 scheduler가
아니다.

## 4. Same-cycle과 handshake semantics

1. `fresh[s] = source_valid[s] && !tracked[s]`이다.
2. 기존 tracked request와 fresh request를 같은 combinational eligibility set에
   넣는다. idle calendar에서 fresh request가 들어오면 같은 cycle에 ready를
   낼 수 있으므로 불필요한 bubble이 없다.
3. 선택되지 않은 fresh request만 현재 epoch tag로 저장한다. 같은 cycle에 바로
   선택된 fresh request는 tracked state를 만들 필요가 없다.
4. 선택된 tracked request는 handshake edge에서 tracked를 지운다. source model은
   그 edge 뒤 valid를 내리므로 한 request를 재삽입하지 않는다.
5. output slot이 막히면 ready를 내지 않고 output payload를 안정적으로 유지한다.
   다만 frozen 46-trace의 sink는 always-ready이며, 무한 sink stall에는 bounded
   wait나 finite modulo ordering을 주장하지 않는다.

## 5. Wraparound와 bucket collision proof obligation

가정은 single-retire, sink-always-ready, source당 최대 one pending request,
work-conserving service다. 임의 request `r`가 처음 관측될 때 `r`보다 먼저
service될 수 있는 request 수는 최대 `NUM_SOURCES-1`이다. 이후 도착은 더 새
epoch이거나 같은 quantized bucket의 tie이며, 어떤 경우에도 시스템 전체에
동시에 존재하는 경쟁자는 source당 하나뿐이다. 매 cycle 한 request를 service하므로

```text
wait(r) <= NUM_SOURCES - 1 cycles
```

이다. 따라서 정확한 modulo ordering의 충분조건은

```text
EPOCH_COUNT * BUCKET_CYCLES > NUM_SOURCES - 1
```

이다. 기본값은 `32 > 15`이므로 pending tag가 살아 있는 동안 epoch가 한 바퀴
돌지 않는다. 서로 다른 절대 시간이 같은 modulo bucket에 들어가는 wrap
collision도 발생하지 않는다. parameter elaboration/unit test는 이 조건을
위반하면 실패해야 한다.

같은 `BUCKET_CYCLES` 구간의 request는 의도적으로 같은 bucket에 충돌한다.
이는 wrap ambiguity가 아니라 quantization이다. rotating tie-break 때문에
동일 class 안에서는 정확한 FCFS가 아니며, 늦게 온 source가 먼저 service될 수
있다. 최대 wait bound는 유지되지만 input gap이 `BUCKET_CYCLES-1` 이하일 때
ordering/latency tail 이득이 사라질 수 있다.

sink가 총 `S` cycle 막히는 확장 환경에서는 보수적으로
`wait <= S + NUM_SOURCES - 1`이고 horizon도 그보다 커야 한다. `S`가 무한하거나
미리 bound되지 않으면 finite modulo timestamp만으로 wrap-free oldest ordering을
증명할 수 없다. 이 후보 profile은 그래서 mandatory always-ready core만 RUN으로
선언한다.

## 6. Ordering, fairness, bounded wait

- 서로 다른 non-wrapped epoch에 속한 request는 더 오래된 bucket이 반드시 먼저다.
- 같은 bucket은 RR tie order이며 exact occurrence ordering을 주장하지 않는다.
- source-local ordering은 common one-entry source latch 때문에 자연히 유지된다.
  첫 event가 accept되기 전 같은 source의 재발화는 TB에서 overrun이며 DUT queue의
  두 번째 entry가 아니다.
- sink-always-ready에서 work-conserving이며 pending이 하나라도 있으면 매 cycle
  하나를 accept한다. 따라서 max request wait bound는 `N-1`이다.
- age priority는 hot source의 새 request가 오래 기다린 mouse를 계속 추월하지
  못하게 한다. 다만 overload에서 source overrun 자체를 없애지는 못하며, single
  lane의 throughput ceiling은 1 event/cycle이다.

## 7. Timing-fidelity 가설

가설 H1은 oldest-class-first가 RR/fixed priority보다 elephant/mouse, rotating
victim, moving hotspot에서 max wait와 demand-conditioned zero-service window를
줄인다는 것이다. H2는 오래된 backlog를 먼저 줄여 phase transition 뒤 sparse
probe의 p95/p99 E2E latency를 낮춘다는 것이다. H3는 정확한 age가 아니라 4-cycle
class이므로 2-cycle timing pair에는 오히려 pair-gap p95/p99가 나빠질 수 있다는
것이다. deadline은 DUT 입력이 아니며 `timing_pair_metrics.py`가 TB-only relation과
deadline으로 사후 계산한다.

측정 시 다음을 숨기지 않는다.

- isolated sparse request의 pipeline latency와 mock/baseline 대비 증감;
- 1/4/16-event burst에서 bucket quantization으로 생긴 tail spread;
- timing-pair actual output-gap error p95/p99와 dropped/censored pair;
- overrun 때문에 delivery되지 않은 event를 latency sample에서 제외하되 별도
  overrun ratio와 censor count로 표시;
- throughput은 fixed stimulus window의 completed event/cycle이며 drain time으로
  denominator를 늘리지 않음.

## 8. State bits와 예상 PPA

payload는 source latch가 ready까지 안정적으로 유지하므로 pending event를 DUT에
복제 저장하지 않는다. output elastic register만 event를 보존한다. 기본 N=16,
ADDR_WIDTH=16일 때 scheduler-owned state 추정은 다음과 같다.

| State | Bits |
| --- | ---: |
| tracked bitmap | 16 |
| 3-bit epoch tag/source | 48 |
| global 3-bit epoch + 2-bit phase | 5 |
| 4-bit same-bucket tie pointer | 4 |
| output valid + 16-bit event + 4-bit source | 21 |
| 합계 | 94 |

실제 calibration reference는 N=16에서 `AGE_WIDTH=clog2(2N)=5`이므로 전체 상태가
121 bit이고, tracked source의 counter incrementer/toggle cone이 매 cycle 동작한다.
A8 B4의 age-related state는 `16 + 48 + 5 = 69` bit이며 pending source의 tag는
삽입 때만 바뀐다. 단, 이 표는 합성 결과가 아닌 RTL bit accounting이다.

예상 critical path는 `tag/fresh -> bucket_nonempty decode -> rotated oldest-bucket
priority -> in-bucket source select -> ready/event mux`다. global age update는 O(1)이지만
grant combinational logic가 source 수와 무관하다는 뜻은 아니다. EPOCH_COUNT를 크게
하면 oldest-bucket encoder가, NUM_SOURCES를 크게 하면 tag decode와 source mux가
커진다. 후보가 기능적으로 유효한 뒤 동일 local synthesis 조건에서 area/Fmax/power를
재야 하며, 현재 단계에서는 사용자 승인 없는 server PPA를 실행하지 않는다.

## 9. 검증과 실패 기준

wheel unit/counterexample test는 다음을 직접 검사한다.

- first-seen tag가 held-valid 동안 변하지 않음;
- older bucket 우선, same-cycle work conservation, simultaneous complete drain;
- modulo boundary 직전/직후 request의 올바른 순서;
- same-bucket collision의 허용된 RR tie와 `N-1` wait bound;
- horizon이 N보다 작을 때 실제 반례를 만들어 parameter guard의 필요성을 입증;
- output stall 중 output 안정성(성능/ordering 보장은 always-ready 조건으로 제한).

후보 실패 기준은 다음 중 하나다.

1. mandatory trace에서 error, duplicate, phantom, corruption, source-local reorder,
   `accepted != delivered`가 하나라도 발생;
2. always-ready unit에서 pending request가 `N-1` cycle을 초과해 기다림;
3. tag가 held-valid 동안 recaptured되어 age가 젊어짐;
4. wrap-safe parameter 조건을 enforce하지 못함;
5. timing pair/rotating victim 핵심 family에서 RR calibration보다 tail 또는 fairness가
   개선되지 않으면서 sparse latency나 PPA만 악화;
6. bucket quantization이 pair-gap p99, phase recovery, 또는 sparse p99를 크게
   악화시켜 1-cycle bucket variant로도 회복되지 않음;
7. single-lane ceiling과 source overrun을 age policy의 throughput 개선으로 잘못
   해석해야만 이득을 주장할 수 있음.

연구 판정은 46개 frozen JSONL SHA를 그대로 사용한 correctness regression과,
timing pair, rotating victim, elephant/mouse, phase transition, retrigger, moving
hotspot, uniform family의 max wait, zero-service window, demand-normalized fairness,
pair gap p95/p99, E2E tail, overrun, event/cycle을 함께 보고 내린다.

## 10. Phase-2 판정

B1/B2/B4/B8, exact counter, RR을 같은 one-entry/source, single-lane,
registered-output 조건에서 비교했다. 46-trace 공식 N=16 회귀는 여섯 구조 모두
correctness issue 0이었고, N=16/32/64의 11-trace scaling matrix는 198/198 PASS였다.
adversarial test는 wrap 직전/직후 ordering, 명시적 same-bucket inversion, 8-cycle
연속 stall, unsafe horizon elaboration rejection을 포함한다.

global aging transition 가설 자체는 부분적으로 확인됐다. B8 sequential-state
toggle은 N=16/32/64에서 11.64/14.04/15.55 bit-flip/cycle로 완만하게 증가한 반면
exact counter는 16.18/29.66/59.58이었다. 그러나 현재의 unrolled oldest-bucket와
source priority scan이 이 state 이득을 조합 논리 이득으로 바꾸지 못했다. local
Yosys generic proxy에서 B8은 exact보다 모든 N에서 cell 수와 최장 위상 경로가
컸다. 이는 technology PPA/Fmax가 아니라 `proc; flatten; opt; stat; ltp -noff`
결과이며, loop-variable 오검출을 피하기 위해 `read_verilog -nolatches`를 썼다.

현재 RTL은 advancement shortlist에서 제외한다. B8은 state/toggle Pareto 연구점은
남기지만, N=16 공식 phase-transition max wait가 exact 12 대비 18, uniform-1.25
max wait가 exact 9 대비 16이었고, local depth proxy도 177 대 163으로 나빴다.
B4는 N=16 절충점이지만 N=64 toggle이 78.31로 exact 59.58을 넘어 scaling 실패다.
B1은 exact ordering control과 동일한 tail을 보이지만 tag rewrite 때문에 toggle과
depth가 exact보다 훨씬 크다. 따라서 승인 없는 server PPA로 넘어갈 근거가 없다.

후속 구현이 shortlist로 복귀하려면 다른 트랙 원리를 쓰지 않은 채 다음을 모두
만족해야 한다.

1. 198-run correctness와 bounded-stall/wrap tests를 그대로 PASS할 것;
2. B8 수준의 N=64 state/toggle scaling을 유지할 것;
3. exact 대비 phase/uniform max-wait 증가를 각각 2 cycle 이내로 줄일 것;
4. exact 대비 timing-pair p99를 악화시키지 않을 것;
5. 동일 local proxy에서 exact보다 generic cell 또는 logic depth 중 적어도 하나를
   개선하고 다른 하나의 악화를 5% 이내로 제한할 것.
