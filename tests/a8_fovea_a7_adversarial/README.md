# A8 fovea/A7 adversarial oracle

This candidate-independent suite checks the address-only seam without changing
the fovea, A7 endpoint, common testbench, or any manifest.  Occurrence IDs are
scoreboard-only evidence: the oracle never uses them to synthesize an address
or replace a missing request, launch, or retirement.

The positive controls freeze two distinct contracts:

- a fovea native result consumes one outstanding request-mask credit, so a
  level request cannot create a second occurrence after its credit is consumed;
- standard R1 ready-valid accepts every `valid && ready` edge, including
  continuous-valid changing-address traffic.  Registered output availability
  is admission +1 cycle and a real pre-NBA synchronous sink observes retirement
  at admission +2 cycles.

The mutation matrix is fail-closed:

| Mutant | Required oracle failure |
| --- | --- |
| missing request mask repeats a held request | `NATIVE_DUPLICATE_NO_REQUEST` |
| valid-edge detector suppresses a legal back-to-back event | `VALID_EDGE_DETECTOR_DROP` |
| first legal post-reset event is lost | `RESET_FIRST_EDGE_LOSS` |
| drain rises over launch/output/in-flight state | `PREMATURE_DRAIN` |
| endpoint swaps an address | `RETIRED_ADDRESS_SWAP` |
| availability/retirement move one cycle late | `AVAILABILITY_LATENCY` |
| stale result consumes a same-cycle retrigger credit | `STALE_RETRIGGER_CAUSALITY` |

Run `tests/a8_fovea_a7_adversarial/run_all.sh`.  The success sentinel is emitted
only after every positive control passes and all seven named mutants are killed.
This is a protocol oracle/mutation qualification, not a production-RTL run or a
physical timing claim.
