# Clean-Slate AER RTL 역할·기능·검증 계약

Status: team-internal implementation contract, 2026-08-07

## 1. 현재 결정

새로 작성할 RTL은 기존 A23, fovea, rotation-priority 설계 중
하나를 베이스로 삼지 않는다. 기존 설계는 결과 교차 확인을 위한
read-only reference로만 남겨 둔다. 재사용하는 것은 다음의 후보
중립 검증 자산뿐이다.

- 논리 AER event 의미와 deterministic occurrence trace;
- source별 one-entry pending-latch 모델;
- loss, duplicate, phantom, corruption, ordering scoreboard;
- latency, throughput, fairness, timing-distortion 계측;
- native 포트를 연결하는 storage-free TB binding 규칙; 그리고
- Xcelium/Genus/Innovus 호환성 틀.

이 문서는 특정 arbiter, FIFO, ROW/COL, ready/valid, serialization,
packing, pipeline 구조를 강제하지 않는다. 새 설계의 내부 구조는
이 계약을 만족하는 범위에서 자유롭다.

## 2. 새 RTL이 실제로 해야 하는 일

새 후보의 핵심 역할은 **발생한 address-event를 제한된 저장공간과
물리 링크 안에서 받아, 인수 후 손실·중복·위조 없이 수신 경계까지
완료하는 AER transport**이다.

논리 event는 다음과 같다.

```text
(source coordinate/address, optional polarity or event type, occurrence time)
```

`occurrence time`과 `tb_only_event_id`는 DUT payload가 아니다. 시간은
trace의 발생 순간으로 나타나고, TB-only ID는 scoreboard가 손실과
중복을 찾기 위해서만 사용한다. 임의 sequence number를 DUT로 보내
정확성을 쉽게 만드는 구조는 허용하지 않는다.

새 RTL은 최소한 다음 기능을 제공해야 한다.

1. 동시에 pending된 하나 이상의 source 중 인수할 event를 합법적으로
   선택한다.
2. 인수 가능 여부를 source별로 관찰할 수 있게 한다.
3. 한번 인수한 event의 source/address를 완료할 때까지 보존한다.
4. 수신 경계에서 완료 event와 그 source를 관찰할 수 있게 한다.
5. 유한 저장공간이 찼을 때는 인수를 멈추지, 인수한 event를 덮어쓰거나
   버리지 않는다.
6. traffic이 멈춘 후에는 인수한 모든 event를 유한 시간 안에 drain한다.
7. reset 후에 이전 event를 phantom completion으로 내보내지 않는다.

여기서 arbitration policy, queue 깊이, encoding, link 폭, retire lane 수와
pipeline 단계는 해답의 일부이지 benchmark의 전제가 아니다.

## 3. 적용 경계

```text
deterministic logical occurrence trace
                  |
                  v
       one pending latch per source       <- common TB
                  |
                  v
   stateless native pin binding, if needed
                  |
                  v
      new candidate RTL + every required
      synthesizable buffer/codec/decoder  <- PPA boundary
                  |
                  v
    stateless completion normalization
                  |
                  v
       common scoreboard and metrics      <- common TB
```

새 설계가 실제 제품에서 필요로 하는 FIFO, retry state, serializer,
encoder/decoder, backpressure 상태, arbitration, clock gating은 모두 candidate
RTL이어야 하며 PPA 경계에 포함한다. TB binding이 이 기능을
공짜로 제공하면 안 된다.

## 4. Common TB 논리 handshake

`tb/clean/aer_bench_if.sv`는 물리 AER 포트를 강제하는 top-level 규격이
아니라, scoreboard 연결을 위한 논리 계측 seam이다.

