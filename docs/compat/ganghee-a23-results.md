# Ganghee workload / A23 compatibility results

Date: 2026-08-04

Candidate: `integration/a23-final-candidate` at
`d62a81548e479d3f9b7c881b42d0cd4207aa6dea`

## Verdict

The Ganghee RTL benches are **not drop-in compatible** with A23. A
handshake-aware compatibility harness is **semantically compatible** with the
audited arrival process, queue-depth/overflow rule, observation window, and
event-level latency/fairness calculations. All non-overflow arrivals survived
the adapter and A23 without a phantom, duplicate, loss, reorder, or payload
corruption.

- Ganghee compatibility matrix: 130/130 PASS (65 Icarus + 65 Verilator).
- Existing A23 functional regression: 18/18 PASS.
- Existing A23 stress regression: 120/120 PASS.
- Explicit FAIL/FATAL result lines: 0.
- Core RTL changes: none.

Generated logs and simulator builds are under `/tmp/ganghee-a23-exact-full`
and `/tmp/ganghee-a23-regression`; none are committed.

## Read-only source audit

The following files were streamed from `~/redred-faer` as terminal base64 and
decoded under `/tmp/ganghee-a23-server-src`. No server file or log was created.
Server and local SHA256 manifests matched byte-for-byte for all 11 files.

| Source file | SHA256 |
|---|---|
| `tb/event_scoreboard.v` | `98c05a3c53e875f2b32c8d1d17f8bd878684ccbef1e4a58587d00b0e807b144d` |
| `tb/aer16_bench_core.vh` | `8f6da29830125e305e53f1fff9eb4c98adee630b87f51423165902bdde5f15c2` |
| `tb/aer64_bench_core.vh` | `1a56ef15c2195c004fb4d63464cb8ac7785fd12bdcf0d1d124acadbfe60bbef1` |
| `tb/hotspot_bench_core.vh` | `7ed66f5f4039b898d9e8b717482af4f0066bfdfa04b9159a52788276ef626c44` |
| `tb/tb_aer16_bench_base.v` | `1118859f525b4b07d1761bbd0a8401b2771d66494e05a9e789ed322447f03446` |
| `tb/tb_aer64_bench_base.v` | `4b8dbdb86f916265394044da06983a9f006b7d214f273b233bce1b57f2445536` |
| `tb/tb_hotspot_base_center.v` | `13daee0f7b6924784c6b0e9acb864845e8f635ab60f0e6cdb441aaba9cc42ce9` |
| `tb/tb_hotspot_base_corner.v` | `7a62c7466eba35545582780a4caa5bab864217b07a10e0636824fc755273fd43` |
| `tb/tb_moving_hotspot.v` | `b0f6e5c9d041daf4b3d4591cecd22388d3b2be33aa531c608c489acf563b011c` |
| `tb/tb_moving_hotspot_v2.v` | `8061b4084f51d8afed918313046c2b60a30f47ba413b7c688ef34f3e96dbc0f1` |
| `tb/tb_moving_hotspot64_v2.v` | `cf7dc81b86e7e99ac4f552dc6e78e50892023b68391e040c0e594af879eb2cb6` |

## Interface compatibility

| Property | Ganghee benches | A23 final candidate | Compatibility action |
|---|---|---|---|
| Reset | active-high `rst` | active-low `rst_ni` | polarity conversion in harness |
| Producers | level `req[15:0]` / `req[63:0]`, no ready | per-source ready/valid/payload | per-source queue plus one stable pending event |
| Link | `valid + addr_type + ROW/COL` serial beats | one direct event with address/source | compare at reconstructed event level |
| Output flow control | none | `event_ready_i` | `always` for like-for-like; separate random-backpressure cases |
| Source identity | reconstructed `row*side+col` | explicit `event_source_o` | N16 `row=src/4`; N64 `row=src/8` |

The adapter never drives `valid` directly from `qcount>0`. Each source has an
unissued queue and exactly one pending event. Pending valid/payload remains
stable until the **latched posedge handshake** says it was accepted. The next
negedge alone may retire/refill pending state. This prevents the over-acceptance
bug caused by observing a combinational `ready` that changed after another
source's handshake.

`QDEPTH` covers every non-departed event, matching `event_scoreboard`: unissued
queue + pending + accepted-but-not-emitted. A full queue increments workload
`overflow`; it is not classified as A23 loss. After the original CYCLES window,
the harness drains all retained events and requires
`generated-overflow == accepted == emitted`.

Payload is `{source[7:0], sequence[23:0]}`. A global reference FIFO plus
per-source sequence checks distinguish phantom/unexpected output, duplicate,
global reorder, source/address corruption, and final loss. The harness also
checks A23 occupancy 0..2, grant onehot0, priority movement only after an input
handshake, and input/output payload stability while stalled.

## Preserved workload semantics

- Arrival sampling uses the original expression and source-major call order:
  `(($random(rng_seed) % 100 + 100) % 100)` once per source per cycle.
- N16 uniform: CYCLES=3000, QDEPTH=32, ARRIVAL_PCT=3/5/6/15%.
- N64 uniform: CYCLES=6000, QDEPTH=64, ARRIVAL_PCT=3%.
- N16 fixed hotspot: BG=3%, HOT=50%; center `{5,6,9,10}` or corner
  `{0,3,12,15}`.
- N16 moving hotspot: BG=3%, HOT=50%, phase length 400,
  center -> corner -> center.
- N64 fixed hotspot: CYCLES=6000, QDEPTH=64, BG=2%, HOT=20%; center rows
  2,3,4,5 or periphery rows 0,1,6,7 (32 sources in either hot set).
