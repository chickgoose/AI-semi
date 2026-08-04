# Hyeonsu testbench / A23 compatibility results

## Scope and provenance

- Branch base: `integration/a23-final-candidate` at `d62a815`
- Server simulator: Xcelium `23.09-s013`
- Isolated server run directory: `/tmp/a2-hyeonsu-a23-20260804/results-final`
- `~/semi-ai` and `~/AI-semi/integration` were read-only. No server source was
  edited and all simulator output was created below `/tmp`.
- `tb/aer_tb.sv` was copied byte-for-byte as `tests/compat/hyeonsu/aer_tb.sv`:
  SHA256 `c57e1a95b4f50f0cc060f9d878d1ed7ddcf395d16e80b44c984324fe9b42a7a8`.
- `tb/dual_level_arbiter_tb.sv` was copied byte-for-byte as
  `tests/compat/hyeonsu/dual_level_arbiter_tb.sv`: SHA256
  `a63317b1040f6328a6cec9cf89fe1c45db2b2a549e2fb34c872aeb7b21ac74a1`.
- No production or baseline RTL was changed. The harness supplies only module,
  parameter, package-width, and packed/unpacked-array adaptation.

The `aer_top` wrapper fixes the implementation to A23. `TX_MODE` and
`ARB_MODE` are accepted only for source compatibility and cannot change the
core. The N=256 adapter maps `req/advance/grant` to the flat A23 rotating RR;
`GROUP_SIZE` is informational.

## Exact original TB result and scheduling diagnosis

All exact-source compilations and elaborations passed. The exact original AER
TB passed single, simultaneous, burst, starvation, all-but-one, and reset
recovery, but failed the backpressure workload in both Xcelium and Verilator.
The Xcelium result rules out a Verilator-only DUT interpretation:

| Run | Compile/elab | Accepted/emitted | Scoreboard errors | Result |
| --- | --- | ---: | ---: | --- |
| active N=4, exact source | PASS | 128/128 (backpressure) | 66 | FAIL |
| active N=64, exact source | PASS | 2048/2048 (backpressure) | 1086 | FAIL |
| long-stall N=4, exact source | PASS | 80/80 | 21 | FAIL |
| long-stall N=64, exact source | PASS | 1280/1280 | 21 | FAIL |

The failures occur where `drive_source` changes `in_valid`/`in_addr` in the
same `posedge` active region in which the DUT and scoreboard sample them.
`backpressure_pattern` changes `out_ready` the same way. Which process observes
the update first is not defined. Evidence is localized to those transitions:
the exact active runs have zero errors in every non-backpressure workload,
while the bound monitor reports duplicate/no-matching-input observations only
when the original scoreboard does. The separately enabled original long-stall
has the same defect. These exact failures are retained in `status.tsv`; they
are not counted as A23 DUT failures.

For a meaningful cross-check, the runner mechanically generates a
`scheduler_safe` TB in the result directory. It preserves the original event
counts, workload ordering, ready/stall lengths, scoreboard, assertions, and
thresholds, changing only stimulus transitions to `negedge` so they are stable
before the sampled `posedge`. The original checked-in copies remain byte exact.

## Scheduler-safe AER results

Every row compiled/elaborated and ran with Xcelium. Errors include both the
unchanged original scoreboard and the additional protocol monitor.

| N | Workload | Accepted/emitted/errors | Throughput | Avg/max latency | Jain fairness | Max wait | Result |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 4 | single | 32/32/0 | 0.666667 | 2.0000/2 | 0.250000 | 1 | PASS |
| 4 | simultaneous | 128/128/0 | 0.888889 | 2.0000/2 | 1.000000 | 3 | PASS |
| 4 | burst | 320/320/0 | 0.952381 | 2.0000/2 | 0.833333 | 3 | PASS |
| 4 | backpressure | 128/128/0 | 0.365714 | 4.9531/5 | 1.000000 | 9 | PASS |
| 4 | starvation probe | 200/198/0 | 0.990000 | 2.0000/2 | 0.500000* | 1* | PASS |
| 4 | all-but-one saturated | 180/180/0 | 0.918367 | 2.0000/2 | 0.750000 | 2 | PASS |
| 4 | reset recovery | 80/80/0 | 0.909091 | 2.0000/2 | 1.000000 | 3 | PASS |
| 64 | single | 32/32/0 | 0.666667 | 2.0000/2 | 0.015625 | 1 | PASS |
| 64 | simultaneous | 2048/2048/0 | 0.992248 | 2.0000/2 | 1.000000 | 63 | PASS |
| 64 | burst | 5120/5120/0 | 0.996885 | 2.0000/2 | 0.833333 | 63 | PASS |
| 64 | backpressure | 2048/2048/0 | 0.397670 | 4.9971/5 | 1.000000 | 159 | PASS |
| 64 | starvation probe | 200/198/0 | 0.990000 | 2.0000/2 | 0.031250* | 1* | PASS |
| 64 | all-but-one saturated | 3780/3780/0 | 0.995785 | 2.0000/2 | 0.984375 | 62 | PASS |
| 64 | reset recovery | 1280/1280/0 | 0.993789 | 2.0000/2 | 1.000000 | 63 | PASS |

