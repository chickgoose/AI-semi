# Candidate-neutral K2 SystemVerilog conformance harness

This new, isolated suite qualifies the shared N=16/K=2 transaction boundary
without editing candidate RTL, the common AER testbench, or frozen workload
manifests.  It deliberately does not encode A2's calendar policy or A3's Fovea
policy.  Singleton and two-source cohorts make count and identity unambiguous;
larger cohorts learn the owner's order at offer time and then enforce it through
stall, atomic acceptance, and optional ordered retirement.

## Contract under test

An owner supplies a module named `k2_candidate_binding`:

```systemverilog
module k2_candidate_binding (
  input  logic clk, rst,
  input  logic [15:0] source_pending,
  output logic [1:0] grant_count,
  output logic [3:0] grant_addr0, grant_addr1,
  input  logic bundle_ready,
  output logic drain_idle
);
```

`grant_count` is 0, 1, or 2.  A nonzero offer commits in full on a rising edge
with `bundle_ready`; count two never accepts partially.  A stalled offer keeps
count, identity, and lane order stable.  `drain_idle` is exactly the absence of
both pending input and an offered/held bundle.  The owner declares exact offer
latency as 0 (same-cycle combinational) or 1 (one registered edge).

The atomic suite covers count 0/1/2, count-one independence from lane-1 ready,
count-two partial-ready rejection, no-bubble refill, held offers,
duplicate/phantom/replay checks, reset abort, truthful drain, and exact latency
stamps.  `k2_conformance_oracle.sv` contains the policy-independent passive
invariants; `k2_conformance_vectors.svh` freezes the directed cohorts.

`k2_ordered_link_conformance_tb.sv` is separate and optional.  Run it only when
the promoted system contract includes a charged buffered link with independent
downstream readiness.  It proves no younger bypass, ordered compaction after a
head-only retirement, same-edge refill, held presentation, reset, and drain.
Partial link movement must never advance scheduler policy.

## Run the self-test and mutation gate

```sh
tests/k2_conformance/run_conformance.sh
tests/k2_conformance/run_mutations.sh
```

The test-only reference binding is exercised at both legal latency stamps.  It
is not candidate evidence.  The mutation gate requires all 16 faults to fail:
bad counts, lane-1 coupling for count one, partial count-two progress, held
offer reorder, duplicate, phantom, stale reset state, latency shift, refill
bubble, younger bypass, missing compaction, retire reorder, and link faults.

## A2/A3 owner consumption

Keep each wrapper in the owner branch; do not modify the candidate module.

- A2 maps `req`, `grant_count`, `grant_addr0`, `grant_addr1`, `bundle_ready`,
  and native `drain_idle` directly, then runs with `--latency 0`.  Its shim is
  `bindings/a2_batched_iwrr_k2_binding.sv`.
- A3 maps `source_pending`, `grant_count`, `lane0_addr`, `lane1_addr`, and
  `bundle_ready`; its wrapper derives `drain_idle` as
  `source_pending == 0 && grant_count == 0`, then runs with `--latency 1`.
  A ready-to-run shim for the candidate in this repository is in
  `bindings/a3_exact_scalar_prefix_k2_binding.sv`; the optional charged-link
  shim is `bindings/a3_ordered_link_binding.sv`.

Example invocation (repeat `--rtl` for every owner source):

```sh
tests/k2_conformance/run_candidate.sh \
  --rtl path/to/owner.sv \
  --binding path/to/k2_candidate_binding.sv \
  --latency 1
```

If the owner promotes a separately charged ordered link, expose it as
`k2_ordered_link_binding` with the port list used by the reference binding and
add `--link-rtl ... --link-binding ...`.  A scheduler-only owner omits those
arguments and remains strictly atomic.