- N64 moving hotspot: BG=2%, HOT=20%, phase length 1500; center rows 2,3,4,5
  -> outer rows 0,1,6,7 -> center rows.
- Seeds: 1,2,3,4,5.

Adaptive/FAER-only `hot_mask`, settling-time, and classification-accuracy
assertions are intentionally absent from A23 PASS/FAIL. Only their traffic
generation is portable.

Icarus sample audit for N16 uniform 3%, seed 1 produced 1493 arrivals in both an
isolated copy of the original sampling loop and the A23 harness. Verilator's
seeded `$random` stream is elaboration/tool dependent and did not reproduce the
same samples. Verilator results therefore cross-check harness/RTL execution but
are not presented as sample-for-sample workload equivalence.

## Icarus workload results

Values aggregate five seeds. `Generated`, `overflow`, and `emitted` are sums.
Window throughput is the five-seed average in events/cycle. Window/e2e latency
starts at workload arrival; fabric latency starts at A23 input acceptance.

| Case | Generated | Overflow | Emitted | Window throughput | Window max latency | Fabric max | Final e2e max | Jain min /1000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| N16 uniform 3% | 7,347 | 0 | 7,347 | 0.489333 | 11 | 2 | 11 | 985 |
| N16 uniform 5% | 12,171 | 0 | 12,171 | 0.809867 | 84 | 2 | 84 | 991 |
| N16 uniform 6% | 14,626 | 0 | 14,626 | 0.968933 | 258 | 2 | 258 | 991 |
| N16 uniform 15% | 36,365 | 18,849 | 17,516 | 0.999333 | 511 | 2 | 511 | 1000 |
| N16 hotspot center | 35,500 | 19,231 | 16,269 | 0.999200 | 452 | 2 | 452 | 519 |
| N16 hotspot corner | 35,727 | 19,457 | 16,270 | 0.999333 | 448 | 2 | 448 | 522 |
| N16 moving hotspot | 14,187 | 6,142 | 8,045 | 0.998000 | 693 | 2 | 693 | 716 |
| N64 uniform 3% | 57,706 | 7,515 | 50,191 | 0.999667 | 3,592 | 2 | 4,095 | 1000 |
| N64 hotspot center rows | 211,636 | 166,921 | 44,715 | 0.999667 | 4,094 | 2 | 4,095 | 999 |
| N64 hotspot periphery rows | 211,367 | 166,888 | 44,479 | 0.999667 | 4,095 | 2 | 4,095 | 999 |
| N64 moving hotspot | 157,881 | 115,216 | 42,665 | 0.999556 | 4,088 | 2 | 4,095 | 999 |

Every row has accepted=emitted=generated-overflow after drain and zero phantom,
duplicate, loss, reorder, or corruption. Same-condition runs use
`out_ready=1`. Fabric latency is exactly two cycles throughout those runs.
Large end-to-end latency and overflow are expected when aggregate offered load
exceeds the one-event/cycle sink and are workload-queue effects, not A23 data
loss.

Follow-up audit of `tb_hotspot64_base.v` and `tb_hotspot64_v2.v` confirmed that
the fixed N64 geometry is center rows 2..5 versus periphery rows 0,1,6,7. The
runner therefore names the cases `h64-center` and `h64-periphery`; no N64
`corner` case is reported.

## Additional output backpressure

Random `out_ready` uses a separate LCG so it cannot perturb Ganghee's `$random`
arrival stream.

| Case | Generated | Overflow | Emitted | Window throughput | Fabric max | Final e2e max | Integrity |
|---|---:|---:|---:|---:|---:|---:|---|
| N16 uniform 5%, random ready | 12,171 | 25 | 12,146 | 0.747933 | 4 | 660 | PASS |
| N64 uniform 3%, random ready | 57,706 | 14,871 | 42,835 | 0.749667 | 4 | 5,463 | PASS |

## Throughput and latency normalization

Ganghee sends ROW and COL as two serial beats and reconstructs an event in its
RX. A23 transports one direct event per handshake. Raw valid-beat throughput or
beat latency is therefore not comparable. This report compares:

1. reconstructed output events per workload cycle;
2. arrival-to-output event latency, matching the scoreboard meaning; and
3. A23 acceptance-to-output fabric latency, reported separately.

This normalization demonstrates event-level correctness under the same offered
workload but does not claim identical wire utilization, serialization latency,
or cycle-by-cycle output order between different arbitration architectures.

The recorded service-gap field under stochastic traffic is descriptive only:
long gaps can mean a source had no pending event. Round-robin fairness bounds are
covered by the existing continuous-contention stress regression instead.

## Separate power-flow audit fact

The Ganghee power report currently being vectorless and the prior VCD timescale
parse error are power-flow audit findings. They are unrelated to this workload
compatibility PASS and are not used as functional evidence here.

## Reproduction

With Icarus, VVP, and Verilator on PATH:

```sh
scripts/compat/run_ganghee_a23.sh
```

Run a single Icarus case and seed:

```sh
GANGHEE_SIMULATORS=iverilog \
GANGHEE_CASE_FILTER=u16-p03 \
GANGHEE_SEEDS=1 \
GANGHEE_RESULTS_ROOT=/tmp/ganghee-a23-repro \
scripts/compat/run_ganghee_a23.sh
```

Set `GANGHEE_TRACE=1` to emit VCDs next to the `/tmp` result logs. The existing
A23 regressions were rerun with `scripts/run_a23_functional_checks.sh` and
`scripts/run_a23_stress.sh`.