The starvation probe deliberately stops at the 200-cycle window without a
tail drain, hence monitor accepted/emitted is 200/198; service is exactly
100:100 for source 0 versus source 3 (N=4) and source 63 (N=64). This is not
loss: the next workload resets the two legal pipeline-resident events.
Starred fairness values apply the original all-N-sources Jain formula to the
two 100-count active sources, and starred max wait follows from the alternating
service sequence; the original starvation task itself prints service counts
rather than calling `drain_and_report`.

The single-source-only workload naturally has Jain fairness `1/N` when the
metric includes idle sources. All-but-one fairness similarly includes the one
deliberately silent source.

### Long-stall experiment

| N | Compile/elab | Accepted/emitted/errors | Throughput | Avg/max latency | Jain fairness | Max wait | Max output stall | Result |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 4 | PASS | 80/80/0 | 0.784314 | 2.1250/7 | 1.000000 | 8 | 5 | PASS |
| 64 | PASS | 1280/1280/0 | 0.900141 | 2.1953/127 | 1.000000 | 188 | 125 | PASS |

Output valid/address/source remained stable throughout both stalls. The
long-stall results are separate from the original active regression as
requested.

## N=256 arbiter adapter

Compilation/elaboration and simulation passed. The generic A23 validity
criteria passed: grant was always onehot0, no grant occurred without a request,
monitor errors were zero, and the maximum wait was 255 handshakes against a
bound of 256. With continuously active request sets and `advance=|grant`, the
arbiter sustains one grant per cycle.

| Probe | Observed service | Interpretation |
| --- | --- | --- |
| same group | min=max=200 | PASS, no starvation |
| different groups | min=max=200 | PASS, no starvation |
| all 256 saturated | min=max=20 | PASS, exact RR balance |
| 255 active, one silent | active min=max=20; silent=0 | PASS |
| skewed groups | group-0 per-source avg=83; lone sources=83 each | informational; flat RR is balanced per source |

No particular hierarchical grant sequence or group-weighted behavior is
claimed because A23 is intentionally flat RR, not `dual_level_arbiter`.

## Native A23 regression

Local Verilator `5.032` reruns used generated output only under
`/tmp/a2-hyeonsu-native-final`:

- EE430 stream/contention: 6/6 PASS for N=1,3,4. N=4 sustained contention
  accepted/emitted 128/128, output II=1, maximum service gap=4 (bound 4).
- Functional: 9/9 PASS for N=1,3,4 and seeds 17, 23001, 48879; input/output
  II=1 and bounded service were preserved.
- Stress: 60/60 PASS for N=1,3,4 and seeds 1..20; zero reported failures.

## Reproduction

Server Xcelium (from an isolated bundle, native tests disabled there):

```bash
csh -fc 'source ~/control_digi.cshrc; rehash; \
  setenv HYEONSU_SIMULATOR xrun; \
  setenv HYEONSU_RESULTS_ROOT /tmp/a2-hyeonsu-a23-20260804/results-final; \
  setenv HYEONSU_RUN_NATIVE 0; \
  cd /tmp/a2-hyeonsu-a23-20260804; \
  bash scripts/compat/run_hyeonsu_a23.sh'
```

The runner order is exact active N=4/64, scheduler-safe active N=4/64,
exact long-stall N=4/64, scheduler-safe long-stall N=4/64, then arbiter N=256.

Local native regression:

```bash
AER_SIMULATOR=verilator AER_SIM_OUT=/tmp/a2-hyeonsu-native-final \
  scripts/run_a23_ee430_checks.sh
AER_SIMULATOR=verilator AER_SIM_OUT=/tmp/a2-hyeonsu-native-final \
  scripts/run_a23_functional_checks.sh
A23_SIMULATORS=verilator \
  A23_RESULTS_ROOT=/tmp/a2-hyeonsu-native-final/stress \
  scripts/run_a23_stress.sh
```

## Remaining compatibility limits

1. The exact original posedge-driving TB is nondeterministic at handshake and
   ready transitions. It must be repaired upstream or run through the clearly
   labeled scheduler-safe generation for a DUT verdict.
2. `TX_MODE`, `ARB_MODE`, and `GROUP_SIZE` are syntactic compatibility inputs;
   A23 behavior is fixed and they do not select other microarchitectures.
3. Flat A23 RR does not reproduce a proprietary dual-level grant order or
   group weighting. Generic safety, liveness, fairness, and throughput are the
   valid cross-architecture comparisons.
