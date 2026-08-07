# Eight-track link/pin semantic normalization audit

## 결론

A2--A9의 현재 native top은 하나의 공통 물리 link를 구현하지 않는다. 따라서
모든 후보에 공통으로 다시 계산할 수 있는 값은 **frozen candidate 경계 전체의
functional event/pin-cycle**이며, link-only event/pin-cycle은 후보 간 순위 지표가
될 수 없다. N=16, native default parameter에서 clock/reset을 제외한 전체 functional
pin 수는 A2/A3/A4/A5=310, A7/A9(K=4)=376, A8=309이다. A6 v2 top은 의미 seam
44핀에 측정/관측 출력 6핀을 더한 50핀이고, encoder와 decoder 사이의 내부 link만
따로 자르면 5핀이다.

A6 v1/v2/v3의 기각 결론은 이 감사에서 변경하지 않는다. 특히 기존 A6 link-only
수치는 실제 data/delimiter가 활동한 cycle만 분모에 사용했으므로, 공통 계약의 전체
measurement window 및 전체 후보 경계로 계산한 값과 직접 비교할 수 없다.

## 범위와 공통 정의

조사는 2026-08-07에 `/home/chickgoose/projects/a2`부터 `a9`까지를 read-only로
수행했다. 서버 실행, 합성, 다른 worktree 수정은 하지 않았다. 근거 정의는
`docs/verification/aer-physical-ppa-contract.md` 157--175행과
`docs/verification/aer-clean-benchmark-spec.md` 203--227행이다.

공통 numerator와 denominator는 다음과 같다.

```text
completed_events = sum over measurement window and native output lanes of
                   (retire_valid && retire_ready)

events_per_pin_cycle = completed_events /
                       (measurement_cycles * functional_pin_bits)
```

A8처럼 native output backpressure가 없고 항상 소비되는 인터페이스는
`completed_events = sum(retire_valid)`이다. `functional_pin_bits`는 frozen candidate
PPA 경계를 통과하는 clock/reset/power 이외의 모든 data/address/source, valid,
ready, lane/type/control wire이다. 사용하지 않는 common-TB lane이나 adapter가
만든 ready는 native pin으로 세지 않는다. 반대로 실제로 존재하는 중복/padding
wire는 정보량이 작더라도 전체 경계 metric에서 빼지 않는다.

Frozen trace의 address는 source/coordinate 4비트, polarity 1비트, event type 1비트로
구성되어 의미 payload는 6비트이다. 대부분의 후보는 이를 `ADDR_WIDTH=16` 버스에
싣는다. 아래의 `event 16`은 물리 버스 폭이고, 의미 entropy가 16비트라는 뜻이
아니다.

## Native interface와 pin 산정

`V/R`은 valid/ready, `E`는 event/address, `S`는 source이다. 모든 행은 N=16,
`S=ceil(log2 N)=4`이다.

| Track | 조사한 native top / normalized output | Native event/source 폭 | Native serialization 및 codec 경계 | V/R/control pin | 전체 functional pin 산식 | 공통 재계산 판정 |
|---|---|---:|---|---|---:|---|
| A2 | `a2_adaptive_dual_path_core`, 1 retire lane | E=16, S=4 | 직렬화 없음; 병렬 source 입력에서 단일 병렬 retire | 입력 V16/R16; 출력 V1/R1 | `16*16 + 16 + 16 + (16+4+1+1) = 310` | functional event와 전체 pin-cycle 재계산 가능. native top/binding은 HEAD와 같지만 candidate tree의 README가 dirty |
| A3 | `a3_homeostatic_inhibition`, 1 lane | E=16, S=4 | 직렬화/codec 없음 | 입력 V16/R16; 출력 V1/R1 | 310 | 가능; 조사 SHA에서 candidate 경로 clean |
| A4 | `a4_quadtree_fabric`, 1 lane | E=16, S=4 | 외부 직렬 link 없음. 내부 tree link도 E16+S4+age8+handshake인 병렬 hop | 입력 V16/R16; 출력 V1/R1 | 310 | 전체 경계는 가능. 내부 hop을 1개 link로 간주한 link-only 비교는 불가; 조사 snapshot에서 candidate 경로 clean |
| A5 | `a5_speculative_pregrant_ppa_top`, 1 lane | E=16, S=4 | 직렬화/codec 없음 | 입력 V16/R16; 출력 V1/R1 | 310 | binding/profile에서 single retire와 output backpressure가 고정되어 재계산 가능 |
| A6 | rejected `a6_v2_lossless_codec_top`, 1 lane | E=6, S=4; 입력 event bus 없음 | exact encoder + 2-data-pin framed link + exact decoder가 모두 top 내부 | 입력 V16/R16; 출력 V1/R1; link data2/count2/ready1은 내부; 관측 출력 6 | 의미 seam `32+12=44`; 현재 top literal 경계 `44+6=50` | 제한된 fixed-positive/single-type 의미에서 event 재계산 가능. 기존 active-link-cycle 결과는 공통 전체-window pin-cycle로 재사용 불가 |
| A7 | `a7_parallel_event_compactor`, K=4 retire lanes | lane당 E=16, S=4 | 4개 병렬 retire lane; serializer가 아님; codec 없음 | 입력 V16/R16; 출력 lane당 V1/R1 | `288 + 4*(16+4+1+1) = 376` | K와 구현 variant를 고정하면 가능. 조사 snapshot은 clean이나 profile이 없어 최종 순위는 NOT_FROZEN |
| A8 | `a8_age_calendar_wheel`, 1 always-ready lane | E=16, S=4 | 직렬화/codec 없음 | 입력 V16/R16; 출력 V1, native R 없음 | `288 + (16+4+1) = 309` | mandatory always-ready workload에서 `sum(V)`로 가능. common binding의 retire_ready는 native pin이 아님 |
| A9 | `a9_distributed_token_fabric`, K=4 lanes | lane당 E=16, S=4 | 외부 직렬 link 없음. 각 내부 hop도 E16+S4+V/R의 병렬 payload | 입력 V16/R16; 출력 lane당 V1/R1 | `288 + 4*22 = 376` | K/topology를 고정하면 전체 경계는 가능. 내부 hop link-only 비교는 불가; candidate 경로 dirty이고 profile 없음 |