| 신호 | 방향 | 의미 |
| --- | --- | --- |
| `clk` | TB -> candidate | 공통 계측 clock |
| `rst_n` | TB -> candidate | normalized active-low reset |
| `source_valid[s]` | TB -> candidate | source `s`의 one-entry latch에 event가 pending |
| `source_event[s]` | TB -> candidate | coordinate/address와, 지원하면 type/polarity를 포함한 event identity |
| `source_ready[s]` | candidate -> TB | 이 cycle에 source `s`를 인수할 수 있음 |
| `retire_valid[l]` | candidate -> TB | lane `l`에 completed logical event가 있음 |
| `retire_event[l]` | candidate -> TB | 완료된 event identity |
| `retire_source[l]` | normalizer -> TB | scoreboard가 참조할 원 source; arbitrary physical payload가 아님 |
| `retire_ready[l]` | TB -> candidate | optional sink-backpressure acceptance |

인수는 clock edge에 `source_valid[s] && source_ready[s]`일 때 발생하고,
완료는 `retire_valid[l] && retire_ready[l]`일 때 발생한다. 설계가
다수 source를 한 cycle에 인수하거나 다수 event를 완료할 수 있다면
그 수만큼 handshake해도 된다. 그러므로 measured logical `event/cycle`은
1을 넘을 수 있다.

다음 규칙은 반드시 지켜야 한다.

- `source_valid && !source_ready`가 지속되는 동안 TB는 `source_event`를
  안정적으로 유지한다. RTL은 이를 보지 못했다고 가정하면 안 된다.
- backpressure를 지원하는 candidate는 `retire_valid && !retire_ready`가
  지속되는 동안 valid, event, source를 안정적으로 유지한다.
- source-local ordering을 지켜야 한다. source 간 완료 순서는 설계가
  정하지만, 같은 source에서 먼저 인수한 event를 뒤 event보다 나중에
  완료해서는 안 된다.
- 한 lane이 한 cycle에 완료하는 논리 event는 하나이다. packing으로
  여러 event를 보내려면 decoder 후 각 event를 별도 lane/완료로
  normalizing해야 한다.

native reset 극성, request 표현, output encoding이 다른 것은 허용된다.
이 경우 candidate native top을 그대로 두고 최소 binding을 작성한다.

## 5. Native binding에 허용되는 일

허용:

- reset 극성 반전;
- 신호 이름, bit order, coordinate/source index의 일대일 매핑;
- 기존 source pending bit를 native request로 연결;
- native completion을 현재 완료된 source/address로 관찰;
- occurrence, acceptance, completion cycle timestamp와 TB-only identity 추적; 그리고
- 기존 `ready`가 있는 sink를 always-ready core에서 high로 묶기.

금지:

- `always_ff`, latch, queue, FIFO, skid/elastic buffer, grant history;
- event retry, duplicate suppression, drop repair;
- DUT에 없는 arbitration, fairness, output backpressure, polarity/type, retire lane;
- cycle 수나 event 수를 바꾸는 무료 serializer/packer/decoder; 그리고
- 물리 링크에서 필요한 조합/순차 변환을 PPA 경계 밖으로 숨기기.

단순 포트 매핑을 넘어서 실제 회로 기능이 필요하면 그 논리는
binding이 아니라 candidate RTL의 일부로 옮긴다.

## 6. Correctness gate와 성능 계측의 분리

현재 mandatory always-ready core에 진입하려면 native candidate와
storage-free binding으로 다음 관찰 가능성을 모두 제공해야 한다.

| Mandatory capability | 필요한 이유 |
| --- | --- |
| `sink_always_ready` | 계속 수신 가능한 공통 core run을 실행 |
| `address_event_correctness` | 완료 event를 발생 source/address와 대조 |
| `occurrence_to_delivery_latency` | 발생에서 완료까지 시간을 같은 clock domain에서 계측 |
| `loss_duplicate_phantom` | TB-only identity를 DUT payload에 넣지 않고 정확성 판정 |
| `fairness` | source별 service count/wait를 계측할 수 있도록 service source를 관찰 |

`fairness` capability은 공정성을 **보장**한다는 뜻이 아니라, 공정성을
측정할 수 있다는 뜻이다. 예를 들어 fixed-priority arbiter도 victim
service source가 보이면 이 test를 RUN하고, 긴 wait 또는 starvation을 결과로
받을 수 있다.

모든 eligible candidate의 먼저 확인할 조건은 다음과 같다.

```text
errors == 0
accepted == delivered after complete drain
```

