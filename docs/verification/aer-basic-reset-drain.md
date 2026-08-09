# Address-only `basic_reset_drain` conformance contract

Status: mandatory direct-SystemVerilog common-core test, 2026-08-10.

`basic_reset_drain` checks reset recovery without defining what a candidate must
do with accepted or pending traffic during reset. The test asserts its second
reset only after the first epoch has completely drained:

```text
pending == 0
accepted == delivered
outstanding == 0
retire_valid == 0
```

The pre-reset epoch offers one address-only event from each lower-half source;
the post-reset epoch offers one from each upper-half source. The sets are
disjoint, and a logical event value is `ADDR_WIDTH'(source)`. Thus a stale
pre-reset completion cannot match a legitimate post-reset event. No polarity,
type, sequence number, or arbitrary payload is added by the testbench or
binding. At least two sources are required.

## Cycle contract

1. Hold the initial normalized active-low reset for four rising edges and
   release it on a falling edge.
2. Offer the lower-half source burst on a falling edge, keep the sink ready,
   and drain completely. Check per-epoch generation and transport conservation.
3. Assert the second reset on a falling edge. Hold it for three rising edges.
   Check completion-valid quiet on each following falling edge. Sampling after
   the rising edge gives synchronous and asynchronous reset implementations the
   same reset edge and does not reward delta-cycle clearing.
4. Release reset on a falling edge. For four complete guard cycles, offer no
   source events, keep the sink ready, and reject every completion as stale.
5. Offer the disjoint upper-half source burst, drain completely, and check
   address identity, no phantom/duplicate/missing completion,
   `accepted == delivered`, and generation conservation.

The common normalized checker observes `retire_valid` during reset. Ganghee's
native binding also checks its unmasked native `valid`, because its normalizer
intentionally masks native results that do not correspond to a live pending
source. A binding must not make reset quiet pass merely by suppressing a stale
native result. Native-only reset-valid and phantom-result checks use `$fatal`,
so the native runner fails closed even if its log is not post-processed.

This test is correctness-only. Its two reset-separated epochs, guard cycles,
latency, and throughput are not ranking measurements.

## Reproduction

Run the mock self-check with Verilator:

```bash
tests/clean_reset/run_reset_drain_test.sh
```

Run it through the common Xcelium runner when the approved server slot is
available:

```bash
AER_SIMULATOR=xrun scripts/run_clean_benchmark.sh mock basic_reset_drain
```

The default common suite and Ganghee native always-ready suite include the
test. The self-check runner also injects (a) reset-valid and (b) stale
pre-reset-address faults and requires both simulations to fail. A test-only
native model separately proves that the Ganghee binding exits nonzero for both
reset-valid and no-request phantom violations. Qualification with Ganghee's raw
external RTL remains an external runner step.