`288 = 16*E16 + V16 + R16`이다. A7/A9는 K가 바뀌면
`functional_pin_bits = 288 + 22*K`로 다시 계산해야 한다. K=4 수치를 K=1 후보와
동일 pin 수로 나누면 안 된다. A2/A8 binding이 common interface의 여분 normalized
lane을 tie-off하더라도 native output은 각각 1 lane이므로 여분 lane은 과금하지 않는다.

## Functional event, pin-cycle, link-only metric의 재계산 가능성

| 대상 | Functional event | 전체 candidate pin-cycle | 동일 의미의 link-only pin-cycle |
|---|---|---|---|
| A2, A3, A5 | binding handshake로 가능 | 동일 measurement window와 위 native pin 수가 있으면 가능 | 선언된 외부 physical link가 없으므로 불가 |
| A4 | 단일 retire handshake로 가능 | 가능 | tree의 여러 내부 병렬 hop 중 어느 cut을 link로 볼지 frozen되지 않아 불가 |
| A6 v2 | 지원 workload의 단일 retire handshake로 가능 | 50핀 literal top 또는 관측 6핀을 제거한 별도 frozen wrapper 44핀 중 하나를 먼저 고정해야 가능 | 5핀 link 자체는 셀 수 있으나 기존 결과는 active data+delimiter cycle 분모이다. 전체 measurement window로 재실행/재집계하기 전 공통 metric과 불가 |
| A7 | K lane handshake 합으로 가능 | 정확한 K/variant와 window가 있으면 가능 | 병렬 retire bundle을 serializer/link라고 부를 수 없어 불가 |
| A8 | always-ready 조건에서 valid 합으로 가능 | 가능 | 선언된 외부 physical link가 없어 불가 |
| A9 | K lane handshake 합으로 가능 | 정확한 K/topology와 window가 있으면 가능 | 모든 hop을 합산할 frozen cut/활동 정의가 없어 불가 |

따라서 기존 result에서 `completed_events`, `measurement_cycles`, 정확한 top parameter와
binding identity가 모두 보존되어 있으면 전체 경계 metric은 재계산할 수 있다. 반면
link-only metric은 A6 외에는 물리 경계 자체가 없고, A6도 시간 분모가 달라 현재
발표값끼리의 재계산은 불가능하다. A4/A9 내부 network 비용을 추가 보고하려면 모든
hop의 폭과 동일 measurement window의 pin-cycle을 합산하는 별도 frozen 정의가
필요하며, 그것은 외부 link metric과 다른 항목이어야 한다.

## 중복 과금과 무료 재구성 경계

### Source identity/AER coordinate의 중복

A2--A5와 A7--A9의 입력은 source별 lane index로 source를 이미 나타내면서 각 lane에
16-bit event/address를 함께 제공한다. 출력은 event/address의 coordinate와 별도의
4-bit `retire_source`를 동시에 운반한다. 즉 의미상 같은 source identity가 입력 lane,
event coordinate, 출력 source에 중복 표현될 수 있다.

이 중복은 두 방식으로 다르게 처리해야 한다.

1. **전체 물리 경계 metric:** 실제 wire이므로 전부 과금한다. 중복이라는 이유로
   310/376에서 빼면 구현하지 않은 압축을 무료로 준다.
2. **semantic payload 또는 theoretical link lower bound:** coordinate/source 4비트를
   한 번만 세고 polarity/type 2비트를 더한 6비트가 frozen trace의 의미 payload다.
   `event16 + source4 = 20 independent semantic bits`로 쓰면 source를 이중 과금하고
   padding 10비트까지 정보량으로 오인한다.