`errors == 0`은 다음을 모두 의미한다.

- unknown/illegal source 또는 event 없음;
- phantom, duplicate, corrupt completion 없음;
- source-local reordering 없음;
- 인수한 event의 post-drain missing 없음;
- continuous source/output stall 중 payload 변경 없음; 그리고
- drain timeout 없음.

반면 다음은 correctness failure가 아니라 **성능·capacity 결과**다.

- source latch가 찬 동안 같은 source가 재발화해 생긴 `source_overrun`;
- 낮은 acceptance/delivery throughput;
- 긴 request wait, tail latency, service gap;
- 낮은 fairness;
- 큰 timing error 또는 deadline miss; 그리고
- 수용 가능 load의 saturation knee.

즉 기존 AER 참조 설계도 인수한 event를 정확히 처리하면 limit
workload의 correctness를 통과할 수 있다. 새 구조의 개선은 기존
구조를 인위적으로 FAIL 시키는 것이 아니라, **같은 trace에서 정확성을
유지하면서 overrun과 latency를 줄이고 지속 throughput 한계를 높이는
것**으로 증명한다.

## 7. Always-ready 공통 10종의 역할

다음은 현재 native N=16 교차 검증에서 사용한 10개 test intention이다.
각 test에서 correctness gate는 계속 적용되고, 개선 경쟁은 마지막
열의 지표로 한다.

| Workload | 분류 | RTL에 묻는 기본 기능 | 드러내는 AER 한계 | 주요 성능 지표 |
| --- | --- | --- | --- | --- |
| `basic_single` | 기본 | 고립 event의 accept, identity 보존, delivery, drain | 해당 없음; 최소 transport sanity | error, accepted/delivered, sparse latency |
| `basic_sparse` | 기본 | 여러 source의 저율 event와 source-local order | 정상 AER 영역의 latency/power baseline | sparse latency, event/cycle, activity/power |
| `basic_simultaneous` | 기본+경계 | 동시 request의 합법적 arbitration과 complete drain | 공유 자원의 순차 service 지연 | drain cycles, max wait, max latency |
| `limit_load` | 한계 | sparse에서 overload까지 부하 증가 | shared-channel bandwidth saturation과 queueing | sustainable event/cycle, saturation knee, overrun, p95/p99 latency |
| `limit_elephant_mouse` | 한계 | 지속 hot source와 저율 victim의 동시 service | biased/fixed priority starvation | victim max wait, zero-service window, per-source count, fairness |
| `limit_global_fanin` | 한계 | 많은 source의 주기적 동시 request | arbitration fan-in, logic depth, burst drain latency | burst drain, tail latency, event/cycle; N sweep의 area/Fmax |
| `limit_local_cluster` | 한계 | 인접 coordinate의 시간적 cluster | locality를 활용할 수 있는 burst/encoding 기회 | event/cycle, event/pin-cycle, latency, energy/event |
| `limit_distributed_burst` | anti-overfit | 같은 크기의 분산 source burst | local-only compression/priority의 취약성 | local-cluster 대비 throughput, overrun, latency |
| `limit_retrigger` | 한계 | 같은 source의 service time보다 빠른 재발화 | one-entry source overrun, insufficient acceptance/storage | generated/accepted, overrun ratio, same-source service rate |
| `limit_timing_fidelity` | 한계 | 정확한 간격의 event pair와 배경 traffic | queueing으로 스파이크 간 시간 정보 왜곡 | interval error, deadline misses, p95/p99 latency |

`basic_single`은 현재 in-SV synthetic sanity test이다. 나머지 9개 intention은
deterministic generator의 trace로 표현할 수 있으며, `limit_load`는 trace
workload `uniform`에 대응한다. 최종 cross-candidate 수치는 DUT `ready`와
무관하게 먼저 생성되는 deterministic trace를 원본으로 삼는다.

## 8. Optional suite와 현재 미구현 항목

다음 두 test는 native output backpressure를 지원하는 candidate만 RUN한다.

