# W5 A8 Independent A7 Endpoint Adversarial Audit

Status: **DIGITAL PASS for the exact bound R1 endpoint; physical timing remains
HOLD.** This audit owns no A7 RTL and modifies no common workload, TB, manifest,
or fixture.

## Frozen owner provenance

The production and fair parallel endpoints are bound to clean A7 follow-up
commit `42377ca81340951bfcd453b3bd664e673091f9f3` (`42377ca`,
`fix(a7): close R1 drain and consumer boundary`). The A8 runner reads each blob
with `git show <commit>:<path>`, verifies its SHA-256, materializes it only below
a secure `/tmp` directory, and directly instantiates the two native tops. It
does not copy from the mutable A7 worktree and contains no functional adapter.

| Owner blob | SHA-256 |
|---|---|
| `a7_r1_launch_qualifier.sv` | `8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d` |
| `a7_r1_icg_boundary.sv` | `0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9` |
| `a7_r1_ddr_tx.sv` | `88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197` |
| `a7_r1_ddr_rx.sv` | `7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06` |
| `a7_r1_retire_observer.sv` | `2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f` |
| production top | `c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03` |
| parallel top | `151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748` |
| owner contract | `9fb0dbdfb66df6f8306525b5703399b38d38403fb0d9314fb9a1c116a3a6294a` |

The binding is machine-readable in
`tests/w5_a7_endpoint_adversarial/a7_w5_binding.json`. A mutated source hash is
an executable negative test and fails before compilation.

## Independent live monitor contract

`live_trace_monitor.py` is independent of A7 RTL and consumes externally
observable protocol actions. The frozen R1 distinction is:

- each `ref_clk_i` posedge with `valid && ready` accepts exactly one occurrence;
- continuous valid with a new accepted address every cycle is legal and is not
  collapsed by a valid-edge detector;
- only `valid && !ready` creates a held transaction, whose ID/address must stay
  stable through its first handshake; and
- an accepted occurrence must cause exactly one launch, one low-half rise, one
  high-half fall, one registered observer publication, and one following-edge
  sink sample.

R>1 cross-rate and level-request one-shot machinery is not inferred or provided
for free. It is outside this endpoint.

The downstream boundary is also frozen precisely. RX commits raw address/toggle
at burst fall. A charged `seen_toggle` detector publishes registered
`retire_valid_o`/address on the next phase-related `ref_clk_i` rise. A real
always-FF sink samples that registered publication in the pre-NBA region of the
following ref edge. Production and parallel tops instantiate the same owner
observer. This is a synchronous half-cycle path with known clock relationship,
**not a 2FF CDC claim**. The requested CDC duplicate/drop attacks are injected
as duplicate/drop mutations at this observer/sink boundary; they demonstrate
occurrence conservation, not unrelated-clock qualification.

## Mutation and fault matrix

| Mutation | Expected diagnosis | Result |
|---|---|---|
| continuous valid, changing address | two or more legal per-cycle handshakes | PASS positive control |
| stalled valid, stable address | one handshake after ready | PASS positive control |
| stalled valid changes ID/address | `STALL_DATA_CHANGED` | FAIL closed |
| duplicate launch after one handshake | `DUPLICATE_OR_PHANTOM_LAUNCH` | FAIL closed |
| missing fall / extra fall | `MISSING_FALL` / `EXTRA_FALL` | FAIL closed |
| rise while frame open | `RISE_OVER_OPEN_FRAME` | FAIL closed |
| high/low halves swapped | `WRONG_LOW_HALF`, `WRONG_HIGH_HALF` | FAIL closed |
| unknown or unstable link data | `UNSTABLE_RISE_DATA` or fall equivalent | FAIL closed |
| reset while occurrence in flight | `RESET_IN_FLIGHT` | FAIL closed; contract-invalid input |
| aborted address appears after reset | `STALE_POST_RESET_EVENT` | FAIL closed |
| sink duplicate / drop | `SINK_DUPLICATE_OR_PHANTOM` / `SINK_DROP` | FAIL closed |
| ready asserted without capacity | `FALSE_READY` | FAIL closed |
| drain idle with launch or registered output pending | `FALSE_DRAIN_IDLE` | FAIL closed |
| observer delayed one extra ref cycle | exact +1 availability / +2 sink assertion | FAIL closed |

All 14 monitor/mutation tests pass. Reset-in-flight is deliberately reported as
an invalid protocol action. The owner endpoint promises no delivery for that
occurrence; after the reset epoch, the monitor forbids a phantom/stale delivery.

## Exact production versus parallel execution

The A8 direct-native TB instantiates `a7_r1_candidate_endpoint` and
`a7_r1_parallel_reference_top` without wrapper or adapter. Both receive the same
ready-valid input and are compared at four boundaries:

1. per-posedge handshake and ready equality;
2. DDR rise/low-half and fall/high-half versus the parallel full address frame;
3. registered output availability and the following-edge pre-NBA sink sample;
4. exact accepted/observed occurrence counts after drain.

The TB assigns an integer ref-cycle timestamp at every admission. It checks the
producer's post-NBA registered output at exactly admission +1 and checks what a
real always-FF sink sees in the pre-NBA region at exactly admission +2. A
candidate-owned mutation inserts one extra observer register into the
materialized `/tmp` copy only; the resulting +3-cycle sink path is required to
exit nonzero with a latency mismatch. The bound owner blobs remain unchanged.

The earlier `ca1a209` owner commit was explicitly rejected by this A8 harness:
post-NBA observation had hidden a cycle in which `retire_valid_o` was pending
but `drain_idle_o` was high. The pre-NBA sink model reproduced the failure at the
first launch. Final binding therefore does not use `ca1a209`.

The bound follow-up run passed 64 continuously-valid changing-address
occurrences at one event per ref cycle and one transaction held through
reset-release arming. Both production and parallel delivered exact
order/address/count at the common real-sink boundary, and `drain_idle_o` stayed
low whenever launch fire or registered retire valid was high. The direct RTL TB
also asserts reset after a real DDR rise but before its fall, checks immediate
abort with no stale post-reset output, re-arms, and proves clean delivery of a
fresh occurrence. Delivery of the aborted occurrence is not claimed because
mid-frame reset is contract-invalid.

`run_all.sh` does not claim or rely on an external owner regression receipt. It
runs only A8-owned monitor tests, binding/hash negatives, the exact-SHA direct
normal execution, and the +3-cycle latency negative. The owner regression may
provide additional evidence, but it is outside this reproducible A8 result.

The final owner generic structural proxy reports DDR at 3 link pins, 20 state
bits, and 29 charged functional cells; the same-boundary parallel reference is
5 pins, 18 bits, and 27 cells. These are digital generic-cell proxies, not
mapped or physical PPA. Registered availability is one ref cycle after
admission and the synchronous always-FF sink consumes on cycle two.

## Evidence boundary and decision

This is a digital protocol/occurrence PASS only. RTL simulation can detect
missing/extra digital edges and model unknown/unstable samples, but cannot prove
ICG pulse integrity, analog metastability resolution, half-cycle setup/hold,
recovery/removal, clock skew, ODDR/IDDR mapping, PVT, or routed energy. The
phase-related observer removes an unrelated-CDC claim; it does not remove the
half-cycle STA obligation. Backpressure and unrelated consumer clocks remain
future charged handshake/FIFO variants.

Reproduce all A8-owned checks with:

```bash
tests/w5_a7_endpoint_adversarial/run_all.sh
```