따라서 `16-bit event bus`와 `6-bit meaningful event`를 같은 표의 동일 열로 비교하면
안 된다. 전자는 pin/PPA 비용, 후자는 trace semantics/entropy 기준이다.

### A6의 무료 payload 재구성

A6 binding은 `bench.source_event`를 codec top에 전달하지 않고 source로부터 normalized
event `{source, 2'b10}`을 재구성한다. 이 seam은 mandatory trace가 fixed-positive,
single-type일 때만 exact이다. polarity/type가 변화하거나 16-bit event의 다른 의미를
요구하는 일반 AER 입력에서는 encoder가 그 정보를 받은 적이 없으므로 lossless codec
비교가 아니다.

그러므로 A6의 44/50핀 수치는 이 제한된 capability subset에서만 유효하다. event
payload bus를 실제로 입력받아 운반하는 후보와 비교하면서 누락된 입력 payload를
0핀으로 두고 decoder가
무료로 복원한다고 가정하는 것은 금지해야 한다. 반대로 제한 subset을 명시한 뒤 실제
source-derived attributes만 비교하는 것은 가능하지만, 이는 full-event capability
순위와 별도 표여야 한다.

### Control/관측 pin 경계

- A8의 common `retire_ready`는 native core에서 사용되지 않으므로 309핀 계산에 넣지
  않는다.
- A6의 `link_count[1:0]`, `link_data[1:0]`, `link_ready_observe`, `decode_error`는 현재
  top 밖으로 나가는 6개 관측 출력이다. 문자 그대로 현 top을 PPA boundary로 삼으면
  과금하여 50핀이다. 측정 전용으로 제외하려면 이 포트가 없는 별도 frozen synthesis
  wrapper/cut을 정의해야 하며, 말로만 제외할 수 없다.
- A6 내부 link는 data2 + count2 + reverse ready1 = 5핀이다. 이 5핀 denominator는
  encoder/decoder/storage를 포함한 전체 top PPA와 함께 부가 metric으로만 보고한다.
- Valid/ready를 payload가 아니라는 이유로 빼면 pin-cycle을 과소계상한다. A7/A9의
  lane별 V/R과 A2--A7/A9의 output ready는 모두 functional pin이다.

## 비교 불가능 판정과 필요한 추가 조건

현재 상태에서 공정하게 가능한 cross-track 수치는 각 후보의 같은 measurement window에
대한 `(completed logical events)/(cycles * whole-native-boundary pins)`뿐이다. 다음은
link-only 또는 최종 ranking에서 비교 불가능하다.

- **A2/A9 dirty candidate trees:** read-only 조사 시점의 변경을 commit SHA만으로
  재현할 수 없다. 표의 정적 포트 수는 보이지만 결과 variant가 frozen되지 않았다.
  A2의 변경은 README뿐이고 조사한 native top/binding은 clean이므로 310핀 산식에는
  영향이 없지만, extractor는 보수적으로 candidate tree dirty로 표시한다.
- **A7/A9 missing candidate capability profile:** 지원 backpressure, lane 수, runner/top identity를
  profile로 고정하기 전 결과 묶음을 최종 비교 대상으로 삼을 수 없다.
- **A4/A9 internal networks:** 단일 off-chip serialization boundary가 없으며 topology에
  따라 hop 수가 달라진다.
- **A7 parallel output:** K배 throughput은 K배 output pin을 사용한다. 이를 W-bit serial
  link 처리량으로 재명명할 수 없다.
- **A6 existing link result:** 5핀은 명확하지만 active link cycle denominator이고 입력
  capability가 축소되어 있다. 전체 window 및 동일 event semantics가 아니므로 다른
  후보의 whole-boundary 수치와 비교 불가하다.

공통 link ranking을 새로 만들려면 후보 모두에 대해 동일한 physical cut, data/control
pin 수, delimiter/idle 과금, 측정 window, backpressure 의미를 고정하고 각 후보 경계
안에 필요한 serializer/deserializer/codec/storage를 포함해야 한다. 이 감사는 그런
새 구현이나 frozen benchmark 변경을 제안/적용하지 않는다.

## 재현과 조사 snapshot

Read-only extractor는 다음처럼 실행한다. JSON은 stdout으로만 출력하며 어떤 worktree도
쓰지 않는다.

```sh
python3 scripts/extract_eight_track_link_pin_audit.py --check
```

조사한 HEAD는 A2 `901ea0f`, A3 `6660b48`, A4 `4aea1f9`, A5 `9f6874b`,
A6 `3d65dae`, A7 `f3520b4`, A8 `30c1f1a`, A9 `e571e67`이다. Extractor는 native
RTL parameter, binding/profile 존재, worktree 및 candidate-path dirty 상태, native
lane/ready/serialization, 전체 pin 산식을 함께 출력한다. `--check`는 위 8개 pin 수가
바뀌면 실패하므로 이후 인터페이스 변경을 조용히 같은 감사 결과로 재사용하지 못하게
한다.