| Workload | 역할 | 지표 |
| --- | --- | --- |
| `basic_backpressure` | 반복 sink stall 중 output stable과 complete recovery | stall assertion, drain correctness, latency |
| `limit_backpressure_shock` | sustained traffic 중 긴 sink stall | finite-storage limit, overrun/loss, recovery time, tail latency |

native ready가 없는 candidate에 TB FIFO를 붙여 이 test를 통과시키지
않는다. capability profile에 `SKIP_UNSUPPORTED`를 남기고 always-ready core
결과와 분리한다. polarity/event type과 multi-lane retirement 또한 각각
optional capability로 분리한다.

글로 정의되었지만 현재 공통 runner에서 아직 완전 자동화/자격 검증되지
않은 항목은 `basic_reset_drain`, native `basic_polarity`, automatic
16/64/256 `limit_scale`, fixed-pin `limit_pin_budget`, mixed-phase trace다.
이들을 PASS한 것처럼 발표하지 않는다.

## 9. 성능 계약

성능은 hard-coded 구조 추정값이 아니라 common TB가 완료 event에서
실제로 측정한다.

- `generated`, `source_overrun`, `accepted`, `delivered`를 각각 보고;
- end-to-end latency = occurrence-to-delivery;
- internal latency = acceptance-to-delivery;
- request wait = occurrence-to-acceptance;
- logical throughput = completed logical events / measured cycle span;
- source service count, Jain fairness, maximum wait, service gap/zero-service window;
- p50/p95/p99 latency, deadline miss/censoring;
- same-source input/output interval error; 그리고
- load sweep의 acceptance/overrun saturation knee.

최종 후보 비교 시 추가로 같은 물리 조건의 `events/pin-cycle`,
post-route 시계 주기를 결합한 `events/s`, area, power, `energy/event`를
보고한다. sparse와 near-saturation activity window를 분리한다. 공식
workload/제약이 없는 현 단계에서 특정 하나의 가중합 점수를 발명하지
않고, correctness gate 후 Pareto 관계를 본다.

## 10. 새 후보 작성 단위

최소 제출 단위는 다음이다.

1. **native synthesizable top**: 실제 arbitration, storage, link/codec,
   completion 기능을 모두 소유한다.
2. **storage-free TB binding**: native pin을 common logical seam으로 연결한다.
3. **candidate profile**: source count, native protocol, retire lane, mandatory/
   optional capability를 선언한다.
4. **file list/top/parameter manifest**: 같은 RTL revision을 simulation, Genus,
   Innovus가 공유하게 한다.
5. **result identity**: candidate name과 commit SHA를 summary/event metrics에
   기록한다.

권장 초기 작업 순서는 다음과 같다.

1. N=16, always-ready, one-retire-lane 최소 구조로 `basic_single`,
   `basic_sparse`, `basic_simultaneous`의 correctness를 먼저 통과한다.
2. 지속 traffic에서 인수한 event의 overwrite/drop이 없는지 `limit_load`와
   complete drain으로 확인한다.
3. `elephant_mouse`, `global_fanin`, `retrigger`로 arbitration/storage의
   병목을 계측한다.
4. `local_cluster`와 `distributed_burst`를 같이 보아 locality 특화
   overfit을 막는다.
5. `timing_fidelity`로 시간 정보 보존을 검토한다.
6. 이후에만 backpressure, event type, multi-lane 같은 optional 기능을
   추가하고 그 회로 비용을 PPA에 포함한다.

새 설계의 최종 목표는 공통 test를 단순히 PASS하는 것이 아니라,
일반적인 sparse AER 기능을 유지하면서 기존 공유 AER transport의
bandwidth, arbitration, fairness, overrun, timing-distortion 한계 중 하나
이상을 **더 적은 area/power 비용 대비 더 높은 유효 event service**로
이동시키는 것이다.

## 11. 관련 공통 자산

- `docs/verification/aer-clean-benchmark-spec.md`
- `docs/verification/aer-native-capability-profile.md`
- `docs/verification/aer-trace-loader.md`
- `benchmarks/clean_slate_aer/manifest.example.json`
- `tb/clean/aer_bench_if.sv`
- `tb/clean/aer_clean_tb.sv`
